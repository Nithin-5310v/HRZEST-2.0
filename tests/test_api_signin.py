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


class TestSigninFindsTheCompany:
    """The app's sign-in has no company box: a bare /api/signin finds the
    company from the details (auth._bare_login_company, as the website's
    /login) and answers which one in company_slug. The suite's one company
    is "Test Co" at /att-test-suite/ (conftest)."""

    def test_company_named_in_the_url_is_answered(self, client, seed_employee):
        resp = client.post("/att-test-suite/api/signin", json={
            "identifier": seed_employee["employee_id"], "password": seed_employee["password"]})
        assert resp.status_code == 200 and resp.get_json()["company_slug"] == "att-test-suite"

    def test_multi_company_server_finds_it_from_the_details(self, client, seed_employee, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = _signin(client, seed_employee["employee_id"], seed_employee["password"])
        body = resp.get_json()
        assert resp.status_code == 200 and body["account"] == "employee"
        assert body["company_slug"] == "att-test-suite"
        # The token belongs to that company: it works under its address.
        with flask_app.test_client() as device:
            mine = device.get("/att-test-suite/api/employee/portal",
                              headers={"Authorization": f"Bearer {body['token']}"})
        assert mine.status_code == 200

    def test_admin_with_mfa_learns_the_company_before_the_code_step(self, client, seed_admin, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        monkeypatch.setitem(flask_app.config, "MANDATORY_LOGIN_MFA", True)
        monkeypatch.setattr("blueprints.core.send_mfa_login_email", lambda *a, **k: None)
        resp = _signin(client, seed_admin["username"], seed_admin["password"])
        body = resp.get_json()
        if resp.status_code == 200 and body.get("mfa_required"):
            # The app sends the code to this company's /api/login/verify.
            assert body["company_slug"] == "att-test-suite" and "token" not in body
        else:
            # A seeded admin without an email can't get a code: refused, no token.
            assert not body.get("token")

    def test_details_in_more_than_one_company_ask_which(self, client, seed_admin, monkeypatch):
        import blueprints.auth as auth
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        monkeypatch.setattr(auth, "_company_for_credentials", lambda identifier, password: "many")
        resp = _signin(client, seed_admin["username"], seed_admin["password"])
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["company_required"] is True and "token" not in body

    def test_unknown_account_is_the_generic_answer(self, client, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = _signin(client, "nobody-here@example.test", "whatever-123")
        assert resp.status_code in (401, 403) and not resp.get_json()["ok"]
