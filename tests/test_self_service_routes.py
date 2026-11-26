"""Employee self-service and notification routes with no route-level test:
the app's own QR and ID card (/api/employee/my_qr, /api/employee/my_id_card),
marking the app's notifications read, the web bell's "Clear all", the admin
bell (/web/admin/notifications/*) and the announcement ribbon (/web/ribbon)."""
import base64
import hashlib
import secrets

import pytest

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _bearer(db_engine, identity, token_type="employee"):
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s, %s, %s, NOW() + INTERVAL '1 hour')",
        (identity, hashlib.sha256(raw.encode()).hexdigest(), token_type),
    )
    cur.close()
    return {"Authorization": f"Bearer {raw}"}


def _employee_session(client, employee_id="TST001"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["employee_id"] = employee_id
        sess["employee_name"] = "Test Employee"


def _admin_session(client, username="test_admin", role="admin"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _notify(db_engine, recipient_type, employee_id, title, is_read=False):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO notifications (recipient_type, employee_id, title, message, is_read) "
                "VALUES (%s, %s, %s, 'msg', %s) RETURNING id", (recipient_type, employee_id, title, is_read))
    nid = cur.fetchone()[0]
    cur.close()
    return nid


def _read_flags(db_engine, title_prefix="Route "):
    cur = db_engine.cursor()
    cur.execute("SELECT title, is_read FROM notifications WHERE title LIKE %s", (title_prefix + "%",))
    out = {t: bool(r) for t, r in cur.fetchall()}
    cur.close()
    return out


# ── /api/employee/my_qr and /api/employee/my_id_card ────────────────────────

class TestOwnQrAndIdCard:
    @pytest.fixture
    def private_root(self, tmp_path, monkeypatch):
        from utils import storage
        monkeypatch.setattr(storage, "_APP_ROOT", str(tmp_path))
        real_save = storage.save_private
        monkeypatch.setattr(storage, "save_private", lambda _root, f, rel: real_save(str(tmp_path), f, rel))
        return tmp_path

    @pytest.mark.parametrize("url", ["/api/employee/my_qr", "/api/employee/my_id_card"])
    def test_needs_an_employee_token(self, client, url):
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_my_qr_is_the_token_owners_signed_badge(self, client, db_engine, seed_employee, private_root):
        resp = client.get("/api/employee/my_qr", headers=_bearer(db_engine, "TST001"))
        assert resp.status_code == 200
        assert base64.b64decode(resp.get_json()["image_base64"]).startswith(PNG_MAGIC)
        cur = db_engine.cursor()
        cur.execute("SELECT qr_code FROM employees WHERE employee_id='TST001'")
        stored = cur.fetchone()[0]
        cur.close()
        assert stored and str(private_root) in stored, "the badge is kept in private storage only"

    def test_my_id_card(self, client, db_engine, seed_employee):
        resp = client.get("/api/employee/my_id_card", headers=_bearer(db_engine, "TST001"))
        assert resp.status_code == 200
        assert base64.b64decode(resp.get_json()["image_base64"]).startswith(PNG_MAGIC)

    def test_my_id_card_for_a_token_with_no_employee(self, client, db_engine):
        resp = client.get("/api/employee/my_id_card", headers=_bearer(db_engine, "GHOST01"))
        assert resp.status_code == 404 and resp.get_json()["ok"] is False


# ── POST /api/employee/notifications/mark_read (employee token) ─────────────

class TestEmployeeMarkReadApi:
    URL = "/api/employee/notifications/mark_read"

    def test_needs_an_employee_token(self, client):
        assert client.post(self.URL).status_code == 401

    def test_marks_only_the_token_owners(self, client, db_engine, seed_employee, seed_assigned_employee):
        _notify(db_engine, "employee", "TST001", "Route mine")
        _notify(db_engine, "employee", "TST002", "Route theirs")
        _notify(db_engine, "admin", None, "Route admin")
        assert client.post(self.URL, headers=_bearer(db_engine, "TST001")).get_json() == {"ok": True}
        assert _read_flags(db_engine) == {"Route mine": True, "Route theirs": False, "Route admin": False}


# ── POST /web/notifications/clear (employee session) ────────────────────────

class TestEmployeeBellClear:
    def test_needs_an_employee_session(self, client, seed_admin):
        assert client.post("/web/notifications/clear").status_code in (302, 401)

    def test_clears_only_own(self, client, db_engine, seed_employee, seed_assigned_employee):
        _notify(db_engine, "employee", "TST001", "Route mine")
        _notify(db_engine, "employee", "TST002", "Route theirs")
        _notify(db_engine, "admin", None, "Route admin")
        _employee_session(client)
        assert client.post("/web/notifications/clear").get_json() == {"ok": True}
        assert set(_read_flags(db_engine)) == {"Route theirs", "Route admin"}
        assert client.get("/web/notifications/list").get_json()["notifications"] == []


# ── /web/admin/notifications/list and /mark_read (admin session) ─────────────

class TestAdminBell:
    @pytest.mark.parametrize("method,url", [("get", "/web/admin/notifications/list"),
                                            ("post", "/web/admin/notifications/mark_read")])
    def test_needs_an_admin_session(self, client, seed_employee, method, url):
        assert getattr(client, method)(url).status_code in (302, 401)
        _employee_session(client)
        assert getattr(client, method)(url).status_code in (302, 401)

    def test_list_then_mark_read(self, client, db_engine, seed_admin, seed_employee):
        cur = db_engine.cursor()
        cur.execute("DELETE FROM notifications WHERE recipient_type='admin'")
        cur.close()
        _notify(db_engine, "admin", None, "Route new leave", is_read=False)
        _notify(db_engine, "admin", None, "Route old ticket", is_read=True)
        _notify(db_engine, "employee", "TST001", "Route employee")
        _admin_session(client)
        body = client.get("/web/admin/notifications/list").get_json()
        assert body["unread"] == 1
        assert {n["title"] for n in body["notifications"]} == {"Route new leave", "Route old ticket"}

        assert client.post("/web/admin/notifications/mark_read").get_json() == {"ok": True}
        assert client.get("/web/admin/notifications/list").get_json()["unread"] == 0
        assert _read_flags(db_engine)["Route employee"] is False


# ── GET /web/ribbon ──────────────────────────────────────────────────────────

class TestAnnouncementRibbon:
    def _announce(self, db_engine, title, content="Body", visibility="public", target=None, days_ago=0):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO announcements (title, content, priority, visibility, target_employee_id, created_at) "
                    "VALUES (%s, %s, 'Urgent', %s, %s, NOW() - (%s * INTERVAL '1 day'))",
                    (title, content, visibility, target, days_ago))
        cur.close()

    @pytest.fixture(autouse=True)
    def _no_other_announcements(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("DELETE FROM announcements")
        cur.close()

    def test_needs_a_session(self, client):
        assert client.get("/web/ribbon").status_code in (302, 401)

    def test_nothing_recent_means_no_ribbon(self, client, db_engine, seed_admin):
        self._announce(db_engine, "Route stale", days_ago=20)
        _admin_session(client)
        assert client.get("/web/ribbon").get_json() == {"ok": True, "ribbon": None}

    def test_latest_public_announcement_flattened_and_trimmed(self, client, db_engine, seed_admin):
        self._announce(db_engine, "Route older", days_ago=3)
        self._announce(db_engine, "Route newest", content="Line one\nLine two " + "x" * 300, days_ago=1)
        _admin_session(client)
        ribbon = client.get("/web/ribbon").get_json()["ribbon"]
        assert ribbon["title"] == "Route newest" and ribbon["priority"] == "Urgent"
        assert "\n" not in ribbon["text"] and ribbon["text"].startswith("Line one Line two")
        assert len(ribbon["text"]) == 160

    def test_private_announcements_only_reach_their_employee(self, client, db_engine, seed_admin, seed_employee,
                                                             seed_assigned_employee):
        self._announce(db_engine, "Route public", days_ago=2)
        self._announce(db_engine, "Route for TST001", visibility="private", target="TST001", days_ago=1)
        _employee_session(client, "TST001")
        assert client.get("/web/ribbon").get_json()["ribbon"]["title"] == "Route for TST001"
        _employee_session(client, "TST002")
        assert client.get("/web/ribbon").get_json()["ribbon"]["title"] == "Route public"
        _admin_session(client)
        assert client.get("/web/ribbon").get_json()["ribbon"]["title"] == "Route public"
