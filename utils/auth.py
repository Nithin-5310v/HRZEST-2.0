# -*- coding: utf-8 -*-
"""Authentication decorators, login lockout, and password hashing."""
import os
import json
import time
import datetime
import hashlib
import urllib.request  # noqa: F401 -- module-level so tests can monkeypatch auth_module.urllib.request.urlopen
import bcrypt as _bcrypt
from functools import wraps
from flask import session, request, jsonify, redirect, url_for, g as _flask_g, current_app
from werkzeug.security import check_password_hash as _wz_check_pw
from extensions import app_log, log_security_event
from utils.session_risk import is_session_compromised, evaluate_session_risk
from utils.async_writer import enqueue_write
from utils.helpers import _db

# ── Password hashing (bcrypt with legacy pbkdf2 fallback) ────────────────────


def generate_password_hash(pw: str, **_) -> str:
    return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt(rounds=12)).decode()


def check_password_hash(pw_hash: str, pw: str) -> bool:
    if not pw_hash:
        return False
    if pw_hash.startswith("$2b$") or pw_hash.startswith("$2a$"):
        try:
            return _bcrypt.checkpw(pw.encode(), pw_hash.encode())
        except Exception:
            return False
    return _wz_check_pw(pw_hash, pw)


_DUMMY_PASSWORD_HASH = None


def dummy_password_check(pw: str) -> None:
    """Spend one real bcrypt check on a throwaway hash -- call this when a
    login identifier matches no account, so it costs the same time as a
    real wrong-password check and response timing can't reveal whether
    the account exists. The hash is of a random value built once per
    process, so it can never match."""
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        import secrets as _secrets
        _DUMMY_PASSWORD_HASH = generate_password_hash(_secrets.token_urlsafe(32))
    check_password_hash(_DUMMY_PASSWORD_HASH, pw or "")


# Single source of truth for the "new password" minimum -- every set/change/
# reset endpoint (admin, employee, platform admin seed script) must call
# this instead of its own inline `len(pw) < N` check, so the floor can't
# silently drift to something weaker on one path.
MIN_PASSWORD_LENGTH = 8


def validate_new_password(pw: str):
    """Return (True, None) if pw meets the minimum bar, else (False, error_message)."""
    if not pw or len(pw) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return True, None


def verify_and_update_password(table, id_column, id_value, current_pw, new_pw):
    """Verify current_pw against `table`.password for the row matching
    id_column=id_value, then overwrite it with new_pw. Returns True on
    success, False if current_pw was wrong or the row doesn't exist (no
    row is touched in that case). table/id_column are always call-site
    literals ("admin_users"/"username" or "employees"/"employee_id"),
    never request-controlled, so the f-string below carries no injection
    risk. Shared by the web-session and Bearer-API "change own password"
    routes for both admin_users and employees, which previously
    reimplemented this identical verify-then-update per channel."""
    from database import get_db_connection
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(f"SELECT password FROM {table} WHERE {id_column}=%s", (id_value,))  # nosec B608 -- call-site literals
    row = cursor.fetchone()
    if not row or not check_password_hash(row[0], current_pw):
        cursor.close()
        db.close()
        return False
    cursor.execute(
        f"UPDATE {table} SET password=%s WHERE {id_column}=%s",  # nosec B608 -- call-site literals
        (generate_password_hash(new_pw), id_value)
    )
    record_password_change(cursor, table, id_value)
    db.commit()
    cursor.close()
    db.close()
    refresh_own_password_version(table, id_value)
    return True


# ── Password-change revocation ────────────────────────────────────────────────
# Sessions are signed cookies with no server-side store, so "log out every
# other device" has to be done by comparison: each login stamps the
# account's password_changed_at into the session (_pwv), and every
# authenticated request re-reads it -- if it moved, the session predates a
# password change/reset and is rejected. Compared as an opaque value read
# from the same DB, never app-clock vs DB-clock, so clock skew between the
# app and database hosts can't wrongly log anyone out.
_PASSWORD_TABLES = {"admin_users": "username", "employees": "employee_id"}


def record_password_change(cursor, table: str, id_value) -> None:
    """Call in the SAME transaction as every password UPDATE (self-service
    change, reset link, forced PIN change, admin reset). Moves
    password_changed_at -- invalidating every older session -- and deletes
    every API token and pending web-bridge link for the identity. Caller
    commits. `table` must be a key of _PASSWORD_TABLES (call-site literal)."""
    id_column = _PASSWORD_TABLES[table]
    cursor.execute(f"UPDATE {table} SET password_changed_at=NOW() WHERE {id_column}=%s",  # nosec B608 -- whitelisted literals
                   (id_value,))
    cursor.execute("DELETE FROM api_tokens WHERE identity=%s", (id_value,))
    cursor.execute("DELETE FROM mobile_bridge_tokens WHERE admin_username=%s", (id_value,))
    log_security_event("auth.password_changed", "Password changed; older sessions and API tokens revoked",
                       level="INFO", identifier=_mask_identifier(str(id_value)), account_type=table)


def _read_password_version(table: str, id_value):
    """'' when never changed (or no such row), the timestamp's isoformat
    otherwise, None if the lookup itself failed."""
    id_column = _PASSWORD_TABLES.get(table)
    if not id_column:
        return None
    try:
        with _db() as (cur, _conn):
            cur.execute(f"SELECT password_changed_at FROM {table} WHERE {id_column}=%s",  # nosec B608 -- whitelisted literals
                        (id_value,))
            row = cur.fetchone()
    except Exception as exc:
        app_log.warning("password version lookup failed (%s): %s", table, type(exc).__name__)
        return None
    return row[0].isoformat() if row and row[0] else ""


def stamp_password_version(table: str, id_value) -> None:
    """Record, at login, which credential this session was established
    with and its current password version. `table` is the table whose
    password was actually checked -- e.g. "employees" for an HR employee
    routed into the admin panel, since that's the password a reset would
    change."""
    version = _read_password_version(table, id_value)
    session["_pwv"] = [table, id_value, version or ""]


def refresh_own_password_version(table: str, id_value) -> None:
    """After a user changes their OWN password, keep the session that did
    it alive (every other session for that account still dies)."""
    pwv = session.get("_pwv")
    if pwv and pwv[0] == table and pwv[1] == id_value:
        stamp_password_version(table, id_value)


def _password_changed_since_login() -> bool:
    """True if this session was stamped at login and the account's password
    has been changed/reset since. Sessions without a stamp (created before
    this check existed) pass -- rotating SECRET_KEY at deploy retires those.
    Fails open on a DB error, same posture as the other per-request checks."""
    pwv = session.get("_pwv")
    if not pwv:
        return False
    try:
        table, id_value, stamped = pwv
    except (TypeError, ValueError):
        return True
    current = _read_password_version(table, id_value)
    if current is None:
        return table not in _PASSWORD_TABLES
    return current != stamped


def _reject_if_password_changed(login_endpoint: str):
    if _password_changed_since_login():
        log_security_event("session.rejected", "Session predates a password change/reset",
                           level="WARNING", identifier=_mask_identifier(
                               str(session.get("admin_username") or session.get("employee_id") or "")))
        session.clear()
        if _is_ajax_request():
            return jsonify({"ok": False, "msg": "Session expired. Please log in again.",
                            "redirect": url_for(login_endpoint)}), 401
        return redirect(url_for(login_endpoint, locked="1"))
    return None


def _is_ajax_request() -> bool:
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.headers.get("Accept", "").startswith("application/json")
        or request.headers.get("Content-Type", "").startswith("application/json")
        or request.is_json
    )


# ── Token hashing ─────────────────────────────────────────────────────────────
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Account lockout ───────────────────────────────────────────────────────────
# 5 matches what app.py's now-removed duplicate decorators actually enforced
# in production -- adopted as canonical here rather than the 10 this module
# used before consolidation, to avoid silently loosening lockout as a side
# effect of removing the duplicate.
_LOGIN_MAX_ATTEMPTS = 4
_LOGIN_LOCKOUT_MINUTES = 15

# ── CAPTCHA gate (Cloudflare Turnstile) ────────────────────────────────────────
# Unconfigured (no TURNSTILE_SECRET_KEY) means the gate is simply never
# triggered -- deliberately fail-open on missing config, not fail-closed.
# Failing closed here would mean "every login past attempt 2 is permanently
# rejected until an admin sets the key," a self-inflicted denial of service
# on the login system itself, unlike e.g. the malware-scan fail-closed
# default in utils/helpers.py where the blast radius is one rejected upload.
_TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
_TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
CAPTCHA_AFTER_ATTEMPTS = 2


def turnstile_enabled() -> bool:
    return bool(_TURNSTILE_SITE_KEY and _TURNSTILE_SECRET_KEY)


def _mask_identifier(identifier: str) -> str:
    """Obscure a username/employee-ID/email before it ever reaches a log
    line -- logs get shipped to less-trusted places (CloudWatch, a Slack
    webhook channel) than the DB itself, and there's no operational need
    for the full identifier to appear in either. The DB-side lockout logic
    (_check_login_lockout etc.) always uses the real, unmasked identifier --
    only what gets logged is obscured."""
    if not identifier:
        return "(empty)"
    if "@" in identifier:
        local, _, domain = identifier.partition("@")
        return f"{local[:1]}***@{domain}"
    if len(identifier) <= 3:
        return identifier[0] + "*" * (len(identifier) - 1)
    return identifier[:2] + "*" * (len(identifier) - 3) + identifier[-1]


def _get_failed_count(identifier: str, attempt_type: str = "admin") -> int:
    """Synchronous read of the current failed-attempt count -- used only to
    decide whether to render the CAPTCHA widget for the *next* attempt.
    Deliberately NOT read-after-write against _record_login_failure, which
    enqueues its DB write onto the background writer thread (see its own
    docstring) and may not have landed yet; callers instead compute
    "current + 1" against this value rather than re-querying post-write."""
    try:
        with _db() as (cur, _):
            cur.execute(
                "SELECT failed_count FROM login_attempts WHERE identifier=%s AND attempt_type=%s",
                (identifier, attempt_type),
            )
            row = cur.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def verify_turnstile(token: str, remote_ip: str) -> bool:
    """Server-side verification against Cloudflare's siteverify endpoint --
    the client-submitted token proves nothing on its own without this
    round trip. Same urllib.request pattern already used for webhook
    delivery (utils/alerts.py) and the AI assistant's API call
    (utils/ai_assistant.py) -- no new dependency."""
    if not _TURNSTILE_SECRET_KEY or not token:
        return False
    try:
        import urllib.parse
        body = urllib.parse.urlencode({
            "secret": _TURNSTILE_SECRET_KEY,
            "response": token,
            "remoteip": remote_ip or "",
        }).encode()
        req = urllib.request.Request(_TURNSTILE_VERIFY_URL, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            result = json.loads(resp.read().decode("utf-8"))
        return bool(result.get("success"))
    except Exception as e:
        app_log.warning("Turnstile verification request failed: %s", e)
        return False


def _check_login_lockout(identifier: str, attempt_type: str = "admin"):
    try:
        with _db() as (cur, _):
            cur.execute(
                "SELECT locked_until FROM login_attempts WHERE identifier=%s AND attempt_type=%s",
                (identifier, attempt_type)
            )
            row = cur.fetchone()
        if row and row[0] and row[0] > datetime.datetime.now():
            return True, row[0].strftime("%H:%M")
    except Exception as exc:
        # Fails open (treated as not-locked) -- a lockout check that can't
        # run is exactly the kind of failure that should never be silent.
        app_log.warning("_check_login_lockout failed for %s (%s): %s", _mask_identifier(identifier), attempt_type, exc, exc_info=True)
    return False, None


def _record_login_failure(identifier: str, attempt_type: str = "admin"):
    """Called from the request-handling thread on every failed login -- must
    stay fast unconditionally, including under a brute-force flood, which is
    exactly when this gets called the most. The actual DB write (measured:
    3s median / 5.8s max latency under 60 concurrent attempts against one
    identifier, from row-lock + connection-pool contention) is handed off to
    the single background writer thread instead of run here. See
    utils/async_writer.py for why this is an in-process queue, not Celery.

    Trade-off, stated plainly: lockout becomes eventually consistent rather
    than exact-on-the-5th-request -- the counter increment and lockout check
    happen slightly after the response for attempt N has already been sent.
    Bounded by queue throughput (a single writer processing sequentially,
    typically sub-millisecond per write once decoupled from contention), not
    unbounded. Acceptable for a defense-in-depth control; would not be
    acceptable for the password check itself, which stays fully synchronous.
    """
    log_security_event(
        "auth.failure", "Failed login attempt", level="WARNING",
        identifier=_mask_identifier(identifier), attempt_type=attempt_type,
    )
    enqueue_write(_record_login_failure_db, identifier, attempt_type)


def _record_login_failure_db(identifier: str, attempt_type: str = "admin"):
    """The actual DB write -- runs only on the background writer thread,
    never on a request thread. Do not call this directly from a route."""
    try:
        with _db() as (cur, conn):
            cur.execute(
                "INSERT INTO login_attempts (identifier, attempt_type, failed_count, last_attempt) "
                "VALUES (%s, %s, 1, NOW()) "
                "ON CONFLICT (identifier, attempt_type) DO UPDATE SET "
                "failed_count=login_attempts.failed_count+1, last_attempt=NOW()",
                (identifier, attempt_type)
            )
            conn.commit()
            cur.execute(
                "SELECT failed_count FROM login_attempts WHERE identifier=%s AND attempt_type=%s",
                (identifier, attempt_type)
            )
            row = cur.fetchone()
            if row and row[0] >= _LOGIN_MAX_ATTEMPTS:
                lockout_until = datetime.datetime.now() + datetime.timedelta(minutes=_LOGIN_LOCKOUT_MINUTES)
                cur.execute(
                    "UPDATE login_attempts SET locked_until=%s WHERE identifier=%s AND attempt_type=%s",
                    (lockout_until, identifier, attempt_type)
                )
                conn.commit()
                log_security_event(
                    "auth.lockout", "Account locked after repeated failed logins", level="ERROR",
                    identifier=_mask_identifier(identifier), attempt_type=attempt_type,
                    failed_count=row[0], locked_until=lockout_until.isoformat(),
                )
    except Exception as exc:
        # This DB write is what actually enforces lockout -- a silent
        # failure here means brute-force protection isn't tracking
        # attempts at all for this identifier.
        app_log.warning("_record_login_failure_db failed for %s (%s): %s", _mask_identifier(identifier), attempt_type, exc, exc_info=True)


def _clear_login_failures(identifier: str, attempt_type: str = "admin"):
    """Enqueued onto the SAME writer queue as _record_login_failure_db, not
    written synchronously -- critical for correctness, not just speed. If
    this ran immediately while a burst of recent failures was still queued
    (e.g. a user's 5th failed attempt followed instantly by a correct 6th),
    a synchronous clear could run BEFORE those queued failure-writes land,
    and the failures would then land AFTER the clear -- undoing it, leaving
    a phantom failed_count despite the successful login. Routing both
    through one FIFO queue with a single consumer thread guarantees this
    clear always executes after every failure recorded before it, with no
    ordering race possible.
    """
    enqueue_write(_clear_login_failures_db, identifier, attempt_type)


def _clear_login_failures_db(identifier: str, attempt_type: str = "admin"):
    try:
        with _db() as (cur, conn):
            cur.execute(
                "DELETE FROM login_attempts WHERE identifier=%s AND attempt_type=%s",
                (identifier, attempt_type)
            )
            conn.commit()
    except Exception as exc:
        # Fails safe (stale failure count just stays, doesn't unlock
        # anything it shouldn't) but still worth knowing about.
        app_log.warning("_clear_login_failures_db failed for %s (%s): %s", _mask_identifier(identifier), attempt_type, exc, exc_info=True)


# ── Session kill-switch enforcement ───────────────────────────────────────────
def _reject_if_compromised(login_endpoint: str):
    """The actual kill switch. Checked on every authenticated request, not
    just at login -- a session flagged 'compromised' mid-lifetime (see
    utils/session_risk.py) is dead on its very next request regardless of
    whether the browser tab that owns it ever sees the SSE push telling it
    to log itself out. Returns a redirect Response if the session should be
    killed, or None if it's fine to proceed."""
    sid = session.get("_sid")
    if sid and is_session_compromised(sid):
        log_security_event("session.rejected", "Rejected a request from a compromised session",
                           level="WARNING", identifier=session.get("admin_username")
                           or session.get("employee_id"))
        session.clear()
        return redirect(url_for(login_endpoint, locked="1"))
    return None


def _account_still_active(table: str, id_column: str, id_value) -> bool:
    """Re-checks that the session's own account still exists and is
    active. api_required() (below) already gives every Bearer-token route
    this same guarantee on each request -- LEFT JOIN admin_users, check
    is_active -- specifically so deactivating/deleting an account revokes
    access immediately rather than just blocking future logins. Web
    sessions (admin_required/employee_required/role_required below) never
    had the equivalent: they only ever checked session flags, with no
    per-request DB re-check at all, so a deleted/deactivated account's
    already-open browser tab kept working until the session's own idle/
    absolute timeout. table/id_column are always call-site literals
    ("admin_users"/"username" or "employees"/"employee_id"), never
    request-controlled. Fails OPEN on a DB error (same posture as
    is_session_compromised() above) so a transient outage doesn't lock
    every legitimate session out of the whole app."""
    if not id_value:
        return True
    try:
        from database import get_db_connection
        db = get_db_connection()
        cur = db.cursor()
        cur.execute(f"SELECT COALESCE(is_active,1) FROM {table} WHERE {id_column}=%s", (id_value,))  # nosec B608 -- table/id_column are fixed call-site literals, never request input; id_value is %s-bound  # noqa: E501
        row = cur.fetchone()
        cur.close()
        db.close()
        return bool(row) and bool(row[0])
    except Exception as e:
        app_log.error("_account_still_active check failed for %s.%s: %s", table, id_column, e)
        return True


def _reject_if_account_deactivated(table: str, id_column: str, id_value, login_endpoint: str):
    """Companion to _reject_if_compromised() above -- same return
    contract (a redirect Response to kill the request, or None to let it
    proceed). Kept as a separate function/DB round trip rather than
    folded into that one so a session-risk failure and an account-status
    failure stay independently diagnosable in the logs."""
    if not _account_still_active(table, id_column, id_value):
        log_security_event(
            "access.denied", "Session rejected: account deactivated or deleted since login",
            level="WARNING", identifier=id_value,
        )
        session.clear()
        return redirect(url_for(login_endpoint, locked="1"))
    return None


# Every guard below tags its wrapper with _hrz_auth_guard; functools.wraps
# copies it outward through limiter/other wrappers, so a test can verify
# every registered route is guarded (tests/test_route_auth_guards.py).
# ── Web session guards ────────────────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("admin_logged_in") and session.get("employee_id"):
            session.pop("employee_id", None)
            session.pop("employee_name", None)
            session.pop("employee_role", None)
        if not session.get("admin_logged_in"):
            # employee_id present but no admin session = an authenticated
            # employee reaching for an admin-only route, not just an
            # anonymous visitor -- that's the signal worth a WARNING; a
            # plain anonymous hit is routine enough to log at INFO only.
            _level = "WARNING" if session.get("employee_id") else "INFO"
            log_security_event("access.denied", "Unauthenticated request to admin-only route",
                               level=_level, required="admin")
            is_ajax = (
                request.headers.get("X-Requested-With") == "XMLHttpRequest"
                or request.headers.get("Accept", "").startswith("application/json")
                or request.headers.get("Content-Type", "").startswith("application/json")
                or request.is_json
            )
            if is_ajax:
                return jsonify({"ok": False, "msg": "Session expired. Please log in again.",
                                "redirect": url_for("auth.admin_login")}), 401
            return redirect(url_for("auth.admin_login"))
        _killed = _reject_if_compromised("auth.admin_login")
        if _killed:
            return _killed
        _killed = _reject_if_account_deactivated("admin_users", "username", session.get("admin_username"), "auth.admin_login")
        if _killed:
            return _killed
        _killed = _reject_if_password_changed("auth.admin_login")
        if _killed:
            return _killed
        return f(*args, **kwargs)
    wrapper._hrz_auth_guard = "admin"  # read by tests/test_route_auth_guards.py
    return wrapper


def employee_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("admin_logged_in") and session.get("employee_id"):
            session.pop("employee_id", None)
            session.pop("employee_name", None)
            session.pop("employee_role", None)
            return redirect("/admin")
        if not session.get("employee_id"):
            log_security_event("access.denied", "Unauthenticated request to employee-only route",
                               level="INFO", required="employee")
            # There is no standalone "employee_login" endpoint anymore --
            # employee and admin credentials are both checked by the one
            # unified /login page. The literal "/employee_login" path this
            # used to redirect to 404s (dead route); auth.admin_login is
            # also what url_for(...) resolves to a build error for below.
            return redirect(url_for("auth.admin_login"))
        _killed = _reject_if_compromised("auth.admin_login")
        if _killed:
            return _killed
        _killed = _reject_if_account_deactivated("employees", "employee_id", session.get("employee_id"), "auth.admin_login")
        if _killed:
            return _killed
        _killed = _reject_if_password_changed("auth.admin_login")
        if _killed:
            return _killed
        # Prevent bypassing forced password change by navigating directly to portal
        from flask import request as _req
        if session.get("_fpc") and _req.endpoint not in ("auth.force_change_pin", "force_change_pin"):
            return redirect("/force_change_pin")
        return f(*args, **kwargs)
    wrapper._hrz_auth_guard = "employee"  # read by tests/test_route_auth_guards.py
    return wrapper


def any_user_required(f):
    """For routes open to BOTH an admin-side session and an employee
    session (shared downloads, own-photo, payslips, ...) that previously
    hand-rolled `if not admin and not employee: redirect` and so skipped
    every per-request check the real decorators do. Applies the same
    kill-switch, account-still-active and password-changed checks as
    admin_required/employee_required; per-resource ownership is still the
    route's job (enforce_ownership())."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        is_admin = bool(session.get("admin_logged_in"))
        if not is_admin and not session.get("employee_id"):
            log_security_event("access.denied", "Unauthenticated request to signed-in-only route",
                               level="INFO", required="any_user")
            if _is_ajax_request():
                return jsonify({"ok": False, "msg": "Session expired. Please log in again.",
                                "redirect": url_for("auth.admin_login")}), 401
            return redirect(url_for("auth.admin_login"))
        _killed = _reject_if_compromised("auth.admin_login")
        if _killed:
            return _killed
        if is_admin:
            _killed = _reject_if_account_deactivated("admin_users", "username", session.get("admin_username"),
                                                     "auth.admin_login")
        else:
            _killed = _reject_if_account_deactivated("employees", "employee_id", session.get("employee_id"),
                                                     "auth.admin_login")
        if _killed:
            return _killed
        _killed = _reject_if_password_changed("auth.admin_login")
        if _killed:
            return _killed
        return f(*args, **kwargs)
    wrapper._hrz_auth_guard = "any_user"  # read by tests/test_route_auth_guards.py
    return wrapper


def session_account_ok() -> bool:
    """Non-redirecting form of any_user_required's checks, for JSON routes
    that accept a session OR a Bearer token and so can't use a decorator.
    True only for a signed-in session that isn't flagged compromised,
    whose account is still active, and whose password hasn't changed
    since login."""
    sid = session.get("_sid")
    if sid and is_session_compromised(sid):
        return False
    if session.get("admin_logged_in"):
        if not _account_still_active("admin_users", "username", session.get("admin_username")):
            return False
    elif session.get("employee_id"):
        if not _account_still_active("employees", "employee_id", session.get("employee_id")):
            return False
    else:
        return False
    return not _password_changed_since_login()


def role_required(*allowed_roles):
    """Like admin_required, but also requires session['admin_role'] to be one
    of allowed_roles. admin_required alone only checks admin_logged_in, so it
    grants every admin-side role (admin/manager/soc_analyst) equally -- use
    this instead for routes that must stay admin-only (payroll mutation,
    employee deletion, credential resets, PII-bearing payslip views)."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not session.get("admin_logged_in"):
                log_security_event("access.denied", "Unauthenticated request to role-restricted route",
                                   level="INFO", required="|".join(allowed_roles))
                is_ajax = (
                    request.headers.get("X-Requested-With") == "XMLHttpRequest"
                    or request.headers.get("Accept", "").startswith("application/json")
                    or request.is_json
                )
                if is_ajax:
                    return jsonify({"ok": False, "msg": "Session expired. Please log in again.",
                                    "redirect": url_for("auth.admin_login")}), 401
                return redirect(url_for("auth.admin_login"))
            _killed = _reject_if_compromised("auth.admin_login")
            if _killed:
                return _killed
            _killed = _reject_if_account_deactivated("admin_users", "username", session.get("admin_username"), "auth.admin_login")
            if _killed:
                return _killed
            _killed = _reject_if_password_changed("auth.admin_login")
            if _killed:
                return _killed
            user_role = session.get("admin_role", "admin")
            if user_role not in allowed_roles and user_role != "superadmin":
                log_security_event("access.denied", "Insufficient role for restricted route",
                                   level="ERROR", required="|".join(allowed_roles),
                                   actual_role=session.get("admin_role"),
                                   identifier=session.get("admin_username"))
                sid = session.get("_sid")
                if sid:
                    evaluate_session_risk(
                        sid, session.get("admin_username") or "unknown", "admin",
                        weight=25, event_type="access.denied",
                        reason="Repeated insufficient-role access attempts",
                    )
                return jsonify({"ok": False, "msg": "Insufficient permissions."}), 403
            return f(*args, **kwargs)
        wrapper._hrz_auth_guard = "role"  # read by tests/test_route_auth_guards.py
        return wrapper
    return decorator


# ── API Bearer token guards ───────────────────────────────────────────────────
# A token minted before the account's last password change/reset is dead,
# even if its row somehow survived the DELETE in record_password_change().
# Expects api_tokens aliased `t`. Checks both account tables because an
# HR/manager employee's admin-panel identity shares its id with the
# employees row whose password it actually logs in with.
_TOKEN_NOT_STALE_SQL = (
    "NOT EXISTS (SELECT 1 FROM admin_users pa WHERE pa.username=t.identity "
    "AND pa.password_changed_at > t.created_at) "
    "AND NOT EXISTS (SELECT 1 FROM employees pe WHERE pe.employee_id=t.identity "
    "AND pe.password_changed_at > t.created_at)"
)


def api_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            log_security_event("access.denied", "API request missing Bearer token",
                               level="INFO", required="admin_api")
            return jsonify({"ok": False, "msg": "Unauthorized"}), 401
        # Never log the token or its hash -- it's the literal credential /
        # DB lookup key, and logging it would hand anyone with log access
        # something they could use to fingerprint or replay-correlate it.
        token_hash = _hash_token(auth[7:])
        with _db() as (cursor, _conn):
            cursor.execute("DELETE FROM api_tokens WHERE expires_at < NOW()")
            _conn.commit()
            cursor.execute(
                f"""
                SELECT t.identity, COALESCE(u.is_active, 1), COALESCE(t.mfa_verified, 0)
                FROM api_tokens t
                LEFT JOIN admin_users u ON u.username = t.identity
                WHERE t.token=%s AND t.token_type='admin' AND t.expires_at > NOW()
                  AND {_TOKEN_NOT_STALE_SQL}
                """,  # nosec B608 -- _TOKEN_NOT_STALE_SQL is a fixed module constant
                (token_hash,)
            )
            row = cursor.fetchone()
        if not row:
            log_security_event("access.denied", "API request with invalid or expired admin token",
                               level="WARNING", required="admin_api")
            return jsonify({"ok": False, "msg": "Invalid or expired token"}), 401
        _flask_g.api_token_mfa_verified = bool(row[2])
        # Re-checked on every request (not just at login) so that
        # deactivating an account revokes its existing tokens immediately,
        # instead of leaving them valid until natural expiry.
        if not row[1]:
            log_security_event("access.denied", "API request from deactivated admin account",
                               level="WARNING", required="admin_api", identifier=row[0])
            return jsonify({"ok": False, "msg": "This account has been deactivated. Contact your administrator."}), 403
        _flask_g.api_user = row[0]
        return f(*args, **kwargs)
    wrapper._hrz_auth_guard = "admin_api"  # read by tests/test_route_auth_guards.py
    return wrapper


def api_role_required(*allowed_roles):
    """Session-based role_required's counterpart for Bearer-token API routes.

    api_required alone only proves the token is valid -- every admin API
    token is treated as equally privileged regardless of the issuing
    admin's actual role, so a manager's or soc_analyst's token gets the
    same bulk salary/PII data an admin's token would. Must sit UNDER
    @api_required (i.e. @api_required above, this decorator below) so
    _flask_g.api_user is already set by the time this runs.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            username = getattr(_flask_g, "api_user", None)
            with _db() as (cursor, _conn):
                cursor.execute("SELECT COALESCE(role,'admin') FROM admin_users WHERE username=%s", (username,))
                row = cursor.fetchone()
            actual_role = row[0] if row else None
            if actual_role not in allowed_roles:
                log_security_event(
                    "access.denied", "API token's role insufficient for restricted endpoint",
                    level="ERROR", required="|".join(allowed_roles),
                    actual_role=actual_role, identifier=username,
                )
                return jsonify({"ok": False, "msg": "Insufficient permissions."}), 403
            return f(*args, **kwargs)
        wrapper._hrz_auth_guard = "admin_api_role"  # read by tests/test_route_auth_guards.py
        return wrapper
    return decorator


def employee_api_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            log_security_event("access.denied", "API request missing Bearer token",
                               level="INFO", required="employee_api")
            return jsonify({"ok": False, "msg": "Unauthorized"}), 401
        token_hash = _hash_token(auth[7:])
        with _db() as (cursor, _conn):
            cursor.execute("DELETE FROM api_tokens WHERE expires_at < NOW()")
            _conn.commit()
            cursor.execute(
                "SELECT t.identity FROM api_tokens t WHERE t.token=%s AND t.token_type='employee' "
                f"AND t.expires_at > NOW() AND {_TOKEN_NOT_STALE_SQL}",  # nosec B608 -- fixed module constant
                (token_hash,)
            )
            row = cursor.fetchone()
        if not row:
            log_security_event("access.denied", "API request with invalid or expired employee token",
                               level="WARNING", required="employee_api")
            return jsonify({"ok": False, "msg": "Invalid or expired token"}), 401
        _flask_g.api_emp_id = row[0]
        return f(*args, **kwargs)
    wrapper._hrz_auth_guard = "employee_api"  # read by tests/test_route_auth_guards.py
    return wrapper


def resolve_admin_identity():
    """Session (web) or Bearer admin token (mobile) -- for routes usable
    from both without a route-level decorator, since admin_required
    redirects to a login page rather than returning JSON, which breaks a
    Bearer-token mobile client. Returns the admin username, or None if
    neither proves a logged-in, active admin. Used by blueprints/ai_hrms.py
    and blueprints/email_blast.py.

    Mirrors admin_required's compromised-session check (a session flagged
    mid-lifetime by utils/session_risk.py must not keep working just
    because this helper skips the decorator) -- but only checks, doesn't
    clear()/redirect like the decorator does, since callers here are JSON
    sub-actions, not full page loads.
    """
    if session.get("admin_logged_in"):
        sid = session.get("_sid")
        if sid and is_session_compromised(sid):
            return None
        if _password_changed_since_login():
            return None
        return session.get("admin_username", "admin")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token_hash = _hash_token(auth[7:])
    with _db() as (cursor, _conn):
        cursor.execute(
            f"""
            SELECT t.identity, COALESCE(u.is_active, 1)
            FROM api_tokens t
            LEFT JOIN admin_users u ON u.username = t.identity
            WHERE t.token=%s AND t.token_type='admin' AND t.expires_at > NOW()
              AND {_TOKEN_NOT_STALE_SQL}
            """,  # nosec B608 -- fixed module constant
            (token_hash,)
        )
        row = cursor.fetchone()
    if not row or not row[1]:
        return None
    return row[0]


def resolve_bearer_identity_any():
    """Bearer-token identity for routes usable by EITHER an employee or an
    admin token, with no session fallback -- for API endpoints that accept
    both personas and have no session cookie to fall back to at all (mobile
    clients). Looks the token up against api_tokens for either token_type,
    unlike resolve_admin_identity() above which is admin-only and also
    checks the session first.

    Returns the identity string, or None if the header is missing/
    malformed or the token doesn't match a live, non-expired row. Does NOT
    check an admin_users.is_active flag the way resolve_admin_identity()
    does -- callers needing that guarantee for the admin case should use
    resolve_admin_identity() instead; this is for routes where "any
    unexpired token for either persona" is the actual requirement.
    Extracted from blueprints/ai_hrms.py's _resolve_bearer_identity(),
    which needed the same lookup and now delegates here.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token_hash = _hash_token(auth[7:])
    with _db() as (cursor, _conn):
        cursor.execute(
            "SELECT t.identity FROM api_tokens t WHERE t.token=%s AND t.token_type IN ('employee','admin') "
            f"AND t.expires_at > NOW() AND {_TOKEN_NOT_STALE_SQL}",  # nosec B608 -- fixed module constant
            (token_hash,)
        )
        row = cursor.fetchone()
    return row[0] if row else None


# ── Email Settings 2FA step-up gate ───────────────────────────────────────────
# Same time.time()-in-session idiom as the WebAuthn fingerprint window
# (utils/webauthn_utils.py:_WA_FP_VERIFY_WINDOW_SEC), but NOT single-use/popped
# -- this gate needs to stay valid across several requests (view, edit, reveal
# password) within the window, and the window is refreshed on every authorized
# request so it reads as "15 minutes of inactivity", matching the requirement,
# rather than a fixed 15 minutes from the moment the code was entered.
EMAIL_2FA_WINDOW_SEC = 15 * 60


def email_settings_step_up_valid() -> bool:
    ts = session.get("email_2fa_verified_at", 0)
    return bool(ts) and (time.time() - ts) <= EMAIL_2FA_WINDOW_SEC


def email_settings_step_up_refresh():
    session["email_2fa_verified_at"] = time.time()


def email_settings_step_up_clear():
    session.pop("email_2fa_verified_at", None)


# ── Payout Settings step-up gate ──────────────────────────────────────────────
# Deliberately a SEPARATE gate/session key from Email Settings above, not a
# reuse of it -- bank details are the company's own source-of-funds account
# and the approval step actually authorizes money movement, so completing
# step-up for one sensitive area must not silently unlock the other. Same
# time.time()-in-session window pattern otherwise.
PAYOUT_2FA_WINDOW_SEC = 15 * 60


def payout_settings_step_up_valid() -> bool:
    ts = session.get("payout_2fa_verified_at", 0)
    return bool(ts) and (time.time() - ts) <= PAYOUT_2FA_WINDOW_SEC


def payout_settings_step_up_refresh():
    session["payout_2fa_verified_at"] = time.time()


def payout_settings_step_up_clear():
    session.pop("payout_2fa_verified_at", None)


def require_payout_2fa(f):
    """Gate for Payout Settings routes (company bank details, and approving
    a disbursement run -- i.e. authorizing real money movement) behind a
    recent TOTP step-up. Off by default like require_email_2fa -- set
    REQUIRE_PAYOUT_2FA=true in .env to turn this back on. Kept consistent
    with the rest of the codebase's opt-in-security-flag convention rather
    than defaulting differently, even though this gates a higher-stakes
    action -- see require_email_2fa's own docstring for the same reasoning."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_app.config.get("REQUIRE_PAYOUT_2FA", False):
            return f(*args, **kwargs)
        if not payout_settings_step_up_valid():
            log_security_event(
                "access.denied", "Payout Settings accessed without a valid 2FA step-up",
                level="WARNING", identifier=session.get("admin_username"),
            )
            return jsonify({"ok": False, "msg": "2FA verification required"}), 403
        return f(*args, **kwargs)
    return wrapper


# HR accounts (created/managed via blueprints/admin_views.py's /hr_accounts
# page) log in through the same general /login as admin -- but
# role_required("admin") elsewhere still scopes them to employees/
# attendance/leave/onboarding/performance/tickets/documents, away from
# tenant/system settings, company management, and analytics that a full
# admin session carries.
HR_ROLE = "hr"

# Manager accounts have no standalone creation UI at all (unlike HR's
# /hr_accounts) -- the only way one comes to exist is auto-provisioning an
# employee whose own employees.role is exactly "Manager", mirroring HR's
# own _ensure_hr_admin_account/_finish_employee_login pattern in
# blueprints/auth.py. Scope is intentionally narrower than HR: just the
# company-wide leave/resignation/overtime approval queues (see
# blueprints/leave.py's _LEAVE_APPROVER_ROLES) -- no employees, payroll,
# performance, onboarding, or tickets (blueprints/tickets.py explicitly
# excludes 'manager' -- no per-team ownership concept exists to scope
# those down to). Enforced web-side by app.py's _restrict_manager_role.
MANAGER_ROLE = "manager"


# ── Security Settings hub step-up gate ────────────────────────────────────────
# Same time.time()-in-session step-up pattern as Email Settings, guarding the
# consolidated "Security" tab in Settings (session timeout, audit log, MFA
# status, security posture -- all in one place, per the row-wise hub
# requirement).
#
# This one has NO role restriction -- every admin can open this hub with just
# their own TOTP code.


def security_settings_step_up_clear():
    session.pop("security_settings_2fa_verified_at", None)


def require_email_2fa(f):
    """Gate for Email Settings routes (SMTP config, including a
    reveal-plaintext-password action) behind a recent TOTP step-up --
    see email_settings_step_up_valid() above. On by default (same as the
    three MANDATORY_*_MFA flags in app.py) -- set REQUIRE_EMAIL_2FA=false in
    .env, or app.config["REQUIRE_EMAIL_2FA"]=False in tests, to turn this
    step-up off."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_app.config["REQUIRE_EMAIL_2FA"]:
            return f(*args, **kwargs)
        if not email_settings_step_up_valid():
            log_security_event(
                "access.denied", "Email Settings accessed without a valid 2FA step-up",
                level="WARNING", identifier=session.get("admin_username"),
            )
            return jsonify({"ok": False, "msg": "2FA verification required"}), 403
        return f(*args, **kwargs)
    return wrapper


# ── Object-level authorization (BOLA/IDOR guard) ──────────────────────────────
def enforce_ownership(resource_owner_id, resource_type, resource_id=None):
    """Check whether the current session may access a resource it doesn't own.

    Centralizes the ownership idiom that already existed hand-rolled in
    payroll.py (view_payslip) and documents.py (download_document): admin
    bypass, else the session's own employee_id must equal the resource's
    owning employee_id. Call this AFTER fetching the row (you need to know
    who owns it), not as a pre-request decorator -- most resources here are
    looked up by an opaque row id (document id, payslip period), not by an
    id that itself encodes the owner.

    Every denial logs at ERROR, which utils/alerts.py turns into a real-time
    webhook alert (extensions.py:log_security_event) -- a BOLA probe is never
    silently swallowed, regardless of which route calls this.

    Returns True if access is allowed, False if it should be denied (caller
    decides the response -- redirect, flash, 403 JSON, etc., to match its
    existing route style).
    """
    if session.get("admin_logged_in"):
        return True
    requester = session.get("employee_id")
    if requester and requester == resource_owner_id:
        return True
    log_security_event(
        "access.denied", f"Attempted cross-employee access to {resource_type}",
        level="ERROR", identifier=requester or "anonymous",
        resource_type=resource_type, resource_id=resource_id,
        requested_owner=resource_owner_id,
    )
    return False
