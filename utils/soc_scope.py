# -*- coding: utf-8 -*-
"""Which recorded events the SOC console shows: genuine activity only.

Every data view -- Overview, the top-bar threat level, Alerts / Events and their
CSV export, Lookup, Analyst brief, Detections, Tenant risk, Attendance fraud,
Response sessions, Ban review and Log analysis of platform activity -- reads
security_events through `events_cte()`, so these are left out everywhere at once:

  * the server's own traffic: events whose address is loopback, private, CGNAT
    or link-local (utils.client_ip.is_internal_ip) -- the app itself, its proxy
    or container gateway, a LAN. They are not visitors.
  * the console's own use: analysts signing in and acting (soc.login_success,
    soc.login_code_sent, soc.action), and any soc.* or /socperson event from an
    address that has signed in to the console. Strangers probing /socperson are
    kept: that is an attack on the console. The console's record of its own
    use is Users & audit (att_master.soc_audit_log).
  * your own testing, as listed in the environment:
      SOC_TEST_CIDRS      addresses / ranges you test from (office, VPN, CI)
      SOC_TEST_ACCOUNTS   identifiers you test with (usernames, emails, IDs)
      SOC_TEST_COMPANIES  company URL slugs used only for testing

Not filtered: the audit-chain verification (it must see every row), bans and
cases (enforcement and analyst decisions, not activity), and the Loki view
(raw shipped logs).
"""
import ipaddress
import os

from flask import g, has_request_context

from utils.client_ip import is_internal_ip, parse_ip

# Analysts using the console -- never platform activity, whoever sends them.
OWN_EVENT_TYPES = ("soc.login_success", "soc.login_code_sent", "soc.action")
CONSOLE_PATH = "/socperson"


def _per_request(key, producer):
    """Computed at most once per request and never reused by a later one: a list
    kept across requests would let an address first seen since then slip through
    (the lookups are indexed: security_events (ip, created_at), (event_type))."""
    if not has_request_context():
        return producer()
    memo = g.setdefault("_soc_scope", {})
    if key not in memo:
        memo[key] = producer()
    return memo[key]


# ── your own testing (environment) ───────────────────────────────────
def _env_list(name):
    return [p.strip().lower() for p in os.environ.get(name, "").split(",") if p.strip()]


def test_networks():
    nets = []
    for part in _env_list("SOC_TEST_CIDRS"):
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets


def test_accounts():
    return sorted(set(_env_list("SOC_TEST_ACCOUNTS")))


def test_companies():
    return sorted(set(_env_list("SOC_TEST_COMPANIES")))


def is_test_company(slug):
    return bool(slug) and slug.strip().lower() in test_companies()


def is_test_account(identifier):
    return bool(identifier) and identifier.strip().lower() in test_accounts()


def ip_reason(ip):
    """-> 'internal' | 'test' | None: why an address is left out, if it is."""
    if not ip:
        return None
    if is_internal_ip(ip):
        return "internal"
    addr = parse_ip(ip)
    if addr is not None and any(addr.version == n.version and addr in n for n in test_networks()):
        return "test"
    return None


EXCLUDED_NOTES = {
    "internal": "This is one of the server's own addresses (loopback, private network or proxy), so the console "
                "leaves its events out: they are not visitors.",
    "test": "This address is listed in SOC_TEST_CIDRS as your own testing, so the console leaves its events out.",
    "test_account": "This account is listed in SOC_TEST_ACCOUNTS as your own testing, so the console leaves its "
                    "events out.",
}


# ── the addresses to leave out ───────────────────────────────────────
def _hidden_ips(cur, hours):
    """-> (internal, test): addresses seen in the window that are left out.
    Classified in Python by the same rules as the rest of the app, so a value the
    database could not cast to inet can never break a query."""
    def build():
        cur.execute("SELECT DISTINCT ip FROM security_events WHERE ip IS NOT NULL "
                    "AND created_at >= NOW() - (%s * INTERVAL '1 hour')", (hours,))
        internal, test = [], []
        for (ip,) in cur.fetchall():
            reason = ip_reason(ip)
            if reason == "internal":
                internal.append(ip)
            elif reason == "test":
                test.append(ip)
        return internal, test
    return _per_request(("hidden_ips", int(hours), os.environ.get("SOC_TEST_CIDRS", "")), build)


def console_ips():
    """Addresses that have signed in to or acted in the console: its own audit
    trail, plus the matching security events of the last 90 days (the longest
    look-back of any view), so either record alone is enough."""
    def build():
        from utils.soc_db import read_cursor
        with read_cursor("att_master") as cur:
            cur.execute("SELECT DISTINCT ip FROM soc_audit_log WHERE action='login' AND ip IS NOT NULL")
            ips = {ip for (ip,) in cur.fetchall()}
        with read_cursor("public") as cur:
            cur.execute("SELECT DISTINCT ip FROM security_events WHERE event_type = ANY(%s) AND ip IS NOT NULL "
                        "AND created_at >= NOW() - INTERVAL '90 days'", (list(OWN_EVENT_TYPES),))
            ips |= {ip for (ip,) in cur.fetchall()}
        return sorted(ips)
    return _per_request(("console_ips",), build)


def _console_event(event_type, path):
    path = path or ""
    return (event_type or "").startswith("soc.") or path == CONSOLE_PATH or path.startswith(CONSOLE_PATH + "/")


# ── the filter ───────────────────────────────────────────────────────
_GENUINE_SQL = (
    "COALESCE(event_type, '') <> ALL(%s) "
    "AND NOT (COALESCE(ip, '') = ANY(%s) AND (COALESCE(event_type, '') LIKE 'soc.%%' "
    "OR COALESCE(path, '') = '" + CONSOLE_PATH + "' OR COALESCE(path, '') LIKE '" + CONSOLE_PATH + "/%%')) "
    "AND NOT (COALESCE(ip, '') = ANY(%s)) "
    "AND lower(COALESCE(identifier, '')) <> ALL(%s) "
    "AND lower(split_part(COALESCE(path, ''), '/', 2)) <> ALL(%s)"
)


def _genuine_params(cur, hours):
    internal, test = _hidden_ips(cur, hours)
    return [list(OWN_EVENT_TYPES), console_ips(), internal + test, test_accounts(), test_companies()]


def events_cte(cur, hours):
    """-> (sql, params). Prefix a query with `sql` and read FROM ev instead of
    security_events; `params` go before the query's own. `hours` is the widest
    look-back the query uses (only addresses seen in it need classifying)."""
    return ("WITH ev AS NOT MATERIALIZED (SELECT * FROM security_events WHERE " + _GENUINE_SQL + ") ",
            _genuine_params(cur, hours))


def excluded_summary(cur, hours):
    """How many events in the window each rule leaves out, for the Overview note."""
    internal, test = _hidden_ips(cur, hours)
    consoles = console_ips()
    cur.execute(
        "SELECT COUNT(*), "
        "COUNT(*) FILTER (WHERE COALESCE(ip, '') = ANY(%s)), "
        "COUNT(*) FILTER (WHERE COALESCE(event_type, '') = ANY(%s) OR (COALESCE(ip, '') = ANY(%s) AND "
        "(COALESCE(event_type, '') LIKE 'soc.%%' OR COALESCE(path, '') = '" + CONSOLE_PATH + "' "
        "OR COALESCE(path, '') LIKE '" + CONSOLE_PATH + "/%%'))), "
        "COUNT(*) FILTER (WHERE COALESCE(ip, '') = ANY(%s) OR lower(COALESCE(identifier, '')) = ANY(%s) "
        "OR lower(split_part(COALESCE(path, ''), '/', 2)) = ANY(%s)) "
        "FROM security_events WHERE created_at >= NOW() - (%s * INTERVAL '1 hour')",
        (internal, list(OWN_EVENT_TYPES), consoles, test, test_accounts(), test_companies(), hours))
    raw, server, console, testing = cur.fetchone()
    return {"recorded": raw, "server": server, "console": console, "testing": testing}


def is_genuine(event_type, ip, identifier, path):
    """The same rules for one row (lists filtered in Python)."""
    if event_type in OWN_EVENT_TYPES:
        return False
    if ip and ip in console_ips() and _console_event(event_type, path):
        return False
    if ip_reason(ip):
        return False
    if is_test_account(identifier):
        return False
    parts = (path or "").split("/")                 # same as split_part(path, '/', 2) in SQL
    return not is_test_company(parts[1] if len(parts) > 1 else "")
