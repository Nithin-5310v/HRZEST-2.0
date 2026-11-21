# -*- coding: utf-8 -*-
"""Your own signed-in sessions (utils/session_registry.py): list them, and end
one you do not recognise or every one but this. Admin-side and employee
sessions alike; each person only ever sees and ends their own.
"""
from flask import Blueprint, jsonify, request, session

from extensions import limiter, log_security_event
from utils import session_registry
from utils.auth import any_user_required

my_sessions_bp = Blueprint("my_sessions", __name__)


def _me():
    if session.get("admin_logged_in"):
        return session.get("admin_username"), "admin"
    return session.get("employee_id"), "employee"


@my_sessions_bp.route("/api/my/sessions", methods=["GET"])
@any_user_required
@limiter.limit("30 per minute")
def my_sessions():
    identifier, kind = _me()
    return jsonify({"ok": True, "sessions": session_registry.list_for(identifier, kind, session.get("_sid"))})


@my_sessions_bp.route("/api/my/sessions/end", methods=["POST"])
@any_user_required
@limiter.limit("10 per minute")
def end_my_sessions():
    identifier, kind = _me()
    data = request.get_json(silent=True) or {}
    ref = str(data.get("ref") or "").strip().lower()[:12]
    all_others = data.get("all_others") is True
    if not ref and not all_others:
        return jsonify({"ok": False, "msg": "Say which session to end."}), 400
    ended, problem = session_registry.end_for(identifier, kind, session.get("_sid"), ref=ref or None, all_others=all_others)
    if problem == "current":
        return jsonify({"ok": False, "msg": "That is this session: use Sign out to end it."}), 400
    if ref and not ended:
        return jsonify({"ok": False, "msg": "That session has already ended."}), 404
    if ended:
        log_security_event("session.ended_by_user", "The account holder ended their own session(s) from another one",
                           level="INFO", identifier=identifier, attempt_type=kind, count=ended,
                           scope="all others" if all_others else "one")
    return jsonify({"ok": True, "ended": ended})
