"""Exception text must never reach the browser: these error paths show a
generic message and keep the detail in the server log only.

- blueprints/admin_views.py   api_test_email (SMTP test)
- blueprints/platform_admin.py platform_admin_delete_tenant (DROP SCHEMA failure)
- blueprints/oauth_signup.py   signup and login OAuth callbacks
"""
import logging
import time

import pytest

import blueprints.admin_views as admin_views
import blueprints.oauth_signup as oauth_signup
import blueprints.platform_admin as platform_admin
from extensions import app_log
from utils.oauth_client import OAuthError

DETAIL = "internal-detail-xyz host=10.0.0.5 user=svc_secret"


@pytest.fixture
def server_log():
    """Every message app_log emits during the test, traceback included."""
    records = []
    formatter = logging.Formatter("%(message)s")

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(formatter.format(record))

    handler = _Collect(level=logging.DEBUG)
    app_log.addHandler(handler)
    yield records
    app_log.removeHandler(handler)


def _flashes(client):
    with client.session_transaction() as s:
        return [msg for _cat, msg in s.get("_flashes", [])]


def _assert_detail_hidden(texts):
    for text in texts:
        assert "internal-detail-xyz" not in text and "svc_secret" not in text


def _assert_detail_logged(server_log):
    assert any("internal-detail-xyz" in r for r in server_log), "detail must still reach the server log"


class TestSmtpTestEmail:
    def test_exception_text_not_returned(self, client, seed_admin, mocker, server_log):
        mocker.patch.object(admin_views, "get_email_config", return_value={
            "host": "smtp.test.local", "port": 587, "user": "u@test.local", "password": "p",
            "from_name": "HR", "from_email": "u@test.local"})
        mocker.patch.object(admin_views, "send_email_smtp", side_effect=RuntimeError(DETAIL))
        client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})

        resp = client.post("/api/admin/test_email", json={"to_email": "to@test.local"})

        assert resp.status_code == 500
        body = resp.get_json()
        assert body["ok"] is False and body["msg"].startswith("SMTP test failed")
        _assert_detail_hidden([resp.get_data(as_text=True)])
        _assert_detail_logged(server_log)


class _DropSchemaFails:
    """Stands in for get_master_db()'s connection: identical except that
    DROP SCHEMA raises, so the route's failure branch runs for real."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _DropSchemaFailsCursor(self._conn.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _DropSchemaFailsCursor:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, *args, **kwargs):
        if sql.lstrip().upper().startswith("DROP SCHEMA"):
            raise RuntimeError(DETAIL)
        return self._cur.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class TestDeleteTenantSchemaDropFailure:
    def test_exception_text_not_flashed(self, client, db_engine, monkeypatch, server_log):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ("Generic Error Co", "generic-error-co", "att_generic_error_co"),
        )
        tenant_id = cur.fetchone()[0]
        real_get_master_db = platform_admin.get_master_db
        monkeypatch.setattr(platform_admin, "get_master_db",
                            lambda *a, **k: _DropSchemaFails(real_get_master_db(*a, **k)))
        with client.session_transaction() as s:
            s["platform_admin_logged_in"] = True
            s["platform_admin_username"] = "generic_error_admin"
            s["platform_admin_last_activity"] = time.time()
        try:
            resp = client.post(f"/super_admin/tenants/{tenant_id}/delete",
                               data={"confirm_subdomain": "generic-error-co"}, follow_redirects=False)
            assert resp.status_code == 302
            messages = _flashes(client)
            assert any(m.startswith("Could not delete company data") for m in messages)
            _assert_detail_hidden(messages)
            _assert_detail_logged(server_log)
            cur.execute("SELECT 1 FROM att_master.tenants WHERE id=%s", (tenant_id,))
            assert cur.fetchone() is not None, "tenant row must survive a failed schema drop"
        finally:
            cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
            cur.close()


class TestOAuthCallbacks:
    @pytest.mark.parametrize("path, generic", [
        ("/create_org/google/callback?code=c&state=s", "Sign-up failed"),
        ("/oauth/callback/google?code=c&state=s", "Sign-in failed"),
    ])
    def test_oauth_error_text_not_flashed(self, client, monkeypatch, server_log, path, generic):
        def _fail(*args, **kwargs):
            raise OAuthError(DETAIL)
        monkeypatch.setattr(oauth_signup, "complete_oauth", _fail)

        resp = client.get(path, follow_redirects=False)

        assert resp.status_code == 302
        messages = _flashes(client)
        assert any(m.startswith(generic) for m in messages)
        _assert_detail_hidden(messages)
        _assert_detail_logged(server_log)
