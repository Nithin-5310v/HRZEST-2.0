"""Sign out everyone (utils/session_cutoff.py): one platform-wide cut-off that
ends every company browser session and API token issued before it, set from
the SOC console (admin only) or scripts/sign_out_everyone.py. People sign in
again as usual, and the console's own sessions are left alone.
"""
import secrets
import time

import pytest

from extensions import app as flask_app
from tests import test_soc_console as _console
from tests.test_my_sessions import FIREFOX, _admin
from tests.test_soc_console import audit_actions
from utils import session_cutoff
from utils.auth import _hash_token

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches)
# and their signed-in console fixture.
_fresh_soc_state = _console._fresh_soc_state
soc = _console.soc


@pytest.fixture(autouse=True)
def _no_cutoff(db_engine):
    """The cut-off is platform-wide: leave none behind for the next test."""
    def clear():
        cur = db_engine.cursor()
        cur.execute("DELETE FROM public.session_cutoff")
        cur.close()
        session_cutoff.clear_cache()
    clear()
    yield
    clear()


def _sign_out_everyone(actor="test"):
    with flask_app.app_context():
        return session_cutoff.sign_out_everyone(actor)


def _token(db_engine, kind, identity, minutes_ago=0):
    """An API token row issued `minutes_ago` minutes ago. -> the raw token"""
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute("INSERT INTO api_tokens (token, token_type, identity, created_at, expires_at, mfa_verified) "
                "VALUES (%s, %s, %s, NOW() - (%s * INTERVAL '1 minute'), NOW() + INTERVAL '1 hour', 1)",
                (_hash_token(raw), kind, identity, minutes_ago))
    cur.close()
    return raw


def _bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


def _signed_out(c):
    """The browser's next request is sent to sign in, its session emptied."""
    r = c.get("/admin", follow_redirects=False)
    with c.session_transaction() as sess:
        return (r.status_code == 302 and r.headers["Location"].rstrip("/").endswith("login")
                and not sess.get("admin_logged_in") and not sess.get("_session_created"))


class TestCutoff:
    def test_nothing_is_cut_off_until_someone_signs_everyone_out(self, db_engine):
        with flask_app.app_context():
            assert session_cutoff.cut_off_epoch() == 0.0
            assert session_cutoff.last_cutoff() is None

    def test_signing_everyone_out_records_the_moment_and_who(self, db_engine):
        before = time.time()
        epoch = _sign_out_everyone("alice")
        assert before - 5 < epoch < time.time() + 5
        with flask_app.app_context():
            assert session_cutoff.cut_off_epoch() == epoch
            assert session_cutoff.last_cutoff()["by"] == "alice"
        session_cutoff.clear_cache()            # another worker reads it from the database
        with flask_app.app_context():
            assert session_cutoff.cut_off_epoch() == pytest.approx(epoch)

    def test_an_unreadable_cutoff_keeps_the_last_value(self, db_engine, monkeypatch):
        epoch = _sign_out_everyone()

        def boom():
            raise RuntimeError("database down")
        monkeypatch.setattr(session_cutoff, "_read", boom)
        session_cutoff._cache["at"] = float("-inf")     # cache expired
        assert session_cutoff.cut_off_epoch() == epoch


class TestBrowserSessions:
    def test_every_signed_in_browser_is_signed_out_and_can_sign_in_again(self, client, seed_admin):
        a = _admin(seed_admin)
        b = _admin(seed_admin, ua=FIREFOX, ip="198.51.100.7")
        assert a.get("/admin").status_code == 200
        _sign_out_everyone()
        assert _signed_out(a) and _signed_out(b)
        assert _admin(seed_admin).get("/admin", follow_redirects=False).status_code == 200

    def test_api_calls_from_a_signed_out_browser_get_401(self, client, seed_admin):
        a = _admin(seed_admin)
        _sign_out_everyone()
        r = a.get("/api/my/sessions")
        assert r.status_code == 401 and r.get_json()["msg"] == "You have been signed out. Please log in again."

    def test_the_signed_in_list_drops_sessions_started_before(self, client, seed_admin):
        _admin(seed_admin)
        _sign_out_everyone()
        fresh = _admin(seed_admin)
        sessions = fresh.get("/api/my/sessions").get_json()["sessions"]
        assert [s["current"] for s in sessions] == [True]


class TestApiTokens:
    def test_admin_and_employee_tokens_issued_before_are_refused(self, client, seed_admin, seed_employee, db_engine):
        admin_tok = _token(db_engine, "admin", seed_admin["username"], minutes_ago=1)
        emp_tok = _token(db_engine, "employee", seed_employee["employee_id"], minutes_ago=1)
        assert client.get("/api/admin/profile", headers=_bearer(admin_tok)).status_code == 200
        assert client.get("/api/employee/portal", headers=_bearer(emp_tok)).status_code == 200
        _sign_out_everyone()
        assert client.get("/api/admin/profile", headers=_bearer(admin_tok)).status_code == 401
        assert client.get("/api/employee/portal", headers=_bearer(emp_tok)).status_code == 401
        # Signing in again (a new token) works.
        assert client.get("/api/admin/profile", headers=_bearer(_token(db_engine, "admin", seed_admin["username"]))).status_code == 200
        assert client.get("/api/employee/portal",
                          headers=_bearer(_token(db_engine, "employee", seed_employee["employee_id"]))).status_code == 200

    def test_a_bridge_link_minted_before_is_used_up_and_refused(self, client, seed_admin, db_engine):
        raw = secrets.token_hex(16)
        cur = db_engine.cursor()
        cur.execute("INSERT INTO mobile_bridge_tokens (token_hash, admin_username, created_at, expires_at) "
                    "VALUES (%s, %s, NOW() - INTERVAL '1 minute', NOW() + INTERVAL '4 minutes')",
                    (_hash_token(raw), seed_admin["username"]))
        _sign_out_everyone()
        r = client.get(f"/mobile_bridge_login/{raw}", follow_redirects=False)
        assert r.status_code == 403 and b"expired" in r.data.lower()
        with client.session_transaction() as sess:
            assert not sess.get("admin_logged_in")
        cur.execute("SELECT 1 FROM mobile_bridge_tokens WHERE token_hash=%s", (_hash_token(raw),))
        assert cur.fetchone() is None
        cur.close()


class TestConsole:
    def test_an_admin_signs_everyone_out_and_stays_in_the_console(self, soc, seed_admin, db_engine):
        a = _admin(seed_admin)
        c, _ = soc("alice", "admin")
        r = c.post("/socperson/api/sessions/sign_out_everyone", json={})
        assert r.status_code == 200 and r.get_json()["ok"]
        assert _signed_out(a)
        last = c.get("/socperson/api/signed_in").get_json()["signed_out_everyone"]
        assert last["by"] == "alice" and last["at"]
        assert ("alice", "sign_out_everyone", None) in audit_actions(db_engine)
        assert c.get("/socperson/api/me").status_code == 200

    def test_analysts_and_viewers_cannot(self, soc, db_engine):
        for name, role in (("ann", "analyst"), ("vic", "viewer")):
            c, _ = soc(name, role)
            assert c.post("/socperson/api/sessions/sign_out_everyone", json={}).status_code == 403
        with flask_app.app_context():
            assert session_cutoff.last_cutoff() is None

    def test_signed_in_says_nothing_until_it_has_been_used(self, soc):
        c, _ = soc("alice", "admin")
        assert c.get("/socperson/api/signed_in").get_json()["signed_out_everyone"] is None


class TestScript:
    def test_it_asks_for_confirmation(self, db_engine):
        from scripts import sign_out_everyone
        assert sign_out_everyone.main([]) == 2
        with flask_app.app_context():
            assert session_cutoff.last_cutoff() is None

    def test_it_signs_everyone_out_and_is_audited_as_cli(self, client, seed_admin, db_engine):
        from scripts import sign_out_everyone
        a = _admin(seed_admin)
        with flask_app.app_context():
            assert sign_out_everyone.main(["--yes"]) == 0
            assert session_cutoff.last_cutoff()["by"] == "cli"
        assert _signed_out(a)
        assert ("cli", "sign_out_everyone", None) in audit_actions(db_engine)
