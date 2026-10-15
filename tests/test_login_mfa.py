"""Tests for the mandatory emailed-OTP login step (blueprints/auth.py's
MANDATORY_LOGIN_MFA / _start_login_mfa / /mfa_verify): admin, employee,
and HR logins (HR uses the same general /login as admin -- see
test_hr_role_can_use_admin_login_and_completes_mfa) all stop at a pending
OTP state instead of completing immediately, and only build a real
session once the emailed code is verified.

Disabled globally in tests/conftest.py (MANDATORY_LOGIN_MFA=False), same
reasoning as disabling flask-limiter and MANDATORY_ADMIN_MFA, since almost
the entire suite uses a plain POST /login or /hr_login as its "get an
authenticated session" setup. Re-enabled locally here."""
import time
import pytest


@pytest.fixture
def mandatory_login_mfa_enabled(client):
    client.application.config["MANDATORY_LOGIN_MFA"] = True
    yield
    client.application.config["MANDATORY_LOGIN_MFA"] = False


@pytest.fixture
def sent_codes(monkeypatch):
    """Captures the emailed login code -- the only place the plaintext code
    exists outside the server (it is never in the session cookie)."""
    import blueprints.auth as auth_module
    codes = []
    monkeypatch.setattr(auth_module, "send_mfa_login_email",
                        lambda to, user, role, secret, code: codes.append(code))
    return codes


class TestAdminLoginMfa:
    def test_correct_password_does_not_grant_session_yet(self, client, sent_codes, seed_admin, mandatory_login_mfa_enabled):
        resp = client.post("/login", data={
            "identifier": seed_admin["username"], "password": seed_admin["password"],
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/mfa_verify"
        with client.session_transaction() as sess:
            assert not sess.get("admin_logged_in")
            assert sess.get("mfa_pending") is True
            assert sess.get("mfa_challenge_id")
            assert sess.get("mfa_user") == seed_admin["username"]

    def test_correct_otp_completes_login(self, client, sent_codes, seed_admin, mandatory_login_mfa_enabled):
        client.post("/login", data={
            "identifier": seed_admin["username"], "password": seed_admin["password"],
        })
        code = sent_codes[-1]

        resp = client.post("/mfa_verify", data={"otp_code": code}, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/admin"
        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") is True
            assert sess.get("admin_username") == seed_admin["username"]
            assert not sess.get("mfa_pending")

    def test_wrong_otp_rejected(self, client, sent_codes, seed_admin, mandatory_login_mfa_enabled):
        client.post("/login", data={
            "identifier": seed_admin["username"], "password": seed_admin["password"],
        })
        resp = client.post("/mfa_verify", data={"otp_code": "000000"})
        assert resp.status_code == 200
        assert b"Invalid code" in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("admin_logged_in")

    def test_expired_otp_rejected(self, client, sent_codes, seed_admin, mandatory_login_mfa_enabled):
        client.post("/login", data={
            "identifier": seed_admin["username"], "password": seed_admin["password"],
        })
        code = sent_codes[-1]
        with client.session_transaction() as sess:
            sess["mfa_issued_at"] = time.time() - 301

        resp = client.post("/mfa_verify", data={"otp_code": code})
        assert resp.status_code == 200
        assert b"expired" in resp.data.lower()

    def test_hr_role_can_use_admin_login_and_completes_mfa(self, client, sent_codes, seed_admin, db_engine, mandatory_login_mfa_enabled):
        """HR accounts (blueprints/admin_views.py's /hr_accounts management
        page) use the same general login as admin, and land on /hr_dashboard
        instead of /admin after MFA -- role_required("admin") elsewhere
        scopes them away from admin-only pages regardless."""
        db_engine.cursor().execute("UPDATE admin_users SET role='hr' WHERE username=%s", (seed_admin["username"],))
        db_engine.commit()
        try:
            client.post("/login", data={
                "identifier": seed_admin["username"], "password": seed_admin["password"],
            })
            code = sent_codes[-1]
            resp = client.post("/mfa_verify", data={"otp_code": code}, follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers.get("Location") == "/hr_dashboard"
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_role") == "hr"
        finally:
            db_engine.cursor().execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()

    def test_nonexistent_identifier_gets_the_same_generic_error_as_wrong_password(
            self, client, sent_codes, seed_admin, mandatory_login_mfa_enabled):
        """Username enumeration: now that every account needs its password
        before the emailed-code step, an unknown identifier and a real
        admin with a wrong password must look identical (same status, same
        generic error, no OTP step, no email)."""
        unknown = client.post("/login", data={
            "identifier": "definitely_not_a_real_admin_98765", "password": "whatever",
        }, follow_redirects=False)
        with client.session_transaction() as sess:
            assert not sess.get("mfa_pending")
        wrong_pw = client.post("/login", data={
            "identifier": seed_admin["username"], "password": "whatever",
        }, follow_redirects=False)
        with client.session_transaction() as sess:
            assert not sess.get("mfa_pending")
        assert unknown.status_code == wrong_pw.status_code == 200
        assert b"Invalid credentials" in unknown.data
        assert b"Invalid credentials" in wrong_pw.data
        assert sent_codes == []

    def test_real_employee_identifier_not_hijacked_into_admin_decoy(self, client, sent_codes, seed_employee, mandatory_login_mfa_enabled):
        """The enumeration-guard branch above only fires when `identifier`
        matches neither admin_users NOR employees -- a real employee_id
        (which also has no admin_users row) must still reach the normal
        employee password check below it, not get redirected into the
        decoy admin flow before its password is ever verified."""
        resp = client.post("/login", data={
            "identifier": seed_employee["employee_id"], "password": "totally wrong password",
        }, follow_redirects=False)
        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("mfa_pending")

    def test_admin_with_no_email_on_file_rejected_generically(self, client, sent_codes, db_engine, mandatory_login_mfa_enabled):
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO admin_users (username, password, email) VALUES (%s,%s,NULL) "
            "ON CONFLICT (username) DO NOTHING",
            ("noemail_admin", generate_password_hash("NoEmail@123")),
        )
        db_engine.commit()
        resp = client.post("/login", data={"identifier": "noemail_admin", "password": "NoEmail@123"})
        assert b"Invalid credentials" in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("mfa_pending")
        cur.execute("DELETE FROM admin_users WHERE username='noemail_admin'")
        db_engine.commit()


class TestEmployeeLoginMfa:
    def test_correct_password_does_not_grant_session_yet(self, client, sent_codes, seed_employee, mandatory_login_mfa_enabled):
        resp = client.post("/login", data={
            "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/mfa_verify"
        with client.session_transaction() as sess:
            assert not sess.get("employee_id")
            assert sess.get("mfa_challenge_id")
            assert sess.get("mfa_user") == seed_employee["employee_id"]

    def test_correct_otp_completes_login(self, client, sent_codes, seed_employee, mandatory_login_mfa_enabled):
        client.post("/login", data={
            "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
        })
        code = sent_codes[-1]

        resp = client.post("/mfa_verify", data={"otp_code": code}, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/employee_portal"
        with client.session_transaction() as sess:
            assert sess.get("employee_id") == seed_employee["employee_id"]

    def test_otp_never_leaks_into_page_html(self, client, sent_codes, seed_employee, mandatory_login_mfa_enabled):
        client.post("/login", data={
            "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
        })
        code = sent_codes[-1]
        resp = client.get("/mfa_verify")
        assert code.encode() not in resp.data


@pytest.fixture
def mandatory_admin_mfa_enabled(client):
    client.application.config["MANDATORY_ADMIN_MFA"] = True
    yield
    client.application.config["MANDATORY_ADMIN_MFA"] = False


class TestHrRoleEmployeeLoginMfa:
    """Full real-world path for a freshly created HR-role employee with
    BOTH MANDATORY_LOGIN_MFA and MANDATORY_ADMIN_MFA on (the actual
    combination this feature was built and debugged against): password ->
    emailed OTP -> forced PIN change (new employees always start with
    force_pin_change=1, see blueprints/employees.py's add_employee_page())
    -> HR admin panel, with no second, redundant TOTP-enrollment demand
    right after."""

    def test_hr_employee_full_first_login_flow_reaches_employees_with_no_mfa_reenrollment_demand(
        self, client, sent_codes, seed_employee, db_engine, mandatory_login_mfa_enabled, mandatory_admin_mfa_enabled,
    ):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET role='HR', force_pin_change=1 WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        try:
            # 1. Password login -> stops at the emailed-OTP step, same as
            # any other employee.
            resp = client.post("/login", data={
                "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
            }, follow_redirects=False)
            assert resp.headers.get("Location") == "/mfa_verify"
            code = sent_codes[-1]

            # 2. Correct OTP -> force_pin_change=1 wins over the HR check,
            # same as the non-MFA path already tested in test_auth_routes.py.
            resp = client.post("/mfa_verify", data={"otp_code": code}, follow_redirects=False)
            assert resp.headers.get("Location") == "/force_change_pin"
            with client.session_transaction() as sess:
                assert sess.get("employee_id") == seed_employee["employee_id"]
                assert not sess.get("admin_logged_in")

            # 3. Completing the forced PIN change re-checks role and NOW
            # routes to the HR admin panel instead of /employee_portal.
            resp = client.post("/force_change_pin", data={
                "new_password": "NewStrongPass@1", "confirm_password": "NewStrongPass@1",
            }, follow_redirects=False)
            assert resp.headers.get("Location") == "/hr_dashboard"
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == seed_employee["employee_id"]
                assert sess.get("admin_role") == "hr"

            # 4. The auto-provisioned admin_users row must NOT be bounced
            # to a second, separate TOTP-enrollment page -- the OTP email
            # already verified in step 2 counts as this login's MFA.
            resp = client.get("/admin", follow_redirects=False)
            assert resp.status_code != 302 or resp.headers.get("Location") != "/admin/mfa-required"
            cur.execute("SELECT totp_enabled FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            assert cur.fetchone()[0] == 1
        finally:
            cur.execute("UPDATE employees SET role=NULL, force_pin_change=0 WHERE employee_id=%s",
                        (seed_employee["employee_id"],))
            cur.execute("DELETE FROM admin_users WHERE username=%s", (seed_employee["employee_id"],))
            cur.close()


# The standalone HR Portal (blueprints/hr_portal.py: its own /hr_login and
# /hr dashboard) was intentionally removed from the codebase (see git log
# "chore: remove HR portal, SuperAdmin, SecOps, Compliance modules & cleanup
# duplicates") -- there is no /hr_login route to test login-MFA against
# anymore. The one security property worth keeping (an admin_users row
# with role='hr' must never complete a session through the regular /login)
# is still covered, and still enforced in blueprints/auth.py, by
# TestAdminLoginMfa::test_hr_role_cannot_use_admin_login_even_with_mfa_enabled
# above.


class TestGateDisabledByDefault:
    def test_admin_login_completes_immediately_without_the_fixture(self, client, sent_codes, seed_admin):
        resp = client.post("/login", data={
            "identifier": seed_admin["username"], "password": seed_admin["password"],
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/admin"
