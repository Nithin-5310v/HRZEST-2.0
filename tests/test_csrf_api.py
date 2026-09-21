"""CSRF coverage for /api/* routes.

app.py's _enforce_csrf used to skip every /api/ path on the assumption they
all authenticate by Bearer token, but ~60 of them are @admin_required /
@employee_required (session cookie). These tests pin the corrected rule:
an /api/ request is exempt only with a Bearer token or without a logged-in
session; everything else needs the CSRF token, for every state-changing
method.

TESTING mode disables the CSRF hook entirely, so the rule is tested directly
via _csrf_exempt, plus a few end-to-end requests with TESTING switched off.
"""
import pytest

from app import _csrf_exempt
from extensions import app as flask_app

SESSION_MSG = "Session expired"


class TestCsrfExemptRule:
    @pytest.mark.parametrize("path", [
        "/login", "/admin_login", "/hr_login",
    ])
    def test_login_routes_always_exempt(self, path):
        assert _csrf_exempt(path, has_bearer=False, session_authenticated=True)

    def test_api_with_bearer_exempt(self):
        assert _csrf_exempt("/api/attendance", has_bearer=True, session_authenticated=False)
        assert _csrf_exempt("/api/attendance", has_bearer=True, session_authenticated=True)

    @pytest.mark.parametrize("path", [
        "/api/login", "/api/employee/login", "/api/employee/signup", "/api/leads", "/api/create_org",
    ])
    def test_anonymous_api_exempt(self, path):
        """Mobile/pre-login calls have no session for a forged request to ride."""
        assert _csrf_exempt(path, has_bearer=False, session_authenticated=False)

    @pytest.mark.parametrize("path", [
        "/api/hr_accounts", "/api/secops/reset-admin-password", "/api/settings/email",
        "/api/admin/email-blast", "/api/employee/devices/asset", "/api/employees/E1",
    ])
    def test_session_authenticated_api_not_exempt(self, path):
        assert not _csrf_exempt(path, has_bearer=False, session_authenticated=True)

    @pytest.mark.parametrize("path", ["/csp-report", "/api/client-log"])
    def test_browser_telemetry_sinks_exempt_even_with_a_session(self, path):
        """Browsers can't attach a CSRF token to a CSP report, and the error beacon
        must also work for logged-in users; both are write-only log sinks."""
        assert _csrf_exempt(path, has_bearer=False, session_authenticated=True)

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_non_post_methods_are_checked_too(self, method):
        from app import _CSRF_METHODS
        assert method in _CSRF_METHODS

    def test_non_api_not_exempt(self):
        assert not _csrf_exempt("/change_admin_password", has_bearer=True, session_authenticated=False)


@pytest.fixture
def csrf_client(client):
    """The shared client with the CSRF hook switched back on."""
    flask_app.config["TESTING"] = False
    try:
        yield client
    finally:
        flask_app.config["TESTING"] = True


def _login_employee(client, csrf="tok"):
    with client.session_transaction() as s:
        s["employee_id"] = "TST001"
        s["_csrf"] = csrf


class TestCsrfEnforcedOnApi:
    def test_session_api_post_without_token_rejected(self, csrf_client):
        _login_employee(csrf_client)
        resp = csrf_client.post("/api/employee/devices/asset", json={})
        assert resp.status_code == 403
        assert SESSION_MSG in resp.get_json()["msg"]

    def test_session_api_delete_without_token_rejected(self, csrf_client):
        """PUT/PATCH/DELETE were previously never checked at all."""
        _login_employee(csrf_client)
        resp = csrf_client.delete("/api/employees/E1")
        assert resp.status_code == 403
        assert SESSION_MSG in resp.get_json()["msg"]

    def test_wrong_token_rejected(self, csrf_client):
        _login_employee(csrf_client, csrf="right")
        resp = csrf_client.post("/api/employee/devices/asset", json={},
                                headers={"X-CSRF-Token": "wrong"})
        assert resp.status_code == 403
        assert SESSION_MSG in resp.get_json()["msg"]

    def test_valid_token_passes_csrf_gate(self, csrf_client):
        _login_employee(csrf_client, csrf="right")
        resp = csrf_client.post("/api/employee/devices/asset", json={},
                                headers={"X-CSRF-Token": "right"})
        body = resp.get_json(silent=True) or {}
        assert SESSION_MSG not in str(body.get("msg", ""))

    def test_anonymous_api_post_still_reaches_route(self, csrf_client):
        resp = csrf_client.post("/api/login", json={"username": "x", "password": "y"})
        assert resp.status_code in (200, 400, 401, 429)
        assert SESSION_MSG not in str((resp.get_json(silent=True) or {}).get("msg", ""))
