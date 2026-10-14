"""Regression tests for the login-hardening security review fixes.

C1  -- emailed login codes are kept server-side (login_otp_challenges);
       the session cookie only ever carries an opaque challenge id.
"""
import base64
import json
import secrets
import zlib

import pytest

from extensions import app as flask_app


# ── helpers ──────────────────────────────────────────────────────────────────

def _decode_session_cookie(raw_value):
    """Decode a Flask session cookie the way an attacker would: it is signed
    but not encrypted, so the payload is plain base64(-zlib) JSON."""
    compressed = raw_value.startswith(".")
    payload = raw_value.lstrip(".").split(".")[0]
    data = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    if compressed:
        data = zlib.decompress(data)
    return json.loads(data)


def _session_cookie_payloads(resp):
    out = []
    for header in resp.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            value = header.split("=", 1)[1].split(";", 1)[0]
            if value:
                out.append(_decode_session_cookie(value))
    return out


_SECRET_KEY_HINTS = ("otp", "code", "token", "secret", "password", "pin")


def _assert_no_secrets(payload, known_secrets=()):
    for key, value in payload.items():
        if key in ("_csrf",):  # CSRF token is meant to be readable by the page
            continue
        lowered = key.lower()
        assert not any(h in lowered and "challenge_id" not in lowered for h in _SECRET_KEY_HINTS), \
            f"session key {key!r} looks like a secret"
        for s in known_secrets:
            assert s not in json.dumps(value), f"secret value leaked into session key {key!r}"


@pytest.fixture
def mfa_on(client):
    client.application.config["MANDATORY_LOGIN_MFA"] = True
    yield
    client.application.config["MANDATORY_LOGIN_MFA"] = False


@pytest.fixture
def sent_codes(monkeypatch):
    import blueprints.auth as auth_module
    codes = []
    monkeypatch.setattr(auth_module, "send_mfa_login_email",
                        lambda to, user, role, secret, code: codes.append(code))
    return codes


def _challenge_rows(db_engine, schema=None):
    cur = db_engine.cursor()
    table = f'"{schema}".login_otp_challenges' if schema else "login_otp_challenges"
    cur.execute(f"SELECT id, kind, username, code_hash, attempts FROM {table}")  # nosec B608 -- test-only fixed name
    rows = cur.fetchall()
    cur.close()
    return rows


# ── C1: no secrets in the cookie ─────────────────────────────────────────────

class TestLoginCodeNeverInCookie:
    def test_admin_login_cookie_has_no_code(self, client, seed_admin, mfa_on, sent_codes):
        resp = client.post("/login", data={"identifier": seed_admin["username"],
                                           "password": seed_admin["password"]})
        assert resp.headers["Location"] == "/mfa_verify"
        payloads = _session_cookie_payloads(resp)
        assert payloads, "expected a session cookie"
        for p in payloads:
            _assert_no_secrets(p, known_secrets=sent_codes)
            assert "mfa_challenge_id" in p

    def test_employee_login_cookie_has_no_code(self, client, seed_employee, mfa_on, sent_codes):
        resp = client.post("/login", data={"identifier": seed_employee["employee_id"],
                                           "password": seed_employee["password"]})
        for p in _session_cookie_payloads(resp):
            _assert_no_secrets(p, known_secrets=sent_codes)

    def test_code_stored_only_as_hmac(self, client, seed_admin, mfa_on, sent_codes, db_engine):
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
        rows = _challenge_rows(db_engine)
        assert len(rows) == 1
        _id, kind, username, code_hash, attempts = rows[0]
        assert kind == "admin_users" and username == seed_admin["username"]
        assert sent_codes[-1] not in code_hash and len(code_hash) == 64

    def test_cookie_replay_attack_from_review_no_longer_works(self, client, seed_admin, mfa_on, sent_codes):
        """The exact proof-of-concept from the review: username only, wrong
        password, then read the code out of the cookie."""
        resp = client.post("/login", data={"identifier": seed_admin["username"],
                                           "password": "not-the-password"})
        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.data
        assert sent_codes == []
        for p in _session_cookie_payloads(resp):
            assert "mfa_otp_code" not in p and "mfa_challenge_id" not in p
        with client.session_transaction() as s:
            assert not s.get("admin_logged_in") and not s.get("mfa_pending")


# ── C1: wrong password never reaches the OTP step ────────────────────────────

class TestWrongPasswordNeverReachesOtp:
    @pytest.mark.parametrize("who", ["admin", "employee", "unknown"])
    def test_wrong_password_no_challenge(self, client, seed_admin, seed_employee, mfa_on, sent_codes,
                                         db_engine, who):
        identifier = {"admin": seed_admin["username"], "employee": seed_employee["employee_id"],
                      "unknown": "nobody_" + secrets.token_hex(4)}[who]
        resp = client.post("/login", data={"identifier": identifier, "password": "Wrong-Password-1"},
                           follow_redirects=False)
        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.data
        assert sent_codes == []
        assert _challenge_rows(db_engine) == []
        with client.session_transaction() as s:
            assert not s.get("mfa_pending")

    def test_mfa_verify_without_challenge_redirects_to_login(self, client, mfa_on):
        with client.session_transaction() as s:
            s["mfa_pending"] = True
            s["mfa_user"] = "test_admin"
        resp = client.post("/mfa_verify", data={"otp_code": "123456"}, follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["Location"] == "/login"


# ── C1: expiry, single use, attempt cap ──────────────────────────────────────

class TestChallengeLifecycle:
    def _start(self, client, seed_admin):
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})

    def test_expired_in_db_is_rejected(self, client, seed_admin, mfa_on, sent_codes, db_engine):
        self._start(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("UPDATE login_otp_challenges SET expires_at = NOW() - INTERVAL '1 second'")
        cur.close()
        resp = client.post("/mfa_verify", data={"otp_code": sent_codes[-1]})
        assert b"Invalid code" in resp.data
        with client.session_transaction() as s:
            assert not s.get("admin_logged_in")

    def test_single_use(self, client, seed_admin, mfa_on, sent_codes, db_engine):
        self._start(client, seed_admin)
        with client.session_transaction() as s:
            challenge_id = s["mfa_challenge_id"]
        code = sent_codes[-1]
        resp = client.post("/mfa_verify", data={"otp_code": code}, follow_redirects=False)
        assert resp.headers["Location"] == "/admin"
        assert _challenge_rows(db_engine) == []
        # Replay the same challenge id + code from a fresh browser.
        with flask_app.test_client() as other:
            with other.session_transaction() as s:
                s["mfa_pending"] = True
                s["mfa_user"] = seed_admin["username"]
                s["mfa_challenge_id"] = challenge_id
                s["mfa_issued_at"] = __import__("time").time()
            other.post("/mfa_verify", data={"otp_code": code})
            with other.session_transaction() as s:
                assert not s.get("admin_logged_in")

    def test_locks_after_five_attempts(self, client, seed_admin, mfa_on, sent_codes, db_engine):
        self._start(client, seed_admin)
        code = sent_codes[-1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            resp = client.post("/mfa_verify", data={"otp_code": wrong})
            assert b"Invalid code" in resp.data
        assert _challenge_rows(db_engine) == []  # burned after the 5th miss
        client.post("/mfa_verify", data={"otp_code": code})
        with client.session_transaction() as s:
            assert not s.get("admin_logged_in")

    def test_fourth_wrong_then_right_still_works(self, client, seed_admin, mfa_on, sent_codes):
        self._start(client, seed_admin)
        code = sent_codes[-1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            client.post("/mfa_verify", data={"otp_code": wrong})
        resp = client.post("/mfa_verify", data={"otp_code": code}, follow_redirects=False)
        assert resp.headers["Location"] == "/admin"

    def test_new_login_replaces_old_challenge(self, client, seed_admin, mfa_on, sent_codes, db_engine):
        self._start(client, seed_admin)
        self._start(client, seed_admin)
        assert len(_challenge_rows(db_engine)) == 1


# ── C1: super-admin console ──────────────────────────────────────────────────

class TestPlatformAdminCodeServerSide:
    USERNAME = "hardening_platform_admin"
    PASSWORD = "Pl@tformPass-" + secrets.token_hex(4)

    @pytest.fixture
    def platform_admin(self, db_engine):
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.platform_admins (username, password, email) VALUES (%s,%s,%s) "
                    "ON CONFLICT (username) DO NOTHING",
                    (self.USERNAME, generate_password_hash(self.PASSWORD), "pa@test.local"))
        cur.close()
        yield

    @pytest.fixture
    def platform_codes(self, client, monkeypatch):
        import blueprints.platform_admin as pa
        codes = []
        monkeypatch.setattr(pa, "send_mfa_login_email", lambda to, u, r, s, code: codes.append(code))
        original = client.application.config.get("MANDATORY_PLATFORM_ADMIN_MFA")
        client.application.config["MANDATORY_PLATFORM_ADMIN_MFA"] = True
        yield codes
        client.application.config["MANDATORY_PLATFORM_ADMIN_MFA"] = original

    def test_cookie_has_no_code_and_code_completes(self, client, platform_admin, platform_codes, db_engine):
        resp = client.post("/super_admin/login", data={"username": self.USERNAME, "password": self.PASSWORD})
        for p in _session_cookie_payloads(resp):
            _assert_no_secrets(p, known_secrets=platform_codes)
            assert "platform_mfa_otp_code" not in p
        assert len(_challenge_rows(db_engine, "att_master")) == 1
        resp = client.post("/super_admin/login", data={"otp_code": platform_codes[-1]}, follow_redirects=False)
        assert resp.headers["Location"] == "/super_admin"
        assert _challenge_rows(db_engine, "att_master") == []

    def test_wrong_password_no_challenge(self, client, platform_admin, platform_codes, db_engine):
        resp = client.post("/super_admin/login", data={"username": self.USERNAME, "password": "nope-nope"})
        assert b"Invalid credentials." in resp.data
        assert platform_codes == []
        assert _challenge_rows(db_engine, "att_master") == []


# ── Cross-tenant isolation ───────────────────────────────────────────────────

def _make_tenant(db_engine, slug):
    """Real, fully-migrated tenant schema + att_master.tenants row, with one
    admin account. Schemas created mid-test are dropped by conftest's
    _reset_db_after_test, and the att_master row is restored from the
    baseline snapshot."""
    from migrations.orchestration import init_tenant_db
    from utils.auth import generate_password_hash
    schema = f"t_{slug}"
    cur = db_engine.cursor()
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    cur.close()
    with flask_app.test_request_context():
        init_tenant_db(schema)
    cur = db_engine.cursor()
    cur.execute(f'INSERT INTO "{schema}".admin_users (username, password, email) VALUES (%s,%s,%s)',  # nosec B608
                ("iso_admin", generate_password_hash("IsoAdmin@123"), f"iso@{slug}.local"))
    cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
                "VALUES (%s,%s,%s,'active')", (f"{slug} Co", slug, schema))
    cur.close()
    return slug, schema


@pytest.fixture
def two_tenants(db_engine):
    suffix = secrets.token_hex(3)
    return _make_tenant(db_engine, f"isoa{suffix}"), _make_tenant(db_engine, f"isob{suffix}")


class TestCrossTenantChallenge:
    def test_challenge_from_tenant_a_fails_in_tenant_b(self, two_tenants, monkeypatch, db_engine):
        (slug_a, schema_a), (slug_b, schema_b) = two_tenants
        import blueprints.auth as auth_module
        codes = []
        monkeypatch.setattr(auth_module, "send_mfa_login_email", lambda to, u, r, s, code: codes.append(code))
        flask_app.config["MANDATORY_LOGIN_MFA"] = True
        try:
            with flask_app.test_client() as a:
                resp = a.post(f"/{slug_a}/login", data={"identifier": "iso_admin", "password": "IsoAdmin@123"})
                assert resp.status_code == 302 and resp.headers["Location"].endswith("/mfa_verify")
                with a.session_transaction() as s:
                    challenge_id = s["mfa_challenge_id"]
                assert len(_challenge_rows(db_engine, schema_a)) == 1
                assert _challenge_rows(db_engine, schema_b) == []

                # Same challenge id + correct code, presented to tenant B.
                with flask_app.test_client() as b:
                    with b.session_transaction() as s:
                        s["mfa_pending"] = True
                        s["mfa_user"] = "iso_admin"
                        s["mfa_challenge_id"] = challenge_id
                        s["mfa_issued_at"] = __import__("time").time()
                    b.post(f"/{slug_b}/mfa_verify", data={"otp_code": codes[-1]})
                    with b.session_transaction() as s:
                        assert not s.get("admin_logged_in")

                # Not vacuous: the very same code still completes in tenant A.
                resp = a.post(f"/{slug_a}/mfa_verify", data={"otp_code": codes[-1]}, follow_redirects=False)
                assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin")
        finally:
            flask_app.config["MANDATORY_LOGIN_MFA"] = False

    def test_hmac_is_bound_to_schema(self, two_tenants):
        """Even a row copied verbatim into another tenant's schema can't
        verify there -- the schema name is part of the HMAC input."""
        from database import get_tenant_db
        from utils.login_otp import issue_challenge, verify_challenge
        (_, schema_a), (_, schema_b) = two_tenants
        with flask_app.test_request_context():
            conn_a = get_tenant_db(schema_a)
            cur_a = conn_a.cursor()
            cid, code = issue_challenge(cur_a, "admin_users", "iso_admin", 300)
            conn_a.commit()
            cur_a.execute("SELECT kind, username, code_hash, expires_at FROM login_otp_challenges WHERE id=%s", (cid,))
            row = cur_a.fetchone()
            cur_a.close()
            conn_a.close()

            conn_b = get_tenant_db(schema_b)
            cur_b = conn_b.cursor()
            cur_b.execute("INSERT INTO login_otp_challenges (id, kind, username, code_hash, expires_at) "
                          "VALUES (%s,%s,%s,%s,%s)", (cid,) + row)
            assert verify_challenge(cur_b, cid, code, ("admin_users",)) is None
            conn_b.rollback()
            cur_b.close()
            conn_b.close()


# ── H1: mobile admin login needs the emailed code ────────────────────────────

@pytest.fixture
def api_codes(monkeypatch):
    import blueprints.core as core_module
    codes = []
    monkeypatch.setattr(core_module, "send_mfa_login_email", lambda to, u, r, s, code: codes.append(code))
    return codes


class TestMobileAdminLoginMfa:
    def test_password_alone_never_returns_token(self, client, seed_admin, mfa_on, api_codes):
        resp = client.post("/api/login", json={"username": seed_admin["username"],
                                               "password": seed_admin["password"]})
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["mfa_required"] is True and body["challenge_id"]
        assert "token" not in body
        assert len(api_codes) == 1 and api_codes[0] not in json.dumps(body)

    def test_verify_returns_token_once(self, client, seed_admin, mfa_on, api_codes, db_engine):
        body = client.post("/api/login", json={"username": seed_admin["username"],
                                               "password": seed_admin["password"]}).get_json()
        ok = client.post("/api/login/verify", json={"challenge_id": body["challenge_id"],
                                                    "otp_code": api_codes[-1]})
        assert ok.status_code == 200 and ok.get_json()["token"]
        token = ok.get_json()["token"]
        cur = db_engine.cursor()
        cur.execute("SELECT mfa_verified FROM api_tokens WHERE identity=%s", (seed_admin["username"],))
        assert cur.fetchone()[0] == 1
        cur.close()
        assert client.get("/api/admin/profile", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        again = client.post("/api/login/verify", json={"challenge_id": body["challenge_id"],
                                                       "otp_code": api_codes[-1]})
        assert again.status_code == 401 and "token" not in again.get_json()

    def test_wrong_code_five_times_burns_challenge(self, client, seed_admin, mfa_on, api_codes):
        body = client.post("/api/login", json={"username": seed_admin["username"],
                                               "password": seed_admin["password"]}).get_json()
        wrong = "000000" if api_codes[-1] != "000000" else "111111"
        for _ in range(5):
            assert client.post("/api/login/verify", json={"challenge_id": body["challenge_id"],
                                                          "otp_code": wrong}).status_code == 401
        resp = client.post("/api/login/verify", json={"challenge_id": body["challenge_id"],
                                                      "otp_code": api_codes[-1]})
        assert resp.status_code == 401

    def test_unknown_user_and_wrong_password_identical(self, client, seed_admin, mfa_on, api_codes):
        a = client.post("/api/login", json={"username": "ghost_" + secrets.token_hex(3), "password": "x-Wrong-1"})
        b = client.post("/api/login", json={"username": seed_admin["username"], "password": "x-Wrong-1"})
        assert a.status_code == b.status_code == 401
        assert a.get_json() == b.get_json() == {"ok": False, "msg": "Invalid credentials"}
        assert api_codes == []

    def test_lockout_returns_429(self, client, seed_admin, mfa_on, api_codes):
        from utils.auth import _LOGIN_MAX_ATTEMPTS
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            client.post("/api/login", json={"username": seed_admin["username"], "password": "x-Wrong-1"})
        resp = client.post("/api/login", json={"username": seed_admin["username"],
                                               "password": seed_admin["password"]})
        assert resp.status_code == 429
        assert "token" not in resp.get_json() and "challenge_id" not in resp.get_json()
        assert api_codes == []

    def test_deactivated_account_gets_generic_error(self, client, seed_admin, mfa_on, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username=%s", (seed_admin["username"],))
        cur.close()
        resp = client.post("/api/login", json={"username": seed_admin["username"],
                                               "password": seed_admin["password"]})
        assert resp.status_code == 401 and resp.get_json()["msg"] == "Invalid credentials"


class TestWebSessionBridgeNeedsMfaToken:
    def _token(self, db_engine, username, mfa_verified):
        from utils.auth import _hash_token
        raw = secrets.token_hex(32)
        cur = db_engine.cursor()
        cur.execute("INSERT INTO api_tokens (token, token_type, identity, expires_at, mfa_verified) "
                    "VALUES (%s,'admin',%s, NOW() + INTERVAL '1 hour', %s)",
                    (_hash_token(raw), username, mfa_verified))
        cur.close()
        return raw

    def test_password_only_token_cannot_mint_bridge(self, client, seed_admin, mfa_on, db_engine):
        raw = self._token(db_engine, seed_admin["username"], 0)
        resp = client.post("/api/mobile/web_session_link", json={}, headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 403 and "url" not in resp.get_json()

    def test_mfa_token_can_mint_bridge(self, client, seed_admin, mfa_on, db_engine):
        raw = self._token(db_engine, seed_admin["username"], 1)
        resp = client.post("/api/mobile/web_session_link", json={}, headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200 and resp.get_json()["url"]


class TestCrossTenantToken:
    def test_token_from_tenant_a_rejected_in_tenant_b(self, two_tenants):
        (slug_a, _), (slug_b, _) = two_tenants
        with flask_app.test_client() as c:
            resp = c.post(f"/{slug_a}/api/login", json={"username": "iso_admin", "password": "IsoAdmin@123"})
            token = resp.get_json()["token"]
            hdr = {"Authorization": f"Bearer {token}"}
        with flask_app.test_client() as c:
            assert c.get(f"/{slug_a}/api/admin/profile", headers=hdr).status_code == 200
        with flask_app.test_client() as c:
            assert c.get(f"/{slug_b}/api/admin/profile", headers=hdr).status_code == 401


# ── M2: password reset/change revokes older sessions and tokens ──────────────

def _reset_link(db_engine, table, id_column, id_value):
    import datetime
    import hashlib
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(f"UPDATE {table} SET reset_token=%s, reset_token_expiry=%s WHERE {id_column}=%s",  # nosec B608
                (hashlib.sha256(raw.encode()).hexdigest(),
                 datetime.datetime.utcnow() + datetime.timedelta(hours=1), id_value))
    cur.close()
    return raw


def _employee_token(client, seed_employee):
    resp = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"]})
    return resp.get_json()["token"]


class TestPasswordChangeRevokes:
    NEW_PW = "Brand-New-Pass-77"

    def test_employee_reset_kills_old_session_and_token(self, client, seed_employee, db_engine):
        token = _employee_token(client, seed_employee)
        client.post("/login", data={"identifier": seed_employee["employee_id"], "password": seed_employee["password"]})
        assert client.get("/employee_portal", follow_redirects=False).status_code == 200
        with client.session_transaction() as s:
            assert s["_pwv"][0] == "employees"

        raw = _reset_link(db_engine, "employees", "employee_id", seed_employee["employee_id"])
        with flask_app.test_client() as other:
            resp = other.post(f"/employee_reset_password/{raw}",
                              data={"new_password": self.NEW_PW, "confirm_password": self.NEW_PW})
            assert resp.status_code == 200

        # Old browser session is dead.
        resp = client.get("/employee_portal", follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as s:
            assert not s.get("employee_id")
        # Old mobile token is dead.
        r = client.get("/api/employee/portal", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        # And a fresh login with the new password works.
        with flask_app.test_client() as fresh:
            fresh.post("/login", data={"identifier": seed_employee["employee_id"], "password": self.NEW_PW})
            assert fresh.get("/employee_portal", follow_redirects=False).status_code == 200

    def test_admin_reset_kills_old_session_and_token(self, client, seed_admin, db_engine):
        tok = client.post("/api/login", json={"username": seed_admin["username"],
                                              "password": seed_admin["password"]}).get_json()["token"]
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
        assert client.get("/admin", follow_redirects=False).status_code == 200

        raw = _reset_link(db_engine, "admin_users", "username", seed_admin["username"])
        with flask_app.test_client() as other:
            other.post(f"/admin_reset_password/{raw}",
                       data={"new_password": self.NEW_PW, "confirm_password": self.NEW_PW})

        assert client.get("/admin", follow_redirects=False).status_code == 302
        assert client.get("/api/admin/profile", headers={"Authorization": f"Bearer {tok}"}).status_code == 401

    def test_own_change_keeps_this_session_kills_others(self, client, seed_employee):
        client.post("/login", data={"identifier": seed_employee["employee_id"], "password": seed_employee["password"]})
        # Not a `with` block: two clients that both preserve request
        # context can't interleave requests.
        other_device = flask_app.test_client()
        other_device.post("/login", data={"identifier": seed_employee["employee_id"],
                                          "password": seed_employee["password"]})
        assert other_device.get("/employee_portal", follow_redirects=False).status_code == 200

        resp = client.post("/change_password", data={
            "current_password": seed_employee["password"],
            "new_password": self.NEW_PW, "confirm_password": self.NEW_PW,
        }, follow_redirects=False)
        assert "pwd_ok=1" in resp.headers["Location"]
        assert client.get("/employee_portal", follow_redirects=False).status_code == 200
        assert other_device.get("/employee_portal", follow_redirects=False).status_code == 302

    def test_token_older_than_password_change_rejected_even_if_not_deleted(self, client, seed_employee, db_engine):
        token = _employee_token(client, seed_employee)
        cur = db_engine.cursor()
        cur.execute("UPDATE api_tokens SET created_at = created_at - INTERVAL '1 hour' WHERE identity=%s",
                    (seed_employee["employee_id"],))
        cur.execute("UPDATE employees SET password_changed_at = NOW() - INTERVAL '1 minute' WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        cur.close()
        r = client.get("/api/employee/portal", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_admin_resets_employee_password_revokes_tokens(self, client, seed_admin, seed_employee):
        token = _employee_token(client, seed_employee)
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
        client.post("/admin_action", data={"action": "reset_password", "emp_id": seed_employee["employee_id"]})
        with flask_app.test_client() as c:
            assert c.get("/api/employee/portal", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ── M1: check-in method is decided by the server ─────────────────────────────

_FACE_ON = {"fingerprint_enabled": True, "qr_enabled": True, "face_enabled": True, "location_enabled": False}
_FACE_OFF = {"fingerprint_enabled": True, "qr_enabled": True, "face_enabled": False, "location_enabled": False}


class TestCheckinCombosServerSide:
    def test_policy_table(self):
        from utils.helpers import allowed_checkin_combos
        assert allowed_checkin_combos(_FACE_ON) == {"qr_face", "face_fingerprint"}
        assert allowed_checkin_combos(_FACE_OFF) == {"qr_only", "qr_fingerprint", "fingerprint_only"}
        assert allowed_checkin_combos({"qr_enabled": True, "face_enabled": True}) == {"qr_face"}
        assert allowed_checkin_combos({}) == set()

    @pytest.mark.parametrize("combo", ["qr_only", "qr_fingerprint", "fingerprint_only"])
    def test_kiosk_rejects_face_skipping_combo_when_face_enabled(self, client, seed_employee, mocker, signed_qr,
                                                                 combo):
        mocker.patch("blueprints.attendance.get_auth_config", return_value=dict(_FACE_ON))
        rv = client.post("/attendance", json={"employee_id": signed_qr(seed_employee["employee_id"]),
                                              "auth_combo": combo})
        assert rv.status_code == 403
        assert rv.get_json()["ok"] is False

    def test_kiosk_qr_only_allowed_when_face_disabled(self, client, seed_employee, mocker, signed_qr):
        mocker.patch("blueprints.attendance.get_auth_config", return_value=dict(_FACE_OFF))
        rv = client.post("/attendance", json={"employee_id": signed_qr(seed_employee["employee_id"]),
                                              "auth_combo": "qr_only"})
        assert rv.status_code != 403

    def test_mobile_checkin_rejects_qr_fingerprint_when_face_enabled(self, client, seed_employee, mocker,
                                                                     signed_qr):
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value=dict(_FACE_ON))
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]), "auth_combo": "qr_fingerprint"})
        assert rv.status_code == 403


# ── M3 + L5: trial signup payment binding and application access ─────────────

def _approved_trial_application(db_engine):
    from utils.auth import _hash_token, generate_password_hash
    token = secrets.token_urlsafe(32)
    slug = "trial" + secrets.token_hex(3)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.tenant_applications (company_name, subdomain, admin_username, admin_email, "
        "admin_password_hash, payment_option, access_token_hash, status) "
        "VALUES (%s,%s,%s,%s,%s,'trial',%s,'approved_pending_payment') RETURNING id",
        (f"{slug} Co", slug, "trial_admin", f"{slug}@test.local", generate_password_hash("Trial@12345"),
         _hash_token(token)))
    app_id = cur.fetchone()[0]
    cur.close()
    return app_id, token


@pytest.fixture
def no_razorpay_keys(monkeypatch):
    """Force the no-keys (demo-capable) configuration regardless of the
    local .env, so these tests exercise the demo/fail-closed branches."""
    import utils.razorpay_utils as rz
    monkeypatch.setattr(rz, "_KEY_ID", "")
    monkeypatch.setattr(rz, "_KEY_SECRET", "")
    monkeypatch.setenv("APP_ENV", "development")


class TestApplicationAccessToken:
    @pytest.mark.parametrize("path", ["/create_org/pay/{id}", "/create_org/setup_trial/{id}"])
    def test_pages_404_without_or_with_wrong_token(self, client, db_engine, path):
        app_id, _token = _approved_trial_application(db_engine)
        assert client.get(path.format(id=app_id)).status_code == 404
        assert client.get(path.format(id=app_id) + "?token=wrong").status_code == 404

    def test_setup_trial_with_token_redirects_to_clean_url(self, client, db_engine):
        app_id, token = _approved_trial_application(db_engine)
        resp = client.get(f"/create_org/setup_trial/{app_id}?token={token}", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["Location"] == f"/create_org/setup_trial/{app_id}"
        for p in _session_cookie_payloads(resp):
            assert token not in json.dumps(p)
        page = client.get(f"/create_org/setup_trial/{app_id}")
        assert page.status_code == 200
        assert token.encode() not in page.data

    def test_create_subscription_404_without_proof(self, client, db_engine):
        app_id, _ = _approved_trial_application(db_engine)
        assert client.post(f"/api/create_org/setup_trial/{app_id}/create_subscription").status_code == 404


class TestTrialSubscriptionBinding:
    def _verified(self, client, db_engine):
        app_id, token = _approved_trial_application(db_engine)
        client.get(f"/create_org/setup_trial/{app_id}?token={token}")
        return app_id

    def _status(self, db_engine, app_id):
        cur = db_engine.cursor()
        cur.execute("SELECT status, pending_subscription_id FROM att_master.tenant_applications WHERE id=%s",
                    (app_id,))
        row = cur.fetchone()
        cur.close()
        return row

    def test_subscription_is_recorded_and_mismatch_rejected(self, client, db_engine, no_razorpay_keys):
        app_id = self._verified(client, db_engine)
        sub = client.post(f"/api/create_org/setup_trial/{app_id}/create_subscription").get_json()
        assert sub["ok"] is True
        assert self._status(db_engine, app_id) == ("approved_pending_payment", sub["subscription_id"])
        other = "sub_" + secrets.token_hex(8)
        resp = client.post(f"/api/create_org/setup_trial/{app_id}/confirm", json={
            "razorpay_subscription_id": other, "razorpay_payment_id": "pay_x", "razorpay_signature": "sig"})
        assert resp.status_code == 400
        assert self._status(db_engine, app_id)[0] == "approved_pending_payment"

    def test_demo_subscription_rejected_outside_development(self, client, db_engine, no_razorpay_keys,
                                                            monkeypatch):
        app_id = self._verified(client, db_engine)
        sub = client.post(f"/api/create_org/setup_trial/{app_id}/create_subscription").get_json()
        assert sub["demo"] is True
        monkeypatch.setenv("APP_ENV", "production")
        resp = client.post(f"/api/create_org/setup_trial/{app_id}/confirm", json={
            "razorpay_subscription_id": sub["subscription_id"]})
        assert resp.status_code == 400
        assert self._status(db_engine, app_id)[0] == "approved_pending_payment"

    @pytest.mark.parametrize("app_env", ["production", "staging", ""])
    def test_demo_helpers_fail_closed(self, no_razorpay_keys, monkeypatch, app_env):
        import utils.razorpay_utils as rz
        monkeypatch.setenv("APP_ENV", app_env)
        new_id, _is_demo, err = rz.create_id_or_demo("demo_sub_", lambda: ("never", None))
        assert new_id is None and err
        ok, _is_demo, _err = rz.verify_or_demo("demo_sub_abc", "demo_sub_", "", "", lambda *a: True)
        assert ok is False

    def test_demo_helpers_work_in_development(self, no_razorpay_keys):
        import utils.razorpay_utils as rz
        new_id, is_demo, err = rz.create_id_or_demo("demo_sub_", lambda: ("never", None))
        assert new_id.startswith("demo_sub_") and is_demo and err is None


# ── L1: ID-card template fields are escaped ──────────────────────────────────

class TestIdCardFieldsEscaped:
    def test_script_breakout_rendered_escaped(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES (%s) RETURNING id", ("Esc Co",))
        cid = cur.fetchone()[0]
        payload = {"employee_name": {"label": "</script><b id=pwn>x</b><script>"}}
        cur.execute("INSERT INTO id_card_templates (company_id, fields) VALUES (%s,%s)",
                    (cid, json.dumps(payload)))
        cur.close()
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
        resp = client.get(f"/companies/{cid}/id_card_template/editor")
        assert resp.status_code == 200
        assert b"</script><b id=pwn>" not in resp.data
        assert b"\\u003c/script\\u003e" in resp.data


# ── L2 / L3: generic errors, no enumeration ──────────────────────────────────

class TestGenericErrors:
    def test_kiosk_registration_error_is_generic(self, client, seed_employee, monkeypatch):
        import blueprints.auth as auth_module
        monkeypatch.setattr(auth_module, "_webauthn_available", True)

        def boom(*a, **k):
            raise RuntimeError("internal-detail-xyz /srv/db password=hunter2")
        monkeypatch.setattr(auth_module, "_wa_verify_and_store_registration", boom)
        with client.session_transaction() as s:
            s["wa_reg_challenge"] = "abc"
            s["wa_reg_emp_id"] = seed_employee["employee_id"]
        resp = client.post("/api/employee/webauthn-register-kiosk",
                           json={"emp_id": seed_employee["employee_id"], "credential": {}})
        assert resp.status_code == 500
        assert b"internal-detail-xyz" not in resp.data and b"hunter2" not in resp.data

    def test_employee_api_login_no_enumeration(self, client, seed_employee):
        real, ghost = seed_employee["employee_id"], "GHOST" + secrets.token_hex(3).upper()
        r1 = client.post("/api/employee/login", json={"employee_id": real})
        r2 = client.post("/api/employee/login", json={"employee_id": ghost})
        assert r1.status_code == r2.status_code == 400 and r1.get_json() == r2.get_json()
        w1 = client.post("/api/employee/login", json={"employee_id": real, "password": "Wrong-1"})
        w2 = client.post("/api/employee/login", json={"employee_id": ghost, "password": "Wrong-1"})
        assert w1.status_code == w2.status_code == 401 and w1.get_json() == w2.get_json()

    def test_employee_api_password_not_stripped(self, client, seed_employee):
        resp = client.post("/api/employee/login", json={"employee_id": seed_employee["employee_id"],
                                                        "password": " " + seed_employee["password"] + " "})
        assert resp.status_code == 401


# ── L4: shared-download routes apply the session kill switch ─────────────────

class TestAnyUserRequired:
    @pytest.mark.parametrize("path", ["/download_document/1", "/download_announcement/1",
                                      "/onboarding_document/1", "/my_photo"])
    def test_anonymous_redirected(self, client, path):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302 and "login" in resp.headers["Location"]

    @pytest.mark.parametrize("path", ["/download_document/1", "/download_announcement/1",
                                      "/onboarding_document/1", "/my_photo",
                                      "/view_payslip/E1/2026/1"])
    def test_compromised_session_rejected(self, client, seed_employee, monkeypatch, path):
        import utils.auth as auth_module
        client.post("/login", data={"identifier": seed_employee["employee_id"],
                                    "password": seed_employee["password"]})
        monkeypatch.setattr(auth_module, "is_session_compromised", lambda sid: True)
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302 and "locked=1" in resp.headers["Location"]
        with client.session_transaction() as s:
            assert not s.get("employee_id")

    def test_compromised_session_rejected_on_upload(self, client, seed_employee, monkeypatch):
        import utils.auth as auth_module
        client.post("/login", data={"identifier": seed_employee["employee_id"],
                                    "password": seed_employee["password"]})
        monkeypatch.setattr(auth_module, "is_session_compromised", lambda sid: True)
        resp = client.post("/upload_my_document", data={"doc_type": "id"}, follow_redirects=False)
        assert resp.status_code == 302 and "locked=1" in resp.headers["Location"]


# ── L6: logout is POST-only ──────────────────────────────────────────────────

class TestLogoutPostOnly:
    @pytest.mark.parametrize("path", ["/logout", "/employee_logout"])
    def test_get_not_allowed(self, client, path):
        assert client.get(path).status_code == 405

    def test_admin_nav_uses_csrf_post_form(self, client, seed_admin):
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
        html = client.get("/admin").data.decode()
        marker = 'action="/logout" method="POST"'
        assert marker in html
        assert 'name="_csrf_token"' in html[html.index(marker):html.index(marker) + 400]
        assert 'href="/logout"' not in html

    def test_csrf_enforced_on_logout_when_not_testing(self, client, seed_admin):
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
        client.application.testing = False
        try:
            resp = client.post("/logout", follow_redirects=False)
        finally:
            client.application.testing = True
        assert resp.status_code in (400, 403)
        with client.session_transaction() as s:
            assert s.get("admin_logged_in")


# ── I1: works without pkg_resources (setuptools>=83) ─────────────────────────

class TestDependencyCompat:
    def test_face_recognition_and_clamd_import_without_pkg_resources(self):
        import os
        import subprocess
        import sys
        # Make pkg_resources genuinely unimportable, as with setuptools>=83.
        code = (
            "import sys\n"
            "class _Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'pkg_resources' or name.startswith('pkg_resources.'):\n"
            "            raise ImportError('blocked for test')\n"
            "sys.meta_path.insert(0, _Block())\n"
            "sys.modules.pop('pkg_resources', None)\n"
            "import utils.face_utils as f\n"
            "import clamd\n"
            "assert 'pkg_resources' not in sys.modules, 'shim leaked'\n"
            "print('FACE', f._face_recognition_available)\n"
            "print('CLAMD', hasattr(clamd, 'ClamdNetworkSocket'))\n"
        )
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=180)
        assert out.returncode == 0, out.stderr[-2000:]
        assert "CLAMD True" in out.stdout
        import importlib.util
        if importlib.util.find_spec("face_recognition") is not None:
            assert "FACE True" in out.stdout, out.stdout + out.stderr[-2000:]


# ── V4: "enrolled" via emailed code but no TOTP secret -> step-up fails closed ─

class TestTotpStepUpFailsClosedWithoutSecret:
    def test_marked_enabled_without_secret_never_verifies(self, client, seed_admin, db_engine):
        from utils.totp import verify_totp_code
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_enabled=1, totp_secret=NULL WHERE username=%s",
                    (seed_admin["username"],))
        cur.close()
        with flask_app.test_request_context():
            for code in ("000000", "123456", "999999"):
                assert verify_totp_code(seed_admin["username"], code) is False
                assert verify_totp_code(seed_admin["username"], code, require_enabled=False) is False


# ── Status page after the approval email rotates the access token ─────────────

class TestStatusPageAfterTokenRotation:
    def test_fresh_link_token_wins_over_stale_session_token(self, client, db_engine):
        app_id, token = _approved_trial_application(db_engine)
        with client.session_transaction() as s:
            s["org_access_token"] = "stale-token-from-signup"
        resp = client.get(f"/create_org/status/{app_id}?token={token}", follow_redirects=False)
        assert resp.status_code == 200

    def test_reachable_after_trial_page_verified_access(self, client, db_engine):
        app_id, token = _approved_trial_application(db_engine)
        with client.session_transaction() as s:
            s["org_access_token"] = "stale-token-from-signup"
        client.get(f"/create_org/setup_trial/{app_id}?token={token}")
        resp = client.get(f"/create_org/status/{app_id}", follow_redirects=False)
        assert resp.status_code == 200

    def test_still_denied_without_any_proof(self, client, db_engine):
        app_id, _token = _approved_trial_application(db_engine)
        resp = client.get(f"/create_org/status/{app_id}", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["Location"] == "/create_org"
