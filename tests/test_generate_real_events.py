"""scripts/generate_real_events.py: it refuses anything but a local or
explicitly allowed dev server (and production always), and against a real HTTP
server it produces real telemetry -- the events, audit rows, sessions and
request counts the SOC console reads -- through ordinary requests only.
"""
import json
import threading

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import make_server

from extensions import app as flask_app
from scripts import generate_real_events as gen
from utils import http_telemetry


class TestTargets:
    @pytest.mark.parametrize("url", ["http://localhost:5000", "http://127.0.0.1:8000/", "https://hrzest.localhost",
                                     "http://192.168.1.20:5000", "http://10.0.0.7", "http://[::1]:5000"])
    def test_this_machine_and_private_networks_are_allowed(self, url):
        assert gen.check_target(url).startswith(("http://", "https://"))

    @pytest.mark.parametrize("url", ["https://hrzest.com", "https://www.hrzest.com/", "https://acme.hrzest.com"])
    def test_production_is_refused_even_when_allowed(self, url):
        host = url.split("//")[1].strip("/")
        with pytest.raises(gen.Refused, match="production"):
            gen.check_target(url, allow_host=host)

    def test_another_public_host_needs_its_exact_name(self):
        with pytest.raises(gen.Refused, match="--allow-host dev.example.org"):
            gen.check_target("https://dev.example.org")
        with pytest.raises(gen.Refused):
            gen.check_target("https://dev.example.org", allow_host="other.example.org")
        assert gen.check_target("https://dev.example.org", allow_host="dev.example.org") == "https://dev.example.org"

    def test_extra_refused_hosts_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("GEN_REFUSE_HOSTS", "staging.example.org")
        with pytest.raises(gen.Refused, match="production"):
            gen.check_target("https://staging.example.org", allow_host="staging.example.org")

    @pytest.mark.parametrize("url", ["ftp://localhost", "localhost:5000", "file:///etc/passwd"])
    def test_only_http_urls(self, url):
        with pytest.raises(gen.Refused):
            gen.check_target(url)

    def test_refusal_exits_2_without_a_request(self, capsys):
        assert gen.main(["https://hrzest.com"]) == 2
        assert "never runs against production" in capsys.readouterr().err


@pytest.fixture
def server(client):
    """The app on a real HTTP port, trusting one proxy hop as a dev server with
    TRUSTED_PROXY_HOPS=1 does, so the generator's X-Forwarded-For is used."""
    srv = make_server("127.0.0.1", 0, ProxyFix(flask_app, x_for=1), threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://localhost:{srv.server_port}"      # the app sends 127.0.0.1 to localhost
    srv.shutdown()


def _rows(db_engine, sql, params=()):
    cur = db_engine.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _events(db_engine, ip, mark):
    return [r[0] for r in _rows(db_engine, "SELECT event_type FROM security_events WHERE ip=%s AND id > %s", (ip, mark))]


def test_every_scenario_produces_real_telemetry(server, seed_admin, db_engine, monkeypatch, capsys):
    monkeypatch.setenv("GEN_ADMIN_USER", seed_admin["username"])
    monkeypatch.setenv("GEN_ADMIN_PASSWORD", seed_admin["password"])
    monkeypatch.delenv("GEN_EMPLOYEE_ID", raising=False)
    [(mark,)] = _rows(db_engine, "SELECT COALESCE(MAX(id), 0) FROM security_events")
    [(audit_mark,)] = _rows(db_engine, "SELECT COALESCE(MAX(id), 0) FROM audit_logs")
    http_telemetry.reset()

    assert gen.main([server, "--pause", "0"]) == 0
    out = capsys.readouterr().out
    for name in gen.SCENARIOS:
        assert f"  {name:15}" in out and "did not succeed" not in out.split(f"  {name:15}")[1].split("\n")[0], out
    assert seed_admin["password"] not in out                       # credentials are never printed

    # credential stuffing from its own documentation address
    assert _events(db_engine, gen.ADDR["stuffer"], mark).count("auth.failure") >= 10
    # the scanner tripped the honeypot and the WAF
    scanner = _events(db_engine, gen.ADDR["scanner"], mark)
    assert any(t.startswith(("secops.honeypot", "honeypot.")) for t in scanner), scanner
    assert any(t.startswith("waf.") for t in scanner), scanner
    # the admin's session moved to another browser and network
    moved = [r[0] for r in _rows(db_engine, "SELECT event_type FROM security_events WHERE identifier=%s AND id > %s "
                                            "AND event_type LIKE 'session.%%'", (seed_admin["username"], mark))]
    assert "session.browser_changed" in moved and "session.network_changed" in moved
    # payroll exports are in the audit log, by the admin
    exports = _rows(db_engine, "SELECT actor, detail FROM audit_logs WHERE id > %s AND action='export_salary_report'",
                    (audit_mark,))
    assert len(exports) >= 6 and {a for a, _ in exports} == {seed_admin["username"]}
    assert any(json.loads(d or "{}").get("via") == "api" for _, d in
               _rows(db_engine, "SELECT actor, detail FROM audit_logs WHERE id > %s AND action='view_salary_report'",
                     (audit_mark,)))
    # the signed-in sessions list knows the admin
    assert _rows(db_engine, "SELECT COUNT(*) FROM user_sessions WHERE identifier=%s", (seed_admin["username"],))[0][0] >= 1
    # and the request telemetry counted real statuses, by route pattern
    http_telemetry.flush()
    statuses = {r[0] for r in _rows(db_engine, "SELECT DISTINCT status FROM att_master.http_minute_stats")}
    assert {200, 404} <= statuses, statuses


def test_a_missing_credential_skips_what_needs_it(server, monkeypatch, capsys):
    monkeypatch.delenv("GEN_ADMIN_USER", raising=False)
    monkeypatch.delenv("GEN_ADMIN_PASSWORD", raising=False)
    assert gen.main([server, "--scenario", "browse", "--scenario", "failed-logins", "--pause", "0"]) == 0
    out = capsys.readouterr().out
    assert "browse          skipped: set GEN_ADMIN_USER and GEN_ADMIN_PASSWORD" in out
    assert "wrong passwords against 6 made-up accounts" in out


def test_a_server_that_is_not_running(capsys):
    assert gen.main(["http://localhost:9", "--pause", "0"]) == 1
