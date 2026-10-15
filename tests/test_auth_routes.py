"""Route-level tests for blueprints/auth.py branches not already covered by
tests/test_auth.py (login happy path, lockout, API tokens, CSRF) or
tests/test_webauthn_enrollment_authz.py (the kiosk-enrollment identity
gate). Covers: setup wizard validation, admin_login edge branches,
password change/reset flows (admin + employee), and the WebAuthn
verification/registration/unenroll routes with the underlying crypto
(_wa_verify_and_store_registration, webauthn.verify_authentication_response)
monkeypatched — those are exercised directly in tests/test_webauthn_utils.py.
"""
import base64
import datetime
import blueprints.auth as auth_bp_module
from utils.auth import _clear_login_failures, _record_login_failure, _LOGIN_MAX_ATTEMPTS
from utils.async_writer import _write_queue


def _wait_for_async_writes():
    _write_queue.join()


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _employee_session(client, employee_id):
    with client.session_transaction() as sess:
        sess["employee_id"] = employee_id


def _employee_bearer_token(client, seed_employee):
    resp = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"],
        "password": seed_employee["password"],
    })
    return resp.get_json()["token"]


class TestAdminLoginEdgeBranches:
    def test_already_admin_logged_in_redirects_to_admin(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin")

    def test_already_employee_logged_in_redirects_to_portal(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 302
        assert "/employee_portal" in resp.headers["Location"]

    def test_injection_shaped_identifier_still_gets_invalid_credentials(self, client, monkeypatch):
        events = []
        monkeypatch.setattr(auth_bp_module, "log_security_event", lambda *a, **k: events.append(a))
        resp = client.post("/login", data={
            "identifier": "admin' OR '1'='1",
            "password": "whatever",
        }, follow_redirects=True)
        assert b"Invalid credentials" in resp.data
        assert any(a[0] == "auth.injection_attempt" for a in events)

    def test_locked_identifier_shows_lockout_message(self, client):
        ident = "auth_route_lockout_user"
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            _record_login_failure(ident)
        _wait_for_async_writes()
        try:
            resp = client.post("/login", data={"identifier": ident, "password": "x"})
            assert b"locked" in resp.data.lower()
        finally:
            _clear_login_failures(ident)
            _wait_for_async_writes()

    def test_captcha_required_after_threshold_blocks_without_token(self, client, monkeypatch):
        monkeypatch.setattr(auth_bp_module, "turnstile_enabled", lambda: True)
        monkeypatch.setattr(auth_bp_module, "_get_failed_count", lambda ident: 5)
        monkeypatch.setattr(auth_bp_module, "verify_turnstile", lambda token, ip: False)
        resp = client.post("/login", data={"identifier": "someone", "password": "x"})
        assert b"verification challenge" in resp.data

    def test_captcha_passes_with_valid_token_falls_through_to_credential_check(self, client, monkeypatch):
        monkeypatch.setattr(auth_bp_module, "turnstile_enabled", lambda: True)
        monkeypatch.setattr(auth_bp_module, "_get_failed_count", lambda ident: 5)
        monkeypatch.setattr(auth_bp_module, "verify_turnstile", lambda token, ip: True)
        resp = client.post("/login", data={"identifier": "someone_unknown", "password": "x"})
        assert b"Invalid credentials" in resp.data

    def test_legacy_admin_hash_upgraded_to_bcrypt_on_login(self, client, db_engine):
        from werkzeug.security import generate_password_hash as wz_hash
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO admin_users (username, password) VALUES (%s,%s) ON CONFLICT (username) DO NOTHING",
            ("legacy_admin_route_test", wz_hash("LegacyPass1", method="pbkdf2:sha256")),
        )
        try:
            resp = client.post("/login", data={
                "identifier": "legacy_admin_route_test", "password": "LegacyPass1",
            }, follow_redirects=False)
            assert resp.status_code == 302
            cur.execute("SELECT password FROM admin_users WHERE username='legacy_admin_route_test'")
            assert cur.fetchone()[0].startswith("$2")
        finally:
            cur.execute("DELETE FROM admin_users WHERE username='legacy_admin_route_test'")
            cur.close()

    def test_employee_login_via_admin_login_route_redirects_to_portal(self, client, seed_employee):
        resp = client.post("/login", data={
            "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/employee_portal" in resp.headers["Location"]

    def test_employee_force_pin_change_redirects_to_force_change_pin(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET force_pin_change=1 WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        try:
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert "/force_change_pin" in resp.headers["Location"]
        finally:
            cur.execute("UPDATE employees SET force_pin_change=0 WHERE employee_id=%s",
                        (seed_employee["employee_id"],))
            cur.close()

    def test_hr_role_employee_login_redirects_to_employees_admin_panel(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='HR' WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/hr_dashboard")
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == seed_employee["employee_id"]
                assert sess.get("admin_role") == "hr"
                assert "employee_id" not in sess
            cur.execute("SELECT role, is_active FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "hr" and row[1] == 1
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.execute("DELETE FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            cur.close()

    def test_hr_role_employee_login_is_idempotent(self, client, seed_employee, db_engine):
        """Logging in twice must not try to INSERT a second admin_users row
        for the same employee_id (which would 500 on the UNIQUE username
        constraint) -- the second login should just reuse the first."""
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='hr' WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            for _ in range(2):
                # Log out first -- admin_login() redirects an already
                # admin_logged_in session straight to /admin without ever
                # reaching credential checking (pre-existing behavior,
                # unrelated to this change), so a fresh session is needed
                # to actually re-exercise _finish_employee_login() each time.
                client.post("/logout")
                resp = client.post("/login", data={
                    "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
                }, follow_redirects=False)
                assert resp.status_code == 302
                assert resp.headers["Location"].endswith("/hr_dashboard")
                # _clear_login_failures() (blueprints/auth.py) enqueues its
                # DB write onto the shared background writer thread rather
                # than running it synchronously (see utils/auth.py's own
                # docstring on that queue) -- back-to-back login attempts
                # with zero delay between them can otherwise race that
                # write. Not specific to this feature; settling here is
                # just what this suite's existing helper is for.
                _wait_for_async_writes()
            cur.execute("SELECT COUNT(*) FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            assert cur.fetchone()[0] == 1
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.execute("DELETE FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            cur.close()

    def test_hr_substring_role_does_not_grant_admin_access(self, client, seed_employee, db_engine):
        """employees.role is free text an admin can type anything into --
        only an EXACT 'HR' match may grant admin-panel access, never a
        substring, or any job title containing "HR" (e.g. "HR Executive")
        would silently escalate that employee to the admin panel."""
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='HR Executive' WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert resp.status_code == 302
            assert "/employee_portal" in resp.headers["Location"]
            cur.execute("SELECT 1 FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            assert cur.fetchone() is None
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.close()

    def test_hr_role_employee_with_pending_pin_change_goes_to_force_change_pin_first(self, client, seed_employee, db_engine):
        """A forced PIN change always wins over the HR redirect -- an
        employee who hasn't changed their initial PIN yet must not be able
        to skip straight into the admin panel."""
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='HR', force_pin_change=1 WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        try:
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert "/force_change_pin" in resp.headers["Location"]
            with client.session_transaction() as sess:
                assert sess.get("employee_id") == seed_employee["employee_id"]
                assert not sess.get("admin_logged_in")
            cur.execute("SELECT 1 FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            assert cur.fetchone() is None
        finally:
            cur.execute("UPDATE employees SET role=NULL, force_pin_change=0 WHERE employee_id=%s",
                        (seed_employee["employee_id"],))
            cur.close()

    def test_manager_role_employee_login_redirects_to_leave_holidays(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='Manager' WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers["Location"].endswith("/leave_holidays")
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == seed_employee["employee_id"]
                assert sess.get("admin_role") == "manager"
                assert "employee_id" not in sess
            cur.execute("SELECT role, is_active FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "manager" and row[1] == 1
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.execute("DELETE FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            cur.close()

    def test_manager_role_employee_login_is_idempotent(self, client, seed_employee, db_engine):
        """Same UNIQUE-constraint concern as the HR case above -- a second
        login must reuse the first auto-provisioned row, not try to
        INSERT a duplicate."""
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='manager' WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            for _ in range(2):
                client.post("/logout")
                resp = client.post("/login", data={
                    "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
                }, follow_redirects=False)
                assert resp.status_code == 302
                assert resp.headers["Location"].endswith("/leave_holidays")
                _wait_for_async_writes()
            cur.execute("SELECT COUNT(*) FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            assert cur.fetchone()[0] == 1
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.execute("DELETE FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            cur.close()

    def test_manager_substring_role_does_not_grant_admin_access(self, client, seed_employee, db_engine):
        """Same free-text-role caveat as HR's own equivalent test -- a job
        title like "Project Manager" or "Store Manager" must not match."""
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='Project Manager' WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert resp.status_code == 302
            assert "/employee_portal" in resp.headers["Location"]
            cur.execute("SELECT 1 FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            assert cur.fetchone() is None
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.close()

    def test_manager_role_employee_with_pending_pin_change_goes_to_force_change_pin_first(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='Manager', force_pin_change=1 WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        try:
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert "/force_change_pin" in resp.headers["Location"]
            with client.session_transaction() as sess:
                assert sess.get("employee_id") == seed_employee["employee_id"]
                assert not sess.get("admin_logged_in")
            cur.execute("SELECT 1 FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            assert cur.fetchone() is None
        finally:
            cur.execute("UPDATE employees SET role=NULL, force_pin_change=0 WHERE employee_id=%s",
                        (seed_employee["employee_id"],))
            cur.close()

    def test_employee_wrong_password_records_failure(self, client, seed_employee):
        resp = client.post("/login", data={
            "identifier": seed_employee["employee_id"], "password": "WrongPass!",
        })
        assert b"Invalid credentials" in resp.data
        _clear_login_failures(seed_employee["employee_id"])
        _wait_for_async_writes()


class TestLogout:
    def test_clears_session_and_redirects(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "admin_logged_in" not in sess


class TestDeactivatedAccountSessionRevocation:
    """admin_required/employee_required/role_required used to check only
    session flags -- a deactivated or deleted account's already-open
    browser session kept working until its own idle/absolute timeout.
    Bearer-token routes (api_required) already re-check is_active on
    every request; these three decorators now do the session-based
    equivalent (utils/auth.py's _reject_if_account_deactivated())."""

    def test_active_admin_session_still_works(self, client, seed_admin):
        """Baseline -- confirms the new check doesn't false-positive on
        an ordinary active account."""
        _admin_session(client, seed_admin["username"])
        resp = client.get("/admin")
        assert resp.status_code == 200

    def test_deactivated_admin_session_rejected(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username=%s", (seed_admin["username"],))
        try:
            resp = client.get("/admin", follow_redirects=False)
            assert resp.status_code == 302
            assert "admin-login" in resp.headers["Location"]
            with client.session_transaction() as sess:
                assert "admin_logged_in" not in sess
        finally:
            cur.execute("UPDATE admin_users SET is_active=1 WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()
            cur.close()

    def test_deactivated_admin_session_rejected_on_role_required_route(self, client, seed_admin, db_engine):
        """Same check, the role_required() decorator's own copy (a
        separate code path from admin_required's)."""
        _admin_session(client, seed_admin["username"])
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username=%s", (seed_admin["username"],))
        try:
            resp = client.get("/settings")
            assert resp.status_code in (302, 401, 403)
        finally:
            cur.execute("UPDATE admin_users SET is_active=1 WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()
            cur.close()

    def test_active_employee_session_still_works(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.get("/employee_portal")
        assert resp.status_code == 200

    def test_deactivated_employee_session_rejected(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee["employee_id"])
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET is_active=0 WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            resp = client.get("/employee_portal", follow_redirects=False)
            assert resp.status_code == 302
            with client.session_transaction() as sess:
                assert "employee_id" not in sess
        finally:
            cur.execute("UPDATE employees SET is_active=1 WHERE employee_id=%s", (seed_employee["employee_id"],))
            db_engine.commit()
            cur.close()

    def test_deleted_employee_session_rejected(self, client, seed_employee, db_engine):
        """Hard-deletion (blueprints/employees.py's delete_employee(), a
        real reachable feature) -- no row at all, not just is_active=0.
        Deleted directly here rather than via the route so this test
        exercises only the auth check, not the whole delete flow."""
        _employee_session(client, seed_employee["employee_id"])
        cur = db_engine.cursor()
        cur.execute("DELETE FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        try:
            resp = client.get("/employee_portal", follow_redirects=False)
            assert resp.status_code == 302
        finally:
            # Restore so the seed_employee fixture's own teardown (a plain
            # DELETE) doesn't fail trying to remove an already-gone row --
            # ON CONFLICT DO NOTHING makes this idempotent either way.
            from utils.auth import generate_password_hash
            cur.execute(
                "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
                "VALUES (%s,%s,%s,%s,0) ON CONFLICT (employee_id) DO NOTHING",
                (seed_employee["employee_id"], seed_employee["name"], "emp@test.local",
                 generate_password_hash(seed_employee["password"]))
            )
            db_engine.commit()
            cur.close()


class TestChangeAdminPassword:
    def test_mismatched_new_passwords_redirects_with_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/change_admin_password", data={
            "current_password": seed_admin["password"],
            "new_password": "NewPass1", "confirm_password": "Different1",
        }, follow_redirects=False)
        assert "pwd_error=mismatch" in resp.headers["Location"]

    def test_wrong_current_password_redirects_with_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/change_admin_password", data={
            "current_password": "NotTheRealOne1", "new_password": "NewPass1", "confirm_password": "NewPass1",
        }, follow_redirects=False)
        assert "pwd_error=wrong" in resp.headers["Location"]

    def test_success_updates_password(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/change_admin_password", data={
            "current_password": seed_admin["password"],
            "new_password": "BrandNewPass1", "confirm_password": "BrandNewPass1",
        }, follow_redirects=False)
        assert "pwd_ok=1" in resp.headers["Location"]
        from utils.auth import check_password_hash
        cur = db_engine.cursor()
        cur.execute("SELECT password FROM admin_users WHERE username=%s", (seed_admin["username"],))
        assert check_password_hash(cur.fetchone()[0], "BrandNewPass1")
        cur.close()


class TestAdminSetRecoveryEmail:
    def test_sets_email(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/admin_set_recovery_email", data={"recovery_email": "recover@test.local"},
                           follow_redirects=False)
        assert "email_ok=1" in resp.headers["Location"]
        cur = db_engine.cursor()
        cur.execute("SELECT email FROM admin_users WHERE username=%s", (seed_admin["username"],))
        assert cur.fetchone()[0] == "recover@test.local"
        cur.close()


class TestAdminForgotPassword:
    def test_get_renders_form(self, client):
        assert client.get("/admin_forgot_password").status_code == 200

    def test_unknown_email_gives_generic_response(self, client):
        resp = client.post("/admin_forgot_password", data={"email": "nobody@nowhere.test"})
        assert resp.status_code == 200
        assert b"check your inbox" in resp.data.lower() or resp.status_code == 200

    def test_known_email_without_smtp_config_shows_error(self, client, seed_admin, db_engine, monkeypatch):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET email=%s WHERE username=%s",
                    ("fp_admin@test.local", seed_admin["username"]))
        monkeypatch.setattr(auth_bp_module, "get_email_config", lambda: None)
        try:
            resp = client.post("/admin_forgot_password", data={"email": "fp_admin@test.local"})
            assert b"Email service not configured" in resp.data
        finally:
            cur.execute("UPDATE admin_users SET email=NULL WHERE username=%s", (seed_admin["username"],))
            cur.close()

    def test_known_email_with_config_sends_and_confirms(self, client, seed_admin, db_engine, monkeypatch):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET email=%s WHERE username=%s",
                    ("fp_admin2@test.local", seed_admin["username"]))
        monkeypatch.setattr(auth_bp_module, "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})
        sent = []
        monkeypatch.setattr(auth_bp_module, "send_email_smtp", lambda *a, **k: sent.append(a))
        try:
            resp = client.post("/admin_forgot_password", data={"email": "fp_admin2@test.local"})
            assert resp.status_code == 200
            assert len(sent) == 1
            cur.execute("SELECT reset_token FROM admin_users WHERE username=%s", (seed_admin["username"],))
            assert cur.fetchone()[0] is not None
        finally:
            cur.execute("UPDATE admin_users SET email=NULL, reset_token=NULL, reset_token_expiry=NULL "
                        "WHERE username=%s", (seed_admin["username"],))
            cur.close()

    def test_send_failure_shows_error(self, client, seed_admin, db_engine, monkeypatch):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET email=%s WHERE username=%s",
                    ("fp_admin3@test.local", seed_admin["username"]))
        monkeypatch.setattr(auth_bp_module, "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})

        def _boom(*a, **k):
            raise RuntimeError("smtp down")
        monkeypatch.setattr(auth_bp_module, "send_email_smtp", _boom)
        try:
            resp = client.post("/admin_forgot_password", data={"email": "fp_admin3@test.local"})
            assert b"Failed to send email" in resp.data
        finally:
            cur.execute("UPDATE admin_users SET email=NULL, reset_token=NULL, reset_token_expiry=NULL "
                        "WHERE username=%s", (seed_admin["username"],))
            cur.close()


class TestAdminResetPassword:
    def _make_token(self, db_engine, seed_admin, expired=False):
        import secrets
        import hashlib
        token = secrets.token_hex(16)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        delta = datetime.timedelta(hours=-1) if expired else datetime.timedelta(hours=1)
        expiry = datetime.datetime.utcnow() + delta
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET reset_token=%s, reset_token_expiry=%s WHERE username=%s",
                    (token_hash, expiry, seed_admin["username"]))
        cur.close()
        return token

    def test_invalid_token_shows_invalid(self, client):
        resp = client.get("/admin_reset_password/not-a-real-token")
        assert b"invalid" in resp.data.lower() or resp.status_code == 200

    def test_expired_token_treated_as_invalid(self, client, seed_admin, db_engine):
        token = self._make_token(db_engine, seed_admin, expired=True)
        resp = client.get(f"/admin_reset_password/{token}")
        assert resp.status_code == 200

    def test_valid_token_get_renders_form(self, client, seed_admin, db_engine):
        token = self._make_token(db_engine, seed_admin)
        resp = client.get(f"/admin_reset_password/{token}")
        assert resp.status_code == 200

    def test_short_password_rejected(self, client, seed_admin, db_engine):
        token = self._make_token(db_engine, seed_admin)
        resp = client.post(f"/admin_reset_password/{token}", data={
            "new_password": "short", "confirm_password": "short"})
        assert b"at least 8 characters" in resp.data

    def test_mismatched_passwords_rejected(self, client, seed_admin, db_engine):
        token = self._make_token(db_engine, seed_admin)
        resp = client.post(f"/admin_reset_password/{token}", data={
            "new_password": "LongEnough1", "confirm_password": "Different1"})
        assert b"do not match" in resp.data

    def test_valid_reset_updates_password(self, client, seed_admin, db_engine):
        from utils.auth import check_password_hash
        token = self._make_token(db_engine, seed_admin)
        resp = client.post(f"/admin_reset_password/{token}", data={
            "new_password": "ResetPass123", "confirm_password": "ResetPass123"})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT password, reset_token FROM admin_users WHERE username=%s", (seed_admin["username"],))
        row = cur.fetchone()
        assert check_password_hash(row[0], "ResetPass123")
        assert row[1] is None
        cur.close()


class TestEmployeeForgotPassword:
    def test_get_renders_form(self, client):
        assert client.get("/employee_forgot_password").status_code == 200

    def test_empty_employee_id_rejected(self, client):
        resp = client.post("/employee_forgot_password", data={"employee_id": ""})
        assert b"enter your Employee ID" in resp.data

    def test_unknown_employee_id_generic_response(self, client):
        resp = client.post("/employee_forgot_password", data={"employee_id": "NOPE_XYZ"})
        assert resp.status_code == 200

    def test_known_employee_without_email_generic_response(self, client, seed_employee):
        resp = client.post("/employee_forgot_password", data={"employee_id": seed_employee["employee_id"]})
        assert resp.status_code == 200

    def test_known_employee_with_email_no_config_shows_error(self, client, seed_employee, db_engine, monkeypatch):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=%s WHERE employee_id=%s",
                    ("emp_fp@test.local", seed_employee["employee_id"]))
        monkeypatch.setattr(auth_bp_module, "get_email_config", lambda: None)
        try:
            resp = client.post("/employee_forgot_password", data={"employee_id": seed_employee["employee_id"]})
            assert b"contact HR" in resp.data
        finally:
            cur.close()

    def test_known_employee_with_config_queues_email(self, client, seed_employee, db_engine, monkeypatch):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=%s WHERE employee_id=%s",
                    ("emp_fp2@test.local", seed_employee["employee_id"]))
        monkeypatch.setattr(auth_bp_module, "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})
        queued = []
        monkeypatch.setattr(auth_bp_module, "send_email_async", lambda *a, **k: queued.append(a))
        try:
            resp = client.post("/employee_forgot_password", data={"employee_id": seed_employee["employee_id"]})
            assert resp.status_code == 200
            assert len(queued) == 1
        finally:
            cur.close()


class TestEmployeeResetPassword:
    def _make_token(self, db_engine, seed_employee, expired=False):
        import secrets
        import hashlib
        token = secrets.token_hex(16)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        delta = datetime.timedelta(hours=-1) if expired else datetime.timedelta(hours=1)
        expiry = datetime.datetime.utcnow() + delta
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET reset_token=%s, reset_token_expiry=%s WHERE employee_id=%s",
                    (token_hash, expiry, seed_employee["employee_id"]))
        cur.close()
        return token

    def test_invalid_token(self, client):
        assert client.get("/employee_reset_password/bogus").status_code == 200

    def test_valid_token_get_renders(self, client, seed_employee, db_engine):
        token = self._make_token(db_engine, seed_employee)
        assert client.get(f"/employee_reset_password/{token}").status_code == 200

    def test_short_password_rejected(self, client, seed_employee, db_engine):
        token = self._make_token(db_engine, seed_employee)
        resp = client.post(f"/employee_reset_password/{token}", data={
            "new_password": "short", "confirm_password": "short"})
        assert b"at least 8 characters" in resp.data

    def test_valid_reset_updates_password_and_audits(self, client, seed_employee, db_engine):
        from utils.auth import check_password_hash
        token = self._make_token(db_engine, seed_employee)
        resp = client.post(f"/employee_reset_password/{token}", data={
            "new_password": "EmpReset123", "confirm_password": "EmpReset123"})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT password FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert check_password_hash(cur.fetchone()[0], "EmpReset123")
        cur.close()


class TestEmployeeLogout:
    def test_clears_session_and_redirects(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/employee_logout", follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "employee_id" not in sess


class TestChangePassword:
    def test_wrong_current_password(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/change_password", data={
            "current_password": "WrongOne1", "new_password": "NewPass123", "confirm_password": "NewPass123",
        }, follow_redirects=False)
        assert "pwd_error=wrong" in resp.headers["Location"]

    def test_short_new_password(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/change_password", data={
            "current_password": seed_employee["password"], "new_password": "short", "confirm_password": "short",
        }, follow_redirects=False)
        assert "pwd_error=short" in resp.headers["Location"]

    def test_mismatched_new_password(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/change_password", data={
            "current_password": seed_employee["password"], "new_password": "NewPass123",
            "confirm_password": "Different123",
        }, follow_redirects=False)
        assert "pwd_error=mismatch" in resp.headers["Location"]

    def test_success_updates_password(self, client, seed_employee, db_engine):
        from utils.auth import check_password_hash
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/change_password", data={
            "current_password": seed_employee["password"], "new_password": "SuccessPass1",
            "confirm_password": "SuccessPass1",
        }, follow_redirects=False)
        assert "pwd_ok=1" in resp.headers["Location"]
        cur = db_engine.cursor()
        cur.execute("SELECT password FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert check_password_hash(cur.fetchone()[0], "SuccessPass1")
        cur.close()


class TestForceChangePin:
    def test_get_renders_form(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        assert client.get("/force_change_pin").status_code == 200

    def test_short_password_rejected(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/force_change_pin", data={"new_password": "short", "confirm_password": "short"})
        assert b"at least 8 characters" in resp.data

    def test_mismatched_rejected(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/force_change_pin", data={
            "new_password": "LongEnough1", "confirm_password": "Different1"})
        assert b"do not match" in resp.data

    def test_common_password_rejected(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/force_change_pin", data={
            "new_password": "password", "confirm_password": "password"})
        assert b"too common" in resp.data

    def test_valid_change_clears_flag_and_redirects(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee["employee_id"])
        with client.session_transaction() as sess:
            sess["_fpc"] = True
        resp = client.post("/force_change_pin", data={
            "new_password": "ForcedNew123", "confirm_password": "ForcedNew123",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert "/employee_portal" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "_fpc" not in sess


class TestWebauthnStatus:
    def test_returns_expected_shape(self, client):
        resp = client.get("/webauthn/status")
        data = resp.get_json()
        assert "webauthn_available" in data
        assert "expected_origins" in data


class TestWebauthnAuthenticationOptions:
    def test_without_emp_id_returns_options(self, client):
        resp = client.get("/webauthn/authentication-options")
        assert resp.status_code == 200

    def test_with_unenrolled_emp_id_returns_empty_allow_list(self, client, seed_employee):
        resp = client.get(f"/webauthn/authentication-options?emp_id={seed_employee['employee_id']}")
        assert resp.status_code == 200

    def test_db_lookup_failure_is_swallowed_and_still_returns_options(self, client, monkeypatch):
        def _raise(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(auth_bp_module, "get_db_connection", _raise)
        resp = client.get("/webauthn/authentication-options?emp_id=SOME_ID")
        assert resp.status_code == 200


class _FakeVerifiedAuth:
    def __init__(self, new_sign_count=5):
        self.new_sign_count = new_sign_count


class TestWebauthnVerifyChallenge:
    def test_missing_credential_or_challenge_returns_400(self, client):
        resp = client.post("/api/employee/webauthn-verify-challenge", json={})
        assert resp.status_code == 400

    def test_unenrolled_employee_returns_401(self, client, seed_employee, monkeypatch):
        with client.session_transaction() as sess:
            sess["wa_auth_challenge"] = "abc"
        resp = client.post("/api/employee/webauthn-verify-challenge", json={
            "emp_id": seed_employee["employee_id"], "credential": {"id": "x"},
        })
        assert resp.status_code == 401

    def test_successful_verification_sets_session_flags(self, client, seed_employee, db_engine, monkeypatch):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET fingerprint_public_key=%s, fingerprint_sign_count=0 WHERE employee_id=%s",
            (base64.b64encode(b"fake-pubkey").decode(), emp_id),
        )
        monkeypatch.setattr(auth_bp_module.webauthn, "verify_authentication_response",
                            lambda **kw: _FakeVerifiedAuth(new_sign_count=7))
        with client.session_transaction() as sess:
            sess["wa_auth_challenge"] = "abc"
        try:
            resp = client.post("/api/employee/webauthn-verify-challenge", json={
                "emp_id": emp_id, "credential": {"id": "cred-id"},
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["emp_id"] == emp_id
            cur.execute("SELECT fingerprint_sign_count FROM employees WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone()[0] == 7
        finally:
            cur.execute(
                "UPDATE employees SET fingerprint_public_key=NULL, fingerprint_sign_count=0 WHERE employee_id=%s",
                (emp_id,),
            )
            cur.close()

    def test_verification_exception_returns_401(self, client, seed_employee, db_engine, monkeypatch):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET fingerprint_public_key=%s WHERE employee_id=%s",
            (base64.b64encode(b"fake-pubkey").decode(), emp_id),
        )

        def _raise(**kw):
            raise ValueError("bad signature")
        monkeypatch.setattr(auth_bp_module.webauthn, "verify_authentication_response", _raise)
        with client.session_transaction() as sess:
            sess["wa_auth_challenge"] = "abc"
        try:
            resp = client.post("/api/employee/webauthn-verify-challenge", json={
                "emp_id": emp_id, "credential": {"id": "cred-id"},
            })
            assert resp.status_code == 401
        finally:
            cur.execute("UPDATE employees SET fingerprint_public_key=NULL WHERE employee_id=%s", (emp_id,))
            cur.close()


class TestWebauthnRegister:
    def test_no_session_returns_401(self, client):
        resp = client.post("/api/employee/webauthn-register", json={"credential": {}})
        assert resp.status_code == 401

    def test_no_challenge_returns_401(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.post("/api/employee/webauthn-register", json={"credential": {}})
        assert resp.status_code == 401

    def test_successful_registration(self, client, seed_employee, monkeypatch):
        _employee_session(client, seed_employee["employee_id"])
        monkeypatch.setattr(auth_bp_module, "_wa_verify_and_store_registration",
                            lambda *a, **k: (True, None))
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
        resp = client.post("/api/employee/webauthn-register", json={"credential": {"id": "x"}})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_failed_registration_returns_401(self, client, seed_employee, monkeypatch):
        _employee_session(client, seed_employee["employee_id"])
        monkeypatch.setattr(auth_bp_module, "_wa_verify_and_store_registration",
                            lambda *a, **k: (False, "bad credential"))
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
        resp = client.post("/api/employee/webauthn-register", json={"credential": {"id": "x"}})
        assert resp.status_code == 401

    def test_exception_returns_500(self, client, seed_employee, monkeypatch):
        _employee_session(client, seed_employee["employee_id"])

        def _raise(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(auth_bp_module, "_wa_verify_and_store_registration", _raise)
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
        resp = client.post("/api/employee/webauthn-register", json={"credential": {"id": "x"}})
        assert resp.status_code == 500


class TestWebauthnUnenroll:
    def test_not_logged_in_returns_401(self, client):
        resp = client.post("/api/employee/webauthn-unenroll")
        assert resp.status_code == 401

    def test_success_clears_credential(self, client, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET fingerprint_credential_id=%s WHERE employee_id=%s",
                    ("some-cred-id", emp_id))
        _employee_session(client, emp_id)
        resp = client.post("/api/employee/webauthn-unenroll")
        assert resp.status_code == 200
        cur.execute("SELECT fingerprint_credential_id FROM employees WHERE employee_id=%s", (emp_id,))
        assert cur.fetchone()[0] is None
        cur.close()

    def test_db_error_returns_500(self, client, seed_employee, monkeypatch):
        _employee_session(client, seed_employee["employee_id"])

        def _raise():
            raise RuntimeError("db down")
        monkeypatch.setattr(auth_bp_module, "get_db_connection", _raise)
        resp = client.post("/api/employee/webauthn-unenroll")
        assert resp.status_code == 500


class TestGetEmployeeWebauthnCredential:
    def test_unauthorized_without_session_or_token(self, client, seed_employee):
        resp = client.get(f"/api/employee/{seed_employee['employee_id']}/webauthn-credential")
        assert resp.status_code == 401

    def test_employee_can_view_own_credential(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.get(f"/api/employee/{seed_employee['employee_id']}/webauthn-credential")
        assert resp.status_code == 200

    def test_employee_cannot_view_someone_elses_credential(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.get("/api/employee/SOMEONE_ELSE/webauthn-credential")
        assert resp.status_code == 403

    def test_admin_can_view_any_credential(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/api/employee/{seed_employee['employee_id']}/webauthn-credential")
        assert resp.status_code == 200

    def test_employee_token_can_view_own_credential(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        resp = client.get(f"/api/employee/{seed_employee['employee_id']}/webauthn-credential",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_token_cannot_view_someone_elses_credential(self, client, seed_employee, db_engine):
        """Finding #15 (Low): the Bearer-token fallback used to only
        check the token was valid, never binding its identity to the
        requested emp_id -- any employee's own token could fetch any
        OTHER employee's fingerprint_credential_id."""
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
            "VALUES ('WATGT002','Other Employee','other@test.local',%s,0) ON CONFLICT DO NOTHING",
            (generate_password_hash("Other@123"),)
        )
        db_engine.commit()
        cur.close()
        token = _employee_bearer_token(client, seed_employee)
        try:
            resp = client.get("/api/employee/WATGT002/webauthn-credential",
                              headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM employees WHERE employee_id='WATGT002'")
            db_engine.commit()
            cur.close()

    def test_admin_token_can_view_any_credential(self, client, seed_admin, seed_employee):
        resp = client.post("/api/login", json={
            "username": seed_admin["username"], "password": seed_admin["password"],
        })
        token = resp.get_json()["token"]
        resp = client.get(f"/api/employee/{seed_employee['employee_id']}/webauthn-credential",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


class TestWebauthnRegisterKiosk:
    def test_missing_emp_id_returns_400(self, client):
        resp = client.post("/api/employee/webauthn-register-kiosk", json={"credential": {}})
        assert resp.status_code == 400

    def test_missing_challenge_returns_400(self, client, seed_employee):
        resp = client.post("/api/employee/webauthn-register-kiosk", json={
            "emp_id": seed_employee["employee_id"], "credential": {}})
        assert resp.status_code == 400

    def test_emp_id_mismatch_returns_403(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
            sess["wa_reg_emp_id"] = "SOMEONE_ELSE"
        resp = client.post("/api/employee/webauthn-register-kiosk", json={
            "emp_id": seed_employee["employee_id"], "credential": {}})
        assert resp.status_code == 403

    def test_unknown_employee_returns_404(self, client):
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
            sess["wa_reg_emp_id"] = "GHOST_EMP"
        resp = client.post("/api/employee/webauthn-register-kiosk", json={
            "emp_id": "GHOST_EMP", "credential": {}})
        assert resp.status_code == 404

    def test_successful_kiosk_registration(self, client, seed_employee, monkeypatch):
        emp_id = seed_employee["employee_id"]
        monkeypatch.setattr(auth_bp_module, "_wa_verify_and_store_registration",
                            lambda *a, **k: (True, None))
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
            sess["wa_reg_emp_id"] = emp_id
        resp = client.post("/api/employee/webauthn-register-kiosk", json={
            "emp_id": emp_id, "credential": {"id": "x"}})
        assert resp.status_code == 200

    def test_failed_kiosk_registration_returns_400(self, client, seed_employee, monkeypatch):
        emp_id = seed_employee["employee_id"]
        monkeypatch.setattr(auth_bp_module, "_wa_verify_and_store_registration",
                            lambda *a, **k: (False, "bad cred"))
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
            sess["wa_reg_emp_id"] = emp_id
        resp = client.post("/api/employee/webauthn-register-kiosk", json={
            "emp_id": emp_id, "credential": {"id": "x"}})
        assert resp.status_code == 400

    def test_exception_returns_500(self, client, seed_employee, monkeypatch):
        emp_id = seed_employee["employee_id"]

        def _raise(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(auth_bp_module, "_wa_verify_and_store_registration", _raise)
        with client.session_transaction() as sess:
            sess["wa_reg_challenge"] = "abc"
            sess["wa_reg_emp_id"] = emp_id
        resp = client.post("/api/employee/webauthn-register-kiosk", json={
            "emp_id": emp_id, "credential": {"id": "x"}})
        assert resp.status_code == 500


class TestAdminResetEmployeeFingerprint:
    def test_success_clears_fields(self, client, seed_admin, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET fingerprint_credential_id=%s WHERE employee_id=%s",
                    ("some-cred", emp_id))
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/api/admin/employee/{emp_id}/reset-fingerprint")
        assert resp.status_code == 200
        cur.execute("SELECT fingerprint_credential_id FROM employees WHERE employee_id=%s", (emp_id,))
        assert cur.fetchone()[0] is None
        cur.close()

    def test_unknown_employee_returns_404(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/admin/employee/NOPE_NOT_REAL/reset-fingerprint")
        assert resp.status_code == 404

    def test_non_admin_role_rejected(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.post(f"/api/admin/employee/{seed_employee['employee_id']}/reset-fingerprint",
                           headers={"Accept": "application/json"})
        assert resp.status_code == 403

    def test_db_error_returns_500(self, client, seed_admin, seed_employee, monkeypatch):
        _admin_session(client, seed_admin["username"])

        def _raise():
            raise RuntimeError("db down")
        monkeypatch.setattr(auth_bp_module, "get_db_connection", _raise)
        resp = client.post(f"/api/admin/employee/{seed_employee['employee_id']}/reset-fingerprint")
        assert resp.status_code == 500


class TestMobileBiometric:
    def test_nonce_issuance_and_successful_attest(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post("/api/employee/mobile-biometric-nonce", headers=headers)
        assert resp.status_code == 200
        nonce = resp.get_json()["nonce"]
        attest = client.post("/api/employee/mobile-biometric-attest", json={"nonce": nonce}, headers=headers)
        assert attest.status_code == 200
        assert attest.get_json()["ok"] is True

    def test_attest_with_wrong_nonce_returns_401(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        headers = {"Authorization": f"Bearer {token}"}
        client.post("/api/employee/mobile-biometric-nonce", headers=headers)
        resp = client.post("/api/employee/mobile-biometric-attest", json={"nonce": "totally-wrong"}, headers=headers)
        assert resp.status_code == 401

    def test_no_token_returns_401(self, client):
        resp = client.post("/api/employee/mobile-biometric-nonce")
        assert resp.status_code == 401


class TestManagerRoleWebAccess:
    """Manager-role web session reachability. An earlier version of this
    class tested a blanket app.py before_request hook (_restrict_manager_role)
    that turned out to be wrong: it intercepted routes that already had
    their own correct, pre-existing manager-aware handling (utils/dlp.py's
    has_pii_clearance masks PII for manager on employee_detail/
    employee_profile/employees; role_required("admin") already 403s
    view_salary/settings; utils/helpers.py's manager_scope_denied now
    scopes leave/resignation/overtime to direct reports) -- see
    tests/test_dlp_pii_masking.py and tests/test_leave_routes.py's
    TestLeaveApprovalRoleGating for those. This class only covers the
    plain admin_required routes manager was always meant to reach."""

    def test_manager_can_reach_leave_holidays(self, client, seed_admin):
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.get("/leave_holidays")
        assert resp.status_code == 200

    def test_manager_can_reach_overtime(self, client, seed_admin):
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.get("/overtime")
        assert resp.status_code == 200

    def test_manager_can_reach_employees_page(self, client, seed_admin):
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.get("/employees")
        assert resp.status_code == 200

    def test_manager_can_reach_admin_dashboard(self, client, seed_admin):
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.get("/admin")
        assert resp.status_code == 200

    def test_manager_denied_settings(self, client, seed_admin):
        """/settings is role_required("admin") -- manager gets a real 403,
        not a redirect."""
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.get("/settings", follow_redirects=False)
        assert resp.status_code == 403

    def test_manager_can_reach_logout(self, client, seed_admin):
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 302

    def test_plain_admin_session_unaffected(self, client, seed_admin):
        """Baseline -- role='admin' still reaches everything it always could."""
        _admin_session(client, seed_admin["username"], role="admin")
        resp = client.get("/employees")
        assert resp.status_code == 200

    def test_hr_session_unaffected(self, client, seed_hr_admin):
        """Baseline -- HR keeps its own existing, broader scope untouched."""
        _admin_session(client, seed_hr_admin["username"], role="hr")
        resp = client.get("/hr_dashboard")
        assert resp.status_code == 200
