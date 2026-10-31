# -*- coding: utf-8 -*-
"""Auth blueprint -- login, logout, password reset, WebAuthn."""
import os
import re
import time
import base64
import secrets
import hashlib
import datetime
import html as _html
from flask import (
    Blueprint, request, session, redirect, jsonify, render_template, g, flash,
)

from extensions import app, limiter, app_log, log_security_event
from database import get_db_connection
from utils.auth import (
    generate_password_hash, check_password_hash, _hash_token,
    _check_login_lockout, _record_login_failure, _clear_login_failures,
    admin_required, role_required, employee_required, employee_api_required,
    _get_failed_count, verify_turnstile, turnstile_enabled,
    CAPTCHA_AFTER_ATTEMPTS, _TURNSTILE_SITE_KEY, HR_ROLE, MANAGER_ROLE,
    api_required, validate_new_password, verify_and_update_password, _mask_identifier,
    dummy_password_check, record_password_change, stamp_password_version, _TOKEN_NOT_STALE_SQL,
    session_account_ok, employment_ended_on, access_ended_message,
)
from utils.login_otp import issue_challenge, verify_challenge, discard_challenge
from utils.helpers import tpath, get_company_settings, _audit, _db, _safe_app_url
from utils.email_utils import get_email_config, send_email_smtp, send_email_async, notify_if_new_login_ip
from utils.session_risk import ensure_session_id
from utils.totp import verify_totp_code, send_mfa_login_email, mark_totp_enabled
from utils.face_utils import verify_uploaded_face
from utils.webauthn_utils import (
    webauthn, _webauthn_available, _wa_rp_id, _wa_check_rp_id, _wa_origins,
    _wa_b64url_encode, _wa_b64url_decode, _wa_verify_and_store_registration,
    _mobile_biometric_issue_nonce, _mobile_biometric_attest,
)

UPLOAD_FOLDER = app.config["UPLOAD_FOLDER"]

# How long a successful kiosk face-match (see api_kiosk_enroll_face_verify)
# stays valid as proof of identity before it must be redone. Mirrors the
# window pattern used for wa_fp_verified_at/soc_2fa_verified_at elsewhere --
# short enough that a stolen session cookie right after enrollment can't
# reuse a stale face-match to enroll a second, different employee_id.
KIOSK_FACE_VERIFY_WINDOW_SEC = 5 * 60
if _webauthn_available:
    from utils.webauthn_utils import (
        AuthenticatorSelectionCriteria, AuthenticatorAttachment,
        UserVerificationRequirement, ResidentKeyRequirement,
        PublicKeyCredentialDescriptor, AuthenticatorTransport,
        COSEAlgorithmIdentifier, AttestationConveyancePreference,
    )

auth_bp = Blueprint("auth", __name__)

# Detection only -- never a security control. Every query in this codebase
# is already parameterized (verified separately), so a payload matching
# this can't actually inject anything; it just tells us someone is
# probing rather than mistyping a username. Deliberately narrow (classic
_INJECTION_PATTERN_RE = re.compile(
    r"('|--|;|\bunion\b|\bor\b\s+['\"0-9]|<script\b)", re.IGNORECASE
)

_MFA_OTP_TTL_SEC = 300


def _login_needs_company():
    """True when /login has no company to sign in to: LOGIN_ASKS_FOR_COMPANY
    is on (a multi-company server) and neither the URL (/<company>/login)
    nor this session names one. Off, a bare /login keeps signing in to the
    default single-company database, as local dev and the tests use it."""
    if os.environ.get("LOGIN_ASKS_FOR_COMPANY", "").strip().lower() not in ("1", "true", "yes"):
        return False
    return not (request.environ.get("hrz.tenant_slug") or session.get("tenant_slug"))


def _find_company_slug(typed):
    """The active company a person typed on /login's company step: its URL
    (acme-corp, hrzest.com/acme-corp/login, https://...) or its name
    (Acme Corp). None if nothing matches."""
    typed = (typed or "").strip()[:200]
    if not typed:
        return None
    parts = [p for p in re.sub(r"^[a-z]+://", "", typed, flags=re.I).split("/") if p]
    if parts and ("." in parts[0] or ":" in parts[0]):
        parts = parts[1:]  # the host part of a pasted address
    candidates = [re.sub(r"[^a-z0-9]+", "-", typed.lower()).strip("-")]
    if parts:
        candidates.insert(0, parts[0].lower())
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    try:
        cur.execute(
            "SELECT subdomain FROM tenants WHERE status='active' "
            "AND (subdomain = ANY(%s) OR LOWER(company_name) = LOWER(%s)) "
            "ORDER BY subdomain = %s DESC LIMIT 1",
            (candidates, typed, candidates[0])
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return row[0] if row else None


@auth_bp.route("/api/company/resolve", methods=["GET"])
@limiter.limit("30 per minute")
def api_company_resolve():
    """The app's sign-in screen: which company did the person type -- its URL
    (acme-corp, hrzest.com/acme-corp/login, https://...) or its name? The same
    lookup as /login's company step (_find_company_slug), so the app can tell
    "no such company" apart from a wrong password: an unknown /<slug>/ prefix
    never reaches a login route at all. Public like every company's own
    /<slug>/login page, and rate-limited."""
    slug = _find_company_slug(request.args.get("q", ""))
    if not slug:
        return jsonify({"ok": False, "msg": "We couldn't find that company. Check the company URL in your welcome email."}), 404
    return jsonify({"ok": True, "slug": slug})


def _kyc_verification_blocks_login():
    """True if this tenant's company-KYC review (blueprints/org.py's
    automated/manual document-verification pipeline) REJECTED its
    documents. Blocks ADMIN login entirely for the whole tenant, regardless
    of which admin_users account is used or whether MFA is configured --
    it's the company that failed review, not any one account, so gating a
    single login path would leave others (e.g. an auto-provisioned HR
    account) able to slip through. Never applies to employee logins
    (blueprints/auth.py's employee branch never calls this).

    Documents not yet uploaded ('pending_documents') or awaiting review
    ('pending_review') do NOT block: a new company's admin lands in the
    dashboard straight after registering and uploads the documents from
    its registration checklist (app.py's inject_registration_checklist).
    Login used to stay blocked until approval, when documents were part of
    signup itself.

    No tenant_applications row at all -- a legacy pre-KYC tenant, or one
    created via Platform Admin's own always-free direct-create tool,
    which never collects KYC documents -- is NOT blocked, same
    "nothing to show/block" reasoning app.py's
    inject_company_verification_status() context processor already uses
    for its advisory banner (this function's query is the same join
    shape). Fails OPEN on any DB error: a transient master-schema blip
    must not lock every already-verified tenant's admin out of the whole
    platform, same posture inject_company_verification_status() already
    takes for the same reason."""
    try:
        from database import get_master_db
        db = get_master_db()
        cur = db.cursor(buffered=True)
        cur.execute(
            "SELECT ta.doc_review_status FROM tenant_applications ta JOIN tenants t ON ta.tenant_id=t.id "
            "WHERE t.db_name=%s ORDER BY ta.id DESC LIMIT 1",
            (g.tenant_db,)
        )
        row = cur.fetchone()
        cur.close()
        db.close()
        return bool(row) and row[0] == "rejected"
    except Exception as exc:
        app_log.warning("_kyc_verification_blocks_login: check failed, failing open: %s", exc)
        return False


_KYC_REJECTED_LOGIN_MSG = (
    "Your company's documents were not approved. Check the email we sent for the reason and a "
    "link to upload corrected documents, or contact support."
)


def _start_login_mfa(co, login_template, kind, identifier, email, role_label):
    """Common second step once a password has already checked out (called
    from admin_login()'s admin/employee branches): email a one-time code
    instead of completing the session immediately, and redirect to
    /mfa_verify to finish."""
    if not email:
        log_security_event(
            "auth.mfa_email_missing", "Account has no email on file for login MFA delivery",
            level="ERROR", identifier=identifier,
        )
        _record_login_failure(identifier)
        return render_template(login_template, co=co, error="Invalid credentials. Check your ID and password.")

    # Only the opaque challenge id goes into the (client-readable) session
    # cookie -- the code itself is kept server-side as an HMAC.
    with _db() as (cursor, conn):
        challenge_id, otp_code = issue_challenge(cursor, kind, identifier, _MFA_OTP_TTL_SEC)
        conn.commit()
    send_mfa_login_email(email, identifier, role_label, "", otp_code)
    session.clear()
    session["mfa_pending"] = True
    session["mfa_user"] = identifier  # display only -- never trusted for auth
    session["mfa_challenge_id"] = challenge_id
    session["mfa_issued_at"] = time.time()
    return redirect(tpath("/mfa_verify"))


# employees.role is free-text (whatever an admin typed as a job title) --
# an EXACT, case-insensitive match only, never a substring check, so a
# title like "HR Executive" or "Senior HR" does NOT grant this. Getting
# this wrong turns a job-title text field into an accidental privilege
# escalation path.
_HR_EMPLOYEE_ROLE = "hr"


def _ensure_hr_admin_account(employee_id, email):
    """Auto-provisions (idempotently) the admin_users row that backs HR-tier
    admin-panel access for an employee whose role is exactly "HR" -- see
    _finish_employee_login() below. Without a REAL admin_users row here,
    granting admin-panel access by just faking session["admin_logged_in"]
    breaks the first time MANDATORY_ADMIN_MFA is turned on:
    _enforce_admin_mfa_enrollment() (app.py) looks up session["admin_username"]
    in admin_users to check TOTP enrollment, finds no row for an employee_id,
    and permanently redirect-loops that account to the enrollment page with
    no way to ever complete it.

    The generated password is random and never shown to or typed by the
    employee -- admin_login() always checks admin_users by username FIRST,
    so an unguessable, never-typed password here is what prevents a
    coincidental identifier collision from letting the wrong credential
    authenticate the wrong account; the employee keeps logging in with
    their own employee password exactly as before, and only reaches this
    account via _finish_employee_login()'s session setup, never a direct
    admin_users password check.
    """
    with _db() as (cursor, db):
        cursor.execute("SELECT 1 FROM admin_users WHERE username=%s", (employee_id,))
        if cursor.fetchone():
            return
        cursor.execute(
            "INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,1)",
            (employee_id, generate_password_hash(secrets.token_urlsafe(32)), HR_ROLE, email)
        )
        db.commit()
    log_security_event(
        "auth.hr_admin_autoprovisioned",
        f"Auto-created admin_users HR-role account for employee '{employee_id}' (employees.role is exactly 'HR')",
        level="INFO", identifier=employee_id,
    )


# Same exact-match, case-insensitive caveat as _HR_EMPLOYEE_ROLE above --
# a job title like "Project Manager" or "Store Manager" does NOT grant this.
_MANAGER_EMPLOYEE_ROLE = "manager"


def _ensure_manager_admin_account(employee_id, email):
    """Auto-provisions (idempotently) the admin_users row that backs
    manager-tier admin-panel access for an employee whose role is exactly
    "Manager" -- see _finish_employee_login() below. Identical rationale
    and mechanics to _ensure_hr_admin_account() above (random, never-shown
    password; only ever reached via the employee-login auto-route, never a
    direct admin_users credential check) -- kept as a separate function
    rather than a shared helper so each role's log message/role constant
    stays a plain, greppable literal rather than a parameterized one."""
    with _db() as (cursor, db):
        cursor.execute("SELECT 1 FROM admin_users WHERE username=%s", (employee_id,))
        if cursor.fetchone():
            return
        cursor.execute(
            "INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,1)",
            (employee_id, generate_password_hash(secrets.token_urlsafe(32)), MANAGER_ROLE, email)
        )
        db.commit()
    log_security_event(
        "auth.manager_admin_autoprovisioned",
        f"Auto-created admin_users manager-role account for employee '{employee_id}' (employees.role is exactly 'Manager')",
        level="INFO", identifier=employee_id,
    )


def _finish_employee_login(employee_id, name, role, force_pin_change, email):
    """Completes an employee login -- called from both the direct
    password-verified branch below and mfa_verify()'s employee branch, so
    the two paths (MANDATORY_LOGIN_MFA off vs. on) can't drift apart.

    A pending forced PIN change always wins, regardless of role -- an
    employee who hasn't changed their initial PIN yet must not be able to
    skip straight into the HR admin panel; they get the normal employee
    session and /force_change_pin exactly as before this existed, and the
    HR check only applies on a later login once that's done.

    An employee past the last working day of an accepted resignation gets
    no session at all -- checked here too, not just at the password step,
    because a code emailed before that day can be entered after it.

    Otherwise: if this employee's role is exactly "HR" (see
    _HR_EMPLOYEE_ROLE), auto-provisions and logs into the backing
    admin_users HR account instead of a normal employee session, landing
    on the HR admin panel (/employees) -- the same destination an
    admin_users role='hr' login already reaches. Every other employee gets
    the unchanged normal employee session and /employee_portal.
    """
    ended_on = employment_ended_on(employee_id)
    if ended_on:
        session.clear()
        return render_template("admin_login.html", co=get_company_settings(), error=access_ended_message(ended_on))

    if force_pin_change:
        session.clear()
        session["employee_id"] = employee_id
        session["employee_name"] = name
        session["employee_role"] = role or ""
        session["_session_created"] = time.time()
        session["_fpc"] = True
        session.permanent = True
        ensure_session_id(session)
        stamp_password_version("employees", employee_id)
        if email:
            notify_if_new_login_ip(employee_id, "employee", request.remote_addr, name, email)
        return redirect(tpath("/force_change_pin"))

    if (role or "").strip().lower() == _HR_EMPLOYEE_ROLE:
        _ensure_hr_admin_account(employee_id, email)
        # If MANDATORY_LOGIN_MFA is on, every call site that can reach this
        # function (the direct branch below, mfa_verify()'s employee
        # branch, and force_change_pin()'s completion) is only reachable
        # AFTER this employee already verified the emailed OTP -- that
        # already-completed step is this login's MFA, exactly like the
        # real admin_users MFA branch above treats it (see its own
        # mark_totp_enabled() call). Without this, app.py's
        # _enforce_admin_mfa_enrollment would immediately bounce this
        # freshly auto-provisioned admin_users row (no TOTP enrolled yet)
        # to a *separate* authenticator-app enrollment page on its very
        # next request, demanding a second, redundant proof of identity
        # right after the first. When MANDATORY_LOGIN_MFA is off, no OTP
        # step happened, so this must NOT be marked -- that's what still
        # correctly forces real TOTP enrollment before HR admin access in
        # that configuration.
        if app.config["MANDATORY_LOGIN_MFA"]:
            mark_totp_enabled(employee_id)
        session.clear()
        session["admin_logged_in"] = True
        session["admin_username"] = employee_id
        session["admin_role"] = HR_ROLE
        session["_session_created"] = time.time()
        session.permanent = True
        ensure_session_id(session)
        stamp_password_version("employees", employee_id)
        log_security_event(
            "auth.admin_login_success",
            f"Employee '{employee_id}' logged in via employee credentials, routed to HR admin panel (role='HR')",
            level="INFO", identifier=employee_id,
        )
        if email:
            notify_if_new_login_ip(employee_id, "admin", request.remote_addr, employee_id, email)
        return redirect(tpath("/hr_dashboard"))

    if (role or "").strip().lower() == _MANAGER_EMPLOYEE_ROLE:
        _ensure_manager_admin_account(employee_id, email)
        # Same MFA bookkeeping rationale as the HR branch above.
        if app.config["MANDATORY_LOGIN_MFA"]:
            mark_totp_enabled(employee_id)
        session.clear()
        session["admin_logged_in"] = True
        session["admin_username"] = employee_id
        session["admin_role"] = MANAGER_ROLE
        session["_session_created"] = time.time()
        session.permanent = True
        ensure_session_id(session)
        stamp_password_version("employees", employee_id)
        log_security_event(
            "auth.admin_login_success",
            f"Employee '{employee_id}' logged in via employee credentials, routed to manager approvals panel (role='Manager')",
            level="INFO", identifier=employee_id,
        )
        if email:
            notify_if_new_login_ip(employee_id, "admin", request.remote_addr, employee_id, email)
        # No manager-specific dashboard exists -- /leave_holidays is the
        # actual real capability (see MANAGER_ROLE's docstring in
        # utils/auth.py), and is already reachable by any admin_logged_in
        # session today.
        return redirect(tpath("/leave_holidays"))

    session.clear()
    session["employee_id"] = employee_id
    session["employee_name"] = name
    session["employee_role"] = role or ""
    session["_session_created"] = time.time()
    # No session["_fpc"] here -- session.clear() above already leaves it
    # absent, matching employee_required()'s `session.get("_fpc")` check
    # (falsy either way) and force_change_pin()'s original
    # session.pop("_fpc", None) semantics precisely, not just a falsy value.
    session.permanent = True
    ensure_session_id(session)
    stamp_password_version("employees", employee_id)
    if email:
        notify_if_new_login_ip(employee_id, "employee", request.remote_addr, name, email)
    return redirect(tpath("/employee_portal"))


@auth_bp.route("/switch_to_my_employee_portal", methods=["POST"])
@role_required(HR_ROLE)
def switch_to_my_employee_portal():
    """Lets an HR-role admin session (templates/admin_base.html's "My
    Profile" button) hop over to the normal employee self-service portal
    for that SAME person's own employee record -- the same
    profile/leave/payslip/attendance view a regular employee sees.

    This is a view-mode toggle for one already-authenticated identity,
    not a second login or a way to view anyone else's portal, so it swaps
    the session in place (no session.clear(), no re-auth) rather than
    routing back through the real login flow. session["admin_username"]
    is stashed under _hr_return_username so switch_back_to_hr_panel()
    below can restore the HR session later without asking for credentials
    or MFA again -- nothing more privileged than what this request
    already held is being granted back.

    404s harmlessly back to /employees if this HR account isn't actually
    backed by an employees row (e.g. a standalone admin_users role='hr'
    account created via /hr_accounts, not an employee auto-routed here) --
    see the hr_has_own_employee context var that hides the button for
    that case in the first place."""
    employee_id = session.get("admin_username")
    with _db() as (cursor, _conn):
        cursor.execute("SELECT name, role FROM employees WHERE employee_id=%s", (employee_id,))
        row = cursor.fetchone()
    if not row:
        flash("No personal employee profile is linked to this HR account.", "error")
        return redirect(tpath("/hr_dashboard"))
    session["_hr_return_username"] = employee_id
    session.pop("admin_logged_in", None)
    session.pop("admin_role", None)
    session.pop("admin_username", None)
    session["employee_id"] = employee_id
    session["employee_name"] = row[0]
    session["employee_role"] = row[1] or ""
    session.permanent = True
    log_security_event(
        "auth.hr_switched_to_own_portal",
        f"HR account '{employee_id}' switched to their own employee portal",
        level="INFO", identifier=employee_id,
    )
    return redirect(tpath("/employee_portal"))


@auth_bp.route("/switch_back_to_hr_panel", methods=["POST"])
@employee_required
def switch_back_to_hr_panel():
    """Reverses switch_to_my_employee_portal() -- templates/
    _employee_sidebar.html's "Back to HR Dashboard" button, shown only
    when _hr_return_username is present in session (i.e. this employee
    session was reached via the switch above, not a normal employee
    login). Restores the HR admin session for the same identity without
    re-authenticating."""
    return_username = session.pop("_hr_return_username", None)
    if not return_username or return_username != session.get("employee_id"):
        return redirect(tpath("/employee_portal"))
    session.pop("employee_id", None)
    session.pop("employee_name", None)
    session.pop("employee_role", None)
    session["admin_logged_in"] = True
    session["admin_username"] = return_username
    session["admin_role"] = HR_ROLE
    session.permanent = True
    log_security_event(
        "auth.hr_switched_back_to_panel",
        f"HR account '{return_username}' switched back to the HR admin panel",
        level="INFO", identifier=return_username,
    )
    return redirect(tpath("/hr_dashboard"))


def begin_admin_session(username, role):
    """Starts a fresh logged-in admin session for admin_users `username` in
    the current company (g.tenant_db) -- shared by the password login, the
    emailed-code login (mfa_verify) and a new company's admin landing in
    its dashboard right after registering (org._log_in_new_company_admin).
    Clears the session first, so a pre-login session can't be fixed onto
    the logged-in one."""
    session.clear()
    session["admin_logged_in"] = True
    session["admin_username"] = username
    session["admin_role"] = role
    session["_session_created"] = time.time()
    session.permanent = True
    ensure_session_id(session)
    stamp_password_version("admin_users", username)


@auth_bp.route("/login", methods=["GET", "POST"])
@auth_bp.route("/admin_login", methods=["GET", "POST"])
@auth_bp.route("/admin-login", methods=["GET", "POST"])
@limiter.limit("60 per 15 minutes")
@limiter.limit("20 per minute")
@limiter.limit("150 per hour")
def admin_login():
    co = get_company_settings()
    if session.get("admin_logged_in"):
        return redirect(tpath("/admin"))
    if session.get("employee_id"):
        return redirect(tpath("/employee_portal"))
    if _login_needs_company():
        # Which company first (a GET form: ?company=), then that company's
        # own /<company>/login -- a bare /login has no company to sign in to.
        typed = request.args.get("company")
        error = None
        if typed is not None:
            slug = _find_company_slug(typed)
            if slug:
                return redirect(f"/{slug}/login")
            error = "We couldn't find that company. Check the company URL in your welcome email."
        return render_template("admin_login.html", co=co, pick_company=True, error=error,
                               company=typed or "")
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "").strip()
        # Password is intentionally never inspected or logged here -- only
        # the identifier field is checked, and only its shape (matched
        # pattern name), never the raw value, goes into the alert.
        _inj_match = _INJECTION_PATTERN_RE.search(identifier)
        if _inj_match:
            log_security_event(
                "auth.injection_attempt",
                "Login identifier matched a SQL-injection/XSS-shaped pattern",
                level="ERROR", pattern=_inj_match.group(1)[:20],
            )
        # Check lockout before attempting any credential check
        locked, until = _check_login_lockout(identifier)
        if locked:
            return render_template("admin_login.html", co=co,
                                   error=f"Account locked until {until} due to too many failed attempts.")

        # CAPTCHA gate: once this identifier has CAPTCHA_AFTER_ATTEMPTS (2)
        # failures already on record, every further attempt must carry a
        # verified Turnstile token before the password is even checked.
        # No-op entirely when Turnstile isn't configured (turnstile_enabled()
        # is False) -- see utils/auth.py's fail-open rationale.
        current_failed_count = _get_failed_count(identifier)
        needs_captcha = turnstile_enabled() and current_failed_count >= CAPTCHA_AFTER_ATTEMPTS
        if needs_captcha:
            token = request.form.get("cf-turnstile-response", "")
            if not verify_turnstile(token, request.remote_addr):
                return render_template("admin_login.html", co=co,
                                       error="Please complete the verification challenge.",
                                       show_captcha=True, turnstile_site_key=_TURNSTILE_SITE_KEY)

        # Whether the failure paths below should show the widget on the
        # NEXT attempt -- computed from current_failed_count rather than a
        # fresh DB read, since _record_login_failure's write is async and
        # may not have landed by the time this response is built.
        will_need_captcha = turnstile_enabled() and (current_failed_count + 1) >= CAPTCHA_AFTER_ATTEMPTS

        # Try admin credentials first
        with _db() as (cursor, db):
            cursor.execute(
                "SELECT password, COALESCE(role,'admin'), email, COALESCE(is_active,1) FROM admin_users WHERE username=%s",
                (identifier,)
            )
            admin_row = cursor.fetchone()
            # A company's sign-up admin logs in with their email, stored
            # lowercase (org.create_org) -- still match it when a phone
            # keyboard capitalised the first letter.
            if not admin_row and "@" in identifier and identifier != identifier.lower():
                cursor.execute(
                    "SELECT password, COALESCE(role,'admin'), email, COALESCE(is_active,1) FROM admin_users "
                    "WHERE username=%s", (identifier.lower(),)
                )
                admin_row = cursor.fetchone()
                if admin_row:
                    identifier = identifier.lower()
        # Every account -- top-level admins included -- needs its password
        # first; the emailed code is only ever a SECOND factor. (Admins used
        # to skip the password entirely, which made a readable/guessable
        # code the sole credential.) Unknown identifiers, wrong passwords
        # and terminated accounts all get the same generic error below.
        if admin_row and check_password_hash(admin_row[0], password):
            if not admin_row[3]:
                # Terminated account -- same generic error as a wrong
                # password, so a probe can't distinguish "deactivated" from
                # "doesn't exist"/"wrong password".
                _record_login_failure(identifier)
                log_security_event(
                    "access.denied", "Login attempt against a terminated admin account",
                    level="WARNING", identifier=identifier,
                )
                return render_template(
                    "admin_login.html", co=co,
                    error="Invalid credentials. Check your ID and password.",
                    show_captcha=will_need_captcha, turnstile_site_key=_TURNSTILE_SITE_KEY,
                )
            _clear_login_failures(identifier)
            # An HR/manager employee's admin account is named after their
            # employee ID: it ends with their employment too.
            ended_on = employment_ended_on(identifier)
            if ended_on:
                return render_template("admin_login.html", co=co, error=access_ended_message(ended_on))
            if _kyc_verification_blocks_login():
                return render_template("admin_login.html", co=co, error=_KYC_REJECTED_LOGIN_MSG)
            # Upgrade legacy hash to bcrypt on first successful login
            if admin_row[0] and not admin_row[0].startswith("$2"):
                with _db() as (_uc, _ud):
                    _uc.execute("UPDATE admin_users SET password=%s WHERE username=%s",
                                (generate_password_hash(password), identifier))
                    _ud.commit()
            if app.config["MANDATORY_LOGIN_MFA"]:
                return _start_login_mfa(co, "admin_login.html", "admin_users", identifier, admin_row[2],
                                        "Executive Administrator" if admin_row[1] == "admin" else admin_row[1].title())
            begin_admin_session(identifier, admin_row[1])
            log_security_event(
                "auth.admin_login_success", f"Admin login for '{identifier}' (role={admin_row[1]})",
                level="INFO", identifier=identifier,
            )
            if admin_row[2]:
                notify_if_new_login_ip(identifier, "admin", request.remote_addr, identifier, admin_row[2])
            if admin_row[1] == HR_ROLE:
                dest = redirect(tpath("/hr_dashboard"))
            elif admin_row[1] == MANAGER_ROLE:
                dest = redirect(tpath("/leave_holidays"))
            else:
                dest = redirect(tpath("/admin"))
            return dest
        # Try employee credentials
        with _db() as (cursor, db):
            cursor.execute(
                "SELECT employee_id, name, role, password, COALESCE(force_pin_change,0), email, "
                "COALESCE(must_change_password,0) FROM employees WHERE employee_id=%s",
                (identifier,)
            )
            emp_row = cursor.fetchone()
        if emp_row:
            stored_pwd = emp_row[3]
            if not stored_pwd or not check_password_hash(stored_pwd, password):
                _record_login_failure(identifier)
                return render_template("admin_login.html", co=co, error="Invalid credentials. Check your ID and password.",
                                       show_captcha=will_need_captcha, turnstile_site_key=_TURNSTILE_SITE_KEY)
            _clear_login_failures(identifier)
            # Past the last working day of an accepted resignation: the right
            # password, but no access (and no emailed code). Records are kept.
            ended_on = employment_ended_on(emp_row[0])
            if ended_on:
                return render_template("admin_login.html", co=co, error=access_ended_message(ended_on))
            # Upgrade legacy hash to bcrypt on first successful login
            if stored_pwd and not stored_pwd.startswith("$2"):
                with _db() as (_uc, _ud):
                    _uc.execute("UPDATE employees SET password=%s WHERE employee_id=%s",
                                (generate_password_hash(password), emp_row[0]))
                    _ud.commit()
            if app.config["MANDATORY_LOGIN_MFA"]:
                return _start_login_mfa(co, "admin_login.html", "employee", emp_row[0], emp_row[5], "Employee")
            return _finish_employee_login(emp_row[0], emp_row[1], emp_row[2], bool(emp_row[4] or emp_row[6]),
                                          emp_row[5])
        if not admin_row:
            # Unknown identifier: spend the same bcrypt time a real
            # wrong-password check would, so response timing can't reveal
            # whether the account exists.
            dummy_password_check(password)
        _record_login_failure(identifier)
        return render_template("admin_login.html", error="Invalid credentials. Check your ID and password.",
                               show_captcha=will_need_captcha, turnstile_site_key=_TURNSTILE_SITE_KEY)
    return render_template("admin_login.html", co=co)


@auth_bp.route("/mfa_verify", methods=["GET", "POST"])
# Raised from 8/15min for the same reason as admin_login above -- all
# visitors share one apparent IP under rootless Podman's port-forwarding.
# The OTP itself is still the real defense (short TTL, compare_digest).
@limiter.limit("40 per 15 minutes")
def mfa_verify():
    """Completion step for _start_login_mfa(): checks the emailed one-time
    code, then builds the real admin/HR/employee session."""
    display_user = session.get("mfa_user")
    challenge_id = session.get("mfa_challenge_id")
    if not display_user or not challenge_id or not session.get("mfa_pending"):
        return redirect(tpath("/login"))

    issued_at = session.get("mfa_issued_at") or 0
    if (time.time() - issued_at) > _MFA_OTP_TTL_SEC:
        with _db() as (cursor, conn):
            discard_challenge(cursor, challenge_id)
            conn.commit()
        session.clear()
        return render_template("mfa_verify.html", error="Your code expired. Please log in again.")

    if request.method == "POST":
        submitted = (request.form.get("otp_code") or "").strip()
        # Who is logging in comes from the server-side challenge row, never
        # from the session -- the session only carries the opaque id.
        with _db() as (cursor, conn):
            verified = verify_challenge(cursor, challenge_id, submitted, ("admin_users", "employee"))
            conn.commit()
        if verified:
            kind, username = verified
            if kind == "employee":
                with _db() as (cursor, db):
                    cursor.execute(
                        "SELECT employee_id, name, role, COALESCE(force_pin_change,0), email, "
                        "COALESCE(must_change_password,0) FROM employees WHERE employee_id=%s", (username,)
                    )
                    row = cursor.fetchone()
                if not row:
                    session.clear()
                    return redirect(tpath("/login"))
                return _finish_employee_login(row[0], row[1], row[2], bool(row[3] or row[5]), row[4])
            else:
                with _db() as (cursor, db):
                    cursor.execute("SELECT role, email FROM admin_users WHERE username=%s", (username,))
                    row = cursor.fetchone()
                if not row:
                    session.clear()
                    return redirect(tpath("/login"))
                role = row[0] or "admin"
                # This emailed code IS this login's MFA -- mark enrolled so
                # app.py's _enforce_admin_mfa_enrollment (which still applies
                # to admin/manager/hr sessions) doesn't also demand a
                # separate authenticator-app enrollment right after.
                mark_totp_enabled(username)
                begin_admin_session(username, role)
                if row[1]:
                    notify_if_new_login_ip(username, "admin", request.remote_addr, username, row[1])
                if role == HR_ROLE:
                    dest = redirect(tpath("/hr_dashboard"))
                elif role == MANAGER_ROLE:
                    dest = redirect(tpath("/leave_holidays"))
                else:
                    dest = redirect(tpath("/admin"))
                return dest

        log_security_event("auth.mfa_failure", "Invalid login MFA code", level="WARNING",
                           identifier=_mask_identifier(display_user))
        return render_template("mfa_verify.html", username=display_user, error="Invalid code. Please try again.")

    return render_template("mfa_verify.html", username=display_user)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    # POST-only (CSRF-protected form) so another site can't log users out
    # with an <img src="/logout">.
    session.clear()
    # "/" is the platform operator's own login (see blueprints/core.py's
    # home()), not somewhere a tenant admin logging out should land --
    # send them back to the check-in kiosk instead, same as before "/"
    # was repurposed.
    return redirect(tpath("/checkin"))


@auth_bp.route("/change_admin_password", methods=["POST"])
@admin_required
def change_admin_password():
    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")
    # Use the logged-in admin's username, not a hardcoded 'admin' string.
    # A hardcoded value lets any admin account change the 'admin' password
    # if they know its current value -- a cross-account privilege escalation.
    logged_in_as = session.get("admin_username", "admin")
    if not new_pw or new_pw != confirm_pw:
        return redirect(tpath("/settings?tab=email&pwd_error=mismatch"))
    _pw_ok, _pw_err = validate_new_password(new_pw)
    if not _pw_ok:
        return redirect(tpath("/settings?tab=email&pwd_error=weak"))
    if not verify_and_update_password("admin_users", "username", logged_in_as, current_pw, new_pw):
        return redirect(tpath("/settings?tab=email&pwd_error=wrong"))
    return redirect(tpath("/settings?tab=email&pwd_ok=1"))


@auth_bp.route("/api/admin/password", methods=["POST"])
@api_required
def api_change_admin_password():
    """Bearer-token twin of change_admin_password() above -- same
    own-account-only scoping (the Bearer token's identity, not a
    caller-supplied username, so this can't be used to change a different
    account's password) and no role restriction, matching @admin_required
    above (any admin-panel role, not just Admin, can change their own
    password on web)."""
    from flask import g as _g
    data = request.get_json(silent=True) or {}
    current_pw = data.get("current_password") or ""
    new_pw = data.get("new_password") or ""
    confirm_pw = data.get("confirm_password") or ""
    username = _g.api_user

    if not new_pw or new_pw != confirm_pw:
        return jsonify({"ok": False, "msg": "New password and confirmation must match."}), 400
    _pw_ok, _pw_err = validate_new_password(new_pw)
    if not _pw_ok:
        return jsonify({"ok": False, "msg": _pw_err}), 400

    if not verify_and_update_password("admin_users", "username", username, current_pw, new_pw):
        return jsonify({"ok": False, "msg": "Current password is incorrect."}), 400
    log_security_event("auth.password_changed", f"Password changed for '{username}' via mobile app",
                       level="INFO", identifier=username)
    return jsonify({"ok": True, "msg": "Password updated."})


@auth_bp.route("/admin_set_recovery_email", methods=["POST"])
@admin_required
def admin_set_recovery_email():
    email = request.form.get("recovery_email", "").strip()
    username = session.get("admin_username", "admin")
    if email:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("UPDATE admin_users SET email=%s WHERE username=%s", (email, username))
        db.commit()
        cursor.close()
        db.close()
    # return_to lets the Profile page's Company Info tab reuse this same
    # route for its own admin-contact-email field instead of re-implementing
    # it -- defaults to the original hardcoded redirect so every existing
    # caller (templates/settings.html's form has no such field) is unaffected.
    return_to = request.form.get("return_to", "").strip()
    if return_to == "profile":
        return redirect(tpath("/profile?tab=company&email_ok=1"))
    return redirect(tpath("/settings?tab=email&email_ok=1#password-management"))


# Was 1 hour -- widened after reports of links "expiring" almost
# immediately, which traced back to slow/queued SMTP delivery on the
# hosted deployment eating most or all of a short window before the email
# even arrived, not an actual bug in the expiry check itself. 3 hours
# gives real-world delivery delays room without matching the 24h welcome-
# email window (_send_welcome_credentials_email in blueprints/employees.py),
# which intentionally stays longer since that link may sit unread further
# out than an active forgot-password request.
RESET_TOKEN_VALID_HOURS = 3


def _new_reset_token():
    """(token, token_hash, expiry) for a password-reset link -- shared by
    admin_forgot_password and employee_forgot_password, which previously
    each generated this identically."""
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=RESET_TOKEN_VALID_HOURS)
    return token, token_hash, expiry


def _find_by_reset_token(cursor, table, id_column, token):
    """Look up a still-valid (unexpired) reset token. Shared by
    admin_reset_password and employee_reset_password. table/id_column are
    always call-site literals ("admin_users"/"id" or "employees"/
    "employee_id"), never request-controlled."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    cursor.execute(
        f"SELECT {id_column} FROM {table} WHERE reset_token=%s AND reset_token_expiry > %s",  # nosec B608 -- call-site literals
        (token_hash, datetime.datetime.utcnow())
    )
    return cursor.fetchone()


@auth_bp.route("/admin_forgot_password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def admin_forgot_password():
    if request.method == "GET":
        return render_template("admin_forgot_password.html",
                               sent=False, error=None)
    admin_email = request.form.get("email", "").strip().lower()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT id FROM admin_users WHERE LOWER(email)=%s", (admin_email,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        # Return the same message whether the email exists or not (no account enumeration)
        return render_template("admin_forgot_password.html", sent=True, error=None)
    token, token_hash, expiry = _new_reset_token()
    admin_id = row[0]
    cursor.execute(
        "UPDATE admin_users SET reset_token=%s, reset_token_expiry=%s WHERE id=%s",
        (token_hash, expiry, admin_id)
    )
    db.commit()
    cursor.close()
    db.close()
    cfg = get_email_config()
    if not cfg:
        return render_template("admin_forgot_password.html", sent=False,
                               error="Email service not configured. Go to Admin → Email Settings first.")
    reset_url = f"{_safe_app_url()}/admin_reset_password/{token}"
    html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">🔐 Admin Password Reset</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">HRzest.com</div>
  </div>
  <div style="padding:28px;">
    <p style="font-size:15px;color:#1e293b;margin-bottom:20px;">You requested a password reset for the admin account.</p>
    <a href="{reset_url}" style="display:block;text-align:center;padding:14px 28px;background:#1e3a8a;color:white;
      border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;margin-bottom:20px;">
      Reset My Password
    </a>
    <p style="font-size:13px;color:#64748b;">This link expires in <strong>{RESET_TOKEN_VALID_HOURS} hours</strong>.
      If you did not request this, ignore this email.</p>
    <p style="font-size:12px;color:#94a3b8;margin-top:12px;">Or copy this link: {reset_url}</p>
  </div>
</div>"""
    try:
        send_email_smtp(admin_email, "Admin Password Reset -- HRzest.com", html_body, cfg)
    except Exception:
        app_log.error("Failed to send admin password reset email", exc_info=True)
        return render_template("admin_forgot_password.html", sent=False,
                               error="Failed to send email. Please check your email settings.")
    return render_template("admin_forgot_password.html", sent=True, error=None)


@auth_bp.route("/admin_reset_password/<token>", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def admin_reset_password(token):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    row = _find_by_reset_token(cursor, "admin_users", "id", token)
    if not row:
        cursor.close()
        db.close()
        return render_template("admin_reset_password.html", valid=False, done=False, token=token)
    if request.method == "GET":
        cursor.close()
        db.close()
        return render_template("admin_reset_password.html", valid=True, done=False, token=token, error=None)
    new_pw = request.form.get("new_password", "").strip()
    confirm_pw = request.form.get("confirm_password", "").strip()
    _pw_ok, _pw_err = validate_new_password(new_pw)
    if not _pw_ok:
        cursor.close()
        db.close()
        return render_template("admin_reset_password.html", valid=True, done=False,
                               token=token, error=_pw_err)
    if new_pw != confirm_pw:
        cursor.close()
        db.close()
        return render_template("admin_reset_password.html", valid=True, done=False,
                               token=token, error="Passwords do not match.")
    admin_id = row[0]
    cursor.execute(
        "UPDATE admin_users SET password=%s, reset_token=NULL, reset_token_expiry=NULL WHERE id=%s "
        "RETURNING username",
        (generate_password_hash(new_pw), admin_id)
    )
    _reset_row = cursor.fetchone()
    if _reset_row:
        record_password_change(cursor, "admin_users", _reset_row[0])
    db.commit()
    cursor.close()
    db.close()
    return render_template("admin_reset_password.html", valid=True, done=True, token=token, error=None)


@auth_bp.route("/employee_forgot_password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def employee_forgot_password():
    if request.method == "GET":
        return render_template("employee_forgot_password.html", sent=False, error=None)
    emp_id = request.form.get("employee_id", "").strip()
    if not emp_id:
        return render_template("employee_forgot_password.html", sent=False, error="Please enter your Employee ID.")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id, email, name FROM employees WHERE employee_id=%s", (emp_id,))
    row = cursor.fetchone()
    if not row or not row[1]:
        cursor.close()
        db.close()
        # Generic message to avoid account enumeration; also covers "no email on file"
        return render_template("employee_forgot_password.html", sent=True, error=None)
    db_email = row[1]
    emp_name = _html.escape(row[2] or emp_id)
    token, token_hash, expiry = _new_reset_token()
    cursor.execute("UPDATE employees SET reset_token=%s, reset_token_expiry=%s WHERE employee_id=%s",
                   (token_hash, expiry, emp_id))
    db.commit()
    cursor.close()
    db.close()
    cfg = get_email_config()
    if not cfg:
        return render_template("employee_forgot_password.html", sent=False,
                               error="Email service not configured. Please contact HR.")
    reset_url = f"{_safe_app_url()}/employee_reset_password/{token}"
    html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">🔐 Password Reset</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">Employee Portal</div>
  </div>
  <div style="padding:28px;">
    <p style="font-size:15px;color:#1e293b;margin-bottom:20px;">Hi <strong>{emp_name}</strong>, you requested a
      password reset for Employee ID <strong>{emp_id}</strong>.</p>
    <a href="{reset_url}" style="display:block;text-align:center;padding:14px 28px;background:#1e3a8a;color:white;
      border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;margin-bottom:20px;">
      Reset My Password
    </a>
    <p style="font-size:13px;color:#64748b;">This link expires in <strong>{RESET_TOKEN_VALID_HOURS} hours</strong>.
      If you did not request this, please ignore this email.</p>
    <p style="font-size:12px;color:#94a3b8;margin-top:12px;">Or copy this link: {reset_url}</p>
  </div>
</div>"""
    send_email_async(db_email, "Password Reset -- Employee Portal", html_body, cfg)
    return render_template("employee_forgot_password.html", sent=True, error=None)


@auth_bp.route("/employee_reset_password/<token>", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def employee_reset_password(token):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    row = _find_by_reset_token(cursor, "employees", "employee_id", token)
    if not row:
        cursor.close()
        db.close()
        return render_template("employee_reset_password.html", valid=False, done=False, token=token)
    if request.method == "GET":
        cursor.close()
        db.close()
        return render_template("employee_reset_password.html", valid=True, done=False, token=token, error=None)
    new_pw = request.form.get("new_password", "").strip()
    confirm_pw = request.form.get("confirm_password", "").strip()
    _pw_ok, _pw_err = validate_new_password(new_pw)
    if not _pw_ok:
        cursor.close()
        db.close()
        return render_template("employee_reset_password.html", valid=True, done=False,
                               token=token, error=_pw_err)
    if new_pw != confirm_pw:
        cursor.close()
        db.close()
        return render_template("employee_reset_password.html", valid=True, done=False,
                               token=token, error="Passwords do not match.")
    emp_id = row[0]
    cursor.execute("UPDATE employees SET password=%s, reset_token=NULL, reset_token_expiry=NULL, "
                   "must_change_password=0 WHERE employee_id=%s",
                   (generate_password_hash(new_pw), emp_id))
    record_password_change(cursor, "employees", emp_id)
    db.commit()
    cursor.close()
    db.close()
    _audit("employee_password_reset", "employees", emp_id, "Password reset via email link")
    return render_template("employee_reset_password.html", valid=True, done=True, token=token, error=None)


@auth_bp.route("/employee_logout", methods=["POST"])
def employee_logout():
    session.clear()
    return redirect(tpath("/login"))


@auth_bp.route("/change_password", methods=["POST"])
@employee_required
def change_password():
    emp_id = session["employee_id"]
    current = request.form.get("current_password", "").strip()
    new_pwd = request.form.get("new_password", "").strip()
    confirm = request.form.get("confirm_password", "").strip()
    _pw_ok, _pw_err = validate_new_password(new_pwd)
    if not _pw_ok:
        return redirect(tpath("/employee_portal?pwd_error=short#my-profile"))
    if new_pwd != confirm:
        return redirect(tpath("/employee_portal?pwd_error=mismatch#my-profile"))
    if not verify_and_update_password("employees", "employee_id", emp_id, current, new_pwd):
        return redirect(tpath("/employee_portal?pwd_error=wrong#my-profile"))
    return redirect(tpath("/employee_portal?pwd_ok=1#my-profile"))


@auth_bp.route("/force_change_pin", methods=["GET", "POST"])
@employee_required
def force_change_pin():
    emp_id = session["employee_id"]
    error = None
    if request.method == "POST":
        new_pwd = request.form.get("new_password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()
        _pw_ok, _pw_err = validate_new_password(new_pwd)
        if not _pw_ok:
            error = _pw_err
        elif new_pwd != confirm:
            error = "Passwords do not match."
        elif new_pwd in ("1234", "12345678", "password", "admin123"):
            error = "That password is too common. Please choose a stronger one."
        else:
            db = get_db_connection()
            cursor = db.cursor(buffered=True)
            cursor.execute(
                "UPDATE employees SET password=%s, force_pin_change=0, must_change_password=0 WHERE employee_id=%s",
                (generate_password_hash(new_pwd), emp_id)
            )
            record_password_change(cursor, "employees", emp_id)
            db.commit()
            cursor.execute("SELECT employee_id, name, role, email FROM employees WHERE employee_id=%s", (emp_id,))
            row = cursor.fetchone()
            cursor.close()
            db.close()
            # Re-run the same login-completion logic used right after
            # password verification (_finish_employee_login), not just an
            # unconditional redirect to /employee_portal -- a newly created
            # employee always starts with force_pin_change=1 (see
            # blueprints/employees.py's add_employee_page()), so an HR-role
            # employee's FIRST login always lands here first. Without this,
            # completing the forced PIN change would silently send them to
            # the employee portal instead of the HR admin panel every time,
            # since force_pin_change is now 0 but nothing re-checked role.
            if row:
                return _finish_employee_login(row[0], row[1], row[2], False, row[3])
            session.pop("_fpc", None)  # clear forced-change flag so portal is accessible
            return redirect(tpath("/employee_portal"))
    return render_template("force_change_pin.html", error=error,
                           emp_name=session.get("employee_name", ""))


@auth_bp.route("/webauthn/status", methods=["GET"])
def webauthn_status():
    """Diagnostic endpoint -- shows WebAuthn config without exposing sensitive data."""
    return jsonify({
        "webauthn_available": _webauthn_available,
        "rp_id": _wa_rp_id() if _webauthn_available else None,
        "expected_origins": _wa_origins() if _webauthn_available else [],
        "challenge_in_session": bool(session.get("wa_reg_challenge")),
        "request_host": request.host,
        "request_scheme": request.scheme,
    })


def _wa_authorize_enrollment(emp_id):
    """Return None if this session is allowed to enroll a passkey for emp_id,
    else an error string. Three legitimate paths, none of which trust a bare
    client-supplied emp_id on its own:
      - an authenticated admin, enrolling on an employee's behalf
      - an authenticated employee, enrolling their own credential
      - a kiosk visitor who just passed a real face match against emp_id's
        on-file photo (see api_kiosk_enroll_face_verify) within the last
        KIOSK_FACE_VERIFY_WINDOW_SEC seconds
    Without this, /webauthn/registration-options + webauthn-register(-kiosk)
    would let anyone enroll their own device's biometric against ANY
    employee_id and then check in as that employee indefinitely."""
    if session.get("admin_logged_in") and session.get("admin_role") == "admin":
        return None
    session_emp = session.get("employee_id")
    if session_emp:
        return None if session_emp.strip().upper() == emp_id else "Not authorized to enroll a passkey for this employee."
    face_emp = session.get("wa_face_verified_emp_id")
    face_at = session.get("wa_face_verified_at", 0)
    if face_emp and face_emp.upper() == emp_id and (time.time() - face_at) < KIOSK_FACE_VERIFY_WINDOW_SEC:
        return None
    return "Identity not verified. Please verify your face or log in first."


@auth_bp.route("/api/employee/kiosk-enroll-face-verify", methods=["POST"])
@limiter.limit("10 per minute")
def api_kiosk_enroll_face_verify():
    """Public kiosk endpoint: proves the person at the kiosk is the employee
    they claim before /webauthn/registration-options will hand out enrollment
    options for that employee_id. This is the real identity check that was
    previously missing entirely -- a QR scan or typed employee_id alone proves
    nothing about who is physically present."""
    employee_id = (request.form.get("employee_id") or "").strip().upper()
    face_photo = request.files.get("face_photo")
    if not employee_id:
        return jsonify({"ok": False, "msg": "employee_id required"}), 400
    if not face_photo:
        return jsonify({"ok": False, "msg": "Face photo required."}), 400

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT face_image FROM employees WHERE employee_id=%s", (employee_id,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return jsonify({"ok": False, "msg": "Employee not found"}), 404

    face_dir = os.path.join(UPLOAD_FOLDER, "face_logs")
    ok, msg = verify_uploaded_face(employee_id, row[0], face_photo, face_dir)
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 401

    session["wa_face_verified_emp_id"] = employee_id
    session["wa_face_verified_at"] = time.time()
    return jsonify({"ok": True})


@auth_bp.route("/webauthn/registration-options", methods=["GET"])
def webauthn_registration_options():
    """Server-generated WebAuthn registration options with challenge stored in session."""
    if not _webauthn_available:
        return jsonify({"ok": False, "msg": "Fingerprint enrollment is not available on this server."}), 503
    try:
        emp_id = (request.args.get("emp_id") or session.get("employee_id") or "employee").strip().upper()
        emp_name = (request.args.get("name") or emp_id).strip()

        auth_err = _wa_authorize_enrollment(emp_id)
        if auth_err:
            return jsonify({"ok": False, "msg": auth_err}), 403
        # Single-use: a face match only ever authorizes the enrollment it was
        # taken for, never a second later request in the same session.
        session.pop("wa_face_verified_emp_id", None)
        session.pop("wa_face_verified_at", None)

        rp_id = _wa_rp_id()
        rp_err = _wa_check_rp_id(rp_id)
        if rp_err:
            return jsonify({"ok": False, "error": rp_err}), 422
        _reg_algs = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256]
        options = webauthn.generate_registration_options(
            rp_id=rp_id,
            rp_name="Employee Attendance",
            user_id=emp_id.encode(),
            user_name=emp_id,
            user_display_name=emp_name,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                user_verification=UserVerificationRequirement.REQUIRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
            supported_pub_key_algs=_reg_algs,
            attestation=AttestationConveyancePreference.NONE,
        )
        session["wa_reg_challenge"] = _wa_b64url_encode(options.challenge)
        session["wa_reg_emp_id"] = emp_id
        session["wa_reg_alg_ids"] = [a.value for a in _reg_algs]
        return webauthn.options_to_json(options), 200, {"Content-Type": "application/json"}
    except Exception as exc:
        # Generic message to the client, full traceback server-side only --
        # matches every other route's error handling in this codebase;
        # this pair used to be the one place still echoing the raw
        # exception string (library-internal, but a verbose-error
        # inconsistency worth closing regardless).
        app_log.error("WebAuthn registration-options failed: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": "Could not start WebAuthn registration. Please try again."}), 500


@auth_bp.route("/webauthn/authentication-options", methods=["GET"])
def webauthn_authentication_options():
    """Server-generated WebAuthn authentication options with challenge stored in session."""
    if not _webauthn_available:
        return jsonify({"ok": False, "msg": "Fingerprint verification is not available on this server."}), 503
    try:
        emp_id = (request.args.get("emp_id") or "").strip().upper()
        rp_id = _wa_rp_id()
        rp_err = _wa_check_rp_id(rp_id)
        if rp_err:
            return jsonify({"ok": False, "error": rp_err}), 422

        allow_creds = []
        if emp_id:
            try:
                db = get_db_connection()
                cur = db.cursor(buffered=True)
                cur.execute("SELECT fingerprint_credential_id FROM employees WHERE employee_id=%s", (emp_id,))
                row = cur.fetchone()
                cur.close()
                db.close()
                if row and row[0]:
                    allow_creds = [PublicKeyCredentialDescriptor(
                        id=_wa_b64url_decode(row[0]), transports=[AuthenticatorTransport.INTERNAL]
                    )]
            except Exception as db_exc:
                app_log.warning("WebAuthn auth-options: DB lookup failed for emp=%s: %s", emp_id, db_exc)

        options = webauthn.generate_authentication_options(
            rp_id=rp_id, allow_credentials=allow_creds, user_verification=UserVerificationRequirement.REQUIRED,
        )
        session["wa_auth_challenge"] = _wa_b64url_encode(options.challenge)
        session["wa_auth_emp_id"] = emp_id
        return webauthn.options_to_json(options), 200, {"Content-Type": "application/json"}
    except Exception as exc:
        app_log.error("WebAuthn authentication-options failed: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": "Could not start WebAuthn authentication. Please try again."}), 500


@auth_bp.route("/api/employee/webauthn-verify-challenge", methods=["POST"])
def webauthn_verify_challenge():
    """
    Real server-side WebAuthn assertion verification: checks the ECDSA/RSA
    signature in the assertion against the employee's stored public key (not
    just the clientDataJSON fields, which are trivially forgeable on their
    own). On success, sets a short-lived, one-time, employee-bound session
    flag that /attendance and /api/employee/qr-face-checkin consume to allow
    the actual check-in -- this stops a verified fingerprint for employee A
    from being reused to check in as employee B.
    """
    if not _webauthn_available:
        return jsonify({"ok": False, "msg": "Fingerprint verification is not available on this server."}), 503
    data = request.get_json(force=True, silent=True) or {}
    emp_id = (data.get("emp_id") or session.get("wa_auth_emp_id") or "").strip().upper()
    credential = data.get("credential")
    challenge_b64 = session.get("wa_auth_challenge")

    if not credential or not challenge_b64:
        return jsonify({"ok": False, "msg": "Missing credential or challenge"}), 400

    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)

        if emp_id:
            # QR + Fingerprint mode: employee already identified by QR scan
            cur.execute(
                "SELECT employee_id, name, fingerprint_public_key, fingerprint_sign_count FROM employees WHERE employee_id=%s",
                (emp_id,)
            )
        else:
            # Passkey mode: employee is identified by the credential ID inside the assertion.
            # credential["id"] is the base64url credential ID the browser signed with.
            cred_id = (credential.get("id") or "") if isinstance(credential, dict) else ""
            if not cred_id:
                cur.close()
                db.close()
                return jsonify({"ok": False, "msg": "Missing credential ID"}), 400
            cur.execute(
                "SELECT employee_id, name, fingerprint_public_key, fingerprint_sign_count FROM employees WHERE fingerprint_credential_id=%s",
                (cred_id,)
            )

        row = cur.fetchone()
        if not row or not row[2]:
            cur.close()
            db.close()
            return jsonify({"ok": False, "msg": "No passkey enrolled. Please enrol from the employee portal."}), 401

        emp_id = row[0]
        emp_name = row[1] or emp_id
        stored_pubkey = base64.b64decode(row[2])
        stored_sign_count = int(row[3] or 0)

        verified = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=_wa_b64url_decode(challenge_b64),
            expected_rp_id=_wa_rp_id(),
            expected_origin=_wa_origins(),
            credential_public_key=stored_pubkey,
            # Per W3C WebAuthn §7.2 step 21: pass the stored count so py_webauthn
            # can reject a cloned credential (new ≤ stored when stored > 0).
            # Authenticators that never increment (Windows Hello, Touch ID) always
            # return 0, so stored stays 0 and the check is skipped automatically.
            credential_current_sign_count=stored_sign_count,
        )
    except Exception as e:
        try:
            cur.close()
            db.close()
        except Exception as _close_exc:
            app_log.debug("WebAuthn auth failure cleanup (cursor/db close) also failed: %s", _close_exc)
        app_log.warning("WebAuthn authentication verification failed for emp_id=%s: %s",
                        emp_id or "(passkey mode)", e, exc_info=True)
        return jsonify({"ok": False, "msg": "Verification failed. Please try again."}), 401

    session.pop("wa_auth_challenge", None)
    session.pop("wa_auth_emp_id", None)

    # Persisting the new sign count is best-effort bookkeeping (anti-clone
    # detection) -- a failure here doesn't invalidate a verification that
    # already succeeded above, so it's swallowed rather than failing the
    # whole request. Reuses the connection from the SELECT above instead of
    # opening a second one.
    try:
        cur.execute(
            "UPDATE employees SET fingerprint_sign_count=%s WHERE employee_id=%s",
            (verified.new_sign_count, emp_id)
        )
        db.commit()
    except Exception as exc:
        app_log.warning("Persisting WebAuthn sign_count failed for %s (anti-clone bookkeeping degraded): %s",
                        emp_id, exc, exc_info=True)
    finally:
        cur.close()
        db.close()

    session["wa_fp_verified_emp_id"] = emp_id
    session["wa_fp_verified_at"] = time.time()
    return jsonify({"ok": True, "emp_id": emp_id, "name": emp_name})


@auth_bp.route("/api/employee/webauthn-register", methods=["POST"])
@limiter.limit("10 per minute")
def webauthn_register():
    """Save a WebAuthn credential after successful enrollment. Requires active employee session."""
    # Use session employee_id; fall back to wa_reg_emp_id stored when options were generated.
    # Both values live in the same signed session cookie, so this is equivalent security.
    emp_id = session.get("employee_id") or session.get("wa_reg_emp_id")
    if not emp_id:
        return jsonify({"ok": False, "msg": "Session expired -- please log in again"}), 401
    data = request.get_json(force=True, silent=True) or {}
    credential = data.get("credential")
    challenge_b64 = session.get("wa_reg_challenge")
    if not challenge_b64:
        return jsonify({"ok": False, "msg": "Enrollment session expired -- please start again"}), 401
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        ok, err = _wa_verify_and_store_registration(emp_id, credential, challenge_b64, cursor, db)
        cursor.close()
        db.close()
    except Exception:
        app_log.error("WebAuthn registration endpoint failed", exc_info=True)
        return jsonify({"ok": False, "msg": "WebAuthn registration failed. Please try again."}), 500
    session.pop("wa_reg_challenge", None)
    session.pop("wa_reg_emp_id", None)
    session.pop("wa_reg_alg_ids", None)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 401
    return jsonify({"ok": True})


@auth_bp.route("/api/employee/webauthn-unenroll", methods=["POST"])
@limiter.limit("10 per minute")
def webauthn_unenroll():
    """Remove the stored WebAuthn credential for the logged-in employee."""
    emp_id = session.get("employee_id")
    if not emp_id:
        return jsonify({"ok": False, "msg": "Not logged in"}), 401
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "UPDATE employees SET fingerprint_credential_id=NULL, fingerprint_public_key=NULL, "
            "fingerprint_sign_count=0 WHERE employee_id=%s",
            (emp_id,)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True})
    except Exception:
        app_log.error("WebAuthn unenroll failed", exc_info=True)
        return jsonify({"ok": False, "msg": "Failed to remove credential. Please try again."}), 500


@auth_bp.route("/api/employee/<emp_id>/webauthn-credential", methods=["GET"])
@limiter.limit("30 per minute")
def get_employee_webauthn_credential(emp_id):
    """Return the stored WebAuthn credential_id -- requires an active admin or employee session."""
    is_admin = session.get("admin_logged_in")
    session_emp = session.get("employee_id")
    if (is_admin or session_emp) and not session_account_ok():
        is_admin = session_emp = None  # stale/flagged session -- fall through to Bearer check
    token_emp = None  # set below only for an employee-type Bearer token
    if not (is_admin or session_emp):
        # Also accept a valid Bearer token (kiosk uses admin token)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token_hash = _hash_token(auth[7:])
            with _db() as (cursor, _):
                cursor.execute(
                    "SELECT t.identity, t.token_type FROM api_tokens t WHERE t.token=%s AND t.expires_at > NOW() "
                    f"AND {_TOKEN_NOT_STALE_SQL}",  # nosec B608 -- fixed module constant
                    (token_hash,)
                )
                token_row = cursor.fetchone()
                if not token_row:
                    return jsonify({"ok": False, "msg": "Unauthorized"}), 401
                # Same ownership rule as a real employee session below --
                # this path previously only checked the token was valid,
                # never binding its identity to `emp_id` at all, so any
                # employee's own token could fetch any OTHER employee's
                # credential_id. An admin-type token (kiosk) is still
                # unrestricted, matching the comment above.
                if token_row[1] == "employee":
                    token_emp = token_row[0]
        else:
            return jsonify({"ok": False, "msg": "Unauthorized"}), 401
    emp_id = emp_id.strip().upper()
    # Employees (session- or token-based) may only retrieve their own
    # credential; admins and admin-type Bearer tokens can retrieve any.
    if session_emp and not is_admin and session_emp.upper() != emp_id:
        return jsonify({"ok": False, "msg": "Unauthorized"}), 403
    if token_emp and token_emp.upper() != emp_id:
        return jsonify({"ok": False, "msg": "Unauthorized"}), 403
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "SELECT fingerprint_credential_id FROM employees WHERE employee_id=%s LIMIT 1",
            (emp_id,)
        )
        row = cursor.fetchone()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "credential_id": row[0] if row else None})
    except Exception:
        return jsonify({"ok": True, "credential_id": None})


@auth_bp.route("/api/employee/webauthn-register-kiosk", methods=["POST"])
@limiter.limit("5 per minute")
def webauthn_register_kiosk():
    """Enrol a passkey from the attendance kiosk. No employee session required."""
    if not _webauthn_available:
        return jsonify({"ok": False, "msg": "Fingerprint enrollment is not available on this server."}), 503
    data = request.get_json(force=True, silent=True) or {}
    emp_id = (data.get("emp_id") or "").strip().upper()
    credential = data.get("credential")
    challenge_b64 = session.get("wa_reg_challenge")
    if not emp_id:
        return jsonify({"ok": False, "msg": "Employee ID required"}), 400
    if not challenge_b64:
        app_log.warning("WebAuthn kiosk enrolment: no challenge in session for emp=%s", emp_id)
        return jsonify({"ok": False, "msg": "Session expired -- please refresh the page and try again"}), 400
    if session.get("wa_reg_emp_id", "").upper() != emp_id:
        app_log.warning("WebAuthn kiosk: emp_id mismatch -- session=%s post=%s",
                        session.get("wa_reg_emp_id"), emp_id)
        return jsonify({"ok": False, "msg": "Employee ID mismatch. Please restart enrollment."}), 403
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (emp_id,))
        if not cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Employee ID not found"}), 404
        ok, err = _wa_verify_and_store_registration(emp_id, credential, challenge_b64, cursor, db)
        cursor.close()
        db.close()
    except Exception as exc:
        app_log.error("WebAuthn kiosk registration unexpected error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "msg": "Registration failed. Please try again."}), 500
    session.pop("wa_reg_challenge", None)
    session.pop("wa_reg_alg_ids", None)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 400
    app_log.info("WebAuthn kiosk enrolment: emp_id=%s", emp_id)
    return jsonify({"ok": True})


@auth_bp.route("/api/admin/employee/<emp_id>/reset-fingerprint", methods=["POST"])
@role_required("admin")
def admin_reset_employee_fingerprint(emp_id):
    """Admin: clear a specific employee's WebAuthn credential so they can re-enroll on a new device."""
    emp_id = emp_id.strip().upper()
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "UPDATE employees SET fingerprint_credential_id=NULL, fingerprint_public_key=NULL, "
            "fingerprint_sign_count=0 WHERE employee_id=%s",
            (emp_id,)
        )
        db.commit()
        affected = cursor.rowcount
        cursor.close()
        db.close()
        if affected == 0:
            return jsonify({"ok": False, "msg": "Employee not found"}), 404
        _audit("admin_reset_fingerprint", "employees", emp_id)
        return jsonify({"ok": True})
    except Exception:
        app_log.error("Failed to reset employee fingerprint", exc_info=True)
        return jsonify({"ok": False, "msg": "Failed to reset fingerprint. Please try again."}), 500


@auth_bp.route("/api/employee/mobile-biometric-nonce", methods=["POST"])
@limiter.limit("10 per minute")
@employee_api_required
def api_mobile_biometric_nonce():
    """Mobile app calls this (with its employee Bearer token) right before
    prompting the device's local biometric/PIN check. The returned nonce
    must be echoed back to /mobile-biometric-attest within 60s."""
    nonce = _mobile_biometric_issue_nonce(g.api_emp_id)
    return jsonify({"ok": True, "nonce": nonce})


@auth_bp.route("/api/employee/mobile-biometric-attest", methods=["POST"])
@limiter.limit("10 per minute")
@employee_api_required
def api_mobile_biometric_attest():
    """Mobile app calls this immediately after a successful local
    LocalAuthentication.authenticateAsync(), turning that local-only signal
    into a server-side, employee-bound, single-use, time-boxed proof that
    /api/employee/qr-face-checkin will accept for fingerprint combos."""
    data = request.get_json(force=True, silent=True) or {}
    nonce = (data.get("nonce") or "").strip()
    ok, err = _mobile_biometric_attest(g.api_emp_id, nonce)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 401
    return jsonify({"ok": True})


@auth_bp.route("/admin/step-up-mfa", methods=["GET", "POST"])
def step_up_mfa():
    """Step-Up MFA challenge: a fresh TOTP re-check before a high-privilege
    action, on top of (not instead of) the admin session already
    established at login."""
    username = session.get("admin_username")
    if not username:
        return redirect(tpath("/login"))

    if request.method == "POST":
        totp_code = request.form.get("totp_code", "").strip()
        valid = verify_totp_code(username, totp_code, require_enabled=False)

        if valid:
            session["_step_up_mfa_verified_at"] = time.time()
            target_url = session.pop("_step_up_target_url", "/admin")
            log_security_event(
                "mfa.step_up_success",
                "Step-Up MFA verified successfully",
                level="INFO",
                identifier=username
            )
            return redirect(target_url)

        return render_template("step_up_mfa.html", error="Invalid MFA verification code.")

    return render_template("step_up_mfa.html")
