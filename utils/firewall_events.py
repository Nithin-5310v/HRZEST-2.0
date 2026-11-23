# -*- coding: utf-8 -*-
"""Firewall decisions from every layer, in one place (att_master.firewall_events).

Layers (docs/FIREWALL.md):

  edge      AWS WAF on the load balancer        (firewall agent, from CloudWatch)
  network   VPC flow logs: security-group REJECTs (firewall agent, from CloudWatch)
  host      ufw / nftables drops, [FW-DROP]      (firewall agent, from the kernel log)
  proxy     nginx + ModSecurity (OWASP CRS)      (firewall agent, from the audit log)
  fail2ban  bans and unbans                      (fail2ban action -> firewall agent)
  app       this app's own guards: IP/CIDR ban, WAF signatures, scanner
            user agents, rate limits             (recorded here, in-process)

Everything outside the app arrives through one signed channel,
POST /webhooks/firewall/events (blueprints/firewall_ingest.py), from the host's
firewall agent (scripts/firewall_agent/). The same agent fetches the active
ban list (blocklist()) and enforces it in the host firewall and, when the edge
exists, in the AWS WAF block list -- so a ban made in the SOC console reaches
every layer, not just the app.

Rows are recorded as they happened: nothing here is sample data, and nothing is
looked up at an outside service. Allowed traffic is counted only where a layer
keeps a real counter (the edge WAF's own metrics, reported by the agent):
storing every allowed request or packet in the app's database would be the
wrong tool -- those stay in nginx's access log and CloudWatch.

Attacker-controlled text (request path, rule message) is stored as text and the
console renders it as text only. Reporters send it base64-encoded (`path_b64`,
`detail_b64`) so the WAF layers it passes through don't block the report itself
for containing the attack it describes.
"""
import base64
import binascii
import datetime
import hashlib
import hmac
import json
import os
import re
import threading
import time

LAYERS = ("edge", "network", "host", "proxy", "fail2ban", "app")
ACTIONS = ("block", "allow", "count", "challenge", "ban", "unban")
REPORTER_LAYERS = tuple(x for x in LAYERS if x != "app")    # only the app itself writes 'app'
MAX_BATCH = 1000
MAX_STATUS_CHARS = 4000
MAX_HITS = 10_000_000
EVENT_LIMIT = 200
BLOCKLIST_LIMIT = 10_000        # an AWS WAF IP set holds at most 10,000 addresses

# Signed channel (shared with scripts/firewall_agent/hrzest_fw_agent.py, which
# keeps its own stdlib copy of sign()).
SIGNATURE_HEADER = "X-Firewall-Signature"
TIMESTAMP_HEADER = "X-Firewall-Timestamp"
MAX_SKEW_SECONDS = 300
MIN_SECRET_CHARS = 32
BLOCKLIST_PATH = "/webhooks/firewall/blocklist"

_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RULE_RE = re.compile(r"^[A-Za-z0-9 _.:#/@+=-]{1,200}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?$")
_METHOD_RE = re.compile(r"^[A-Z]{1,10}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_PROTOCOLS = ("tcp", "udp", "icmp", "icmpv6", "other")


# ── signing ──────────────────────────────────────────────────────────
def secret():
    """FIREWALL_AGENT_SECRET, or "" when unset or too short (the channel is then
    closed: every report and blocklist request is refused)."""
    value = os.environ.get("FIREWALL_AGENT_SECRET", "").strip()
    return value if len(value) >= MIN_SECRET_CHARS else ""


def sign(message: bytes, timestamp: str, key: str) -> str:
    """HMAC-SHA256 over `<timestamp>.<message>`, the honeypot channel's scheme
    (utils/honeypot_report.py): the timestamp is inside the MAC, so a captured
    request can't be replayed outside the skew window."""
    return hmac.new(key.encode(), timestamp.encode() + b"." + message, hashlib.sha256).hexdigest()


def verify(message: bytes, timestamp: str, signature: str, key: str, now=None) -> bool:
    if not (key and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    if abs(now - ts) > MAX_SKEW_SECONDS:
        return False
    sig = signature[7:] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(sign(message, timestamp, key), sig)


def blocklist_message():
    """What a blocklist GET signs: there is no body, so the method and path."""
    return ("GET " + BLOCKLIST_PATH).encode()


# ── the app's own blocks ─────────────────────────────────────────────
# Aggregated per worker for a few seconds before one batched insert: a banned
# scanner hammering the site would otherwise turn every refused request into a
# database write, which is exactly the load the ban is meant to shed.
_FLUSH_SECONDS = 5.0
_MAX_PENDING_KEYS = 5000
_pending_lock = threading.Lock()
_pending = {}
_dropped = 0
_flusher_pid = None


def record_app_block(rule, *, ip, method=None, host=None, path=None, detail=None):
    """Note one request the app refused (rule: 'ip_ban', 'cidr_ban',
    'waf.sqli_blocked', 'scanner_ua', 'rate_limit', ...). Called on the request
    thread: never raises and never touches the database here."""
    global _dropped
    try:
        from utils.client_ip import parse_ip
        addr = parse_ip(ip) if ip else None
        row = {
            "occurred_at": time.time(),
            "layer": "app",
            "action": "block",
            "src_ip": str(addr) if addr is not None else None,
            "dst_port": None,
            "protocol": "tcp",
            "method": method if method and _METHOD_RE.match(method) else None,
            "host": host if host and _HOST_RE.match(host) else None,
            "path": (path or "")[:512] or None,
            "rule": rule if rule and _RULE_RE.match(rule) else "app",
            "detail": (detail or "")[:500] or None,
            "country": None,
            "hits": 1,
        }
        key = (row["rule"], row["src_ip"], row["method"], row["path"])
        with _pending_lock:
            entry = _pending.get(key)
            if entry is not None:
                entry["hits"] += 1
            elif len(_pending) < _MAX_PENDING_KEYS:
                _pending[key] = row
            else:
                _dropped += 1
    except Exception:       # recording must never be why a request fails
        return
    # Getting the row written is a separate step on purpose: when it failed
    # inside the block above (an import that had gone missing), every app
    # block stayed in memory and never reached the SOC console, silently.
    try:
        from utils.async_writer import is_synchronous_mode
        if is_synchronous_mode():
            flush()
            return
    except Exception:
        _warn_once("firewall_events: could not check the writer mode; writing in the background")
    try:
        _ensure_flusher()
    except Exception:
        _warn_once("firewall_events: could not start the background writer; app blocks are not being recorded")


_warned = set()


def _warn_once(message):
    if message in _warned:
        return
    _warned.add(message)
    try:
        from extensions import app_log
        app_log.warning(message, exc_info=True)
    except Exception:
        pass


def flush():
    """Write whatever the app has noted since the last flush (one insert)."""
    global _dropped
    with _pending_lock:
        rows = list(_pending.values())
        _pending.clear()
        dropped, _dropped = _dropped, 0
    if dropped:
        from extensions import app_log
        app_log.warning("firewall_events: %d app block records dropped (aggregation table full)", dropped)
    if rows:
        from utils.async_writer import enqueue_write
        enqueue_write(_write_rows, rows, "app", None, None)


def _ensure_flusher():
    """One daemon flusher per process (gunicorn forks workers after import, so
    the check is per pid, not per module)."""
    global _flusher_pid
    pid = os.getpid()
    if _flusher_pid == pid:
        return
    with _pending_lock:
        if _flusher_pid == pid:
            return
        _flusher_pid = pid
    threading.Thread(target=_flush_loop, name="firewall-events-flush", daemon=True).start()


def _flush_loop():
    while True:
        time.sleep(_FLUSH_SECONDS)
        try:
            flush()
        except Exception:
            pass


def _write_rows(rows, source, reporter_ip, status_json):
    """Insert `rows` and update the reporter's heartbeat. Runs on the writer
    thread (app blocks) or in the ingest request (reporters)."""
    from utils.soc_db import write_cursor
    with write_cursor("att_master") as cur:
        if rows:
            cur.executemany(
                "INSERT INTO firewall_events (occurred_at, layer, action, src_ip, dst_port, protocol, method, host, "
                "path, rule, detail, country, hits, source) "
                "VALUES (to_timestamp(%s)::timestamp, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(r["occurred_at"], r["layer"], r["action"], r["src_ip"], r["dst_port"], r["protocol"], r["method"],
                  r["host"], r["path"], r["rule"], r["detail"], r["country"], r["hits"], source) for r in rows])
        cur.execute(
            "INSERT INTO firewall_sources (source, last_seen, last_ip, events_total, status_json) "
            "VALUES (%s, CURRENT_TIMESTAMP, %s, %s, %s) "
            "ON CONFLICT (source) DO UPDATE SET last_seen = CURRENT_TIMESTAMP, "
            "  last_ip = COALESCE(EXCLUDED.last_ip, firewall_sources.last_ip), "
            "  events_total = firewall_sources.events_total + EXCLUDED.events_total, "
            "  status_json = COALESCE(EXCLUDED.status_json, firewall_sources.status_json)",
            (source, reporter_ip, sum(r["hits"] for r in rows), status_json))


# ── reports from the other layers ────────────────────────────────────
def _b64_text(value, cap):
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None
    return raw.decode("utf-8", "replace")[:cap] or None


def _epoch(value, now):
    """Reporter timestamp -> epoch seconds, or None when missing or implausible
    (more than 7 days old or 5 minutes in the future)."""
    if value is None:
        return now
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value > 1e12:            # milliseconds (CloudWatch, AWS WAF logs)
        value = value / 1000.0
    if value < now - 7 * 86400 or value > now + 300:
        return None
    return float(value)


def normalize(raw, now=None):
    """One reported event -> a row dict, or None when it is malformed. Strict:
    an unknown layer, action or an unparseable address rejects the event rather
    than storing a guess."""
    from utils.client_ip import parse_ip
    if not isinstance(raw, dict):
        return None
    now = time.time() if now is None else now
    layer, action = raw.get("layer"), raw.get("action")
    if layer not in REPORTER_LAYERS or action not in ACTIONS:
        return None
    ts = _epoch(raw.get("ts"), now)
    if ts is None:
        return None
    src = raw.get("src_ip")
    src_ip = None
    if src not in (None, ""):
        addr = parse_ip(src) if isinstance(src, str) else None
        if addr is None:
            return None
        src_ip = str(addr)
    port = raw.get("dst_port")
    if port is not None:
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            return None
    proto = raw.get("protocol")
    proto = proto.lower() if isinstance(proto, str) else None
    hits = raw.get("hits", 1)
    if isinstance(hits, bool) or not isinstance(hits, int) or not 1 <= hits <= MAX_HITS:
        return None
    method, host, rule, country = raw.get("method"), raw.get("host"), raw.get("rule"), raw.get("country")
    return {
        "occurred_at": ts,
        "layer": layer,
        "action": action,
        "src_ip": src_ip,
        "dst_port": port,
        "protocol": proto if proto in _PROTOCOLS else ("other" if proto else None),
        "method": method if isinstance(method, str) and _METHOD_RE.match(method) else None,
        "host": host if isinstance(host, str) and _HOST_RE.match(host) else None,
        "path": _b64_text(raw.get("path_b64"), 512),
        "rule": rule if isinstance(rule, str) and _RULE_RE.match(rule) else None,
        "detail": _b64_text(raw.get("detail_b64"), 500),
        "country": country if isinstance(country, str) and _COUNTRY_RE.match(country) else None,
        "hits": hits,
    }


def fail2ban_ban_minutes():
    try:
        return max(1, int(os.environ.get("FIREWALL_FAIL2BAN_REVIEW_MINUTES", "60")))
    except ValueError:
        return 60


def ingest(payload, reporter_ip=None):
    """A signed report from a firewall agent -> (ok, result).

    payload: {"source": "<agent id>", "status": {...}, "events": [ ... ]}.
    A fail2ban ban is also raised for an analyst's decision (Ban review), so a
    host-level ban that lapses after an hour can become a platform-wide one --
    or be trusted, if it was a customer office."""
    if not isinstance(payload, dict):
        return False, "Expected a JSON object."
    source = payload.get("source")
    if not isinstance(source, str) or not _SOURCE_RE.match(source) or source == "app":
        return False, "Invalid source."
    events = payload.get("events", [])
    if not isinstance(events, list):
        return False, "events must be a list."
    if len(events) > MAX_BATCH:
        return False, f"At most {MAX_BATCH} events per report."
    status = payload.get("status")
    status_json = None
    if isinstance(status, dict):
        status_json = json.dumps(status, default=str, separators=(",", ":"))
        if len(status_json) > MAX_STATUS_CHARS:
            status_json = json.dumps({"truncated": True})
    now = time.time()
    rows, rejected = [], 0
    for raw in events:
        row = normalize(raw, now)
        if row is None:
            rejected += 1
        else:
            rows.append(row)
    _write_rows(rows, source, reporter_ip, status_json)
    _raise_reviews(rows)
    return True, {"accepted": len(rows), "rejected": rejected}


def _raise_reviews(rows):
    from extensions import log_security_event
    from utils import soc_ban_review
    seen = set()
    for r in rows:
        if r["layer"] != "fail2ban" or r["action"] != "ban" or not r["src_ip"] or r["src_ip"] in seen:
            continue
        seen.add(r["src_ip"])
        jail = r["rule"] or "fail2ban"
        log_security_event("firewall.fail2ban_ban", "fail2ban banned an address on the host",
                           level="WARNING", identifier=r["src_ip"], ip=r["src_ip"], jail=jail)
        soc_ban_review.raise_review(r["src_ip"], "fail2ban", f"fail2ban jail {jail}",
                                    suggested_minutes=fail2ban_ban_minutes())


# ── the ban list the agent enforces ──────────────────────────────────
def _cidr_of(ip):
    from utils.client_ip import parse_ip
    addr = parse_ip(ip)
    if addr is None:
        return None
    return f"{addr}/{32 if addr.version == 4 else 128}"


def _overlaps_protected(net):
    from utils.client_ip import never_ban_networks
    return any(net.version == p.version and net.overlaps(p) for p in never_ban_networks())


def blocklist():
    """Active platform-wide bans and trusted addresses, as CIDRs:
    {"generated_at", "block": [{"cidr", "expires", "reason"}], "trusted": [...]}.

    Only the platform list (public.banned_ips / banned_cidrs): a company's own
    banned_ips applies to that company's pages only, and the edge cannot tell
    companies apart. Never-ban ranges are dropped here too, whatever is stored."""
    import ipaddress
    from utils.client_ip import is_protected_ip, never_ban_networks
    from utils.soc_db import read_cursor
    from utils.soc_util import iso
    block = []
    with read_cursor("public") as cur:
        cur.execute("SELECT ip, expires_at, reason FROM public.banned_ips "
                    "WHERE expires_at IS NULL OR expires_at > NOW() ORDER BY banned_at DESC LIMIT %s",
                    (BLOCKLIST_LIMIT,))
        for ip, expires, reason in cur.fetchall():
            if is_protected_ip(ip):
                continue
            cidr = _cidr_of(ip)
            if cidr:
                block.append({"cidr": cidr, "expires": iso(expires), "reason": (reason or "")[:120]})
        cur.execute("SELECT to_regclass('public.banned_cidrs')")
        if cur.fetchone()[0] is not None:
            cur.execute("SELECT cidr::text, expires_at, reason FROM public.banned_cidrs "
                        "WHERE expires_at IS NULL OR expires_at > NOW() ORDER BY banned_at DESC LIMIT %s",
                        (BLOCKLIST_LIMIT,))
            for cidr, expires, reason in cur.fetchall():
                net = ipaddress.ip_network(cidr, strict=False)
                if _overlaps_protected(net):
                    continue
                block.append({"cidr": str(net), "expires": iso(expires), "reason": (reason or "")[:120]})
    trusted = []
    with read_cursor("att_master") as cur:
        cur.execute("SELECT ip FROM soc_ban_reviews WHERE status='trusted' "
                    "AND (trusted_until IS NULL OR trusted_until > NOW()) ORDER BY ip")
        for (ip,) in cur.fetchall():
            cidr = _cidr_of(ip)
            if cidr:
                trusted.append(cidr)
    # Operator never-ban ranges that are real internet addresses (an office's
    # egress, an uptime probe) are exempt at the edge too; private ones mean
    # nothing there.
    from utils.client_ip import internal_networks
    internal = [ipaddress.ip_network(n) for n in internal_networks()]
    for net in never_ban_networks():
        if not any(net.version == i.version and net.overlaps(i) for i in internal):
            trusted.append(str(net))
    return {
        "generated_at": iso(datetime.datetime.now()),
        "block": block[:BLOCKLIST_LIMIT],
        "trusted": sorted(set(trusted))[:BLOCKLIST_LIMIT],
    }


# ── SOC console queries ──────────────────────────────────────────────
def reporter_stale_minutes():
    try:
        return max(1, int(os.environ.get("FIREWALL_REPORTER_STALE_MINUTES", "10")))
    except ValueError:
        return 10


def _hidden_networks():
    """-> (internal CIDRs, test CIDRs) as strings, for server-side exclusion
    (the console's genuine-activity rules, utils/soc_scope.py)."""
    from utils import soc_scope
    from utils.client_ip import internal_networks
    return internal_networks(), [str(n) for n in soc_scope.test_networks()]


_GENUINE = "(src_ip IS NULL OR NOT (src_ip::inet <<= ANY(%s::inet[])))"


def overview(hours=24):
    """Per-layer counts, the busiest blocked sources and rules, an hourly
    timeline and each reporter's heartbeat, over the last `hours`."""
    from utils.soc_db import read_cursor
    from utils.soc_util import iso
    internal, test = _hidden_networks()
    hidden = internal + test
    since = "occurred_at > NOW() - make_interval(hours => %s)"
    with read_cursor("att_master") as cur:
        cur.execute(f"SELECT layer, action, SUM(hits) FROM firewall_events WHERE {since} AND {_GENUINE} "  # nosec B608 -- fixed fragments
                    "GROUP BY layer, action", (hours, hidden))
        layers = {name: {a: 0 for a in ACTIONS} for name in LAYERS}
        for layer, action, n in cur.fetchall():
            if layer in layers and action in layers[layer]:
                layers[layer][action] = int(n)
        cur.execute(f"SELECT date_trunc('hour', occurred_at) AS t, layer, SUM(hits) FROM firewall_events "  # nosec B608
                    f"WHERE {since} AND {_GENUINE} AND action IN ('block','ban','challenge') "
                    "GROUP BY t, layer ORDER BY t", (hours, hidden))
        timeline = [{"t": iso(t), "layer": layer, "blocked": int(n)} for t, layer, n in cur.fetchall()]
        cur.execute(f"SELECT src_ip, SUM(hits), array_agg(DISTINCT layer), MAX(occurred_at) FROM firewall_events "  # nosec B608
                    f"WHERE {since} AND {_GENUINE} AND src_ip IS NOT NULL AND action IN ('block','ban','challenge') "
                    "GROUP BY src_ip ORDER BY SUM(hits) DESC LIMIT 15", (hours, hidden))
        top_sources = [{"ip": ip, "hits": int(n), "layers": sorted(ls or []), "last": iso(last)}
                       for ip, n, ls, last in cur.fetchall()]
        cur.execute(f"SELECT layer, rule, SUM(hits) FROM firewall_events "  # nosec B608
                    f"WHERE {since} AND {_GENUINE} AND rule IS NOT NULL AND action IN ('block','ban','challenge','count') "
                    "GROUP BY layer, rule ORDER BY SUM(hits) DESC LIMIT 15", (hours, hidden))
        top_rules = [{"layer": layer, "rule": rule, "hits": int(n)} for layer, rule, n in cur.fetchall()]
        cur.execute(f"SELECT "  # nosec B608 -- fixed fragments, bound values
                    "COALESCE(SUM(hits) FILTER (WHERE src_ip::inet <<= ANY(%s::inet[])), 0), "
                    "COALESCE(SUM(hits) FILTER (WHERE src_ip::inet <<= ANY(%s::inet[]) "
                    "  AND NOT (src_ip::inet <<= ANY(%s::inet[]))), 0) "
                    f"FROM firewall_events WHERE {since} AND src_ip IS NOT NULL",
                    (internal, test, internal, hours))
        excl_internal, excl_test = cur.fetchone()
        cur.execute("SELECT source, first_seen, last_seen, last_ip, events_total, status_json, "
                    "last_seen < NOW() - make_interval(mins => %s) FROM firewall_sources ORDER BY source",
                    (reporter_stale_minutes(),))
        reporters = []
        for source, first, last, last_ip, total, status_json, stale in cur.fetchall():
            try:
                status = json.loads(status_json) if status_json else None
            except ValueError:
                status = None
            reporters.append({"source": source, "first_seen": iso(first), "last_seen": iso(last), "last_ip": last_ip,
                              "events_total": int(total), "stale": bool(stale),
                              "status": status if isinstance(status, dict) else None})
    return {
        "hours": hours,
        "layers": [{"layer": name, **counts} for name, counts in layers.items()],
        "timeline": timeline,
        "top_sources": top_sources,
        "top_rules": top_rules,
        "reporters": reporters,
        "excluded": {"internal": int(excl_internal), "test": int(excl_test)},
        "stale_minutes": reporter_stale_minutes(),
    }


def events(hours=24, layer=None, action=None, ip=None, before_id=None, limit=EVENT_LIMIT):
    """The newest decisions, filtered. -> list of dicts (newest first)."""
    from utils.client_ip import parse_ip
    from utils.soc_db import read_cursor
    from utils.soc_util import iso
    internal, test = _hidden_networks()
    where = ["occurred_at > NOW() - make_interval(hours => %s)", _GENUINE]
    params = [hours, internal + test]
    if layer in LAYERS:
        where.append("layer = %s")
        params.append(layer)
    if action in ACTIONS:
        where.append("action = %s")
        params.append(action)
    if ip:
        addr = parse_ip(ip)
        if addr is None:
            return []
        where.append("src_ip = %s")
        params.append(str(addr))
    if before_id:
        where.append("id < %s")
        params.append(int(before_id))
    params.append(int(limit))
    cols = ("id", "occurred_at", "layer", "action", "src_ip", "dst_port", "protocol", "method", "host", "path",
            "rule", "detail", "country", "hits", "source")
    with read_cursor("att_master") as cur:
        cur.execute("SELECT " + ", ".join(cols) + " FROM firewall_events WHERE " + " AND ".join(where)  # nosec B608 -- fixed fragments
                    + " ORDER BY id DESC LIMIT %s", params)
        return [{c: iso(v) for c, v in zip(cols, row)} for row in cur.fetchall()]


# ── retention ────────────────────────────────────────────────────────
def retention_days():
    try:
        return max(1, int(os.environ.get("FIREWALL_EVENT_RETENTION_DAYS", "90")))
    except ValueError:
        return 90


def purge_old_events():
    """Daily: drop decisions older than FIREWALL_EVENT_RETENTION_DAYS (90), in
    batches so one run never holds a long lock. -> rows deleted."""
    from utils.soc_db import write_cursor
    total = 0
    while True:
        with write_cursor("att_master", timeout_ms=30000) as cur:
            cur.execute("DELETE FROM firewall_events WHERE id IN (SELECT id FROM firewall_events "
                        "WHERE occurred_at < NOW() - make_interval(days => %s) LIMIT 5000)", (retention_days(),))
            n = cur.rowcount
        total += n
        if n < 5000:
            return total
