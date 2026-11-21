#!/usr/bin/env python3
"""HRZEST firewall agent: connects every firewall layer on the host to the SOC
console (docs/FIREWALL.md).

Runs on the EC2 host as hrzest-fw-agent.service. Every few seconds it

  reports, over the signed channel POST /webhooks/firewall/events:
    host      [FW-DROP*] lines (ufw / nftables drops) from /var/log/ufw.log
    fail2ban  Ban / Unban lines from /var/log/fail2ban.log
    proxy     ModSecurity refusals (and nginx 429s) from its JSON audit log
    edge      AWS WAF BLOCK/COUNT/CAPTCHA/CHALLENGE records from CloudWatch Logs,
              and the WAF's own AllowedRequests counts         (AWS, optional)
    network   VPC flow-log REJECT records from CloudWatch Logs  (AWS, optional)

  enforces the SOC console's ban list (GET /webhooks/firewall/blocklist):
    host      nftables sets inet hrzest_fw block4 / block6 (scripts/harden_host_firewall.sh)
    edge      the AWS WAF block / trusted IP sets               (AWS, optional)

Attacker-supplied text (request paths, rule messages) is sent base64-encoded
(path_b64, detail_b64), so the WAF layers the report passes through don't
block it for containing the attack it describes. No request headers, cookies
or bodies are ever read or sent.

Standard library only, so it runs on the host's python3 with nothing
installed; the AWS parts need python3-boto3 (apt) and are off until their
settings are present. Configuration is environment variables, normally from
/etc/hrzest/firewall-agent.env (see firewall-agent.env.example).

    hrzest_fw_agent.py run     the service loop
    hrzest_fw_agent.py once    one collect / report / enforce cycle, then exit
    hrzest_fw_agent.py check   configuration and connectivity check (exit 1 on a problem)
"""
import base64
import calendar
import datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import subprocess  # nosec B404 -- runs nft and ss with fixed arguments, never a shell
import sys
import time
import urllib.parse

VERSION = "1.0.0"
MAX_BATCH = 1000
MAX_PENDING = 20_000
MAX_LINES_PER_FILE = 20_000     # per cycle, so a log flood can't stall the loop
EVENTS_PATH = "/webhooks/firewall/events"
BLOCKLIST_PATH = "/webhooks/firewall/blocklist"
SIGNATURE_HEADER = "X-Firewall-Signature"
TIMESTAMP_HEADER = "X-Firewall-Timestamp"


# ── configuration ────────────────────────────────────────────────────────────
def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _flag(name, default):
    v = _env(name, "true" if default else "false").lower()
    return v in ("1", "true", "yes", "on")


def _int(name, default, lo=1):
    try:
        return max(lo, int(_env(name, str(default))))
    except ValueError:
        return default


def _source_default():
    name = re.sub(r"[^a-z0-9._-]", "-", socket.gethostname().lower())[:56].strip("-.") or "host"
    return "host-" + name


class Config:
    def __init__(self):
        self.app_url = _env("HRZEST_FW_APP_URL", "https://127.0.0.1")
        self.app_host = _env("HRZEST_FW_APP_HOST")
        self.secret = _env("HRZEST_FW_SECRET")
        self.source = _env("HRZEST_FW_SOURCE") or _source_default()
        self.cafile = _env("HRZEST_FW_CAFILE")
        self.tls_verify = _flag("HRZEST_FW_TLS_VERIFY", True)
        self.state_dir = _env("HRZEST_FW_STATE_DIR", "/var/lib/hrzest-fw")
        self.kern_log = _env("HRZEST_FW_KERNLOG", "/var/log/ufw.log")
        self.fail2ban_log = _env("HRZEST_FW_FAIL2BAN_LOG", "/var/log/fail2ban.log")
        self.modsec_log = _env("HRZEST_FW_MODSEC_LOG", "/var/log/nginx/modsec/audit.json")
        self.report_interval = _int("HRZEST_FW_INTERVAL", 10)
        self.blocklist_interval = _int("HRZEST_FW_BLOCKLIST_INTERVAL", 15)
        self.host_enforce = _flag("HRZEST_FW_HOST_ENFORCE", True)
        self.nft = _env("HRZEST_FW_NFT", "/usr/sbin/nft")
        self.nft_file = _env("HRZEST_FW_NFT_FILE", "/etc/hrzest-fw/blocklist.nft")
        self.never_block = _env("HRZEST_FW_NEVER_BLOCK")
        # AWS edge (infra/terraform/edge outputs); each part is off while unset.
        self.aws_region = _env("HRZEST_FW_AWS_REGION")
        self.waf_scope = _env("HRZEST_FW_WAF_SCOPE", "REGIONAL")
        self.waf_sets = {k: _env(f"HRZEST_FW_WAF_{k.upper()}") for k in ("block_v4", "block_v6", "trusted_v4", "trusted_v6")}
        self.waf_log_group = _env("HRZEST_FW_WAF_LOG_GROUP")
        self.flow_log_group = _env("HRZEST_FW_FLOW_LOG_GROUP")
        self.waf_acl_name = _env("HRZEST_FW_WAF_ACL_NAME")
        self.aws_interval = _int("HRZEST_FW_AWS_INTERVAL", 60, lo=30)

    def problems(self):
        out = []
        if len(self.secret) < 32:
            out.append("HRZEST_FW_SECRET is missing or shorter than 32 characters (use the app's FIREWALL_AGENT_SECRET)")
        u = urllib.parse.urlsplit(self.app_url)
        if u.scheme not in ("https", "http") or not u.hostname:
            out.append("HRZEST_FW_APP_URL must be https://host[:port] (normally https://127.0.0.1)")
        if u.scheme == "https" and not self.app_host:
            out.append("HRZEST_FW_APP_HOST must name the site (e.g. hrzest.com): it is the TLS name and Host header")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", self.source) or self.source == "app":
            out.append("HRZEST_FW_SOURCE must be lowercase letters, digits, '.', '_' or '-' (and not 'app')")
        for k, v in self.waf_sets.items():
            if v and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}/[0-9a-f-]{36}", v):
                out.append(f"HRZEST_FW_WAF_{k.upper()} must be <name>/<id> (terraform output)")
        return out

    def aws_enabled(self):
        return bool(self.aws_region and (any(self.waf_sets.values()) or self.waf_log_group
                                         or self.flow_log_group or self.waf_acl_name))


# ── signing (same scheme as utils/firewall_events.py) ────────────────────────
def sign(message: bytes, timestamp: str, key: str) -> str:
    return hmac.new(key.encode(), timestamp.encode() + b"." + message, hashlib.sha256).hexdigest()


def signed_headers(message: bytes, key: str, now=None):
    ts = str(int(time.time() if now is None else now))
    return {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: "sha256=" + sign(message, ts, key)}


def b64(text):
    return base64.b64encode(text.encode("utf-8", "replace")).decode("ascii") if text else None


# ── parsing: host firewall ([FW-DROP*] kernel log lines) ─────────────────────
_KERN_PREFIX = re.compile(r"\[(FW-DROP(?:-[A-Z]+)?)\]")
_KV = re.compile(r"\b(SRC|DST|PROTO|DPT|SPT)=(\S+)")
_ISO_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?")
_BSD_TS = re.compile(r"^([A-Z][a-z]{2}) +(\d{1,2}) (\d\d):(\d\d):(\d\d)")
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def _syslog_time(line, now):
    m = _ISO_TS.match(line)
    if m:
        try:
            dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
            tz = m.group(2)
            if tz in (None, ""):
                return time.mktime(dt.timetuple())
            if tz == "Z":
                return calendar.timegm(dt.timetuple())
            sign_ = 1 if tz[0] == "+" else -1
            tz = tz[1:].replace(":", "")
            offset = sign_ * (int(tz[:2]) * 3600 + int(tz[2:]) * 60)
            return calendar.timegm(dt.timetuple()) - offset
        except ValueError:
            return now
    m = _BSD_TS.match(line)
    if m and m.group(1) in _MONTHS:
        year = time.localtime(now).tm_year
        try:
            dt = datetime.datetime(year, _MONTHS[m.group(1)], int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
        except ValueError:
            return now
        ts = time.mktime(dt.timetuple())
        return ts - 365 * 86400 if ts > now + 86400 else ts     # a December line read in January
    return now


def parse_kern_line(line, now=None):
    """One [FW-DROP*] kernel-log line -> event dict, or None."""
    now = time.time() if now is None else now
    m = _KERN_PREFIX.search(line)
    if not m:
        return None
    kv = dict(_KV.findall(line))
    tag = m.group(1)
    outbound = tag == "FW-DROP-OUT"
    remote = kv.get("DST") if outbound else kv.get("SRC")
    if not remote or _ip(remote) is None:
        return None
    port = kv.get("DPT")
    proto = (kv.get("PROTO") or "").lower()
    proto = {"icmpv6": "icmpv6", "ipv6-icmp": "icmpv6", "tcp": "tcp", "udp": "udp", "icmp": "icmp"}.get(proto, "other" if proto else None)
    return {
        "layer": "host", "action": "block", "ts": _syslog_time(line, now), "src_ip": str(_ip(remote)),
        "dst_port": int(port) if port and port.isdigit() and int(port) <= 65535 else None,
        "protocol": proto, "rule": tag,
        "detail_b64": b64("outbound to this address" if outbound else None),
    }


# ── parsing: fail2ban ────────────────────────────────────────────────────────
_F2B = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ fail2ban\.actions\s+\[\d+\]: NOTICE\s+\[([^\]]{1,60})\] (Ban|Unban) (\S+)\s*$")


def parse_fail2ban_line(line, now=None):
    """'... NOTICE  [jail] Ban 1.2.3.4' -> event dict, or None. 'Restore Ban'
    (fail2ban re-applying its bans after a restart) is not a new decision."""
    now = time.time() if now is None else now
    m = _F2B.match(line.strip())
    if not m or _ip(m.group(4)) is None:
        return None
    try:
        ts = time.mktime(datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timetuple())
    except ValueError:
        ts = now
    jail = re.sub(r"[^A-Za-z0-9_.:-]", "-", m.group(2))
    return {"layer": "fail2ban", "action": m.group(3).lower(), "ts": ts, "src_ip": str(_ip(m.group(4))), "rule": jail}


# ── parsing: ModSecurity JSON audit log ──────────────────────────────────────
_RULE_ID = re.compile(r"^[0-9]{3,7}$")


def parse_modsec_record(line, now=None):
    """One audit-log JSON line -> event dict, or None.

    A 403 with matched rules is a ModSecurity block; matched rules without a
    403 (DetectionOnly, or a path that only logs) is a 'count'; a 429 with no
    rules is nginx's own rate limit, which the audit log also records."""
    now = time.time() if now is None else now
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    tx = rec.get("transaction") if isinstance(rec, dict) else None
    if not isinstance(tx, dict) or _ip(str(tx.get("client_ip", ""))) is None:
        return None
    req = tx.get("request") or {}
    code = (tx.get("response") or {}).get("http_code")
    rules = []
    for msg in tx.get("messages") or []:
        rid = str(((msg or {}).get("details") or {}).get("ruleId", ""))
        if _RULE_ID.match(rid) and rid not in rules:
            rules.append(rid)
    if rules:
        action = "block" if code == 403 else "count"
        rule = "949110" if "949110" in rules else rules[0]
        contributing = [r for r in rules if r not in ("949110", "980170", "980130")]
        detail = "CRS rules " + ",".join(contributing[:20]) if contributing else None
    elif code == 429:
        action, rule, detail = "block", "nginx_limit_req", None
    else:
        return None
    ts = now
    stamp = tx.get("time_stamp")
    if isinstance(stamp, str):
        try:
            ts = time.mktime(datetime.datetime.strptime(stamp.strip(), "%a %b %d %H:%M:%S %Y").timetuple())
        except ValueError:
            pass
    method = req.get("method") if isinstance(req.get("method"), str) and re.fullmatch(r"[A-Z]{1,10}", req.get("method", "")) else None
    uri = req.get("uri") if isinstance(req.get("uri"), str) else None
    return {"layer": "proxy", "action": action, "ts": ts, "src_ip": str(_ip(tx["client_ip"])), "method": method,
            "path_b64": b64((uri or "")[:512]), "rule": rule, "detail_b64": b64(detail), "protocol": "tcp",
            "dst_port": 443}


# ── parsing: AWS ─────────────────────────────────────────────────────────────
_WAF_TERMINATING = {"BLOCK": "block", "CAPTCHA": "challenge", "CHALLENGE": "challenge"}
_RULE_SAFE = re.compile(r"[^A-Za-z0-9 _.:#/@+=-]")
# The edge's own labelling rule (infra/terraform/edge/waf.tf): it counts every
# trusted request by design, which is not a finding.
_WAF_IGNORED_COUNT_RULES = ("trusted",)


def _group_name(group):
    return str((group or {}).get("ruleGroupId", "")).split("#")[-1]


def _counted_rule(rec):
    """The first rule that matched without deciding (count mode, or a managed
    rule overridden to count), as 'Group:rule' -- or None."""
    for group in rec.get("ruleGroupList") or []:
        term = (group or {}).get("terminatingRule") or {}
        if term.get("ruleId"):                      # the whole group runs in count mode
            return f"{_group_name(group)}:{term['ruleId']}"
        for key in ("nonTerminatingMatchingRules", "excludedRules"):
            for nt in (group or {}).get(key) or []:
                if nt.get("ruleId"):
                    return f"{_group_name(group)}:{nt['ruleId']}"
    for nt in rec.get("nonTerminatingMatchingRules") or []:
        rid = str(nt.get("ruleId", ""))
        if rid and rid not in _WAF_IGNORED_COUNT_RULES:
            return rid
    return None


def parse_waf_log(message):
    """One AWS WAF log record (JSON) -> event dict, or None.

    BLOCK / CAPTCHA / CHALLENGE are decisions. A record whose final action is
    ALLOW (or COUNT) is only in the log because some rule counted it: that is
    a 'count' event for that rule, unless the only rule was the trusted label.
    Allowed traffic itself is reported from the WAF's metrics, not from here."""
    try:
        rec = json.loads(message)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    req = rec.get("httpRequest") or {}
    ip = _ip(str(req.get("clientIp", "")))
    if ip is None:
        return None
    final = str(rec.get("action", "")).upper()
    if final in _WAF_TERMINATING:
        action = _WAF_TERMINATING[final]
        rule = str(rec.get("terminatingRuleId") or "")
        for group in rec.get("ruleGroupList") or []:
            term = (group or {}).get("terminatingRule") or {}
            if term.get("ruleId") and str(term.get("action", "BLOCK")).upper() != "COUNT":
                rule = f"{_group_name(group) or rule}:{term['ruleId']}"
                break
    else:
        rule = _counted_rule(rec)
        if not rule:
            return None
        action = "count"
    host = next((h.get("value") for h in req.get("headers") or [] if str(h.get("name", "")).lower() == "host"), None)
    uri = str(req.get("uri") or "")
    if req.get("args"):
        uri += "?" + str(req["args"])
    ts = rec.get("timestamp")
    return {"layer": "edge", "action": action, "ts": ts / 1000.0 if isinstance(ts, (int, float)) else None,
            "src_ip": str(ip), "method": req.get("httpMethod") if re.fullmatch(r"[A-Z]{1,10}", str(req.get("httpMethod", ""))) else None,
            "host": host if isinstance(host, str) and re.fullmatch(r"[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?", host) else None,
            "path_b64": b64(uri[:512]), "rule": _RULE_SAFE.sub("-", rule)[:200] or None,
            "country": req.get("country") if re.fullmatch(r"[A-Z]{2}", str(req.get("country", ""))) else None,
            "protocol": "tcp", "dst_port": 443}


_PROTO_NUM = {"6": "tcp", "17": "udp", "1": "icmp", "58": "icmpv6"}


def parse_flow_log(message):
    """One VPC flow-log record in the default format -> event dict, or None.
    Only REJECT records (the security groups / network ACLs refusing)."""
    parts = message.split()
    if len(parts) < 14 or parts[12] != "REJECT":
        return None
    src = _ip(parts[3])
    if src is None:
        return None
    try:
        start = int(parts[10])
        packets = max(1, int(parts[8]))
    except ValueError:
        return None
    port = int(parts[6]) if parts[6].isdigit() and int(parts[6]) <= 65535 else None
    return {"layer": "network", "action": "block", "ts": float(start), "src_ip": str(src), "dst_port": port,
            "protocol": _PROTO_NUM.get(parts[7], "other"), "rule": "vpc-reject", "hits": min(packets, 10_000_000)}


# ── helpers ──────────────────────────────────────────────────────────────────
def _ip(text):
    try:
        addr = ipaddress.ip_address(text.strip().split("%", 1)[0])
    except (ValueError, AttributeError):
        return None
    return addr.ipv4_mapped if addr.version == 6 and addr.ipv4_mapped else addr


def aggregate(events):
    """Collapse repeats within one cycle: same layer, decision, address, rule,
    port, method and path -> one event with a hit count (first timestamp)."""
    out = {}
    for e in events:
        key = (e.get("layer"), e.get("action"), e.get("src_ip"), e.get("rule"), e.get("dst_port"),
               e.get("method"), e.get("path_b64"))
        if key in out:
            out[key]["hits"] = min(out[key].get("hits", 1) + e.get("hits", 1), 10_000_000)
        else:
            out[key] = dict(e, hits=e.get("hits", 1))
    return list(out.values())


class FileTail:
    """Reads lines appended to a log since the last call. Remembers inode and
    offset in the state dict; starts at the end of a file it has not seen
    (history is not replayed), and from the top after a rotation or a
    copytruncate."""

    def __init__(self, path, state, key):
        self.path, self.state, self.key = path, state, key

    def read(self, limit=MAX_LINES_PER_FILE):
        try:
            st = os.stat(self.path)
        except OSError as exc:
            raise FileNotFoundError(f"{self.path}: {exc.strerror}") from None
        mark = self.state.get(self.key)
        if not mark:
            self.state[self.key] = {"inode": st.st_ino, "offset": st.st_size}
            return []
        offset = mark["offset"] if mark.get("inode") == st.st_ino and st.st_size >= mark.get("offset", 0) else 0
        lines = []
        with open(self.path, "rb") as fh:
            fh.seek(offset)
            while len(lines) < limit:
                raw = fh.readline()
                if not raw or not raw.endswith(b"\n"):     # stop at a half-written line
                    break
                offset += len(raw)
                lines.append(raw.decode("utf-8", "replace").rstrip("\r\n"))
        self.state[self.key] = {"inode": st.st_ino, "offset": offset}
        return lines


# ── talking to the app ───────────────────────────────────────────────────────
class _SNIConnection(http.client.HTTPSConnection):
    """HTTPS to one address (127.0.0.1) with the site's name for SNI and
    certificate checking, so the agent reaches the local nginx directly and
    still verifies it is talking to the real site."""

    def __init__(self, host, port, server_hostname, context, timeout):
        super().__init__(host, port, timeout=timeout, context=context)
        self._server_hostname = server_hostname

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._server_hostname)


class AppClient:
    def __init__(self, cfg):
        self.cfg = cfg
        u = urllib.parse.urlsplit(cfg.app_url)
        self.scheme, self.addr = u.scheme, u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)

    def _conn(self):
        if self.scheme == "http":
            return http.client.HTTPConnection(self.addr, self.port, timeout=15)
        ctx = ssl.create_default_context(cafile=self.cfg.cafile or None)
        if not self.cfg.tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return _SNIConnection(self.addr, self.port, self.cfg.app_host or self.addr, ctx, 15)

    def request(self, method, path, body=b"", signed_message=None):
        headers = signed_headers(signed_message if signed_message is not None else body, self.cfg.secret)
        headers["Host"] = self.cfg.app_host or self.addr
        headers["User-Agent"] = f"hrzest-fw-agent/{VERSION}"
        if body:
            headers["Content-Type"] = "application/json"
        conn = self._conn()
        try:
            conn.request(method, path, body=body or None, headers=headers)
            resp = conn.getresponse()
            data = resp.read(4 * 1024 * 1024)
            return resp.status, data
        finally:
            conn.close()

    def report(self, events, status):
        body = json.dumps({"source": self.cfg.source, "status": status, "events": events},
                          separators=(",", ":")).encode()
        code, data = self.request("POST", EVENTS_PATH, body)
        if code != 200:
            raise RuntimeError(f"report refused: HTTP {code} {data[:200]!r}")
        return json.loads(data or b"{}")

    def blocklist(self):
        code, data = self.request("GET", BLOCKLIST_PATH, signed_message=("GET " + BLOCKLIST_PATH).encode())
        if code != 200:
            raise RuntimeError(f"blocklist refused: HTTP {code}")
        out = json.loads(data)
        if not isinstance(out, dict) or not isinstance(out.get("block"), list):
            raise RuntimeError("blocklist: unexpected answer")
        return out


# ── enforcing the ban list ───────────────────────────────────────────────────
def _networks(csv):
    nets = []
    for part in (csv or "").split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                pass
    return nets


def ssh_peers():
    """Addresses with an established SSH session to this host right now: never
    blocked here, whatever the list says, so a ban can't cut off an admin."""
    try:
        out = subprocess.run(["ss", "-Htn", "state", "established", "( sport = :22 )"],  # nosec B603 B607
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    peers = []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 4:
            host = cols[-1].rsplit(":", 1)[0].strip("[]")
            addr = _ip(host)
            if addr is not None:
                peers.append(addr)
    return peers


def plan_blocks(blocklist, never_block, peers):
    """-> (v4 CIDRs, v6 CIDRs, skipped) to enforce. Drops anything that covers
    a never-block range or a live SSH peer."""
    v4, v6, skipped = [], [], []
    for item in blocklist.get("block") or []:
        try:
            net = ipaddress.ip_network(str(item.get("cidr")), strict=False)
        except (ValueError, AttributeError):
            continue
        if any(net.version == n.version and net.overlaps(n) for n in never_block) or \
                any(p.version == net.version and p in net for p in peers):
            skipped.append(str(net))
            continue
        (v4 if net.version == 4 else v6).append(str(net))
    return sorted(set(v4)), sorted(set(v6)), skipped


def nft_script(v4, v6):
    """One atomic nft transaction that replaces both sets' contents."""
    lines = ["flush set inet hrzest_fw block4", "flush set inet hrzest_fw block6"]
    for name, items in (("block4", v4), ("block6", v6)):
        for i in range(0, len(items), 500):
            lines.append(f"add element inet hrzest_fw {name} {{ {', '.join(items[i:i + 500])} }}")
    return "\n".join(lines) + "\n"


class HostEnforcer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.applied = None

    def _nft(self, args, stdin=None):
        return subprocess.run([self.cfg.nft] + args, input=stdin, capture_output=True, text=True, timeout=20)  # nosec B603

    def ensure_table(self):
        if self._nft(["list", "table", "inet", "hrzest_fw"]).returncode == 0:
            return False
        if not os.path.exists(self.cfg.nft_file):
            raise RuntimeError(f"nft table inet hrzest_fw is missing and {self.cfg.nft_file} does not exist "
                               "(run scripts/harden_host_firewall.sh)")
        r = self._nft(["-f", self.cfg.nft_file])
        if r.returncode != 0:
            raise RuntimeError(f"nft -f {self.cfg.nft_file}: {r.stderr.strip()[:200]}")
        self.applied = None
        return True

    def apply(self, v4, v6):
        self.ensure_table()
        digest = hashlib.sha256(("|".join(v4) + "#" + "|".join(v6)).encode()).hexdigest()
        if digest == self.applied:
            return False
        r = self._nft(["-f", "-"], stdin=nft_script(v4, v6))
        if r.returncode != 0:
            raise RuntimeError(f"nft: {r.stderr.strip()[:300]}")
        self.applied = digest
        return True


class EdgeEnforcer:
    """Keeps the AWS WAF IP sets equal to the ban list (block) and the trusted
    list (excluded from the edge's rate limits). Needs wafv2:GetIPSet and
    wafv2:UpdateIPSet on exactly those sets (infra/terraform/edge/iam.tf)."""

    def __init__(self, cfg, boto3):
        self.cfg = cfg
        self.client = boto3.client("wafv2", region_name=cfg.aws_region)

    def _sync(self, ref, addresses):
        name, set_id = ref.split("/", 1)
        for attempt in (1, 2):
            got = self.client.get_ip_set(Name=name, Scope=self.cfg.waf_scope, Id=set_id)
            current = set(got["IPSet"].get("Addresses") or [])
            if current == set(addresses):
                return False
            try:
                self.client.update_ip_set(Name=name, Scope=self.cfg.waf_scope, Id=set_id,
                                          Addresses=sorted(addresses), LockToken=got["LockToken"])
                return True
            except self.client.exceptions.WAFOptimisticLockException:
                if attempt == 2:
                    raise
        return False

    def apply(self, v4, v6, trusted):
        t4 = [c for c in trusted if ":" not in c]
        t6 = [c for c in trusted if ":" in c]
        changed = False
        for key, addresses in (("block_v4", v4), ("block_v6", v6), ("trusted_v4", t4), ("trusted_v6", t6)):
            ref = self.cfg.waf_sets.get(key)
            if ref:
                changed = self._sync(ref, addresses[:10_000]) or changed
        return changed


# ── pulling the AWS edge's records ───────────────────────────────────────────
class CloudWatchSource:
    """WAF and flow-log records from CloudWatch Logs, and the WAF's allowed
    request counts from CloudWatch metrics. Cursors live in the state file."""

    def __init__(self, cfg, boto3, state):
        self.cfg, self.state = cfg, state
        self.logs = boto3.client("logs", region_name=cfg.aws_region)
        self.metrics = boto3.client("cloudwatch", region_name=cfg.aws_region)

    def _log_events(self, group, key, pattern=None, max_pages=10):
        now_ms = int(time.time() * 1000)
        start = self.state.get(key) or now_ms - 5 * 60 * 1000
        kwargs = {"logGroupName": group, "startTime": start, "endTime": now_ms}
        if pattern:
            kwargs["filterPattern"] = pattern
        latest, out = start, []
        for _ in range(max_pages):
            page = self.logs.filter_log_events(**kwargs)
            for ev in page.get("events", []):
                out.append(ev.get("message", ""))
                latest = max(latest, int(ev.get("timestamp", start)) + 1)
            token = page.get("nextToken")
            if not token:
                break
            kwargs["nextToken"] = token
        self.state[key] = latest
        return out

    def waf_events(self):
        if not self.cfg.waf_log_group:
            return []
        return [e for e in (parse_waf_log(m) for m in self._log_events(self.cfg.waf_log_group, "cw_waf")) if e]

    def flow_events(self):
        if not self.cfg.flow_log_group:
            return []
        msgs = self._log_events(self.cfg.flow_log_group, "cw_flow", pattern='" REJECT "')
        return [e for e in (parse_flow_log(m) for m in msgs) if e]

    def allowed_counts(self):
        """AllowedRequests per 5 minutes, for every period that has closed since
        the last call -> 'allow' events with no address."""
        if not self.cfg.waf_acl_name:
            return []
        period = 300
        end = int(time.time() // period * period) - period          # last closed period
        start = self.state.get("cw_allow") or end - period
        if end <= start:
            return []
        got = self.metrics.get_metric_statistics(
            Namespace="AWS/WAFV2", MetricName="AllowedRequests", Period=period, Statistics=["Sum"],
            StartTime=datetime.datetime.fromtimestamp(start, datetime.timezone.utc),
            EndTime=datetime.datetime.fromtimestamp(end, datetime.timezone.utc),
            Dimensions=[{"Name": "WebACL", "Value": self.cfg.waf_acl_name},
                        {"Name": "Region", "Value": self.cfg.aws_region},
                        {"Name": "Rule", "Value": "ALL"}])
        self.state["cw_allow"] = end
        out = []
        for dp in got.get("Datapoints", []):
            n = int(dp.get("Sum") or 0)
            if n > 0:
                out.append({"layer": "edge", "action": "allow", "ts": dp["Timestamp"].timestamp(),
                            "rule": "AllowedRequests", "hits": min(n, 10_000_000)})
        return out


# ── the agent ────────────────────────────────────────────────────────────────
class Agent:
    def __init__(self, cfg, client=None, boto3=None):
        self.cfg = cfg
        self.client = client or AppClient(cfg)
        self.state_path = os.path.join(cfg.state_dir, "state.json")
        self.state = self._load_state()
        self.pending = []
        self.dropped = 0
        self.layers = {}
        self.blocklist_status = {}
        self.last_blocklist = 0.0
        self.last_aws = 0.0
        self.tails = [
            ("host", FileTail(cfg.kern_log, self.state, "kern"), parse_kern_line),
            ("fail2ban", FileTail(cfg.fail2ban_log, self.state, "f2b"), parse_fail2ban_line),
            ("proxy", FileTail(cfg.modsec_log, self.state, "modsec"), parse_modsec_record),
        ]
        self.host = HostEnforcer(cfg) if cfg.host_enforce else None
        self.edge = self.cloud = None
        if cfg.aws_enabled():
            if boto3 is None:
                try:
                    import boto3 as _boto3  # noqa: PLC0415 -- optional dependency
                    boto3 = _boto3
                except ImportError:
                    self.layers["edge"] = {"ok": False, "error": "python3-boto3 not installed"}
            if boto3 is not None:
                if any(cfg.waf_sets.values()):
                    self.edge = EdgeEnforcer(cfg, boto3)
                if cfg.waf_log_group or cfg.flow_log_group or cfg.waf_acl_name:
                    self.cloud = CloudWatchSource(cfg, boto3, self.state)

    def _load_state(self):
        try:
            with open(self.state_path) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        os.makedirs(self.cfg.state_dir, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self.state_path)

    def _ok(self, layer):
        self.layers[layer] = {"ok": True, "at": int(time.time())}

    def _fail(self, layer, exc):
        self.layers[layer] = {"ok": False, "error": str(exc)[:160], "at": int(time.time())}

    def collect(self):
        events = []
        now = time.time()
        for layer, tail, parse in self.tails:
            try:
                for line in tail.read():
                    e = parse(line, now)
                    if e:
                        events.append(e)
                self._ok(layer)
            except FileNotFoundError as exc:
                self._fail(layer, exc)
            except OSError as exc:
                self._fail(layer, exc)
        if self.cloud and time.time() - self.last_aws >= self.cfg.aws_interval:
            self.last_aws = time.time()
            for layer, fn in (("edge", self.cloud.waf_events), ("edge", self.cloud.allowed_counts),
                              ("network", self.cloud.flow_events)):
                try:
                    events += fn()
                    self._ok(layer)
                except Exception as exc:        # boto3 raises many types; report, keep running
                    self._fail(layer, exc)
        return aggregate(events)

    def status(self):
        return {"version": VERSION, "mode": "edge" if self.cfg.aws_enabled() else "direct",
                "layers": self.layers, "blocklist": self.blocklist_status, "dropped_events": self.dropped}

    def send(self):
        sent = 0
        while True:
            batch = self.pending[:MAX_BATCH]
            self.client.report(batch, self.status())
            del self.pending[:len(batch)]
            sent += len(batch)
            if not self.pending:
                return sent

    def enforce(self):
        bl = self.client.blocklist()
        never = _networks(self.cfg.never_block)
        v4, v6, skipped = plan_blocks(bl, never, ssh_peers())
        status = {"entries": len(v4) + len(v6), "skipped": len(skipped), "synced_at": datetime.datetime.now().isoformat(timespec="seconds")}
        if self.host:
            try:
                status["host_changed"] = self.host.apply(v4, v6)
                self._ok("host_blocklist")
            except Exception as exc:
                self._fail("host_blocklist", exc)
        if self.edge:
            try:
                trusted = [str(ipaddress.ip_network(c, strict=False)) for c in bl.get("trusted") or []
                           if _valid_net(c)]
                status["edge_changed"] = self.edge.apply(v4, v6, trusted)
                self._ok("edge_blocklist")
            except Exception as exc:
                self._fail("edge_blocklist", exc)
        self.blocklist_status = status
        return status

    def cycle(self):
        new = self.collect()
        self.pending += new
        if len(self.pending) > MAX_PENDING:
            self.dropped += len(self.pending) - MAX_PENDING
            self.pending = self.pending[-MAX_PENDING:]
        if time.time() - self.last_blocklist >= self.cfg.blocklist_interval:
            self.last_blocklist = time.time()
            try:
                self.enforce()
                self._ok("app_blocklist")
            except Exception as exc:
                self._fail("app_blocklist", exc)
        try:
            sent = self.send()
            self._ok("app_report")
        except Exception as exc:
            self._fail("app_report", exc)
            sent = 0
        self._save_state()
        return sent


def _valid_net(text):
    try:
        ipaddress.ip_network(str(text), strict=False)
        return True
    except ValueError:
        return False


# ── entry points ─────────────────────────────────────────────────────────────
def _log(msg):
    print(f"hrzest-fw-agent: {msg}", flush=True)


def cmd_check(cfg):
    problems = cfg.problems()
    for p in problems:
        _log("FAIL " + p)
    if problems:
        return 1
    rc = 0
    for label, path in (("host firewall log", cfg.kern_log), ("fail2ban log", cfg.fail2ban_log),
                        ("ModSecurity audit log", cfg.modsec_log)):
        if os.access(path, os.R_OK):
            _log(f"PASS {label}: {path}")
        else:
            _log(f"WARN {label}: {path} not readable yet (that layer reports an error until it is)")
    client = AppClient(cfg)
    try:
        bl = client.blocklist()
        _log(f"PASS app reachable and signature accepted: {len(bl['block'])} active bans")
        client.report([], {"version": VERSION, "check": True})
        _log("PASS report accepted")
    except Exception as exc:
        _log(f"FAIL app: {exc}")
        rc = 1
    if cfg.host_enforce:
        try:
            created = HostEnforcer(cfg).ensure_table()
            _log("PASS nft table inet hrzest_fw " + ("created" if created else "present"))
        except Exception as exc:
            _log(f"FAIL nft: {exc}")
            rc = 1
    if cfg.aws_enabled():
        try:
            import boto3  # noqa: F401,PLC0415
            _log("PASS boto3 available for the AWS edge")
        except ImportError:
            _log("FAIL AWS settings are present but python3-boto3 is not installed")
            rc = 1
    return rc


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    cfg = Config()
    if cmd == "check":
        return cmd_check(cfg)
    problems = cfg.problems()
    if problems:
        for p in problems:
            _log("config: " + p)
        return 2
    agent = Agent(cfg)
    if cmd == "once":
        n = agent.cycle()
        _log(f"sent {n} events; status {json.dumps(agent.status())}")
        return 0 if all(v.get("ok") for v in agent.layers.values() if isinstance(v, dict)) else 1
    if cmd != "run":
        _log(f"unknown command {cmd!r} (run, once, check)")
        return 2
    stop = {"now": False}

    def _stop(*_a):
        stop["now"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    _log(f"started (source {cfg.source}, every {cfg.report_interval}s, blocklist every {cfg.blocklist_interval}s)")
    last_errors = None
    while not stop["now"]:
        started = time.time()
        try:
            agent.cycle()
        except Exception as exc:          # never let one bad cycle kill the service
            _log(f"cycle failed: {exc}")
        errors = sorted(k for k, v in agent.layers.items() if isinstance(v, dict) and not v.get("ok"))
        if errors != last_errors:
            _log("all layers ok" if not errors else "problems: " + ", ".join(f"{k} ({agent.layers[k].get('error')})" for k in errors))
            last_errors = errors
        while not stop["now"] and time.time() - started < cfg.report_interval:
            time.sleep(0.5)
    _log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
