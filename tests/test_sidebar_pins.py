"""Pinned items in the left sidebar: POST /api/sidebar_pins saves a person's
pins (admin_ui_prefs) and templates/admin_base.html hands them to
static/sidebar.js through the sidebar's data-pins attribute."""
import json

import pytest

from utils.auth import HR_ROLE


def _login(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def clean_prefs(db_engine):
    def wipe():
        cur = db_engine.cursor()
        cur.execute("DELETE FROM admin_ui_prefs")
        db_engine.commit()
        cur.close()
    wipe()
    yield
    wipe()


def _saved(db_engine, username):
    cur = db_engine.cursor()
    cur.execute("SELECT sidebar_pins FROM admin_ui_prefs WHERE username=%s", (username,))
    row = cur.fetchone()
    cur.close()
    return json.loads(row[0]) if row else None


class TestSavePins:
    def test_saves_a_list_of_paths(self, client, db_engine, seed_admin, clean_prefs):
        _login(client, seed_admin["username"])
        resp = client.post("/api/sidebar_pins", json={"pins": ["/employees", "/tickets"]})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert _saved(db_engine, seed_admin["username"]) == ["/employees", "/tickets"]

    def test_saving_again_replaces_the_list(self, client, db_engine, seed_admin, clean_prefs):
        _login(client, seed_admin["username"])
        client.post("/api/sidebar_pins", json={"pins": ["/employees"]})
        client.post("/api/sidebar_pins", json={"pins": []})
        assert _saved(db_engine, seed_admin["username"]) == []

    @pytest.mark.parametrize("body", [
        {}, {"pins": "/employees"}, {"pins": [1]}, {"pins": ["employees"]},
        {"pins": ["/a b"]}, {"pins": ["https://evil.example/x"]}, {"pins": ["/x"] * 13},
    ])
    def test_rejects_anything_that_is_not_a_short_list_of_page_paths(self, client, db_engine, seed_admin, clean_prefs, body):
        _login(client, seed_admin["username"])
        assert client.post("/api/sidebar_pins", json=body).status_code == 400
        assert _saved(db_engine, seed_admin["username"]) is None

    def test_needs_a_signed_in_admin(self, client, clean_prefs):
        with client.session_transaction() as sess:
            sess.clear()
        assert client.post("/api/sidebar_pins", json={"pins": ["/employees"]}).status_code in (301, 302, 401, 403)


class TestSidebarMarkup:
    def test_nothing_saved_means_empty_data_pins(self, client, seed_admin, clean_prefs):
        _login(client, seed_admin["username"])
        assert b'data-pins=""' in client.get("/employees").data

    def test_saved_pins_reach_the_page(self, client, seed_admin, clean_prefs):
        _login(client, seed_admin["username"])
        client.post("/api/sidebar_pins", json={"pins": ["/tickets"]})
        assert b"data-pins=" in (page := client.get("/employees").data) and b"/tickets" in page
        assert b'data-pins=""' not in page

    def test_pins_belong_to_the_person(self, client, seed_admin, seed_hr_admin, clean_prefs):
        _login(client, seed_admin["username"])
        client.post("/api/sidebar_pins", json={"pins": ["/tickets"]})
        _login(client, seed_hr_admin["username"], HR_ROLE)
        assert b'data-pins=""' in client.get("/employees").data

    def test_page_loads_the_sidebar_script(self, client, seed_admin):
        _login(client, seed_admin["username"])
        assert b"sidebar.js" in client.get("/employees").data
