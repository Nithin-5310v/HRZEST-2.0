# -*- coding: utf-8 -*-
"""Blueprint for AI-powered HRMS features (Helpdesk, Interview Evaluation, Attrition Analytics)."""

from flask import Blueprint, request, jsonify, session
from utils.ai_helpdesk import process_helpdesk_query
from utils.ai_interview_evaluator import evaluate_interview_notes
from utils.ai_attrition_analytics import compute_attrition_and_burnout_analytics
from utils.auth import resolve_admin_identity, resolve_bearer_identity_any, session_account_ok

ai_hrms_bp = Blueprint("ai_hrms", __name__)

# Bearer-token counterpart of the session.get(...) check below -- the
# helpdesk is usable by both employees and admins (no single
# api_required/employee_api_required decorator covers both personas), so
# this looks the token up against api_tokens for either type. Mobile has no
# Flask session cookie at all, so without this every mobile helpdesk
# request silently fell through to the client's canned error-fallback text
# instead of a real AI answer. Moved to utils/auth.py so
# blueprints/attendance.py's api_breaks() can share the same real
# validation instead of a bare "starts with Bearer " check.
_resolve_bearer_identity = resolve_bearer_identity_any


@ai_hrms_bp.route("/api/ai/hr-helpdesk", methods=["POST"])
def api_hr_helpdesk():
    """API Endpoint: Conversational HR Helpdesk Q&A with automatic ticket escalation."""
    session_identity = (session.get("employee_id") or session.get("admin_username")) if session_account_ok() else None
    emp_id = session_identity or _resolve_bearer_identity()
    if not emp_id:
        return jsonify({"ok": False, "msg": "Login required."}), 401

    data = request.get_json(silent=True) or {}
    query = (data.get("query") or data.get("message") or "").strip()

    if not query:
        return jsonify({"ok": False, "msg": "Query text required."}), 400

    result = process_helpdesk_query(emp_id, query)
    return jsonify({"ok": True, "data": result})


@ai_hrms_bp.route("/api/ai/evaluate-interview", methods=["POST"])
def api_evaluate_interview():
    """API Endpoint: Synthesize interviewer notes into structured scorecard & sentiment analysis."""
    if not resolve_admin_identity():
        return jsonify({"ok": False, "msg": "Unauthorized access."}), 401
    data = request.get_json(silent=True) or {}
    candidate_name = data.get("candidate_name", "Candidate")
    position = data.get("position", "Software Engineer")
    notes = data.get("notes", "")

    if not notes:
        return jsonify({"ok": False, "msg": "Interviewer notes required."}), 400

    evaluation = evaluate_interview_notes(candidate_name, position, notes)
    return jsonify({"ok": True, "evaluation": evaluation})


@ai_hrms_bp.route("/api/ai/attrition-analytics")
def api_attrition_analytics():
    """API Endpoint: Predict turnover trends and burnout risk indicators."""
    if not resolve_admin_identity():
        return jsonify({"ok": False, "msg": "Unauthorized access."}), 401
    active_cid = session.get("active_company_id")
    analytics = compute_attrition_and_burnout_analytics(company_id=active_cid)
    return jsonify({"ok": True, "analytics": analytics})
