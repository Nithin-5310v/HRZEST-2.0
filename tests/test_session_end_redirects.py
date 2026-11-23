"""Where a browser goes when the app ends its session (app_setup/security.py's
_end_session): the 8-hour limit, "sign out everyone" and the idle timeout.

A platform-admin session goes to the console's own /super_admin/login; every
other session to the company login. That is decided by the session, not the
URL, so a company admin who opens a /super_admin page with an expired session
is not told the console exists (security review OBS-07).
"""
import time

import pytest

from extensions import app as flask_app
from utils import session_cutoff

OLD = 9 * 3600          # older than the 8-hour absolute lifetime
COMPANY_LOGIN = "/admin-login"


@pytest.fixture(autouse=True)
def _no_cutoff(db_engine):
    def clear():
        cur = db_engine.cursor()
        cur.execute("DELETE FROM public.session_cutoff")
        cur.close()
        session_cutoff.clear_cache()
    clear()
    yield
    clear()


def _platform(sess, age=0):
    sess["platform_admin_logged_in"] = True
    sess["platform_admin_username"] = "pa_test"
    sess["platform_admin_last_activity"] = time.time()
    sess["_session_created"] = time.time() - age


def _company(sess, age=0):
    sess["admin_logged_in"] = True
    sess["admin_username"] = "test_admin"
    sess["admin_role"] = "admin"
    sess["_session_created"] = time.time() - age


def _ended(client, path):
    """-> (redirect target, flashed messages); asserts the session was emptied."""
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 302, r.status_code
    with client.session_transaction() as sess:
        assert not sess.get("platform_admin_logged_in") and not sess.get("admin_logged_in")
        return r.headers["Location"], [m for _c, m in sess.get("_flashes", [])]


class TestAbsoluteLifetime:
    def test_a_platform_admin_goes_to_the_console_login(self, client):
        with client.session_transaction() as sess:
            _platform(sess, age=OLD)
        where, flashes = _ended(client, "/super_admin")
        assert where == "/super_admin/login"
        assert "Your session expired. Please log in again." in flashes

    def test_a_company_admin_goes_to_the_company_login(self, client):
        with client.session_transaction() as sess:
            _company(sess, age=OLD)
        assert _ended(client, "/admin")[0] == COMPANY_LOGIN

    def test_a_company_admin_on_a_console_url_is_not_shown_the_console(self, client):
        with client.session_transaction() as sess:
            _company(sess, age=OLD)
        assert _ended(client, "/super_admin")[0] == COMPANY_LOGIN

    @pytest.mark.parametrize("path, where", [("/super_admin", "/super_admin/login"), ("/admin", COMPANY_LOGIN)])
    def test_a_browser_signed_in_to_both_goes_by_the_page(self, client, path, where):
        with client.session_transaction() as sess:
            _company(sess)
            _platform(sess, age=OLD)
        assert _ended(client, path)[0] == where

    def test_api_calls_still_get_401_json(self, client):
        with client.session_transaction() as sess:
            _platform(sess, age=OLD)
        r = client.get("/api/my/sessions")
        assert r.status_code == 401 and r.get_json()["msg"] == "Session expired. Please log in again."


class TestSignOutEveryone:
    def test_a_platform_admin_goes_to_the_console_login(self, client):
        with client.session_transaction() as sess:
            _platform(sess, age=60)
        with flask_app.app_context():
            session_cutoff.sign_out_everyone("test")
        where, flashes = _ended(client, "/super_admin")
        assert where == "/super_admin/login"
        assert "You have been signed out. Please log in again." in flashes


class TestIdleTimeout:
    def test_a_company_admin_still_goes_to_the_company_login(self, client):
        with client.session_transaction() as sess:
            _company(sess)
            sess["_last_activity"] = time.time() - 24 * 3600
        where, flashes = _ended(client, "/admin")
        assert where == COMPANY_LOGIN
        assert "Your session expired due to inactivity. Please log in again." in flashes
