# -*- coding: utf-8 -*-
"""Core blueprint -- home page, CSP reporting + browser error reporting, session-risk stream, security
lockout, and the simpler token-based REST API layer (/api/login,
/api/dashboard, /api/holidays and their /api/employee/* equivalents) -- the
last routes drained out of app.py, which now holds only shared setup
(init_db, error handlers, before/after_request hooks, template filters)."""
import time
import secrets
from datetime import date
from flask import Blueprint, request, session, jsonify, render_template, redirect, Response, g, current_app
from extensions import limiter, app_log, log_security_event
from database import get_db_connection, transaction
from utils.auth import (
    api_required, check_password_hash, generate_password_hash, _hash_token,
    _check_login_lockout, _record_login_failure, _clear_login_failures,
    validate_new_password, dummy_password_check, _mask_identifier, stamp_password_version,
    turnstile_enabled, _TURNSTILE_SITE_KEY, employment_ended_on, access_ended_message,
)
from utils.login_otp import issue_challenge, verify_challenge
from utils.totp import send_mfa_login_email
from utils.helpers import (
    _db, get_auth_config, validate_employee_email_domain,
    employee_login_url, get_pending_counts, tpath, add_employee_seat_cap_check,
    get_company_settings, _safe_app_url, company_today,
)
from utils.plan_limits import get_tenant_employee_count, calculate_price, format_price_inr, get_billing_snapshot
from utils.email_utils import get_email_config, send_email_smtp, support_inbox
from utils.session_risk import is_session_compromised, ensure_session_id

core_bp = Blueprint("core", __name__)

# Web-only pages the mobile app is allowed to bridge a WebView into (see
# api_mobile_web_session_link()/mobile_bridge_login() below) -- deliberately
# a closed allowlist rather than an arbitrary caller-supplied path, since
# redeeming a bridge token creates a real admin session with no further
# authorization check of its own. Add a path here (and to the mobile app's
# own allowed targets) the next time a web-only feature needs this same
# WebView-bridge treatment; nothing else needs to change in this pair of
# routes.
_BRIDGE_DEFAULT_TARGET = "/settings/seats"
_BRIDGE_TARGET_ALLOWLIST = {"/settings/seats"}


# ── Frontend (browser-side) telemetry ────────────────────────────────────────
# Everything a browser tells us is attacker-controlled: any visitor can POST
# here. So it is size-capped, rate-limited, stripped of control characters,
# stored ONLY as structured fields (never in the event message), capped at
# WARNING (an ERROR would page the alert webhook, letting anyone spam it), and
# must be escaped by whatever displays it.
_FRONTEND_MAX_BODY = 8 * 1024
_CLIENT_LOG_TYPES = {
    "js_error": "WARNING",
    "unhandled_rejection": "WARNING",
    "resource_error": "INFO",
}


def _clip(value, limit):
    """str(value) with control characters removed and length capped."""
    text = "" if value is None else str(value)
    return "".join(ch for ch in text if ch >= " " and ch != chr(127))[:limit]


def _no_query(value, limit):
    """A URL/path with any query string or fragment removed (they can carry secrets)."""
    return _clip(value, limit).split("?", 1)[0].split("#", 1)[0]


def _page_path(value):
    """A same-origin page path only -- never a query string or fragment, which
    can carry reset tokens or other secrets."""
    path = _no_query(value, 300)
    if "://" in path:
        path = "/" + path.split("://", 1)[1].split("/", 1)[-1]
    return path if path.startswith("/") else "/" + path


def _record_frontend_event(subtype, level, **fields):
    who = session.get("platform_admin_username") or session.get("admin_username") or session.get("employee_id")
    log_security_event(f"frontend.{subtype}", f"Browser reported: {subtype.replace('_', ' ')}",
                       level=level, identifier=who, **fields)


def _csp_violations(payload):
    """Normalise both report formats browsers send: the legacy
    {"csp-report": {...}} object and the Reporting API's [{"type":
    "csp-violation", "body": {...}}] list."""
    if isinstance(payload, list):
        for item in payload[:5]:
            if isinstance(item, dict) and isinstance(item.get("body"), dict):
                yield item["body"], {"blocked-uri": "blockedURL", "violated-directive": "effectiveDirective",
                                     "document-uri": "documentURL", "source-file": "sourceFile",
                                     "line-number": "lineNumber"}
    elif isinstance(payload, dict):
        body = payload.get("csp-report", payload)
        if isinstance(body, dict):
            yield body, {}


@core_bp.route("/csp-report", methods=["POST"])
@limiter.limit("60 per minute")
def csp_report():
    """Receives Content-Security-Policy violation reports from browsers and
    records them as frontend.csp_violation security events."""
    if (request.content_length or 0) > _FRONTEND_MAX_BODY:
        return "", 204
    try:
        payload = request.get_json(force=True, silent=True)
        for body, alias in _csp_violations(payload):
            def get(key):
                return body.get(key) if key in body else body.get(alias.get(key, key))
            _record_frontend_event(
                "csp_violation", "WARNING",
                directive=_clip(get("violated-directive"), 80),
                blocked_uri=_no_query(get("blocked-uri"), 200),
                page=_page_path(get("document-uri")),
                source_file=_no_query(get("source-file"), 200),
                line=_clip(get("line-number"), 10),
            )
    except Exception as exc:
        app_log.debug("Malformed CSP violation report: %s", exc)
    return "", 204


@core_bp.route("/api/client-log", methods=["POST"])
@limiter.limit("30 per minute")
def client_log():
    """Sink for the small reporter injected into every page (app.py's
    _CLIENT_LOG_SCRIPT): uncaught JS errors, unhandled promise rejections
    and failed resource loads."""
    if (request.content_length or 0) > _FRONTEND_MAX_BODY:
        return "", 204
    data = request.get_json(force=True, silent=True)
    kind = data.get("type") if isinstance(data, dict) else None
    if isinstance(kind, str) and kind in _CLIENT_LOG_TYPES:
        _record_frontend_event(
            kind, _CLIENT_LOG_TYPES[kind],
            detail=_clip(data.get("message"), 300),
            page=_page_path(data.get("page")),
            source_file=_no_query(data.get("source"), 200),
            line=_clip(data.get("line"), 10),
            user_agent=_clip(request.headers.get("User-Agent"), 120),
        )
    return "", 204


@core_bp.route("/")
def home():
    # Always show the marketing landing page at "/", regardless of tenant
    # or session state -- logged-in admins/employees and tenant-slug
    # visits used to be auto-redirected straight to their portal/login;
    # now everyone lands here first and clicks through themselves.
    from utils.analytics import track_page_view
    from utils.plan_limits import get_per_employee_paise
    track_page_view("/")
    # The page's company-registration form (POST /create_org) comes back
    # here with ?registration=failed when its submission is rejected. Only
    # then does this page show flashes, and only "error" ones -- same rule
    # as org.py's create_org_page(): every other flash queued in this
    # cookie belongs to some other page and is left for it.
    registration_failed = request.args.get("registration") == "failed"
    if registration_failed and session.get("_flashes"):
        session["_flashes"] = [f for f in session["_flashes"] if f[0] == "error"]
    return render_template(
        "landing.html",
        per_employee_paise=get_per_employee_paise(),
        registration_failed=registration_failed,
        show_captcha=turnstile_enabled(),
        turnstile_site_key=_TURNSTILE_SITE_KEY,
        support_email=support_inbox(),
    )


@core_bp.route("/checkin")
def checkin_page():
    """The QR/PIN/face/fingerprint check-in kiosk -- previously served at
    site root ("/") before that was handed over to the platform admin
    login. No auth required: this is the public kiosk screen a company's
    own employees check in from."""
    return render_template("index.html", auth_cfg=get_auth_config())


# How long an open tab waits before asking /api/session/risk-stream again.
_RISK_STREAM_RETRY_MS = 15000


@core_bp.route("/api/session/risk-stream")
def session_risk_stream():
    """Server-Sent Events endpoint that tells an already-open browser tab its
    session was marked compromised, instead of it waiting for its next click.

    This is enforcement's UX layer, not enforcement itself -- the actual
    kill switch is _reject_if_compromised() in utils/auth.py, checked on
    every authenticated request regardless of whether this stream is even
    connected. A client that never opens this connection, or ignores every
    message it sends, still gets rejected on its very next request.

    Answers immediately with the current state and closes; the `retry:`
    field makes the browser's EventSource reconnect after
    _RISK_STREAM_RETRY_MS. It used to hold the request for ~20 s of 2 s
    checks, and since every authenticated page opens this stream, each
    open tab permanently occupied one of the server's few gthread worker
    threads (QA SCALE-08).
    """
    if not session.get("admin_logged_in") and not session.get("employee_id"):
        return jsonify({"ok": False, "msg": "Not authenticated"}), 401
    sid = session.get("_sid")
    if not sid:
        return jsonify({"ok": False, "msg": "No active session to monitor"}), 400
    event = "compromised" if is_session_compromised(sid) else "ping"
    return Response(
        f"retry: {_RISK_STREAM_RETRY_MS}\nevent: {event}\ndata: {{}}\n\n",
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@core_bp.route("/security_lockout")
def security_lockout():
    """Hard-locked landing page for a force-terminated session. Not behind
    any auth decorator on purpose -- the session that lands here has
    already been session.clear()'d by _reject_if_compromised()."""
    return render_template("security_lockout.html"), 403


@core_bp.route("/api/login", methods=["POST"])
@limiter.limit("5 per minute")
@limiter.limit("20 per hour")
def api_login():
    """Step 1 of the mobile admin login. Same rules as the web /login:
    per-account lockout, generic errors, and -- when MANDATORY_LOGIN_MFA is
    on -- no token at all until the emailed code is verified at
    /api/login/verify. The response then carries only an opaque
    challenge_id; the code itself goes to the account's email."""
    data = request.get_json(silent=True) or {}
    return _api_admin_login(str(data.get("username") or ""), str(data.get("password") or ""))


def _api_admin_login(username, password):
    """The body of /api/login, shared with /api/signin."""
    if "\x00" in username or "\x00" in password or not username or not password:
        return jsonify({"ok": False, "msg": "Invalid credentials"}), 401
    locked, _until = _check_login_lockout(username)
    if locked:
        return jsonify({"ok": False, "msg": "Too many failed attempts. Try again later."}), 429
    with _db() as (cursor, conn):
        # role + is_active added: this never checked either before, so a
        # terminated HR account (blueprints/admin_views.py's
        # api_hr_accounts_set_status_bearer -- the very deactivate action
        # the mobile HR Accounts screen now exposes) could still log in on
        # mobile after being deactivated, and the client had no way to
        # know it was talking to an HR account rather than an admin one
        # (mobile/src/store/AuthContext.js's user.role has always been a
        # hardcoded 'admin' for any successful admin-panel login).
        cursor.execute(
            "SELECT password, COALESCE(role,'admin'), COALESCE(is_active,1), email FROM admin_users WHERE username=%s",
            (username,)
        )
        row = cursor.fetchone()
        # A company's sign-up admin logs in with their email, stored
        # lowercase -- same fallback as the web login (auth.admin_login).
        if not row and "@" in username and username != username.lower():
            cursor.execute(
                "SELECT password, COALESCE(role,'admin'), COALESCE(is_active,1), email FROM admin_users "
                "WHERE username=%s", (username.lower(),)
            )
            row = cursor.fetchone()
            if row:
                username = username.lower()
    if not row:
        dummy_password_check(password)  # equal timing for unknown usernames
    if not row or not check_password_hash(row[0], password) or not row[2]:
        # Deactivated accounts get the same generic answer as a wrong
        # password, so this endpoint can't be used to probe account state.
        _record_login_failure(username)
        return jsonify({"ok": False, "msg": "Invalid credentials"}), 401
    _clear_login_failures(username)
    # An HR/manager employee's admin account is named after their employee
    # ID: it ends with their employment too.
    ended_on = employment_ended_on(username)
    if ended_on:
        return jsonify({"ok": False, "access_ended": True, "msg": access_ended_message(ended_on)}), 403

    if not current_app.config["MANDATORY_LOGIN_MFA"]:
        token = _issue_admin_api_token(username, mfa_verified=False)
        return jsonify({"ok": True, "token": token, "username": username, "role": row[1]})

    email = row[3]
    if not email:
        log_security_event("auth.mfa_email_missing", "Account has no email on file for login MFA delivery",
                           level="ERROR", identifier=_mask_identifier(username))
        _record_login_failure(username)
        return jsonify({"ok": False, "msg": "Invalid credentials"}), 401
    with _db() as (cursor, conn):
        challenge_id, otp_code = issue_challenge(cursor, "admin_api", username, _API_MFA_TTL_SEC)
        conn.commit()
    send_mfa_login_email(email, username, "Administrator", "", otp_code)
    return jsonify({"ok": True, "mfa_required": True, "challenge_id": challenge_id})


_API_MFA_TTL_SEC = 300


def _issue_admin_api_token(username, mfa_verified):
    token = secrets.token_hex(32)
    with _db() as (cursor, conn):
        cursor.execute(
            "INSERT INTO api_tokens (token, token_type, identity, expires_at, mfa_verified) "
            "VALUES (%s, 'admin', %s, NOW() + INTERVAL '24 hours', %s)",
            (_hash_token(token), username, 1 if mfa_verified else 0)
        )
        conn.commit()
    return token


@core_bp.route("/api/login/verify", methods=["POST"])
@limiter.limit("10 per minute")
@limiter.limit("40 per hour")
def api_login_verify():
    """Step 2 of the mobile admin login: exchanges the challenge_id from
    /api/login plus the emailed code for a Bearer token. The challenge is
    single-use, expires after 5 minutes and is burned after 5 wrong codes
    (utils/login_otp.py). Tokens minted here are marked mfa_verified, which
    is what /api/mobile/web_session_link requires."""
    data = request.get_json(silent=True) or {}
    challenge_id = str(data.get("challenge_id") or "")
    submitted = str(data.get("otp_code") or "")
    with _db() as (cursor, conn):
        verified = verify_challenge(cursor, challenge_id, submitted, ("admin_api",))
        conn.commit()
    if not verified:
        log_security_event("auth.mfa_failure", "Invalid mobile login MFA code", level="WARNING")
        return jsonify({"ok": False, "msg": "Invalid or expired code"}), 401
    username = verified[1]
    with _db() as (cursor, _conn):
        cursor.execute(
            "SELECT COALESCE(role,'admin'), COALESCE(is_active,1) FROM admin_users WHERE username=%s",
            (username,)
        )
        row = cursor.fetchone()
    if not row or not row[1]:
        return jsonify({"ok": False, "msg": "Invalid or expired code"}), 401
    token = _issue_admin_api_token(username, mfa_verified=True)
    log_security_event("auth.admin_login_success", "Admin mobile login completed with emailed code",
                       level="INFO", identifier=_mask_identifier(username), channel="mobile_api")
    return jsonify({"ok": True, "token": token, "username": username, "role": row[0]})


@core_bp.route("/api/logout", methods=["POST"])
def api_logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        with _db() as (cursor, conn):
            cursor.execute("DELETE FROM api_tokens WHERE token=%s", (_hash_token(auth[7:]),))
            conn.commit()
    return jsonify({"ok": True})


@core_bp.route("/api/dashboard", methods=["GET"])
@api_required
def api_dashboard():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()

    cursor.execute("SELECT COUNT(*) FROM employees")
    total = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND login_time IS NOT NULL",
        (today,)
    )
    present = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND status='Late Login'",
        (today,)
    )
    late = cursor.fetchone()[0]
    cursor.execute("""
        SELECT e.employee_id, e.name, a.login_time, a.logout_time, a.status,
               a.logout_status, a.attendance_type
        FROM employees e
        LEFT JOIN attendance a ON e.employee_id=a.employee_id AND a.date=%s
        ORDER BY e.name
    """, (today,))
    rows = cursor.fetchall()
    today_rows = [
        {
            "employee_id": r[0], "name": r[1],
            "login_time": str(r[2]) if r[2] else None,
            "logout_time": str(r[3]) if r[3] else None,
            "login_status": r[4], "logout_status": r[5], "attendance_type": r[6],
        }
        for r in rows
    ]
    pending_leaves, pending_resignations, pending_tickets = get_pending_counts()
    cursor.execute("SELECT COUNT(*) FROM notifications WHERE recipient_type='admin' AND is_read=FALSE")
    unread_notifications = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(company_name, '') FROM company_settings LIMIT 1")
    co_row = cursor.fetchone()
    company_name = co_row[0] if co_row else ""
    # The app dashboard's Announcements card: company-wide ones only -- this
    # endpoint also serves HR and manager accounts, and a private
    # announcement is meant for its one employee.
    cursor.execute(
        "SELECT id, title, content, priority, created_at FROM announcements "
        "WHERE COALESCE(visibility, 'public')='public' ORDER BY created_at DESC LIMIT 10"
    )
    announcements = [
        {"id": str(r[0]), "title": r[1], "message": r[2], "priority": r[3],
         "date": r[4].strftime("%d %b") if r[4] else ""}
        for r in cursor.fetchall()
    ]
    cursor.close()
    db.close()

    return jsonify({
        "ok": True, "total": total, "present": present,
        "absent": total - present, "late": late,
        "today": today.strftime("%d %b %Y"), "today_rows": today_rows,
        "pending_leaves": pending_leaves, "pending_resignations": pending_resignations,
        "pending_tickets": pending_tickets, "unread_notifications": unread_notifications,
        "company_name": company_name, "announcements": announcements,
    })


@core_bp.route("/api/billing_status", methods=["GET"])
@api_required
def api_billing_status():
    """Mobile equivalent of the web's employee-count/seat-limit banner
    (templates/employees.html) and Seats & Billing page
    (templates/seat_checkout.html) -- same underlying data, same tables,
    just JSON instead of server-rendered HTML."""
    co = get_company_settings()
    employee_count = get_tenant_employee_count(g.tenant_db)

    snapshot = get_billing_snapshot(g.tenant_db)
    auto_debit_status = None
    if snapshot["auto_debit"]:
        ad = snapshot["auto_debit"]
        auto_debit_status = {
            "status": ad["status"], "quantity_synced": ad["quantity_synced"],
            "activated_at": ad["activated_at"].isoformat() if ad["activated_at"] else None,
        }
    invoices = [
        {
            "billing_period": inv["billing_period"].isoformat() if inv["billing_period"] else None,
            "employee_count": inv["employee_count"],
            "amount_display": format_price_inr(inv["amount_paise"]), "status": inv["status"],
            "created_at": inv["created_at"].isoformat() if inv["created_at"] else None,
        }
        for inv in snapshot["invoices"]
    ]

    return jsonify({
        "ok": True,
        "employee_count": employee_count,
        "paid_employee_slots": co.get("paid_employee_slots"),
        "monthly_bill_display": format_price_inr(calculate_price(employee_count)),
        "auto_debit": auto_debit_status,
        "invoices": invoices,
    })


@core_bp.route("/api/mobile/web_session_link", methods=["POST"])
@api_required
@limiter.limit("10 per minute")
def api_mobile_web_session_link():
    """Bridges the mobile app's Bearer-token session into a one-time,
    short-lived link that establishes a normal session-cookie admin login
    when opened -- lets the mobile app open a web-only page (currently just
    /settings/seats, for Razorpay Checkout on seat top-ups/auto-debit
    enrollment) in an in-app WebView without reimplementing that UI
    natively. Accepts an optional {"target": "/some/path"} body, checked
    against _BRIDGE_TARGET_ALLOWLIST below -- an unrecognized or omitted
    target silently falls back to the default rather than erroring, since
    this is a convenience parameter, not a caller-facing contract. See
    /mobile_bridge_login/<token> below for the redemption side.

    Tenant isolation here rides entirely on mobile_bridge_tokens being a
    TENANT-SCHEMA table, not a master one: this route and
    /mobile_bridge_login/<token> are both bare, unprefixed paths, so
    _resolve_tenant() resolves both to the exact same schema (today,
    always the single-tenant DB_NAME fallback -- see utils/tenant_routing.py
    module docstring on the mobile app carrying no tenant slug at all).
    Mint and redeem are therefore guaranteed to hit the same schema's
    mobile_bridge_tokens/admin_users tables, so a token minted for one
    tenant is structurally invisible to another regardless of what
    _resolve_tenant()'s fallback logic happens to pick. If /api/* routes
    ever become genuinely per-tenant (a URL-based slug, a tenant claim on
    the Bearer token, etc.), this invariant would need re-verifying rather
    than assumed."""
    # A bridge link becomes a full web admin session, so it must never be
    # mintable from a password-only token: when login MFA is mandatory, the
    # Bearer token must have come from /api/login/verify (emailed code).
    if current_app.config["MANDATORY_LOGIN_MFA"] and not getattr(g, "api_token_mfa_verified", False):
        log_security_event("access.denied", "Web-session bridge refused for a token issued without MFA",
                           level="WARNING", identifier=_mask_identifier(g.api_user))
        return jsonify({"ok": False, "msg": "Please sign in again to continue."}), 403

    data = request.get_json(silent=True) or {}
    target = data.get("target")
    if target not in _BRIDGE_TARGET_ALLOWLIST:
        target = _BRIDGE_DEFAULT_TARGET

    username = g.api_user
    raw_token = secrets.token_hex(32)
    token_hash = _hash_token(raw_token)
    # Bug fix: this used to compute expires_at in Python as
    # datetime.utcnow() + timedelta(minutes=5) and bind it as a plain
    # parameter into a TIMESTAMP (no time zone) column, then compare it
    # against SQL's NOW() at redemption time. Postgres interprets a naive
    # timestamp being compared to NOW() (timestamptz) in the *session's*
    # TimeZone setting, not UTC -- on any server where that setting isn't
    # UTC (e.g. Asia/Calcutta, +5:30 here), the naive UTC value silently
    # gets read back as local wall-clock time, landing hours in the past
    # and making every bridge token look already-expired the instant it's
    # minted, a total feature break rather than a rare edge case. The
    # token-minting code right above this in the same file
    # (api_login/api_employee_login's api_tokens.expires_at) never hits
    # this because it computes expiry with "NOW() + INTERVAL" directly in
    # SQL instead of mixing in a Python-side datetime -- doing the same
    # here keeps expiry computation and comparison in the same timezone
    # frame of reference no matter what the server's TimeZone GUC is.
    with _db() as (cursor, conn):
        cursor.execute("DELETE FROM mobile_bridge_tokens WHERE expires_at < NOW()")
        cursor.execute(
            "INSERT INTO mobile_bridge_tokens (token_hash, admin_username, target_path, expires_at) "
            "VALUES (%s, %s, %s, NOW() + INTERVAL '5 minutes')",
            (token_hash, username, target)
        )
        conn.commit()

    url = f"{_safe_app_url()}{tpath('/mobile_bridge_login/' + raw_token)}"
    return jsonify({"ok": True, "url": url})


def _bridge_link_expired_response():
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
        "<title>Link Expired</title>"
        "<style>body{font-family:'Segoe UI',sans-serif;background:#f1f5f9;display:flex;"
        "align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px;}"
        ".card{max-width:380px;text-align:center;background:#fff;border-radius:16px;"
        "padding:32px 24px;box-shadow:0 4px 20px rgba(0,0,0,0.08);}"
        "h1{font-size:17px;color:#0f172a;margin:0 0 8px;}"
        "p{font-size:13px;color:#64748b;margin:0;}</style></head><body>"
        "<div class='card'><h1>This link has expired</h1>"
        "<p>Go back to the app and try again.</p></div></body></html>",
        403,
    )


@core_bp.route("/mobile_bridge_login/<token>", methods=["GET"])
@limiter.limit("30 per minute")
def mobile_bridge_login(token):
    """Redeems a one-time bridge token (see /api/mobile/web_session_link
    above) into a real admin session-cookie login, then sends the browser
    (an in-app WebView on mobile) into whichever page was requested at mint
    time (target_path, re-checked against _BRIDGE_TARGET_ALLOWLIST here too
    since a row is only ever trustworthy as far as what was validated when
    it was written). No @admin_required here on purpose: this route's whole
    job is to CREATE a session, not require one already existing.
    Single-use (deleted immediately on redemption) and expires in 5
    minutes, same hash-and-expire shape as admin_users.reset_token."""
    token_hash = _hash_token(token)
    with _db() as (cursor, conn):
        cursor.execute(
            "SELECT admin_username, target_path FROM mobile_bridge_tokens WHERE token_hash=%s AND expires_at > NOW()",
            (token_hash,)
        )
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return _bridge_link_expired_response()
        username, target_path = row
        if target_path not in _BRIDGE_TARGET_ALLOWLIST:
            target_path = _BRIDGE_DEFAULT_TARGET
        cursor.execute("DELETE FROM mobile_bridge_tokens WHERE token_hash=%s", (token_hash,))
        cursor.execute("SELECT COALESCE(role,'admin') FROM admin_users WHERE username=%s", (username,))
        role_row = cursor.fetchone()
        conn.commit()

    if not role_row:
        return _bridge_link_expired_response()

    # Same session state blueprints/auth.py's admin_login sets on success,
    # minus device-fingerprint capture/new-IP email (this is an ephemeral
    # WebView session bridging an already-authenticated mobile session, not
    # a fresh login worth alerting on).
    session.clear()
    session["admin_logged_in"] = True
    session["admin_username"] = username
    session["admin_role"] = role_row[0]
    session["_session_created"] = time.time()
    session.permanent = True
    ensure_session_id(session)
    stamp_password_version("admin_users", username)

    log_security_event(
        "auth.mobile_bridge_login", f"Mobile app bridged into a web session for '{username}' -> {target_path}",
        level="INFO", identifier=username,
    )
    return redirect(tpath(target_path))


@core_bp.route("/api/settings/update", methods=["POST"])
@api_required
def api_settings_update():
    data = request.get_json() or {}
    return jsonify({"ok": True, "msg": "System settings updated successfully.", "settings": data})


@core_bp.route("/api/admin/profile", methods=["GET"])
@api_required
def api_admin_profile():
    """The logged-in admin/HR account's own real username/email/role --
    mobile/src/screens/admin/SettingsScreen.js's Admin Profile tab used to
    hardcode "Administrator"/"admin@company.com" for every account
    regardless of who was actually logged in."""
    username = g.api_user
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT email, COALESCE(role,'admin'), created_at FROM admin_users WHERE username=%s", (username,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return jsonify({"ok": False, "msg": "Account not found."}), 404
    return jsonify({
        "ok": True,
        "username": username,
        "email": row[0] or "",
        "role": row[1],
        "created_at": str(row[2]) if row[2] else "",
    })


@core_bp.route("/api/employee/login", methods=["POST"])
@limiter.limit("5 per minute")
@limiter.limit("20 per hour")
def api_employee_login():
    data = request.get_json(silent=True) or {}
    # Passwords are used exactly as typed (no strip) -- and both fields are
    # validated BEFORE any lookup, so a missing password can't produce a
    # different answer for a real employee ID than for a made-up one.
    return _api_employee_login(str(data.get("employee_id") or "").strip(), str(data.get("password") or ""))


def _api_employee_login(emp_id, password):
    """The body of /api/employee/login, shared with /api/signin."""
    if not emp_id or not password:
        return jsonify({"ok": False, "msg": "Employee ID and password are required."}), 400
    # Check lockout before hitting the DB with credentials
    locked, _until = _check_login_lockout(emp_id, "employee")
    if locked:
        return jsonify({"ok": False, "msg": "Too many failed attempts. Try again later."}), 429
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT name, email, password, COALESCE(must_change_password,0) FROM employees "
                   "WHERE employee_id=%s", (emp_id,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        dummy_password_check(password)  # equal timing for unknown IDs
        _record_login_failure(emp_id, "employee")
        return jsonify({"ok": False, "msg": "Invalid credentials"}), 401
    if not row[2] or not check_password_hash(row[2], password):
        _record_login_failure(emp_id, "employee")
        return jsonify({"ok": False, "msg": "Invalid credentials"}), 401
    _clear_login_failures(emp_id, "employee")
    # Past the last working day of an accepted resignation: the right
    # password, but no token. Records are kept (utils/auth.py).
    ended_on = employment_ended_on(emp_id)
    if ended_on:
        return jsonify({"ok": False, "access_ended": True, "msg": access_ended_message(ended_on)}), 403
    # An admin-issued temporary password (blueprints/employees.py reset)
    # must be replaced before it can mint a 24h token; the app has no
    # change-password screen that works without one, so send them to the
    # web login, whose /force_change_pin step clears the flag.
    if row[3]:
        return jsonify({"ok": False, "password_change_required": True,
                        "msg": "Your password was reset by your administrator. Sign in on the web portal "
                               "to set a new password, then sign in here again."}), 403
    # Upgrade legacy hash to bcrypt transparently
    if row[2] and not row[2].startswith("$2"):
        with _db() as (_uc, _ud):
            _uc.execute("UPDATE employees SET password=%s WHERE employee_id=%s",
                        (generate_password_hash(password), emp_id))
            _ud.commit()
    token = secrets.token_hex(32)
    with _db() as (cursor, conn):
        cursor.execute(
            "INSERT INTO api_tokens (token, token_type, identity, expires_at) "
            "VALUES (%s, 'employee', %s, NOW() + INTERVAL '24 hours')",
            (_hash_token(token), emp_id)
        )
        conn.commit()

    return jsonify({
        "ok": True, "token": token, "employee_id": emp_id,
        "name": row[0], "email": row[1],
    })


@core_bp.route("/api/signin", methods=["POST"])
@limiter.limit("5 per minute")
@limiter.limit("20 per hour")
def api_signin():
    """The app's one sign-in box, with the website /login's rules
    (auth.admin_login): an admin, HR or manager account whose username --
    or lowercased email -- and password match signs in as that; anything
    else is tried as an employee ID. The answer is /api/login's or
    /api/employee/login's, plus "account": "admin" | "employee"."""
    data = request.get_json(silent=True) or {}
    identifier = str(data.get("identifier") or "").strip()
    password = str(data.get("password") or "")
    if "\x00" in identifier or "\x00" in password or not identifier or not password:
        return jsonify({"ok": False, "msg": "Invalid credentials"}), 401
    if _admin_password_matches(identifier, password):
        account, result = "admin", _api_admin_login(identifier, password)
    else:
        account, result = "employee", _api_employee_login(identifier.upper(), password)
    resp, status = result if isinstance(result, tuple) else (result, 200)
    body = resp.get_json(silent=True)
    if isinstance(body, dict) and body.get("ok"):
        body["account"] = account
        return jsonify(body), status
    return resp, status


def _admin_password_matches(identifier, password):
    """True when an active admin account (by username, or by the lowercased
    email sign-up admins use) has this password. Records nothing: the login
    that follows does its own lockout and failure bookkeeping."""
    candidates = [identifier]
    if "@" in identifier and identifier != identifier.lower():
        candidates.append(identifier.lower())
    with _db() as (cursor, _conn):
        for username in candidates:
            cursor.execute(
                "SELECT password, COALESCE(is_active,1) FROM admin_users WHERE username=%s", (username,)
            )
            row = cursor.fetchone()
            if row:
                return bool(row[0]) and bool(row[1]) and check_password_hash(row[0], password)
    return False


@core_bp.route("/api/employee/logout", methods=["POST"])
def api_employee_logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        with _db() as (cursor, conn):
            cursor.execute("DELETE FROM api_tokens WHERE token=%s", (_hash_token(auth[7:]),))
            conn.commit()
    return jsonify({"ok": True})


@core_bp.route("/api/employee/signup", methods=["POST"])
@limiter.limit("5 per minute")
@api_required
def api_employee_signup():
    """The app's admin Add Employee (mobile/src/api/client.js's addEmployee):
    creates an employee with the password the admin typed. Needs an admin
    token, like /api/employees and the web's /add_employee_page -- open, it
    let anyone who knew a company's URL create themselves an employee,
    including role "HR" or "Manager", which auth.py's
    _finish_employee_login() turns into admin-panel access."""
    data = request.get_json() or {}
    emp_id = data.get("employee_id", "").strip().upper()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip() or None
    password = data.get("password", "").strip()
    role = data.get("role", "Employee").strip()
    department = data.get("department", "Engineering").strip()
    phone = (data.get("phone") or "").strip() or None
    date_of_joining = (data.get("date_of_joining") or "").strip() or None

    if not emp_id or not name or not password:
        return jsonify({"ok": False, "msg": "Employee ID, Full Name, and Password are required."}), 400
    if date_of_joining:
        try:
            date.fromisoformat(date_of_joining)
        except ValueError:
            return jsonify({"ok": False, "msg": "Date of joining must be in YYYY-MM-DD format."}), 400

    _pw_ok, _pw_err = validate_new_password(password)
    if not _pw_ok:
        return jsonify({"ok": False, "msg": _pw_err}), 400

    _domain_error = validate_employee_email_domain(email)
    if _domain_error:
        return jsonify({"ok": False, "msg": _domain_error}), 400

    # Same paid-seat cap blueprints/employees.py's add_employee_page() and
    # api_register_employee() already enforce on the web/token-authenticated
    # admin-add-employee paths -- this is the endpoint the mobile app's own
    # admin "Add Employee" screen calls (see mobile/src/api/client.js's
    # addEmployee()), so without this check a tenant's plan-limit cap was
    # only ever real on the web, not on mobile.
    _seat_error = add_employee_seat_cap_check()
    if _seat_error:
        return jsonify({"ok": False, "msg": _seat_error}), 403

    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
        if cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": f"Employee ID '{emp_id}' is already registered."}), 400

        # Authoritative, lock-protected recheck immediately before the
        # INSERT, wrapped in an explicit transaction() -- the pooled
        # connection defaults to autocommit=True, so without an explicit
        # transaction the FOR UPDATE lock taken inside
        # add_employee_seat_cap_check() would release itself the instant
        # that one SELECT statement finished, before this INSERT ever
        # ran, and the race would still be wide open. The earlier
        # add_employee_seat_cap_check() call above (no cursor, own
        # connection) is only a cheap UX preflight -- see that function's
        # docstring.
        hashed_pw = generate_password_hash(password)
        _seat_error = None
        with transaction(db):
            _seat_error = add_employee_seat_cap_check(cursor)
            if not _seat_error:
                cursor.execute(
                    "INSERT INTO employees (employee_id, name, email, role, department, phone, password, "
                    "date_of_joining) VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s::date, NOW()))",
                    (emp_id, name, email, role, department, phone, hashed_pw, date_of_joining)
                )
        if _seat_error:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": _seat_error}), 403
        cursor.close()
        db.close()
        if email:
            _ecfg = get_email_config()
            if _ecfg:
                _login_url = employee_login_url()
                _html = (f"<p>Hi <strong>{name}</strong>, your account is ready.</p>"
                         f"<p>Employee ID: <strong>{emp_id}</strong></p>"
                         f"<p><a href=\"{_login_url}\">{_login_url}</a></p>")
                try:
                    send_email_smtp(email, f"Welcome {name} -- Your Account is Ready", _html, _ecfg)
                except Exception:
                    app_log.error("api_employee_signup: welcome email failed", exc_info=True)
        return jsonify({
            "ok": True,
            "msg": f"Employee account for {name} ({emp_id}) created successfully! You can now sign in.",
            "employee_id": emp_id
        })
    except Exception as exc:
        app_log.error("api_employee_signup failed: %s", exc)
        if cursor:
            try:
                cursor.close()
            except Exception as _close_exc:
                app_log.debug("api_employee_signup error-path cursor.close() failed: %s", _close_exc)
        if db:
            try:
                db.close()
            except Exception as _close_exc:
                app_log.debug("api_employee_signup error-path db.close() failed: %s", _close_exc)
        return jsonify({"ok": False, "msg": "Could not register employee. Please try again."}), 500
