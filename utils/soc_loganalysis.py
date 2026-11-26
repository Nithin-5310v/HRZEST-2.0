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
from utils.scanner_signatures import SCANNER_UA_RE

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
    """One log line -> Rec (kind json, http, ssh or generic), or None for a blank line."""
    line = line[:MAX_LINE_CHARS].rstrip("\r\n")
    if not line.strip():
        return None
    obj = _json_object(line.lstrip())
    if obj is not None:
        return _parse_json(n, line, obj)
    m = _COMBINED.match(line)
    if m:
        return _parse_access(n, line, m)
    m = _SSH.match(line)
    if m:
        return _parse_ssh(n, line, m)
    return _parse_generic(n, line)


def _json_object(s):
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _first_value(obj, keys):
    """The value of the first of `keys` that is set (truthy) in `obj`, else None."""
    for k in keys:
        if obj.get(k):
            return obj[k]
    return None


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_json(n, line, obj):
    r = Rec(n, line, "json")
    ip = _first_value(obj, _JSON_IP)
    r.ip = _valid_ip(str(ip).split(",")[0]) if ip is not None else None
    path = _first_value(obj, _JSON_PATH)
    r.path = str(path)[:2000] if path is not None else None
    r.ts = _parse_ts(_first_value(obj, _JSON_TS))
    ua = _first_value(obj, _JSON_UA)
    r.ua = str(ua)[:300] if ua is not None else None
    r.method = (str(obj.get("method") or obj.get("request_method") or "") or None)
    r.status = _int_or_none(obj.get("status") or obj.get("status_code"))
    r.event = obj.get("event_type")
    r.level = (str(obj.get("level") or obj.get("levelname") or "").upper() or None)
    user = obj.get("identifier") or obj.get("user") or obj.get("username")
    r.user = str(user)[:100] if user else None
    return r


def _parse_access(n, line, m):
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


def _parse_ssh(n, line, m):
    r = Rec(n, line, "ssh")
    r.ts = _parse_ts(m.group("ts"))
    msg = m.group("msg")
    ok = _SSH_OK.match(msg)
    hit = ok or _SSH_FAIL.match(msg)
    if hit:
        r.ip = _valid_ip(hit.group("ip"))
        r.user = hit.group("user")[:100]
        r.outcome = "success" if ok else "failure"
    return r


def _parse_generic(n, line):
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
_SCANNER_UA = SCANNER_UA_RE     # shared with the in-app WAF, which blocks on it
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

    def tally(self, ip, amount, text=None, ts=None):
        """An aggregate hit (no single line to quote): `amount` more for `ip`, and
        `text` as a summary evidence line while there is room."""
        self.count += amount
        if ip:
            self.ips[ip] += amount
        if text is not None and len(self.evidence) < EVIDENCE_PER_FINDING:
            self.evidence.append({"line": None, "ip": ip, "ts": ts, "text": text})

    def out(self):
        return {"id": self.fid, "title": self.title, "severity": self.severity,
                "mitre": _technique_list(self.mitre), "count": self.count,
                "ips": [{"ip": ip, "count": c} for ip, c in self.ips.most_common(10)],
                "evidence": self.evidence, "advice": self.advice}


_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_SEV_POINTS = {"critical": 40, "high": 25, "medium": 12, "low": 4, "info": 0}
TIMELINE_MAX_BUCKETS = 2000

_BUCKET_STEP = {"minute": datetime.timedelta(minutes=1), "hour": datetime.timedelta(hours=1),
                "day": datetime.timedelta(days=1)}
_BUCKET_ZERO = {"minute": {"second": 0, "microsecond": 0}, "hour": {"minute": 0, "second": 0, "microsecond": 0},
                "day": {"hour": 0, "minute": 0, "second": 0, "microsecond": 0}}
_BUCKET_FORMAT = {"minute": "%Y-%m-%d %H:%M", "hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d"}


def _earlier(current, ts):
    return ts if current is None or ts < current else current


def _later(current, ts):
    return ts if current is None or ts > current else current


def _path_key(target):
    return target.split("?", 1)[0][:200]


def _new_source():
    return {"requests": 0, "errors": 0, "bytes": 0, "paths": set(), "missing": set(),
            "login_posts": 0, "login_fail": 0, "login_ok": 0, "users": set(),
            "flags": collections.Counter(), "first": None, "last": None}


def _flag(s, name):
    if s is not None:
        s["flags"][name] += 1


def _iso(ts):
    return ts.isoformat(sep=" ") if ts else None


def _score_parts(s, sev_of):
    """The parts a source's score is the sum of (capped at 100); they go out with
    it so the page shows exactly how a source got its number."""
    parts = []
    for flag, c in s["flags"].items():
        sev = sev_of.get(flag) or sev_of.get("app_event:" + flag) or "medium"
        if c and _SEV_POINTS[sev]:
            parts.append({"what": flag, "severity": sev, "points": _SEV_POINTS[sev]})
    if s["requests"] >= 20 and s["errors"] / s["requests"] >= 0.6:
        parts.append({"what": "60%+ of its 20+ requests failed", "severity": None, "points": 8})
    return parts


def _source_row(ip, s, sev_of):
    parts = _score_parts(s, sev_of)
    score = min(100, sum(p["points"] for p in parts))
    return {
        "ip": ip, "requests": s["requests"], "errors": s["errors"], "bytes": s["bytes"],
        "distinct_paths": len(s["paths"]), "login_posts": s["login_posts"], "accounts": len(s["users"]),
        "flags": sorted(s["flags"]), "score": score, "score_parts": sorted(parts, key=lambda p: -p["points"]),
        "non_public": _ip_is_non_public(ip),
        "verdict": ("block" if score >= 60 else "watch" if score >= 25 else "ok"),
        "first": _iso(s["first"]), "last": _iso(s["last"])}


def _timeline_bucket(first_ts, last_ts):
    if not (first_ts and last_ts):
        return None
    span = last_ts - first_ts
    if span <= datetime.timedelta(hours=3):
        return "minute"
    if span <= datetime.timedelta(days=3):
        return "hour"
    return "day"


def _timeline_point(key):
    return {"t": key, "requests": 0, "errors5xx": 0, "attack": 0}


class _Analysis:
    """The running totals of one analyze() call: fed one parsed line at a time,
    then turned into aggregate findings, scored sources and the report."""

    def __init__(self, text):
        self.started = time.monotonic()
        self.limits = {"truncated_bytes": False, "truncated_lines": False, "time_budget_hit": False,
                       "long_lines_clipped": 0, "ip_table_full": False}
        if len(text) > MAX_BYTES:
            text = text[:MAX_BYTES]
            self.limits["truncated_bytes"] = True
        self.text = text
        self.findings = {}
        self.kinds = collections.Counter()
        self.status_c = collections.Counter()
        self.method_c = collections.Counter()
        self.path_c = collections.Counter()
        self.ua_c = collections.Counter()
        self.tool_ua_c = collections.Counter()
        self.ip_stats = {}
        self.minute_ip = collections.Counter()       # (ip, minute) -> requests
        self.minute_tot = collections.Counter()      # minute -> requests
        self.minute_5xx = collections.Counter()
        self.ssh_fail = collections.defaultdict(lambda: [0, set()])    # ip -> [count, users]
        self.ssh_ok = collections.defaultdict(list)
        self.hours = collections.Counter()
        self.first_ts = self.last_ts = None
        self.parsed = self.skipped = self.lines_read = 0
        self.first_login_ok = {}         # ip -> (record, login POSTs seen before it)

    def finding(self, fid, title, severity, mitre, advice=""):
        if fid not in self.findings:
            self.findings[fid] = _Finding(fid, title, severity, mitre, advice)
        return self.findings[fid]

    def stats(self, ip):
        s = self.ip_stats.get(ip)
        if s is None:
            if len(self.ip_stats) >= MAX_TRACKED_IPS:
                self.limits["ip_table_full"] = True
                return None
            s = self.ip_stats[ip] = _new_source()
        return s

    # ── per line ──────────────────────────────────────────────────────
    def read(self):
        for n, line in enumerate(self.text.splitlines(), 1):
            if n > MAX_LINES:
                self.limits["truncated_lines"] = True
                break
            if n % 1000 == 0 and (time.monotonic() - self.started) > TIME_BUDGET_SECONDS:
                self.limits["time_budget_hit"] = True
                break
            self.lines_read = n
            if len(line) > MAX_LINE_CHARS:
                self.limits["long_lines_clipped"] += 1
            rec = _parse_line(n, line)
            if rec is not None:
                self.add(rec)

    def add(self, rec):
        s, minute = self._count(rec)
        if rec.kind == "ssh":
            self._ssh_line(rec, s)
            return
        target = rec.path or ""
        scan_text = _decode(target) if target else ""
        self._count_http(rec, s, minute, target)
        self._app_event(rec, s)
        self._signatures(rec, s, target, scan_text)
        self._probe_and_client(rec, s, scan_text)
        self._method_and_size(rec, s, target)
        self._login(rec, s, target)

    def _count(self, rec):
        """Totals every record feeds, whatever its format. -> (source stats or None, minute or None)"""
        if rec.kind == "generic" and not rec.ip and not rec.ts:
            self.skipped += 1      # no structure recognised; still scanned for attack shapes
        else:
            self.parsed += 1
        self.kinds[rec.kind] += 1
        if rec.ts:
            self.first_ts = _earlier(self.first_ts, rec.ts)
            self.last_ts = _later(self.last_ts, rec.ts)
            self.hours[rec.ts.hour] += 1
        s = self.stats(rec.ip) if rec.ip else None
        minute = rec.ts.replace(second=0, microsecond=0) if rec.ts else None
        if s is not None:
            s["requests"] += 1
            if rec.ts:
                s["first"] = _earlier(s["first"], rec.ts)
                s["last"] = _later(s["last"], rec.ts)
        if minute is not None:
            self.minute_tot[minute] += 1
            if rec.ip:
                self.minute_ip[(rec.ip, minute)] += 1
        return s, minute

    def _ssh_line(self, rec, s):
        if rec.outcome == "failure" and rec.ip:
            fails = self.ssh_fail[rec.ip]
            fails[0] += 1
            if len(fails[1]) < MAX_DISTINCT_PER_IP:
                fails[1].add(rec.user)
            if s is not None:
                s["errors"] += 1
                if rec.user:
                    s["users"].add(rec.user)
            self.finding("ssh_bruteforce", "SSH password guessing", "high", ["T1110.001"],
                         "Block the sources, enforce key-only SSH, and review any 'Accepted' line from the same source.").add(rec)
        elif rec.outcome == "success" and rec.ip:
            self.ssh_ok[rec.ip].append(rec)

    def _count_http(self, rec, s, minute, target):
        if rec.status:
            self.status_c[rec.status // 100 * 100] += 1
            if rec.status >= 400 and s is not None:
                s["errors"] += 1
            if rec.status >= 500 and minute is not None:
                self.minute_5xx[minute] += 1
        if rec.method:
            self.method_c[rec.method.upper()] += 1
        if target:
            self.path_c[_path_key(target)] += 1
        if rec.ua:
            self.ua_c[rec.ua[:120]] += 1
        if s is None:
            return
        if rec.size:
            s["bytes"] += rec.size
        if target:
            if len(s["paths"]) < MAX_DISTINCT_PER_IP:
                s["paths"].add(_path_key(target))
            if rec.status == 404 and len(s["missing"]) < MAX_DISTINCT_PER_IP:
                s["missing"].add(_path_key(target))

    def _app_event(self, rec, s):
        """JSON security events already carry their own classification."""
        if rec.kind != "json" or not rec.event:
            return
        cls = tax.classify(rec.event)
        if not (cls["mitre"] and tax.level_rank(rec.level) >= 2):
            return
        severity = tax.severity_of(rec.event, rec.level)
        f = self.finding("app_event:" + cls["family"], "App security events: " + cls["family_label"],
                         severity if severity != "info" else "medium",
                         [m["id"] for m in cls["mitre"]], "Pivot on these in the Events view for full context.")
        f.add(rec, note=rec.event)
        _flag(s, "app_event")

    def _signatures(self, rec, s, target, scan_text):
        if scan_text and (_SUSPICIOUS_CHARS.search(scan_text) or "%" in target):
            self._match_signatures(rec, s, scan_text)
        elif rec.kind == "generic" and _SUSPICIOUS_CHARS.search(rec.text):
            self._match_signatures(rec, s, _decode(rec.text))

    def _match_signatures(self, rec, s, text):
        for fid, title, sev, mitre, rx, advice in _SIGNATURES:
            if rx.search(text):
                self.finding(fid, title, sev, mitre, advice).add(rec)
                _flag(s, fid)

    def _probe_and_client(self, rec, s, scan_text):
        ua = rec.ua or ""
        if scan_text and _PROBE_PATHS.search(scan_text):
            self.finding("probe", "Probe for sensitive / framework files", "medium", ["T1595.003"],
                         "Confirm none of these paths exist; consider a WAF rule or honeypot for them.").add(rec)
            _flag(s, "probe")
        if ua and _SCANNER_UA.search(ua):
            self.finding("scanner_ua", "Security-scanner user agent", "medium", ["T1595.002"],
                         "Expected only from your own authorised scans; otherwise block the source.").add(rec, note=ua[:60])
            _flag(s, "scanner_ua")
        elif ua and _TOOL_UA.search(ua):
            self.tool_ua_c[ua.split("/")[0][:40]] += 1

    def _method_and_size(self, rec, s, target):
        if rec.method and rec.method.upper() in _ODD_METHODS:
            self.finding("odd_method", "Unusual HTTP method", "low", ["T1190"],
                         "TRACE/CONNECT/WebDAV verbs are rarely legitimate on this app.").add(rec)
            _flag(s, "odd_method")
        if len(target) > 1800 or "%00" in target:
            self.finding("malformed", "Oversized or NUL-byte request target", "low", ["T1190"],
                         "Often fuzzing or a smuggling attempt.").add(rec)

    def _login(self, rec, s, target):
        if s is None or not target or (rec.method or "").upper() != "POST" \
                or not _LOGIN_PATH.search(target.split("?", 1)[0]):
            return
        s["login_posts"] += 1
        if rec.status in (401, 403, 429):
            s["login_fail"] += 1
        elif rec.status in (301, 302, 303, 307, 308):
            # This app re-renders the form (200) on a bad password and redirects (302)
            # on success, so only a redirect counts as a likely success. Other stacks
            # may answer 200 either way; the finding says "possible", not "confirmed".
            s["login_ok"] += 1
            self.first_login_ok.setdefault(rec.ip, (rec, s["login_posts"] - 1))
        if rec.user:
            s["users"].add(rec.user)

    # ── aggregate detections ──────────────────────────────────────────
    def detect(self, brute_force_min, enumeration_min, flood_per_minute):
        # One pass per source in table order: the order findings are first
        # created in decides the report's order between equally ranked ones.
        for ip, s in self.ip_stats.items():
            self._bruteforce(ip, s, brute_force_min)
            self._stuffing(ip, s)
            self._enumeration(ip, s, enumeration_min)
        self._possible_success(brute_force_min)
        self._floods(flood_per_minute)
        self._error_spikes()
        self._ssh_detections()

    def _bruteforce(self, ip, s, brute_force_min):
        if s["login_posts"] < brute_force_min:
            return
        f = self.finding("bruteforce", "Login brute force / password guessing", "high", ["T1110"],
                         "Block or rate-limit the source; check whether any attempt succeeded (see 'possible success').")
        s["flags"]["bruteforce"] += 1
        f.tally(ip, s["login_posts"], f"{s['login_posts']} login POSTs, {s['login_fail']} refused (401/403/429)")

    def _stuffing(self, ip, s):
        if len(s["users"]) < 5 or s["login_posts"] < 8:
            return
        f = self.finding("stuffing", "Credential stuffing / spraying pattern", "critical", ["T1110.004", "T1110.003"],
                         "One source trying many accounts: block it and force resets for any account that then succeeded.")
        s["flags"]["stuffing"] += 1
        f.tally(ip, len(s["users"]), f"{len(s['users'])} distinct accounts from one source")

    def _enumeration(self, ip, s, enumeration_min):
        if len(s["missing"]) < enumeration_min:
            return
        f = self.finding("enumeration", "Path enumeration (many distinct 404s)", "medium", ["T1595.003"],
                         "Wordlist scanning. Block or tarpit; a honeypot path would make this visible earlier.")
        s["flags"]["enumeration"] += 1
        f.tally(ip, len(s["missing"]),
                f"{len(s['missing'])} distinct missing paths, e.g. " + ", ".join(sorted(s["missing"])[:4]))

    def _possible_success(self, brute_force_min):
        for ip, (rec, posts_before) in self.first_login_ok.items():
            if posts_before >= brute_force_min:
                f = self.finding("possible_success", "Possible successful login after many attempts", "critical",
                                 ["T1078", "T1110"],
                                 "Treat the account as compromised until proven otherwise: reset, revoke sessions, review activity.")
                f.add(rec, note=f"redirect after {posts_before} earlier login POSTs")
                self.ip_stats[ip]["flags"]["possible_success"] += 1

    def _floods(self, flood_per_minute):
        peaks = {}
        for (ip, _minute), c in self.minute_ip.items():
            if c >= flood_per_minute:
                peaks[ip] = max(peaks.get(ip, 0), c)
        for ip, c in peaks.items():
            f = self.finding("flood", "Request flood from one source", "medium", ["T1499"],
                             "Rate-limit at the edge; confirm the source is not a legitimate integration.")
            _flag(self.ip_stats.get(ip), "flood")
            f.tally(ip, c, f"peak {c} requests in one minute")

    def _error_spikes(self):
        for minute, c5 in self.minute_5xx.items():
            tot = self.minute_tot.get(minute, 0)
            if c5 >= 10 and tot and c5 / tot >= 0.2:
                f = self.finding("error_spike", "Server-error spike", "medium", ["T1499"],
                                 "Correlate with deploys and with the attack findings above; may be exploitation or an outage.")
                f.tally(None, c5, f"{c5} of {tot} requests returned 5xx in this minute", ts=_iso(minute))

    def _ssh_detections(self):
        for ip, (cnt, users) in self.ssh_fail.items():
            _flag(self.ip_stats.get(ip), "ssh_bruteforce")
            if len(users) >= 5:
                self.finding("ssh_spray", "SSH username spraying", "high", ["T1110.003"],
                             "One source trying many usernames; block it.").tally(ip, len(users))
            if cnt >= 5 and self.ssh_ok.get(ip):
                f = self.finding("ssh_possible_success", "SSH login accepted after repeated failures", "critical",
                                 ["T1078", "T1110.001"],
                                 "Assume the account is compromised: rotate credentials and inspect the session.")
                f.add(self.ssh_ok[ip][0], note=f"{cnt} failures first")
                self.ip_stats[ip]["flags"]["possible_success"] += 1

    # ── report ────────────────────────────────────────────────────────
    def sources(self):
        sev_of = {fid: f.severity for fid, f in self.findings.items()}
        rows = [_source_row(ip, s, sev_of) for ip, s in self.ip_stats.items()]
        rows.sort(key=lambda x: (-x["score"], -x["requests"]))
        return rows

    def timeline(self, sources):
        bucket = _timeline_bucket(self.first_ts, self.last_ts)
        points = collections.OrderedDict()
        if bucket:
            fmt = _BUCKET_FORMAT[bucket]
            self._quiet_buckets(points, bucket, fmt)
            # re-walk the per-minute totals (already aggregated) instead of the lines
            for minute in sorted(self.minute_tot):
                key = minute.strftime(fmt)
                slot = points.setdefault(key, _timeline_point(key))
                slot["requests"] += self.minute_tot[minute]
                slot["errors5xx"] += self.minute_5xx.get(minute, 0)
            attack_ips = {src["ip"] for src in sources if src["flags"]}
            for (ip, minute), c in self.minute_ip.items():
                if ip in attack_ips:
                    points[minute.strftime(fmt)]["attack"] += c
        return {"bucket": bucket, "points": list(points.values())}

    def _quiet_buckets(self, points, bucket, fmt):
        """Every bucket from the first record to the last, quiet ones at zero:
        without them a quiet stretch vanished and the chart drew a line across
        it as if there had been traffic. (Bounded: a log whose dates are far
        apart keeps only its busy buckets rather than thousands of empty ones.)"""
        step = _BUCKET_STEP[bucket]
        t = self.first_ts.replace(**_BUCKET_ZERO[bucket])
        if (self.last_ts - t) / step > TIMELINE_MAX_BUCKETS:
            return
        while t <= self.last_ts:
            key = t.strftime(fmt)
            points[key] = _timeline_point(key)
            t += step

    def iocs(self, sources):
        iocs = [{"type": "ip", "value": s["ip"], "reason": ",".join(s["flags"]) or "noisy"}
                for s in sources if s["verdict"] != "ok" and not s["non_public"]]
        for u, _count in self.ua_c.most_common(200):
            if _SCANNER_UA.search(u):
                iocs.append({"type": "user_agent", "value": u, "reason": "scanner"})
        return iocs[:500]

    def report(self):
        sources = self.sources()
        findings = sorted((f.out() for f in self.findings.values()),
                          key=lambda f: (-_SEV_RANK[f["severity"]], -f["count"]))
        timeline = self.timeline(sources)
        iocs = self.iocs(sources)
        return {
            "summary": self._summary(findings, sources),
            "limits": self.limits,
            "findings": findings,
            "sources": sources[:50],
            "status": {str(k): v for k, v in sorted(self.status_c.items())},
            "methods": dict(self.method_c.most_common(10)),
            "top_paths": [{"path": p, "count": c} for p, c in self.path_c.most_common(20)],
            "top_user_agents": [{"ua": u, "count": c, "scanner": bool(_SCANNER_UA.search(u))}
                                for u, c in self.ua_c.most_common(15)],
            "tool_clients": dict(self.tool_ua_c.most_common(8)),
            "hourly": [self.hours.get(h, 0) for h in range(24)],
            "timeline": timeline,
            "iocs": iocs,
        }

    def _summary(self, findings, sources):
        return {
            "lines_read": self.lines_read, "records": self.parsed, "unparsed": self.skipped,
            "formats": dict(self.kinds), "first": _iso(self.first_ts), "last": _iso(self.last_ts),
            "unique_ips": len(self.ip_stats), "findings": len(findings),
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "high": sum(1 for f in findings if f["severity"] == "high"),
            "block_candidates": sum(1 for s in sources if s["verdict"] == "block" and not s["non_public"]),
            "elapsed_ms": int((time.monotonic() - self.started) * 1000)}


def analyze(text, *, brute_force_min=10, enumeration_min=20, flood_per_minute=120):
    """Analyse log text. -> JSON-serialisable report dict (see _Analysis.report).

    Format is auto-detected per line, so a file may mix access-log, JSON and
    sshd lines. Thresholds are parameters so tests (and later a settings
    screen) can tune them without touching the detection logic."""
    run = _Analysis(text)
    run.read()
    run.detect(brute_force_min, enumeration_min, flood_per_minute)
    return run.report()
