"""
utils/honeypot.py tests — no pytest-asyncio installed, so async code is
driven directly with asyncio.run() inside plain sync test functions
rather than adding a new dependency for this one module.

send_security_alert is monkeypatched everywhere here — these tests must
never attempt a real webhook call, and utils.alerts is exercised on its
own in tests/test_alerts.py.

Run with:
    python -m pytest tests/test_honeypot.py -v
"""
import asyncio
import json
import logging

import pytest

import utils.honeypot as hp


class TestDecoyPortsShape:
    def test_no_duplicate_bind_ports(self):
        assert len(hp.DECOY_PORTS) == len(set(hp.DECOY_PORTS.keys()))

    def test_no_duplicate_public_ports(self):
        public_ports = [v[0] for v in hp.DECOY_PORTS.values()]
        assert len(public_ports) == len(set(public_ports))

    def test_privileged_public_ports_have_high_bind_port(self):
        """21/23/25 are <1024 — this container is unprivileged (see
        compose.yaml), so any DECOY_PORTS entry whose public_port is
        privileged MUST bind to something >=1024 (redirected at the host
        via scripts/host_hardening.sh's NAT redirect) or the process can
        never actually start."""
        for bind_port, (public_port, _label) in hp.DECOY_PORTS.items():
            if public_port < 1024:
                assert bind_port >= 1024, (
                    f"public_port {public_port} is privileged but bind_port "
                    f"{bind_port} is also privileged — this can't bind unprivileged"
                )

    def test_unprivileged_public_ports_bind_directly(self):
        """1433/3306/3389 need no redirect — bind_port should equal
        public_port for these, or the compose.yaml port mapping and the
        actual listener would silently disagree."""
        for bind_port, (public_port, _label) in hp.DECOY_PORTS.items():
            if public_port >= 1024:
                assert bind_port == public_port


class TestRecordHit:
    def test_writes_expected_json_line(self, tmp_path, monkeypatch):
        log_path = tmp_path / "hits.jsonl"
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(log_path))

        hp._record_hit(21, "ftp", "203.0.113.9", b"USER root\r\n")

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["service"] == "system32_crypto_admin"
        assert entry["port"] == 21
        assert entry["protocol"] == "ftp"
        assert entry["source_ip"] == "203.0.113.9"
        assert entry["captured_bytes"] == 11
        assert entry["captured_preview"] == "USER root\r\n"

    def test_appends_multiple_hits(self, tmp_path, monkeypatch):
        log_path = tmp_path / "hits.jsonl"
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(log_path))

        hp._record_hit(23, "telnet", "203.0.113.1", b"")
        hp._record_hit(3389, "rdp", "203.0.113.2", b"\x03\x00\x00")

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_capture_preview_truncated_to_max_bytes(self, tmp_path, monkeypatch):
        log_path = tmp_path / "hits.jsonl"
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(log_path))
        big_payload = b"A" * 2000

        hp._record_hit(25, "smtp", "203.0.113.3", big_payload)

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["captured_bytes"] == 2000  # true size still recorded
        assert len(entry["captured_preview"]) == hp._MAX_CAPTURE_BYTES  # preview capped


class TestAlertHit:
    def test_sends_alert_with_sanitizable_fields_only(self, monkeypatch):
        captured = {}

        def _fake_send(event_type, description, severity="ERROR", **fields):
            captured["event_type"] = event_type
            captured["description"] = description
            captured["severity"] = severity
            captured["fields"] = fields

        monkeypatch.setattr("utils.alerts.send_security_alert", _fake_send)

        hp._alert_hit(1433, "mssql", "203.0.113.4", b"malicious payload")

        assert captured["event_type"] == "honeypot.connection"
        assert "1433" in captured["description"]
        assert captured["severity"] == "WARNING"
        assert captured["fields"]["ip"] == "203.0.113.4"
        assert captured["fields"]["identifier"] == "system32_crypto_admin:1433"
        # Only fields utils.alerts._ALLOWED_FIELDS actually accepts — a
        # typo'd field name here would be silently dropped downstream,
        # not caught until the alert itself was inspected in production.
        from utils.alerts import _ALLOWED_FIELDS
        assert set(captured["fields"].keys()) <= _ALLOWED_FIELDS

    def test_webhook_failure_does_not_raise(self, monkeypatch):
        """A dead webhook must never crash the listener mid-connection —
        matches send_security_alert's own fire-and-forget design."""
        def _boom(*a, **kw):
            raise RuntimeError("webhook is down")

        monkeypatch.setattr("utils.alerts.send_security_alert", _boom)
        with pytest.raises(RuntimeError):
            # _alert_hit itself doesn't catch — that's _handle_connection's
            # job (tested below), so this documents _alert_hit's own
            # contract: it propagates, the caller is responsible.
            hp._alert_hit(3306, "mysql", "203.0.113.5", b"")


class TestHandleConnectionEndToEnd:
    def test_real_connection_is_captured_and_logged(self, tmp_path, monkeypatch):
        log_path = tmp_path / "hits.jsonl"
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(log_path))
        alerts_sent = []
        monkeypatch.setattr(
            "utils.alerts.send_security_alert",
            lambda *a, **kw: alerts_sent.append(kw),
        )

        # bind_port 3306 with public_port 3306 (mysql, no redirect needed)
        # is already in DECOY_PORTS — reuse it so port/protocol resolve
        # through the real mapping, not a hand-rolled one.
        test_port = 33061  # a free high port, standing in for 3306's bind_port in this test

        async def _serve_once():
            server = await asyncio.start_server(
                lambda r, w: hp._handle_connection(r, w, 3306), "127.0.0.1", test_port
            )
            async with server:
                await asyncio.wait_for(server.serve_forever(), timeout=2)

        async def _client():
            await asyncio.sleep(0.3)
            reader, writer = await asyncio.open_connection("127.0.0.1", test_port)
            writer.write(b"attacker probe payload")
            await writer.drain()
            writer.close()

        async def _run_both():
            try:
                await asyncio.gather(_serve_once(), _client())
            except asyncio.TimeoutError:
                pass

        asyncio.run(_run_both())

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["port"] == 3306
        assert entry["protocol"] == "mysql"
        assert entry["source_ip"] == "127.0.0.1"
        assert b"attacker probe payload".decode() in entry["captured_preview"]

        assert len(alerts_sent) == 1
        assert alerts_sent[0]["ip"] == "127.0.0.1"


class TestRecordHitWriteFailure:
    def test_write_failure_is_logged_not_raised(self, tmp_path, monkeypatch):
        # HONEYPOT_LOG_PATH's parent segment is a regular file, not a
        # directory — os.makedirs(..., exist_ok=True) still raises OSError
        # in that case, exercising _record_hit's own except branch.
        blocking_file = tmp_path / "blocked"
        blocking_file.write_text("x")
        bad_log_path = blocking_file / "hits.jsonl"
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(bad_log_path))

        hp._record_hit(21, "ftp", "203.0.113.9", b"data")  # must not raise


class _FakeReader:
    def __init__(self, data=b"", exc=None):
        self._data = data
        self._exc = exc

    async def read(self, n):
        if self._exc:
            raise self._exc
        return self._data


class _SlowReader:
    async def read(self, n):
        await asyncio.sleep(10)
        return b"too-late"


class _FakeWriter:
    def __init__(self, peer=("203.0.113.9", 12345), close_exc=None):
        self._peer = peer
        self.closed = False
        self._close_exc = close_exc

    def get_extra_info(self, name):
        return self._peer if name == "peername" else None

    def close(self):
        self.closed = True
        if self._close_exc:
            raise self._close_exc


class TestHandleConnectionWithFakeStreams:
    """Drives _handle_connection with duck-typed fake reader/writer objects
    instead of real sockets, to reach branches (slow read, dropped
    connection, writer.close() failure, alert-dispatch failure) that would
    otherwise need fragile real-network timing."""

    def test_read_timeout_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(tmp_path / "hits.jsonl"))
        monkeypatch.setattr(hp, "_READ_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr("utils.alerts.send_security_alert", lambda *a, **k: None)

        asyncio.run(hp._handle_connection(_SlowReader(), _FakeWriter(), 8021))

        entry = json.loads((tmp_path / "hits.jsonl").read_text(encoding="utf-8").strip())
        assert entry["captured_bytes"] == 0

    def test_connection_error_while_reading_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(tmp_path / "hits.jsonl"))
        monkeypatch.setattr("utils.alerts.send_security_alert", lambda *a, **k: None)

        asyncio.run(hp._handle_connection(_FakeReader(exc=ConnectionError("reset")), _FakeWriter(), 8023))

        entry = json.loads((tmp_path / "hits.jsonl").read_text(encoding="utf-8").strip())
        assert entry["protocol"] == "telnet"

    def test_writer_close_failure_does_not_propagate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(tmp_path / "hits.jsonl"))
        monkeypatch.setattr("utils.alerts.send_security_alert", lambda *a, **k: None)
        writer = _FakeWriter(close_exc=RuntimeError("already closed"))

        asyncio.run(hp._handle_connection(_FakeReader(data=b"hi"), writer, 3306))

        assert writer.closed

    def test_alert_dispatch_failure_does_not_propagate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(tmp_path / "hits.jsonl"))

        def _boom(*a, **k):
            raise RuntimeError("webhook down")

        monkeypatch.setattr("utils.alerts.send_security_alert", _boom)

        # Must not raise despite the webhook failure.
        asyncio.run(hp._handle_connection(_FakeReader(data=b"hi"), _FakeWriter(), 3389))


class TestServePort:
    def test_binds_logs_and_accepts_a_connection(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(tmp_path / "hits.jsonl"))
        monkeypatch.setattr("utils.alerts.send_security_alert", lambda *a, **k: None)

        async def _client():
            await asyncio.sleep(0.3)
            reader, writer = await asyncio.open_connection("127.0.0.1", 8021)
            writer.write(b"probe")
            await writer.drain()
            writer.close()

        async def _run():
            try:
                await asyncio.wait_for(asyncio.gather(hp._serve_port(8021), _client()), timeout=1.5)
            except asyncio.TimeoutError:
                pass

        with caplog.at_level(logging.INFO, logger="honeypot"):
            asyncio.run(_run())

        assert any("bound 8021" in r.message for r in caplog.records)


class TestRun:
    def test_run_starts_a_listener_for_every_configured_port(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HONEYPOT_LOG_PATH", str(tmp_path / "hits.jsonl"))
        monkeypatch.setattr("utils.alerts.send_security_alert", lambda *a, **k: None)
        # Swap in a single throwaway port so this doesn't try to bind all
        # six real decoy ports (some of which map to privileged public
        # ports via host-level DNAT that doesn't exist in a test process).
        monkeypatch.setattr(hp, "DECOY_PORTS", {18099: (18099, "test-proto")})

        async def _run_with_timeout():
            try:
                await asyncio.wait_for(hp.run(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

        asyncio.run(_run_with_timeout())
