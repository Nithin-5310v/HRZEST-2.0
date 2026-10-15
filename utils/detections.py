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
    reveal_n = _int("DETECT_PII_REVEAL_COUNT", 20)
    travel_km = _int("DETECT_TRAVEL_KM", 200)
    travel_min = _int("DETECT_TRAVEL_MINUTES", 60)
    device_emps = _int("DETECT_SHARED_DEVICE_EMPLOYEES", 3)
    mock_n = _int("DETECT_MOCK_LOCATION_COUNT", 3)
    live_n = _int("DETECT_LIVENESS_FAIL_COUNT", 5)
    waf_n = _int("DETECT_WAF_BURST_COUNT", 50)
    waf_ips = _int("DETECT_WAF_BURST_IPS", 5)

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
