"""The host firewall agent (scripts/firewall_agent/hrzest_fw_agent.py):
log parsing for every layer, ban-list planning, and -- end to end against the
real app -- that what it sends is accepted, stored and shown, and that the
ban list it enforces is the one the SOC console manages.
"""
import base64
import importlib.util
import ipaddress
import json
import os
import pathlib
import time

import pytest

from extensions import app as flask_app
from utils import firewall_events, waf

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "firewall_agent" / "hrzest_fw_agent.py"
_spec = importlib.util.spec_from_file_location("hrzest_fw_agent", _PATH)
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

SECRET = "a" * 40
NOW = time.time()


def unb64(s):
    return base64.b64decode(s).decode() if s else None


class TestHostFirewallLines:
    LINE = ("2026-11-21T06:31:51.601234+05:30 ip-10-0-1-5 kernel: [FW-DROP] IN=ens5 OUT= MAC=0a:1b SRC=45.33.32.156 "
            "DST=10.0.1.5 LEN=60 TOS=0x00 PREC=0x00 TTL=50 ID=1 DF PROTO=TCP SPT=51515 DPT=3389 WINDOW=64240 SYN URGP=0")

    def test_inbound_drop(self):
        e = agent.parse_kern_line(self.LINE, NOW)
        assert (e["layer"], e["action"], e["src_ip"], e["dst_port"], e["protocol"], e["rule"]) == \
            ("host", "block", "45.33.32.156", 3389, "tcp", "FW-DROP")
        import calendar
        assert e["ts"] == calendar.timegm((2026, 11, 21, 6, 31, 51, 0, 0, 0)) - 5.5 * 3600   # the line's own time

    def test_offset_is_applied(self):
        utc = agent.parse_kern_line(self.LINE.replace("+05:30", "Z"), NOW)["ts"]
        ist = agent.parse_kern_line(self.LINE, NOW)["ts"]
        assert utc - ist == 5.5 * 3600

    def test_outbound_drop_names_the_remote_address(self):
        line = self.LINE.replace("[FW-DROP]", "[FW-DROP-OUT]")
        line = line.replace("SRC=45.33.32.156 DST=10.0.1.5", "SRC=10.0.1.5 DST=198.51.100.66")
        e = agent.parse_kern_line(line, NOW)
        assert (e["src_ip"], e["rule"]) == ("198.51.100.66", "FW-DROP-OUT")
        assert unb64(e["detail_b64"]) == "outbound to this address"

    def test_ban_list_and_old_syslog_format(self):
        line = "Nov 21 06:31:51 web kernel: [12.34] [FW-DROP-BL] IN=ens5 SRC=2001:db8::5 DST=2001:db8::1 PROTO=UDP DPT=53"
        e = agent.parse_kern_line(line, NOW)
        assert (e["src_ip"], e["protocol"], e["rule"], e["dst_port"]) == ("2001:db8::5", "udp", "FW-DROP-BL", 53)

    @pytest.mark.parametrize("line", ["kernel: [UFW BLOCK] SRC=1.2.3.4", "[FW-DROP] SRC=not-an-ip DPT=1", ""])
    def test_other_lines_are_ignored(self, line):
        assert agent.parse_kern_line(line, NOW) is None


class TestFail2banLines:
    def test_ban_and_unban(self):
        e = agent.parse_fail2ban_line("2026-11-21 06:31:51,601 fail2ban.actions        [125]: NOTICE  [hrzest-modsec] Ban 45.33.32.156")
        assert (e["layer"], e["action"], e["src_ip"], e["rule"]) == ("fail2ban", "ban", "45.33.32.156", "hrzest-modsec")
        e = agent.parse_fail2ban_line("2026-11-21 07:31:51,601 fail2ban.actions        [125]: NOTICE  [sshd] Unban 45.33.32.156")
        assert e["action"] == "unban"

    @pytest.mark.parametrize("line", [
        "2026-11-21 06:31:51,601 fail2ban.actions        [125]: NOTICE  [sshd] Restore Ban 45.33.32.156",
        "2026-11-21 06:31:51,601 fail2ban.filter         [125]: INFO    [sshd] Found 45.33.32.156",
        "2026-11-21 06:31:51,601 fail2ban.actions        [125]: NOTICE  [sshd] Ban not-an-ip",
    ])
    def test_not_decisions(self, line):
        assert agent.parse_fail2ban_line(line) is None


class TestModSecurityRecords:
    def record(self, code=403, rules=("941100", "941110", "949110"), ip="45.33.32.156"):
        return json.dumps({"transaction": {
            "client_ip": ip, "time_stamp": "Sat Nov 21 06:21:24 2026",
            "request": {"method": "GET", "http_version": 2.0, "uri": "/acme/x?q=<script>alert(1)</script>"},
            "response": {"http_code": code},
            "messages": [{"message": "m", "details": {"ruleId": r}} for r in rules]}})

    def test_block(self):
        e = agent.parse_modsec_record(self.record(), NOW)
        assert (e["layer"], e["action"], e["src_ip"], e["rule"], e["method"]) == ("proxy", "block", "45.33.32.156", "949110", "GET")
        assert unb64(e["path_b64"]) == "/acme/x?q=<script>alert(1)</script>"
        assert unb64(e["detail_b64"]) == "CRS rules 941100,941110"

    def test_detection_only_is_a_count(self):
        assert agent.parse_modsec_record(self.record(code=200), NOW)["action"] == "count"

    def test_nginx_rate_limit(self):
        e = agent.parse_modsec_record(self.record(code=429, rules=()), NOW)
        assert (e["action"], e["rule"]) == ("block", "nginx_limit_req")

    @pytest.mark.parametrize("bad", ["not json", "{}", json.dumps({"transaction": {"client_ip": "x"}}),
                                     json.dumps({"transaction": {"client_ip": "1.2.3.4", "response": {"http_code": 500}}})])
    def test_ignored(self, bad):
        assert agent.parse_modsec_record(bad, NOW) is None


class TestAwsRecords:
    WAF = {
        "timestamp": 1795669311000, "action": "BLOCK", "terminatingRuleId": "AWS-AWSManagedRulesSQLiRuleSet",
        "terminatingRuleType": "MANAGED_RULE_GROUP",
        "ruleGroupList": [{"ruleGroupId": "AWS#AWSManagedRulesSQLiRuleSet",
                           "terminatingRule": {"ruleId": "SQLi_QUERYARGUMENTS", "action": "BLOCK"}}],
        "httpRequest": {"clientIp": "45.33.32.156", "country": "NL", "uri": "/acme/admin", "args": "id=1' OR 1=1--",
                        "httpMethod": "GET", "headers": [{"name": "Host", "value": "hrzest.com"},
                                                         {"name": "Cookie", "value": "secret"}]},
    }

    def test_waf_block(self):
        e = agent.parse_waf_log(json.dumps(self.WAF))
        assert (e["layer"], e["action"], e["src_ip"], e["country"], e["host"], e["rule"]) == \
            ("edge", "block", "45.33.32.156", "NL", "hrzest.com", "AWSManagedRulesSQLiRuleSet:SQLi_QUERYARGUMENTS")
        assert unb64(e["path_b64"]) == "/acme/admin?id=1' OR 1=1--"
        assert e["ts"] == 1795669311.0
        assert "secret" not in json.dumps(e)                    # headers are never forwarded

    def test_rate_rule_and_captcha(self):
        rec = dict(self.WAF, action="CAPTCHA", terminatingRuleId="rate-login", ruleGroupList=[])
        e = agent.parse_waf_log(json.dumps(rec))
        assert (e["action"], e["rule"]) == ("challenge", "rate-login")

    def test_managed_rules_in_count_mode_are_counts(self):
        rec = dict(self.WAF, action="ALLOW", terminatingRuleId="Default_Action",
                   ruleGroupList=[{"ruleGroupId": "AWS#AWSManagedRulesCommonRuleSet",
                                   "terminatingRule": {"ruleId": "CrossSiteScripting_QUERYARGUMENTS", "action": "BLOCK"}}])
        e = agent.parse_waf_log(json.dumps(rec))
        assert (e["action"], e["rule"]) == ("count", "AWSManagedRulesCommonRuleSet:CrossSiteScripting_QUERYARGUMENTS")
        rec = dict(self.WAF, action="ALLOW", terminatingRuleId="Default_Action",
                   ruleGroupList=[{"ruleGroupId": "AWS#AWSManagedRulesCommonRuleSet", "terminatingRule": None,
                                   "nonTerminatingMatchingRules": [{"ruleId": "SizeRestrictions_BODY", "action": "COUNT"}]}])
        assert agent.parse_waf_log(json.dumps(rec))["rule"] == "AWSManagedRulesCommonRuleSet:SizeRestrictions_BODY"

    def test_a_trusted_address_is_not_a_finding(self):
        rec = dict(self.WAF, action="ALLOW", terminatingRuleId="Default_Action", ruleGroupList=[],
                   nonTerminatingMatchingRules=[{"ruleId": "trusted", "action": "COUNT"}])
        assert agent.parse_waf_log(json.dumps(rec)) is None

    def test_flow_reject_only(self):
        rej = "2 123456789012 eni-0a1b 45.33.32.156 10.0.1.5 51515 5432 6 3 180 1795669300 1795669360 REJECT OK"
        e = agent.parse_flow_log(rej)
        assert (e["layer"], e["src_ip"], e["dst_port"], e["protocol"], e["hits"]) == ("network", "45.33.32.156", 5432, "tcp", 3)
        assert agent.parse_flow_log(rej.replace("REJECT", "ACCEPT")) is None
        assert agent.parse_flow_log("garbage") is None


class TestAggregationAndTail:
    def test_repeats_collapse(self):
        e = {"layer": "host", "action": "block", "src_ip": "45.33.32.156", "rule": "FW-DROP", "dst_port": 22}
        out = agent.aggregate([dict(e), dict(e), dict(e, dst_port=23), dict(e, hits=5)])
        assert sorted(x["hits"] for x in out) == [1, 7]

    def test_tail_starts_at_the_end_follows_appends_and_rotation(self, tmp_path):
        log = tmp_path / "x.log"
        log.write_text("old 1\nold 2\n")
        state = {}
        t = agent.FileTail(str(log), state, "k")
        assert t.read() == []                       # history is not replayed
        with open(log, "a") as fh:
            fh.write("new 1\nnew 2\nhalf")
        assert t.read() == ["new 1", "new 2"]       # the half-written line waits
        with open(log, "a") as fh:
            fh.write(" done\n")
        assert t.read() == ["half done"]
        log.write_text("after truncate\n")          # copytruncate: smaller than the offset
        assert t.read() == ["after truncate"]
        os.replace(log, tmp_path / "x.log.1")
        log.write_text("rotated 1\n")               # new inode
        assert t.read() == ["rotated 1"]

    def test_missing_file_is_reported(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            agent.FileTail(str(tmp_path / "nope"), {}, "k").read()


class TestBanListPlanning:
    def test_never_block_and_live_ssh_peers_are_kept_out(self):
        bl = {"block": [{"cidr": "45.33.32.156/32"}, {"cidr": "198.51.100.0/24"}, {"cidr": "203.0.113.5/32"},
                        {"cidr": "2001:db8::/48"}, {"cidr": "garbage"}]}
        v4, v6, skipped = agent.plan_blocks(bl, agent._networks("203.0.113.0/28"), [ipaddress.ip_address("198.51.100.7")])
        assert v4 == ["45.33.32.156/32"] and v6 == ["2001:db8::/48"]
        assert sorted(skipped) == ["198.51.100.0/24", "203.0.113.5/32"]

    def test_nft_script_replaces_both_sets_atomically(self):
        s = agent.nft_script(["45.33.32.156/32"], [])
        assert s.splitlines()[:2] == ["flush set inet hrzest_fw block4", "flush set inet hrzest_fw block6"]
        assert "add element inet hrzest_fw block4 { 45.33.32.156/32 }" in s
        big = agent.nft_script([f"10.0.{i // 256}.{i % 256}/32" for i in range(1200)], [])
        assert big.count("add element") == 3                     # chunked

    def test_config_validation(self, monkeypatch):
        for k in list(os.environ):
            if k.startswith("HRZEST_FW_"):
                monkeypatch.delenv(k)
        problems = agent.Config().problems()
        assert any("SECRET" in p for p in problems) and any("APP_HOST" in p for p in problems)
        monkeypatch.setenv("HRZEST_FW_SECRET", SECRET)
        monkeypatch.setenv("HRZEST_FW_APP_HOST", "hrzest.com")
        monkeypatch.setenv("HRZEST_FW_WAF_BLOCK_V4", "not-a-ref")
        assert [p for p in agent.Config().problems()] == ["HRZEST_FW_WAF_BLOCK_V4 must be <name>/<id> (terraform output)"]


# ── end to end, against the app ──────────────────────────────────────────────
class _FlaskAppClient(agent.AppClient):
    """agent.AppClient with the HTTP hop replaced by Flask's test client: the
    body, headers and signature are built by the agent's own code."""

    def __init__(self, cfg, client):
        super().__init__(cfg)
        self.flask = client

    def request(self, method, path, body=b"", signed_message=None):
        headers = agent.signed_headers(signed_message if signed_message is not None else body, self.cfg.secret)
        headers["User-Agent"] = f"hrzest-fw-agent/{agent.VERSION}"
        kw = {"headers": headers, "environ_overrides": {"REMOTE_ADDR": "127.0.0.1"}}
        if body:
            r = self.flask.open(path, method=method, data=body, content_type="application/json", **kw)
        else:
            r = self.flask.open(path, method=method, **kw)
        return r.status_code, r.get_data()


@pytest.fixture
def wired(tmp_path, monkeypatch, db_engine, client):
    monkeypatch.setenv("FIREWALL_AGENT_SECRET", SECRET)
    for k in list(os.environ):
        if k.startswith("HRZEST_FW_"):
            monkeypatch.delenv(k)
    env = {"HRZEST_FW_SECRET": SECRET, "HRZEST_FW_APP_URL": "http://127.0.0.1", "HRZEST_FW_SOURCE": "host-test",
           "HRZEST_FW_STATE_DIR": str(tmp_path / "state"), "HRZEST_FW_HOST_ENFORCE": "false",
           "HRZEST_FW_KERNLOG": str(tmp_path / "ufw.log"), "HRZEST_FW_FAIL2BAN_LOG": str(tmp_path / "fail2ban.log"),
           "HRZEST_FW_MODSEC_LOG": str(tmp_path / "audit.json")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for name in ("ufw.log", "fail2ban.log", "audit.json"):
        (tmp_path / name).write_text("")

    def clear():
        cur = db_engine.cursor()
        cur.execute("DELETE FROM att_master.firewall_events")
        cur.execute("DELETE FROM att_master.firewall_sources")
        cur.execute("DELETE FROM att_master.soc_ban_reviews WHERE ip='45.33.32.156'")
        cur.execute("DELETE FROM public.banned_ips WHERE ip='45.33.32.156'")
        cur.execute("DELETE FROM public.banned_cidrs")
        db_engine.commit()
        cur.close()
    clear()
    cfg = agent.Config()
    a = agent.Agent(cfg, client=_FlaskAppClient(cfg, flask_app.test_client()))
    yield a, tmp_path
    clear()


class TestEndToEnd:
    def test_every_host_layer_reaches_the_soc(self, wired, db_engine):
        a, d = wired
        a.cycle()                                   # first sight of the logs: start at their end
        with open(d / "ufw.log", "a") as fh:
            fh.write(TestHostFirewallLines.LINE + "\n" + TestHostFirewallLines.LINE + "\n")
        with open(d / "fail2ban.log", "a") as fh:
            fh.write("2026-11-21 06:31:51,601 fail2ban.actions        [125]: NOTICE  [hrzest-modsec] Ban 45.33.32.156\n")
        with open(d / "audit.json", "a") as fh:
            fh.write(TestModSecurityRecords().record() + "\n")
        a.cycle()
        assert all(v.get("ok") for k, v in a.layers.items() if k in ("host", "fail2ban", "proxy", "app_report", "app_blocklist")), a.layers
        cur = db_engine.cursor()
        cur.execute("SELECT layer, action, src_ip, rule, hits, path FROM att_master.firewall_events ORDER BY layer")
        got = {r[0]: r[1:] for r in cur.fetchall()}
        assert got["host"] == ("block", "45.33.32.156", "FW-DROP", 2, None)
        assert got["fail2ban"][:3] == ("ban", "45.33.32.156", "hrzest-modsec")
        assert got["proxy"] == ("block", "45.33.32.156", "949110", 1, "/acme/x?q=<script>alert(1)</script>")
        cur.execute("SELECT source, events_total, status_json FROM att_master.firewall_sources WHERE source='host-test'")
        source, total, status = cur.fetchone()
        assert total == 4 and json.loads(status)["version"] == agent.VERSION
        cur.execute("SELECT status, source FROM att_master.soc_ban_reviews WHERE ip='45.33.32.156'")
        assert cur.fetchone() == ("pending", "fail2ban")       # the host ban waits for an analyst
        cur.close()

    def test_a_console_ban_is_what_the_agent_enforces(self, wired):
        a, _ = wired
        assert waf.ban_ip("45.33.32.156", "SOC: test", banned_by="soc:ana", minutes=60)
        assert waf.ban_cidr("198.51.100.0/24", "SOC: range", banned_by="soc:ana")
        applied = {}

        class FakeHost:
            def apply(self, v4, v6):
                applied["v4"], applied["v6"] = v4, v6
                return True
        a.host = FakeHost()
        status = a.enforce()
        assert applied["v4"] == ["198.51.100.0/24", "45.33.32.156/32"]
        assert status["entries"] == 2

    def test_a_wrong_secret_is_refused_and_reported(self, wired, monkeypatch):
        a, _ = wired
        a.cfg.secret = "b" * 40
        a.cycle()
        assert a.layers["app_report"]["ok"] is False and "401" in a.layers["app_report"]["error"]

    def test_unsent_events_are_kept_for_the_next_cycle(self, wired):
        a, d = wired
        a.cycle()
        a.cfg.secret, real = "b" * 40, a.cfg.secret
        with open(d / "ufw.log", "a") as fh:
            fh.write(TestHostFirewallLines.LINE + "\n")
        a.cycle()
        assert len(a.pending) == 1
        a.cfg.secret = real
        a.cycle()
        assert a.pending == []

    def test_signing_matches_the_app(self):
        msg, ts = b'{"x":1}', str(int(time.time()))
        assert agent.sign(msg, ts, SECRET) == firewall_events.sign(msg, ts, SECRET)
        assert ("GET " + agent.BLOCKLIST_PATH).encode() == firewall_events.blocklist_message()
        assert agent.EVENTS_PATH == "/webhooks/firewall/events"
