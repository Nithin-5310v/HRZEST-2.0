"""The owner admin's app token on the employee endpoints (utils/auth.py's
employee_api_required -> _owner_token_as_own_employee) and GET /api/admin/me:
what the app's "Me" tab checks in with. Only the owner role maps, only to an
active linked employee (utils/own_profile.py), and only with the same checks
api_required makes on an admin token."""
import secrets

import pytest
from flask import g, jsonify

from utils.auth import _hash_token, employee_api_required


def _token(db_engine, username, token_type="admin", expires="NOW() + INTERVAL '1 hour'",
           created="NOW()"):
    token = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (token, token_type, identity, expires_at, created_at) "
        f"VALUES (%s, %s, %s, {expires}, {created})",
        (_hash_token(token), token_type, username),
    )
    db_engine.commit()
    cur.close()
    return token


def _link(db_engine, username, employee_id, role=None):
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET linked_employee_id=%s, role=%s WHERE username=%s",
                (employee_id, role, username))
    db_engine.commit()
    cur.close()


def _as_employee(token):
    """employee_api_required on its own: the employee_id it hands the route
    (g.api_emp_id), or the refusal's status code."""
    from app import app

    @employee_api_required
    def probe():
        return jsonify({"emp_id": g.api_emp_id})

    with app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
        resp = app.make_response(probe())
    return resp.get_json().get("emp_id") if resp.status_code == 200 else resp.status_code


@pytest.fixture
def owner(db_engine, seed_admin, seed_employee):
    """seed_admin (no role = the owner 'admin') linked to seed_employee."""
    _link(db_engine, seed_admin["username"], seed_employee["employee_id"])
    yield seed_admin
    cur = db_engine.cursor()
    cur.execute("DELETE FROM api_tokens WHERE identity=%s", (seed_admin["username"],))
    db_engine.commit()
    cur.close()


class TestOwnerTokenOnEmployeeEndpoints:
    def test_owner_token_acts_as_the_linked_employee(self, db_engine, owner, seed_employee):
        assert _as_employee(_token(db_engine, owner["username"])) == seed_employee["employee_id"]

    def test_owner_token_reads_its_own_attendance(self, client, db_engine, owner):
        token = _token(db_engine, owner["username"])
        resp = client.get("/api/employee/attendance", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True

    def test_an_employee_token_still_works(self, db_engine, seed_employee):
        token = _token(db_engine, seed_employee["employee_id"], token_type="employee")
        assert _as_employee(token) == seed_employee["employee_id"]

    def test_unlinked_owner_token_is_refused(self, db_engine, seed_admin):
        assert _as_employee(_token(db_engine, seed_admin["username"])) == 401

    @pytest.mark.parametrize("role", ["hr", "manager", "soc_analyst"])
    def test_other_admin_roles_never_map(self, db_engine, seed_admin, seed_employee, role):
        _link(db_engine, seed_admin["username"], seed_employee["employee_id"], role=role)
        assert _as_employee(_token(db_engine, seed_admin["username"])) == 401

    def test_deactivated_linked_employee_is_refused(self, db_engine, owner, seed_employee):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET is_active=0 WHERE employee_id=%s", (seed_employee["employee_id"],))
        db_engine.commit()
        cur.close()
        assert _as_employee(_token(db_engine, owner["username"])) == 401

    def test_deactivated_owner_account_is_refused(self, db_engine, owner):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username=%s", (owner["username"],))
        db_engine.commit()
        cur.close()
        assert _as_employee(_token(db_engine, owner["username"])) == 401

    def test_expired_owner_token_is_refused(self, db_engine, owner):
        token = _token(db_engine, owner["username"], expires="NOW() - INTERVAL '1 minute'",
                       created="NOW() - INTERVAL '2 hours'")
        assert _as_employee(token) == 401

    def test_token_from_before_a_password_change_is_refused(self, db_engine, owner):
        token = _token(db_engine, owner["username"], created="NOW() - INTERVAL '1 hour'")
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET password_changed_at=NOW() WHERE username=%s", (owner["username"],))
        db_engine.commit()
        cur.close()
        assert _as_employee(token) == 401

    def test_linked_employee_past_last_working_day_is_signed_out(self, client, db_engine, owner, seed_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason, status) "
            "VALUES (%s, CURRENT_DATE - 2, 'x', 'Accepted')",
            (seed_employee["employee_id"],),
        )
        db_engine.commit()
        cur.close()
        token = _token(db_engine, owner["username"])
        resp = client.get("/api/employee/attendance", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401 and resp.get_json().get("access_ended") is True


class TestAdminMe:
    def test_owner_with_a_profile_gets_its_id_and_name(self, client, db_engine, owner, seed_employee):
        token = _token(db_engine, owner["username"])
        body = client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"}).get_json()
        assert body == {"ok": True, "linked_employee_id": seed_employee["employee_id"],
                        "name": seed_employee["name"]}

    def test_owner_without_a_profile_gets_null(self, client, db_engine, seed_admin):
        token = _token(db_engine, seed_admin["username"])
        body = client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"}).get_json()
        assert body["ok"] is True and body["linked_employee_id"] is None

    def test_hr_gets_null_even_if_a_link_is_set(self, client, db_engine, seed_admin, seed_employee):
        _link(db_engine, seed_admin["username"], seed_employee["employee_id"], role="hr")
        token = _token(db_engine, seed_admin["username"])
        body = client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"}).get_json()
        assert body["linked_employee_id"] is None

    def test_needs_an_admin_token(self, client, db_engine, seed_employee):
        token = _token(db_engine, seed_employee["employee_id"], token_type="employee")
        assert client.get("/api/admin/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
