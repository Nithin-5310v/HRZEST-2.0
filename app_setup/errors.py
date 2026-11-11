# -*- coding: utf-8 -*-
"""Error handlers and admin alerting on unhandled errors.

Split out of app.py; registered on the shared Flask app at import time.
app.py imports the app_setup modules in a fixed order because Flask runs
before_request hooks in registration order (after_request in reverse).
"""
import datetime
import hashlib
import html as _html
import threading
import time
import traceback as _traceback
import utils.waf as waf
from database import DatabaseUnavailable
from extensions import app, app_log, log_security_event
from flask import flash, jsonify, make_response, redirect, request
from utils.email_utils import get_admin_emails, get_email_config, send_email_async
from utils.helpers import _error_page, _safe_referrer_redirect, wants_json_response
from werkzeug.exceptions import HTTPException


# ---------------- ERROR ALERTING (malfunction detection) ----------------
# The catch-all exception handler below tells users "the team has been
# notified" -- this is what actually makes that true. Emails admins on
# unhandled errors, deduped by error signature so one hot failing endpoint
# can't flood inboxes or the email_queue table.
_error_alert_cache = {}
_error_alert_lock = threading.Lock()
_ERROR_ALERT_COOLDOWN = 900  # 15 min -- same error signature won't re-alert sooner


def _alert_on_error(tb_text, context=""):
    """Best-effort: any failure here is swallowed so alerting can never
    mask or crash on top of the original error being reported."""
    try:
        # Dedup key = exception type + the line that actually raised it, not
        # the full traceback (which varies request-to-request -- different
        # IDs, line numbers in called code, etc. would defeat deduping).
        last_line = tb_text.strip().splitlines()[-1] if tb_text.strip() else "unknown"
        sig = hashlib.sha256(last_line.encode()).hexdigest()[:16]
        now = time.time()
        with _error_alert_lock:
            last_sent = _error_alert_cache.get(sig)
            if last_sent and now - last_sent < _ERROR_ALERT_COOLDOWN:
                return
            _error_alert_cache[sig] = now
            if len(_error_alert_cache) > 500:  # cap unbounded growth
                _error_alert_cache.clear()

        cfg = get_email_config()
        if not cfg:
            return
        admins = get_admin_emails()
        if not admins:
            return

        body = (
            "<pre style='white-space:pre-wrap;font-family:monospace;font-size:13px'>"
            f"Time: {datetime.datetime.now().isoformat()}\n"
            f"Route: {_html.escape(context or 'unknown')}\n"
            f"Method: {_html.escape(request.method if request else 'n/a')}\n"
            f"Remote IP: {_html.escape(request.remote_addr or '') if request else 'n/a'}\n\n"
            f"{_html.escape(tb_text)}</pre>"
        )
        subject = f"⚠️ Application error -- {context or 'unknown route'}"
        for admin_email in admins:
            send_email_async(admin_email, subject, body, cfg)
    except Exception as _alert_err:
        app_log.error("Failed to send error alert email: %s", _alert_err)


@app.errorhandler(429)
def rate_limit_exceeded(e):
    """Flask-Limiter raises this (a werkzeug HTTPException) whenever any
    @limiter.limit(...) threshold is crossed. Distinct from _check_login_lockout's
    DB-backed account lockout (utils/auth.py) -- this is the generic per-route
    rate ceiling. Logs every breach (feeds the same auto-ban counter WAF
    blocks use -- see utils/waf.py) instead of letting it pass through as a
    silent, unlogged 429."""
    waf.record_breach_and_maybe_ban(request.remote_addr, "Repeated rate-limit breaches")
    log_security_event("ratelimit.exceeded", f"Rate limit exceeded: {getattr(e, 'description', '')}",
                       level="WARNING")
    if wants_json_response():
        return jsonify({"ok": False, "msg": "Too many requests. Please slow down and try again shortly."}), 429
    return _error_page(429, "⏳", "Too Many Requests",
                       "You've made too many requests in a short period.",
                       "Please wait a moment before trying again.")


@app.errorhandler(DatabaseUnavailable)
def database_unavailable(e):
    """database.py raises DatabaseUnavailable instead of silently switching
    to a local SQLite file when PostgreSQL can't be reached (outside
    APP_ENV=development). Answer 503 so the outage is visible and retryable,
    and alert (CRITICAL security events go to the alert webhook)."""
    log_security_event("db.unavailable", "PostgreSQL unavailable -- request answered 503", level="CRITICAL")
    if wants_json_response():
        resp = jsonify({"ok": False, "msg": "Service temporarily unavailable. Please try again shortly."})
    else:
        resp = _error_page(503, "🛠️", "Service Temporarily Unavailable",
                           "We can't reach the database right now.",
                           "Please try again in a minute.")
    resp = make_response(resp)
    resp.status_code = 503
    resp.headers["Retry-After"] = "30"
    return resp


@app.errorhandler(404)
def not_found(e):
    return _error_page(404, "🔍", "Page Not Found",
                       "The page you're looking for doesn't exist or has been moved.",
                       "Check the URL or use one of the links below to get back on track.")


@app.errorhandler(403)
def forbidden(e):
    return _error_page(403, "🔒", "Access Denied",
                       "You don't have permission to access this page.",
                       "Please log in with the right account or contact your administrator.")


@app.errorhandler(413)
def payload_too_large(e):
    """An upload over MAX_CONTENT_LENGTH: back to the page it came from with
    a message, instead of a bare "Request Entity Too Large" page."""
    max_mb = (app.config.get("MAX_CONTENT_LENGTH") or 0) // (1024 * 1024)
    msg = (f"That file is too large to upload (the limit is {max_mb} MB). Please choose a smaller file."
           if max_mb else "That file is too large to upload. Please choose a smaller file.")
    if wants_json_response():
        return jsonify({"ok": False, "msg": msg}), 413
    flash(msg, "error")
    return redirect(_safe_referrer_redirect(request.referrer or "", "/"))


@app.errorhandler(500)
def internal_error(e):
    tb = _traceback.format_exc()
    app_log.error("500 error: %s", tb.replace('\n', '\\n'))
    _alert_on_error(tb, context=request.path if request else "")
    # WARNING, not ERROR: _alert_on_error above already alerts. This row is for
    # log analysis (error type only -- never the traceback).
    log_security_event("app.server_error", "Unhandled server error", level="WARNING",
                       error_type=type(getattr(e, "original_exception", None) or e).__name__)
    return _error_page(500, "⚙️", "Internal Server Error",
                       "Something went wrong on our end. The error has been logged.",
                       "Please try again in a moment or contact your administrator.")


@app.errorhandler(Exception)
def unhandled_exception(e):
    if isinstance(e, HTTPException):
        return _error_page(e.code, "⚠️", e.name,
                           "The page you requested could not be processed.",
                           "Use the buttons below to navigate back.")
    tb = _traceback.format_exc()
    app_log.error("Unhandled exception: %s", tb.replace('\n', '\\n'))
    _alert_on_error(tb, context=request.path if request else "")
    log_security_event("app.server_error", "Unhandled server error", level="WARNING",
                       error_type=type(e).__name__)
    return _error_page(500, "⚙️", "Internal Server Error",
                       "An unexpected error occurred. The team has been notified.",
                       "Please try again or contact your administrator.")
