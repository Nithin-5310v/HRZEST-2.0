# -*- coding: utf-8 -*-
"""Records a security event when a signed-in company session changes browser
or network mid-session (utils/session_context.py). Imported after
app_setup.security in app.py, so banned addresses and refused sessions never
get this far. Never blocks or ends a request.
"""
from flask import request, session

from extensions import app, app_log, log_security_event
from utils import session_context

_SKIP_PREFIXES = ("/static/", "/socperson", "/healthz", "/api/health", "/favicon.ico")


@app.before_request
def _watch_session_context():
    if request.path.startswith(_SKIP_PREFIXES):
        return
    if not session.get("_sid") or not (session.get("admin_logged_in") or session.get("employee_id")):
        return
    try:
        changes = session_context.observe(session, request.remote_addr, request.headers.get("User-Agent", ""))
    except Exception as exc:     # a watcher must never break the request it watches
        app_log.warning("session context check failed: %s", exc)
        return
    if not changes:
        return
    identifier = session.get("admin_username") or session.get("employee_id")
    kind = "admin" if session.get("admin_logged_in") else "employee"
    ref = session_context.session_ref(session["_sid"])
    for what, before, after, extra in changes:
        if what == "browser":
            log_security_event("session.browser_changed", "A signed-in session is now used from a different browser",
                               level="WARNING", identifier=identifier, attempt_type=kind, session_ref=ref,
                               from_browser=before, to_browser=after, **extra)
        else:
            log_security_event("session.network_changed", "A signed-in session is now used from a different network",
                               level="INFO", identifier=identifier, attempt_type=kind, session_ref=ref,
                               from_network=before, to_network=after, **extra)
