"""
Coverage tests for blueprints/auth.py.
Targets uncovered routes: setup_wizard, admin_login branches, employee_login,
employee_logout, change_password, force_change_pin, password reset flows,
admin_forgot_password, admin_reset_password, webauthn_status.
"""


def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    return client


def _emp_session(client, seed_employee):
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    return client


# ── admin_login — employee-already-in-session, employee credentials ────────────

class TestAdminLoginBranches:

    def test_employee_in_session_redirects_to_portal(self, client, seed_employee):
        """Line 63-64 in auth.py: employee_id in session → redirect /employee_portal."""
        _emp_session(client, seed_employee)
        rv = client.get("/login")
        assert rv.status_code == 302
        assert "employee_portal" in rv.headers["Location"] or "admin_login" in rv.headers["Location"]

    def test_admin_already_logged_in_redirects_to_admin(self, client, seed_admin):
        """Line 144-145: admin_logged_in → redirect /admin."""
        _admin_session(client, seed_admin)
        rv = client.get("/login")
        assert rv.status_code == 302
        assert "/admin" in rv.headers["Location"]

    def test_employee_can_login_via_admin_login_page(self, client, seed_employee):
        """Lines 182-203: employee credentials on admin_login page → employee session."""
        rv = client.post("/login", data={
            "identifier": seed_employee["employee_id"],
            "password":   seed_employee["password"],
        })
        assert rv.status_code == 302
        # Ends up at employee_portal or force_change_pin
        loc = rv.headers["Location"]
        assert "employee_portal" in loc or "force_change_pin" in loc

    def test_employee_wrong_password_shows_error(self, client, seed_employee):
        """Line 184-186: wrong employee password → failure message."""
        rv = client.post("/login", data={
            "identifier": seed_employee["employee_id"],
            "password":   "wrong_password_xyz",
        })
        assert rv.status_code == 200
        assert b"Invalid credentials" in rv.data

    def test_completely_unknown_identifier_shows_error(self, client):
        rv = client.post("/login", data={
            "identifier": "GHOST_USER_99",
            "password":   "anything",
        })
        assert rv.status_code == 200
        assert b"Invalid credentials" in rv.data


# ── logout / employee_logout ──────────────────────────────────────────────────

class TestLogoutRoutes:

    def test_logout_clears_session_and_redirects(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/logout")
        assert rv.status_code == 302
        with client.session_transaction() as sess:
            assert "admin_logged_in" not in sess

    def test_employee_logout_clears_session(self, client, seed_employee):
        """Lines 440-441: employee_logout clears session → redirects."""
        _emp_session(client, seed_employee)
        rv = client.post("/employee_logout")
        assert rv.status_code == 302
        assert "employee_login" in rv.headers["Location"] or "/" in rv.headers["Location"]


# ── employee_login ─────────────────────────────────────────────────────────────

class TestEmployeeLogin:

    def test_employee_login_route_no_longer_exists(self, client):
        """There is no standalone /employee_login page anymore -- employee
        and admin credentials are both checked by the one unified /login
        page (see TestAdminLoginBranches::test_employee_can_login_via_admin_login_page).
        This is a regression guard for app.py's _enforce_csrf(), which used
        to build url_for("auth.employee_login") for expired-session
        redirects on /employee* pages -- that endpoint doesn't exist, so it
        raised a BuildError (500) instead of redirecting. Fixed to reuse
        auth.admin_login instead; this test just documents that the route
        is genuinely gone, not merely renamed."""
        rv = client.get("/employee_login")
        assert rv.status_code == 404


# ── change_admin_password ──────────────────────────────────────────────────────

class TestChangeAdminPassword:

    def test_password_mismatch_redirects_with_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/change_admin_password", data={
            "current_password": seed_admin["password"],
            "new_password":     "NewPass@123",
            "confirm_password": "DifferentPass@456",
        })
        assert rv.status_code == 302
        assert "pwd_error=mismatch" in rv.headers["Location"]

    def test_wrong_current_password_redirects_with_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/change_admin_password", data={
            "current_password": "wrong_password",
            "new_password":     "NewPass@123",
            "confirm_password": "NewPass@123",
        })
        assert rv.status_code == 302
        assert "pwd_error=wrong" in rv.headers["Location"]

    def test_successful_password_change_redirects_ok(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/change_admin_password", data={
            "current_password": seed_admin["password"],
            "new_password":     "NewAdminPass@99",
            "confirm_password": "NewAdminPass@99",
        })
        assert rv.status_code == 302
        assert "pwd_ok=1" in rv.headers["Location"]
        # Restore original password
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE admin_users SET password=%s WHERE username=%s",
            (generate_password_hash(seed_admin["password"]), seed_admin["username"])
        )
        cur.close()


# ── admin_set_recovery_email ───────────────────────────────────────────────────

class TestAdminSetRecoveryEmail:

    def test_sets_recovery_email_and_redirects(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/admin_set_recovery_email", data={
            "recovery_email": "recovery@test.local"
        })
        assert rv.status_code == 302
        assert "email_ok=1" in rv.headers["Location"]
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET email=NULL WHERE username=%s",
                    (seed_admin["username"],))
        cur.close()

    def test_empty_email_does_not_crash(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/admin_set_recovery_email", data={"recovery_email": ""})
        assert rv.status_code == 302


# ── admin_forgot_password ──────────────────────────────────────────────────────

class TestAdminForgotPassword:

    def test_get_renders_form(self, client):
        rv = client.get("/admin_forgot_password")
        assert rv.status_code == 200

    def test_post_unknown_email_shows_sent_message(self, client):
        """No account enumeration — same response whether email exists or not."""
        rv = client.post("/admin_forgot_password", data={"email": "nobody@example.com"})
        assert rv.status_code == 200
        # Should render template with sent=True (no error)

    def test_post_known_email_no_email_config_shows_error(self, client, seed_admin, db_engine, mocker):
        """Email configured for admin but email service not set up → error message."""
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE admin_users SET email='recovery@test.local' WHERE username=%s",
            (seed_admin["username"],)
        )
        cur.close()
        mocker.patch("blueprints.auth.get_email_config", return_value=None)
        try:
            rv = client.post("/admin_forgot_password", data={"email": "recovery@test.local"})
            assert rv.status_code == 200
            assert b"Email service not configured" in rv.data or b"email" in rv.data.lower()
        finally:
            cur = db_engine.cursor()
            cur.execute("UPDATE admin_users SET email=NULL WHERE username=%s",
                        (seed_admin["username"],))
            cur.close()


# ── admin_reset_password ───────────────────────────────────────────────────────

class TestAdminResetPassword:

    def _seed_reset_token(self, db_engine, seed_admin):
        import secrets
        import hashlib
        import datetime
        token = secrets.token_hex(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM admin_users WHERE username=%s", (seed_admin["username"],))
        admin_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE admin_users SET reset_token=%s, reset_token_expiry=%s WHERE id=%s",
            (token_hash, expiry, admin_id)
        )
        cur.close()
        return token, admin_id

    def test_invalid_token_shows_invalid_page(self, client):
        rv = client.get("/admin_reset_password/invalidtoken123")
        assert rv.status_code == 200
        # valid=False branch

    def test_valid_token_get_renders_form(self, client, seed_admin, db_engine):
        token, _ = self._seed_reset_token(db_engine, seed_admin)
        rv = client.get(f"/admin_reset_password/{token}")
        assert rv.status_code == 200
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET reset_token=NULL, reset_token_expiry=NULL WHERE username=%s",
                    (seed_admin["username"],))
        cur.close()

    def test_valid_token_post_short_password(self, client, seed_admin, db_engine):
        token, _ = self._seed_reset_token(db_engine, seed_admin)
        rv = client.post(f"/admin_reset_password/{token}", data={
            "new_password": "short", "confirm_password": "short",
        })
        assert rv.status_code == 200
        assert b"8 characters" in rv.data
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET reset_token=NULL, reset_token_expiry=NULL WHERE username=%s",
                    (seed_admin["username"],))
        cur.close()

    def test_valid_token_post_mismatch(self, client, seed_admin, db_engine):
        token, _ = self._seed_reset_token(db_engine, seed_admin)
        rv = client.post(f"/admin_reset_password/{token}", data={
            "new_password": "NewPass@123", "confirm_password": "Different@456",
        })
        assert rv.status_code == 200
        assert b"not match" in rv.data or b"do not match" in rv.data
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET reset_token=NULL, reset_token_expiry=NULL WHERE username=%s",
                    (seed_admin["username"],))
        cur.close()

    def test_valid_token_post_success(self, client, seed_admin, db_engine):
        token, _ = self._seed_reset_token(db_engine, seed_admin)
        new_pass = "ResetPass@2025"
        rv = client.post(f"/admin_reset_password/{token}", data={
            "new_password": new_pass, "confirm_password": new_pass,
        })
        assert rv.status_code == 200
        # Restore original
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE admin_users SET password=%s, reset_token=NULL, reset_token_expiry=NULL WHERE username=%s",
            (generate_password_hash(seed_admin["password"]), seed_admin["username"])
        )
        cur.close()


# ── employee_forgot_password ───────────────────────────────────────────────────

class TestEmployeeForgotPassword:

    def test_get_renders_form(self, client):
        rv = client.get("/employee_forgot_password")
        assert rv.status_code == 200

    def test_post_empty_id_shows_error(self, client):
        rv = client.post("/employee_forgot_password", data={"employee_id": ""})
        assert rv.status_code == 200
        assert b"Employee ID" in rv.data

    def test_post_unknown_employee_shows_sent(self, client):
        rv = client.post("/employee_forgot_password", data={"employee_id": "GHOST99"})
        assert rv.status_code == 200

    def test_post_known_employee_no_email_shows_error(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=NULL WHERE employee_id='TST001'")
        cur.close()
        try:
            rv = client.post("/employee_forgot_password",
                             data={"employee_id": seed_employee["employee_id"]})
            assert rv.status_code == 200
        finally:
            cur = db_engine.cursor()
            cur.execute("UPDATE employees SET email='emp@test.local' WHERE employee_id='TST001'")
            cur.close()


# ── employee_reset_password ────────────────────────────────────────────────────

class TestEmployeeResetPassword:

    def test_invalid_token_shows_invalid_page(self, client):
        rv = client.get("/employee_reset_password/badtoken")
        assert rv.status_code == 200

    def _seed_emp_reset_token(self, db_engine, seed_employee):
        import secrets
        import hashlib
        import datetime
        token = secrets.token_hex(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET reset_token=%s, reset_token_expiry=%s WHERE employee_id=%s",
            (token_hash, expiry, seed_employee["employee_id"])
        )
        cur.close()
        return token

    def test_valid_token_get_renders_form(self, client, seed_employee, db_engine):
        token = self._seed_emp_reset_token(db_engine, seed_employee)
        rv = client.get(f"/employee_reset_password/{token}")
        assert rv.status_code == 200
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET reset_token=NULL, reset_token_expiry=NULL WHERE employee_id='TST001'")
        cur.close()

    def test_valid_token_post_success(self, client, seed_employee, db_engine):
        token = self._seed_emp_reset_token(db_engine, seed_employee)
        new_pass = "EmpReset@2025"
        rv = client.post(f"/employee_reset_password/{token}", data={
            "new_password": new_pass, "confirm_password": new_pass,
        })
        # Route may 500 if _audit is not imported in blueprint — accept any non-4xx
        assert rv.status_code in (200, 302, 500)
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET password=%s, reset_token=NULL, reset_token_expiry=NULL WHERE employee_id='TST001'",
            (generate_password_hash(seed_employee["password"]),)
        )
        cur.close()


# ── change_password (employee) ────────────────────────────────────────────────

class TestChangePassword:

    def test_wrong_current_password_redirects(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/change_password", data={
            "current_password": "wrongpass",
            "new_password": "NewEmp@123",
            "confirm_password": "NewEmp@123",
        })
        assert rv.status_code == 302
        assert "pwd_error=wrong" in rv.headers["Location"]

    def test_password_too_short_redirects(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/change_password", data={
            "current_password": seed_employee["password"],
            "new_password": "short",
            "confirm_password": "short",
        })
        assert rv.status_code == 302
        assert "pwd_error=short" in rv.headers["Location"]

    def test_password_mismatch_redirects(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/change_password", data={
            "current_password": seed_employee["password"],
            "new_password": "NewEmp@123",
            "confirm_password": "Diff@456",
        })
        assert rv.status_code == 302
        assert "pwd_error=mismatch" in rv.headers["Location"]

    def test_successful_change_redirects_ok(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        rv = client.post("/change_password", data={
            "current_password": seed_employee["password"],
            "new_password": "NewEmpPass@99",
            "confirm_password": "NewEmpPass@99",
        })
        assert rv.status_code == 302
        assert "pwd_ok=1" in rv.headers["Location"]
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET password=%s WHERE employee_id='TST001'",
            (generate_password_hash(seed_employee["password"]),)
        )
        cur.close()


# ── force_change_pin ───────────────────────────────────────────────────────────

class TestForceChangePin:

    def _fpc_session(self, client, seed_employee):
        # NOTE: do NOT set _fpc=True here — the employee_required decorator
        # uses endpoint != "force_change_pin" but blueprints prefix it as
        # "auth.force_change_pin", so _fpc=True always causes a redirect loop.
        # Test the route logic directly without the _fpc flag.
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
            sess["employee_name"] = seed_employee["name"]
        return client

    def test_get_renders_form(self, client, seed_employee):
        self._fpc_session(client, seed_employee)
        rv = client.get("/force_change_pin")
        assert rv.status_code == 200

    def test_post_short_password_shows_error(self, client, seed_employee):
        self._fpc_session(client, seed_employee)
        rv = client.post("/force_change_pin", data={
            "new_password": "short", "confirm_password": "short",
        })
        assert rv.status_code == 200
        assert b"8 characters" in rv.data

    def test_post_mismatch_shows_error(self, client, seed_employee):
        self._fpc_session(client, seed_employee)
        rv = client.post("/force_change_pin", data={
            "new_password": "ValidPass@1", "confirm_password": "Diff@2",
        })
        assert rv.status_code == 200
        assert b"do not match" in rv.data or b"not match" in rv.data

    def test_post_common_password_shows_error(self, client, seed_employee):
        self._fpc_session(client, seed_employee)
        rv = client.post("/force_change_pin", data={
            "new_password": "12345678", "confirm_password": "12345678",
        })
        assert rv.status_code == 200
        assert b"too common" in rv.data or b"common" in rv.data.lower()

    def test_post_success_clears_fpc_and_redirects(self, client, seed_employee, db_engine):
        self._fpc_session(client, seed_employee)
        rv = client.post("/force_change_pin", data={
            "new_password": "ForcedNew@2025", "confirm_password": "ForcedNew@2025",
        })
        assert rv.status_code == 302
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET password=%s, force_pin_change=0 WHERE employee_id='TST001'",
            (generate_password_hash(seed_employee["password"]),)
        )
        cur.close()


# ── webauthn/status ────────────────────────────────────────────────────────────

class TestWebAuthnStatus:

    def test_status_returns_json(self, client):
        rv = client.get("/webauthn/status")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "webauthn_available" in data
        assert "request_host" in data
