"""Tests for utils/push_utils.py (Expo push token registration + delivery)
and its two registration routes (blueprints/notifications.py), plus its
wiring into utils/helpers.py's _create_notification(). Every test mocks
utils.push_utils._send_expo_push (the one function that actually calls
out over the network) rather than urllib itself -- same level Expo's own
per-token result format is easiest to fake at, and consistent with how
tests/test_billing.py etc. mock utils.razorpay_utils' equivalent."""
import pytest


def _admin_token(client, seed_admin):
    resp = client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    })
    return resp.get_json()["token"]


def _emp_token(client, seed_employee):
    resp = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    })
    return resp.get_json()["token"]


@pytest.fixture(autouse=True)
def _clean_push_tokens(db_engine):
    yield
    cur = db_engine.cursor()
    cur.execute("DELETE FROM push_tokens")
    cur.execute("DELETE FROM notifications")
    db_engine.commit()
    cur.close()


class TestRegisterPushToken:
    def test_inserts_new_token(self, db_engine):
        from utils.push_utils import register_push_token
        register_push_token("employee", "TST001", "ExponentPushToken[aaa]", "ios")
        cur = db_engine.cursor()
        cur.execute("SELECT owner_type, owner_id, platform FROM push_tokens WHERE expo_push_token=%s",
                    ("ExponentPushToken[aaa]",))
        row = cur.fetchone()
        cur.close()
        assert row == ("employee", "TST001", "ios")

    def test_reregistering_same_token_reassigns_owner(self, db_engine):
        """Same physical device, different account signed in later (a
        shared kiosk phone, or a logout/login as someone else) -- the row
        is updated in place, not duplicated."""
        from utils.push_utils import register_push_token
        register_push_token("employee", "TST001", "ExponentPushToken[shared]", "android")
        register_push_token("admin", "test_admin", "ExponentPushToken[shared]", "android")
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM push_tokens WHERE expo_push_token=%s", ("ExponentPushToken[shared]",))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT owner_type, owner_id FROM push_tokens WHERE expo_push_token=%s",
                    ("ExponentPushToken[shared]",))
        assert cur.fetchone() == ("admin", "test_admin")
        cur.close()


class TestSendPushNotification:
    def test_no_tokens_skips_network_call(self, monkeypatch):
        from utils.push_utils import send_push_notification
        calls = []
        monkeypatch.setattr("utils.push_utils._send_expo_push", lambda *a, **k: calls.append(a) or [])
        send_push_notification("employee", "NO_SUCH_EMPLOYEE", "Title", "Body")
        assert calls == []

    def test_sends_to_every_token_for_owner(self, db_engine, monkeypatch):
        from utils.push_utils import register_push_token, send_push_notification
        register_push_token("employee", "TST001", "ExponentPushToken[one]", "ios")
        register_push_token("employee", "TST001", "ExponentPushToken[two]", "android")
        calls = []
        monkeypatch.setattr("utils.push_utils._send_expo_push",
                             lambda tokens, title, body: calls.append((set(tokens), title, body)) or [])
        send_push_notification("employee", "TST001", "Leave Approved", "Your leave was approved.")
        assert len(calls) == 1
        tokens, title, body = calls[0]
        assert tokens == {"ExponentPushToken[one]", "ExponentPushToken[two]"}
        assert title == "Leave Approved"
        assert body == "Your leave was approved."

    def test_does_not_send_to_a_different_owner(self, db_engine, monkeypatch):
        from utils.push_utils import register_push_token, send_push_notification
        register_push_token("employee", "OTHER_EMP", "ExponentPushToken[notmine]", "ios")
        calls = []
        monkeypatch.setattr("utils.push_utils._send_expo_push", lambda *a, **k: calls.append(a) or [])
        send_push_notification("employee", "TST001", "Title", "Body")
        assert calls == []

    def test_network_exception_does_not_raise(self, db_engine, monkeypatch):
        from utils.push_utils import register_push_token, send_push_notification
        register_push_token("employee", "TST001", "ExponentPushToken[boom]", "ios")
        monkeypatch.setattr("utils.push_utils._send_expo_push",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down")))
        send_push_notification("employee", "TST001", "Title", "Body")  # must not raise

    def test_stale_device_token_is_pruned(self, db_engine, monkeypatch):
        from utils.push_utils import register_push_token, send_push_notification
        register_push_token("employee", "TST001", "ExponentPushToken[stale]", "ios")
        monkeypatch.setattr(
            "utils.push_utils._send_expo_push",
            lambda tokens, title, body: [{"status": "error", "details": {"error": "DeviceNotRegistered"}}],
        )
        send_push_notification("employee", "TST001", "Title", "Body")
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM push_tokens WHERE expo_push_token=%s", ("ExponentPushToken[stale]",))
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_non_stale_error_does_not_delete_token(self, db_engine, monkeypatch):
        from utils.push_utils import register_push_token, send_push_notification
        register_push_token("employee", "TST001", "ExponentPushToken[flaky]", "ios")
        monkeypatch.setattr(
            "utils.push_utils._send_expo_push",
            lambda tokens, title, body: [{"status": "error", "details": {"error": "MessageTooBig"}}],
        )
        send_push_notification("employee", "TST001", "Title", "Body")
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM push_tokens WHERE expo_push_token=%s", ("ExponentPushToken[flaky]",))
        assert cur.fetchone()[0] == 1
        cur.close()


class TestSendPushNotificationBroadcast:
    def test_sends_to_every_admin_type_token_regardless_of_owner(self, db_engine, monkeypatch):
        from utils.push_utils import register_push_token, send_push_notification_broadcast
        register_push_token("admin", "test_admin", "ExponentPushToken[admin1]", "ios")
        register_push_token("admin", "test_hr_admin", "ExponentPushToken[admin2]", "android")
        register_push_token("employee", "TST001", "ExponentPushToken[emp]", "ios")
        calls = []
        monkeypatch.setattr("utils.push_utils._send_expo_push",
                             lambda tokens, title, body: calls.append(set(tokens)) or [])
        send_push_notification_broadcast("admin", "New Ticket", "A new ticket was raised.")
        assert len(calls) == 1
        assert calls[0] == {"ExponentPushToken[admin1]", "ExponentPushToken[admin2]"}


class TestCreateNotificationTriggersPush:
    """utils.helpers._create_notification() -- the one call site every
    leave/ticket/announcement action already goes through -- must attempt
    a push after the in-app row lands, routed by recipient_type."""

    def test_employee_notification_triggers_targeted_push(self, monkeypatch):
        from utils.helpers import _create_notification
        calls = []
        monkeypatch.setattr("utils.push_utils.send_push_notification",
                             lambda *a, **k: calls.append(a))
        _create_notification("employee", "Title", "Body", employee_id="TST001")
        assert calls == [("employee", "TST001", "Title", "Body")]

    def test_admin_notification_triggers_broadcast_push(self, monkeypatch):
        from utils.helpers import _create_notification
        calls = []
        monkeypatch.setattr("utils.push_utils.send_push_notification_broadcast",
                             lambda *a, **k: calls.append(a))
        _create_notification("admin", "Title", "Body")
        assert calls == [("admin", "Title", "Body")]

    def test_failed_db_write_skips_push_entirely(self, monkeypatch):
        """If the in-app notification row itself never landed, there is
        nothing to push about -- confirms the early `return` on the DB
        exception path actually prevents the push call."""
        from utils.helpers import _create_notification

        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr("utils.helpers.get_db_connection", _boom)
        calls = []
        monkeypatch.setattr("utils.push_utils.send_push_notification", lambda *a, **k: calls.append(a))
        _create_notification("employee", "Title", "Body", employee_id="TST001")
        assert calls == []


class TestPushTokenApiEndpoints:
    def test_admin_can_register_push_token(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/push_token",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"expo_push_token": "ExponentPushToken[adminapi]", "platform": "ios"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("SELECT owner_type, owner_id FROM push_tokens WHERE expo_push_token=%s",
                    ("ExponentPushToken[adminapi]",))
        assert cur.fetchone() == ("admin", seed_admin["username"])
        cur.close()

    def test_admin_missing_token_rejected(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/push_token", headers={"Authorization": f"Bearer {token}"}, json={})
        assert resp.status_code == 400

    def test_admin_requires_auth(self, client):
        resp = client.post("/api/push_token", json={"expo_push_token": "x"})
        assert resp.status_code == 401

    def test_employee_can_register_push_token(self, client, seed_employee, db_engine):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/push_token",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"expo_push_token": "ExponentPushToken[empapi]", "platform": "android"})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT owner_type, owner_id FROM push_tokens WHERE expo_push_token=%s",
                    ("ExponentPushToken[empapi]",))
        assert cur.fetchone() == ("employee", seed_employee["employee_id"])
        cur.close()

    def test_employee_missing_token_rejected(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/push_token", headers={"Authorization": f"Bearer {token}"}, json={})
        assert resp.status_code == 400

    def test_employee_requires_auth(self, client):
        resp = client.post("/api/employee/push_token", json={"expo_push_token": "x"})
        assert resp.status_code == 401
