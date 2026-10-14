"""Coverage tests for blueprints/org.py.
Targets: create_org_page, org_chart_page, api_org_chart_data.
"""


def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    return client


# ── create_org_page ───────────────────────────────────────────────────────────

class TestCreateOrgPage:
    """Signup is open by default (no shared-secret gate) -- see
    tests/test_org.py for the full validation/provisioning coverage.
    These are the lighter GET-page and malformed-POST coverage cases."""

    def test_get_shows_form(self, client):
        rv = client.get("/create_org")
        assert rv.status_code == 200
        assert b"Start Your Organisation" in rv.data

    def test_post_missing_fields_returns_error(self, client):
        rv = client.post("/create_org", data={
            "company_name": "",
            "subdomain": "",
            "admin_email": "",
            "admin_password": "",
        })
        assert rv.status_code in (200, 302, 400)

    def test_post_captcha_rejected_when_turnstile_configured(self, client, monkeypatch):
        import blueprints.org as _org
        monkeypatch.setattr(_org, "turnstile_enabled", lambda: True)
        monkeypatch.setattr(_org, "verify_turnstile", lambda token, ip: False)
        rv = client.post("/create_org", data={
            "company_name": "Test Corp", "subdomain": "testcorp-captcha",
            "admin_username": "admin", "admin_password": "Admin@123",
            "admin_email": "admin@testcorp.com",
        }, follow_redirects=False)
        assert rv.status_code in (301, 302)
        assert rv.headers.get("Location") == "/create_org"


# ── org_chart_page ────────────────────────────────────────────────────────────

class TestOrgChartPage:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/org_chart")
        assert rv.status_code == 302
        assert "/admin-login" in rv.headers["Location"]

    def test_renders_for_admin(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/org_chart")
        assert rv.status_code == 200

    def test_renders_with_company_filter(self, client, seed_admin):
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 1
        rv = client.get("/org_chart")
        assert rv.status_code == 200


# ── api_org_chart_data ────────────────────────────────────────────────────────

class TestApiOrgChartData:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/api/org_chart_data")
        assert rv.status_code in (302, 401)

    def test_returns_json_for_admin(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/api/org_chart_data")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data.get("ok") is True
        assert "tree" in data

    def test_filters_by_department(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/api/org_chart_data?dept=Engineering")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "tree" in data or data.get("ok") is True

    def test_filters_by_company_id(self, client, seed_admin):
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 1
        rv = client.get("/api/org_chart_data")
        assert rv.status_code == 200

    def test_seed_employee_appears_in_chart(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.get("/api/org_chart_data")
        assert rv.status_code == 200
        data = rv.get_json()
        tree = data.get("tree", {})
        import json
        tree_str = json.dumps(tree)
        assert seed_employee["employee_id"] in tree_str
