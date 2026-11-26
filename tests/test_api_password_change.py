"""The app's own-password change APIs: POST /api/admin/password (admin
Bearer token, blueprints/auth.py) and POST /api/employee/change-password
(employee Bearer token, blueprints/employee_portal/portal.py) -- the Bearer
twins of the web Settings and portal password forms."""
import hashlib
import secrets

import pytest

from utils.auth import check_password_hash, generate_password_hash

NEW_PASSWORD = "Violet-Harbor-Lamp-42"


def _bearer(db_engine, identity, token_type):
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s, %s, %s, NOW() + INTERVAL '1 hour')",
        (identity, hashlib.sha256(raw.encode()).hexdigest(), token_type),
    )
    cur.close()
    return {"Authorization": f"Bearer {raw}"}


def _stored_hash(db_engine, table, id_column, id_value):
    cur = db_engine.cursor()
    cur.execute(f"SELECT password FROM {table} WHERE {id_column}=%s", (id_value,))  # nosec B608 -- test literals
    row = cur.fetchone()
    cur.close()
    return row[0]


def _token_count(db_engine, identity):
    cur = db_engine.cursor()
    cur.execute("SELECT COUNT(*) FROM api_tokens WHERE identity=%s", (identity,))
    n = cur.fetchone()[0]
    cur.close()
    return n


# ── POST /api/admin/password ─────────────────────────────────────────────────

class TestAdminPasswordApi:
    URL = "/api/admin/password"

    def _body(self, current="Test@1234", new=NEW_PASSWORD, confirm=None):
        return {"current_password": current, "new_password": new,
                "confirm_password": new if confirm is None else confirm}

    def test_needs_a_bearer_token(self, client):
        assert client.post(self.URL, json=self._body()).status_code == 401

    def test_an_employee_token_is_not_enough(self, client, db_engine, seed_employee):
        headers = _bearer(db_engine, seed_employee["employee_id"], "employee")
        assert client.post(self.URL, json=self._body(), headers=headers).status_code == 401

    def test_changes_the_password_and_revokes_every_token(self, client, db_engine, seed_admin):
        headers = _bearer(db_engine, seed_admin["username"], "admin")
        _bearer(db_engine, seed_admin["username"], "admin")          # another signed-in device
        resp = client.post(self.URL, json=self._body(), headers=headers)
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert check_password_hash(_stored_hash(db_engine, "admin_users", "username", "test_admin"), NEW_PASSWORD)
        assert _token_count(db_engine, seed_admin["username"]) == 0
        # The token that made the change is gone too.
        assert client.post(self.URL, json=self._body(current=NEW_PASSWORD), headers=headers).status_code == 401

    def test_only_the_token_owner_is_changed(self, client, db_engine, seed_admin):
        """There is no username in the request: a caller can't aim the change
        at another account."""
        cur = db_engine.cursor()
        cur.execute("INSERT INTO admin_users (username, password, email) VALUES (%s,%s,%s)",
                    ("route_other_admin", generate_password_hash("Test@1234"), "other@test.local"))
        cur.close()
        before = _stored_hash(db_engine, "admin_users", "username", "route_other_admin")
        headers = _bearer(db_engine, seed_admin["username"], "admin")
        body = dict(self._body(), username="route_other_admin")
        assert client.post(self.URL, json=body, headers=headers).status_code == 200
        assert _stored_hash(db_engine, "admin_users", "username", "route_other_admin") == before
        assert check_password_hash(_stored_hash(db_engine, "admin_users", "username", "test_admin"), NEW_PASSWORD)

    @pytest.mark.parametrize("body,needle", [
        ({"current_password": "Test@1234", "new_password": NEW_PASSWORD, "confirm_password": "different-one-123"},
         "must match"),
        ({"current_password": "Test@1234", "new_password": "", "confirm_password": ""}, "must match"),
        ({"current_password": "Test@1234", "new_password": "short-1", "confirm_password": "short-1"},
         "at least 12"),
        ({"current_password": "Test@1234", "new_password": "test_admin-is-me-2026",
          "confirm_password": "test_admin-is-me-2026"}, "must not contain"),
        ({"current_password": "wrong-current-pass", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
         "incorrect"),
    ])
    def test_refusals_leave_the_password_alone(self, client, db_engine, seed_admin, body, needle):
        before = _stored_hash(db_engine, "admin_users", "username", "test_admin")
        headers = _bearer(db_engine, seed_admin["username"], "admin")
        resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == 400
        assert needle in resp.get_json()["msg"]
        assert _stored_hash(db_engine, "admin_users", "username", "test_admin") == before
        assert _token_count(db_engine, seed_admin["username"]) == 1


# ── POST /api/employee/change-password ───────────────────────────────────────

class TestEmployeePasswordApi:
    URL = "/api/employee/change-password"

    def _body(self, current="EmpPass@1", new=NEW_PASSWORD):
        return {"current_password": current, "new_password": new}

    def test_needs_an_employee_token(self, client):
        assert client.post(self.URL, json=self._body()).status_code == 401
        assert client.post(self.URL, json=self._body(),
                           headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401

    def test_changes_the_password_and_revokes_every_token(self, client, db_engine, seed_employee):
        headers = _bearer(db_engine, seed_employee["employee_id"], "employee")
        resp = client.post(self.URL, json=self._body(), headers=headers)
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert check_password_hash(_stored_hash(db_engine, "employees", "employee_id", "TST001"), NEW_PASSWORD)
        assert _token_count(db_engine, seed_employee["employee_id"]) == 0
        assert client.post(self.URL, json=self._body(current=NEW_PASSWORD), headers=headers).status_code == 401

    def test_only_the_token_owner_is_changed(self, client, db_engine, seed_employee, seed_assigned_employee):
        before = _stored_hash(db_engine, "employees", "employee_id", "TST002")
        headers = _bearer(db_engine, seed_employee["employee_id"], "employee")
        body = dict(self._body(), employee_id="TST002")
        assert client.post(self.URL, json=body, headers=headers).status_code == 200
        assert _stored_hash(db_engine, "employees", "employee_id", "TST002") == before

    @pytest.mark.parametrize("body,status", [
        ({"current_password": "", "new_password": NEW_PASSWORD}, 400),
        ({"current_password": "EmpPass@1", "new_password": ""}, 400),
        ({"current_password": "EmpPass@1", "new_password": "short-1"}, 400),
        ({"current_password": "EmpPass@1", "new_password": "tst001-my-own-id"}, 400),
        ({"current_password": "wrong-current-pass", "new_password": NEW_PASSWORD}, 401),
    ])
    def test_refusals_leave_the_password_alone(self, client, db_engine, seed_employee, body, status):
        before = _stored_hash(db_engine, "employees", "employee_id", "TST001")
        headers = _bearer(db_engine, seed_employee["employee_id"], "employee")
        resp = client.post(self.URL, json=body, headers=headers)
        assert resp.status_code == status and resp.get_json()["ok"] is False
        assert _stored_hash(db_engine, "employees", "employee_id", "TST001") == before

    # Regression: api_employee_change_password called .strip() on the JSON values without checking
    # they are strings, so a number (or list) in the body was a 500 instead of a 400.
    def test_non_string_values_are_a_400_not_a_500(self, client, db_engine, seed_employee):
        headers = _bearer(db_engine, seed_employee["employee_id"], "employee")
        resp = client.post(self.URL, json={"current_password": 12345, "new_password": NEW_PASSWORD},
                           headers=headers)
        assert resp.status_code == 400
