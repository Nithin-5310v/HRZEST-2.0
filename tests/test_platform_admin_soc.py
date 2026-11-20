"""There is one SOC console: /socperson (blueprints/soc.py).

The platform admin console used to have a second "SOC" page,
/super_admin/soc, that only framed a separate external dashboard. It was
removed so nobody signs in to the wrong one. These tests keep it from
coming back by accident, even on a server whose .env still has the old
SOC_DASHBOARD_URL / SOC_ADMIN_TOKEN settings.
"""
import re
import time

import pytest

OLD_URL = "https://soc.example.test:4500"
OLD_TOKEN = "soc-test-token"


@pytest.fixture
def old_settings(monkeypatch):
    monkeypatch.setenv("SOC_DASHBOARD_URL", OLD_URL)
    monkeypatch.setenv("SOC_ADMIN_TOKEN", OLD_TOKEN)


def _login_platform_admin(client):
    with client.session_transaction() as sess:
        sess["platform_admin_logged_in"] = True
        sess["platform_admin_username"] = "soc_tester"
        sess["platform_admin_last_activity"] = time.time()


def _frame_src(resp):
    return re.search(r"frame-src ([^;]+);", resp.headers["Content-Security-Policy"]).group(1).strip()


def test_the_old_page_is_gone(client, old_settings):
    _login_platform_admin(client)
    assert client.get("/super_admin/soc").status_code == 404


@pytest.mark.parametrize("path", ["/super_admin", "/super_admin/audit-log"])
def test_the_console_links_only_to_nothing_soc(client, old_settings, path):
    _login_platform_admin(client)
    resp = client.get(path)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "/super_admin/soc" not in body and OLD_URL not in body and OLD_TOKEN not in body
    assert _frame_src(resp) == "'none'"
