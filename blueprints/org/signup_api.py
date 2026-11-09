# -*- coding: utf-8 -*-
"""org: mobile/JSON signup API"""
import re
from extensions import app_log, limiter, log_security_event
from flask import g, jsonify, request
from utils.auth import _hash_token, generate_password_hash
from utils.helpers import _safe_app_url, clean_email_domain
from . import org_bp
from .applications import (
    _GENERIC_DUPLICATE_MSG,
    _OTP_TTL_MINUTES,
    _create_signup_company,
    _generate_access_token,
    _generate_otp,
    _load_application,
    _provision_application_instantly,
    _save_application_documents,
    _verify_application_otp,
    send_org_signup_otp_email,
)
from .duplicates import (
    SIGNUP_BLOCKED_MSG,
    signup_block_for,
    _record_duplicate_alert,
    check_duplicate_admin_email,
    check_duplicate_gst,
    check_duplicate_name,
)
from .signup_web import (
    _check_new_signup,
    _load_profile_application,
    _save_company_profile,
    _start_signup_application,
)
from .validation import _clean_gst, _clean_mobile, _validate_gst_number, _validate_new_tenant_fields


@org_bp.route("/api/create_org", methods=["POST"])
@limiter.limit("10 per hour")
def api_create_org():
    """JSON step 1 of the gated signup flow (mobile app's company
    registration screen). Mirrors /create_org's validation, duplicate-name
    check, and pending-application creation -- this is a second,
    independent path into the same logic and must not be a softer bypass
    of those protections. Unlike the old version of this route, this no
    longer provisions a tenant -- it returns an application_id + access_token
    that the app must carry through verify_otp -> upload_documents ->
    status (there's no session cookie on this side to hold it implicitly,
    the way the web flow's does).

    Current app builds send what the web form does -- company_name,
    admin_email, admin_mobile, admin_password -- and the code step creates
    the company and signs the admin in (api_create_org_verify_otp). A
    request carrying a subdomain is an older build sending everything at
    once (URL, GSTIN, username), which still works the old way: its
    company is provisioned at the code step, documents follow."""
    try:
        data = request.get_json() or {}
        if "subdomain" not in data:
            return _api_start_account(data)
        company_name = data.get("company_name", "").strip()
        raw_subdomain = data.get("subdomain", "").strip().lower()
        subdomain = re.sub(r'[^a-z0-9\-]', '-', raw_subdomain).strip('-')
        admin_username = data.get("admin_username", "").strip()
        admin_password = data.get("admin_password", "").strip()
        admin_email = data.get("admin_email", "").strip()
        email_domain = clean_email_domain(data.get("email_domain", ""))
        gst_number = _clean_gst(data.get("gst_number", ""))

        error = _validate_new_tenant_fields(company_name, subdomain, admin_username, admin_password, admin_email,
                                            email_domain)
        if error:
            return jsonify({"ok": False, "msg": error}), 400

        gst_error = _validate_gst_number(gst_number)
        if gst_error:
            return jsonify({"ok": False, "msg": gst_error}), 400

        if signup_block_for(admin_email):
            return jsonify({"ok": False, "msg": SIGNUP_BLOCKED_MSG}), 403

        conflicting, match_type = check_duplicate_name(company_name)
        if not conflicting:
            conflicting, match_type = check_duplicate_gst(gst_number), "gst"
        if not conflicting:
            conflicting, match_type = check_duplicate_admin_email(admin_email), "email"
        if conflicting:
            _record_duplicate_alert(None, company_name, admin_email, conflicting, match_type=match_type)
            return jsonify({"ok": False, "msg": _GENERIC_DUPLICATE_MSG}), 409

        from utils.razorpay_utils import accepting_online_payments
        payment_option = "online" if accepting_online_payments() else "manual"

        access_token = _generate_access_token()
        otp_code = _generate_otp()
        from database import get_master_db
        mconn = get_master_db()
        mcur = mconn.cursor()
        mcur.execute(
            "INSERT INTO tenant_applications (company_name, subdomain, admin_username, admin_email, "
            "admin_password_hash, email_domain, gst_number, payment_option, access_token_hash, otp_code_hash, "
            "otp_expires_at, source_ip) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW() + (%s * INTERVAL '1 minute'), %s) "
            "RETURNING id",
            (company_name, subdomain, admin_username, admin_email, generate_password_hash(admin_password),
             email_domain, gst_number, payment_option, _hash_token(access_token), _hash_token(otp_code),
             _OTP_TTL_MINUTES, request.remote_addr)
        )
        application_id = mcur.fetchone()[0]
        mconn.commit()
        mcur.close()
        mconn.close()

        send_org_signup_otp_email(admin_email, company_name, otp_code)

        return jsonify({
            "ok": True,
            "msg": f"We've emailed a verification code to {admin_email}.",
            "application_id": application_id,
            "access_token": access_token,
            "step": "otp",
        })
    except Exception as global_exc:
        app_log.error("api_create_org global exception: %s", global_exc)
        return jsonify({"ok": False, "msg": f"Server error: {global_exc}"}), 500


def _api_start_account(data):
    """api_create_org()'s signup step: the same checks as the web form's
    create_org()."""
    company_name = re.sub(r'\s+', ' ', str(data.get("company_name") or "")).strip()
    admin_email = (data.get("admin_email") or "").strip().lower()
    admin_mobile = _clean_mobile(data.get("admin_mobile"))
    admin_password = (data.get("admin_password") or "").strip()
    error = _check_new_signup(company_name, admin_email, admin_mobile, admin_password)
    if error:
        return jsonify({"ok": False, "msg": error}), 409 if error == _GENERIC_DUPLICATE_MSG else 400
    application_id, access_token = _start_signup_application(company_name, admin_email, admin_mobile,
                                                             admin_password)
    return jsonify({
        "ok": True,
        "msg": f"We've emailed a verification code to {admin_email}.",
        "application_id": application_id,
        "access_token": access_token,
        "step": "otp",
    })


@org_bp.route("/api/create_org/verify_otp", methods=["POST"])
@limiter.limit("20 per hour")
def api_create_org_verify_otp():
    data = request.get_json() or {}
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    if not application:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    if application["status"] == "otp_verified" and not application["company_name"]:
        return jsonify({"ok": True, "step": "profile", "msg": "Email already verified."})

    ok, error = _verify_application_otp(application, str(data.get("otp_code", "")).strip())
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400

    # Verified before step 1 asked for the company name: the old profile
    # step (/api/create_org/profile) still finishes it.
    if not application["company_name"]:
        return jsonify({"ok": True, "step": "profile", "msg": "Email verified -- now set up your company."})

    # Re-fetch: _verify_application_otp() just advanced status in the DB,
    # but `application` here is still the pre-verification snapshot.
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    if not application["subdomain"]:
        return _api_create_company_and_sign_in(application)

    # An older app build's one-request signup already chose its URL.
    ok, error, portal_url, checkin_url = _provision_application_instantly(application)
    if not ok:
        app_log.error("api_create_org_verify_otp: instant provisioning failed for application %s: %s",
                      application["id"], error)
        return jsonify({"ok": False, "msg": f"Could not set up your company: {error}. Contact support."}), 500

    return jsonify({"ok": True, "step": "documents", "portal_url": portal_url, "checkin_url": checkin_url})


def _api_create_company_and_sign_in(application):
    """The app's counterpart of create_org_verify_otp()'s tail: creates the
    company (_create_signup_company) and signs its admin in with a Bearer
    token issued in the new company's schema. The app sends every later
    request under /<company_slug>/ -- tenant routing then resolves the
    company from that prefix, as it does for the website -- and finishes
    the registration checklist from its dashboard (/api/admin/registration).
    The token counts as MFA-verified: the code just checked was emailed to
    this admin's address, the same factor /api/login/verify accepts."""
    from blueprints.core import _issue_admin_api_token
    from database import get_master_db

    ok, error, application = _create_signup_company(application)
    if not ok:
        app_log.warning("api_create_org_verify_otp: could not create the company for application %s: %s",
                        application and application["id"], error)
        if error == _GENERIC_DUPLICATE_MSG:
            return jsonify({"ok": False, "msg": error}), 409
        return jsonify({"ok": False, "msg": f"Could not set up your company: {error}. Contact support."}), 500

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT db_name, subdomain, company_name FROM tenants WHERE id=%s", (application["tenant_id"],))
    db_name, slug, company_name = mcur.fetchone()
    mcur.close()
    mconn.close()

    g.tenant_db = db_name
    username = application["admin_username"]
    token = _issue_admin_api_token(username, mfa_verified=True)
    log_security_event(
        "auth.admin_login_success", f"App sign-in for '{username}' (role=admin) right after registering",
        level="INFO", identifier=username, subdomain=slug,
    )
    return jsonify({
        "ok": True, "step": "done", "token": token, "username": username, "role": "admin",
        "company_slug": slug, "company_name": company_name,
        "portal_url": f"{_safe_app_url()}/{slug}/login",
    })


@org_bp.route("/api/create_org/profile", methods=["POST"])
@limiter.limit("20 per hour")
def api_create_org_profile():
    """The mobile app's company profile step (see _save_company_profile):
    application_id, access_token, company_name, gst_number, subdomain,
    signup_plan, as JSON -- or multipart form fields when the optional
    logo file comes along."""
    data = request.form if request.form else (request.get_json(silent=True) or {})
    application = _load_profile_application(data.get("application_id"), data.get("access_token", ""))
    if not application:
        return jsonify({"ok": False, "msg": "Verify your email first, or start a new registration."}), 400
    ok, error, portal_url, checkin_url = _save_company_profile(application, data, request.files.get("logo"))
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400
    return jsonify({"ok": True, "step": "documents", "portal_url": portal_url, "checkin_url": checkin_url})


@org_bp.route("/api/create_org/resend_otp", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def api_create_org_resend_otp():
    data = request.get_json() or {}
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    if not application or application["status"] != "started":
        return jsonify({"ok": False, "msg": "This application can no longer receive a new code."}), 400

    otp_code = _generate_otp()
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "UPDATE tenant_applications SET otp_code_hash=%s, otp_expires_at=NOW() + (%s * INTERVAL '1 minute'), "
        "otp_attempts=0, updated_at=NOW() WHERE id=%s",
        (_hash_token(otp_code), _OTP_TTL_MINUTES, application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    send_org_signup_otp_email(application["admin_email"], application["company_name"], otp_code)
    return jsonify({"ok": True, "msg": "A new code has been sent to your email."})


@org_bp.route("/api/create_org/upload_documents", methods=["POST"])
def api_create_org_upload_documents():
    application = _load_application(request.form.get("application_id"), request.form.get("access_token", ""))
    if not application:
        return jsonify({"ok": False, "msg": "Application not found."}), 404

    ok, error = _save_application_documents(application, request.files)
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400

    if application["status"] == "provisioned":
        portal_url = f"{_safe_app_url()}/{application['subdomain']}/login"
        return jsonify({"ok": True, "step": "provisioned", "portal_url": portal_url,
                        "msg": "Documents submitted -- your company is live. Log in below."})
    return jsonify({"ok": True, "step": "pending_review",
                    "msg": "Your application has been submitted and is under review. We'll email you once it's approved."})


@org_bp.route("/api/create_org/status/<int:application_id>", methods=["GET"])
def api_create_org_status(application_id):
    access_token = request.args.get("access_token", "")
    application = _load_application(application_id, access_token)
    if not application:
        return jsonify({"ok": False, "msg": "Application not found."}), 404

    portal_url = None
    if application["status"] == "provisioned":
        portal_url = f"{_safe_app_url()}/{application['subdomain']}/login"

    return jsonify({
        "ok": True,
        "status": application["status"],
        "company_name": application["company_name"],
        "rejection_reason": application["rejection_reason"],
        "portal_url": portal_url,
    })


# /superadmin is superseded by /super_admin -- redirect for backwards compat
