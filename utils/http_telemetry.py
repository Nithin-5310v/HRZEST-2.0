# -*- coding: utf-8 -*-
"""Request telemetry: how many requests each route answered, with which
status, and how fast. Shown on the SOC console's Traffic view and read by the
http_error_spike detection (utils/detections.py).

How it is collected
-------------------
app_setup/telemetry.py times every request and calls record() with the
route's pattern (e.g. /view_payslip/<emp_id>/<int:year>/<int:month>, never
the raw URL, so the number of rows stays bounded and no IDs are stored), the
HTTP method, the status code, the company and the duration. Each worker
process adds those up per minute in memory, and a background thread writes
them every FLUSH_SECONDS into att_master.http_minute_stats, adding to what
other workers wrote for the same minute. Response times are kept as a
histogram (BUCKETS_MS), so percentiles are upper bounds such as "p95 <= 250
ms", never invented exact numbers.

Each write also records the time in att_master.http_telemetry_flushes, so
the console can tell "the site was quiet" from "the collector stopped": with
no write for OFFLINE_AFTER_SECONDS the source is shown as offline, with the
time of its last write.

Left out: this console's own requests, static files and health checks, the
same "genuine activity only" rule the rest of the console follows.
"""
import atexit
import bisect
import contextlib
import datetime
import math
import os
import socket
import threading
import time

from extensions import app_log

# Upper bounds (ms) of the histogram buckets; one more bucket holds anything slower.
BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500)
N_BUCKETS = len(BUCKETS_MS) + 1
FLUSH_SECONDS = 60
OFFLINE_AFTER_SECONDS = 3 * FLUSH_SECONDS
_MAX_PENDING_KEYS = 50000        # memory bound if the database is unreachable for a long time
_SKIP_PREFIXES = ("/static/", "/socperson", "/healthz", "/api/health", "/favicon.ico", "/robots.txt")
_NO_ROUTE = "(no matching route)"

_lock = threading.Lock()
_pending = {}
_flusher_pid = None


def enabled():
    return os.environ.get("HTTP_TELEMETRY", "on").strip().lower() not in ("off", "0", "false", "no")


def retention_days():
    try:
        return max(1, int(os.environ.get("HTTP_TELEMETRY_RETENTION_DAYS", "14")))
    except ValueError:
        return 14


def skipped(path):
    return any(path == p.rstrip("/") or path.startswith(p) for p in _SKIP_PREFIXES)


def bucket_index(ms):
    """Index of the histogram bucket for a duration: 0 for <= 5 ms ... the last for > 2500 ms."""
    return bisect.bisect_left(BUCKETS_MS, ms)


def _minute(ts):
    return datetime.datetime.fromtimestamp(int(ts // 60) * 60, tz=datetime.timezone.utc)


def record(endpoint, method, status, tenant, duration_ms, now=None):
    key = (_minute(time.time() if now is None else now), (endpoint or _NO_ROUTE)[:160],
           (method or "")[:8], int(status), (tenant or "")[:63])
    ms = max(0.0, float(duration_ms))
    with _lock:
        entry = _pending.get(key)
        if entry is None:
            if len(_pending) >= _MAX_PENDING_KEYS:
                return
            entry = _pending[key] = [0, 0, 0, [0] * N_BUCKETS]
        entry[0] += 1
        entry[1] += int(round(ms))
        entry[2] = max(entry[2], int(math.ceil(ms)))
        entry[3][bucket_index(ms)] += 1


def reset():
    """Drop counts not yet written (tests)."""
    with _lock:
        _pending.clear()


_UPSERT = (
    "INSERT INTO http_minute_stats (minute, endpoint, method, status, tenant, hits, total_ms, max_ms, buckets) "
    "VALUES %s "
    "ON CONFLICT (minute, endpoint, method, status, tenant) DO UPDATE SET "
    "hits = http_minute_stats.hits + EXCLUDED.hits, "
    "total_ms = http_minute_stats.total_ms + EXCLUDED.total_ms, "
    "max_ms = GREATEST(http_minute_stats.max_ms, EXCLUDED.max_ms), "
    "buckets = ARRAY(SELECT x.a + x.b FROM unnest(http_minute_stats.buckets, EXCLUDED.buckets) "
    "WITH ORDINALITY AS x(a, b, i) ORDER BY x.i)"
)


def flush():
    """Write the counts gathered since the last flush. -> rows written.
    Never raises: on failure the counts go back in memory for the next try."""
    global _pending
    with _lock:
        batch, _pending = _pending, {}
    try:
        from psycopg2.extras import execute_values
        from database import get_master_db
        db = get_master_db()
        cur = db.cursor()
        try:
            if batch:
                execute_values(cur, _UPSERT, [k + (v[0], v[1], v[2], v[3]) for k, v in batch.items()])
            cur.execute(
                "INSERT INTO http_telemetry_flushes (host, pid, flushed_at) VALUES (%s, %s, NOW()) "
                "ON CONFLICT (host, pid) DO UPDATE SET flushed_at = EXCLUDED.flushed_at",
                (socket.gethostname()[:64], os.getpid()))
            db.commit()
        finally:
            cur.close()
            db.close()
        return len(batch)
    except Exception as exc:
        with _lock:
            for key, value in batch.items():
                if key not in _pending and len(_pending) < _MAX_PENDING_KEYS:
                    _pending[key] = value
                elif key in _pending:
                    mine = _pending[key]
                    mine[0] += value[0]
                    mine[1] += value[1]
                    mine[2] = max(mine[2], value[2])
                    mine[3] = [a + b for a, b in zip(mine[3], value[3])]
        app_log.warning("http telemetry flush failed: %s", exc)
        return 0


def _flush_loop():
    while True:
        time.sleep(FLUSH_SECONDS)
        flush()


def ensure_flusher():
    """Start this process's writer thread once (gunicorn forks workers after
    import, so it is started from the first request each worker serves)."""
    global _flusher_pid
    pid = os.getpid()
    if _flusher_pid == pid:
        return
    with _lock:
        if _flusher_pid == pid:
            return
        _flusher_pid = pid
    threading.Thread(target=_flush_loop, name="http-telemetry-flush", daemon=True).start()
    atexit.register(flush)


def purge_old():
    """Scheduled daily: drop request stats older than the retention period,
    and writer records of processes gone for a day."""
    from database import get_master_db
    db = get_master_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM http_minute_stats WHERE minute < NOW() - (%s * INTERVAL '1 day')", (retention_days(),))
        removed = cur.rowcount
        cur.execute("DELETE FROM http_telemetry_flushes WHERE flushed_at < NOW() - INTERVAL '1 day'")
        db.commit()
        return removed
    finally:
        cur.close()
        db.close()


# ── reading it back (the SOC console's Traffic view) ─────────────────────────
WINDOWS = (1, 6, 24, 72, 168, 720)


def hours_arg(value, default=24):
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return default
    return hours if hours in WINDOWS else default


def _step_seconds(hours):
    return {1: 60, 6: 300, 24: 900, 72: 3600, 168: 3 * 3600, 720: 12 * 3600}[hours]


def percentile(hist, p, max_ms=None):
    """Upper bound of the p-th percentile from bucket counts.
    -> {"le": ms} (at most that many ms), {"gt": 2500, "max": ms} for the slowest
    bucket, or None when there are no requests."""
    total = sum(hist)
    if not total:
        return None
    target = math.ceil(p / 100.0 * total)
    running = 0
    for i, n in enumerate(hist):
        running += n
        if running >= target:
            if i < len(BUCKETS_MS):
                return {"le": BUCKETS_MS[i]}
            return {"gt": BUCKETS_MS[-1], "max": max_ms}
    return None


def _iso(ts):
    """The database's own wall clock with no zone: the form every other console
    timestamp takes (soc-core.js corrects it by the clock gap it measures).
    The queries below convert with ::timestamp, which uses the session's zone."""
    if ts is None:
        return None
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _status_class(code):
    return "5xx" if code >= 500 else "4xx" if code >= 400 else "ok"


def source_status(cur):
    cur.execute("SELECT MAX(flushed_at)::timestamp, EXTRACT(EPOCH FROM NOW() - MAX(flushed_at)) "
                "FROM http_telemetry_flushes")
    last, age_seconds = cur.fetchone()
    if last is None:
        return {"state": "never", "last_write": None}
    stale = float(age_seconds) > OFFLINE_AFTER_SECONDS
    return {"state": "offline" if stale else "ok", "last_write": _iso(last)}


@contextlib.contextmanager
def _read_cursor(timeout_ms=15000):
    """A read-only att_master cursor with a statement timeout, so a slow
    query can't hold a request thread."""
    from database import get_master_db
    db = get_master_db()
    cur = db.cursor()
    try:
        cur.execute("BEGIN READ ONLY")
        cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
        yield cur
    finally:
        try:
            cur.execute("ROLLBACK")
        finally:
            cur.close()
            db.close()


def traffic(hours=None):
    """Everything the Traffic view shows, for the last `hours`."""
    hours = hours_arg(hours)
    step = _step_seconds(hours)
    window = "minute >= NOW() - (%s * INTERVAL '1 hour')"
    with _read_cursor() as cur:
        source = source_status(cur)
        cur.execute("SELECT MAX(minute)::timestamp FROM http_minute_stats")
        last_minute = cur.fetchone()[0]

        cur.execute(
            "SELECT to_timestamp(floor(extract(epoch FROM minute) / %s) * %s)::timestamp AS b, status, SUM(hits), MAX(max_ms) "
            "FROM http_minute_stats WHERE " + window + " GROUP BY b, status ORDER BY b",
            (step, step, hours))
        series_rows = cur.fetchall()
        cur.execute(
            "SELECT to_timestamp(floor(extract(epoch FROM s.minute) / %s) * %s)::timestamp AS b, u.i, SUM(u.n) "
            "FROM http_minute_stats s, unnest(s.buckets) WITH ORDINALITY AS u(n, i) "
            "WHERE s." + window + " GROUP BY b, u.i",
            (step, step, hours))
        series_hist = cur.fetchall()

        cur.execute(
            "SELECT endpoint, method, SUM(hits), SUM(total_ms), MAX(max_ms), "
            "SUM(hits) FILTER (WHERE status = 401), SUM(hits) FILTER (WHERE status = 403), "
            "SUM(hits) FILTER (WHERE status = 404), SUM(hits) FILTER (WHERE status = 429), "
            "SUM(hits) FILTER (WHERE status >= 500), SUM(hits) FILTER (WHERE status >= 400) "
            "FROM http_minute_stats WHERE " + window + " GROUP BY endpoint, method "
            "ORDER BY SUM(hits) DESC, endpoint LIMIT 60",
            (hours,))
        endpoint_rows = cur.fetchall()
        cur.execute(
            "SELECT s.endpoint, s.method, u.i, SUM(u.n) "
            "FROM http_minute_stats s, unnest(s.buckets) WITH ORDINALITY AS u(n, i) "
            "WHERE s." + window + " GROUP BY s.endpoint, s.method, u.i",
            (hours,))
        endpoint_hist = cur.fetchall()

        cur.execute("SELECT status, SUM(hits) FROM http_minute_stats WHERE " + window +
                    " GROUP BY status ORDER BY status", (hours,))
        statuses = [{"status": s, "hits": int(n)} for s, n in cur.fetchall()]
        cur.execute("SELECT COALESCE(SUM(total_ms), 0), COALESCE(MAX(max_ms), 0) FROM http_minute_stats WHERE " + window,
                    (hours,))
        total_ms, max_ms = cur.fetchone()

    def _hist(rows, key_len):
        out = {}
        for row in rows:
            key, i, n = row[:key_len], row[key_len], row[key_len + 1]
            out.setdefault(key, [0] * N_BUCKETS)[int(i) - 1] += int(n)
        return out

    s_hist = _hist(series_hist, 1)
    buckets = {}
    for b, status, n, mx in series_rows:
        point = buckets.setdefault(b, {"t": _iso(b), "ok": 0, "4xx": 0, "5xx": 0, "max_ms": 0})
        point[_status_class(status)] += int(n)
        point["max_ms"] = max(point["max_ms"], int(mx or 0))
    series = []
    for b in sorted(buckets):
        point = buckets[b]
        point["p95"] = percentile(s_hist.get((b,), []), 95, point["max_ms"])
        series.append(point)

    e_hist = _hist(endpoint_hist, 2)
    endpoints = []
    for ep, method, hits, ms, mx, s401, s403, s404, s429, s5xx, s4xx in endpoint_rows:
        hist = e_hist.get((ep, method), [0] * N_BUCKETS)
        endpoints.append({
            "endpoint": ep, "method": method, "hits": int(hits), "avg_ms": round(int(ms) / int(hits)) if hits else None,
            "max_ms": int(mx or 0), "p50": percentile(hist, 50, mx), "p95": percentile(hist, 95, mx),
            "p99": percentile(hist, 99, mx), "s401": int(s401 or 0), "s403": int(s403 or 0), "s404": int(s404 or 0),
            "s429": int(s429 or 0), "s5xx": int(s5xx or 0), "errors": int(s4xx or 0)})

    total = sum(s["hits"] for s in statuses)
    by_class = {"ok": 0, "4xx": 0, "5xx": 0}
    for s in statuses:
        by_class[_status_class(s["status"])] += s["hits"]
    overall = [0] * N_BUCKETS
    for hist in s_hist.values():
        overall = [a + b for a, b in zip(overall, hist)]
    pick = {s["status"]: s["hits"] for s in statuses}
    return {
        "hours": hours, "step_seconds": step, "source": source, "last_request_minute": _iso(last_minute),
        "retention_days": retention_days(), "buckets_ms": list(BUCKETS_MS),
        "totals": {
            "requests": total, "ok": by_class["ok"], "client_errors": by_class["4xx"], "server_errors": by_class["5xx"],
            "s401": pick.get(401, 0), "s403": pick.get(403, 0), "s404": pick.get(404, 0), "s429": pick.get(429, 0),
            "avg_ms": round(int(total_ms) / total) if total else None, "max_ms": int(max_ms or 0),
            "p50": percentile(overall, 50, max_ms), "p95": percentile(overall, 95, max_ms),
            "p99": percentile(overall, 99, max_ms)},
        "series": series, "endpoints": endpoints, "statuses": statuses,
    }
