"""Signed-in sessions (utils/session_registry.py): every signed-in session is
recorded server-side, people see and end their own (blueprints/my_sessions.py),
an ended session's cookie stops working on its next request, and the SOC
console lists and revokes signed-in sessions across companies.
"""
import secrets

from extensions import app as flask_app
from tests import test_soc_console as _console
from tests.test_auth_hardening import _make_tenant
from tests.test_soc_console import make_user, sign_in

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches).
_fresh_soc_state = _console._fresh_soc_state

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"


def _admin(seed_admin, ua=CHROME, ip="203.0.113.5"):
    """A separate browser signed in as the seeded admin. -> test client"""
    c = flask_app.test_client()
    env = {"HTTP_USER_AGENT": ua, "REMOTE_ADDR": ip}
    c.environ_base.update(env)
    c.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    with c.session_transaction() as sess:
        assert sess.get("admin_logged_in") and sess.get("_sid")
    c.get("/admin")                                  # the first signed-in request records the session
    return c


def _sid(c):
    with c.session_transaction() as sess:
        return sess["_sid"]


def _rows(db_engine, sql, params=()):
    cur = db_engine.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _signed_in(c):
    """True if this browser's session is still accepted on a signed-in page."""
    resp = c.get("/api/my/sessions")
    return resp.status_code == 200 and resp.get_json().get("ok") is True


class TestRecording:
    def test_signing_in_records_the_session(self, client, seed_admin, db_engine):
        a = _admin(seed_admin)
        [(ident, kind, browser, network)] = _rows(
            db_engine, "SELECT identifier, kind, browser, network FROM user_sessions WHERE sid=%s", (_sid(a),))
        assert (ident, kind, browser, network) == (seed_admin["username"], "admin", "Chrome on Windows", "203.0.113.0/24")
        # and the kill switch has a row for it, so the SOC's revoke can end it
        assert _rows(db_engine, "SELECT status, score FROM session_risk WHERE sid=%s", (_sid(a),)) == [("active", 0)]

    def test_refreshes_at_most_every_few_minutes(self, client, seed_admin, db_engine):
        a = _admin(seed_admin)
        sid = _sid(a)
        cur = db_engine.cursor()
        cur.execute("UPDATE user_sessions SET last_seen_at = NOW() - INTERVAL '1 hour' WHERE sid=%s", (sid,))
        cur.close()
        a.get("/admin")
        [(stale,)] = _rows(db_engine, "SELECT last_seen_at < NOW() - INTERVAL '30 minutes' FROM user_sessions WHERE sid=%s", (sid,))
        assert stale                                   # not written again within REFRESH_SECONDS
        with a.session_transaction() as sess:
            sess["_reg_at"] = 0
        a.get("/admin")
        [(stale,)] = _rows(db_engine, "SELECT last_seen_at < NOW() - INTERVAL '30 minutes' FROM user_sessions WHERE sid=%s", (sid,))
        assert not stale


class TestOwnSessions:
    def test_lists_only_my_live_sessions_without_their_ids(self, client, seed_admin, db_engine):
        a = _admin(seed_admin)
        b = _admin(seed_admin, ua=FIREFOX, ip="198.51.100.7")
        cur = db_engine.cursor()
        old, other, ended = (secrets.token_hex(16) for _ in range(3))
        cur.execute("INSERT INTO user_sessions (sid, identifier, kind, started_at, last_seen_at) VALUES "
                    "(%s, %s, 'admin', NOW() - INTERVAL '9 hours', NOW()), (%s, 'someone_else', 'admin', NOW(), NOW()), "
                    "(%s, %s, 'admin', NOW(), NOW())", (old, seed_admin["username"], other, ended, seed_admin["username"]))
        cur.execute("INSERT INTO session_risk (sid, identifier, status) VALUES (%s, %s, 'revoked')", (ended, seed_admin["username"]))
        cur.close()
        sessions = a.get("/api/my/sessions").get_json()["sessions"]
        assert sorted(s["browser"] for s in sessions) == ["Chrome on Windows", "Firefox on Linux"]
        assert [s["current"] for s in sessions].count(True) == 1
        assert all(len(s["ref"]) == 12 and "sid" not in s for s in sessions)
        body = a.get("/api/my/sessions").get_data(as_text=True)
        assert _sid(a) not in body and _sid(b) not in body

    def test_ending_another_session_signs_it_out_on_its_next_request(self, client, seed_admin, db_engine):
        a = _admin(seed_admin)
        b = _admin(seed_admin, ua=FIREFOX, ip="198.51.100.7")
        b_sid = _sid(b)
        theirs = next(s for s in a.get("/api/my/sessions").get_json()["sessions"] if not s["current"])
        resp = a.post("/api/my/sessions/end", json={"ref": theirs["ref"]})
        assert resp.status_code == 200 and resp.get_json()["ended"] == 1
        assert not _signed_in(b)
        assert _signed_in(a)
        assert [s["current"] for s in a.get("/api/my/sessions").get_json()["sessions"]] == [True]
        assert _rows(db_engine, "SELECT status FROM session_risk WHERE sid=%s", (b_sid,)) == [("revoked",)]
        with b.session_transaction() as sess:
            assert "_sid" not in sess and not sess.get("admin_logged_in")      # the app dropped B's cookie session
        events = _rows(db_engine, "SELECT COUNT(*) FROM security_events WHERE event_type='session.ended_by_user' "
                                  "AND identifier=%s", (seed_admin["username"],))
        assert events[0][0] >= 1

    def test_this_session_is_ended_with_sign_out_not_here(self, client, seed_admin):
        a = _admin(seed_admin)
        mine = next(s for s in a.get("/api/my/sessions").get_json()["sessions"] if s["current"])
        resp = a.post("/api/my/sessions/end", json={"ref": mine["ref"]})
        assert resp.status_code == 400 and _signed_in(a)

    def test_unknown_reference_and_empty_request(self, client, seed_admin):
        a = _admin(seed_admin)
        assert a.post("/api/my/sessions/end", json={"ref": "0" * 12}).status_code == 404
        assert a.post("/api/my/sessions/end", json={}).status_code == 400

    def test_sign_out_of_all_other_sessions(self, client, seed_admin):
        a = _admin(seed_admin)
        others = [_admin(seed_admin, ua=FIREFOX, ip=f"198.51.100.{n}") for n in (7, 8)]
        assert a.post("/api/my/sessions/end", json={"all_others": True}).get_json()["ended"] == 2
        assert [_signed_in(o) for o in others] == [False, False] and _signed_in(a)

    def test_employee_sessions_work_too(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
            sess["employee_name"] = seed_employee["name"]
            sess["_sid"] = secrets.token_hex(16)
        [mine] = client.get("/api/my/sessions").get_json()["sessions"]
        assert mine["current"] is True

    def test_needs_a_signed_in_session(self, client):
        assert client.get("/api/my/sessions").status_code in (302, 401)
        assert client.post("/api/my/sessions/end", json={"all_others": True}).status_code in (302, 401)


class TestSocConsole:
    def test_lists_and_revokes_signed_in_company_sessions(self, client, db_engine):
        slug, schema = _make_tenant(db_engine, "sess" + secrets.token_hex(3))
        company = flask_app.test_client()
        company.environ_base.update({"HTTP_USER_AGENT": CHROME, "REMOTE_ADDR": "203.0.113.9"})
        company.post(f"/{slug}/login", data={"identifier": "iso_admin", "password": "IsoAdmin@123"})
        company.get(f"/{slug}/admin")
        assert company.get(f"/{slug}/api/my/sessions").status_code == 200

        viewer = flask_app.test_client()
        assert sign_in(viewer, make_user("sess_viewer", "viewer")).status_code == 303
        listed = [s for s in viewer.get("/socperson/api/signed_in").get_json()["sessions"] if s["slug"] == slug]
        assert [s["identifier"] for s in listed] == ["iso_admin"] and "sid" not in listed[0]

        analyst = flask_app.test_client()
        assert sign_in(analyst, make_user("sess_analyst", "analyst")).status_code == 303
        [row] = [s for s in analyst.get("/socperson/api/signed_in").get_json()["sessions"] if s["slug"] == slug]
        resp = analyst.post("/socperson/api/sessions/revoke", json={"tenant_id": row["tenant_id"], "sid": row["sid"]})
        assert resp.get_json()["ok"] is True
        assert company.get(f"/{slug}/api/my/sessions").status_code != 200
        assert not [s for s in analyst.get("/socperson/api/signed_in").get_json()["sessions"] if s["slug"] == slug]


class TestLiveCount:
    def test_the_live_strip_counts_signed_in_sessions(self, client, db_engine):
        from utils import live_feed, session_registry
        session_registry.clear_count_cache()
        before = session_registry.count_signed_in()["count"]
        slug, _schema = _make_tenant(db_engine, "cnt" + secrets.token_hex(3))
        company = flask_app.test_client()
        company.post(f"/{slug}/login", data={"identifier": "iso_admin", "password": "IsoAdmin@123"})
        company.get(f"/{slug}/admin")
        session_registry.clear_count_cache()
        live_feed._kpi_cache.update(at=0.0, value=None)
        assert live_feed.kpis()["signed_in"]["count"] == before + 1
        session_registry.clear_count_cache()
        live_feed._kpi_cache.update(at=0.0, value=None)
