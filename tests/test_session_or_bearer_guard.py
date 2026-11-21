"""Security audit P2 (2026-11-20): routes that take a session OR a Bearer
token use one standard guard, utils.auth.session_or_bearer_required.

The inline checks it replaced had drifted. /api/breaks accepted any open
session without the per-request checks, so a session the kill switch had
flagged, or whose account had been deactivated, kept working there; and
resolve_bearer_identity_any() never checked that an admin account was still
active.
"""
import pytest

from extensions import app as flask_app
from tests.test_route_auth_guards import _is_guarded

FLAGGED_SID = "audit-p2-flagged-sid"

GUARDED_ENDPOINTS = [
    "attendance.api_breaks",
    "ai_hrms.api_hr_helpdesk",
    "ai_hrms.api_evaluate_interview",
    "ai_hrms.api_attrition_analytics",
    "email_blast.api_email_blast",
    "employees.generate_emp_id",
    "employee_portal.api_employee_portal",
    "employee_portal.api_employee_qr_face_checkin",
    "auth.get_employee_webauthn_credential",
]


def _employee_session(client, emp_id, sid=None):
    with client.session_transaction() as sess:
        sess["employee_id"] = emp_id
        if sid:
            sess["_sid"] = sid


def _employee_token(client, seed_employee):
    return client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    }).get_json()["token"]


def _admin_token(client, seed_admin):
    body = client.post("/api/login", json={"username": seed_admin["username"],
                                           "password": seed_admin["password"]}).get_json()
    return body.get("token")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def flagged_sid(db_engine):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO session_risk (sid, identifier, attempt_type, score, status) "
                "VALUES (%s,'TST001','employee',100,'compromised') ON CONFLICT (sid) DO UPDATE "
                "SET status='compromised'", (FLAGGED_SID,))
    yield FLAGGED_SID
    cur.execute("DELETE FROM session_risk WHERE sid=%s", (FLAGGED_SID,))
    cur.close()


@pytest.mark.parametrize("endpoint", GUARDED_ENDPOINTS)
def test_routes_carry_the_standard_guard(endpoint):
    assert _is_guarded(flask_app.view_functions[endpoint])


class TestApiBreaks:
    def test_no_credentials_is_401(self, client):
        assert client.get("/api/breaks").status_code == 401

    def test_made_up_bearer_is_401(self, client):
        assert client.get("/api/breaks", headers=_bearer("not-a-real-token")).status_code == 401

    def test_healthy_employee_session_is_allowed(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        assert client.get("/api/breaks").status_code == 200

    def test_kill_switch_flagged_session_is_refused(self, client, seed_employee, flagged_sid):
        """The audit's finding: /api/breaks skipped session_account_ok()."""
        _employee_session(client, seed_employee["employee_id"], sid=flagged_sid)
        assert client.get("/api/breaks").status_code == 401

    def test_deactivated_employee_session_is_refused(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee["employee_id"])
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET is_active=0 WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()
        assert client.get("/api/breaks").status_code == 401

    def test_live_employee_token_is_allowed(self, client, seed_employee):
        token = _employee_token(client, seed_employee)
        assert client.get("/api/breaks", headers=_bearer(token)).status_code == 200

    def test_deactivated_employee_token_is_refused(self, client, seed_employee, db_engine):
        token = _employee_token(client, seed_employee)
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET is_active=0 WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()
        assert client.get("/api/breaks", headers=_bearer(token)).status_code == 401

    def test_deactivated_admin_token_is_refused(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        if not token:
            pytest.skip("admin API login needs MFA here")
        assert client.get("/api/breaks", headers=_bearer(token)).status_code == 200
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username=%s", (seed_admin["username"],))
        cur.close()
        assert client.get("/api/breaks", headers=_bearer(token)).status_code == 401


class TestOtherRoutes:
    def test_flagged_session_cannot_start_a_check_in(self, client, seed_employee, flagged_sid):
        _employee_session(client, seed_employee["employee_id"], sid=flagged_sid)
        resp = client.post("/api/employee/qr-face-checkin", data={"auth_combo": "qr_face"})
        assert resp.status_code == 401
        assert resp.get_json()["msg"] == "Please sign in to mark attendance."

    def test_flagged_session_cannot_read_the_portal(self, client, seed_employee, flagged_sid):
        _employee_session(client, seed_employee["employee_id"], sid=flagged_sid)
        assert client.get("/api/employee/portal").status_code == 401

    def test_portal_with_employee_session_works(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        assert client.get("/api/employee/portal").status_code == 200

    def test_employee_session_cannot_use_admin_only_ai_routes(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        assert client.get("/api/ai/attrition-analytics").status_code == 401
        assert client.post("/api/ai/evaluate-interview", json={"notes": "x"}).status_code == 401

    def test_employee_token_cannot_read_another_employees_credential(self, client, seed_employee):
        token = _employee_token(client, seed_employee)
        resp = client.get("/api/employee/SOMEONE-ELSE/webauthn-credential", headers=_bearer(token))
        assert resp.status_code == 403

    def test_employee_session_cannot_generate_employee_ids(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        assert client.get("/api/generate_emp_id").status_code == 401
