# -*- coding: utf-8 -*-
"""Detection rules over the telemetry the platform already collects.

The app records a lot -- security_events, audit_logs, punch_evidence -- but
until now nothing *correlated* it: a human had to notice that one IP failed
against twelve accounts, or that one phone punched for four employees. This
module is that correlation layer. It is deliberately plain SQL + thresholds:
auditable, tunable by env, and testable with fixture rows.

How it runs
-----------
run_detections() is called on a schedule (wsgi.py, every few minutes, in
every worker). Each rule is a query returning (key, count, detail). A hit is
emitted through log_security_event() at the rule's level -- so it persists to
security_events (visible on the SOC dashboard), is logged as JSON (ships to
Loki), and ERROR-level rules page via the existing webhook/email alerting.
Duplicate suppression is a per-(rule, key) cooldown row claimed with an
atomic upsert, so N gunicorn workers each running the job produce one alert.

Scope: security_events is written by a background thread with no tenant
context, so it lives in the `public` schema and its rules run once.
punch_evidence and audit_logs are per tenant; those rules run per tenant
schema and prefix the key with the tenant.

Source-address caveat: the IP-keyed rules need real client IPs. Behind a
gateway that collapses every visitor to one address they cannot fire (see
utils/client_ip.py) -- the app already logs that condition loudly.
"""
import json
import os
from dataclasses import dataclass
from typing import Callable, Optional


# Audit actions that read or change pay, payslips or bank details
# (blueprints/payroll.py, disbursement, self-service): what payroll_off_hours watches.
PAYROLL_ACTIONS = ("view_salary_list", "view_salary_report", "export_salary_report", "view_payslip",
                   "download_payslip", "download_tax_statement", "update_salary", "apply_hike",
                   "update_bank_details", "save_payout_bank_config", "unlock_payroll")


def _int(name, default):
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


@dataclass
class Rule:
    name: str
    level: str                       # WARNING | ERROR (ERROR pages)
    cooldown_minutes: int
    scope: str                       # "global" (public schema) | "tenant"
    sql: str
    params: Callable[[], tuple]
    describe: Callable[[tuple], tuple]   # row -> (key, human description, extra fields)
    summary: str = ""


# ── rule definitions ────────────────────────────────────────────────────────
def _rules():
    stuff_ids = _int("DETECT_STUFFING_IDENTIFIERS", 5)
    stuff_n = _int("DETECT_STUFFING_ATTEMPTS", 8)
    spray_ips = _int("DETECT_SPRAY_IPS", 5)
    mass_n = _int("DETECT_MASS_EXPORT_COUNT", 5)
    spike_n = _int("DETECT_HTTP_ERROR_SPIKE_COUNT", 100)
    session_nets = _int("DETECT_SESSION_NETWORKS", 4)
    try:
        grace = max(0, int(os.environ.get("DETECT_OFF_HOURS_GRACE_MINUTES", "120")))
    except ValueError:
        grace = 120
    spike_x = _int("DETECT_HTTP_ERROR_SPIKE_FACTOR", 5)
    reveal_n = _int("DETECT_PII_REVEAL_COUNT", 20)
    travel_km = _int("DETECT_TRAVEL_KM", 200)
    travel_min = _int("DETECT_TRAVEL_MINUTES", 60)
    device_emps = _int("DETECT_SHARED_DEVICE_EMPLOYEES", 3)
    mock_n = _int("DETECT_MOCK_LOCATION_COUNT", 3)
    live_n = _int("DETECT_LIVENESS_FAIL_COUNT", 5)
    waf_n = _int("DETECT_WAF_BURST_COUNT", 50)
    waf_ips = _int("DETECT_WAF_BURST_IPS", 5)
    csp_n = _int("DETECT_CSP_BURST_COUNT", 20)
    csp_sources = _int("DETECT_CSP_BURST_SOURCES", 3)

    return [
        Rule(
            "credential_stuffing", "ERROR", 30, "global",
            "SELECT ip, COUNT(DISTINCT identifier), COUNT(*) FROM security_events "
            "WHERE event_type='auth.failure' AND created_at > NOW() - INTERVAL '10 minutes' AND ip IS NOT NULL "
            "GROUP BY ip HAVING COUNT(DISTINCT identifier) >= %s AND COUNT(*) >= %s",
            lambda: (stuff_ids, stuff_n),
            lambda r: (r[0], f"One source failed authentication against {r[1]} different accounts "
                       f"({r[2]} attempts in 10 minutes)", {"ip": r[0], "failed_count": r[2]}),
        ),
        Rule(
            "distributed_bruteforce", "ERROR", 30, "global",
            "SELECT identifier, COUNT(DISTINCT ip), COUNT(*) FROM security_events "
            "WHERE event_type='auth.failure' AND created_at > NOW() - INTERVAL '10 minutes' "
            "AND identifier IS NOT NULL AND ip IS NOT NULL "
            "GROUP BY identifier HAVING COUNT(DISTINCT ip) >= %s",
            lambda: (spray_ips,),
            lambda r: (r[0], f"Account '{r[0]}' is being attacked from {r[1]} different addresses "
                       f"({r[2]} failures in 10 minutes)", {"identifier": r[0], "failed_count": r[2]}),
        ),
        Rule(
            "tenant_boundary_probe", "ERROR", 30, "global",
            "SELECT COALESCE(identifier, ip), COUNT(*) FROM security_events "
            "WHERE event_type='tenant.session_mismatch' AND created_at > NOW() - INTERVAL '15 minutes' "
            "GROUP BY COALESCE(identifier, ip) HAVING COUNT(*) >= 3",
            lambda: (),
            lambda r: (r[0], f"{r[1]} requests presented one tenant's session against another tenant's URL "
                       f"in 15 minutes", {"identifier": r[0]}),
        ),
        Rule(
            "privilege_probe", "ERROR", 30, "global",
            "SELECT identifier, COUNT(*) FROM security_events "
            "WHERE event_type='access.denied' AND level='ERROR' AND identifier IS NOT NULL "
            "AND created_at > NOW() - INTERVAL '15 minutes' GROUP BY identifier HAVING COUNT(*) >= 5",
            lambda: (),
            lambda r: (r[0], f"'{r[0]}' hit {r[1]} role-restricted routes without the required role "
                       f"in 15 minutes", {"identifier": r[0]}),
        ),
        Rule(
            "recon_then_probe", "ERROR", 60, "global",
            "SELECT COALESCE(h.identifier, h.ip) AS src, COUNT(*) FROM security_events h "
            "JOIN security_events e ON e.ip = COALESCE(h.identifier, h.ip) AND e.created_at >= h.created_at "
            "WHERE h.event_type IN ('honeypot.tcp_decoy_hit','secops.honeypot_triggered') "
            "AND h.created_at > NOW() - INTERVAL '60 minutes' "
            "AND (e.event_type LIKE 'waf.%%' OR e.event_type IN ('auth.failure','access.denied','auth.injection_attempt')) "
            "GROUP BY COALESCE(h.identifier, h.ip)",
            lambda: (),
            lambda r: (r[0], f"{r[0]} touched a honeypot and then probed the application "
                       f"({r[1]} follow-up events)", {"ip": r[0]}),
        ),
        Rule(
            "pii_reveal_burst", "WARNING", 60, "global",
            "SELECT identifier, COUNT(*) FROM security_events "
            "WHERE event_type='data.reveal' AND identifier IS NOT NULL "
            "AND created_at > NOW() - INTERVAL '10 minutes' GROUP BY identifier HAVING COUNT(*) >= %s",
            lambda: (reveal_n,),
            lambda r: (r[0], f"'{r[0]}' revealed masked personal data {r[1]} times in 10 minutes",
                       {"identifier": r[0]}),
        ),
        Rule(
            "waf_burst_distributed", "ERROR", 30, "global",
            "SELECT 'waf', COUNT(*), COUNT(DISTINCT ip) FROM security_events "
            "WHERE event_type LIKE 'waf.%%_blocked' AND created_at > NOW() - INTERVAL '5 minutes' "
            "HAVING COUNT(*) >= %s AND COUNT(DISTINCT ip) >= %s",
            lambda: (waf_n, waf_ips),
            lambda r: ("waf", f"{r[1]} WAF blocks from {r[2]} addresses in 5 minutes (coordinated scan)", {}),
        ),
        # Many browsers reporting CSP violations on the same page at once is how
        # an injected script or a hijacked third-party resource first shows up
        # (the CSP blocks it, every visitor's browser reports it). The page is
        # pulled from extra_json with a regex, not a JSON cast: the column is
        # truncated at 4000 characters, and one malformed row must not break
        # the whole detection run. Two kinds of noise are kept out: violations
        # caused by browser extensions, and a single visitor's browser
        # repeating itself (several distinct sources are required).
        Rule(
            "csp_violation_burst", "ERROR", 60, "global",
            "SELECT page, COUNT(*), COUNT(DISTINCT src) FROM ("
            "  SELECT substring(extra_json from '\"page\": \"((?:[^\"\\\\]|\\\\.)*)\"') AS page, "
            "         COALESCE(ip, identifier) AS src FROM security_events "
            "  WHERE event_type='frontend.csp_violation' AND created_at > NOW() - INTERVAL '10 minutes' "
            "  AND COALESCE(extra_json, '') !~ '\"blocked_uri\": \"(chrome|moz|safari-web|ms-browser)-extension:') v "
            "WHERE page IS NOT NULL GROUP BY page HAVING COUNT(*) >= %s AND COUNT(DISTINCT src) >= %s",
            lambda: (csp_n, csp_sources),
            lambda r: (r[0], f"{r[1]} Content-Security-Policy violations on {r[0]} from {r[2]} different "
                       f"sources in 10 minutes (possible script injection)", {"path": r[0]}),
        ),
        # Request telemetry (utils/http_telemetry.py): one status code (or all 5xx)
        # answered far more often in the last 10 minutes than in an average 10
        # minutes of the day before, and at least DETECT_HTTP_ERROR_SPIKE_COUNT times.
        Rule(
            "http_error_spike", "WARNING", 60, "global",
            "WITH coded AS (SELECT CASE WHEN status >= 500 THEN '5xx' ELSE status::text END AS code, minute, hits "
            "  FROM att_master.http_minute_stats WHERE (status IN (401, 403, 404, 429) OR status >= 500) "
            "  AND minute >= NOW() - INTERVAL '24 hours 10 minutes'), "
            "recent AS (SELECT code, SUM(hits) AS n FROM coded WHERE minute >= NOW() - INTERVAL '10 minutes' GROUP BY code), "
            "usual AS (SELECT code, SUM(hits) / 144.0 AS per10 FROM coded "
            "  WHERE minute < NOW() - INTERVAL '10 minutes' GROUP BY code) "
            "SELECT r.code, r.n, COALESCE(u.per10, 0) FROM recent r LEFT JOIN usual u ON u.code = r.code "
            "WHERE r.n >= %s AND r.n >= %s * GREATEST(COALESCE(u.per10, 0), 1) ORDER BY r.n DESC",
            lambda: (spike_n, spike_x),
            lambda r: (f"http_{r[0]}", f"{int(r[1])} HTTP {r[0]} responses in 10 minutes "
                       f"(usually about {round(float(r[2]))})", {"reason": f"http_{r[0]}", "count": int(r[1])}),
        ),
        # utils/session_context.py: a signed-in session used from another browser
        # family (a copied session cookie; DevTools device emulation is the usual
        # harmless cause) ...
        Rule(
            "session_browser_switch", "WARNING", 60, "global",
            "SELECT identifier, COUNT(*), MAX(ip) FROM security_events "
            "WHERE event_type = 'session.browser_changed' AND identifier IS NOT NULL "
            "AND created_at > NOW() - INTERVAL '10 minutes' GROUP BY identifier",
            lambda: (),
            lambda r: (r[0], f"A session of '{r[0]}' switched to another browser ({r[1]} change(s) in 10 minutes, "
                       f"latest from {r[2]}): a copied session cookie, or DevTools device emulation",
                       {"identifier": r[0], "ip": r[2]}),
        ),
        # ... or one account's session hopping across several networks.
        Rule(
            "session_network_hopping", "WARNING", 60, "global",
            "SELECT identifier, COUNT(DISTINCT ip) FROM security_events "
            "WHERE event_type = 'session.network_changed' AND identifier IS NOT NULL "
            "AND created_at > NOW() - INTERVAL '30 minutes' GROUP BY identifier HAVING COUNT(DISTINCT ip) >= %s",
            lambda: (session_nets,),
            lambda r: (r[0], f"A session of '{r[0]}' moved across {r[1]} networks in 30 minutes",
                       {"identifier": r[0], "count": int(r[1])}),
        ),
        # ── tenant-scoped ──
        Rule(
            "mass_export", "WARNING", 60, "tenant",
            "SELECT actor, COUNT(*) FROM audit_logs "
            "WHERE (action ILIKE '%%export%%' OR action ILIKE '%%download%%') "
            "AND created_at > NOW() - INTERVAL '10 minutes' GROUP BY actor HAVING COUNT(*) >= %s",
            lambda: (mass_n,),
            lambda r: (r[0], f"'{r[0]}' performed {r[1]} export/download actions in 10 minutes",
                       {"identifier": r[0]}),
        ),
        # Pay, payslip or bank data used outside the company's working hours
        # (company_settings: shift_start / shift_end in its time zone, working
        # days, holidays), with DETECT_OFF_HOURS_GRACE_MINUTES either side.
        # Night shifts (end before start) wrap past midnight. One alert per
        # actor per hour, listing what was done.
        Rule(
            "payroll_off_hours", "WARNING", 60, "tenant",
            "WITH cfg AS (SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = c.timezone) "
            "    THEN c.timezone ELSE 'Asia/Kolkata' END AS tz, "
            "  EXTRACT(HOUR FROM COALESCE(c.shift_start, TIME '09:00')) * 60 "
            "    + EXTRACT(MINUTE FROM COALESCE(c.shift_start, TIME '09:00')) AS s, "
            "  EXTRACT(HOUR FROM COALESCE(c.shift_end, TIME '18:00')) * 60 "
            "    + EXTRACT(MINUTE FROM COALESCE(c.shift_end, TIME '18:00')) AS e, "
            "  lower(COALESCE(NULLIF(c.working_days, ''), 'Mon,Tue,Wed,Thu,Fri')) AS days "
            "  FROM (SELECT 1) one LEFT JOIN (SELECT * FROM company_settings ORDER BY id LIMIT 1) c ON TRUE), "
            "acc AS (SELECT a.actor, a.action, "
            "  (a.created_at AT TIME ZONE current_setting('TimeZone')) AT TIME ZONE cfg.tz AS local, cfg.s, cfg.e, cfg.days "
            "  FROM audit_logs a CROSS JOIN cfg WHERE a.created_at > NOW() - INTERVAL '10 minutes' "
            "  AND a.action = ANY(%s) AND COALESCE(a.actor_type, '') <> 'system'), "
            "off AS (SELECT *, EXTRACT(HOUR FROM local) * 60 + EXTRACT(MINUTE FROM local) AS m FROM acc) "
            "SELECT actor, COUNT(*), string_agg(DISTINCT action, ', '), to_char(MIN(local), 'Dy DD Mon HH24:MI'), "
            "to_char(MAX(local), 'HH24:MI') FROM off "
            "WHERE position(lower(to_char(local, 'Dy')) IN days) = 0 "
            "OR local::date IN (SELECT date FROM holidays) "
            "OR (CASE WHEN s <= e THEN m < s - %s OR m > e + %s ELSE m < s - %s AND m > e + %s END) "
            "GROUP BY actor",
            lambda: (list(PAYROLL_ACTIONS), grace, grace, grace, grace),
            lambda r: (r[0], f"'{r[0]}' used payroll or bank data {r[1]} time(s) outside working hours "
                       f"({r[3]} to {r[4]}, company time): {r[2]}", {"identifier": r[0], "reason": r[2]}),
        ),
        Rule(
            "sensitive_admin_change", "WARNING", 5, "tenant",
            "SELECT id::text, actor, action FROM audit_logs "
            "WHERE created_at > NOW() - INTERVAL '10 minutes' "
            "AND action IN ('update_employee_role','reassign_employee_hr','admin_reset_fingerprint',"
            "'employee_password_reset','update_bank_details','save_payout_bank_config','delete_employee',"
            "'privacy.erase','unlock_payroll')",
            lambda: (),
            lambda r: (r[0], f"Sensitive change '{r[2]}' by '{r[1]}'", {"identifier": r[1], "reason": r[2]}),
        ),
        Rule(
            "impossible_travel", "ERROR", 12 * 60, "tenant",
            "SELECT a.employee_id, ROUND(d.km::numeric), a.id, b.id FROM punch_evidence a "
            "JOIN punch_evidence b ON b.employee_id = a.employee_id AND b.id > a.id "
            "AND b.created_at BETWEEN a.created_at AND a.created_at + (%s * INTERVAL '1 minute') "
            "CROSS JOIN LATERAL (SELECT 6371 * 2 * ASIN(SQRT("
            "  POWER(SIN(RADIANS(b.lat - a.lat) / 2), 2) + COS(RADIANS(a.lat)) * COS(RADIANS(b.lat)) * "
            "  POWER(SIN(RADIANS(b.lon - a.lon) / 2), 2))) AS km) d "
            "WHERE a.outcome='accepted' AND b.outcome='accepted' AND a.lat IS NOT NULL AND b.lat IS NOT NULL "
            "AND a.lon IS NOT NULL AND b.lon IS NOT NULL AND a.created_at > NOW() - INTERVAL '2 hours' "
            "AND d.km >= %s",
            lambda: (travel_min, travel_km),
            lambda r: (r[0], f"Employee {r[0]} punched {int(r[1])} km apart within {travel_min} minutes "
                       f"(evidence rows {r[2]} and {r[3]})", {"identifier": r[0]}),
        ),
        Rule(
            "shared_device", "ERROR", 24 * 60, "tenant",
            "SELECT device_id, COUNT(DISTINCT employee_id) FROM punch_evidence "
            "WHERE device_id IS NOT NULL AND outcome='accepted' AND created_at > NOW() - INTERVAL '24 hours' "
            "GROUP BY device_id HAVING COUNT(DISTINCT employee_id) >= %s",
            lambda: (device_emps,),
            lambda r: (r[0], f"One device punched for {r[1]} different employees in 24 hours "
                       f"(buddy punching)", {"identifier": r[0]}),
        ),
        Rule(
            "repeat_mock_location", "ERROR", 6 * 60, "tenant",
            "SELECT employee_id, COUNT(*) FROM punch_evidence "
            "WHERE flags LIKE '%%mock_location%%' AND created_at > NOW() - INTERVAL '24 hours' "
            "GROUP BY employee_id HAVING COUNT(*) >= %s",
            lambda: (mock_n,),
            lambda r: (r[0], f"Employee {r[0]} reported a mocked location {r[1]} times in 24 hours",
                       {"identifier": r[0]}),
        ),
        Rule(
            "repeat_liveness_failure", "ERROR", 60, "tenant",
            "SELECT employee_id, COUNT(*) FROM punch_evidence "
            "WHERE liveness IN ('failed','challenge_invalid') AND created_at > NOW() - INTERVAL '30 minutes' "
            "GROUP BY employee_id HAVING COUNT(*) >= %s",
            lambda: (live_n,),
            lambda r: (r[0], f"{r[1]} failed liveness checks for employee {r[0]} in 30 minutes "
                       f"(spoofing attempt?)", {"identifier": r[0]}),
        ),
        Rule(
            "photo_replay", "ERROR", 6 * 60, "tenant",
            "SELECT employee_id, COUNT(*) FROM punch_evidence "
            "WHERE flags LIKE '%%photo_exact_replay%%' AND created_at > NOW() - INTERVAL '24 hours' "
            "GROUP BY employee_id HAVING COUNT(*) >= 2",
            lambda: (),
            lambda r: (r[0], f"Employee {r[0]} resubmitted a previously used check-in photo {r[1]} times",
                       {"identifier": r[0]}),
        ),
    ]


RULES = _rules()


# ── engine ──────────────────────────────────────────────────────────────────
def _claim(cursor, rule_name, key, cooldown_minutes):
    """True if this caller should emit: either first time, or the cooldown
    has elapsed. One atomic statement, so concurrent workers can't both win."""
    cursor.execute(
        "INSERT INTO detection_state (rule, key, last_fired, fired) VALUES (%s,%s,NOW(),1) "
        "ON CONFLICT (rule, key) DO UPDATE SET last_fired = NOW(), fired = detection_state.fired + 1 "
        "WHERE detection_state.last_fired < NOW() - (%s * INTERVAL '1 minute') "
        "RETURNING 1",
        (rule_name, str(key)[:200], cooldown_minutes))
    return cursor.fetchone() is not None


def evaluate(cursor, rule: Rule, scope_label: Optional[str] = None, emit=None):
    """-> list of findings fired for this rule (after cooldown). `emit` is
    log_security_event-compatible; defaults to the real one."""
    if emit is None:
        from extensions import log_security_event as emit
    cursor.execute(rule.sql, rule.params())
    fired = []
    for row in cursor.fetchall():
        key, description, fields = rule.describe(row)
        full_key = f"{scope_label}:{key}" if scope_label else str(key)
        if not _claim(cursor, rule.name, full_key, rule.cooldown_minutes):
            continue
        extra = dict(fields)
        if scope_label:
            extra["pattern"] = f"tenant={scope_label}"
        # `identifier` is the pivot the SOC dashboard shows; keep whatever the
        # rule supplied, falling back to the correlation key.
        extra.setdefault("identifier", str(key))
        emit(f"detection.{rule.name}", description, level=rule.level, **extra)
        fired.append({"rule": rule.name, "key": full_key, "description": description})
    return fired


def run_global(cursor, emit=None):
    out = []
    for rule in RULES:
        if rule.scope == "global":
            out.extend(evaluate(cursor, rule, None, emit))
    return out


def run_tenant(cursor, tenant_label, emit=None):
    out = []
    for rule in RULES:
        if rule.scope == "tenant":
            out.extend(evaluate(cursor, rule, tenant_label, emit))
    return out


def run_detections(emit=None):
    """Entry point for the scheduler. Never raises: a broken rule or schema
    must not be able to take the scheduler thread down, and one tenant's
    failure must not hide detections in the others."""
    from extensions import app_log
    from database import get_db_connection, get_master_db, get_tenant_db
    findings = []

    def _guard(label, fn):
        try:
            return fn()
        except Exception:
            app_log.warning("detection run failed for %s", label, exc_info=True)
            return []

    def _public():
        db = get_db_connection()
        cur = db.cursor()
        try:
            return run_global(cur, emit) + run_tenant(cur, "public", emit)
        finally:
            cur.close()
            db.close()

    findings.extend(_guard("public", _public))

    try:
        mdb = get_master_db()
        mcur = mdb.cursor()
        mcur.execute("SELECT db_name FROM tenants WHERE COALESCE(status,'active') <> 'deleted'")
        schemas = [r[0] for r in mcur.fetchall()]
        mcur.close()
        mdb.close()
    except Exception:
        app_log.warning("detections: could not list tenants", exc_info=True)
        schemas = []

    for schema in schemas:
        def _one(schema=schema):
            tdb = get_tenant_db(schema)
            tcur = tdb.cursor()
            try:
                return run_tenant(tcur, schema, emit)
            finally:
                tcur.close()
                tdb.close()
        findings.extend(_guard(schema, _one))
    return findings


def dumps(findings):
    return json.dumps(findings, indent=2, default=str)
