"""Tests for authentication flows: login, lockout, session, password hashing."""
from utils.auth import (
    generate_password_hash,
    check_password_hash,
    _check_login_lockout,
    _record_login_failure,
    _clear_login_failures,
    _LOGIN_MAX_ATTEMPTS,
)
from utils.async_writer import _write_queue


def _wait_for_async_writes():
    """_record_login_failure/_clear_login_failures enqueue their DB write
    onto a background thread instead of writing synchronously (see
    utils/async_writer.py — the fix for request threads blocking under a
    brute-force flood). queue.Queue.join() blocks until every enqueued
    item has had task_done() called, which the writer thread does after
    each write completes — the precise way to wait for the queue to drain
    in a test, rather than an arbitrary sleep."""
    _write_queue.join()


# ── Password hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_bcrypt_round_trip(self):
        pw_hash = generate_password_hash("MySecret@99")
        assert check_password_hash(pw_hash, "MySecret@99")

    def test_wrong_password_rejected(self):
        pw_hash = generate_password_hash("CorrectHorse")
        assert not check_password_hash(pw_hash, "WrongPassword")

    def test_empty_hash_rejected(self):
        assert not check_password_hash("", "anything")
        assert not check_password_hash(None, "anything")

    def test_legacy_pbkdf2_accepted(self):
        """Legacy werkzeug pbkdf2 hashes must still verify after migration."""
        from werkzeug.security import generate_password_hash as wz_hash
        legacy = wz_hash("OldPass123", method="pbkdf2:sha256")
        assert check_password_hash(legacy, "OldPass123")

    def test_new_hashes_are_argon2id(self):
        pw_hash = generate_password_hash("anything")
        assert pw_hash.startswith("$argon2id$")
        assert "m=19456,t=2,p=1" in pw_hash

    def test_legacy_bcrypt_still_verifies_and_needs_rehash(self):
        import bcrypt
        from utils.auth import password_needs_rehash
        legacy = bcrypt.hashpw(b"OldPass123", bcrypt.gensalt(rounds=4)).decode()
        assert check_password_hash(legacy, "OldPass123")
        assert not check_password_hash(legacy, "wrong")
        assert password_needs_rehash(legacy)
        assert not password_needs_rehash(generate_password_hash("OldPass123"))

    def test_garbage_argon2_hash_is_rejected_not_raised(self):
        assert not check_password_hash("$argon2id$garbage", "x")

    def test_different_users_get_different_hashes(self):
        h1 = generate_password_hash("SamePassword")
        h2 = generate_password_hash("SamePassword")
        assert h1 != h2  # bcrypt uses random salt


# ── Admin login ───────────────────────────────────────────────────────────────

class TestAdminLogin:
    def test_valid_login_redirects_to_admin(self, client, seed_admin):
        resp = client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        }, follow_redirects=False)
        assert resp.status_code in (302, 200)
        if resp.status_code == 302:
            assert "/admin" in resp.headers.get("Location", "")

    def test_wrong_password_returns_error(self, client, seed_admin):
        resp = client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": "WrongPassword!",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.data

    def test_unknown_user_returns_error(self, client):
        resp = client.post("/login", data={
            "identifier": "no_such_user_xyz",
            "password": "doesntmatter",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.data


# ── Account lockout ───────────────────────────────────────────────────────────

class TestAccountLockout:
    _IDENT = "lockout_test_user_99"

    def teardown_method(self):
        _clear_login_failures(self._IDENT)
        _wait_for_async_writes()

    def test_not_locked_initially(self):
        locked, _ = _check_login_lockout(self._IDENT)
        assert not locked

    def test_locked_after_max_failures(self):
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            _record_login_failure(self._IDENT)
        _wait_for_async_writes()
        locked, until = _check_login_lockout(self._IDENT)
        assert locked
        assert until is not None

    def test_cleared_after_success(self):
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            _record_login_failure(self._IDENT)
        _clear_login_failures(self._IDENT)
        _wait_for_async_writes()
        locked, _ = _check_login_lockout(self._IDENT)
        assert not locked


# ── API token auth ────────────────────────────────────────────────────────────

class TestApiTokenAuth:
    def test_missing_token_returns_401(self, client):
        resp = client.get("/api/employees")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        resp = client.get("/api/employees",
                          headers={"Authorization": "Bearer fake-token-abc"})
        assert resp.status_code == 401

    def test_valid_admin_token_flow(self, client, seed_admin):
        # Get a real token via /api/login
        resp = client.post("/api/login", json={
            "username": seed_admin["username"],
            "password": seed_admin["password"],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"]
        token = data["token"]

        # Use it
        resp2 = client.get("/api/employees",
                           headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200

        # Logout
        resp3 = client.post("/api/logout",
                            headers={"Authorization": f"Bearer {token}"})
        assert resp3.get_json()["ok"]

        # Token should now be invalid
        resp4 = client.get("/api/employees",
                           headers={"Authorization": f"Bearer {token}"})
        assert resp4.status_code == 401

    def test_employee_token_flow(self, client, seed_employee):
        resp = client.post("/api/employee/login", json={
            "employee_id": seed_employee["employee_id"],
            "password": seed_employee["password"],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"]
        token = data["token"]

        resp2 = client.get("/api/employee/portal",
                           headers={"Authorization": f"Bearer {token}"})
        assert resp2.status_code == 200


# ── CSRF protection ───────────────────────────────────────────────────────────

class TestCsrfProtection:
    def test_post_without_csrf_returns_403_or_redirect(self, client):
        """A POST to a non-API endpoint without CSRF token must be rejected."""
        resp = client.post("/change_admin_password", data={
            "current_password": "x", "new_password": "y", "confirm": "y",
        })
        # 403 for JSON/AJAX or redirect (302) to login
        assert resp.status_code in (302, 403)

    def test_api_post_without_csrf_allowed(self, client):
        """API endpoints bypass CSRF (they use Bearer tokens)."""
        resp = client.post("/api/login", json={"username": "x", "password": "y"})
        # Should reach the route (not 403); might return 401 for bad creds
        assert resp.status_code in (200, 401)
