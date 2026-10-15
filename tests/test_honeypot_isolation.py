"""The internet-facing honeypot must run without the app's secrets, and its
hits must still reach the app.

Regression coverage for the audit finding that compose.yaml gave the
honeypot container the entire production .env (SECRET_KEY, DB password,
ENCRYPTION_KEY, SMTP, payment keys) because utils.alerts imported the whole
Flask app just to get a logger.
"""
import datetime
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

from utils import honeypot_report

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = "unit-test-honeypot-secret"


class TestProcessIsolation:
    def test_honeypot_imports_without_flask_or_app_secrets(self):
        """Fresh interpreter, scrubbed environment: the honeypot's import
        chain must not pull in extensions/Flask (which hard-fail without
        SECRET_KEY/REDIS_HOST and open the app's database config)."""
        code = (
            f"import sys; sys.path.insert(0, {REPO_ROOT!r});"
            "import utils.alerts, utils.honeypot, utils.honeypot_report;"
            "bad = [m for m in ('extensions', 'flask', 'psycopg2', 'database', 'app') if m in sys.modules];"
            "assert not bad, bad;"
            "print('isolated')"
        )
        env = {k: v for k, v in os.environ.items()
               if k not in ("SECRET_KEY", "ENCRYPTION_KEY", "DB_PASS", "DB_HOST", "REDIS_HOST", "APP_ENV")}
        env["APP_ENV"] = "production"           # would make extensions.py refuse to start
        out = subprocess.run([sys.executable, "-I", "-c", code], cwd=REPO_ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert "isolated" in out.stdout

    def test_compose_honeypot_service_has_no_env_file_and_no_app_secrets(self):
        text = open(os.path.join(REPO_ROOT, "compose.yaml"), encoding="utf-8").read()
        m = re.search(r"^  honeypot:\n(.*?)(?=^  \w[\w-]*:\n|^volumes:)", text, re.S | re.M)
        assert m, "honeypot service not found in compose.yaml"
        block = m.group(1)
        code_lines = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))
        assert "env_file" not in code_lines
        for secret_name in ("SECRET_KEY", "ENCRYPTION_KEY", "DB_PASS", "SMTP_PASS",
                            "RAZORPAY", "ANTHROPIC_API_KEY"):
            assert secret_name not in code_lines.replace("HONEYPOT_REPORT_SECRET", ""), secret_name
        assert "HONEYPOT_REPORT_SECRET" in code_lines
        nets = re.search(r"networks:\n((?:[ ]+- [\w-]+\n)+)", code_lines + "\n")
        assert nets, "honeypot networks list not found"
        assert re.findall(r"- ([\w-]+)", nets.group(1)) == ["frontend"], \
            "honeypot must never be attached to the backend (db/clamav) network"


class TestSignature:
    BODY = b'{"event":"tcp_decoy_hit","ip":"198.51.100.9","port":3306}'

    def test_roundtrip(self):
        ts = str(int(time.time()))
        sig = honeypot_report.sign(self.BODY, ts, SECRET)
        assert honeypot_report.verify(self.BODY, ts, sig, SECRET)
        assert honeypot_report.verify(self.BODY, ts, "sha256=" + sig, SECRET)

    @pytest.mark.parametrize("mutate", ["body", "secret", "timestamp", "empty_secret", "no_sig"])
    def test_rejections(self, mutate):
        ts = str(int(time.time()))
        sig = honeypot_report.sign(self.BODY, ts, SECRET)
        body, secret, stamp = self.BODY, SECRET, ts
        if mutate == "body":
            body = self.BODY.replace(b"3306", b"3307")
        elif mutate == "secret":
            secret = "another-secret"
        elif mutate == "timestamp":
            stamp = str(int(ts) + 1)
        elif mutate == "empty_secret":
            secret = ""
        elif mutate == "no_sig":
            sig = ""
        assert not honeypot_report.verify(body, stamp, sig, secret)

    def test_stale_and_future_timestamps_rejected(self):
        now = 1_700_000_000
        for ts in (now - honeypot_report.MAX_SKEW_SECONDS - 1, now + honeypot_report.MAX_SKEW_SECONDS + 1):
            sig = honeypot_report.sign(self.BODY, str(ts), SECRET)
            assert not honeypot_report.verify(self.BODY, str(ts), sig, SECRET, now=now)
        ok = honeypot_report.sign(self.BODY, str(now - 10), SECRET)
        assert honeypot_report.verify(self.BODY, str(now - 10), ok, SECRET, now=now)

    def test_non_numeric_timestamp_rejected(self):
        assert not honeypot_report.verify(self.BODY, "yesterday", "abc", SECRET)

    def test_report_never_contains_captured_bytes(self):
        body, headers = honeypot_report.build_request(
            "https://app:5000/webhooks/honeypot", SECRET, "198.51.100.9", 3306, "mysql", 42)
        payload = json.loads(body)
        assert set(payload) == {"event", "ip", "port", "protocol", "captured_bytes"}
        assert payload["captured_bytes"] == 42
        assert honeypot_report.verify(body, headers[honeypot_report.TIMESTAMP_HEADER],
                                      headers[honeypot_report.SIGNATURE_HEADER], SECRET)


class TestReportHit:
    def test_noop_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("HONEYPOT_REPORT_URL", raising=False)
        monkeypatch.delenv("HONEYPOT_REPORT_SECRET", raising=False)
        assert honeypot_report.report_hit("198.51.100.9", 21, "ftp", 0) is False

    def test_rejects_non_http_scheme(self):
        assert honeypot_report.report_hit("198.51.100.9", 21, "ftp", 0,
                                          url="file:///etc/passwd", secret=SECRET) is False

    def test_posts_signed_report_to_a_real_listener(self):
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                seen["body"] = self.rfile.read(n)
                seen["ts"] = self.headers.get(honeypot_report.TIMESTAMP_HEADER)
                seen["sig"] = self.headers.get(honeypot_report.SIGNATURE_HEADER)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        try:
            ok = honeypot_report.report_hit(
                "198.51.100.9", 3389, "rdp", 7,
                url=f"http://127.0.0.1:{srv.server_address[1]}/webhooks/honeypot", secret=SECRET)
        finally:
            t.join(timeout=5)
            srv.server_close()
        assert ok is True
        assert honeypot_report.verify(seen["body"], seen["ts"], seen["sig"], SECRET)
        assert json.loads(seen["body"])["port"] == 3389

    def test_unreachable_app_returns_false_not_raises(self):
        assert honeypot_report.report_hit("198.51.100.9", 21, "ftp", 0,
                                          url="http://127.0.0.1:9/x", secret=SECRET, timeout=1) is False


def _signed_post(client, ip, port=3306, protocol="mysql", secret=SECRET, ts=None, event=None):
    body = json.dumps({"event": event or honeypot_report.EVENT_NAME, "ip": ip, "port": port,
                       "protocol": protocol, "captured_bytes": 0}).encode()
    ts = str(int(time.time())) if ts is None else ts
    return client.post(
        "/webhooks/honeypot", data=body,
        headers={"Content-Type": "application/json",
                 honeypot_report.TIMESTAMP_HEADER: ts,
                 honeypot_report.SIGNATURE_HEADER: "sha256=" + honeypot_report.sign(body, ts, secret)},
    )


def _ban(db_engine, ip):
    cur = db_engine.cursor()
    cur.execute("SELECT banned_by, expires_at, reason FROM public.banned_ips WHERE ip=%s", (ip,))
    row = cur.fetchone()
    cur.close()
    return row


def _unban(db_engine, ip):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM public.banned_ips WHERE ip=%s", (ip,))
    cur.close()


class TestAppSideHandler:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setenv("HONEYPOT_REPORT_SECRET", SECRET)

    def test_valid_report_bans_the_scanner(self, client, db_engine):
        ip = "198.51.100.77"
        _unban(db_engine, ip)
        try:
            resp = _signed_post(client, ip, port=3306, protocol="mysql")
            assert resp.status_code == 200
            row = _ban(db_engine, ip)
            assert row is not None
            assert row[0] == "system:honeypot-tcp"
            assert "3306" in row[2]
            assert row[1] > datetime.datetime.now() + datetime.timedelta(hours=1)
        finally:
            _unban(db_engine, ip)

    def test_banned_scanner_is_then_blocked_on_any_tenant_url(self, client, db_engine):
        ip = "198.51.100.78"
        _unban(db_engine, ip)
        try:
            _signed_post(client, ip)
            # Banned in public.banned_ips; the web-tier hook must honour it
            # even when the request resolves against a different schema.
            blocked = client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": ip})
            assert blocked.status_code == 403
        finally:
            _unban(db_engine, ip)

    def test_bad_signature_is_rejected_and_bans_nothing(self, client, db_engine):
        ip = "198.51.100.79"
        _unban(db_engine, ip)
        resp = _signed_post(client, ip, secret="wrong-secret")
        assert resp.status_code == 400
        assert _ban(db_engine, ip) is None

    def test_stale_timestamp_is_rejected(self, client, db_engine):
        ip = "198.51.100.80"
        _unban(db_engine, ip)
        resp = _signed_post(client, ip, ts=str(int(time.time()) - 3600))
        assert resp.status_code == 400
        assert _ban(db_engine, ip) is None

    def test_unconfigured_secret_rejects_everything(self, client, db_engine, monkeypatch):
        monkeypatch.delenv("HONEYPOT_REPORT_SECRET", raising=False)
        ip = "198.51.100.81"
        resp = _signed_post(client, ip, secret="")
        assert resp.status_code == 400
        assert _ban(db_engine, ip) is None

    @pytest.mark.parametrize("ip", ["10.0.0.4", "127.0.0.1", "172.20.0.1"])
    def test_report_from_shared_address_logs_but_never_bans(self, client, db_engine, ip):
        _unban(db_engine, ip)
        resp = _signed_post(client, ip)
        assert resp.status_code == 200
        assert _ban(db_engine, ip) is None

    def test_garbage_ip_is_ignored(self, client):
        resp = _signed_post(client, "not-an-ip'; DROP TABLE x;--")
        # Valid signature, so the route accepts it; the handler refuses to act.
        assert resp.status_code in (200, 403)   # 403 if the WAF flags the string first
