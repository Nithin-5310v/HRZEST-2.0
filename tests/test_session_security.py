"""Security review Section 9 (2026-11): session values were exposed in a
terminal during testing, so check that a session can't outlive its logout.

  * a new session id at login (no fixation) and after a password change;
  * logout revokes the session server-side: a copy of the cookie taken
    before logout is refused afterwards (tenant admin, employee, super-admin);
  * idle and absolute timeouts end a session.
"""
import time

import pytest

COOKIE = "__Host-session"


def _cookie(client):
    c = client.get_cookie(COOKIE)
    assert c is not None, "client has a session cookie"
    return c.value


def _replay(client, value):
    client.set_cookie(COOKIE, value)


def _sid(client):
    with client.session_transaction() as sess:
        return sess.get("_sid")


def _status(db_engine, sid):
    cur = db_engine.cursor()
    cur.execute("SELECT status FROM session_risk WHERE sid=%s", (sid,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


@pytest.fixture
def cleanup_risk(db_engine):
    sids = []
    yield sids
    cur = db_engine.cursor()
    for sid in sids:
        cur.execute("DELETE FROM session_risk WHERE sid=%s", (sid,))
    db_engine.commit()
    cur.close()


# ── login ───────────────────────────────────────────────────────────────────
def test_login_starts_a_new_session_id(client, seed_admin, cleanup_risk):
    with client.session_transaction() as sess:
        sess["_sid"] = "attacker-planted-sid"
        sess["_csrf"] = "attacker-planted-csrf"
    resp = client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get("admin_logged_in")
        assert sess.get("_sid") and sess["_sid"] != "attacker-planted-sid"
        assert sess.get("_csrf") != "attacker-planted-csrf"
        cleanup_risk.append(sess["_sid"])


# ── logout revokes server-side ──────────────────────────────────────────────
def test_admin_logout_kills_copies_of_the_cookie(client, db_engine, seed_admin, cleanup_risk):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    sid = _sid(client)
    cleanup_risk.append(sid)
    assert client.get("/employees").status_code == 200
    stolen = _cookie(client)

    client.post("/logout")
    assert _status(db_engine, sid) == "revoked"

    _replay(client, stolen)
    resp = client.get("/employees")
    assert resp.status_code in (302, 303) and "login" in resp.headers["Location"]
    _replay(client, stolen)
    assert client.get("/api/employees", headers={"Accept": "application/json"}).status_code in (302, 401, 403)


def test_employee_logout_kills_copies_of_the_cookie(client, db_engine, seed_employee, cleanup_risk):
    client.post("/login", data={"identifier": seed_employee["employee_id"], "password": seed_employee["password"]})
    sid = _sid(client)
    assert sid, "employee login sets a session id"
    cleanup_risk.append(sid)
    assert client.get("/employee_portal").status_code == 200
    stolen = _cookie(client)

    client.post("/employee_logout")
    assert _status(db_engine, sid) == "revoked"

    _replay(client, stolen)
    resp = client.get("/employee_portal")
    assert resp.status_code in (302, 303)


def test_super_admin_logout_kills_copies_of_the_cookie(client, db_engine, cleanup_risk):
    from utils.session_risk import ensure_session_id
    with client.session_transaction() as sess:
        sess["platform_admin_logged_in"] = True
        sess["platform_admin_username"] = "pa_test"
        sess["platform_admin_last_activity"] = time.time()
        sess["_session_created"] = time.time()
        sid = ensure_session_id(sess)
    cleanup_risk.append(sid)
    assert client.get("/super_admin").status_code == 200
    stolen = _cookie(client)

    client.post("/super_admin/logout")
    assert _status(db_engine, sid) == "revoked"
    _replay(client, stolen)
    assert client.get("/super_admin").status_code == 404


def test_logout_without_a_session_is_harmless(client):
    assert client.post("/logout").status_code in (302, 303)
    assert client.post("/employee_logout").status_code in (302, 303)


# ── password change ─────────────────────────────────────────────────────────
def test_password_change_rotates_the_session(client, db_engine, seed_admin, cleanup_risk):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    with client.session_transaction() as sess:
        old_sid, old_csrf = sess.get("_sid"), sess.setdefault("_csrf", "before-change")
    cleanup_risk.append(old_sid)
    stolen = _cookie(client)
    resp = client.post("/change_admin_password", data={
        "current_password": seed_admin["password"], "new_password": "Changed@98765",
        "confirm_password": "Changed@98765"})
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get("admin_logged_in"), "the session that changed the password stays signed in"
        new_sid = sess.get("_sid")
        assert new_sid and new_sid != old_sid
        assert sess.get("_csrf") != old_csrf
    cleanup_risk.append(new_sid)
    assert _status(db_engine, old_sid) == "revoked"
    assert client.get("/employees").status_code == 200
    # A copy of the pre-change cookie is dead.
    _replay(client, stolen)
    assert client.get("/employees").status_code in (302, 303)


# ── timeouts ────────────────────────────────────────────────────────────────
def test_idle_timeout(client, seed_admin):
    with client.session_transaction() as sess:
        sess.update(admin_logged_in=True, admin_username="test_admin", admin_role="admin",
                    _session_created=time.time(), _last_activity=time.time() - 24 * 3600)
    resp = client.get("/employees")
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")


def test_absolute_timeout(client, seed_admin):
    with client.session_transaction() as sess:
        sess.update(admin_logged_in=True, admin_username="test_admin", admin_role="admin",
                    _session_created=time.time() - 9 * 3600, _last_activity=time.time())
    resp = client.get("/employees")
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")


def test_revoked_sessions_stay_out_of_the_soc_risky_list(db_engine, cleanup_risk):
    from utils.session_risk import revoke_session_id
    assert revoke_session_id("plain-logout-sid", "someone")
    cleanup_risk.append("plain-logout-sid")
    cur = db_engine.cursor()
    cur.execute("SELECT 1 FROM session_risk WHERE sid='plain-logout-sid' "
                "AND (status NOT IN ('active', 'revoked') OR score > 0)")
    assert cur.fetchone() is None
    cur.close()
