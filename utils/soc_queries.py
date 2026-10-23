# -*- coding: utf-8 -*-
"""Read-side queries for the SOC console.

Data sources (all already produced by the product -- nothing here invents data):

  public.security_events   every log_security_event(): auth, WAF, honeypot, detections, ...
  public.banned_ips        platform-wide IP bans
  public.detection_state   when each correlation rule last fired
  att_master.tenants       company registry
  att_master.soc_*         the console's own cases / users / audit trail
  <tenant>.punch_evidence  attendance-integrity evidence      (per tenant)
  <tenant>.session_risk    sessions flagged by the risk engine (per tenant)

All SQL is parameterised; analyst-supplied text only ever reaches a query as a
bound parameter (LIKE metacharacters escaped). Every call runs in a read-only
transaction with a statement timeout (utils/soc_db.py). Cross-tenant views
(sessions, attendance integrity) iterate tenants one at a time with a short
timeout and a small in-process cache so one analyst's refresh loop cannot turn
into a per-tenant query storm.
"""
import json
import os
import threading
import time

from utils import soc_taxonomy as tax
from utils.soc_db import each_tenant, list_tenants, read_cursor
from utils.soc_util import clamp_int, iso, like_escape, looks_like_ip

EV_COLS = "id, event_type, level, message, identifier, ip, path, method, extra_json, created_at"
_WINDOW = "created_at >= NOW() - (%s * INTERVAL '1 hour')"
ALERT_SQL = "(event_type LIKE 'detection.%%' OR level IN ('ERROR','CRITICAL'))"


def hours_arg(value, default=24):
    return clamp_int(value, default, 1, 720)


def _extra(raw, limit=4000):
    if not raw:
        return {}
    try:
        obj = json.loads(raw[:limit])
        return obj if isinstance(obj, dict) else {"value": obj}
    except ValueError:
        return {"raw": raw[:500]}


def shape_event(row, detail=False):
    eid, et, level, msg, ident, ip, path, method, extra, ts = row
    cls = tax.classify(et, level)
    d = {"id": eid, "event_type": et, "level": level, "message": msg, "identifier": ident, "ip": ip,
         "path": path, "method": method, "created_at": iso(ts), "family": cls["family"], "label": cls["label"],
         "mitre": [m["id"] for m in cls["mitre"]], "severity": tax.severity_of(et, level),
         "alert": tax.is_alert(et, level), "tenant": tax.tenant_hint(path)}
    if detail:
        d["extra"] = _extra(extra)
        d["mitre_detail"] = cls["mitre"]
        d["family_label"] = cls["family_label"]
    return d


def _banned_set(cur, ips):
    ips = [i for i in set(ips) if i]
    if not ips:
        return set()
    cur.execute("SELECT ip FROM public.banned_ips WHERE ip = ANY(%s) AND (expires_at IS NULL OR expires_at > NOW())", (ips,))
    return {r[0] for r in cur.fetchall()}


def _protected(ip):
    try:
        from utils.client_ip import is_protected_ip
        return bool(is_protected_ip(ip))
    except Exception:
        return False


# ── overview ─────────────────────────────────────────────────────────
def threat_level(last_hour):
    """Transparent, rule-based -- the reasons are returned with the level so an
    analyst can see exactly why the console says 'High'."""
    crit, err, warn = last_hour["CRITICAL"], last_hour["ERROR"], last_hour["WARNING"]
    reasons = []
    if crit:
        reasons.append(f"{crit} CRITICAL event(s) in the last hour")
        level = "critical"
    elif err >= 3:
        reasons.append(f"{err} ERROR events in the last hour (3+ = high)")
        level = "high"
    elif err >= 1 or warn >= 25:
        if err:
            reasons.append(f"{err} ERROR event(s) in the last hour")
        if warn >= 25:
            reasons.append(f"{warn} WARNING events in the last hour (25+ = elevated)")
        level = "elevated"
    else:
        reasons.append("No ERROR/CRITICAL events in the last hour")
        level = "normal"
    return {"level": level, "reasons": reasons}


def overview(hours=24):
    hours = hours_arg(hours)
    bucket = "5 minutes" if hours <= 3 else "1 hour" if hours <= 48 else "1 day"
    out = {"hours": hours, "bucket": bucket}
    with read_cursor("public") as cur:
        cur.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE level='CRITICAL'), COUNT(*) FILTER (WHERE level='ERROR'), "
            "COUNT(*) FILTER (WHERE level='WARNING'), COUNT(*) FILTER (WHERE event_type LIKE 'detection.%%'), "
            "COUNT(*) FILTER (WHERE event_type IN ('auth.failure','auth.platform_admin_login_failed','auth.mfa_failure')), "
            "COUNT(*) FILTER (WHERE event_type LIKE 'waf.%%' AND event_type <> 'waf.auto_ban'), "
            "COUNT(*) FILTER (WHERE event_type IN ('secops.honeypot_triggered','honeypot.tcp_decoy_hit')), "
            "COUNT(*) FILTER (WHERE event_type='auth.lockout'), "
            "COUNT(DISTINCT ip) FILTER (WHERE ip IS NOT NULL), MAX(created_at) "
            "FROM security_events WHERE " + _WINDOW, (hours,))
        (total, crit, err, warn, det, authfail, waf, honey, lock, ips, last_ts) = cur.fetchone()
        out["kpi"] = {"events": total, "critical": crit, "error": err, "warning": warn, "detections": det,
                      "failed_logins": authfail, "waf_blocks": waf, "honeypot_hits": honey, "lockouts": lock,
                      "unique_ips": ips, "last_event": iso(last_ts)}
        cur.execute("SELECT level, COUNT(*) FROM security_events WHERE created_at >= NOW() - INTERVAL '1 hour' GROUP BY level")
        lh = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0}
        lh.update({lvl: c for lvl, c in cur.fetchall() if lvl in lh})
        out["last_hour"] = lh
        out["threat"] = threat_level(lh)

        cur.execute(
            "SELECT date_bin(%s::interval, created_at, TIMESTAMP '2001-01-01'), level, COUNT(*) "
            "FROM security_events WHERE " + _WINDOW + " GROUP BY 1, 2 ORDER BY 1", (bucket, hours))
        pts = {}
        for t, lvl, c in cur.fetchall():
            p = pts.setdefault(iso(t), {"t": iso(t), "CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0})
            p[lvl if lvl in p else "INFO"] += c
        out["timeline"] = list(pts.values())

        cur.execute("SELECT event_type, COUNT(*) FROM security_events WHERE " + _WINDOW + " GROUP BY event_type", (hours,))
        fam, types = {}, []
        for et, c in cur.fetchall():
            f = tax.family_of(et)
            fam[f] = fam.get(f, 0) + c
            types.append((et, c))
        out["families"] = sorted(({"family": f, "label": tax.FAMILIES[f], "count": c} for f, c in fam.items()),
                                 key=lambda x: -x["count"])
        out["top_event_types"] = [{"event_type": et, "label": tax.classify(et)["label"], "count": c}
                                  for et, c in sorted(types, key=lambda x: -x[1])[:10]]
        tech = {}
        for et, c in types:
            for m in tax.classify(et)["mitre"]:
                tech[m["id"]] = tech.get(m["id"], {"id": m["id"], "name": m["name"], "url": m["url"], "count": 0})
                tech[m["id"]]["count"] += c
        out["techniques"] = sorted(tech.values(), key=lambda x: -x["count"])[:12]

        cur.execute(
            "SELECT ip, COUNT(*), COUNT(DISTINCT event_type), MAX(created_at), "
            "COUNT(*) FILTER (WHERE level IN ('ERROR','CRITICAL')) FROM security_events "
            "WHERE " + _WINDOW + " AND ip IS NOT NULL GROUP BY ip ORDER BY 5 DESC, 2 DESC LIMIT 10", (hours,))
        rows = cur.fetchall()
        banned = _banned_set(cur, [r[0] for r in rows])
        out["top_ips"] = [{"ip": ip, "events": c, "types": t, "last": iso(ts), "high": hi,
                           "banned": ip in banned, "protected": _protected(ip)} for ip, c, t, ts, hi in rows]
        cur.execute(
            "SELECT identifier, COUNT(*), COUNT(DISTINCT ip), MAX(created_at) FROM security_events "
            "WHERE " + _WINDOW + " AND identifier IS NOT NULL AND identifier NOT IN ('', 'None') "
            "GROUP BY identifier ORDER BY 2 DESC LIMIT 10", (hours,))
        out["top_identifiers"] = [{"identifier": i, "events": c, "ips": n, "last": iso(ts)} for i, c, n, ts in cur.fetchall()]
        cur.execute("SELECT " + EV_COLS + " FROM security_events WHERE " + _WINDOW + " AND " + ALERT_SQL +
                    " ORDER BY id DESC LIMIT 12", (hours,))
        out["recent_alerts"] = [shape_event(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM public.banned_ips WHERE expires_at IS NULL OR expires_at > NOW()")
        out["active_bans"] = cur.fetchone()[0]
    with read_cursor("att_master") as cur:
        cur.execute("SELECT status, COUNT(*) FROM soc_cases GROUP BY status")
        cases = dict(cur.fetchall())
    out["cases"] = {"open": cases.get("open", 0) + cases.get("investigating", 0) + cases.get("contained", 0),
                    "by_status": cases}
    return out


def pulse():
    """Cheap status for the top bar and nav badges (two small queries)."""
    with read_cursor("public") as cur:
        cur.execute("SELECT NOW()")
        now = cur.fetchone()[0]
        cur.execute("SELECT level, COUNT(*) FROM security_events WHERE created_at >= NOW() - INTERVAL '1 hour' GROUP BY level")
        lh = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0}
        lh.update({lvl: c for lvl, c in cur.fetchall() if lvl in lh})
        cur.execute("SELECT COUNT(*) FROM security_events WHERE created_at >= NOW() - INTERVAL '24 hours' AND " + ALERT_SQL)
        alerts_24h = cur.fetchone()[0]
    with read_cursor("att_master") as cur:
        cur.execute("SELECT COUNT(*) FROM soc_cases WHERE status IN ('open','investigating','contained')")
        open_cases = cur.fetchone()[0]
    return {"now": iso(now), "threat": threat_level(lh), "last_hour": lh, "alerts_24h": alerts_24h, "open_cases": open_cases}


# ── events ───────────────────────────────────────────────────────────
def search_events(*, q=None, levels=None, family=None, event_type=None, ip=None, identifier=None, tenant=None,
                  alerts_only=False, hours=24, cursor=None, limit=100):
    hours = hours_arg(hours)
    limit = clamp_int(limit, 100, 1, 200)
    where, params = [_WINDOW], [hours]
    with read_cursor("public") as cur:
        if family:
            cur.execute("SELECT DISTINCT event_type FROM security_events WHERE " + _WINDOW, (hours,))
            types = [t for (t,) in cur.fetchall() if tax.family_of(t) == family]
            if not types:
                return {"events": [], "next_cursor": None, "hours": hours}
            where.append("event_type = ANY(%s)")
            params.append(types)
        if levels:
            lv = [lvl.upper() for lvl in levels if lvl.upper() in tax.LEVEL_RANK]
            if lv:
                where.append("level = ANY(%s)")
                params.append(lv)
        if event_type:
            where.append("event_type = %s")
            params.append(event_type[:80])
        if ip:
            where.append("ip = %s")
            params.append(ip[:64])
        if identifier:
            where.append("identifier = %s")
            params.append(identifier[:150])
        if tenant:
            where.append("split_part(path, '/', 2) = %s")
            params.append(tenant[:100].lower())
        if alerts_only:
            where.append(ALERT_SQL)
        if q:
            pat = "%" + like_escape(q.strip()[:200]) + "%"
            clause = ("(message ILIKE %s ESCAPE '\\' OR identifier ILIKE %s ESCAPE '\\' OR path ILIKE %s ESCAPE '\\' "
                      "OR event_type ILIKE %s ESCAPE '\\' OR extra_json ILIKE %s ESCAPE '\\'")
            params += [pat] * 5
            if looks_like_ip(q.strip()):
                clause += " OR ip = %s"
                params.append(q.strip())
            where.append(clause + ")")
        if cursor:
            where.append("id < %s")
            params.append(clamp_int(cursor, 0, 0, 2 ** 31 - 1))
        cur.execute("SELECT " + EV_COLS + " FROM security_events WHERE " + " AND ".join(where) +  # nosec B608 -- fixed clauses, bound values
                    " ORDER BY id DESC LIMIT %s", params + [limit + 1])
        rows = cur.fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    ev = [shape_event(r) for r in rows]
    if ev:
        with read_cursor("att_master") as cur:
            cur.execute("SELECT source_event_id, id, status FROM soc_cases WHERE source_event_id = ANY(%s)",
                        ([e["id"] for e in ev],))
            linked = {r[0]: {"id": r[1], "status": r[2]} for r in cur.fetchall()}
        for e in ev:
            e["case"] = linked.get(e["id"])
    return {"events": ev, "next_cursor": ev[-1]["id"] if more and ev else None, "hours": hours}


def event_detail(event_id):
    with read_cursor("public") as cur:
        cur.execute("SELECT " + EV_COLS + " FROM security_events WHERE id=%s", (event_id,))
        row = cur.fetchone()
        if not row:
            return None
        ev = shape_event(row, detail=True)
        ts = row[9]
        related = {}
        for key, col, val in (("same_ip", "ip", ev["ip"]), ("same_identifier", "identifier", ev["identifier"])):
            if not val or val in ("None", ""):
                related[key] = []
                continue
            cur.execute("SELECT " + EV_COLS + " FROM security_events WHERE " + col + " = %s AND id <> %s "  # nosec B608 -- col is a literal above
                        "AND created_at BETWEEN %s - INTERVAL '24 hours' AND %s + INTERVAL '24 hours' "
                        "ORDER BY created_at DESC LIMIT 25", (val, event_id, ts, ts))
            related[key] = [shape_event(r) for r in cur.fetchall()]
        ev["related"] = related
        try:
            cur.execute("SELECT prev_hash, row_hash FROM security_events WHERE id=%s", (event_id,))
            ph, rh = cur.fetchone()
            ev["chain"] = {"prev_hash": ph, "row_hash": rh}
        except Exception:
            ev["chain"] = None
        ev["banned"] = bool(ev["ip"]) and ev["ip"] in _banned_set(cur, [ev["ip"]])
        ev["protected"] = _protected(ev["ip"]) if ev["ip"] else False
    with read_cursor("att_master") as cur:
        cur.execute("SELECT id, status, title FROM soc_cases WHERE source_event_id=%s ORDER BY id DESC LIMIT 5", (event_id,))
        ev["cases"] = [{"id": a, "status": b, "title": c} for a, b, c in cur.fetchall()]
    return ev


# ── entities ─────────────────────────────────────────────────────────
def _profile(col, value, days=30):
    days = clamp_int(days, 30, 1, 90)
    with read_cursor("public") as cur:
        win = "created_at >= NOW() - (%s * INTERVAL '1 day')"
        cur.execute("SELECT COUNT(*), MIN(created_at), MAX(created_at), COUNT(DISTINCT event_type), "  # nosec B608 -- col is a literal
                    "COUNT(*) FILTER (WHERE level IN ('ERROR','CRITICAL')) FROM security_events WHERE " + col + "=%s AND " + win,
                    (value, days))
        total, first, last, ntypes, high = cur.fetchone()
        prof = {"value": value, "kind": col, "days": days, "events": total, "first_seen": iso(first),
                "last_seen": iso(last), "event_types": ntypes, "high_severity": high}
        if not total:
            prof.update({"types": [], "other": [], "paths": [], "recent": [], "families": [], "techniques": []})
        else:
            cur.execute("SELECT event_type, COUNT(*) FROM security_events WHERE " + col + "=%s AND " + win +  # nosec B608
                        " GROUP BY event_type ORDER BY 2 DESC LIMIT 25", (value, days))
            types = cur.fetchall()
            prof["types"] = [{"event_type": et, "label": tax.classify(et)["label"], "count": c} for et, c in types]
            fam, tech = {}, {}
            for et, c in types:
                f = tax.family_of(et)
                fam[f] = fam.get(f, 0) + c
                for m in tax.classify(et)["mitre"]:
                    tech[m["id"]] = {"id": m["id"], "name": m["name"], "url": m["url"]}
            prof["families"] = [{"family": f, "label": tax.FAMILIES[f], "count": c} for f, c in sorted(fam.items(), key=lambda x: -x[1])]
            prof["techniques"] = list(tech.values())
            other_col = "identifier" if col == "ip" else "ip"
            cur.execute("SELECT " + other_col + ", COUNT(*) FROM security_events WHERE " + col + "=%s AND " + win +  # nosec B608
                        " AND " + other_col + " IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 20", (value, days))
            prof["other"] = [{"value": v, "count": c} for v, c in cur.fetchall()]
            cur.execute("SELECT path, COUNT(*) FROM security_events WHERE " + col + "=%s AND " + win +  # nosec B608
                        " AND path IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15", (value, days))
            prof["paths"] = [{"path": p, "count": c} for p, c in cur.fetchall()]
            cur.execute("SELECT " + EV_COLS + " FROM security_events WHERE " + col + "=%s AND " + win +  # nosec B608
                        " ORDER BY id DESC LIMIT 50", (value, days))
            prof["recent"] = [shape_event(r) for r in cur.fetchall()]
        if col == "ip":
            cur.execute("SELECT reason, banned_by, banned_at, expires_at FROM public.banned_ips "
                        "WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW())", (value,))
            b = cur.fetchone()
            prof["ban"] = {"reason": b[0], "by": b[1], "at": iso(b[2]), "expires": iso(b[3])} if b else None
            prof["protected"] = _protected(value)
    return prof


def entity_ip(ip, days=30):
    return _profile("ip", ip, days)


def entity_identifier(identifier, days=30):
    return _profile("identifier", identifier, days)


# ── detections ───────────────────────────────────────────────────────
def detections(hours=72):
    hours = hours_arg(hours, 72)
    with read_cursor("public") as cur:
        cur.execute("SELECT " + EV_COLS + " FROM security_events WHERE event_type LIKE 'detection.%%' AND " + _WINDOW +
                    " ORDER BY id DESC LIMIT 200", (hours,))
        events = [shape_event(r) for r in cur.fetchall()]
        cur.execute("SELECT rule, MAX(last_fired), SUM(fired) FROM detection_state GROUP BY rule")
        fired = {r[0]: {"last": iso(r[1]), "times": int(r[2] or 0)} for r in cur.fetchall()}
    from utils.detections import RULES
    catalog = []
    for r in RULES:
        label, techniques, meaning = tax.DETECTIONS.get(r.name, (r.name, [], r.summary))
        try:
            thresholds = list(r.params())
        except Exception:
            thresholds = []
        catalog.append({"name": r.name, "label": label, "level": r.level, "scope": r.scope,
                        "cooldown_minutes": r.cooldown_minutes, "meaning": meaning or r.summary,
                        "mitre": [tax._technique(t) for t in techniques], "thresholds": thresholds,
                        "last_fired": fired.get(r.name, {}).get("last"), "times_fired": fired.get(r.name, {}).get("times", 0)})
    return {"hours": hours, "events": events, "rules": catalog}


# ── response state ───────────────────────────────────────────────────
def bans():
    with read_cursor("public") as cur:
        cur.execute("SELECT ip, reason, banned_by, banned_at, expires_at, (expires_at IS NULL OR expires_at > NOW()) "
                    "FROM banned_ips ORDER BY banned_at DESC LIMIT 500")
        return [{"ip": ip, "reason": r, "by": by, "at": iso(at), "expires": iso(ex), "active": bool(act)}
                for ip, r, by, at, ex, act in cur.fetchall()]


# ── cases ────────────────────────────────────────────────────────────
_CASE_COLS = "id, title, severity, status, assignee, source_type, source_event_id, summary, resolution, created_by, created_at, updated_at, closed_at"


def _case(row):
    keys = ("id", "title", "severity", "status", "assignee", "source_type", "source_event_id", "summary", "resolution",
            "created_by", "created_at", "updated_at", "closed_at")
    return {k: iso(v) for k, v in zip(keys, row)}


def cases(status=None, limit=200):
    with read_cursor("att_master") as cur:
        if status == "open":
            cur.execute("SELECT " + _CASE_COLS + " FROM soc_cases WHERE status IN ('open','investigating','contained') "  # nosec B608
                        "ORDER BY updated_at DESC LIMIT %s", (clamp_int(limit, 200, 1, 500),))
        elif status:
            cur.execute("SELECT " + _CASE_COLS + " FROM soc_cases WHERE status=%s ORDER BY updated_at DESC LIMIT %s",  # nosec B608
                        (status, clamp_int(limit, 200, 1, 500)))
        else:
            cur.execute("SELECT " + _CASE_COLS + " FROM soc_cases ORDER BY updated_at DESC LIMIT %s",  # nosec B608
                        (clamp_int(limit, 200, 1, 500),))
        return [_case(r) for r in cur.fetchall()]


def case_detail(case_id):
    with read_cursor("att_master") as cur:
        cur.execute("SELECT " + _CASE_COLS + " FROM soc_cases WHERE id=%s", (case_id,))  # nosec B608
        row = cur.fetchone()
        if not row:
            return None
        c = _case(row)
        cur.execute("SELECT id, author, body, created_at FROM soc_case_notes WHERE case_id=%s ORDER BY id", (case_id,))
        c["notes"] = [{"id": i, "author": a, "body": b, "created_at": iso(t)} for i, a, b, t in cur.fetchall()]
    if c["source_event_id"]:
        c["source_event"] = event_detail(c["source_event_id"])
    return c


# ── audit integrity ──────────────────────────────────────────────────
def audit_integrity(timeout_ms=45000):
    """Verify the platform-wide security_events hash chain (utils/audit_chain)."""
    from database import owner_privileges
    from utils import audit_chain
    started = time.monotonic()
    cap = clamp_int(os.environ.get("SOC_VERIFY_MAX_ROWS", 500000), 500000, 1000, 50000000)
    # Under the owner/runtime role split the runtime role has no access to
    # audit_chain_head (by design), so verify as the owner like
    # utils/audit_jobs.py and scripts/verify_audit_chain.py do. The block is a
    # single READ ONLY transaction, so the owner connection cannot write here.
    with owner_privileges(), read_cursor("public", timeout_ms) as cur:
        # verify_table holds every chained row in memory; on a very large table run the
        # scheduled job / scripts/verify_audit_chain.py instead of a button.
        cur.execute("SELECT COUNT(*) FROM security_events")
        rows = cur.fetchone()[0]
        if rows > cap:
            raise ValueError(f"security_events has {rows:,} rows (console limit {cap:,}). "
                             "Run scripts/verify_audit_chain.py, or raise SOC_VERIFY_MAX_ROWS.")
        report = audit_chain.verify_table(cur, "public", "security_events")
    report = {k: (iso(v) if not isinstance(v, (int, float, str, bool, list, dict, type(None))) else v)
              for k, v in report.items()}
    report["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return report


# ── cross-tenant views (cached) ──────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 45


def _cached(key, producer):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_cache():
    with _cache_lock:
        _cache.clear()


def tenants_overview(hours=24):
    hours = hours_arg(hours)

    def build():
        tenants = list_tenants()
        with read_cursor("public") as cur:
            cur.execute(
                "SELECT split_part(path, '/', 2) AS slug, COUNT(*), COUNT(*) FILTER (WHERE " + ALERT_SQL + ") "
                "FROM security_events WHERE " + _WINDOW + " AND path LIKE '/%%/%%' GROUP BY 1", (hours,))
            counts = {s: (c, a) for s, c, a in cur.fetchall()}
        with read_cursor("att_master") as cur:
            cur.execute("SELECT conflicting_tenant_id, COUNT(*) FROM tenant_duplicate_alerts WHERE acknowledged=0 GROUP BY 1")
            dupes = dict(cur.fetchall())
        out = []
        for t in tenants:
            ev, al = counts.get(t["slug"], (0, 0))
            d = dupes.get(t["id"], 0)
            score = min(100, al * 8 + min(ev, 200) // 10 + d * 10 + (15 if t["status"] != "active" else 0))
            out.append({"id": t["id"], "company": t["company"], "slug": t["slug"], "status": t["status"],
                        "billing_state": t["billing_state"], "events": ev, "alerts": al, "duplicate_alerts": d,
                        "risk": score, "created_at": iso(t["created_at"])})
        out.sort(key=lambda x: (-x["risk"], x["company"].lower()))
        return {"hours": hours, "tenants": out, "note": "Event counts attribute events to a tenant by the first URL "
                "segment of the request path; events without a tenant URL are not attributed."}
    return _cached(("tenants", hours), build)


def attendance_integrity(days=7):
    days = clamp_int(days, 7, 1, 60)

    def build():
        def per_tenant(t, cur):
            cur.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE outcome <> 'accepted'), COUNT(*) FILTER (WHERE mocked), "
                "COUNT(*) FILTER (WHERE flags IS NOT NULL AND flags <> ''), "
                "COUNT(*) FILTER (WHERE liveness_action IS NOT NULL AND liveness_action NOT IN ('pass','')) "
                "FROM punch_evidence WHERE created_at >= NOW() - (%s * INTERVAL '1 day')", (days,))
            total, rejected, mocked, flagged, live = cur.fetchone()
            cur.execute("SELECT flags FROM punch_evidence WHERE flags IS NOT NULL AND flags <> '' "
                        "AND created_at >= NOW() - (%s * INTERVAL '1 day') LIMIT 5000", (days,))
            flags = {}
            for (f,) in cur.fetchall():
                for part in f.split(","):
                    part = part.strip()
                    if part:
                        flags[part] = flags.get(part, 0) + 1
            if not total:
                return None
            return {"id": t["id"], "company": t["company"], "slug": t["slug"], "punches": total, "rejected": rejected,
                    "mock_location": mocked, "flagged": flagged, "liveness_issues": live, "flags": flags}
        rows, errors = each_tenant(per_tenant)
        agg = {}
        for r in rows:
            for k, v in r["flags"].items():
                agg[k] = agg.get(k, 0) + v
        rows.sort(key=lambda r: (-(r["flagged"] + r["rejected"]), r["company"].lower()))
        flags = sorted(({"flag": k, "count": v} for k, v in agg.items()), key=lambda x: -x["count"])
        totals = {k: sum(r[k] for r in rows) for k in ("punches", "rejected", "mock_location", "flagged")}
        return {"days": days, "tenants": rows, "flags": flags, "errors": errors, "totals": totals}
    return _cached(("attendance", days), build)


def risky_sessions():
    def build():
        def per_tenant(t, cur):
            cur.execute("SELECT sid, identifier, attempt_type, score, status, last_reason, updated_at FROM session_risk "
                        "WHERE status <> 'active' OR score > 0 ORDER BY updated_at DESC LIMIT 25")
            rows = cur.fetchall()
            if not rows:
                return None
            return [{"tenant_id": t["id"], "company": t["company"], "slug": t["slug"],
                     "sid_hint": sid[:6] + "…" + sid[-4:], "sid": sid, "identifier": ident, "type": typ, "score": score,
                     "status": status, "reason": reason, "updated_at": iso(ts)}
                    for sid, ident, typ, score, status, reason, ts in rows]
        groups, errors = each_tenant(per_tenant)
        flat = [s for g in groups for s in g]
        flat.sort(key=lambda s: s["updated_at"] or "", reverse=True)
        return {"sessions": flat[:200], "errors": errors}
    return _cached(("sessions",), build)


# ── posture ──────────────────────────────────────────────────────────
def _truthy(name, default=""):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def posture():
    """Configuration checks computed from live settings. Reports only whether a
    control is on and where to change it -- never a secret or a secret's value."""
    env = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "production")).lower()
    prod = env == "production"
    checks = []

    def add(cid, title, status, detail, fix=""):
        checks.append({"id": cid, "title": title, "status": status, "detail": detail, "fix": fix})

    add("env", "Production mode", "pass" if prod else "warn", f"APP_ENV is '{env}'.",
        "Set APP_ENV=production on every internet-facing deployment (development relaxes OTP and malware-scan checks).")
    add("secret", "Session signing key", "pass" if os.environ.get("SECRET_KEY") else "fail",
        "SECRET_KEY is set." if os.environ.get("SECRET_KEY") else "SECRET_KEY is not set in the environment.",
        "Set a long random SECRET_KEY; without it sessions are not stable across restarts.")
    add("enc", "PII encryption key", "pass" if os.environ.get("ENCRYPTION_KEY") else "fail",
        "ENCRYPTION_KEY is set." if os.environ.get("ENCRYPTION_KEY") else "ENCRYPTION_KEY is not set.",
        "Set ENCRYPTION_KEY (Fernet). Rotate with scripts/rotate_encryption_key.py using ENCRYPTION_KEY_PREVIOUS.")
    hops = os.environ.get("TRUSTED_PROXY_HOPS", "")
    add("proxy", "Client-IP trust (proxy hops)", "pass" if hops.strip() else "warn",
        f"TRUSTED_PROXY_HOPS={hops}" if hops.strip() else "TRUSTED_PROXY_HOPS is not set.",
        "Set it to the number of reverse proxies in front of the app so IP bans and rate limits see real client addresses.")
    split = bool(os.environ.get("DB_OWNER_USER", "").strip())
    add("dbsplit", "Database owner / runtime role split", "pass" if split else "warn",
        "Runtime role is separate from the schema owner." if split else "The app runs with one database role.",
        "Set DB_OWNER_USER (+ DB_OWNER_PASS) so the web process cannot alter or drop schema; see scripts/provision_db_roles.py.")
    live = os.environ.get("LIVENESS_MODE", "").strip().lower() or "warn"
    add("liveness", "Face liveness enforcement", "pass" if live == "enforce" else "warn",
        f"LIVENESS_MODE={live}.", "Move LIVENESS_MODE from warn to enforce once mobile clients send the liveness burst.")
    loc = _truthy("MOBILE_REQUIRE_LOCATION", "true")
    add("location", "Mobile punches require location", "pass" if loc else "warn",
        "Location is required." if loc else "Punches are accepted without coordinates.",
        "Leave MOBILE_REQUIRE_LOCATION unset/true.")
    consent = _truthy("BIOMETRIC_CONSENT_REQUIRED")
    add("consent", "Biometric consent required", "pass" if consent else "warn",
        "Consent is enforced." if consent else "Biometric capture does not require recorded consent.",
        "Set BIOMETRIC_CONSENT_REQUIRED=true once the consent screen is live.")
    ts = bool(os.environ.get("TURNSTILE_SECRET_KEY") and os.environ.get("TURNSTILE_SITE_KEY"))
    add("captcha", "Login / signup CAPTCHA", "pass" if ts else "warn",
        "Cloudflare Turnstile is configured." if ts else "Turnstile keys are not configured.",
        "Set TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY.")
    add("socnet", "SOC console network allow-list", "pass" if os.environ.get("SOC_ALLOWED_CIDRS", "").strip() else "warn",
        "SOC_ALLOWED_CIDRS restricts who can reach /soc." if os.environ.get("SOC_ALLOWED_CIDRS", "").strip()
        else "/soc is reachable from any address (login still needs password + TOTP).",
        "Set SOC_ALLOWED_CIDRS to your office / VPN ranges.")
    add("anchors", "Audit-log anchoring", "info", "Hash-chain heads are written to stdout for off-box shipping.",
        "Confirm Alloy / Promtail ships them to Loki (observability/).")
    add("loki", "Log pipeline (Loki)", "pass" if os.environ.get("LOKI_URL") else "info",
        "LOKI_URL is set; the Logs view can query it." if os.environ.get("LOKI_URL") else
        "LOKI_URL is not set; the Logs view is disabled.", "Set LOKI_URL to query shipped logs from the console.")
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
    checks.sort(key=lambda c: order[c["status"]])
    return {"checks": checks, "summary": {s: sum(1 for c in checks if c["status"] == s) for s in order}}


# ── Loki (optional) ──────────────────────────────────────────────────
def loki_status():
    return {"configured": bool(os.environ.get("LOKI_URL", "").strip())}


def loki_query(query, minutes=60, limit=200):
    """Run a LogQL range query against the configured Loki. Read-only, bounded,
    and the target URL comes only from the LOKI_URL environment variable (never
    from the request), so it cannot be turned into an SSRF primitive."""
    import urllib.parse
    import urllib.request
    base = os.environ.get("LOKI_URL", "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return {"configured": False, "streams": []}
    query = (query or "").strip()
    if not query or len(query) > 500 or "\x00" in query:
        raise ValueError("Query must be 1-500 characters.")
    minutes = clamp_int(minutes, 60, 1, 1440)
    limit = clamp_int(limit, 200, 1, 500)
    end = int(time.time() * 1e9)
    params = urllib.parse.urlencode({"query": query, "limit": limit, "direction": "backward",
                                     "start": end - minutes * 60 * int(1e9), "end": end})
    req = urllib.request.Request(base + "/loki/api/v1/query_range?" + params, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:       # nosec B310 -- scheme checked, URL from env only
        body = json.loads(resp.read(8 * 1024 * 1024))
    lines = []
    for stream in (body.get("data", {}).get("result") or []):
        labels = stream.get("stream", {})
        for ts, line in stream.get("values", []):
            lines.append({"ts": int(ts), "labels": {k: str(v)[:80] for k, v in list(labels.items())[:8]}, "line": line[:2000]})
    lines.sort(key=lambda r: -r["ts"])
    return {"configured": True, "lines": lines[:limit], "minutes": minutes}
