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

    def test_employee_notification_triggers_targeted_push(self, monkeypatch, seed_employee):
        from utils.helpers import _create_notification
        emp_id = seed_employee["employee_id"]
        calls = []
        monkeypatch.setattr("utils.push_utils.send_push_notification",
                             lambda *a, **k: calls.append(a))
        _create_notification("employee", "Title", "Body", employee_id=emp_id)
        assert calls == [("employee", emp_id, "Title", "Body")]

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

    def test_push_is_dispatched_through_the_async_writer_not_called_directly(self, monkeypatch, seed_employee):
        """The push send must go through utils.async_writer.enqueue_write(),
        not a direct call on the request thread -- _send_expo_push()'s
        blocking urllib call (up to 10s) would otherwise add that much
        latency to whatever leave/ticket/announcement request triggered
        this. Patches enqueue_write itself (rather than relying on
        tests/conftest.py's set_synchronous_mode(True), which would call
        the real function and mask exactly this regression) so this
        assertion is meaningful independent of test-mode synchronicity."""
        from utils.helpers import _create_notification
        import utils.push_utils as push_utils_module
        emp_id = seed_employee["employee_id"]
        enqueue_calls = []
        monkeypatch.setattr("utils.async_writer.enqueue_write", lambda fn, *a, **k: enqueue_calls.append((fn, a)))
        direct_calls = []
        monkeypatch.setattr(push_utils_module, "send_push_notification", lambda *a, **k: direct_calls.append(a))
        _create_notification("employee", "Title", "Body", employee_id=emp_id)
        assert direct_calls == []
        assert len(enqueue_calls) == 1
        fn, args = enqueue_calls[0]
        assert fn is push_utils_module.send_push_notification
        assert args == ("employee", emp_id, "Title", "Body")


class TestPostAnnouncementBroadcastPush:
    """post_announcement()'s public-visibility branch bypasses
    _create_notification() entirely (a batched executemany() instead, for
    a single company-wide round-trip) -- it needs its own push dispatch,
    or a broadcast announcement (the common case) never reaches a phone at
    all while a private one-employee announcement does."""

    def test_public_announcement_dispatches_bulk_push_for_every_active_employee(self, monkeypatch, seed_employee, db_engine):
        from utils.helpers import post_announcement
        cur = db_engine.cursor()
        enqueue_calls = []
        monkeypatch.setattr("utils.async_writer.enqueue_write", lambda fn, *a, **k: enqueue_calls.append((fn, a)))
        import utils.push_utils as push_utils_module
        post_announcement(cur, db_engine, "Office Closed", "Closed tomorrow for maintenance.", "Urgent", "public")
        db_engine.commit()
        cur.execute("DELETE FROM notifications WHERE title LIKE %s", ("%Office Closed%",))
        cur.execute("DELETE FROM announcements WHERE title=%s", ("Office Closed",))
        db_engine.commit()
        cur.close()
        bulk_calls = [c for c in enqueue_calls if c[0] is push_utils_module.send_push_notification_bulk]
        assert len(bulk_calls) == 1
        _, args = bulk_calls[0]
        owner_type, owner_ids, title, body = args
        assert owner_type == "employee"
        assert seed_employee["employee_id"] in owner_ids
        assert "Office Closed" in title

    def test_private_announcement_does_not_use_bulk_push(self, monkeypatch, seed_employee, db_engine):
        """Private (single-employee) announcements already go through
        _create_notification() -> send_push_notification() -- must not
        ALSO fire the bulk path."""
        from utils.helpers import post_announcement
        cur = db_engine.cursor()
        enqueue_calls = []
        monkeypatch.setattr("utils.async_writer.enqueue_write", lambda fn, *a, **k: enqueue_calls.append((fn, a)))
        import utils.push_utils as push_utils_module
        post_announcement(cur, db_engine, "Just For You", "A private note.", "Normal", "private",
                          target_emp=seed_employee["employee_id"])
        db_engine.commit()
        cur.execute("DELETE FROM notifications WHERE title LIKE %s", ("%Just For You%",))
        cur.execute("DELETE FROM announcements WHERE title=%s", ("Just For You",))
        db_engine.commit()
        cur.close()
        assert not any(c[0] is push_utils_module.send_push_notification_bulk for c in enqueue_calls)
        assert any(c[0] is push_utils_module.send_push_notification for c in enqueue_calls)


class TestSendPushNotificationBulk:
    def test_sends_to_tokens_across_multiple_owner_ids_in_one_call(self, db_engine, monkeypatch):
        from utils.push_utils import register_push_token, send_push_notification_bulk
        register_push_token("employee", "TST001", "ExponentPushToken[bulk1]", "ios")
        register_push_token("employee", "OTHER_BULK_EMP", "ExponentPushToken[bulk2]", "android")
        calls = []
        monkeypatch.setattr("utils.push_utils._send_expo_push",
                             lambda tokens, title, body: calls.append(set(tokens)) or [])
        send_push_notification_bulk("employee", ["TST001", "OTHER_BULK_EMP"], "Announcement", "Body")
        assert len(calls) == 1
        assert calls[0] == {"ExponentPushToken[bulk1]", "ExponentPushToken[bulk2]"}

    def test_empty_owner_ids_skips_entirely(self, monkeypatch):
        from utils.push_utils import send_push_notification_bulk
        calls = []
        monkeypatch.setattr("utils.push_utils._send_expo_push", lambda *a, **k: calls.append(a) or [])
        send_push_notification_bulk("employee", [], "Title", "Body")
        assert calls == []


class TestSendExpoPushChunking:
    def test_chunks_requests_at_the_expo_message_cap(self, monkeypatch):
        from utils.push_utils import _send_expo_push, _EXPO_MAX_MESSAGES_PER_REQUEST
        tokens = [f"ExponentPushToken[{i}]" for i in range(_EXPO_MAX_MESSAGES_PER_REQUEST + 5)]
        sent_chunks = []

        class _FakeResp:
            def __init__(self, n):
                self._n = n
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                import json as _json
                return _json.dumps({"data": [{"status": "ok"}] * self._n}).encode("utf-8")

        def _fake_urlopen(req, timeout=None):
            import json as _json
            payload = _json.loads(req.data.decode("utf-8"))
            sent_chunks.append(len(payload))
            return _FakeResp(len(payload))

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        results = _send_expo_push(tokens, "Title", "Body")
        assert sent_chunks == [_EXPO_MAX_MESSAGES_PER_REQUEST, 5]
        assert len(results) == len(tokens)


class TestPushTokensTenantBackfill:
    """push_tokens is only created inside _init_core_tables(), which never
    re-runs against an already-provisioned tenant -- _run_migrations_for_
    all_tenants() only calls _run_schema_migrations(). Without a matching
    CREATE TABLE IF NOT EXISTS in the migrations/column_migrations.py
    chain (which DOES run for every existing tenant), any tenant
    provisioned before this feature shipped would 500 on push-token
    registration forever."""

    def test_run_schema_migrations_actually_calls_the_push_tokens_migration(self, db_engine, monkeypatch):
        """Direct proof of the wiring itself -- _run_schema_migrations() is
        the function _run_migrations_for_all_tenants() calls for every
        already-provisioned tenant; this confirms the new migration is
        actually reachable from there, not just correct in isolation."""
        import migrations.column_migrations as column_migrations_module
        calls = []
        monkeypatch.setattr(column_migrations_module, "_run_push_tokens_table_migration",
                            lambda cursor, db: calls.append(True))
        cur = db_engine.cursor()
        column_migrations_module._run_schema_migrations(cur, db_engine)
        cur.close()
        assert calls == [True]

    def test_migration_creates_push_tokens_if_missing(self, db_engine):
        from migrations.column_migrations import _run_push_tokens_table_migration
        cur = db_engine.cursor()
        cur.execute("DROP TABLE IF EXISTS push_tokens CASCADE")
        cur.execute("SELECT to_regclass('push_tokens')")
        assert cur.fetchone()[0] is None
        _run_push_tokens_table_migration(cur, db_engine)
        cur.execute("SELECT to_regclass('push_tokens')")
        assert cur.fetchone()[0] is not None
        cur.close()

    def test_migration_is_a_noop_when_table_already_exists(self, db_engine):
        """Idempotent -- running it again (as every real boot does, via
        _run_migrations_for_all_tenants()) must not error or touch
        existing rows."""
        from migrations.column_migrations import _run_push_tokens_table_migration
        from utils.push_utils import register_push_token
        register_push_token("employee", "TST001", "ExponentPushToken[idempotent]", "ios")
        cur = db_engine.cursor()
        _run_push_tokens_table_migration(cur, db_engine)
        cur.execute("SELECT owner_id FROM push_tokens WHERE expo_push_token=%s", ("ExponentPushToken[idempotent]",))
        assert cur.fetchone()[0] == "TST001"
        cur.execute("DELETE FROM push_tokens WHERE expo_push_token=%s", ("ExponentPushToken[idempotent]",))
        cur.close()

    def test_backfilled_table_actually_works_end_to_end(self, db_engine):
        """Not just that the table exists -- that a real insert/query
        against it (what register_push_token()/send_push_notification()
        actually do) succeeds afterward, same as a fresh install's copy."""
        from migrations.column_migrations import _run_push_tokens_table_migration
        from utils.push_utils import register_push_token
        cur = db_engine.cursor()
        cur.execute("DROP TABLE IF EXISTS push_tokens CASCADE")
        _run_push_tokens_table_migration(cur, db_engine)
        cur.close()
        register_push_token("employee", "TST001", "ExponentPushToken[backfilled]", "ios")
        cur = db_engine.cursor()
        cur.execute("SELECT owner_id FROM push_tokens WHERE expo_push_token=%s", ("ExponentPushToken[backfilled]",))
        assert cur.fetchone()[0] == "TST001"
        cur.execute("DELETE FROM push_tokens WHERE expo_push_token=%s", ("ExponentPushToken[backfilled]",))
        cur.close()


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
