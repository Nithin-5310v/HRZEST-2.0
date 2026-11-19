"""Security review OBS-07 (2026-11): robots.txt no longer advertises the
operator console, and the console answers 404 to anyone without a
super-admin session, so its pages look like they don't exist. Admin and
console responses are kept out of search engines with X-Robots-Tag, not
robots.txt."""
import time

import pytest

from extensions import app as flask_app

# Every /super_admin GET page without URL parameters, plus a few with them.
_PAGES = sorted({r.rule for r in flask_app.url_map.iter_rules()
                 if r.rule.startswith("/super_admin") and "GET" in r.methods
                 and "<" not in r.rule and r.rule not in ("/super_admin/login",)})
_POSTS = ["/super_admin/costs", "/super_admin/tenants/1/status", "/super_admin/tenants/1/delete"]


def _as(client, **session_values):
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(session_values)


def test_robots_txt_names_nothing_private(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    body = client.get("/robots.txt").get_data(as_text=True)
    for private in ("super_admin", "webhooks", "csp-report"):
        assert private not in body


def test_there_are_console_pages_to_check():
    assert "/super_admin" in _PAGES and len(_PAGES) >= 5


@pytest.mark.parametrize("path", _PAGES)
def test_anonymous_visitor_gets_404(client, path):
    _as(client)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", _POSTS)
def test_anonymous_post_gets_404(client, path):
    _as(client)
    assert client.post(path, data={}).status_code == 404


@pytest.mark.parametrize("who", [
    {"admin_logged_in": True, "admin_username": "test_admin", "admin_role": "admin"},
    {"admin_logged_in": True, "admin_username": "hr1", "admin_role": "hr"},
    {"employee_id": "EMP001"},
])
def test_tenant_admins_and_employees_get_404(client, who):
    _as(client, **who)
    assert client.get("/super_admin").status_code == 404
    assert client.get("/super_admin/tenants").status_code == 404


def test_a_super_admin_still_gets_in(client):
    _as(client, platform_admin_logged_in=True, platform_admin_username="pa_test",
        platform_admin_last_activity=time.time())
    assert client.get("/super_admin").status_code == 200


def test_an_idle_super_admin_is_sent_to_login(client):
    _as(client, platform_admin_logged_in=True, platform_admin_username="pa_test",
        platform_admin_last_activity=time.time() - 3 * 3600)
    resp = client.get("/super_admin")
    assert resp.status_code == 302 and resp.headers["Location"] == "/super_admin/login"


@pytest.mark.parametrize("path", ["/super_admin/login", "/super_admin", "/login", "/admin", "/soc/login"])
def test_admin_responses_carry_noindex(client, path):
    _as(client)
    assert "noindex" in client.get(path).headers.get("X-Robots-Tag", "")
