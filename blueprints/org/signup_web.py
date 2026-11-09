# -*- coding: utf-8 -*-
"""org: web signup routes: get-started, create_org, OTP, profile, documents, status"""
import re
import time
from extensions import app_log, limiter, log_security_event
from flask import flash, g, redirect, render_template, request, session
from utils.auth import (
    _TURNSTILE_SITE_KEY,
    _hash_token,
    generate_password_hash,
    turnstile_enabled,
    verify_turnstile,
)
from utils.helpers import _safe_app_url, clean_email_domain
from utils.plan_limits import get_per_employee_paise
from . import org_bp
from .applications import (
    _GENERIC_DUPLICATE_MSG,
    _OTP_TTL_MINUTES,
    _back_to_signup_form,
    _create_signup_company,
    _fetch_application_row,
    _free_registration_allowed,
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
from .validation import (
    _clean_gst,
    _clean_mobile,
    _clean_subdomain_slug,
    _validate_company_profile,
    _validate_signup_account,
)


@org_bp.route("/get-started", methods=["GET"])
def get_started_page():
    """Retired standalone chooser page -- the landing page ("/") now shows
    its own "Create Your Company" / "Login" links directly instead of
    sending visitors through this extra hop. Kept as a redirect (rather
    than removed outright) so old bookmarks/links/emails pointing here
    still land somewhere useful; "get-started" also stays in
    RESERVED_PATH_SEGMENTS so no company can ever claim it as a slug."""
    from utils.analytics import track_page_view
    track_page_view("/get-started")
    return redirect("/")


@org_bp.route("/create_org", methods=["GET"])
def create_org_page():
    # Flashed messages live in the session cookie, not scoped to any one
    # page -- an unrelated admin-session-expiry notice (category
    # "warning", queued by app.py's _enforce_session_lifetime /
    # _enforce_idle_timeout / _enforce_csrf hooks on some earlier request
    # in this same browser, possibly a different tab entirely) must never
    # surface on this public signup page just because it shares a cookie
    # with that admin session. This page's own flashes (validation
    # errors, captcha failures, from the POST handler below) are always
    # category "error" -- keep only those, drop everything else.
    if session.get("_flashes"):
        session["_flashes"] = [f for f in session["_flashes"] if f[0] == "error"]
    from utils.analytics import track_page_view
    track_page_view("/create_org")
    return render_template(
        "create_org.html",
        per_employee_paise=get_per_employee_paise(),
        show_captcha=turnstile_enabled(),
        turnstile_site_key=_TURNSTILE_SITE_KEY,
        free_registration=request.args.get("free") == "1" and _free_registration_allowed(),
        portal_base=_safe_app_url(),
    )


@org_bp.route("/create_org", methods=["POST"])
@limiter.limit("10 per hour")
def create_org():
    """The whole web signup is one step: the company's name and the admin's
    account -- email (also their login username), mobile number and
    password -- then, on the same page, the code emailed to verify the
    address. This creates the tenant_applications row and emails the code;
    create_org_verify_otp() then creates the company and logs the admin
    straight into its dashboard, whose checklist (app.py's
    inject_registration_checklist) collects the rest: company details,
    documents, payment."""
    free_registration = request.form.get("signup_plan", "").strip().lower() == "free"
    if free_registration and not _free_registration_allowed():
        flash("Free registration can only be started from the Platform Admin dashboard.", "error")
        return redirect("/create_org")

    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response", "")
        if not verify_turnstile(token, request.remote_addr):
            flash("Captcha verification failed. Please try again.", "error")
            return _back_to_signup_form()

    company_name = re.sub(r'\s+', ' ', request.form.get("company_name", "")).strip()
    # Lowercase: it's the login username, matched exactly at /login.
    admin_email = request.form.get("admin_email", "").strip().lower()
    admin_mobile = _clean_mobile(request.form.get("admin_mobile", ""))
    admin_password = request.form.get("admin_password", "").strip()
    # Only the /create_org page has a "confirm password" box; the landing-page
    # form and API clients don't send one, so check it only when present.
    confirm_password = request.form.get("confirm_password")

    if confirm_password is not None and admin_password != confirm_password.strip():
        flash("Password and confirm password do not match.", "error")
        return _back_to_signup_form()

    error = _check_new_signup(company_name, admin_email, admin_mobile, admin_password)
    if error:
        flash(error, "error")
        return _back_to_signup_form()

    application_id, access_token = _start_signup_application(company_name, admin_email, admin_mobile,
                                                             admin_password, free_registration)
    # access_token lives ONLY in the session cookie from here on for the
    # web flow -- never in a URL/query string, so it can't leak via
    # referrer headers or browser history. application_id (a bare
    # sequential id, not a secret) is fine to carry in the URL.
    session["org_access_token"] = access_token
    # Display only (drops the Payment step from the wizard's indicator);
    # what's provisioned follows the application row's free_registration.
    session["org_free_registration"] = free_registration
    return redirect(f"/create_org/verify_otp?application_id={application_id}")


def _check_new_signup(company_name, admin_email, admin_mobile, admin_password):
    """Step 1's checks, shared by the web form (create_org()) and the app
    (_api_start_account()): the fields, then whether this company or this
    admin email is already registered (both recorded as duplicate alerts
    for Platform Admin). Returns an error message, or None."""
    error = _validate_signup_account(company_name, admin_email, admin_mobile, admin_password)
    if error:
        return error
    if signup_block_for(admin_email):
        return SIGNUP_BLOCKED_MSG
    conflicting, match_type = check_duplicate_name(company_name)
    if not conflicting:
        conflicting, match_type = check_duplicate_admin_email(admin_email), "email"
    if conflicting:
        _record_duplicate_alert(None, company_name, admin_email, conflicting, match_type=match_type)
        return _GENERIC_DUPLICATE_MSG
    return None


def _start_signup_application(company_name, admin_email, admin_mobile, admin_password, free_registration=False):
    """Step 1's shared tail (web create_org(), the app's api_create_org()),
    once _check_new_signup() passes: creates the application and emails
    the verification code. The company URL stays blank ('' -- the column is
    NOT NULL) until the code is verified (_create_signup_company). Returns
    (application_id, access_token)."""
    access_token = _generate_access_token()
    otp_code = _generate_otp()
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    # payment_option is settled when the company is created
    # (_create_signup_company).
    mcur.execute(
        "INSERT INTO tenant_applications (company_name, subdomain, admin_username, admin_email, admin_mobile, "
        "admin_password_hash, email_domain, payment_option, access_token_hash, otp_code_hash, "
        "otp_expires_at, source_ip, free_registration) "
        "VALUES (%s, '', %s,%s,%s,%s,%s,'manual',%s,%s, NOW() + (%s * INTERVAL '1 minute'), %s, %s) RETURNING id",
        (company_name, admin_email, admin_email, admin_mobile, generate_password_hash(admin_password),
         clean_email_domain(admin_email), _hash_token(access_token), _hash_token(otp_code),
         _OTP_TTL_MINUTES, request.remote_addr, free_registration)
    )
    application_id = mcur.fetchone()[0]
    mconn.commit()
    mcur.close()
    mconn.close()

    # Email verification is never skipped, in any APP_ENV: an
    # APP_ENV=development shortcut here once jumped straight to the document
    # upload, which also skipped the provisioning that only runs after a
    # verified code, stranding the applicant.
    send_org_signup_otp_email(admin_email, company_name, otp_code)
    return application_id, access_token


@org_bp.route("/create_org/verify_otp", methods=["GET"])
def create_org_verify_otp_page():
    """The signup page again, with what was entered locked in and a box for
    the emailed code -- verification belongs to the one signup step."""
    application = _load_application(request.args.get("application_id", ""), session.get("org_access_token"))
    if not application or application["status"] != "started":
        flash("Your registration session has ended. Please start again.", "error")
        return redirect("/create_org")
    return render_template(
        "create_org.html", code_step=True, application=application,
        free_registration=bool(application["free_registration"]),
        portal_base=_safe_app_url(),
    )


@org_bp.route("/create_org/verify_otp", methods=["POST"])
@limiter.limit("20 per hour")
def create_org_verify_otp():
    """Checks the emailed code, creates the company (_create_signup_company)
    and logs its admin in -- the dashboard's checklist takes it from there.
    A platform admin's free registration goes back to Platform Admin
    instead: the company's own admin logs in with their email."""
    application_id = request.form.get("application_id", "")
    otp_code = request.form.get("otp_code", "").strip()
    application = _load_application(application_id, session.get("org_access_token"))
    if not application:
        flash("Application not found. Please start again.", "error")
        return redirect("/create_org")
    if application["status"] == "otp_verified" and not application["company_name"]:
        # Verified before step 1 asked for the company name: the old
        # company-profile step still finishes it.
        return redirect(f"/create_org/profile?application_id={application['id']}")

    ok, error = _verify_application_otp(application, otp_code)
    if not ok:
        flash(error, "error")
        return redirect(f"/create_org/verify_otp?application_id={application['id']}")
    if not application["company_name"]:
        return redirect(f"/create_org/profile?application_id={application['id']}")

    # Re-fetch: _verify_application_otp() just advanced status to
    # 'otp_verified', but `application` is the pre-verification snapshot.
    ok, error, application = _create_signup_company(_fetch_application_row(application["id"]))
    if not ok:
        app_log.warning("create_org_verify_otp: could not create the company for application %s: %s",
                        application_id, error)
        flash(error if error == _GENERIC_DUPLICATE_MSG else
              f"Could not set up your company: {error}. Please contact support.", "error")
        return redirect("/create_org")

    session.pop("org_access_token", None)
    session.pop("org_free_registration", None)
    if application["free_registration"]:
        flash(f"'{application['company_name']}' is created at {_safe_app_url()}/{application['subdomain']}/login. "
              f"Its admin logs in with {application['admin_email']} and finishes registering from the dashboard.",
              "success")
        return redirect("/super_admin")
    return _log_in_new_company_admin(application)


def _log_in_new_company_admin(application):
    """Signs the new company's admin straight into its dashboard: binds the
    session to the company just provisioned (this request ran on a URL with
    no company in it, so under the default one) and starts the admin
    session the way a password login does (auth.begin_admin_session). The
    code they just entered was emailed to this same address, so it stands
    in for the emailed login code when MANDATORY_LOGIN_MFA is on, exactly as
    auth.mfa_verify() treats one."""
    from flask import current_app
    from blueprints.auth import begin_admin_session
    from database import get_master_db
    from utils.helpers import invalidate_settings_cache
    from utils.totp import mark_totp_enabled

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT db_name, subdomain FROM tenants WHERE id=%s", (application["tenant_id"],))
    db_name, slug = mcur.fetchone()
    mcur.close()
    mconn.close()

    g.tenant_db, g.tenant_slug = db_name, slug
    username = application["admin_username"]
    if current_app.config["MANDATORY_LOGIN_MFA"]:
        mark_totp_enabled(username)
    begin_admin_session(username, "admin")
    session["tenant_db"] = db_name
    session["tenant_slug"] = slug
    session["_tenant_status_checked_at"] = time.time()
    session["_billing_locked"] = False
    invalidate_settings_cache()
    log_security_event(
        "auth.admin_login_success", f"Admin login for '{username}' (role=admin) right after registering",
        level="INFO", identifier=username, subdomain=slug,
    )
    flash("Your company is ready. Complete your organization profile to open your dashboard.", "success")
    return redirect(f"/{slug}/welcome")


def _load_profile_application(application_id, access_token):
    """The application if access_token proves access, its email is
    verified and it's still waiting for its company profile, else None."""
    application = _load_application(application_id, access_token)
    if application and application["status"] == "otp_verified" and not application["tenant_id"]:
        return application
    return None


def _save_company_profile(application, fields, logo_file):
    """Step 3 of the signup wizard, shared by the web form and the mobile
    app's /api/create_org/profile: validates the company profile -- name,
    GSTIN, company URL, optional logo, the plan -- saves it on the verified
    application and provisions the company right away (as the code step
    used to). Returns (ok, error, portal_url, checkin_url); on failure the
    application is left verified, so the profile can be submitted again."""
    company_name = (fields.get("company_name") or "").strip()
    subdomain = _clean_subdomain_slug(fields.get("subdomain") or "", company_name)
    gst_number = _clean_gst(fields.get("gst_number") or "")
    error = _validate_company_profile(company_name, subdomain, gst_number)
    if error:
        return False, error, None, None
    # Again here: the block may have been set after step 1.
    if signup_block_for(application["admin_email"]):
        return False, SIGNUP_BLOCKED_MSG, None, None

    conflicting, match_type = check_duplicate_name(company_name)
    if not conflicting:
        conflicting, match_type = check_duplicate_gst(gst_number), "gst"
    if conflicting:
        _record_duplicate_alert(application["id"], company_name, application["admin_email"], conflicting,
                                match_type=match_type)
        return False, _GENERIC_DUPLICATE_MSG, None, None

    logo_path = application["logo_path"]
    if logo_file and logo_file.filename:
        from utils.helpers import save_uploaded_logo
        logo_path, logo_err = save_uploaded_logo(logo_file, subdomain)
        if logo_err:
            return False, f"Company logo: {logo_err}", None, None

    from utils.razorpay_utils import accepting_online_payments, payments_enabled
    # "trial" is only ever chosen explicitly (the "2-Day Free Trial" option,
    # blueprints/trial_billing.TRIAL_DURATION_DAYS long, beside "Pay now").
    # A platform admin's free registration has no payment step and is
    # billed by manual invoice -- as is everyone while online payments are
    # switched off (the trial needs a card mandate).
    signup_plan = (fields.get("signup_plan") or "pay_now").strip().lower()
    if application["free_registration"]:
        payment_option = "manual"
    elif signup_plan == "trial" and payments_enabled():
        payment_option = "trial"
    else:
        payment_option = "online" if accepting_online_payments() else "manual"

    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "UPDATE tenant_applications SET company_name=%s, subdomain=%s, gst_number=%s, logo_path=%s, "
        "payment_option=%s, updated_at=NOW() WHERE id=%s AND status='otp_verified'",
        (company_name, subdomain, gst_number, logo_path, payment_option, application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    ok, error, portal_url, checkin_url = _provision_application_instantly(_fetch_application_row(application["id"]))
    if not ok:
        app_log.warning("company profile: provisioning failed for application %s: %s", application["id"], error)
    return ok, error, portal_url, checkin_url


def _render_profile_page(application):
    from utils.razorpay_utils import payments_enabled
    return render_template(
        "create_org.html", profile_step=True, application_id=application["id"],
        free_registration=bool(application["free_registration"]),
        payments_enabled=payments_enabled(),
        per_employee_paise=get_per_employee_paise(),
        portal_base=_safe_app_url(),
    )


@org_bp.route("/create_org/profile", methods=["GET"])
def create_org_profile_page():
    application = _load_profile_application(request.args.get("application_id", ""), session.get("org_access_token"))
    if not application:
        flash("Verify your email first, or start a new registration.", "error")
        return redirect("/create_org")
    return _render_profile_page(application)


@org_bp.route("/create_org/profile", methods=["POST"])
@limiter.limit("20 per hour")
def create_org_profile():
    """Step 3 of the web signup wizard, after the email code (see
    _save_company_profile). A rejected profile -- a taken URL, a duplicate
    company -- comes back to this form, application intact; documents
    come next."""
    application = _load_profile_application(request.form.get("application_id", ""), session.get("org_access_token"))
    if not application:
        flash("Verify your email first, or start a new registration.", "error")
        return redirect("/create_org")
    ok, error, _portal_url, _checkin_url = _save_company_profile(application, request.form, request.files.get("logo"))
    if not ok:
        flash(error, "error")
        return redirect(f"/create_org/profile?application_id={application['id']}")
    return redirect(f"/create_org/upload_documents?application_id={application['id']}")


@org_bp.route("/create_org/resend_otp", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def create_org_resend_otp():
    application_id = request.form.get("application_id", "")
    application = _load_application(application_id, session.get("org_access_token"))
    if not application or application["status"] != "started":
        flash("This application can no longer receive a new code.", "error")
        return redirect("/create_org")

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
    flash("A new code has been sent to your email.", "info")
    return redirect(f"/create_org/verify_otp?application_id={application['id']}")


@org_bp.route("/create_org/upload_documents", methods=["GET"])
def create_org_upload_documents_page():
    # token: carries a re-upload magic link through from create_org_status.html's
    # rejected branch (a rejected application's original session is very
    # often long gone by the time someone acts on the rejection email) --
    # forwarded into the form as a hidden field so the POST below can use
    # it too. Falls back to nothing for the normal same-session flow, which
    # doesn't need it (session["org_access_token"] already covers that).
    return render_template("create_org_upload_documents.html",
                           application_id=request.args.get("application_id", ""),
                           token=request.args.get("token", ""),
                           free_registration=session.get("org_free_registration", False))


@org_bp.route("/create_org/upload_documents", methods=["POST"])
def create_org_upload_documents():
    application_id = request.form.get("application_id", "")
    access_token = session.get("org_access_token") or request.form.get("token", "")
    application = _load_application(application_id, access_token)
    if not application:
        flash("Application not found. Please start again.", "error")
        return redirect("/create_org")

    ok, error = _save_application_documents(application, request.files)
    if not ok:
        flash(error, "error")
        _token_qs = f"&token={request.form.get('token', '')}" if request.form.get("token") else ""
        return redirect(f"/create_org/upload_documents?application_id={application['id']}{_token_qs}")

    # Every self-serve application reaches status='provisioned' right after
    # OTP now (see _provision_application_instantly()) -- there's nothing
    # left to "wait" for once documents are in, so send them straight to
    # their live company's status/login page instead of the old
    # please-wait screen, which only still applies to a legacy in-flight
    # application that somehow reached here pre-provisioned.
    if application["status"] == "provisioned":
        flash("Documents submitted -- your company is live. Log in below.", "info")
        return redirect(f"/create_org/status/{application['id']}")
    return redirect(f"/create_org/pending?application_id={application['id']}")


@org_bp.route("/create_org/pending", methods=["GET"])
def create_org_pending_page():
    return render_template("create_org_pending.html", application_id=request.args.get("application_id", ""))


@org_bp.route("/create_org/status/<int:application_id>", methods=["GET"])
def create_org_status_page(application_id):
    """Lets a returning registrant check their application's status later
    (e.g. from a link in the approval/rejection email) without needing a
    live session -- the token travels in the URL for this one read-only,
    self-scoped endpoint (a deliberate, common "magic link" tradeoff; it
    reveals only the requester's own application, nothing about anyone
    else's). session's org_access_token is tried first so the same-tab
    "just finished uploading, want to see it" case never needs a token
    in the URL at all.

    The URL token is tried before the session's: approval/rejection both
    rotate the application's token, so a session token saved at signup may
    be stale while the emailed link is current. A session that already
    proved access on the pay/trial page (_verify_application_access) is
    also accepted, so that page's redirect here never needs a token."""
    application, access_token = None, ""
    for candidate in (request.args.get("token", ""), session.get("org_access_token", "")):
        if candidate:
            application = _load_application(application_id, candidate)
            if application:
                access_token = candidate
                break
    if not application and session.get("org_verified_application_id") == application_id:
        application = _fetch_application_row(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/create_org")
    portal_url = f"{_safe_app_url()}/{application['subdomain']}/login" if application["status"] == "provisioned" else None
    return render_template("create_org_status.html", application=application, portal_url=portal_url,
                           access_token=access_token)
