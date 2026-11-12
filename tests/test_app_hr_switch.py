"""HR staff in the app, like on the website: an employee assigned the HR
role in Settings -> Roles & Access signs in (/api/signin) to the HR panel -- through the admin login, emailed
code included -- and can switch to their own employee view
(/api/hr/my_employee_session), the app's twin of the web "My Profile" switch."""
import pytest

from extensions import app as flask_app


@pytest.fixture
def hr_employee(db_engine, seed_employee):
    emp_id = seed_employee["employee_id"]
    cur = db_engine.cursor()
    cur.execute("INSERT INTO roles (role_key, display_name, base_type, is_system) "
                "VALUES ('hr','HR','hr',FALSE) ON CONFLICT (role_key) DO NOTHING")
    db_engine.commit()
    from utils.helpers.rbac import assign_employee_role
    with flask_app.test_request_context():
        assign_employee_role(emp_id, "hr")
    yield seed_employee
    cur.execute("UPDATE employees SET role=NULL, access_role=NULL WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM api_tokens WHERE identity=%s", (emp_id,))
    cur.execute("DELETE FROM admin_users WHERE username=%s", (emp_id,))
    db_engine.commit()
    cur.close()


def _signin(client, identifier, password):
    return client.post("/api/signin", json={"identifier": identifier, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


class TestHrSignin:
    def test_hr_employee_signs_in_to_the_hr_panel(self, client, db_engine, hr_employee):
        resp = _signin(client, hr_employee["employee_id"], hr_employee["password"])
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["account"] == "admin" and body["role"] == "hr"
        assert body["username"] == hr_employee["employee_id"]
        # An HR-only API answers the token; no stray employee token was left behind.
        assert client.get("/api/admin/profile", headers=_bearer(body["token"])).status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT token_type FROM api_tokens WHERE identity=%s", (hr_employee["employee_id"],))
        assert [r[0] for r in cur.fetchall()] == ["admin"]
        cur.close()

    def test_hr_employee_gets_the_emailed_code_when_required(self, client, hr_employee, monkeypatch):
        monkeypatch.setitem(flask_app.config, "MANDATORY_LOGIN_MFA", True)
        sent = []
        monkeypatch.setattr("blueprints.core.send_mfa_login_email", lambda *a, **k: sent.append(a))
        body = _signin(client, hr_employee["employee_id"], hr_employee["password"]).get_json()
        assert body["mfa_required"] and "token" not in body and body["account"] == "admin"
        verified = client.post("/api/login/verify", json={"challenge_id": body["challenge_id"],
                                                          "otp_code": sent[0][4]}).get_json()
        assert verified["ok"] and verified["role"] == "hr" and verified["token"]

    def test_other_employees_still_sign_in_as_employees(self, client, seed_employee):
        body = _signin(client, seed_employee["employee_id"], seed_employee["password"]).get_json()
        assert body["account"] == "employee"

    def test_hr_job_title_alone_signs_in_as_an_employee(self, client, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='HR' WHERE employee_id=%s", (emp_id,))
        db_engine.commit()
        try:
            body = _signin(client, emp_id, seed_employee["password"]).get_json()
            assert body["account"] == "employee"
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (emp_id,))
            cur.execute("DELETE FROM api_tokens WHERE identity=%s", (emp_id,))
            db_engine.commit()
            cur.close()

    def test_wrong_password_is_still_generic(self, client, hr_employee):
        resp = _signin(client, hr_employee["employee_id"], "Wrong-Pass-1")
        assert resp.status_code == 401 and resp.get_json() == {"ok": False, "msg": "Invalid credentials"}


class TestSwitchToMyEmployeeView:
    def test_hr_gets_an_employee_token_for_themselves(self, client, hr_employee):
        hr_token = _signin(client, hr_employee["employee_id"], hr_employee["password"]).get_json()["token"]
        resp = client.post("/api/hr/my_employee_session", headers=_bearer(hr_token))
        body = resp.get_json()
        assert resp.status_code == 200 and body["employee_id"] == hr_employee["employee_id"]
        assert client.get("/api/employee/profile", headers=_bearer(body["token"])).status_code == 200
        # The HR token stays valid, so the app can switch back.
        assert client.get("/api/admin/profile", headers=_bearer(hr_token)).status_code == 200

    def test_owner_admin_without_a_profile_is_told_to_make_one(self, client, seed_admin):
        admin_token = _signin(client, seed_admin["username"], seed_admin["password"]).get_json()["token"]
        resp = client.post("/api/hr/my_employee_session", headers=_bearer(admin_token))
        assert resp.status_code == 404 and "Create your employee profile" in resp.get_json()["msg"]

    def test_owner_admin_switches_to_their_linked_profile(self, client, db_engine, seed_admin, seed_employee):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET linked_employee_id=%s WHERE username=%s",
                    (seed_employee["employee_id"], seed_admin["username"]))
        # The linked profile's own password is never used, so a pending
        # change on it doesn't block the switch.
        cur.execute("UPDATE employees SET must_change_password=1 WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        db_engine.commit()
        cur.close()
        admin_token = _signin(client, seed_admin["username"], seed_admin["password"]).get_json()["token"]
        resp = client.post("/api/hr/my_employee_session", headers=_bearer(admin_token))
        body = resp.get_json()
        assert resp.status_code == 200 and body["employee_id"] == seed_employee["employee_id"]
        assert client.get("/api/employee/profile", headers=_bearer(body["token"])).status_code == 200
        assert client.get("/api/admin/profile", headers=_bearer(admin_token)).status_code == 200

    def test_managers_and_employees_cannot(self, client, db_engine, seed_admin, seed_employee):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='manager', linked_employee_id=%s WHERE username=%s",
                    (seed_employee["employee_id"], seed_admin["username"]))
        db_engine.commit()
        cur.close()
        manager_token = _signin(client, seed_admin["username"], seed_admin["password"]).get_json()["token"]
        assert client.post("/api/hr/my_employee_session", headers=_bearer(manager_token)).status_code == 403
        emp_token = _signin(client, seed_employee["employee_id"], seed_employee["password"]).get_json()["token"]
        assert client.post("/api/hr/my_employee_session", headers=_bearer(emp_token)).status_code == 401
        assert client.post("/api/hr/my_employee_session").status_code == 401

    def test_hr_account_without_an_employee_record(self, client, seed_hr_admin):
        token = _signin(client, seed_hr_admin["username"], seed_hr_admin["password"]).get_json()["token"]
        resp = client.post("/api/hr/my_employee_session", headers=_bearer(token))
        assert resp.status_code == 404 and "No personal employee profile" in resp.get_json()["msg"]
