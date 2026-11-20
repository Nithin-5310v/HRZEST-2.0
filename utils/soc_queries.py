# -*- coding: utf-8 -*-
"""Read-side queries for the SOC console.

Data sources (all already produced by the product -- nothing here invents data):

  public.security_events   every log_security_event(): auth, WAF, honeypot, detections, ...
  public.banned_ips        platform-wide IP bans
  att_master.tenants       company registry
  att_master.soc_*         the console's own cases / users / audit trail
  <tenant>.punch_evidence  attendance-integrity evidence      (per tenant)
  <tenant>.session_risk    sessions flagged by the risk engine (per tenant)

Genuine activity only: every view reads security_events through
soc_scope.events_cte() (the CTE `ev`), which leaves out the server's own
traffic, the console's own use and your listed testing. See utils/soc_scope.py.
No number here is a made-up score: counts, times and states as recorded, and
where the console draws a conclusion (the threat level) it returns its reasons.

All SQL is parameterised; analyst-supplied text only ever reaches a query as a
bound parameter (LIKE metacharacters escaped). Every call runs in a read-only
transaction with a statement timeout (utils/soc_db.py). Cross-tenant views
(sessions, attendance integrity) iterate tenants one at a time with a short
timeout and a small in-process cache so one analyst's refresh loop cannot turn
into a per-tenant query storm.
"""
import json
import os
import re
import threading
import time

from utils import soc_scope
from utils import soc_taxonomy as tax
from utils.soc_db import each_tenant, list_tenants, read_cursor
from utils.soc_util import clamp_int, iso, like_escape, looks_like_ip

EV_COLS = "id, event_type, level, message, identifier, ip, path, method, extra_json, created_at, tenant"
# The company an event belongs to: the `tenant` the request ran as, recorded at
# write time (extensions.log_security_event); rows from before that column fall
# back to the first path segment.
TENANT_SQL = "lower(COALESCE(NULLIF(tenant, ''), split_part(COALESCE(path, ''), '/', 2)))"
_WINDOW = "created_at >= NOW() - (%s * INTERVAL '1 hour')"
ALERT_SQL = "(event_type LIKE 'detection.%%' OR level IN ('ERROR','CRITICAL'))"


FAILED_LOGIN_TYPES = ("auth.failure", "auth.platform_admin_login_failed", "auth.mfa_failure")
HONEYPOT_TYPES = ("secops.honeypot_triggered", "honeypot.tcp_decoy_hit")
WAF_BLOCK_TYPES = ("waf.sqli_blocked", "waf.xss_blocked", "waf.path_traversal_blocked")


def _in(values):
    """SQL list literal from CONSTANT strings only (never request data)."""
    return "(" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


# The Overview KPI tiles, as SQL conditions. One definition serves both the tile
# counts and the drill-down brief, so a tile and its brief can never disagree.
# '%%' because every query using these also has bound parameters.
KPI_SQL = {
    "events": ("TRUE", None),
    "critical": ("level='CRITICAL'", {"level": "CRITICAL"}),
    "errors": ("level='ERROR'", {"level": "ERROR"}),
    "detections": ("event_type LIKE 'detection.%%'", {"family": "detection"}),
    "failed_logins": ("event_type IN " + _in(FAILED_LOGIN_TYPES), {"event_type": ",".join(FAILED_LOGIN_TYPES)}),
    "lockouts": ("event_type='auth.lockout'", {"event_type": "auth.lockout"}),
    # Only actual blocks: waf.redis_error / ban_write_failed / ban_skipped_shared_ip
    # / auto_ban are WAF housekeeping, not attacks stopped.
    "waf_blocks": ("event_type LIKE 'waf.%%\\_blocked' ESCAPE '\\'", {"event_type": ",".join(WAF_BLOCK_TYPES)}),
    "honeypot_hits": ("event_type IN " + _in(HONEYPOT_TYPES), {"event_type": ",".join(HONEYPOT_TYPES)}),
    "unique_ips": ("ip IS NOT NULL", None),
}


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


def _path_company(path):
    """For rows recorded before security_events.tenant: the company named by the
    path's first segment -- only when a company with that URL slug exists, so a
    platform route ('/employees') is never shown as a company."""
    hint = tax.tenant_hint(path)
    if not hint:
        return None
    slugs = soc_scope.per_request(("company_slugs",), lambda: {(t["slug"] or "").lower() for t in list_tenants()})
    return hint if hint in slugs else None


def shape_event(row, detail=False):
    eid, et, level, msg, ident, ip, path, method, extra, ts, tenant = row
    cls = tax.classify(et, level)
    d = {"id": eid, "event_type": et, "level": level, "message": msg, "identifier": ident, "ip": ip,
         "path": path, "method": method, "created_at": iso(ts), "family": cls["family"], "label": cls["label"],
         "mitre": [m["id"] for m in cls["mitre"]], "severity": tax.severity_of(et, level),
         "alert": tax.is_alert(et, level), "tenant": tenant or _path_company(path)}
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
    return {r[0] for r in cur.fetchall()} | (set(ips) & set(_tenant_active_bans()))


def _tenant_active_bans():
    """{ip: number of companies} for still-enforced bans in companies' own
    banned_ips tables. Those rows are no longer written but app.py's ban hook
    still enforces them, so every "is this IP banned" answer must include them,
    not only the Response view (bans()). Cached; ban/unban clears the cache."""
    def build():
        def per_tenant(t, cur):
            if t["schema"] == "public":
                return None
            table = banned_ips_table(t["schema"])
            cur.execute("SELECT to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                return None
            cur.execute("SELECT ip FROM " + table + " WHERE expires_at IS NULL OR expires_at > NOW()")  # nosec B608 -- validated schema name
            return [ip for (ip,) in cur.fetchall()] or None
        out = {}
        for ips in each_tenant(per_tenant)[0]:
            for ip in ips:
                out[ip] = out.get(ip, 0) + 1
        return out
    return _cached(("tenant_active_bans",), build)


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
        ev, ep = soc_scope.events_cte(cur, hours)
        k = {name: sql for name, (sql, _) in KPI_SQL.items()}
        cur.execute(
            ev + "SELECT COUNT(*), COUNT(*) FILTER (WHERE " + k["critical"] + "), COUNT(*) FILTER (WHERE " + k["errors"] + "), "
            "COUNT(*) FILTER (WHERE level='WARNING'), COUNT(*) FILTER (WHERE " + k["detections"] + "), "
            "COUNT(*) FILTER (WHERE " + k["failed_logins"] + "), "
            "COUNT(*) FILTER (WHERE " + k["waf_blocks"] + "), "
            "COUNT(*) FILTER (WHERE " + k["honeypot_hits"] + "), "
            "COUNT(*) FILTER (WHERE " + k["lockouts"] + "), "
            "MAX(created_at) "
            "FROM ev WHERE " + _WINDOW, ep + [hours])  # nosec B608 -- KPI_SQL is constant
        (total, crit, err, warn, det, authfail, waf, honey, lock, last_ts) = cur.fetchone()
        cur.execute(ev + "SELECT COUNT(DISTINCT ip) FROM ev WHERE " + _WINDOW + " AND ip IS NOT NULL", ep + [hours])
        ips = cur.fetchone()[0]
        out["kpi"] = {"events": total, "critical": crit, "error": err, "warning": warn, "detections": det,
                      "failed_logins": authfail, "waf_blocks": waf, "honeypot_hits": honey, "lockouts": lock,
                      "unique_ips": ips, "last_event": iso(last_ts)}
        cur.execute(ev + "SELECT level, COUNT(*) FROM ev WHERE created_at >= NOW() - INTERVAL '1 hour' GROUP BY level", ep)
        lh = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0}
        lh.update({lvl: c for lvl, c in cur.fetchall() if lvl in lh})
        out["last_hour"] = lh
        out["threat"] = threat_level(lh)

        cur.execute(
            ev + "SELECT date_bin(%s::interval, created_at, TIMESTAMP '2001-01-01'), level, COUNT(*) "
            "FROM ev WHERE " + _WINDOW + " GROUP BY 1, 2 ORDER BY 1", ep + [bucket, hours])
        counted = cur.fetchall()
        # Every bucket in the window, including empty ones, so quiet periods show
        # as gaps on the chart instead of busy periods being drawn side by side.
        # Same origin as date_bin above, so the keys line up.
        cur.execute(
            "SELECT generate_series(date_bin(%s::interval, LOCALTIMESTAMP - (%s * INTERVAL '1 hour'), "
            "TIMESTAMP '2001-01-01'), LOCALTIMESTAMP, %s::interval)", (bucket, hours, bucket))
        pts = {iso(t): {"t": iso(t), "CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0} for (t,) in cur.fetchall()}
        for t, lvl, c in counted:
            p = pts.setdefault(iso(t), {"t": iso(t), "CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0})
            p[lvl if lvl in p else "INFO"] += c
        out["timeline"] = sorted(pts.values(), key=lambda p: p["t"])

        cur.execute(ev + "SELECT event_type, COUNT(*) FROM ev WHERE " + _WINDOW + " GROUP BY event_type", ep + [hours])
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
            ev + "SELECT ip, COUNT(*), COUNT(DISTINCT event_type), MAX(created_at), "
            "COUNT(*) FILTER (WHERE level IN ('ERROR','CRITICAL')) FROM ev "
            "WHERE " + _WINDOW + " AND ip IS NOT NULL GROUP BY ip ORDER BY 5 DESC, 2 DESC LIMIT 10", ep + [hours])
        rows = cur.fetchall()
        banned = _banned_set(cur, [r[0] for r in rows])
        out["top_ips"] = [{"ip": ip, "events": c, "types": t, "last": iso(ts), "high": hi,
                           "banned": ip in banned, "protected": _protected(ip)} for ip, c, t, ts, hi in rows]
        # What was left out, so the page can say so instead of hiding it silently.
        out["excluded"] = soc_scope.excluded_summary(cur, hours)
        # Behind a proxy that is not trusted (TRUSTED_PROXY_HOPS unset/wrong) every
        # visitor is recorded as the proxy's private address -- and so left out as
        # the server's own traffic. Look at everything recorded to say so, since
        # the numbers above would otherwise just look quiet.
        cur.execute("SELECT ip, COUNT(*) FROM security_events WHERE " + _WINDOW +
                    " AND ip IS NOT NULL GROUP BY ip ORDER BY 2 DESC LIMIT 1", (hours,))
        busiest = cur.fetchone()
        recorded = out["excluded"]["recorded"]
        out["proxy_hint"] = bool(busiest and recorded and _protected(busiest[0]) and busiest[1] * 2 >= recorded)
        out["proxy_ip"] = busiest[0] if out["proxy_hint"] else None
        # "Most active accounts": WAF / honeypot / ban-review events put the
        # source *address* in `identifier` (the ip column is always the
        # requester -- for a TCP decoy report, the honeypot container), so
        # leave address-shaped identifiers out: they are not accounts, and
        # they already appear under the busiest sources.
        cur.execute(
            ev + "SELECT identifier, COUNT(*), COUNT(DISTINCT ip), MAX(created_at) FROM ev "
            "WHERE " + _WINDOW + " AND identifier IS NOT NULL AND identifier NOT IN ('', 'None') "
            "AND identifier !~ '^[0-9]{1,3}(\\.[0-9]{1,3}){3}$' AND identifier NOT LIKE '%%:%%:%%' "
            "GROUP BY identifier ORDER BY 2 DESC LIMIT 10", ep + [hours])
        out["top_identifiers"] = [{"identifier": i, "events": c, "ips": n, "last": iso(ts)} for i, c, n, ts in cur.fetchall()]
        cur.execute(ev + "SELECT " + EV_COLS + " FROM ev WHERE " + _WINDOW + " AND " + ALERT_SQL +
                    " ORDER BY created_at DESC, id DESC LIMIT 12", ep + [hours])
        out["recent_alerts"] = [shape_event(r) for r in cur.fetchall()]
        # Same count as the Response view's active_total (platform + company rows,
        # bans on your own test addresses apart).
        ips = _active_platform_bans(cur) + [ip for ip, n in _tenant_active_bans().items() for _ in range(n)]
        out["active_bans"], out["active_bans_testing"] = _count_bans(ips)
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
        ev, ep = soc_scope.events_cte(cur, 24)
        cur.execute(ev + "SELECT level, COUNT(*) FROM ev WHERE created_at >= NOW() - INTERVAL '1 hour' GROUP BY level", ep)
        lh = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0, "INFO": 0}
        lh.update({lvl: c for lvl, c in cur.fetchall() if lvl in lh})
        cur.execute(ev + "SELECT COUNT(*) FROM ev WHERE created_at >= NOW() - INTERVAL '24 hours' AND " + ALERT_SQL, ep)
        alerts_24h = cur.fetchone()[0]
    with read_cursor("att_master") as cur:
        cur.execute("SELECT COUNT(*) FROM soc_cases WHERE status IN ('open','investigating','contained')")
        open_cases = cur.fetchone()[0]
    return {"now": iso(now), "threat": threat_level(lh), "last_hour": lh, "alerts_24h": alerts_24h, "open_cases": open_cases}


def event_totals(hours=24):
    """Genuine events in the last `hours` and in the `hours` before that."""
    hours = hours_arg(hours)
    with read_cursor("public") as cur:
        ev, ep = soc_scope.events_cte(cur, hours * 2)
        cur.execute(ev + "SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - (%s * INTERVAL '1 hour')), "
                    "COUNT(*) FILTER (WHERE created_at < NOW() - (%s * INTERVAL '1 hour')) "
                    "FROM ev WHERE created_at >= NOW() - (%s * INTERVAL '1 hour')", ep + [hours, hours, hours * 2])
        current, previous = cur.fetchone()
    return {"current": current, "previous": previous}


# ── events ───────────────────────────────────────────────────────────
def search_events(*, q=None, levels=None, family=None, event_type=None, ip=None, identifier=None, tenant=None,
                  alerts_only=False, hours=24, cursor=None, limit=100, since=None):
    hours = hours_arg(hours)
    limit = clamp_int(limit, 100, 1, 200)
    where, params = [_WINDOW], [hours]
    with read_cursor("public") as cur:
        ev, ep = soc_scope.events_cte(cur, hours)
        if family:
            cur.execute(ev + "SELECT DISTINCT event_type FROM ev WHERE " + _WINDOW, ep + [hours])
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
            # One type, or a comma-separated list (drill-downs from the Overview).
            types = [t.strip()[:80] for t in event_type.split(",") if t.strip()][:50]
            if types:
                where.append("event_type = ANY(%s)")
                params.append(types)
        if ip:
            where.append(soc_scope.IP_MATCH_SQL)
            params += [ip[:64]] * 3
        if identifier:
            where.append("identifier = %s")
            params.append(identifier[:150])
        if tenant:
            where.append(TENANT_SQL + " = %s")
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
        if since:
            # "What arrived after the newest row on screen" -- the live-list check.
            where.append("id > %s")
            params.append(clamp_int(since, 0, 0, 2 ** 31 - 1))
        cur.execute(ev + "SELECT " + EV_COLS + " FROM ev WHERE " + " AND ".join(where) +  # nosec B608 -- fixed clauses, bound values
                    " ORDER BY id DESC LIMIT %s", ep + params + [limit + 1])
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
    src = " FROM (" + soc_scope.SOURCE_SQL + ") src WHERE "       # `ip` = the address each event is about
    with read_cursor("public") as cur:
        cur.execute("SELECT " + EV_COLS + src + "id=%s", (event_id,))  # nosec B608 -- constant SQL
        row = cur.fetchone()
        if not row:
            return None
        ev = shape_event(row, detail=True)
        ts = row[9]
        related = {}
        for key, val in (("same_ip", ev["ip"]), ("same_identifier", ev["identifier"])):
            if not val or val in ("None", ""):
                related[key] = []
                continue
            cond, cp = (soc_scope.IP_MATCH_SQL, [val] * 3) if key == "same_ip" else ("identifier = %s", [val])
            cur.execute("SELECT " + EV_COLS + src + cond + " AND id <> %s "  # nosec B608 -- constant SQL, bound values
                        "AND created_at BETWEEN %s - INTERVAL '24 hours' AND %s + INTERVAL '24 hours' "
                        "ORDER BY created_at DESC LIMIT 100", cp + [event_id, ts, ts])
            # Genuine activity only, as in every list (utils/soc_scope.py).
            related[key] = [shape_event(r) for r in cur.fetchall()
                            if soc_scope.is_genuine(r[1], r[5], r[4], r[6], r[10])][:25]
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
    if col == "ip":
        reason = soc_scope.ip_reason(value)
    else:
        reason = "test_account" if soc_scope.is_test_account(value) else None
    # ev.ip is the address each event is about (soc_scope.SOURCE_SQL).
    match, mp = (soc_scope.IP_MATCH_SQL, [value] * 3) if col == "ip" else ("identifier = %s", [value])
    with read_cursor("public") as cur:
        ev, ep = soc_scope.events_cte(cur, days * 24)
        win = "created_at >= NOW() - (%s * INTERVAL '1 day')"
        cur.execute(ev + "SELECT COUNT(*), MIN(created_at), MAX(created_at), COUNT(DISTINCT event_type), "  # nosec B608 -- constant SQL
                    "COUNT(*) FILTER (WHERE level IN ('ERROR','CRITICAL')) FROM ev WHERE " + match + " AND " + win,
                    ep + mp + [days])
        total, first, last, ntypes, high = cur.fetchone()
        prof = {"value": value, "kind": col, "days": days, "events": total, "first_seen": iso(first),
                "last_seen": iso(last), "event_types": ntypes, "high_severity": high,
                # Why this value shows nothing, when it is one the console leaves out.
                "excluded": soc_scope.EXCLUDED_NOTES.get(reason) if reason else None}
        if not total:
            prof.update({"types": [], "other": [], "paths": [], "recent": [], "families": [], "techniques": []})
        else:
            cur.execute(ev + "SELECT event_type, COUNT(*) FROM ev WHERE " + match + " AND " + win +  # nosec B608
                        " GROUP BY event_type ORDER BY 2 DESC LIMIT 25", ep + mp + [days])
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
            # Not the value itself: WAF, honeypot and ban-review events name their
            # source address in `identifier`, and an address is not its own account.
            cur.execute(ev + "SELECT " + other_col + ", COUNT(*) FROM ev WHERE " + match + " AND " + win +  # nosec B608
                        " AND " + other_col + " IS NOT NULL AND " + other_col + " NOT IN ('', 'None', %s) "
                        "GROUP BY 1 ORDER BY 2 DESC LIMIT 20", ep + mp + [days, value])
            prof["other"] = [{"value": v, "count": c} for v, c in cur.fetchall()]
            cur.execute(ev + "SELECT path, COUNT(*) FROM ev WHERE " + match + " AND " + win +  # nosec B608
                        " AND path IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15", ep + mp + [days])
            prof["paths"] = [{"path": p, "count": c} for p, c in cur.fetchall()]
            cur.execute(ev + "SELECT " + EV_COLS + " FROM ev WHERE " + match + " AND " + win +  # nosec B608
                        " ORDER BY id DESC LIMIT 50", ep + mp + [days])
            prof["recent"] = [shape_event(r) for r in cur.fetchall()]
        if col == "ip":
            cur.execute("SELECT reason, banned_by, banned_at, expires_at FROM public.banned_ips "
                        "WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW())", (value,))
            b = cur.fetchone()
            prof["ban"] = ({"reason": b[0], "by": b[1], "at": iso(b[2]), "expires": iso(b[3]), "company": None,
                            "tenant_id": None} if b else None)
            prof["protected"] = _protected(value)
    if col == "ip" and prof["ban"] is None and value in _tenant_active_bans():
        # Still enforced from a company's own list (app.py's ban hook): banned all
        # the same, and lifted from that list.
        prof["ban"] = _company_ban(value)
    return prof


def _company_ban(ip):
    """The still-enforced ban on `ip` in a company's own banned_ips, as Lookup
    shows it (with the company, so it can be lifted there), or None."""
    def per_tenant(t, cur):
        if t["schema"] == "public":
            return None
        table = banned_ips_table(t["schema"])
        cur.execute("SELECT to_regclass(%s)", (table,))
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT reason, banned_by, banned_at, expires_at FROM " + table +  # nosec B608 -- validated schema name
                    " WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW())", (ip,))
        b = cur.fetchone()
        return ({"reason": b[0], "by": b[1], "at": iso(b[2]), "expires": iso(b[3]), "company": t["company"],
                 "tenant_id": t["id"]} if b else None)
    found = each_tenant(per_tenant)[0]
    if not found:
        return None
    return dict(found[0], other_companies=len(found) - 1)


def entity_ip(ip, days=30):
    return _profile("ip", ip, days)


def entity_identifier(identifier, days=30):
    return _profile("identifier", identifier, days)


# ── detections ───────────────────────────────────────────────────────
# How far back the rule catalogue counts firings (the longest look-back of any view).
DETECTION_HISTORY_DAYS = 90


def detections(hours=72):
    hours = hours_arg(hours, 72)
    with read_cursor("public") as cur:
        ev, ep = soc_scope.events_cte(cur, DETECTION_HISTORY_DAYS * 24)
        cur.execute(ev + "SELECT " + EV_COLS + " FROM ev WHERE event_type LIKE 'detection.%%' AND " + _WINDOW +
                    " ORDER BY id DESC LIMIT 200", ep + [hours])
        events = [shape_event(r) for r in cur.fetchall()]
        # Counted from the same genuine events as the list above -- not from
        # detection_state, the engine's cooldown table, which also counts rules
        # fired by the server's own addresses and your testing.
        cur.execute(ev + "SELECT substr(event_type, 11), MAX(created_at), COUNT(*) FROM ev "
                    "WHERE event_type LIKE 'detection.%%' AND created_at >= NOW() - (%s * INTERVAL '1 day') "
                    "GROUP BY 1", ep + [DETECTION_HISTORY_DAYS])
        fired = {r: {"last": iso(ts), "times": n} for r, ts, n in cur.fetchall()}
    from utils.detections import RULES
    outcomes = detection_case_outcomes()
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
                        "last_fired": fired.get(r.name, {}).get("last"), "times_fired": fired.get(r.name, {}).get("times", 0),
                        "case_outcomes": outcomes.get(r.name)})
    return {"hours": hours, "events": events, "rules": catalog, "history_days": DETECTION_HISTORY_DAYS}


# ── platform activity as log lines (Log analysis without an upload) ──
PLATFORM_LOG_MAX_EVENTS = 50_000


def platform_log_text(hours=24, max_bytes=None, max_events=PLATFORM_LOG_MAX_EVENTS):
    """The security events recorded in the window, as JSON lines in the shape
    utils/soc_loganalysis.py already parses (ip, path, method, event_type,
    level, identifier, created_at). Newest events win when the window holds
    more than the analyser's size limit. -> (text, {events, total, capped})."""
    from utils import soc_loganalysis
    hours = hours_arg(hours)
    max_bytes = max_bytes or soc_loganalysis.MAX_BYTES
    # Genuine activity only (utils/soc_scope.py): the console's own use would inflate
    # the numbers and could put an analyst's own address among the "block"
    # recommendations; the server's own addresses and your testing are not attacks.
    # Strangers probing /socperson stay in the analysis.
    with read_cursor("public", 20000) as cur:
        ev, ep = soc_scope.events_cte(cur, hours)
        cur.execute(ev + "SELECT COUNT(*) FROM ev WHERE " + _WINDOW, ep + [hours])
        total = cur.fetchone()[0]
        cur.execute(ev + "SELECT " + EV_COLS + " FROM ev WHERE " + _WINDOW + " ORDER BY id DESC LIMIT %s",
                    ep + [hours, max_events])
        rows = cur.fetchall()
    lines, size = [], 0
    for eid, et, level, msg, ident, ip, path, method, extra, ts, _tenant in rows:      # newest first
        rec = {"created_at": iso(ts), "ip": ip, "method": method, "path": path, "event_type": et,
               "level": level, "identifier": ident if ident not in (None, "", "None") else None, "message": msg}
        x = _extra(extra)           # parse whole: cutting first breaks the JSON and loses the UA
        for key in ("user_agent", "ua", "status", "status_code"):
            if x.get(key):
                rec[key] = x[key]
        line = json.dumps({k: v for k, v in rec.items() if v is not None}, default=str)
        size += len(line) + 1
        if size > max_bytes:
            break
        lines.append(line)
    lines.reverse()                                                         # chronological for the timeline
    return "\n".join(lines), {"events": len(lines), "total": total, "capped": len(lines) < total, "hours": hours}


# ── analyst brief (Overview drill-down) ──────────────────────────────
BRIEF_KINDS = ("kpi", "event_type", "family", "technique", "level")


def _slice(cur, kind, value, hours, ev, ep):
    """-> (sql_condition, params, events_view_filter) for one Overview slice.
    `ev`/`ep` is the genuine-activity CTE (soc_scope.events_cte). Raises
    ValueError for anything unknown."""
    if kind == "kpi":
        if value not in KPI_SQL:
            raise ValueError("Unknown indicator.")
        sql, filt = KPI_SQL[value]
        return sql, [], filt
    if kind == "event_type":
        et = (value or "").strip()[:80]
        if not et:
            raise ValueError("Give an event type.")
        return "event_type = %s", [et], {"event_type": et}
    if kind == "level":
        lv = (value or "").upper()
        if lv not in tax.LEVELS:
            raise ValueError("Unknown level.")
        return "level = %s", [lv], {"level": lv}
    if kind in ("family", "technique"):
        if kind == "family" and value not in tax.FAMILIES:
            raise ValueError("Unknown area.")
        if kind == "technique" and value not in tax.MITRE:
            raise ValueError("Unknown technique.")
        cur.execute(ev + "SELECT DISTINCT event_type FROM ev "
                    "WHERE created_at >= NOW() - (%s * INTERVAL '1 hour')", ep + [hours * 2])
        seen = [t for (t,) in cur.fetchall()]
        if kind == "family":
            types = [t for t in seen if tax.family_of(t) == value]
            filt = {"family": value}
        else:
            types = [t for t in seen if any(m["id"] == value or m["id"].startswith(value + ".")
                                            for m in tax.classify(t)["mitre"])]
            # No matching types -> no events; an empty filter would open ALL events.
            filt = {"event_type": ",".join(types[:50])} if types else False
        return "event_type = ANY(%s)", [types], filt
    raise ValueError("Unknown selection.")


def brief(kind, value, hours=24):
    """Everything an analyst needs to understand one Overview item: the playbook
    (what / why / check / respond), live numbers with the trend against the
    previous window of the same length, who is involved (sources, accounts,
    paths), which event types make it up, and recent examples."""
    from utils import soc_playbooks
    hours = hours_arg(hours)
    if kind == "level":
        value = (value or "").upper()
    cur_w = "created_at >= NOW() - (%s * INTERVAL '1 hour')"
    with read_cursor("public") as cur:
        ev, ep = soc_scope.events_cte(cur, hours * 2)
        cond, cp, filt = _slice(cur, kind, value, hours, ev, ep)
        base = " FROM ev WHERE " + cond + " AND "     # nosec B608 -- cond from _slice (constants / bound params)

        cur.execute(ev + "SELECT COUNT(*) FILTER (WHERE " + cur_w + "), "
                    "COUNT(*) FILTER (WHERE created_at < NOW() - (%s * INTERVAL '1 hour')), "
                    "COUNT(DISTINCT ip) FILTER (WHERE " + cur_w + "), "
                    "COUNT(DISTINCT identifier) FILTER (WHERE " + cur_w + " AND identifier NOT IN ('', 'None')), "
                    "MIN(created_at) FILTER (WHERE " + cur_w + "), MAX(created_at) FILTER (WHERE " + cur_w + ")"
                    + base + "created_at >= NOW() - (%s * INTERVAL '1 hour')",
                    ep + [hours, hours, hours, hours, hours, hours] + cp + [hours * 2])
        now_n, prev_n, n_ips, n_ids, first, last = cur.fetchone()
        stats = {"events": now_n, "previous": prev_n, "unique_ips": n_ips, "accounts": n_ids,
                 "first": iso(first), "last": iso(last),
                 "change_pct": (round(100.0 * (now_n - prev_n) / prev_n) if prev_n else None)}

        win = base + cur_w
        cur.execute(ev + "SELECT event_type, COUNT(*)" + win + " GROUP BY 1 ORDER BY 2 DESC LIMIT 30", ep + cp + [hours])
        types = [{"event_type": et, "label": tax.classify(et)["label"], "count": c} for et, c in cur.fetchall()]
        cur.execute(ev + "SELECT level, COUNT(*)" + win + " GROUP BY 1", ep + cp + [hours])
        levels = dict(cur.fetchall())
        cur.execute(ev + "SELECT ip, COUNT(*), COUNT(DISTINCT event_type), MAX(created_at), "
                    "COUNT(DISTINCT identifier) FILTER (WHERE identifier NOT IN ('', 'None'))"
                    + win + " AND ip IS NOT NULL GROUP BY ip ORDER BY 2 DESC LIMIT 15", ep + cp + [hours])
        ip_rows = cur.fetchall()
        banned = _banned_set(cur, [r[0] for r in ip_rows])
        cur.execute(ev + "SELECT identifier, COUNT(*), COUNT(DISTINCT ip), MAX(created_at)" + win +
                    " AND identifier IS NOT NULL AND identifier NOT IN ('', 'None') GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
                    ep + cp + [hours])
        accounts = [{"identifier": i, "events": c, "ips": n, "last": iso(ts)} for i, c, n, ts in cur.fetchall()]
        cur.execute(ev + "SELECT path, COUNT(*)" + win + " AND path IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
                    ep + cp + [hours])
        paths = [{"path": p, "count": c} for p, c in cur.fetchall()]
        cur.execute(ev + "SELECT " + EV_COLS + win + " ORDER BY id DESC LIMIT 15", ep + cp + [hours])
        recent = [shape_event(r) for r in cur.fetchall()]

    sources = [{"ip": ip, "events": c, "types": t, "accounts": ids, "last": iso(ts), "banned": ip in banned,
                "protected": _protected(ip)} for ip, c, t, ts, ids in ip_rows]
    if kind == "kpi" and value in ("failed_logins", "waf_blocks", "honeypot_hits") and types:
        filt = {"event_type": ",".join(t["event_type"] for t in types)}
    out = soc_playbooks.brief_for(kind, value)
    out.update({
        "kind": kind, "value": value, "hours": hours, "stats": stats, "levels": levels, "types": types,
        "sources": sources, "accounts": accounts, "paths": paths, "recent": recent,
        # None = nothing to open (the UI hides the links); {} = every event.
        "events_filter": None if filt is False else (filt or {}),
    })
    return out


# ── response state ───────────────────────────────────────────────────
BANS_LIMIT = 500
_BANS_SQL = ("SELECT ip, reason, banned_by, banned_at, expires_at, (expires_at IS NULL OR expires_at > NOW()) AS act "
             "FROM {table} ORDER BY act DESC, banned_at DESC LIMIT %s")


def banned_ips_table(schema):
    """Schema-qualified banned_ips. Qualified on purpose: a tenant connection's
    search_path is '"<tenant>", public', so a bare name in a schema without its
    own table would silently mean the platform table instead."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", schema or ""):
        raise ValueError("bad schema name")
    return f'"{schema}".banned_ips'


def _ban_rows(cur, schema, scope):
    cur.execute("SELECT to_regclass(%s)", (banned_ips_table(schema),))
    if cur.fetchone()[0] is None:
        return []
    cur.execute(_BANS_SQL.format(table=banned_ips_table(schema)), (BANS_LIMIT,))  # nosec B608 -- validated schema name
    return [{"ip": ip, "reason": r, "by": by, "at": iso(at), "expires": iso(ex), "active": bool(act), **scope}
            for ip, r, by, at, ex, act in cur.fetchall()]


def bans():
    """Platform-wide bans plus any left in a company's own banned_ips.

    Active bans come first: ordering by time alone let a pile of expired rows
    push still-enforced permanent bans off the list. Company-level rows are no
    longer written (utils/waf.ban_ip writes public.banned_ips only) but older
    ones are still enforced by app.py's ban hook, so they must be visible and
    liftable here. -> {"bans": [...], "errors": [...]}"""
    with read_cursor("public") as cur:
        rows = _ban_rows(cur, "public", {"tenant_id": None, "company": None})
        active_ips = _active_platform_bans(cur)

    def per_tenant(t, cur):
        if t["schema"] == "public":         # same table as the platform list above
            return None
        return _ban_rows(cur, t["schema"], {"tenant_id": t["id"], "company": t["company"]}) or None
    groups, errors = each_tenant(per_tenant)
    tenant_rows = [b for g in groups for b in g]
    active_ips += [b["ip"] for b in tenant_rows if b["active"]]
    rows += tenant_rows
    for b in rows:
        b["testing"] = soc_scope.is_test_ip(b["ip"])          # listed (and liftable), not counted
    rows.sort(key=lambda b: b["at"] or "", reverse=True)    # newest first ...
    rows.sort(key=lambda b: not b["active"])                # ... within active, then expired (stable)
    active_total, testing = _count_bans(active_ips)
    return {"bans": rows[:BANS_LIMIT], "active_total": active_total, "testing_active": testing, "errors": errors}


def _active_platform_bans(cur):
    cur.execute("SELECT ip FROM public.banned_ips WHERE expires_at IS NULL OR expires_at > NOW()")
    return [ip for (ip,) in cur.fetchall()]


def _count_bans(active_ips):
    """-> (counted, testing). A ban on one of your own SOC_TEST_CIDRS addresses is
    real and stays listed so it can be lifted, but it is your testing, not an
    attacker stopped, so it is counted apart (as the Overview does)."""
    testing = sum(1 for ip in active_ips if soc_scope.is_test_ip(ip))
    return len(active_ips) - testing, testing


# ── cases ────────────────────────────────────────────────────────────
_CASE_COLS = "id, title, severity, status, assignee, source_type, source_event_id, summary, resolution, created_by, created_at, updated_at, closed_at"


def _case(row):
    keys = ("id", "title", "severity", "status", "assignee", "source_type", "source_event_id", "summary", "resolution",
            "created_by", "created_at", "updated_at", "closed_at")
    return {k: iso(v) for k, v in zip(keys, row)}


def cases(status=None, limit=200, assignee=None):
    where, params = [], []
    if status == "open":
        where.append("status IN ('open','investigating','contained')")
    elif status:
        where.append("status=%s")
        params.append(status)
    if assignee:
        where.append("assignee=%s")
        params.append(assignee[:60])
    sql = ("SELECT " + _CASE_COLS + " FROM soc_cases" + (" WHERE " + " AND ".join(where) if where else "") +  # nosec B608 -- fixed clauses
           " ORDER BY updated_at DESC LIMIT %s")
    with read_cursor("att_master") as cur:
        cur.execute(sql, params + [clamp_int(limit, 200, 1, 500)])
        return [_case(r) for r in cur.fetchall()]


def assignees():
    """Active SOC users who can work a case (analyst or admin)."""
    with read_cursor("att_master") as cur:
        cur.execute("SELECT username FROM soc_users WHERE active AND role IN ('analyst','admin') ORDER BY username")
        return [u for (u,) in cur.fetchall()]


def detection_case_outcomes():
    """How cases opened from each detection rule ended. -> {rule: {...}}.

    A rule whose closed cases are mostly 'false_positive' needs its threshold
    tuned; this is the evidence. Cases live in att_master and events in public,
    so the two are joined here rather than in SQL."""
    with read_cursor("att_master") as cur:
        cur.execute("SELECT source_event_id, status FROM soc_cases WHERE source_event_id IS NOT NULL "
                    "ORDER BY id DESC LIMIT 5000")
        by_event = cur.fetchall()
    if not by_event:
        return {}
    with read_cursor("public") as cur:
        cur.execute("SELECT id, event_type FROM security_events WHERE id = ANY(%s) AND event_type LIKE 'detection.%%'",
                    ([e for e, _ in by_event],))
        rule_of = {i: et.split(".", 1)[1] for i, et in cur.fetchall()}
    out = {}
    for eid, status in by_event:
        rule = rule_of.get(eid)
        if not rule:
            continue
        o = out.setdefault(rule, {"cases": 0, "open": 0, "confirmed": 0, "false_positive": 0})
        o["cases"] += 1
        if status == "false_positive":
            o["false_positive"] += 1
        elif status == "resolved":
            o["confirmed"] += 1
        else:
            o["open"] += 1
    for o in out.values():
        closed = o["confirmed"] + o["false_positive"]
        o["fp_rate"] = round(100.0 * o["false_positive"] / closed) if closed else None
        # Only call it noisy with enough closed cases to mean something.
        o["needs_tuning"] = bool(closed >= 3 and o["fp_rate"] >= 50)
    return out


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
    # Under the owner/runtime role split the runtime role has no access to
    # audit_chain_head (by design), so verify as the owner like
    # utils/audit_jobs.py and scripts/verify_audit_chain.py do. The block is a
    # single READ ONLY transaction, so the owner connection cannot write here.
    # The verifier streams (flat memory at any table size); what bounds a button
    # press is time, so the whole run gets the same budget as one statement.
    with owner_privileges(), read_cursor("public", timeout_ms) as cur:
        try:
            report = audit_chain.verify_table(cur, "public", "security_events",
                                              deadline=started + timeout_ms / 1000)
        except audit_chain.VerificationTimeout as exc:
            raise ValueError(f"The chain is too long to verify within {timeout_ms // 1000} s from the console "
                             f"({exc}). The scheduled check verifies all of it every night at 03:45; "
                             "scripts/verify_audit_chain.py runs it on demand.") from exc
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
        tenants = [t for t in list_tenants() if not soc_scope.is_test_company(t["slug"])]
        with read_cursor("public") as cur:
            ev, ep = soc_scope.events_cte(cur, hours)
            cur.execute(
                ev + "SELECT " + TENANT_SQL + " AS slug, COUNT(*), COUNT(*) FILTER (WHERE " + ALERT_SQL + ") "
                "FROM ev WHERE " + _WINDOW + " AND (NULLIF(tenant, '') IS NOT NULL OR path LIKE '/%%/%%') "
                "GROUP BY 1", ep + [hours])
            counts = {s: (c, a) for s, c, a in cur.fetchall()}
        with read_cursor("att_master") as cur:
            cur.execute("SELECT conflicting_tenant_id, COUNT(*) FROM tenant_duplicate_alerts WHERE acknowledged=0 GROUP BY 1")
            dupes = dict(cur.fetchall())
        out = []
        for t in tenants:
            ev, al = counts.get(t["slug"], (0, 0))
            out.append({"id": t["id"], "company": t["company"], "slug": t["slug"], "status": t["status"],
                        "billing_state": t["billing_state"], "events": ev, "alerts": al,
                        "duplicate_alerts": dupes.get(t["id"], 0), "created_at": iso(t["created_at"])})
        # Counts as recorded, no weighted score: most alerts first, then the
        # unacknowledged duplicate-signup alerts, then the most activity.
        out.sort(key=lambda x: (-x["alerts"], -x["duplicate_alerts"], -x["events"], x["company"].lower()))
        return {"hours": hours, "tenants": out, "note": "Each event counts for the company its request ran as "
                "(recorded with the event). Events from before that was recorded fall back to the company named "
                "in the request path; events of the platform itself belong to no company."}
    return _cached(("tenants", hours), build)


def attendance_integrity(days=7):
    days = clamp_int(days, 7, 1, 60)

    def build():
        def per_tenant(t, cur):
            if soc_scope.is_test_company(t["slug"]):       # your own testing (SOC_TEST_COMPANIES)
                return None
            # Default company stores its tables in public.
            # Other tenants must have their own schema-specific table.
            default_schema = os.environ.get("DB_NAME", "employee_attendance")
            evidence_schema = (
                "public" if t["schema"] == default_schema
                else t["schema"]
            )

            # Without its own table, the bare name below would resolve to
            # public.punch_evidence and count the default company's punches again.
            # (read_cursor already validated the schema name.)
            cur.execute("SELECT to_regclass(%s)", (f'"{evidence_schema}".punch_evidence',))
            if cur.fetchone()[0] is None:
                return None
            win = "created_at >= NOW() - (%s * INTERVAL '1 day')"
            # Your own testing, as everywhere: punches by a SOC_TEST_ACCOUNTS
            # employee ID or from a SOC_TEST_CIDRS address. (Not private
            # addresses: behind an untrusted proxy every real punch has one.)
            accounts = soc_scope.test_accounts()
            test_ips = []
            if soc_scope.test_networks():
                cur.execute("SELECT DISTINCT ip FROM punch_evidence WHERE ip IS NOT NULL AND " + win, (days,))
                test_ips = [ip for (ip,) in cur.fetchall() if soc_scope.is_test_ip(ip)]
            mine = "(lower(employee_id) = ANY(%s) OR COALESCE(ip, '') = ANY(%s))"
            keep, kp = win + " AND NOT " + mine, [days, accounts, test_ips]
            cur.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE outcome <> 'accepted'), COUNT(*) FILTER (WHERE mocked), "
                "COUNT(*) FILTER (WHERE flags IS NOT NULL AND flags <> ''), "
                # `liveness` is the outcome (passed / failed / challenge_invalid / missing /
                # not_required); `liveness_action` is only the challenge asked (blink, turn...).
                "COUNT(*) FILTER (WHERE liveness IN ('failed','challenge_invalid','missing')) "
                "FROM punch_evidence WHERE " + keep, kp)
            total, rejected, mocked, flagged, live = cur.fetchone()
            # Every flagged punch, counted in the database (not a sample of rows).
            cur.execute("SELECT trim(f), COUNT(*) FROM punch_evidence, unnest(string_to_array(flags, ',')) AS f "
                        "WHERE flags IS NOT NULL AND flags <> '' AND " + keep + " GROUP BY 1", kp)
            flags = {f: n for f, n in cur.fetchall() if f}
            cur.execute("SELECT COUNT(*) FROM punch_evidence WHERE " + win + " AND " + mine, kp)
            testing = cur.fetchone()[0]
            if not total and not testing:
                return None
            return {"id": t["id"], "company": t["company"], "slug": t["slug"], "punches": total, "rejected": rejected,
                    "mock_location": mocked, "flagged": flagged, "liveness_issues": live, "flags": flags,
                    "testing": testing}
        rows, errors = each_tenant(per_tenant)
        testing = sum(r.pop("testing") for r in rows)
        rows = [r for r in rows if r["punches"]]
        agg = {}
        for r in rows:
            for k, v in r["flags"].items():
                agg[k] = agg.get(k, 0) + v
        rows.sort(key=lambda r: (-(r["flagged"] + r["rejected"]), r["company"].lower()))
        flags = sorted(({"flag": k, "count": v} for k, v in agg.items()), key=lambda x: -x["count"])
        totals = {k: sum(r[k] for r in rows) for k in ("punches", "rejected", "mock_location", "flagged")}
        totals["testing"] = testing                 # left out, so the page can say so
        return {"days": days, "tenants": rows, "flags": flags, "errors": errors, "totals": totals}
    return _cached(("attendance", days), build)


def risky_sessions():
    """Not cached: a revoke clears the cache only in the worker that handled it,
    so a cached copy in another worker would keep showing the session as live.
    The view loads on demand (it does not poll), so there is no refresh storm."""
    # A plain logout ('revoked', score 0) isn't risky. Your test accounts are left
    # out in the query, so they can never push real sessions off the list.
    risky = ("(status NOT IN ('active', 'revoked') OR score > 0) "
             "AND lower(identifier) <> ALL(%s)")

    def build():
        accounts = soc_scope.test_accounts()

        def per_tenant(t, cur):
            if soc_scope.is_test_company(t["slug"]):       # your own testing (SOC_TEST_COMPANIES)
                return None
            cur.execute("SELECT COUNT(*) FROM session_risk WHERE " + risky, (accounts,))
            count = cur.fetchone()[0]
            if not count:
                return None
            cur.execute("SELECT sid, identifier, attempt_type, score, status, last_reason, updated_at FROM session_risk "
                        "WHERE " + risky + " ORDER BY updated_at DESC LIMIT %s", (accounts, SESSIONS_LIMIT))
            return count, [{"tenant_id": t["id"], "company": t["company"], "slug": t["slug"],
                            "sid_hint": sid[:6] + "…" + sid[-4:], "sid": sid, "identifier": ident, "type": typ,
                            "score": score, "status": status, "reason": reason, "updated_at": iso(ts)}
                           for sid, ident, typ, score, status, reason, ts in cur.fetchall()]
        groups, errors = each_tenant(per_tenant)
        flat = [s for _, g in groups for s in g]
        flat.sort(key=lambda s: s["updated_at"] or "", reverse=True)
        # The newest SESSIONS_LIMIT across companies, and how many there are in all.
        return {"sessions": flat[:SESSIONS_LIMIT], "total": sum(n for n, _ in groups), "errors": errors}
    return build()


SESSIONS_LIMIT = 200


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
    from utils.soc_auth import allow_list_state, login_emails
    socnet = {
        "restricted": ("pass", "SOC_ALLOWED_CIDRS restricts who can reach /socperson."),
        "everywhere": ("warn", "SOC_ALLOWED_CIDRS opens /socperson to every address; an emailed code is the only barrier."),
        "invalid": ("fail", "SOC_ALLOWED_CIDRS cannot be parsed, so /socperson refuses everyone."),
        "unset_open": ("warn", "SOC_ALLOWED_CIDRS is not set: open here only because APP_ENV=development. "
                               "Any other environment keeps /socperson closed until it is set."),
        "unset_closed": ("fail", "SOC_ALLOWED_CIDRS is not set, so /socperson is closed to every address."),
    }[allow_list_state()]
    add("socnet", "SOC console network allow-list", socnet[0], socnet[1],
        "Set SOC_ALLOWED_CIDRS to your office / VPN ranges (0.0.0.0/0,::/0 opens it to everyone on purpose).")
    logins = len(login_emails())
    add("soclogin", "SOC console sign-in addresses", "pass" if logins else "fail",
        f"{logins} address(es) in SOC_LOGIN_EMAILS can request a sign-in code." if logins
        else "SOC_LOGIN_EMAILS lists no valid address, so nobody can sign in to /socperson.",
        "List the people who may sign in: SOC_LOGIN_EMAILS=you@example.com,colleague@example.com:analyst.")
    from utils import soc_passkeys
    pk_on = soc_passkeys.required()
    add("socpasskey", "SOC console passkey step", "pass" if pk_on else "fail",
        "Sign-in needs the emailed code and then the person's passkey." if pk_on
        else "SOC_PASSKEY=off: the emailed code alone opens the console, so whoever controls a listed inbox can sign in.",
        "Leave SOC_PASSKEY unset (on). Use off only briefly, to let a sole admin who lost their passkey back in.")
    # Alert delivery. With no channel, utils/alerts.send_security_alert skips
    # silently, so this is the one place that can say nobody is being paged.
    # Never shows the webhook URL (it is the credential) or the addresses.
    from utils import alerts as _alerts
    hook = _alerts._WEBHOOK_URL
    hook_ok = hook.startswith(("https://", "http://"))
    n_mail = len(_alerts._alert_recipients())
    mail_txt = f"email to {n_mail} recipient(s)" if n_mail else ""
    alert_fix = "Set SECURITY_ALERT_WEBHOOK_URL (Slack/Discord) and/or SECURITY_ALERT_EMAIL; see .env.example."
    if hook and not hook_ok:
        add("alerts", "Security alert delivery", "warn",
            "SECURITY_ALERT_WEBHOOK_URL does not start with http:// or https://, so webhook alerts are refused"
            + (f"; {mail_txt} still works." if n_mail else "."), alert_fix)
    elif hook_ok or n_mail:
        via = " and ".join(x for x in ("webhook" if hook_ok else "", mail_txt) if x)
        add("alerts", "Security alert delivery", "pass",
            f"ERROR / CRITICAL events and detections are sent by {via}.", alert_fix)
    else:
        add("alerts", "Security alert delivery", "warn",
            "No alert channel is configured: ERROR / CRITICAL events and detections only appear in this console.",
            alert_fix)
    add("anchors", "Audit-log anchoring", "info", "Hash-chain heads are written to stdout for off-box shipping.",
        "Confirm Alloy / Promtail ships them to Loki (observability/).")
    n_nets, n_accts, n_cos = (len(soc_scope.test_networks()), len(soc_scope.test_accounts()),
                              len(soc_scope.test_companies()))
    add("scope", "Genuine activity only", "info",
        "The console leaves out the server's own addresses, its own sign-ins and actions, and your testing: "
        f"{n_nets} address range(s), {n_accts} account(s) and {n_cos} company(ies) listed. "
        "No address is sent to any outside lookup service.",
        "List what you test with in SOC_TEST_CIDRS, SOC_TEST_ACCOUNTS and SOC_TEST_COMPANIES.")
    _loki_raw = os.environ.get("LOKI_URL", "").strip()
    if _loki_base():
        add("loki", "Log pipeline (Loki)", "pass", "LOKI_URL is set; the Logs view can query it.",
            "Set LOKI_URL to query shipped logs from the console.")
    elif _loki_raw:
        add("loki", "Log pipeline (Loki)", "warn",
            "LOKI_URL is set but does not start with http:// or https://, so the Logs view is disabled.",
            "Include the scheme, e.g. LOKI_URL=http://loki:3100.")
    else:
        add("loki", "Log pipeline (Loki)", "info", "LOKI_URL is not set; the Logs view is disabled.",
            "Set LOKI_URL to query shipped logs from the console.")
    order = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
    checks.sort(key=lambda c: order[c["status"]])
    return {"checks": checks, "summary": {s: sum(1 for c in checks if c["status"] == s) for s in order}}


# ── Loki (optional) ──────────────────────────────────────────────────
def _loki_base():
    """LOKI_URL without a trailing slash, or "" when unset or not http(s).
    The status check and the query path both use this, so the console never
    shows the Logs view as enabled for a URL the query would refuse."""
    base = os.environ.get("LOKI_URL", "").strip().rstrip("/")
    return base if base.startswith(("http://", "https://")) else ""


def loki_status():
    return {"configured": bool(_loki_base())}


def loki_query(query, minutes=60, limit=200):
    """Run a LogQL range query against the configured Loki. Read-only, bounded,
    and the target URL comes only from the LOKI_URL environment variable (never
    from the request), so it cannot be turned into an SSRF primitive."""
    import urllib.parse
    import urllib.request
    base = _loki_base()
    if not base:
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
