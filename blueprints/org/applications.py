# -*- coding: utf-8 -*-
"""org: signup application lifecycle: OTP, access tokens, approve/reject, documents"""
import html
import os
import secrets
from extensions import app_log, log_security_event
from flask import redirect, request, session
from utils.auth import _hash_token
from utils.email_utils import get_email_config, send_email_async
from utils.helpers import _safe_app_url, save_application_document
from .duplicates import _record_duplicate_alert, check_duplicate_name
from .provisioning import provision_tenant, send_portal_ready_email
from .validation import _slug_for_company


_OTP_TTL_MINUTES = 5
_OTP_MAX_ATTEMPTS = 5
_GENERIC_DUPLICATE_MSG = "A company with this name appears to already be registered. If this is your company, please contact support."
_APPLICATION_DOC_KINDS = ("registration_cert", "address_proof", "visiting_card", "name_board_photo")


def _generate_otp():
    return f"{secrets.randbelow(900000) + 100000}"


def send_org_signup_otp_email(to_email, company_name, otp_code):
    """Email the signup-verification OTP for a new company application.
    Deliberately a separate function from utils/totp.py's
    send_mfa_login_email() -- that one's copy ("Login Verification Code" /
    "finish signing in") is written for an existing account's login step;
    reusing it here for a brand-new registrant who hasn't created anything
    yet would be a confusing email to receive."""
    try:
        email_cfg = get_email_config()
        if not email_cfg:
            if os.environ.get("APP_ENV", "production") == "development":
                # Local dev only (same situation MANDATORY_PLATFORM_ADMIN_MFA's
                # .env comment documents for login MFA) -- this OTP step is a
                # required business gate, not an optional hardening layer, so
                # there's no bypass flag for it the way login MFA has one.
                # Logging the code here (never in a response body, and only
                # reached when there's genuinely no other way to deliver it)
                # is what makes the flow testable locally without real SMTP
                # creds. Explicitly gated on APP_ENV -- a production
                # deployment with SMTP creds missing/expired/misconfigured
                # must never fall into this branch: anyone with log/SIEM
                # read access (a broader population than DB access, often
                # including junior ops/on-call) could otherwise complete the
                # email-verification step for an arbitrary address during an
                # SMTP outage.
                app_log.warning("send_org_signup_otp_email: no SMTP configured -- OTP for %s (%s) is %s",
                                to_email, company_name, otp_code)
            else:
                # Fails closed in production: no code anywhere an operator's
                # log/alert pipeline (utils/alerts.py) would surface it, so
                # this is visible to ops without being a usable bypass. The
                # applicant is stuck at the OTP-entry screen with no way to
                # receive a code -- a real signup is blocked, which is the
                # correct outcome for a genuinely broken SMTP config, versus
                # silently handing out a working credential to anyone
                # reading the logs.
                app_log.error(
                    "send_org_signup_otp_email: SMTP not configured in production -- OTP delivery to %s "
                    "for '%s' failed. Fix SMTP configuration; applicant cannot complete signup until then.",
                    to_email, company_name,
                )
            return False
        # No company name yet on the web signup's first step.
        _company = html.escape(str(company_name)) if company_name else "your company"
        _code = html.escape(str(otp_code))
        html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:480px;margin:auto;background:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #1e293b;">
  <div style="background:#1d4ed8;padding:22px 26px;color:#fff;">
    <div style="font-size:18px;font-weight:700;">Verify Your Email</div>
    <div style="font-size:12px;opacity:0.8;margin-top:4px;">Registering {_company}</div>
  </div>
  <div style="padding:26px;">
    <p style="color:#cbd5e1;font-size:13px;">Use this code to confirm your email address and continue
      registering your company. It expires in {_OTP_TTL_MINUTES} minutes and can only be used once.</p>
    <div style="text-align:center;margin:22px 0;padding:16px;background:#090d16;border-radius:12px;">
      <span style="font-size:32px;font-weight:800;letter-spacing:6px;color:#60a5fa;">{_code}</span>
    </div>
    <p style="font-size:12px;color:#64748b;">If you didn't request this, you can safely ignore this email.</p>
  </div>
</div>"""
        send_email_async(to_email, "Verify your email to register your company", html_body, email_cfg)
        return True
    except Exception as exc:
        app_log.error("send_org_signup_otp_email failed: %s", exc)
        return False


def send_application_rejected_email(to_email, company_name, reason, status_url=None):
    """status_url (when given) is a fresh magic link -- see
    blueprints/platform_admin.py's platform_admin_reject_application(),
    which rotates access_token_hash specifically so this link works even
    once the applicant's original browser session is long gone. Lets them
    re-upload corrected documents directly from this email instead of
    restarting the whole application from scratch."""
    try:
        email_cfg = get_email_config()
        if not email_cfg:
            return False
        _company = html.escape(str(company_name))
        _reason = html.escape(str(reason or "It did not meet our verification requirements."))
        _link_block = ""
        if status_url:
            _link_block = f"""
    <a href="{status_url}" style="display:block;text-align:center;padding:13px 20px;background:#1e3a8a;color:#fff;
       border-radius:10px;text-decoration:none;font-size:14px;font-weight:700;margin:18px 0 6px;">
      Re-upload Corrected Documents
    </a>"""
        html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:480px;margin:auto;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e2e8f0;">
  <div style="background:#7f1d1d;padding:22px 26px;color:#fff;">
    <div style="font-size:18px;font-weight:700;">Application Not Approved</div>
    <div style="font-size:12px;opacity:0.85;margin-top:4px;">{_company}</div>
  </div>
  <div style="padding:26px;">
    <p style="color:#334155;font-size:14px;">We're unable to approve your company registration at this time.</p>
    <div style="background:#fef2f2;border-left:4px solid #dc2626;padding:14px;border-radius:6px;margin:16px 0;font-size:13px;color:#7f1d1d;">{_reason}</div>
    <p style="color:#334155;font-size:13px;">You don't need to start over -- fix what's noted above and re-upload just your documents.</p>
    {_link_block}
    <p style="color:#64748b;font-size:12px;">If you believe this is an error, please contact support.</p>
  </div>
</div>"""
        send_email_async(to_email, f"Your company registration was not approved -- {company_name}", html_body, email_cfg)
        return True
    except Exception as exc:
        app_log.error("send_application_rejected_email failed: %s", exc)
        return False


def _generate_access_token():
    return secrets.token_urlsafe(32)


def _load_application(application_id, access_token):
    """Fetch a tenant_applications row by id, verifying the caller presents
    the matching access_token (see app.py's schema comment on
    access_token_hash) -- the single mechanism (used identically by web
    and mobile) that stops someone from guessing a sequential application
    id and hijacking or peeking at someone else's in-progress signup.
    Returns a dict of the row's columns, or None if not found / token
    mismatch."""
    if not application_id or not access_token:
        return None
    application = _fetch_application_row(application_id)
    if not application:
        return None
    if not secrets.compare_digest(application["access_token_hash"], _hash_token(access_token)):
        return None
    return application


def _fetch_application_row(application_id):
    """The raw tenant_applications row as a dict, with NO access check --
    only ever call this after the caller's access to application_id has
    been proven (_load_application's token check, or
    _verify_application_access's session marker)."""
    try:
        application_id = int(application_id)
    except (TypeError, ValueError):
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "SELECT id, company_name, subdomain, admin_username, admin_email, admin_password_hash, "
        "email_domain, employee_count, payment_option, logo_path, access_token_hash, otp_code_hash, "
        "otp_expires_at, otp_attempts, email_verified_at, doc_registration_cert, doc_address_proof, "
        "doc_visiting_card, doc_name_board_photo, documents_submitted_at, status, reviewed_by, "
        "reviewed_at, rejection_reason, tenant_id, doc_review_status, gst_number, free_registration, admin_mobile "
        "FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return None
    cols = ["id", "company_name", "subdomain", "admin_username", "admin_email", "admin_password_hash",
            "email_domain", "employee_count", "payment_option", "logo_path", "access_token_hash", "otp_code_hash",
            "otp_expires_at", "otp_attempts", "email_verified_at", "doc_registration_cert", "doc_address_proof",
            "doc_visiting_card", "doc_name_board_photo", "documents_submitted_at", "status", "reviewed_by",
            "reviewed_at", "rejection_reason", "tenant_id", "doc_review_status", "gst_number", "free_registration",
            "admin_mobile"]
    return dict(zip(cols, row))


def _verify_application_access(application_id):
    """Whether this browser has proven it holds application_id's access
    token -- via the emailed link's ?token=, the signup session, or an
    earlier successful check in this session. On success only the
    (non-secret) application id is remembered in the session, never the
    token, so later same-page API calls (create_subscription/confirm) can
    be authorised without the page's JavaScript ever seeing the token."""
    try:
        application_id = int(application_id)
    except (TypeError, ValueError):
        return False
    for candidate in (request.args.get("token", ""), session.get("org_access_token", "")):
        if candidate and _load_application(application_id, candidate):
            session["org_verified_application_id"] = application_id
            return True
    return session.get("org_verified_application_id") == application_id


def _free_registration_allowed():
    """A free registration -- the same signup wizard with no payment step
    (signup_plan=free) -- can only be started by a logged-in platform admin,
    from the dashboard's "New Company (Free)" link to /create_org?free=1."""
    from blueprints.platform_admin import platform_admin_session_active
    return platform_admin_session_active()


def _verify_application_otp(application, otp_code):
    """Shared OTP-check logic for both the web and mobile verify_otp
    routes. Returns (True, None) on success (advances the row to
    'otp_verified'), or (False, error_message) -- the caller decides how
    to surface that (flash+redirect for web, a JSON 400 for mobile)."""
    if application["status"] != "started":
        return False, "This application has already moved past email verification."
    if application["otp_attempts"] >= _OTP_MAX_ATTEMPTS:
        return False, "Too many incorrect attempts. Please request a new code."

    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    stored_hash = application["otp_code_hash"] or ""
    # Always the real code, in every APP_ENV: an APP_ENV=development bypass
    # that accepted any code let a server left in development mode create
    # companies for email addresses nobody had verified. Local dev without
    # SMTP reads the code from the log instead (send_org_signup_otp_email).
    code_matches = bool(otp_code) and secrets.compare_digest(_hash_token(otp_code), stored_hash)
    if not code_matches:
        mcur.execute("UPDATE tenant_applications SET otp_attempts=otp_attempts+1, updated_at=NOW() WHERE id=%s",
                     (application["id"],))
        mconn.commit()
        mcur.close()
        mconn.close()
        return False, "Incorrect code. Please try again."

    mcur.execute("SELECT otp_expires_at < NOW() FROM tenant_applications WHERE id=%s", (application["id"],))
    expired = mcur.fetchone()[0]
    if expired:
        mcur.close()
        mconn.close()
        return False, "This code has expired. Please request a new one."

    _mark_application_otp_verified(application["id"], mconn, mcur)
    return True, None


def _mark_application_otp_verified(application_id, mconn, mcur):
    """Advances the application to 'otp_verified' once its emailed code
    has been checked; commits and closes mconn/mcur."""
    mcur.execute(
        "UPDATE tenant_applications SET status='otp_verified', email_verified_at=NOW(), updated_at=NOW() WHERE id=%s",
        (application_id,)
    )
    mconn.commit()
    mcur.close()
    mconn.close()


def _provision_application_instantly(application):
    """Called right after OTP verification succeeds, for EVERY
    payment_option -- every self-serve signup lane now provisions the
    tenant immediately, with KYC document collection/review moved fully
    async (see _save_application_documents()'s 'provisioned' guard below,
    and blueprints/platform_admin.py's approve/reject branches for an
    application whose tenant_id is already set). subscription_status is
    forced 'trialing' regardless of payment_option, since none of the
    three lanes collect payment upfront anymore -- see provision_tenant()'s
    own docstring for why this can't just be provision_tenant()'s default
    behavior (Platform Admin's own always-free tenant-creation tool shares
    that function and must NOT become trial-capped).

    Idempotent: a retried/duplicate request against an already-provisioned
    application (status=='provisioned') is a no-op success reusing the
    existing tenant, not a second schema-creation attempt.

    Returns (ok, error, portal_url_or_None, checkin_url_or_None) -- same
    shape as provision_tenant() itself."""
    from database import get_master_db

    if application["status"] == "provisioned":
        portal_url = f"{_safe_app_url()}/{application['subdomain']}/login"
        checkin_url = f"{_safe_app_url()}/{application['subdomain']}/checkin"
        return True, None, portal_url, checkin_url

    # A platform admin's free registration is a normal manually-invoiced
    # company (provision_tenant()'s default status), never a capped trial.
    ok, error, portal_url, checkin_url = provision_tenant(
        application["company_name"], application["subdomain"], application["admin_username"],
        application["admin_password_hash"], application["admin_email"],
        payment_option=application["payment_option"], email_domain=application["email_domain"],
        employee_count=application["employee_count"], logo_path=application["logo_path"],
        gst_number=application["gst_number"],
        subscription_status=None if application.get("free_registration") else "trialing",
        contact_phone=application.get("admin_mobile"),
    )
    if not ok:
        return False, error, None, None

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT id FROM tenants WHERE subdomain=%s", (application["subdomain"],))
    tenant_row = mcur.fetchone()
    tenant_id = tenant_row[0] if tenant_row else None
    mcur.execute(
        "UPDATE tenant_applications SET status='provisioned', tenant_id=%s, updated_at=NOW() WHERE id=%s",
        (tenant_id, application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    log_security_event(
        "org.tenant_provisioned_instant",
        f"{'Free registration' if application.get('free_registration') else 'Self-serve signup'} "
        f"'{application['company_name']}' provisioned instantly "
        f"(payment_option={application['payment_option']}) -- KYC document review still pending, async",
        level="INFO", subdomain=application["subdomain"], application_id=application["id"], tenant_id=tenant_id,
    )
    send_portal_ready_email(
        application["admin_email"], application["company_name"], application["admin_username"],
        portal_url, checkin_url=checkin_url,
    )
    return True, None, portal_url, checkin_url


def _create_signup_company(application):
    """Creates the company for an application whose step 1 named it, once
    its email code is verified: re-checks the name against companies
    registered since step 1, picks the company URL (_slug_for_company),
    records it and the billing option on the application, and provisions
    it (_provision_application_instantly). Retries with the next free URL if
    a concurrent signup took this one between the pick and the insert.
    Returns (ok, error, application) -- the refreshed row on success."""
    conflicting, match_type = check_duplicate_name(application["company_name"])
    if conflicting:
        _record_duplicate_alert(application["id"], application["company_name"], application["admin_email"],
                                conflicting, match_type=match_type)
        return False, _GENERIC_DUPLICATE_MSG, None

    from utils.razorpay_utils import accepting_online_payments
    from database import get_master_db
    # A platform admin's free registration is billed by manual invoice; a
    # self-serve company sets up auto-debit from its dashboard's checklist.
    online = not application["free_registration"] and accepting_online_payments()
    error = "Could not pick a company URL. Please contact support."
    for _attempt in range(3):
        slug = _slug_for_company(application["company_name"])
        if not slug:
            return False, "Company name must contain at least one letter or digit.", None
        mconn = get_master_db()
        mcur = mconn.cursor()
        mcur.execute(
            "UPDATE tenant_applications SET subdomain=%s, payment_option=%s, updated_at=NOW() "
            "WHERE id=%s AND status='otp_verified'",
            (slug, "online" if online else "manual", application["id"])
        )
        mconn.commit()
        mcur.close()
        mconn.close()
        ok, error, _portal_url, _checkin_url = _provision_application_instantly(
            _fetch_application_row(application["id"]))
        if ok:
            return True, None, _fetch_application_row(application["id"])
        if "already taken" not in (error or ""):
            break
    return False, error, None


def _approve_application_reviewed(application, reviewed_by):
    """Approves an already-provisioned application's KYC review
    (doc_review_status 'pending_review' -> 'approved'), reinstating the
    tenant if it was previously suspended for a rejected review. Shared by
    blueprints/platform_admin.py's human-reviewer route AND
    utils/document_verification.py's automated worker -- reviewed_by is
    what distinguishes the two in the audit trail (a platform-admin
    username, or the literal string 'system:automated_verification').

    Returns (ok, msg) -- ok is False only if the application wasn't
    actually awaiting review (stale/double-submitted call); msg is a
    human-readable result either way, safe to flash() as-is."""
    if application["tenant_id"] is None:
        return False, "Application is not yet provisioned."
    if application["doc_review_status"] != "pending_review":
        return False, "This application's documents aren't awaiting review."
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    # "AND doc_review_status='pending_review'" is load-bearing, not
    # decorative -- this is called from both the platform-admin route AND
    # utils/document_verification.py's automated worker, which can race
    # each other for the same application. Only the request whose UPDATE
    # actually flips a row (cursor.rowcount == 1) proceeds to reinstate/
    # log; the loser gets the same success result a retried call expects,
    # without a second reinstate or a duplicate security-event pair.
    cur.execute(
        "UPDATE tenant_applications SET doc_review_status='approved', reviewed_by=%s, reviewed_at=NOW(), "
        "updated_at=NOW() WHERE id=%s AND doc_review_status='pending_review'",
        (reviewed_by, application["id"])
    )
    if cur.rowcount != 1:
        conn.commit()
        cur.close()
        conn.close()
        return True, f"'{application['company_name']}'s documents approved."
    cur.execute("SELECT status FROM tenants WHERE id=%s", (application["tenant_id"],))
    tenant_row = cur.fetchone()
    reinstated = bool(tenant_row and tenant_row[0] == "suspended")
    if reinstated:
        cur.execute("UPDATE tenants SET status='active' WHERE id=%s", (application["tenant_id"],))
    conn.commit()
    cur.close()
    conn.close()
    if reinstated:
        log_security_event(
            "platform_admin.tenant_reinstated",
            f"Tenant {application['tenant_id']} reinstated -- corrected KYC documents approved",
            level="WARNING", identifier=reviewed_by, application_id=application["id"], tenant_id=application["tenant_id"],
        )
    log_security_event(
        "platform_admin.application_approved",
        f"Application {application['id']} KYC documents approved (tenant already provisioned)",
        level="INFO", identifier=reviewed_by, application_id=application["id"], tenant_id=application["tenant_id"],
    )
    return True, f"'{application['company_name']}'s documents approved."


def _reject_application_reviewed(application, reviewed_by, reason):
    """Rejects an already-provisioned application's KYC review, suspending
    the live tenant (utils/tenant_routing.py stops resolving a
    status!='active' tenant's slug at all -- the same mechanism
    platform_admin_set_status() relies on elsewhere) and emailing a fresh
    magic-link re-upload URL so the applicant has a way back in even
    though they can no longer log in to use the in-portal verification
    page. Shared by blueprints/platform_admin.py's human-reviewer route
    AND utils/document_verification.py's automated worker.

    Returns (ok, msg) -- ok is False only if the application isn't
    actually provisioned yet (nothing to suspend)."""
    if application["tenant_id"] is None:
        return False, "Application is not yet provisioned."
    from database import get_master_db
    from utils.auth import _hash_token
    new_token = secrets.token_urlsafe(32)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE tenant_applications SET doc_review_status='rejected', reviewed_by=%s, reviewed_at=NOW(), "
        "rejection_reason=%s, access_token_hash=%s, updated_at=NOW() WHERE id=%s",
        (reviewed_by, reason, _hash_token(new_token), application["id"])
    )
    cur.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (application["tenant_id"],))
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        "platform_admin.tenant_suspended_kyc_rejected",
        f"Tenant {application['tenant_id']} suspended -- KYC documents rejected: {reason}",
        level="WARNING", identifier=reviewed_by, application_id=application["id"],
        tenant_id=application["tenant_id"], reason=reason,
    )
    status_url = f"{_safe_app_url()}/create_org/status/{application['id']}?token={new_token}"
    send_application_rejected_email(application["admin_email"], application["company_name"], reason, status_url)
    log_security_event("platform_admin.application_rejected", f"Application {application['id']} rejected",
                       level="INFO", identifier=reviewed_by, application_id=application["id"], reason=reason)
    return True, "Application rejected."


def _save_application_documents(application, files):
    """Shared document-upload logic for both web and mobile. `files` is a
    dict-like of {doc_kind: FileStorage} (request.files works directly).
    Returns (True, None) on success -- documents are saved and the
    application is auto-approved in the same call (see the
    _approve_application_reviewed() call at the end of this function),
    unblocking login immediately rather than waiting on a review -- or
    (False, error_message).

    'rejected' is deliberately allowed back in here -- re-uploading
    corrected documents is the ONLY recovery path for a rejected applicant;
    without it they'd have to restart the entire application (company_name/
    subdomain/admin_username/password, all of it) from scratch at
    POST /create_org, re-triggering the duplicate-name check and a fresh
    OTP for no reason, since none of that was ever wrong.

    'provisioned' is also allowed -- every self-serve application reaches
    this state right after OTP verification now (see
    _provision_application_instantly()), before ever uploading a single
    document, so this is the common case going forward, not an edge case.
    For a 'provisioned' application, `status` itself is left alone below
    (it means "has a tenant" now, not "where's the KYC review at") and
    only `doc_review_status` advances -- see that column's own comment."""
    if application["status"] not in ("otp_verified", "pending_review", "rejected", "provisioned"):
        return False, "This application isn't ready for document upload."

    saved_paths = {}
    for doc_kind in _APPLICATION_DOC_KINDS:
        file_storage = files.get(doc_kind)
        if not file_storage or not file_storage.filename:
            return False, f"Please upload your {doc_kind.replace('_', ' ')}."
        path, err = save_application_document(file_storage, application["id"], doc_kind)
        if err:
            return False, f"{doc_kind.replace('_', ' ').title()}: {err}"
        saved_paths[doc_kind] = path

    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        # rejection_reason/reviewed_by/reviewed_at reset to NULL -- a
        # re-upload from 'rejected' must look like a fresh submission to
        # the platform admin's queue/detail view, not carry forward stale
        # rejection context from the prior review. `status` only advances
        # to 'pending_review' for the legacy (pre-instant-provisioning)
        # in-flight applications -- an already-'provisioned' application
        # keeps that status (it now means "has a tenant", tracked
        # separately from KYC review progress) and only doc_review_status
        # moves, to 'pending_review'.
        # verification_job_status='pending' hands this straight to
        # utils/document_verification.py's automated worker -- a re-upload
        # after rejection resets verification_attempts/ocr_extraction too,
        # same "must look like a fresh submission" reasoning as
        # rejection_reason/reviewed_by/reviewed_at just below.
        "UPDATE tenant_applications SET doc_registration_cert=%s, doc_address_proof=%s, doc_visiting_card=%s, "
        "doc_name_board_photo=%s, documents_submitted_at=NOW(), "
        "status = CASE WHEN status='provisioned' THEN status ELSE 'pending_review' END, "
        "doc_review_status='pending_review', updated_at=NOW(), "
        "rejection_reason=NULL, reviewed_by=NULL, reviewed_at=NULL, "
        "verification_job_status='pending', verification_attempts=0, verification_error=NULL, ocr_extraction=NULL "
        "WHERE id=%s",
        (saved_paths["registration_cert"], saved_paths["address_proof"], saved_paths["visiting_card"],
         saved_paths["name_board_photo"], application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    # Auto-approve immediately on submission -- KYC review no longer
    # gates login (matches the OAuth signup path in
    # blueprints/oauth_signup.py, which never collects documents at all).
    # verification_job_status stays 'pending' above, so
    # utils/document_verification.py's automated Claude-vision worker
    # still runs afterward and can still suspend the tenant via
    # _reject_application_reviewed() if it finds fraud (duplicate GST/PAN/
    # CIN) -- this removes the WAIT for a human/AI reviewer before access,
    # not the fraud check itself, which now runs as a retroactive safety
    # net instead of a precondition. Mutate the in-memory dict to match
    # what the UPDATE above just wrote (doc_review_status='pending_review')
    # -- _approve_application_reviewed()'s own guard reads this cached
    # field, not a fresh row, and the caller's `application` here still
    # holds whatever it looked like before this function ran.
    application["doc_review_status"] = "pending_review"
    _approve_application_reviewed(application, "system:auto_approved_on_submit")
    return True, None


def _back_to_signup_form():
    """Where a rejected POST /create_org sends the visitor: back to the
    form they filled in. The home page's registration form posts
    return_to=home -- an allowlisted value, never a URL, so this can't be
    used as an open redirect. Anything else keeps the original
    /create_org."""
    if request.form.get("return_to") == "home":
        return redirect("/?registration=failed#register")
    if request.form.get("signup_plan") == "free":
        return redirect("/create_org?free=1")
    return redirect("/create_org")
