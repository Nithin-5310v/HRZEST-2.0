"""Automated Pytest Suite for AI HRMS Portal Services & Endpoints."""

from utils.ai_helpdesk import process_helpdesk_query
from utils.ai_interview_evaluator import evaluate_interview_notes
from utils.ai_attrition_analytics import compute_attrition_and_burnout_analytics


def test_ai_helpdesk_query_and_fallback(db_engine, seed_employee):
    emp_id = seed_employee["employee_id"]

    # Normal policy query
    res = process_helpdesk_query(emp_id, "What is the paid leave policy?")
    assert res["answer"] is not None
    assert res["escalated"] is False

    # High complexity / escalation query
    res_esc = process_helpdesk_query(emp_id, "I have an urgent salary dispute and want to speak to a human manager")
    assert res_esc["escalated"] is True
    assert res_esc["ticket_id"] is not None


def test_interview_evaluator_unit():
    notes = "Candidate showed excellent technical skills in Python and SQL, clear communication, and impressive problem solving."
    eval_report = evaluate_interview_notes("Jane Smith", "Senior Developer", notes)

    assert eval_report["overall_rating"] >= 7.5
    assert "positive" in eval_report["sentiment_analysis"]
    assert eval_report["overall_recommendation"] in ("Hire", "Strong Hire")


def test_attrition_analytics_unit(db_engine):
    analytics = compute_attrition_and_burnout_analytics()
    assert "summary" in analytics
    assert "overall_turnover_index" in analytics["summary"]
    assert isinstance(analytics["all_employee_analytics"], list)


def test_ai_hrms_api_endpoints(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"

    # Test /api/ai/hr-helpdesk
    res = client.post("/api/ai/hr-helpdesk", json={"query": "How many sick days do I get?"})
    assert res.status_code == 200
    assert res.get_json()["data"]["answer"] is not None

    # Test /api/ai/evaluate-interview
    res = client.post("/api/ai/evaluate-interview", json={
        "candidate_name": "Alex Dev",
        "position": "Python Engineer",
        "notes": "Great technical skills and clear communication."
    })
    assert res.status_code == 200
    assert res.get_json()["evaluation"]["overall_rating"] >= 7.0

    # Test /api/ai/attrition-analytics
    res = client.get("/api/ai/attrition-analytics")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_hr_helpdesk_bearer_token_employee(client, seed_employee):
    """Mobile has no Flask session cookie -- the helpdesk must also accept
    an employee Bearer token (this is what mobile/src/components/common/
    AiHelpdeskModal.js actually calls)."""
    token = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    }).get_json()["token"]

    res = client.post("/api/ai/hr-helpdesk", json={"query": "How many sick days do I get?"},
                      headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.get_json()["data"]["answer"] is not None


def test_hr_helpdesk_bearer_token_admin(client, seed_admin):
    """Same Bearer support for an admin token (mobile/src/components/
    AiChatModal.js, used from AdminDashboard)."""
    token = client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    }).get_json()["token"]

    res = client.post("/api/ai/hr-helpdesk", json={"query": "What is the payroll schedule?"},
                      headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.get_json()["data"]["answer"] is not None


def test_hr_helpdesk_rejects_missing_auth(client):
    res = client.post("/api/ai/hr-helpdesk", json={"query": "Anything?"})
    assert res.status_code == 401


def _admin_bearer_token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    }).get_json()["token"]


def test_recruitment_routes_accept_admin_bearer_token(client, seed_admin):
    """evaluate-interview/attrition-analytics were admin_required
    (session-only, redirects on failure) -- mobile needs these reachable
    with a Bearer token instead."""
    token = _admin_bearer_token(client, seed_admin)
    auth = {"Authorization": f"Bearer {token}"}

    res = client.post("/api/ai/evaluate-interview", json={
        "candidate_name": "Alex Dev", "position": "Engineer", "notes": "Strong technical performance.",
    }, headers=auth)
    assert res.status_code == 200 and res.get_json()["ok"] is True

    res = client.get("/api/ai/attrition-analytics", headers=auth)
    assert res.status_code == 200 and res.get_json()["ok"] is True


def test_recruitment_routes_reject_missing_auth(client):
    assert client.post("/api/ai/evaluate-interview", json={"notes": "x"}).status_code == 401
    assert client.get("/api/ai/attrition-analytics").status_code == 401


def test_recruitment_routes_reject_employee_bearer_token(client, seed_employee):
    token = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    }).get_json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/ai/attrition-analytics", headers=auth).status_code == 401
