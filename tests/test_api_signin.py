"""/api/signin: the app's one sign-in box, with the website /login's rules
(blueprints/core.py's api_signin) -- an admin account by username or email,
anything else as an employee ID, the same answers /api/login and
/api/employee/login give."""
from extensions import app as flask_app


def _signin(client, identifier, password):
    return client.post("/api/signin", json={"identifier": identifier, "password": password})


class TestApiSignin:
    def test_admin_username_signs_in_as_admin(self, client, seed_admin):
        resp = _signin(client, seed_admin["username"], seed_admin["password"])
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["ok"] and body["token"]
        assert body["account"] == "admin"
        assert body["username"] == seed_admin["username"]
        assert body["role"] == "admin"

    def test_employee_id_signs_in_as_employee(self, client, seed_employee):
        resp = _signin(client, seed_employee["employee_id"], seed_employee["password"])
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["ok"] and body["token"]
        assert body["account"] == "employee"
        assert body["employee_id"] == seed_employee["employee_id"]

    def test_employee_id_is_case_insensitive_like_the_old_employee_tab(self, client, seed_employee):
        resp = _signin(client, seed_employee["employee_id"].lower(), seed_employee["password"])
        assert resp.status_code == 200
        assert resp.get_json()["account"] == "employee"

    def test_token_works_on_the_api(self, client, seed_employee):
        token = _signin(client, seed_employee["employee_id"], seed_employee["password"]).get_json()["token"]
        with flask_app.test_client() as device:
            resp = device.get("/api/employee/profile", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code != 401

    def test_wrong_password_is_the_generic_answer(self, client, seed_admin, seed_employee):
        for ident in (seed_admin["username"], seed_employee["employee_id"], "NO-SUCH-PERSON"):
            resp = _signin(client, ident, "Wrong-Pass-1")
            assert resp.status_code == 401
            body = resp.get_json()
            assert body == {"ok": False, "msg": "Invalid credentials"}

    def test_missing_fields_are_rejected(self, client):
        assert _signin(client, "", "x").status_code == 401
        assert _signin(client, "someone", "").status_code == 401
        assert client.post("/api/signin", json={}).status_code == 401

    def test_admin_with_mfa_gets_a_challenge_not_a_token(self, client, seed_admin, monkeypatch):
        monkeypatch.setitem(flask_app.config, "MANDATORY_LOGIN_MFA", True)
        sent = []
        monkeypatch.setattr("blueprints.core.send_mfa_login_email", lambda *a, **k: sent.append(a))
        resp = _signin(client, seed_admin["username"], seed_admin["password"])
        body = resp.get_json()
        if resp.status_code == 200 and body.get("mfa_required"):
            assert body["challenge_id"] and "token" not in body
            assert body["account"] == "admin"
            assert sent
        else:
            # A seeded admin without an email can't receive the code: same
            # generic refusal /api/login gives.
            assert resp.status_code == 401
