"""Tests for security_events persistence: every log_security_event() call
(extensions.py) now also writes to the security_events table via the
background writer thread (utils/async_writer.py), so the SOC dashboard has
real data to show instead of just the log stream."""
import time
import psycopg2
import pytest
from extensions import log_security_event


def _purge_security_events(db_engine, identifier):
    """security_events is append-only in Postgres (a BEFORE UPDATE OR DELETE
    trigger rejects mutation — see app.py's _reject_audit_mutation) so test
    cleanup needs the same explicit, narrow bypass a DBA would use; the app
    itself never sets this GUC, so this doesn't weaken production behavior."""
    cur = db_engine.cursor()
    cur.execute("SET audit.bypass = 'on'")
    cur.execute("DELETE FROM security_events WHERE identifier=%s", (identifier,))
    cur.execute("SET audit.bypass = 'off'")
    cur.close()


def _wait_for_event(db_engine, identifier, timeout=2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = db_engine.cursor()
        cur.execute("SELECT event_type, level, message FROM security_events WHERE identifier=%s", (identifier,))
        row = cur.fetchone()
        cur.close()
        if row:
            return row
        time.sleep(0.05)
    return None


class TestSecurityEventPersistence:
    def test_event_lands_in_db_asynchronously(self, client, db_engine):
        # `client` fixture ensures the Flask app (and its background writer
        # thread) is initialized before this runs.
        identifier = "TEST_PERSIST_001"
        with client.application.test_request_context("/some/path"):
            log_security_event("test.event", "A test security event", level="WARNING",
                               identifier=identifier)

        row = _wait_for_event(db_engine, identifier)
        assert row is not None
        event_type, level, message = row
        assert event_type == "test.event"
        assert level == "WARNING"
        assert message == "A test security event"

        _purge_security_events(db_engine, identifier)

    def test_extra_fields_stored_as_json(self, client, db_engine):
        identifier = "TEST_PERSIST_002"
        with client.application.test_request_context("/some/path"):
            log_security_event("test.event2", "Another test event", level="INFO",
                               identifier=identifier, custom_field="custom_value", score="42")

        row = _wait_for_event(db_engine, identifier)
        assert row is not None

        cur = db_engine.cursor()
        cur.execute("SELECT extra_json FROM security_events WHERE identifier=%s", (identifier,))
        extra_json = cur.fetchone()[0]
        cur.close()
        assert extra_json is not None
        assert "custom_value" in extra_json
        assert "42" in extra_json

        _purge_security_events(db_engine, identifier)

    def test_error_level_still_persists_alongside_webhook(self, client, db_engine, monkeypatch):
        # send_security_alert is lazily imported inside log_security_event;
        # stub it so this test doesn't attempt a real webhook call.
        import utils.alerts
        monkeypatch.setattr(utils.alerts, "send_security_alert", lambda *a, **kw: None)

        identifier = "TEST_PERSIST_003"
        with client.application.test_request_context("/some/path"):
            log_security_event("test.error_event", "A test ERROR event", level="ERROR",
                               identifier=identifier)

        row = _wait_for_event(db_engine, identifier)
        assert row is not None
        assert row[1] == "ERROR"

        _purge_security_events(db_engine, identifier)


class TestRealTimeAlertDispatch:
    """log_security_event()'s real-time webhook dispatch (utils.alerts.
    send_security_alert) must fire for ERROR and CRITICAL, and only those --
    CRITICAL specifically because blueprints/honeypot_routes.py logs
    deception-honeypot trap hits at that level, and a scanner bot tripping
    a decoy route with no real-time alert reaching anyone defeats the
    point of having a honeypot at all."""

    def test_error_level_triggers_alert(self, client, db_engine, monkeypatch):
        import utils.alerts
        calls = []
        monkeypatch.setattr(utils.alerts, "send_security_alert",
                            lambda *a, **kw: calls.append((a, kw)))
        identifier = "TEST_ALERT_ERROR"
        with client.application.test_request_context("/some/path"):
            log_security_event("test.error_event", "msg", level="ERROR", identifier=identifier)
        assert len(calls) == 1
        assert calls[0][1]["level"] == "ERROR"
        _purge_security_events(db_engine, identifier)

    def test_critical_level_triggers_alert(self, client, db_engine, monkeypatch):
        import utils.alerts
        calls = []
        monkeypatch.setattr(utils.alerts, "send_security_alert",
                            lambda *a, **kw: calls.append((a, kw)))
        identifier = "TEST_ALERT_CRITICAL"
        with client.application.test_request_context("/some/path", environ_base={"REMOTE_ADDR": "203.0.113.5"}):
            log_security_event("secops.honeypot_triggered", "trap hit", level="CRITICAL",
                               identifier=identifier)
        assert len(calls) == 1
        assert calls[0][1]["level"] == "CRITICAL"
        assert calls[0][1]["ip"] == "203.0.113.5"
        _purge_security_events(db_engine, identifier)

    def test_warning_level_does_not_trigger_alert(self, client, db_engine, monkeypatch):
        import utils.alerts
        calls = []
        monkeypatch.setattr(utils.alerts, "send_security_alert",
                            lambda *a, **kw: calls.append((a, kw)))
        identifier = "TEST_ALERT_WARNING"
        with client.application.test_request_context("/some/path"):
            log_security_event("test.warning_event", "msg", level="WARNING", identifier=identifier)
        assert calls == []
        _purge_security_events(db_engine, identifier)


class TestAuditTablesAreAppendOnly:
    """security_events and audit_logs must be tamper-resistant even against
    a party with direct DB access (not just app-level guards) — a BEFORE
    UPDATE OR DELETE trigger (app.py's _reject_audit_mutation) rejects
    mutation unless the narrow `audit.bypass` session GUC is explicitly set,
    which the app itself never does. The trigger is FOR EACH ROW, so it only
    fires on rows actually matched — each test seeds one real row first."""

    def _seed(self, cur, identifier):
        cur.execute(
            "INSERT INTO security_events (event_type, level, message, identifier) "
            "VALUES ('test.immutability_probe', 'INFO', 'probe', %s)",
            (identifier,),
        )

    def _cleanup(self, cur, identifier):
        cur.execute("SET audit.bypass = 'on'")
        cur.execute("DELETE FROM security_events WHERE identifier=%s", (identifier,))
        cur.execute("SET audit.bypass = 'off'")

    def test_delete_without_bypass_is_rejected(self, db_engine):
        identifier = "IMMUTABLE_PROBE_DELETE"
        cur = db_engine.cursor()
        self._seed(cur, identifier)
        try:
            cur.execute("SET audit.bypass = 'off'")
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("DELETE FROM security_events WHERE identifier=%s", (identifier,))
        finally:
            db_engine.rollback()
            self._cleanup(cur, identifier)
            cur.close()

    def test_update_without_bypass_is_rejected(self, db_engine):
        identifier = "IMMUTABLE_PROBE_UPDATE"
        cur = db_engine.cursor()
        self._seed(cur, identifier)
        try:
            cur.execute("SET audit.bypass = 'off'")
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("UPDATE security_events SET message='tampered' WHERE identifier=%s", (identifier,))
        finally:
            db_engine.rollback()
            self._cleanup(cur, identifier)
            cur.close()

    def test_delete_with_bypass_succeeds(self, db_engine):
        identifier = "IMMUTABLE_PROBE_BYPASS"
        cur = db_engine.cursor()
        self._seed(cur, identifier)
        cur.execute("SET audit.bypass = 'on'")
        cur.execute("DELETE FROM security_events WHERE identifier=%s", (identifier,))
        cur.execute("SET audit.bypass = 'off'")
        cur.execute("SELECT 1 FROM security_events WHERE identifier=%s", (identifier,))
        assert cur.fetchone() is None
        cur.close()
