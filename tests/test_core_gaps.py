"""Narrowly-targeted tests for the blueprints/core.py routes NOT already
exercised anywhere else in the suite.

blueprints/core.py already has substantial incidental coverage: grepping
the existing test files for exact route-string hits shows /csp-report is
already covered (tests/test_admin_views_coverage.py's TestCspReport class
and tests/test_waf.py's WAF-body tests), and the rest of core.py's routes
(/api/login, /api/dashboard, /api/employee/login, /api/employee/signup,
etc.) are hit across test_admin_views_*, test_auth*,
test_attendance_checkin.py, test_employee_portal_coverage.py,
test_leave_*, test_notifications_flow.py, test_payroll.py,
test_pending_work_apis.py, test_employee_welcome_email.py, and
test_sync_punches.py.

The confirmed gaps (zero hits anywhere in tests/ for the route string) are:
/api/session/risk-stream, /api/mobile/web_session_link,
/mobile_bridge_login/<token>, /api/billing_status, /api/settings/update,
and /api/admin/profile -- this file covers exactly those six, nothing else.
"""
import re


def _admin_token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    }).get_json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestSessionRiskStream:
    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/session/risk-stream")
        assert resp.status_code == 401
        assert resp.get_json()["ok"] is False

    def test_authenticated_admin_without_sid_returns_400(self, client):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        resp = client.get("/api/session/risk-stream")
        assert resp.status_code == 400

    def test_authenticated_admin_with_sid_opens_event_stream(self, client):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["_sid"] = "test-sid-12345"
        # The body (an immediate answer plus `retry:`) is covered by
        # tests/test_qa_session_risk_stream.py.
        resp = client.get("/api/session/risk-stream")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"

    def test_authenticated_employee_session_also_allowed(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
            sess["_sid"] = "test-sid-emp"
        resp = client.get("/api/session/risk-stream")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"


class TestMobileWebSessionLinkAndBridgeLogin:
    def test_requires_admin_bearer_token(self, client):
        resp = client.post("/api/mobile/web_session_link", json={})
        assert resp.status_code == 401

    def test_mints_link_and_redeeming_it_creates_admin_session(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/mobile/web_session_link", json={}, headers=_auth(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        m = re.search(r"/mobile_bridge_login/([0-9a-f]+)$", data["url"])
        assert m, f"unexpected bridge url: {data['url']}"
        bridge_token = m.group(1)

        resp2 = client.get(f"/mobile_bridge_login/{bridge_token}", follow_redirects=False)
        assert resp2.status_code == 302
        assert resp2.headers["Location"].endswith("/settings/seats")

        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") is True
            assert sess.get("admin_username") == seed_admin["username"]

    def test_bridge_token_is_single_use(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        data = client.post("/api/mobile/web_session_link", json={}, headers=_auth(token)).get_json()
        bridge_token = re.search(r"/mobile_bridge_login/([0-9a-f]+)$", data["url"]).group(1)

        first = client.get(f"/mobile_bridge_login/{bridge_token}", follow_redirects=False)
        assert first.status_code == 302

        second = client.get(f"/mobile_bridge_login/{bridge_token}", follow_redirects=False)
        assert second.status_code == 403
        assert b"expired" in second.data.lower()

    def test_unknown_bridge_token_returns_expired_page(self, client):
        resp = client.get("/mobile_bridge_login/deadbeef00112233", follow_redirects=False)
        assert resp.status_code == 403
        assert b"expired" in resp.data.lower()

    def test_unrecognized_target_falls_back_to_default_on_redemption(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        data = client.post("/api/mobile/web_session_link", json={"target": "/some/other/page"},
                           headers=_auth(token)).get_json()
        bridge_token = re.search(r"/mobile_bridge_login/([0-9a-f]+)$", data["url"]).group(1)
        resp = client.get(f"/mobile_bridge_login/{bridge_token}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/settings/seats")

    def test_allowlisted_target_is_honored(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        data = client.post("/api/mobile/web_session_link", json={"target": "/settings/seats"},
                           headers=_auth(token)).get_json()
        bridge_token = re.search(r"/mobile_bridge_login/([0-9a-f]+)$", data["url"]).group(1)
        resp = client.get(f"/mobile_bridge_login/{bridge_token}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/settings/seats")


class TestApiBillingStatus:
    def test_requires_admin_bearer_token(self, client):
        resp = client.get("/api/billing_status")
        assert resp.status_code == 401

    def test_returns_billing_snapshot_shape(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/billing_status", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["employee_count"], int)
        assert isinstance(data["monthly_bill_display"], str)
        assert data["monthly_bill_display"].startswith("₹")  # rupee sign
        assert isinstance(data["invoices"], list)


class TestApiSettingsUpdate:
    def test_requires_admin_bearer_token(self, client):
        resp = client.post("/api/settings/update", json={"foo": "bar"})
        assert resp.status_code == 401

    def test_echoes_back_submitted_settings(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/settings/update", json={"theme": "dark"}, headers=_auth(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["settings"] == {"theme": "dark"}


class TestApiAdminProfile:
    def test_requires_admin_bearer_token(self, client):
        resp = client.get("/api/admin/profile")
        assert resp.status_code == 401

    def test_returns_the_authenticated_admins_own_profile(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/admin/profile", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["username"] == seed_admin["username"]
        assert data["email"] == "admin@test.local"
        assert data["role"] == "admin"
