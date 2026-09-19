"""/super_admin/soc -- the iframe embed of the standalone SOC dashboard.

The CSP tests exist because the page was once shipped with the app-wide
`frame-src 'none'`: the HTML contained the iframe, but every browser refused to
load it, so the console showed an empty box.
"""
import re
import time

SOC_URL = "http://soc.example.test:4500"
SOC_TOKEN = "soc-test-token"


def _login_platform_admin(client):
    with client.session_transaction() as sess:
        sess["platform_admin_logged_in"] = True
        sess["platform_admin_username"] = "soc_tester"
        sess["platform_admin_last_activity"] = time.time()


def _frame_src(resp):
    match = re.search(r"frame-src ([^;]+);", resp.headers["Content-Security-Policy"])
    return match.group(1).strip()


class TestSocDashboardPage:
    def test_requires_platform_admin_login(self, client, monkeypatch):
        monkeypatch.setenv("SOC_DASHBOARD_URL", SOC_URL)
        resp = client.get("/super_admin/soc")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/super_admin/login")

    def test_redirects_to_console_when_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("SOC_DASHBOARD_URL", raising=False)
        _login_platform_admin(client)
        resp = client.get("/super_admin/soc")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/super_admin")

    def test_embeds_the_dashboard_with_the_token(self, client, monkeypatch):
        monkeypatch.setenv("SOC_DASHBOARD_URL", SOC_URL + "/")
        monkeypatch.setenv("SOC_ADMIN_TOKEN", SOC_TOKEN)
        _login_platform_admin(client)
        resp = client.get("/super_admin/soc")
        assert resp.status_code == 200
        assert f'<iframe src="{SOC_URL}/?token={SOC_TOKEN}"' in resp.get_data(as_text=True)

    def test_csp_lets_the_browser_load_only_the_soc_origin(self, client, monkeypatch):
        monkeypatch.setenv("SOC_DASHBOARD_URL", SOC_URL + "/some/path?x=1")
        monkeypatch.setenv("SOC_ADMIN_TOKEN", SOC_TOKEN)
        _login_platform_admin(client)
        resp = client.get("/super_admin/soc")
        # origin only: no path, no query, and never the token
        assert _frame_src(resp) == SOC_URL
        assert SOC_TOKEN not in resp.headers["Content-Security-Policy"]

    def test_csp_exception_is_scoped_to_the_soc_page(self, client, monkeypatch):
        monkeypatch.setenv("SOC_DASHBOARD_URL", SOC_URL)
        _login_platform_admin(client)
        assert _frame_src(client.get("/super_admin")) == "'none'"
        assert _frame_src(client.get("/super_admin/audit-log")) == "'none'"

    def test_csp_ignores_a_non_http_soc_url(self, client, monkeypatch):
        monkeypatch.setenv("SOC_DASHBOARD_URL", "javascript:alert(1)")
        _login_platform_admin(client)
        resp = client.get("/super_admin/soc")
        assert _frame_src(resp) == "'none'"


class TestSocNavButton:
    def test_button_only_appears_when_configured(self, client, monkeypatch):
        _login_platform_admin(client)
        monkeypatch.delenv("SOC_DASHBOARD_URL", raising=False)
        assert 'href="/super_admin/soc"' not in client.get("/super_admin").get_data(as_text=True)
        monkeypatch.setenv("SOC_DASHBOARD_URL", SOC_URL)
        assert 'href="/super_admin/soc"' in client.get("/super_admin").get_data(as_text=True)
