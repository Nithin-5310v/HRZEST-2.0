"""Tests for the mandatory admin/manager/hr MFA-enrollment gate
(app.py's _enforce_admin_mfa_enrollment before_request hook): an admin-side
session without TOTP enrolled can reach nothing except the enrollment flow
itself (and login/logout) until they enroll — no grace period.

Disabled globally in tests/conftest.py (MANDATORY_ADMIN_MFA=False), same
reasoning as disabling flask-limiter, since most of the suite logs in admin
sessions directly without an enrolled TOTP secret. Re-enabled locally here."""
import pyotp
import pytest
from utils.totp import reset_admin_totp_secret


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def mandatory_mfa_enabled(client):
    client.application.config["MANDATORY_ADMIN_MFA"] = True
    yield
    client.application.config["MANDATORY_ADMIN_MFA"] = False


class TestMandatoryMfaGate:
    @pytest.mark.parametrize("role", ["admin", "manager", "hr"])
    def test_unenrolled_admin_role_redirected_to_enrollment(self, client, seed_admin, mandatory_mfa_enabled, role):
        _admin_session(client, seed_admin["username"], role=role)
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("Location") == "/admin/mfa-required"

    def test_enrollment_page_itself_is_reachable(self, client, seed_admin, mandatory_mfa_enabled):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/admin/mfa-required")
        assert resp.status_code == 200
        assert b"Two-Factor Authentication Required" in resp.data

    def test_setup_and_enable_endpoints_stay_reachable_before_enrollment(self, client, seed_admin, mandatory_mfa_enabled):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/settings/2fa/setup")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        # reset_admin_totp_secret() (not a raw UPDATE) so this also
        # invalidates app.py's is_totp_enabled_cached() cache -- a raw SQL
        # write here would leave a stale cached "enabled" entry for
        # seed_admin's shared username, which the NEXT test using the same
        # fixture could read within the 60s TTL window regardless of what
        # the database actually says.
        reset_admin_totp_secret(seed_admin["username"])

    def test_after_enrollment_admin_route_succeeds(self, client, seed_admin, mandatory_mfa_enabled):
        _admin_session(client, seed_admin["username"])
        setup = client.get("/api/settings/2fa/setup").get_json()
        code = pyotp.TOTP(setup["secret"]).now()
        enable_resp = client.post("/api/settings/2fa/enable", json={"code": code})
        assert enable_resp.get_json()["ok"] is True

        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 200

        # See test_setup_and_enable_endpoints_stay_reachable_before_enrollment
        # above for why this must go through reset_admin_totp_secret()
        # rather than a raw UPDATE.
        reset_admin_totp_secret(seed_admin["username"])

    def test_employee_only_session_unaffected(self, client, seed_employee, mandatory_mfa_enabled):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/employee_portal", follow_redirects=False)
        assert resp.status_code != 302 or resp.headers.get("Location") != "/admin/mfa-required"

    def test_logout_and_login_pages_exempt(self, client, seed_admin, mandatory_mfa_enabled):
        _admin_session(client, seed_admin["username"])
        assert client.post("/logout", follow_redirects=False).headers.get("Location") != "/admin/mfa-required"
        assert client.get("/login", follow_redirects=False).status_code == 200

    def test_api_path_gets_json_403_not_redirect(self, client, seed_admin, mandatory_mfa_enabled):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/org_chart_data")
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["ok"] is False
        assert data["redirect"] == "/admin/mfa-required"

    def test_gate_disabled_by_default_in_suite(self, client, seed_admin):
        # Sanity check the opt-out itself: without the fixture above, an
        # unenrolled admin must NOT be redirected — this is what every other
        # test in the suite relies on implicitly.
        _admin_session(client, seed_admin["username"])
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 200
