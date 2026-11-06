# -*- coding: utf-8 -*-
"""Request guards: IP bans, WAF, session lifetime/idle timeout,
mandatory MFA, org-profile gate, private-upload block.

Split out of app.py; registered on the shared Flask app at import time.
app.py imports the app_setup modules in a fixed order because Flask runs
before_request hooks in registration order (after_request in reverse).
"""
import os
import time
import utils.waf as waf
from database import get_db_connection
from extensions import app, app_log, log_security_event
from flask import abort, current_app, flash, jsonify, redirect, request, session, url_for
from utils.client_ip import is_protected_ip, warn_if_client_ip_collapsed
from utils.helpers import get_company_settings, tpath as _tpath
from app_setup.config import _SESSION_MAX_AGE


@app.before_request
def _enforce_ip_ban():
    """Application-layer IP ban, enforced before every other before_request
    hook (registered second, right after the perf timer) so a banned source
    never reaches session/auth logic, let alone a route handler. Backs the
    SOC dashboard's one-click ban action (blueprints/admin_views.py). Static
    assets stay reachable -- banning is about stopping active app usage
    (login attempts, API calls), not making the banned party's browser look
    broken in a way that itself signals "you got blocked, try harder.\""""
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    ip = request.remote_addr
    if not ip:
        return
    if is_protected_ip(ip):
        # A non-routable/shared source address (container gateway, LAN,
        # allow-listed probe) can't identify one client, so a ban row for
        # it -- including one written before this guard existed -- would
        # lock out every visitor behind it. See utils/client_ip.py.
        warn_if_client_ip_collapsed(ip)
        return
    db = get_db_connection()
    cursor = db.cursor()
    try:
        # banned_ips exists once per tenant schema AND in public, and the
        # search_path puts the tenant's copy first -- so a plain
        # `FROM banned_ips` only ever sees that tenant's own bans. Automated
        # bans (utils/waf.ban_ip) are written to public.banned_ips so an
        # attacker can't hop to another tenant's URL to shake them off;
        # check both. With no tenant active the two names are one table.
        try:
            cursor.execute(
                "SELECT reason FROM banned_ips WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW()) "
                "UNION ALL "
                "SELECT reason FROM public.banned_ips WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW()) "
                "LIMIT 1",
                (ip, ip),
            )
        except Exception:
            # Schema-qualified name unsupported (SQLite dev fallback).
            cursor.execute(
                "SELECT reason FROM banned_ips WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW())",
                (ip,),
            )
        row = cursor.fetchone()
    finally:
        cursor.close()
        db.close()
    if row:
        return jsonify({"ok": False, "msg": "Access denied."}), 403


# Credential-check endpoints are deliberately exempt from the blanket WAF
# block below. blueprints/auth.py's admin_login already detects
# injection-shaped identifiers itself (_INJECTION_PATTERN_RE) and responds
# with the same generic "Invalid credentials" any wrong password gets -- a
# real, tested design choice (tests/test_auth_routes.py,
# tests/test_comprehensive.py's TestInputValidation) so a probing attacker
# can't use a distinguishing WAF-block response to tell "malicious-shaped
# input" apart from "wrong password" on the one surface where that
# distinction would be most valuable to them. A hard 403 here would both
# leak that signal and short-circuit the existing detection before it runs.
# Not a real exposure either way -- every one of these routes only ever
# uses the identifier in a parameterized query, never executes or reflects
# it -- so exempting them costs no actual protection.
_WAF_EXEMPT_PATHS = {"/login", "/admin_login", "/employee_login", "/api/login", "/api/employee/login", "/api/signin"}


@app.before_request
def _waf_inspect_request():
    """Native signature-based WAF: rejects requests whose query string,
    form fields, JSON body, uploaded filenames, or path segments match a
    known SQLi/XSS/path-traversal shape. Runs immediately after the IP ban
    check (before session/CSRF/route logic) so a malicious payload is
    rejected before it can reach anything else. See utils/waf.py."""
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    if request.path in _WAF_EXEMPT_PATHS:
        return
    # SOC analysts legitimately search for and paste attack strings (an SQLi payload
    # as an event filter, a hostile log excerpt). Exempt only authenticated
    # /soc/api/ calls: the login page and everything else stay inspected, and
    # that API uses bound parameters only (utils/soc_queries.py).
    if request.path.startswith("/soc/api/") and session.get("soc_uid"):
        return
    hit = waf.inspect_request(request)
    if hit:
        event_type, field, matched = hit
        ip = request.remote_addr
        log_security_event(
            event_type, f"WAF blocked request: signature matched in {field}",
            level="ERROR", field=field, matched=matched[:100],
        )
        waf.record_breach_and_maybe_ban(ip, f"Repeated WAF blocks ({event_type})")
        return jsonify({"ok": False, "msg": "Request blocked by security policy."}), 403


@app.before_request
def _enforce_session_lifetime():
    """Expire sessions that are older than the absolute max age, regardless of activity."""
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    created = session.get("_session_created")
    if created and (time.time() - created) > _SESSION_MAX_AGE:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "msg": "Session expired. Please log in again."}), 401
        flash("Your session expired. Please log in again.", "warning")
        return redirect(url_for("auth.admin_login"))


@app.before_request
def _enforce_idle_timeout():
    """Expire sessions after N minutes of *inactivity* -- distinct from the
    absolute max-age check above, which only catches a session once it's
    lived 8 hours regardless of how recently it was used. The threshold
    itself (company_settings.session_timeout, admin-configurable 5-1440 min
    via Settings)
    used to be stored and displayed in the UI but was never actually
    enforced anywhere -- this closes that gap. Reads through
    get_company_settings()'s existing 60s cache rather than querying the DB
    on every request.
    """
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    if not (session.get("admin_logged_in") or session.get("employee_id")):
        return
    now = time.time()
    last_activity = session.get("_last_activity")
    if last_activity:
        timeout_minutes = get_company_settings().get("session_timeout", 30)
        if (now - last_activity) > timeout_minutes * 60:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "msg": "Session expired due to inactivity. Please log in again."}), 401
            flash("Your session expired due to inactivity. Please log in again.", "warning")
            return redirect(url_for("auth.admin_login"))
    session["_last_activity"] = now


# Roles that count as "administrative/HR" for the mandatory-MFA requirement
# below -- every role that can reach admin-side data or actions.
_MANDATORY_MFA_ROLES = {"admin", "manager", "hr"}

# Routes reachable by an admin/manager/soc_analyst/hr session that has NOT
# yet enrolled TOTP -- must stay small and deliberate. Anything not on this
# list is unreachable until enrollment is complete, which is the point (a
# genuine "mandatory," not a step-up an admin can defer indefinitely).
_MANDATORY_MFA_EXEMPT_PATHS = {
    "/admin/mfa-required", "/api/settings/2fa/setup", "/api/settings/2fa/enable",
    "/logout", "/admin_login", "/hr_login"
}

# All four MFA/2FA gates below default ON (secure by default) -- set any of
# them to "false" in .env only for a deliberate, documented reason (e.g. a
# local dev box with no authenticator app handy). This used to default OFF
# despite every docstring/comment near these gates claiming otherwise (see
# _enforce_admin_mfa_enrollment below, and the .get(key, True) fallbacks
# throughout blueprints/auth.py and blueprints/platform_admin.py that could
# never actually apply -- app.config[...] is unconditionally set here on
# every boot, so those fallback defaults were dead code, not a real
# secure-by-default posture).
app.config["MANDATORY_ADMIN_MFA"] = os.environ.get("MANDATORY_ADMIN_MFA", "True").lower() in ("true", "1", "yes")
app.config["MANDATORY_ORG_PROFILE"] = os.environ.get("MANDATORY_ORG_PROFILE", "True").lower() in ("true", "1", "yes")
app.config["MANDATORY_LOGIN_MFA"] = os.environ.get("MANDATORY_LOGIN_MFA", "True").lower() in ("true", "1", "yes")
app.config["MANDATORY_PLATFORM_ADMIN_MFA"] = os.environ.get("MANDATORY_PLATFORM_ADMIN_MFA", "True").lower() in ("true", "1", "yes")
# Email Settings step-up gate (utils/auth.py's require_email_2fa) -- same
# secure-by-default posture as the three flags above.
app.config["REQUIRE_EMAIL_2FA"] = os.environ.get("REQUIRE_EMAIL_2FA", "True").lower() in ("true", "1", "yes")


@app.before_request
def _enforce_admin_mfa_enrollment():
    """Hard requirement: an admin/manager/soc_analyst session with TOTP not
    yet enrolled can reach nothing except the enrollment flow itself (and
    login/logout) -- no grace period, no dismissible nag. This is distinct
    from the existing TOTP *step-up* gates (Email Settings, Security hub,
    SOC), which only apply once already enrolled; this is what forces
    enrollment to happen in the first place.

    MANDATORY_ADMIN_MFA defaults on (app.config[...] is set unconditionally
    at boot, above -- direct dict access here, not a .get(..., True)
    fallback that could never actually apply). Tests disable it globally
    (matching the existing pattern of disabling flask-limiter under pytest)
    since most of the suite logs in admin sessions directly via
    session_transaction without walking through enrollment, and re-enable
    it only in the tests that specifically exercise this gate.
    """
    if not current_app.config["MANDATORY_ADMIN_MFA"]:
        return
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    if request.path in _MANDATORY_MFA_EXEMPT_PATHS:
        return
    username = session.get("admin_username")
    if not (session.get("admin_logged_in") and username):
        return
    if session.get("admin_role", "admin") not in _MANDATORY_MFA_ROLES:
        return
    from utils.totp import is_totp_enabled_cached
    if is_totp_enabled_cached(username):
        return
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "msg": "MFA enrollment required before continuing.",
                        "redirect": "/admin/mfa-required"}), 403
    return redirect("/admin/mfa-required")


# What a new company's admin can still reach while its Organization profile
# is pending. /api/ and /web/ are left to their own auth: the mobile app signs
# up through Bearer-token routes, and a locked page never calls the others.
_ORG_PROFILE_EXEMPT_PREFIXES = ("/static/", "/profile", "/api/", "/web/", "/admin/mfa-required")
_ORG_PROFILE_EXEMPT_PATHS = {"/healthz", "/welcome", "/logout", "/pay_overdue_bill", "/csp-report"}


@app.before_request
def _enforce_org_profile():
    """A newly registered company's admin can reach nothing but the
    Organization page until its mandatory form is saved (company_profile.py's
    profile_details_save clears the flag). Read through the cached settings,
    so this adds no query per request, and fail-soft for the same reason the
    banner lookups are: it must never be what takes a dashboard down. Only
    the company admin is held here: HR and managers can't fill this form."""
    if not current_app.config["MANDATORY_ORG_PROFILE"]:
        return
    if not session.get("admin_logged_in") or session.get("admin_role", "admin") != "admin":
        return
    if request.path in _ORG_PROFILE_EXEMPT_PATHS or request.path.startswith(_ORG_PROFILE_EXEMPT_PREFIXES):
        return
    try:
        pending = get_company_settings().get("org_profile_pending")
    except Exception:
        app_log.warning("_enforce_org_profile: settings lookup failed", exc_info=True)
        return
    if pending:
        return redirect(_tpath("/profile"))


@app.before_request
def _block_private_uploads():
    """Uploaded HR documents used to live under static/ (and the `documents`
    volume is still mounted at static/employee_docs), so a legacy file left in
    either folder would be handed to anyone who can guess its URL by Flask's
    static handler. They are only ever served through authenticated routes."""
    if request.path.startswith(("/static/employee_docs/", "/static/onboarding_docs/")):
        abort(404)
