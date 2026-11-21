# -*- coding: utf-8 -*-
"""The SOC console's live feed: GET /socperson/api/stream (Server-Sent Events).

Each response is short: it carries the security events recorded since the last
one the browser received, the header status (threat level, alert and case
counts) and the "right now" numbers, then ends with `retry: 5000`, so the
browser's EventSource reconnects five seconds later and sends back the last
event id it saw (Last-Event-ID). A response never stays open: the app runs on
a few gthread worker threads, and a held-open stream per console tab would
tie one up for good (the same reason /api/session/risk-stream answers at once,
QA SCALE-08).

Events come from the same "genuine activity only" query as the Events view
(utils.soc_queries.search_events), so nothing the console leaves out elsewhere
appears here. Because separate workers write events in the background, one
with a lower id can commit just after a higher one was read; each response
therefore looks OVERLAP_IDS ids back and the browser drops ids it already has.
"""
import json
import time

RETRY_MS = 5000
OVERLAP_IDS = 25
MAX_EVENTS = 60
KPI_TTL_SECONDS = 10

_kpi_cache = {"at": 0.0, "value": None}


def parse_last_id(value):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 <= n < 2 ** 31 else None


def kpis():
    """The live strip on the Overview: fixed 1 h / 24 h windows, counted the
    same way as the Overview's own numbers. Cached for KPI_TTL_SECONDS so many
    open tabs cost one set of queries."""
    now = time.monotonic()
    if _kpi_cache["value"] is not None and now - _kpi_cache["at"] < KPI_TTL_SECONDS:
        return _kpi_cache["value"]
    from utils import http_telemetry, soc_queries as q, soc_scope
    from utils.soc_db import read_cursor
    failed = q.KPI_SQL["failed_logins"][0]
    lockout = q.KPI_SQL["lockouts"][0]
    with read_cursor("public") as cur:
        ev, ep = soc_scope.events_cte(cur, 24)
        cur.execute(
            ev + "SELECT COUNT(*) FILTER (WHERE " + failed + " AND created_at >= NOW() - INTERVAL '1 hour'), "
            "COUNT(*) FILTER (WHERE " + failed + "), COUNT(*) FILTER (WHERE " + lockout + "), "
            "COUNT(*) FILTER (WHERE " + q.ALERT_SQL + " AND created_at >= NOW() - INTERVAL '1 hour'), "
            "COUNT(*) FILTER (WHERE " + q.ALERT_SQL + ") "
            "FROM ev WHERE created_at >= NOW() - INTERVAL '24 hours'", ep)  # nosec B608 -- constant SQL fragments
        failed_1h, failed_24h, lockouts_24h, alerts_1h, alerts_24h = cur.fetchone()
        ips = q._active_platform_bans(cur) + [ip for ip, n in q._tenant_active_bans().items() for _ in range(n)]
    blocked, blocked_testing = q._count_bans(ips)
    with read_cursor("att_master") as cur:
        source = http_telemetry.source_status(cur)
        cur.execute("SELECT COALESCE(SUM(hits) FILTER (WHERE status = 401), 0), "
                    "COALESCE(SUM(hits) FILTER (WHERE status = 403), 0) "
                    "FROM http_minute_stats WHERE minute >= NOW() - INTERVAL '1 hour'")
        s401, s403 = cur.fetchone()
    from utils import session_registry
    value = {
        "signed_in": session_registry.count_signed_in(),
        "failed_logins_1h": failed_1h, "failed_logins_24h": failed_24h, "lockouts_24h": lockouts_24h,
        "alerts_1h": alerts_1h, "alerts_24h": alerts_24h,
        "blocked_ips": blocked, "blocked_ips_testing": blocked_testing,
        "denied_1h": {"s401": int(s401), "s403": int(s403), "source": source["state"], "last_write": source["last_write"]},
    }
    _kpi_cache.update(at=now, value=value)
    return value


def _max_event_id():
    from utils.soc_db import read_cursor
    with read_cursor("public") as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM security_events")
        return int(cur.fetchone()[0])


def stream_body(last_id):
    """One response of the stream. `last_id` is the newest event id the
    browser has (None on its first connection: it gets no backlog, since the
    page loaded its own data)."""
    from utils import soc_queries
    newest = _max_event_id()
    parts = [f"retry: {RETRY_MS}\n\n"]
    if last_id is not None and newest > last_id:
        found = soc_queries.search_events(since=max(0, last_id - OVERLAP_IDS), limit=MAX_EVENTS, hours=24)
        events = found["events"]
        if events:
            payload = {"events": events, "more": bool(found["next_cursor"])}
            parts.append(f"id: {newest}\nevent: events\ndata: {json.dumps(payload, default=str)}\n\n")
    pulse = soc_queries.pulse()
    pulse["kpis"] = kpis()
    seen = max(newest, last_id or 0)
    parts.append(f"id: {seen}\nevent: pulse\ndata: {json.dumps(pulse, default=str)}\n\n")
    return "".join(parts)
