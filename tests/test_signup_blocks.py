"""Blocking a duplicate signup from Platform Admin's duplicate-alerts page
(blueprints/platform_admin/alerts.py), and every signup entry point
refusing a blocked email or IP (blueprints/org/duplicates.signup_block_for).
"""
import secrets

import pytest

from tests.signup_flow import capture_codes, start_signup
from tests.test_platform_admin import _login_platform_admin


@pytest.fixture
def alert(db_engine):
    email = f"Blocked.{secrets.token_hex(3)}@Example.TEST"
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.tenant_duplicate_alerts (attempted_company_name, attempted_admin_email, "
        "conflicting_tenant_id, conflicting_company_name, source_ip) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        ("Copycat Co", email, 999999, "Original Co", "203.0.113.9"),
    )
    alert_id = cur.fetchone()[0]
    db_engine.commit()
    yield alert_id, email
    cur.execute("DELETE FROM att_master.signup_blocks WHERE alert_id=%s OR value IN (%s, %s)",
                (alert_id, email.lower(), "127.0.0.1"))
    cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE id=%s", (alert_id,))
    db_engine.commit()
    cur.close()


def _blocks(db_engine, alert_id):
    cur = db_engine.cursor()
    cur.execute("SELECT kind, value, reason, blocked_by FROM att_master.signup_blocks WHERE alert_id=%s ORDER BY kind",
                (alert_id,))
    rows = cur.fetchall()
    cur.close()
    return rows


class TestBlockFromAlert:
    def test_requires_login(self, client, alert):
        resp = client.post(f"/super_admin/duplicate-alerts/{alert[0]}/block", data={"block_email": "1"})
        assert resp.headers["Location"] == "/super_admin/login"

    def test_block_email(self, client, db_engine, alert):
        alert_id, email = alert
        _login_platform_admin(client, username="blocking_admin")
        resp = client.post(f"/super_admin/duplicate-alerts/{alert_id}/block",
                           data={"block_email": "1", "reason": "Trial abuse"}, follow_redirects=True)
        assert b"Blocked." in resp.data
        assert _blocks(db_engine, alert_id) == [("email", email.lower(), "Trial abuse", "blocking_admin")]
        cur = db_engine.cursor()
        cur.execute("SELECT acknowledged, action_taken FROM att_master.tenant_duplicate_alerts WHERE id=%s",
                    (alert_id,))
        assert cur.fetchone() == (1, "blocked")
        cur.close()
        # Listed under "Blocked from registering", with a way to undo it.
        page = resp.get_data(as_text=True)
        assert "Blocked from registering" in page and "Unblock" in page

    def test_block_email_and_ip(self, client, db_engine, alert):
        alert_id, email = alert
        _login_platform_admin(client)
        client.post(f"/super_admin/duplicate-alerts/{alert_id}/block", data={"block_email": "1", "block_ip": "1"})
        assert [r[:2] for r in _blocks(db_engine, alert_id)] == [("email", email.lower()), ("ip", "203.0.113.9")]

    def test_nothing_chosen_blocks_nothing(self, client, db_engine, alert):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/duplicate-alerts/{alert[0]}/block", data={}, follow_redirects=True)
        assert b"Choose the email" in resp.data
        assert _blocks(db_engine, alert[0]) == []

    def test_blocking_twice_is_harmless(self, client, db_engine, alert):
        _login_platform_admin(client)
        client.post(f"/super_admin/duplicate-alerts/{alert[0]}/block", data={"block_email": "1"})
        resp = client.post(f"/super_admin/duplicate-alerts/{alert[0]}/block", data={"block_email": "1"},
                           follow_redirects=True)
        assert b"Already blocked" in resp.data
        assert len(_blocks(db_engine, alert[0])) == 1

    def test_unknown_alert(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/duplicate-alerts/999999999/block", data={"block_email": "1"},
                           follow_redirects=True)
        assert b"Alert not found." in resp.data

    def test_bulk_block_blocks_each_email(self, client, db_engine, alert):
        alert_id, email = alert
        _login_platform_admin(client)
        resp = client.post("/super_admin/duplicate-alerts/bulk-block", data={"alert_ids": [str(alert_id)]},
                           follow_redirects=True)
        assert b"Blocked the email of 1 alert(s)." in resp.data
        assert [r[:2] for r in _blocks(db_engine, alert_id)] == [("email", email.lower())]

    def test_unblock(self, client, db_engine, alert):
        alert_id, _email = alert
        _login_platform_admin(client)
        client.post(f"/super_admin/duplicate-alerts/{alert_id}/block", data={"block_email": "1"})
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM att_master.signup_blocks WHERE alert_id=%s", (alert_id,))
        block_id = cur.fetchone()[0]
        cur.close()
        resp = client.post(f"/super_admin/signup-blocks/{block_id}/delete", follow_redirects=True)
        assert b"Unblocked." in resp.data
        assert _blocks(db_engine, alert_id) == []

    def test_delete_alert_keeps_its_block(self, client, db_engine, alert):
        alert_id, _email = alert
        _login_platform_admin(client)
        client.post(f"/super_admin/duplicate-alerts/{alert_id}/block", data={"block_email": "1"})
        resp = client.post(f"/super_admin/duplicate-alerts/{alert_id}/delete", follow_redirects=True)
        assert b"Alert deleted." in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM att_master.tenant_duplicate_alerts WHERE id=%s", (alert_id,))
        assert cur.fetchone() is None
        cur.close()
        assert len(_blocks(db_engine, alert_id)) == 1

    def test_page_offers_the_actions(self, client, alert):
        _login_platform_admin(client)
        page = client.get("/super_admin/duplicate-alerts").get_data(as_text=True)
        assert f'/super_admin/duplicate-alerts/{alert[0]}/block' in page
        assert f'/super_admin/duplicate-alerts/{alert[0]}/delete' in page
        assert 'name="block_ip"' in page  # the alert has a source IP
        assert "/super_admin/companies/999999" in page


class TestBlockedSignupRefused:
    def test_blocked_email_cannot_start_a_signup(self, client, db_engine, monkeypatch, alert):
        alert_id, email = alert
        _login_platform_admin(client)
        client.post(f"/super_admin/duplicate-alerts/{alert_id}/block", data={"block_email": "1"})
        codes = capture_codes(monkeypatch)
        # Any capitalisation of the same address.
        resp = start_signup(client, email.upper())
        assert resp.headers["Location"] == "/create_org"
        assert codes == []
        follow = client.get("/create_org").get_data(as_text=True)
        assert "We can&#39;t accept a new company registration" in follow

    def test_after_unblock_they_can_sign_up(self, client, db_engine, monkeypatch, alert):
        alert_id, email = alert
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.signup_blocks (kind, value, alert_id) VALUES ('email', %s, %s) "
                    "RETURNING id", (email.lower(), alert_id))
        block_id = cur.fetchone()[0]
        db_engine.commit()
        _login_platform_admin(client)
        client.post(f"/super_admin/signup-blocks/{block_id}/delete")
        capture_codes(monkeypatch)
        resp = start_signup(client, email)
        try:
            assert resp.headers["Location"].startswith("/create_org/verify_otp")
        finally:
            cur.execute("DELETE FROM att_master.tenant_applications WHERE LOWER(admin_email)=%s", (email.lower(),))
            db_engine.commit()
            cur.close()

    def test_blocked_ip_refuses_any_email(self, client, db_engine, monkeypatch, alert):
        alert_id, _email = alert
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.signup_blocks (kind, value, alert_id) VALUES ('ip', '127.0.0.1', %s)",
                    (alert_id,))
        db_engine.commit()
        cur.close()
        codes = capture_codes(monkeypatch)
        resp = start_signup(client, f"someone.else.{secrets.token_hex(3)}@example.test")
        assert resp.headers["Location"] == "/create_org"
        assert codes == []

    def test_other_signups_are_unaffected(self, client, db_engine, monkeypatch, alert):
        alert_id, email = alert
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.signup_blocks (kind, value, alert_id) VALUES ('email', %s, %s)",
                    (email.lower(), alert_id))
        db_engine.commit()
        other = f"fine.{secrets.token_hex(3)}@example.test"
        capture_codes(monkeypatch)
        resp = start_signup(client, other)
        try:
            assert resp.headers["Location"].startswith("/create_org/verify_otp")
        finally:
            cur.execute("DELETE FROM att_master.tenant_applications WHERE admin_email=%s", (other,))
            db_engine.commit()
            cur.close()

    def test_google_or_microsoft_signup_refused(self, db_engine, alert):
        from extensions import app
        from blueprints.oauth_signup import _signup_blocked
        alert_id, email = alert
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.signup_blocks (kind, value, alert_id) VALUES ('email', %s, %s)",
                    (email.lower(), alert_id))
        db_engine.commit()
        cur.close()
        with app.test_request_context("/create_org/google/callback"):
            resp = _signup_blocked(email.lower())
            assert resp is not None and resp.status_code in (301, 302)

    def test_lookup_failure_lets_signups_through(self, monkeypatch):
        import database
        from extensions import app
        from blueprints.org import signup_block_for

        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(database, "get_master_db", boom)
        with app.test_request_context("/create_org"):
            assert signup_block_for("anyone@example.test") is None
