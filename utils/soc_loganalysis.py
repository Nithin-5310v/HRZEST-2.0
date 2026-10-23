# -*- coding: utf-8 -*-
"""Log analysis for the SOC console.

Takes raw log text (nginx / Apache access logs, JSON-lines such as this app's
own structured log, sshd / auth.log) and returns a report: what was parsed,
who the noisy sources are, which attack classes appear, and which source
addresses look worth blocking.

Safety properties -- this module parses attacker-controlled text:
  * Pure: no I/O, no eval, nothing is stored or executed.
  * Bounded: input bytes, line count, line length, evidence samples, tracked
    sources and wall-clock time are all capped. Hitting a cap is reported, not
    silent (`report["limits"]`).
  * Linear-time regexes only (no nested quantifiers), each applied to a
    length-capped string, so a hostile line cannot stall the analyser.
  * Evidence lines are returned as plain text; the console renders them with
    textContent and the CSV export neutralises spreadsheet formulas.
"""
import collections
import datetime
import ipaddress
import json
import os
import re
import time
import urllib.parse

from utils import soc_taxonomy as tax

MAX_BYTES = int(float(os.environ.get("SOC_LOG_MAX_MB", "5")) * 1024 * 1024)
MAX_LINES = 100_000
MAX_LINE_CHARS = 8192
MAX_TRACKED_IPS = 50_000
MAX_DISTINCT_PER_IP = 400
TIME_BUDGET_SECONDS = 10.0
EVIDENCE_PER_FINDING = 5
EVIDENCE_CHARS = 300

# ── parsing ───────────────────────────────────────────────────────────
_COMBINED = re.compile(
    r'^(?P<ip>\S+) \S+ (?P<user>\S+) \[(?P<ts>[^\]]+)\] "(?P<req>[^"]*)" '
    r'(?P<status>\d{3}) (?P<bytes>\d+|-)(?: "(?P<ref>[^"]*)" "(?P<ua>[^"]*)")?')
_SSH = re.compile(
    r'^(?P<ts>[A-Z][a-z]{2}\s+\d{1,2} \d\d:\d\d:\d\d|\d{4}-\d\d-\d\dT\S+)\s+\S+\s+sshd(?:\[\d+\])?:\s+(?P<msg>.*)$')
_SSH_FAIL = re.compile(r'^(?:Failed (?:password|publickey|keyboard-interactive/pam) for|Invalid user|'
                       r'Connection closed by (?:authenticating|invalid) user|Disconnecting (?:authenticating|invalid) user)\s+'
                       r'(?:invalid user\s+)?(?P<user>\S+)\s+(?:from\s+)?(?P<ip>[0-9A-Fa-f:.]+)')
_SSH_OK = re.compile(r'^Accepted (?:password|publickey|keyboard-interactive/pam) for (?P<user>\S+) from (?P<ip>[0-9A-Fa-f:.]+)')
_HTTP_VERSION = re.compile(r" HTTP/\d(?:\.\d)?$")
_IP_ANY = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
_ISO_TS = re.compile(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d')
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

_JSON_IP = ("ip", "remote_addr", "client_ip", "src_ip", "clientip", "remote_ip", "source_ip")
_JSON_PATH = ("path", "uri", "url", "request_uri", "request_path")
_JSON_TS = ("ts", "time", "timestamp", "@timestamp", "asctime", "created_at")
_JSON_UA = ("user_agent", "ua", "http_user_agent", "agent")


class Rec:
    __slots__ = ("n", "ts", "ip", "method", "path", "status", "size", "ua", "user",
                 "kind", "event", "level", "text", "outcome")

    def __init__(self, n, text, kind):
        self.n = n
        self.text = text
        self.kind = kind
        self.ts = self.ip = self.method = self.path = self.status = self.size = None
        self.ua = self.user = self.event = self.level = self.outcome = None


def _parse_ts(text, year=None):
    if not text:
        return None
    if isinstance(text, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(text), datetime.timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    t = str(text).strip()
    try:
        return datetime.datetime.strptime(t, "%d/%b/%Y:%H:%M:%S %z").astimezone(datetime.timezone.utc).replace(tzinfo=None)
    except ValueError:
        pass
    m = re.match(r"^([A-Z][a-z]{2})\s+(\d{1,2}) (\d\d):(\d\d):(\d\d)$", t)
    if m and m.group(1) in _MONTHS:
        try:
            return datetime.datetime(year or datetime.datetime.utcnow().year, _MONTHS[m.group(1)],
                                     int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
        except ValueError:
            return None
    try:
        dt = datetime.datetime.fromisoformat(t.replace("Z", "+00:00").replace(",", "."))
        if dt.tzinfo:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _valid_ip(text):
    if not text:
        return None
    text = text.strip().strip("[]")
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _parse_line(n, line):
    line = line[:MAX_LINE_CHARS].rstrip("\r\n")
    if not line.strip():
        return None
    s = line.lstrip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            r = Rec(n, line, "json")
            for k in _JSON_IP:
                if obj.get(k):
                    r.ip = _valid_ip(str(obj[k]).split(",")[0])
                    break
            for k in _JSON_PATH:
                if obj.get(k):
                    r.path = str(obj[k])[:2000]
                    break
            for k in _JSON_TS:
                if obj.get(k):
                    r.ts = _parse_ts(obj[k])
                    break
            for k in _JSON_UA:
                if obj.get(k):
                    r.ua = str(obj[k])[:300]
                    break
            r.method = (str(obj.get("method") or obj.get("request_method") or "") or None)
            st = obj.get("status") or obj.get("status_code")
            try:
                r.status = int(st) if st is not None else None
            except (TypeError, ValueError):
                r.status = None
            r.event = obj.get("event_type")
            r.level = (str(obj.get("level") or obj.get("levelname") or "").upper() or None)
            r.user = obj.get("identifier") or obj.get("user") or obj.get("username")
            r.user = str(r.user)[:100] if r.user else None
            return r
    m = _COMBINED.match(line)
    if m:
        r = Rec(n, line, "http")
        r.ip = _valid_ip(m.group("ip"))
        r.ts = _parse_ts(m.group("ts"))
        req = m.group("req")
        method, _, rest = req.partition(" ")
        if rest:
            # Everything between the method and the trailing "HTTP/x.y" is the target. Servers log raw
            # spaces for malformed (400) requests, so splitting on every space would drop the payload.
            if _HTTP_VERSION.search(rest):
                rest = rest[:rest.rfind(" ")]
            r.method, r.path = method[:12], rest[:2000]
        else:
            r.method, r.path = None, req[:2000]
        r.status = int(m.group("status"))
        r.size = None if m.group("bytes") == "-" else int(m.group("bytes"))
        r.ua = (m.group("ua") or "")[:300] or None
        r.user = None if m.group("user") == "-" else m.group("user")[:100]
        return r
    m = _SSH.match(line)
    if m:
        r = Rec(n, line, "ssh")
        r.ts = _parse_ts(m.group("ts"))
        msg = m.group("msg")
        ok = _SSH_OK.match(msg)
        bad = None if ok else _SSH_FAIL.match(msg)
        hit = ok or bad
        if hit:
            r.ip = _valid_ip(hit.group("ip"))
            r.user = hit.group("user")[:100]
            r.outcome = "success" if ok else "failure"
        return r
    r = Rec(n, line, "generic")
    ip = _IP_ANY.search(line)
    r.ip = _valid_ip(ip.group(0)) if ip else None
    ts = _ISO_TS.search(line)
    r.ts = _parse_ts(ts.group(0)) if ts else None
    return r


# ── signatures ────────────────────────────────────────────────────────
# (id, title, severity, techniques, regex, advice). Applied to the URL-decoded
# request target (+ user agent for tool detection). All linear-time.
_I = re.IGNORECASE
_SIGNATURES = [
    ("sqli", "SQL injection attempt", "high", ["T1190"], re.compile(
        r"union[\s+/*]{1,20}(?:all[\s+/*]{1,20})?select|['\"]\s*(?:or|and)\s+['\"\w]{1,20}\s*=\s*['\"\w]{1,20}"
        r"|\bor\s+1\s*=\s*1\b|(?:sleep|benchmark|pg_sleep)\s*\(|waitfor\s+delay|information_schema"
        r"|;\s*(?:drop|truncate)\s+table|into\s+(?:out|dump)file|load_file\s*\(|xp_cmdshell", _I),
     "Confirm parameters are bound, not concatenated; block the source if it repeats."),
    ("xss", "Cross-site scripting attempt", "high", ["T1190"], re.compile(
        r"<\s*script|javascript\s*:|\bon(?:error|load|click|mouseover|focus)\s*=|<\s*(?:svg|iframe|img)[^>]{0,80}(?:src|onload|onerror)", _I),
     "Check output encoding on the reflected parameter."),
    ("traversal", "Path traversal / local file read", "high", ["T1190", "T1083"], re.compile(
        r"\.\./|\.\.\\|%2e%2e|/etc/(?:passwd|shadow|hosts)|boot\.ini|win\.ini|/proc/self|\.\.%2f|\.\.%5c", _I),
     "Verify file access is confined to an allow-listed directory."),
    ("rce", "Command / code injection attempt", "critical", ["T1190", "T1059"], re.compile(
        r"(?:;|\||&&|`|\$\()\s*(?:cat|ls|id|whoami|wget|curl|nc|ncat|bash|sh|powershell|cmd)(?![\w=-])|\bcmd(?:\.exe)?\s*/c"
        r"|/bin/(?:ba)?sh|\$\{jndi:|base64\s+-d|\(\)\s*\{\s*:;\s*\}|\{\{\s*\d+\s*\*\s*\d+\s*\}\}", _I),
     "Treat as an exploitation attempt: check the endpoint for shell / template evaluation and review the host."),
    ("ssrf", "SSRF / cloud-metadata probe", "high", ["T1190"], re.compile(
        r"169\.254\.169\.254|metadata\.google\.internal|[?&](?:url|uri|dest|redirect|next|file|path)=https?://"
        r"(?:127\.|localhost|0\.0\.0\.0|\[::1\]|10\.|192\.168\.)", _I),
     "Ensure outbound fetches validate the destination; block link-local ranges."),
]
_PROBE_PATHS = re.compile(
    r"/\.env\b|/\.git(?:/|$)|/\.aws/|/\.ssh/|/\.svn|/\.DS_Store|/wp-(?:admin|login|content|includes)|/xmlrpc\.php|/phpmyadmin"
    r"|/pma/|/vendor/phpunit|/actuator(?:/|$)|/server-status|/cgi-bin/|/config\.(?:php|json|ya?ml)|/backup\.(?:zip|sql|tar|gz)"
    r"|/id_rsa|/boaform/|/HNAP1|/_ignition/|/solr/|/jenkins|/\.well-known/security\.txt\.bak|/admin\.php|/shell\.php|/eval-stdin\.php", _I)
_SCANNER_UA = re.compile(
    r"sqlmap|nikto|nmap|masscan|zgrab|gobuster|dirbuster|dirb\b|wpscan|nuclei|acunetix|nessus|openvas|burp|havij|w3af"
    r"|arachni|metasploit|ffuf|feroxbuster|whatweb|netsparker|appscan|paros|skipfish|jaeles|dalfox", _I)
_TOOL_UA = re.compile(r"^(?:curl|wget|python-requests|go-http-client|libwww-perl|okhttp|java/|httpclient)", _I)
_LOGIN_PATH = re.compile(
    r"/(?:login|admin_login|admin-login|employee_login|super_admin/login|soc/login|api/login|api/employee/login"
    r"|wp-login\.php|xmlrpc\.php|users/sign_in|oauth/token)(?:[/?]|$)", _I)
_SUSPICIOUS_CHARS = re.compile(r"[<>'\"`;|$(){}\\]|%[0-9a-fA-F]{2}|\.\.|169\.254")
_ODD_METHODS = {"TRACE", "CONNECT", "PROPFIND", "PROPPATCH", "MKCOL", "COPY", "MOVE", "DEBUG", "TRACK"}


def _decode(target):
    """URL-decode up to twice (double-encoding is a common evasion), bounded."""
    t = target[:2000]
    for _ in range(2):
        d = urllib.parse.unquote_plus(t)
        if d == t:
            break
        t = d
    return t


def _technique_list(ids):
    return [tax._technique(t) for t in ids]


def _ip_is_non_public(ip):
    """True for anything that is not a globally routable address: RFC 1918 and
    loopback, but also link-local, CGNAT, documentation and reserved ranges.
    Such a source is shown in the report but is never offered for blocking --
    banning it would mean banning your own network or proxy."""
    try:
        return not ipaddress.ip_address(ip).is_global
    except ValueError:
        return True


class _Finding:
    def __init__(self, fid, title, severity, mitre, advice=""):
        self.fid, self.title, self.severity = fid, title, severity
        self.mitre = mitre
        self.advice = advice
        self.count = 0
        self.ips = collections.Counter()
        self.evidence = []

    def add(self, rec, note=None):
        self.count += 1
        if rec.ip:
            self.ips[rec.ip] += 1
        if len(self.evidence) < EVIDENCE_PER_FINDING:
            self.evidence.append({
                "line": rec.n, "ip": rec.ip, "ts": rec.ts.isoformat(sep=" ") if rec.ts else None,
                "text": (note + " | " if note else "") + rec.text[:EVIDENCE_CHARS]})

    def out(self):
        return {"id": self.fid, "title": self.title, "severity": self.severity,
                "mitre": _technique_list(self.mitre), "count": self.count,
                "ips": [{"ip": ip, "count": c} for ip, c in self.ips.most_common(10)],
                "evidence": self.evidence, "advice": self.advice}


_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_SEV_POINTS = {"critical": 40, "high": 25, "medium": 12, "low": 4, "info": 0}


def analyze(text, *, brute_force_min=10, enumeration_min=20, flood_per_minute=120):
    """Analyse log text. -> JSON-serialisable report dict (see keys below).

    Format is auto-detected per line, so a file may mix access-log, JSON and
    sshd lines. Thresholds are parameters so tests (and later a settings
    screen) can tune them without touching the detection logic."""
    started = time.monotonic()
    limits = {"truncated_bytes": False, "truncated_lines": False, "time_budget_hit": False,
              "long_lines_clipped": 0, "ip_table_full": False}
    if len(text) > MAX_BYTES:
        text = text[:MAX_BYTES]
        limits["truncated_bytes"] = True

    findings = {}

    def finding(fid, title, severity, mitre, advice=""):
        if fid not in findings:
            findings[fid] = _Finding(fid, title, severity, mitre, advice)
        return findings[fid]

    kinds = collections.Counter()
    status_c = collections.Counter()
    method_c = collections.Counter()
    path_c = collections.Counter()
    ua_c = collections.Counter()
    tool_ua_c = collections.Counter()
    ip_stats = {}
    minute_ip = collections.Counter()       # (ip, minute) -> requests
    minute_tot = collections.Counter()      # minute -> requests
    minute_5xx = collections.Counter()
    ssh_fail = collections.defaultdict(lambda: [0, set()])    # ip -> [count, users]
    ssh_ok = collections.defaultdict(list)
    hours = collections.Counter()
    first_ts = last_ts = None
    parsed = skipped = lines_read = 0
    first_login_ok = {}         # ip -> (record, login POSTs seen before it)

    def stats(ip):
        s = ip_stats.get(ip)
        if s is None:
            if len(ip_stats) >= MAX_TRACKED_IPS:
                limits["ip_table_full"] = True
                return None
            s = ip_stats[ip] = {"requests": 0, "errors": 0, "bytes": 0, "paths": set(), "missing": set(),
                                "login_posts": 0, "login_fail": 0, "login_ok": 0, "users": set(),
                                "flags": collections.Counter(), "first": None, "last": None}
        return s

    for n, line in enumerate(text.splitlines(), 1):
        if n > MAX_LINES:
            limits["truncated_lines"] = True
            break
        if n % 1000 == 0 and (time.monotonic() - started) > TIME_BUDGET_SECONDS:
            limits["time_budget_hit"] = True
            break
        lines_read = n
        if len(line) > MAX_LINE_CHARS:
            limits["long_lines_clipped"] += 1
        rec = _parse_line(n, line)
        if rec is None:
            continue
        if rec.kind == "generic" and not rec.ip and not rec.ts:
            skipped += 1      # no structure recognised; still scanned for attack shapes below
        else:
            parsed += 1
        kinds[rec.kind] += 1
        if rec.ts:
            first_ts = rec.ts if first_ts is None or rec.ts < first_ts else first_ts
            last_ts = rec.ts if last_ts is None or rec.ts > last_ts else last_ts
            hours[rec.ts.hour] += 1
        s = stats(rec.ip) if rec.ip else None
        minute = rec.ts.replace(second=0, microsecond=0) if rec.ts else None
        if s is not None:
            s["requests"] += 1
            if rec.ts:
                s["first"] = rec.ts if s["first"] is None or rec.ts < s["first"] else s["first"]
                s["last"] = rec.ts if s["last"] is None or rec.ts > s["last"] else s["last"]
        if minute is not None:
            minute_tot[minute] += 1
            if rec.ip:
                minute_ip[(rec.ip, minute)] += 1

        # ---- sshd ----
        if rec.kind == "ssh":
            if rec.outcome == "failure" and rec.ip:
                ssh_fail[rec.ip][0] += 1
                if len(ssh_fail[rec.ip][1]) < MAX_DISTINCT_PER_IP:
                    ssh_fail[rec.ip][1].add(rec.user)
                if s is not None:
                    s["errors"] += 1
                    if rec.user:
                        s["users"].add(rec.user)
                finding("ssh_bruteforce", "SSH password guessing", "high", ["T1110.001"],
                        "Block the sources, enforce key-only SSH, and review any 'Accepted' line from the same source.").add(rec)
            elif rec.outcome == "success" and rec.ip:
                ssh_ok[rec.ip].append(rec)
            continue

        # ---- HTTP-ish ----
        target = rec.path or ""
        if rec.status:
            status_c[rec.status // 100 * 100] += 1
            if rec.status >= 400 and s is not None:
                s["errors"] += 1
            if rec.status >= 500 and minute is not None:
                minute_5xx[minute] += 1
        if rec.method:
            method_c[rec.method.upper()] += 1
        if target:
            path_c[target.split("?", 1)[0][:200]] += 1
        if rec.ua:
            ua_c[rec.ua[:120]] += 1
        if rec.size and s is not None:
            s["bytes"] += rec.size
        if s is not None and target:
            if len(s["paths"]) < MAX_DISTINCT_PER_IP:
                s["paths"].add(target.split("?", 1)[0][:200])
            if rec.status == 404 and len(s["missing"]) < MAX_DISTINCT_PER_IP:
                s["missing"].add(target.split("?", 1)[0][:200])

        # JSON security events already carry their own classification
        if rec.kind == "json" and rec.event:
            cls = tax.classify(rec.event)
            if cls["mitre"] and tax.level_rank(rec.level) >= 2:
                f = finding("app_event:" + cls["family"], "App security events: " + cls["family_label"],
                            tax.severity_of(rec.event, rec.level) if tax.severity_of(rec.event, rec.level) != "info" else "medium",
                            [m["id"] for m in cls["mitre"]], "Pivot on these in the Events view for full context.")
                f.add(rec, note=rec.event)
                if s is not None:
                    s["flags"]["app_event"] += 1

        scan_text = _decode(target) if target else ""
        ua = rec.ua or ""

        # signature checks
        if scan_text and (_SUSPICIOUS_CHARS.search(scan_text) or "%" in target):
            for fid, title, sev, mitre, rx, advice in _SIGNATURES:
                if rx.search(scan_text):
                    finding(fid, title, sev, mitre, advice).add(rec)
                    if s is not None:
                        s["flags"][fid] += 1
        elif rec.kind == "generic" and _SUSPICIOUS_CHARS.search(rec.text):
            decoded = _decode(rec.text)
            for fid, title, sev, mitre, rx, advice in _SIGNATURES:
                if rx.search(decoded):
                    finding(fid, title, sev, mitre, advice).add(rec)
                    if s is not None:
                        s["flags"][fid] += 1
        if scan_text and _PROBE_PATHS.search(scan_text):
            finding("probe", "Probe for sensitive / framework files", "medium", ["T1595.003"],
                    "Confirm none of these paths exist; consider a WAF rule or honeypot for them.").add(rec)
            if s is not None:
                s["flags"]["probe"] += 1
        if ua and _SCANNER_UA.search(ua):
            finding("scanner_ua", "Security-scanner user agent", "medium", ["T1595.002"],
                    "Expected only from your own authorised scans; otherwise block the source.").add(rec, note=ua[:60])
            if s is not None:
                s["flags"]["scanner_ua"] += 1
        elif ua and _TOOL_UA.search(ua):
            tool_ua_c[ua.split("/")[0][:40]] += 1
        if rec.method and rec.method.upper() in _ODD_METHODS:
            finding("odd_method", "Unusual HTTP method", "low", ["T1190"],
                    "TRACE/CONNECT/WebDAV verbs are rarely legitimate on this app.").add(rec)
            if s is not None:
                s["flags"]["odd_method"] += 1
        if len(target) > 1800 or "%00" in target:
            finding("malformed", "Oversized or NUL-byte request target", "low", ["T1190"],
                    "Often fuzzing or a smuggling attempt.").add(rec)

        # login behaviour
        if target and _LOGIN_PATH.search(target.split("?", 1)[0]) and (rec.method or "").upper() == "POST" and s is not None:
            s["login_posts"] += 1
            if rec.status in (401, 403, 429):
                s["login_fail"] += 1
            elif rec.status in (301, 302, 303, 307, 308):
                # This app re-renders the form (200) on a bad password and redirects (302)
                # on success, so only a redirect counts as a likely success. Other stacks
                # may answer 200 either way; the finding says "possible", not "confirmed".
                s["login_ok"] += 1
                first_login_ok.setdefault(rec.ip, (rec, s["login_posts"] - 1))
            if rec.user:
                s["users"].add(rec.user)

    # ── aggregate detections ──────────────────────────────────────────
    for ip, s in ip_stats.items():
        if s["login_posts"] >= brute_force_min:
            f = finding("bruteforce", "Login brute force / password guessing", "high", ["T1110"],
                        "Block or rate-limit the source; check whether any attempt succeeded (see 'possible success').")
            f.count += s["login_posts"]
            f.ips[ip] += s["login_posts"]
            s["flags"]["bruteforce"] += 1
            if not f.evidence or all(e["ip"] != ip for e in f.evidence):
                if len(f.evidence) < EVIDENCE_PER_FINDING:
                    f.evidence.append({"line": None, "ip": ip, "ts": None,
                                       "text": f"{s['login_posts']} login POSTs, {s['login_fail']} refused (401/403/429)"})
        if len(s["users"]) >= 5 and s["login_posts"] >= 8:
            f = finding("stuffing", "Credential stuffing / spraying pattern", "critical", ["T1110.004", "T1110.003"],
                        "One source trying many accounts: block it and force resets for any account that then succeeded.")
            f.count += len(s["users"])
            f.ips[ip] += len(s["users"])
            s["flags"]["stuffing"] += 1
            if len(f.evidence) < EVIDENCE_PER_FINDING:
                f.evidence.append({"line": None, "ip": ip, "ts": None,
                                   "text": f"{len(s['users'])} distinct accounts from one source"})
        if len(s["missing"]) >= enumeration_min:
            f = finding("enumeration", "Path enumeration (many distinct 404s)", "medium", ["T1595.003"],
                        "Wordlist scanning. Block or tarpit; a honeypot path would make this visible earlier.")
            f.count += len(s["missing"])
            f.ips[ip] += len(s["missing"])
            s["flags"]["enumeration"] += 1
            if len(f.evidence) < EVIDENCE_PER_FINDING:
                f.evidence.append({"line": None, "ip": ip, "ts": None,
                                   "text": f"{len(s['missing'])} distinct missing paths, e.g. " + ", ".join(sorted(s["missing"])[:4])})
    for ip, (rec, posts_before) in first_login_ok.items():
        if posts_before >= brute_force_min:
            f = finding("possible_success", "Possible successful login after many attempts", "critical", ["T1078", "T1110"],
                        "Treat the account as compromised until proven otherwise: reset, revoke sessions, review activity.")
            f.add(rec, note=f"redirect after {posts_before} earlier login POSTs")
            ip_stats[ip]["flags"]["possible_success"] += 1
    flood_ips = {}
    for (ip, minute), c in minute_ip.items():
        if c >= flood_per_minute:
            flood_ips[ip] = max(flood_ips.get(ip, 0), c)
    for ip, c in flood_ips.items():
        f = finding("flood", "Request flood from one source", "medium", ["T1499"],
                    "Rate-limit at the edge; confirm the source is not a legitimate integration.")
        f.count += c
        f.ips[ip] += c
        if ip in ip_stats:
            ip_stats[ip]["flags"]["flood"] += 1
        if len(f.evidence) < EVIDENCE_PER_FINDING:
            f.evidence.append({"line": None, "ip": ip, "ts": None, "text": f"peak {c} requests in one minute"})
    for minute, c5 in minute_5xx.items():
        tot = minute_tot.get(minute, 0)
        if c5 >= 10 and tot and c5 / tot >= 0.2:
            f = finding("error_spike", "Server-error spike", "medium", ["T1499"],
                        "Correlate with deploys and with the attack findings above; may be exploitation or an outage.")
            f.count += c5
            if len(f.evidence) < EVIDENCE_PER_FINDING:
                f.evidence.append({"line": None, "ip": None, "ts": minute.isoformat(sep=" "),
                                   "text": f"{c5} of {tot} requests returned 5xx in this minute"})
    for ip, (cnt, users) in ssh_fail.items():
        if ip in ip_stats:
            ip_stats[ip]["flags"]["ssh_bruteforce"] += 1
        if len(users) >= 5:
            f = finding("ssh_spray", "SSH username spraying", "high", ["T1110.003"],
                        "One source trying many usernames; block it.")
            f.count += len(users)
            f.ips[ip] += len(users)
        if cnt >= 5 and ssh_ok.get(ip):
            f = finding("ssh_possible_success", "SSH login accepted after repeated failures", "critical",
                        ["T1078", "T1110.001"], "Assume the account is compromised: rotate credentials and inspect the session.")
            f.add(ssh_ok[ip][0], note=f"{cnt} failures first")
            ip_stats[ip]["flags"]["possible_success"] += 1

    # ── per-source scoring ────────────────────────────────────────────
    sev_of = {fid: f.severity for fid, f in findings.items()}
    sources = []
    for ip, s in ip_stats.items():
        score = 0
        for flag, c in s["flags"].items():
            sev = sev_of.get(flag) or sev_of.get("app_event:" + flag) or "medium"
            score += _SEV_POINTS[sev] if c else 0
        if s["requests"] >= 20 and s["errors"] / s["requests"] >= 0.6:
            score += 8
        score = min(100, score)
        sources.append({
            "ip": ip, "requests": s["requests"], "errors": s["errors"], "bytes": s["bytes"],
            "distinct_paths": len(s["paths"]), "login_posts": s["login_posts"], "accounts": len(s["users"]),
            "flags": sorted(s["flags"]), "score": score, "non_public": _ip_is_non_public(ip),
            "verdict": ("block" if score >= 60 else "watch" if score >= 25 else "ok"),
            "first": s["first"].isoformat(sep=" ") if s["first"] else None,
            "last": s["last"].isoformat(sep=" ") if s["last"] else None})
    sources.sort(key=lambda x: (-x["score"], -x["requests"]))

    out_findings = sorted((f.out() for f in findings.values()),
                          key=lambda f: (-_SEV_RANK[f["severity"]], -f["count"]))

    span = (last_ts - first_ts) if first_ts and last_ts else None
    if span is None:
        bucket, fmt = None, None
    elif span <= datetime.timedelta(hours=3):
        bucket, fmt = "minute", "%Y-%m-%d %H:%M"
    elif span <= datetime.timedelta(days=3):
        bucket, fmt = "hour", "%Y-%m-%d %H:00"
    else:
        bucket, fmt = "day", "%Y-%m-%d"
    timeline = collections.OrderedDict()
    attack_ips = {src["ip"] for src in sources if src["flags"]}
    if bucket:
        # re-walk the per-minute totals (already aggregated) instead of the lines
        for minute in sorted(minute_tot):
            key = minute.strftime(fmt)
            slot = timeline.setdefault(key, {"t": key, "requests": 0, "errors5xx": 0, "attack": 0})
            slot["requests"] += minute_tot[minute]
            slot["errors5xx"] += minute_5xx.get(minute, 0)
        for (ip, minute), c in minute_ip.items():
            if ip in attack_ips:
                timeline[minute.strftime(fmt)]["attack"] += c

    iocs = [{"type": "ip", "value": s["ip"], "reason": ",".join(s["flags"]) or "noisy"}
            for s in sources if s["verdict"] != "ok" and not s["non_public"]]
    for u, c in ua_c.most_common(200):
        if _SCANNER_UA.search(u):
            iocs.append({"type": "user_agent", "value": u, "reason": "scanner"})

    return {
        "summary": {
            "lines_read": lines_read, "records": parsed, "unparsed": skipped,
            "formats": dict(kinds), "first": first_ts.isoformat(sep=" ") if first_ts else None,
            "last": last_ts.isoformat(sep=" ") if last_ts else None,
            "unique_ips": len(ip_stats), "findings": len(out_findings),
            "critical": sum(1 for f in out_findings if f["severity"] == "critical"),
            "high": sum(1 for f in out_findings if f["severity"] == "high"),
            "block_candidates": sum(1 for s in sources if s["verdict"] == "block" and not s["non_public"]),
            "elapsed_ms": int((time.monotonic() - started) * 1000)},
        "limits": limits,
        "findings": out_findings,
        "sources": sources[:50],
        "status": {str(k): v for k, v in sorted(status_c.items())},
        "methods": dict(method_c.most_common(10)),
        "top_paths": [{"path": p, "count": c} for p, c in path_c.most_common(20)],
        "top_user_agents": [{"ua": u, "count": c, "scanner": bool(_SCANNER_UA.search(u))} for u, c in ua_c.most_common(15)],
        "tool_clients": dict(tool_ua_c.most_common(8)),
        "hourly": [hours.get(h, 0) for h in range(24)],
        "timeline": {"bucket": bucket, "points": list(timeline.values())},
        "iocs": iocs[:500],
    }
