# -*- coding: utf-8 -*-
"""employee_portal: HR chat assistant and device-risk reporting"""
from database import get_db_connection
from extensions import limiter, log_security_event
from flask import jsonify, request, session
from utils.ai_assistant import ask_assistant, build_employee_context
from utils.auth import employee_required
from utils.session_risk import ensure_session_id, evaluate_session_risk
from . import employee_portal_bp


@employee_portal_bp.route("/api/employee/chat", methods=["POST"])
@employee_required
@limiter.limit("6 per minute")
@limiter.limit("40 per hour")
def api_employee_chat():
    """AI chat assistant for employee self-service queries -- see
    utils/ai_assistant.py for the scoping/security model. The client only
    ever sends free text; the employee's own data is always re-fetched
    server-side from the authenticated session's employee_id, never
    accepted from the request body."""
    emp_id = session["employee_id"]
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")
    history = payload.get("history", [])
    if not isinstance(message, str):
        return jsonify({"ok": False, "msg": "Invalid message."}), 400

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    context = build_employee_context(cursor, emp_id)
    cursor.close()
    db.close()

    ok, reply = ask_assistant(context, message, history if isinstance(history, list) else [], emp_id=emp_id)
    return jsonify({"ok": ok, "reply": reply})


# A locally-running device-posture agent is the only thing that can actually
# see Wi-Fi encryption type / ARP state / DNS config -- no web page or web
# server can (see utils/session_risk.py's module docstring). This endpoint
# receives whatever score that agent already computed, relayed by the
# browser over the user's own authenticated session -- it never computes a
# risk score itself, and there is no code path in this app for one.
_DEVICE_RISK_KILL_WEIGHT = 100  # comfortably exceeds SESSION_RISK_THRESHOLD (default 50)
# regardless of how that env var is configured, so one
# qualifying report always crosses the kill threshold.


@employee_portal_bp.route("/api/employee/device_risk", methods=["POST"])
@employee_required
@limiter.limit("20 per minute")
def api_employee_device_risk():
    """Relay endpoint for a native device-posture agent's Wi-Fi/network risk
    score (see the docstring above `_DEVICE_RISK_KILL_WEIGHT`). A score over
    60 feeds the existing session-risk kill switch (utils/session_risk.py)
    instead of a new mechanism: evaluate_session_risk() marks this session
    compromised, employee_required's _reject_if_compromised() then rejects
    this session's very next request on ANY route and clears it -- that's
    the "block the UI / terminate the session" requirement.
    log_security_event() below is "ship to Admin Security Logs" -- the same
    structured-log + webhook-alert pipeline every other security event in
    this app already uses, not a separate logging path."""
    payload = request.get_json(silent=True) or {}
    try:
        risk_score = int(payload.get("risk_score", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "risk_score must be an integer"}), 400
    risk_score = max(0, min(risk_score, 100))

    threat_vectors = payload.get("threat_vectors")
    if not isinstance(threat_vectors, list):
        threat_vectors = []
    threat_vectors = [str(v)[:60] for v in threat_vectors][:10]

    emp_id = session["employee_id"]
    sid = ensure_session_id(session)
    over_threshold = risk_score > 60

    log_security_event(
        "device.wifi_risk_reported",
        "Device posture agent reported a Wi-Fi/network risk score",
        level="ERROR" if over_threshold else "INFO",
        identifier=emp_id, risk_score=str(risk_score),
        threat_vectors=",".join(threat_vectors) or "none",
    )

    if over_threshold:
        evaluate_session_risk(
            sid, emp_id, "employee",
            _DEVICE_RISK_KILL_WEIGHT,
            "device.wifi_risk_block",
            f"Wi-Fi risk score {risk_score} exceeded 60 ({', '.join(threat_vectors) or 'unspecified threat'})",
        )
        return jsonify({"ok": True, "blocked": True,
                        "msg": "Device risk too high -- this session is being terminated."})

    return jsonify({"ok": True, "blocked": False})
