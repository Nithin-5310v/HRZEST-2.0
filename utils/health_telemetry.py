# -*- coding: utf-8 -*-
"""Server, database and app-service health for the SOC console's Health view.

Once a minute (wsgi.py's scheduler; one worker per minute through the same
claim every scheduled job uses) take_snapshot() reads three sources and stores
one row per source in att_master.health_snapshots:

  host      CPU busy % (two /proc/stat reads one second apart), load average,
            memory and swap (/proc/meminfo), uptime, and disk use of the root
            and app filesystems. Needs Linux /proc for everything but disks; on
            another OS the snapshot says which readings it could not take.
  postgres  round-trip time of SELECT 1, connections against max_connections,
            active and idle-in-transaction sessions, the longest running query
            and open transaction, waiting locks, database size, and the
            cumulative counters of pg_stat_database (commits, rollbacks,
            deadlocks, cache hits) that the view turns into rates.
  app       the app service: how long the gunicorn master (the service) and
            the snapshot's worker have been up, that worker's memory and threads.

Every number is a reading, never an estimate. A source that fails is stored
with ok = false and a short, generic reason (no connection strings or paths);
one that stops being read shows as offline once its newest row is older than
OFFLINE_AFTER_SECONDS.
"""
import contextlib
import json
import os
import platform
import shutil
import threading
import time

from extensions import app_log

SOURCES = ("host", "postgres", "app")
OFFLINE_AFTER_SECONDS = 180
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def retention_days():
    try:
        return max(1, int(os.environ.get("HEALTH_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


# ── host ─────────────────────────────────────────────────────────────────────
def _cpu_times(proc):
    text = _read(os.path.join(proc, "stat"))
    if not text:
        return None
    first = text.splitlines()[0].split()
    if not first or first[0] != "cpu":
        return None
    values = [int(v) for v in first[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)       # idle + iowait
    total = sum(values[:8])                                           # guest time is already in user
    return idle, total


def cpu_percent(before, after):
    """Busy share of all CPUs between two (idle, total) readings, or None."""
    if not before or not after:
        return None
    d_total = after[1] - before[1]
    if d_total <= 0:
        return None
    return round(100.0 * (1 - (after[0] - before[0]) / d_total), 1)


def _meminfo(proc):
    text = _read(os.path.join(proc, "meminfo"))
    if not text:
        return None
    kb = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            kb[name.strip()] = int(parts[0]) * 1024
    if "MemTotal" not in kb or "MemAvailable" not in kb:
        return None
    total, available = kb["MemTotal"], kb["MemAvailable"]
    out = {"mem_total": total, "mem_available": available,
           "mem_used_percent": round(100.0 * (total - available) / total, 1) if total else None}
    if "SwapTotal" in kb and "SwapFree" in kb:
        out.update(swap_total=kb["SwapTotal"], swap_used=kb["SwapTotal"] - kb["SwapFree"])
    return out


def _disks(paths):
    out, seen = [], set()
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        key = (usage.total, usage.free)
        if key in seen:                       # same filesystem as one already listed
            continue
        seen.add(key)
        out.append({"path": path, "total": usage.total, "used": usage.used, "free": usage.free,
                    "used_percent": round(100.0 * usage.used / usage.total, 1) if usage.total else None})
    return out


def read_host(proc="/proc", disk_paths=None, cpu_interval=1.0):
    metrics = {"cpu_count": os.cpu_count()}
    missing = []
    if os.path.isdir(proc):
        before = _cpu_times(proc)
        time.sleep(cpu_interval)
        cpu = cpu_percent(before, _cpu_times(proc))
        if cpu is None:
            missing.append("cpu")
        else:
            metrics["cpu_percent"] = cpu
        load = (_read(os.path.join(proc, "loadavg")) or "").split()
        if len(load) >= 3:
            metrics.update(load1=float(load[0]), load5=float(load[1]), load15=float(load[2]))
        else:
            missing.append("load")
        mem = _meminfo(proc)
        if mem:
            metrics.update(mem)
        else:
            missing.append("memory")
        uptime = (_read(os.path.join(proc, "uptime")) or "").split()
        if uptime:
            metrics["uptime_seconds"] = int(float(uptime[0]))
        else:
            missing.append("uptime")
    else:
        missing += ["cpu", "load", "memory", "uptime"]
    metrics["disks"] = _disks(disk_paths or ["/", ROOT])
    if missing:
        metrics["missing"] = missing
        metrics["missing_reason"] = ("this server has no Linux /proc" if not os.path.isdir(proc)
                                     else "not readable in /proc on this server")
    return metrics


# ── app service ──────────────────────────────────────────────────────────────
def _started_at(proc, pid):
    """Unix time a process started, from /proc (Linux), or None."""
    stat = _read(os.path.join(proc, str(pid), "stat"))
    boot = _read(os.path.join(proc, "stat"))
    if not stat or not boot:
        return None
    try:
        fields = stat.rsplit(")", 1)[1].split()
        start_ticks = int(fields[19])                       # field 22 of /proc/<pid>/stat
        btime = next(int(line.split()[1]) for line in boot.splitlines() if line.startswith("btime "))
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except (IndexError, ValueError, StopIteration, AttributeError, OSError):
        return None


def read_app(proc="/proc"):
    metrics = {"pid": os.getpid(), "threads": threading.active_count(), "python": platform.python_version()}
    status = _read(os.path.join(proc, "self", "status")) or ""
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            metrics["rss_bytes"] = int(line.split()[1]) * 1024
    now = time.time()
    worker_start = _started_at(proc, os.getpid())
    if worker_start:
        metrics["worker_uptime_seconds"] = int(now - worker_start)
    parent = os.getppid()
    cmdline = (_read(os.path.join(proc, str(parent), "cmdline")) or "").replace("\0", " ")
    if "gunicorn" in cmdline:
        metrics["server"] = "gunicorn"
        service_start = _started_at(proc, parent)
    else:
        metrics["server"] = "single process"
        service_start = worker_start
    if service_start:
        metrics["service_uptime_seconds"] = int(now - service_start)
    if not os.path.isdir(proc):
        metrics["missing"] = ["memory", "uptime"]
        metrics["missing_reason"] = "this server has no Linux /proc"
    return metrics


# ── postgres ─────────────────────────────────────────────────────────────────
def read_postgres(cur):
    """Readings from the app's own database connection. No parameters are
    passed to execute(), so '%' in these statements is literal."""
    t0 = time.perf_counter()
    cur.execute("SELECT 1")
    cur.fetchone()
    metrics = {"latency_ms": round((time.perf_counter() - t0) * 1000.0, 2)}
    cur.execute("SELECT current_setting('max_connections')::int, pg_is_in_recovery(), "
                "pg_database_size(current_database()), EXTRACT(EPOCH FROM NOW() - pg_postmaster_start_time()), "
                "pg_has_role(current_user, 'pg_read_all_stats', 'MEMBER')")
    maxc, recovery, size, up, all_stats = cur.fetchone()
    metrics.update(max_connections=maxc, in_recovery=bool(recovery), db_size_bytes=int(size),
                   server_uptime_seconds=int(up), sees_all_sessions=bool(all_stats))
    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE state = 'active'), "
        "count(*) FILTER (WHERE state LIKE 'idle in transaction%'), "
        "COALESCE(EXTRACT(EPOCH FROM MAX(NOW() - query_start) FILTER (WHERE state = 'active' AND pid <> pg_backend_pid())), 0), "
        "COALESCE(EXTRACT(EPOCH FROM MAX(NOW() - xact_start) FILTER (WHERE pid <> pg_backend_pid())), 0) "
        "FROM pg_stat_activity WHERE backend_type = 'client backend'")
    conns, active, idle_tx, longest_q, longest_tx = cur.fetchone()
    metrics.update(connections=int(conns), active=int(active), idle_in_transaction=int(idle_tx),
                   longest_query_seconds=round(float(longest_q), 1), longest_transaction_seconds=round(float(longest_tx), 1))
    cur.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
    metrics["waiting_locks"] = int(cur.fetchone()[0])
    cur.execute("SELECT xact_commit, xact_rollback, deadlocks, blks_hit, blks_read, temp_bytes "
                "FROM pg_stat_database WHERE datname = current_database()")
    row = cur.fetchone()
    if row:
        for key, value in zip(("xact_commit", "xact_rollback", "deadlocks", "blks_hit", "blks_read", "temp_bytes"), row):
            metrics[key] = int(value or 0)
    return metrics


# ── snapshot ─────────────────────────────────────────────────────────────────
def _generic_error(source, exc):
    """What the console shows when a source fails: the kind of failure, never
    its message (which can carry hosts, paths or connection details)."""
    what = {"host": "read the server's readings", "postgres": "query PostgreSQL", "app": "read the app service"}[source]
    return f"Could not {what} ({type(exc).__name__})"[:300]


def take_snapshot(host_reader=None, app_reader=None):
    """Read every source and store one row each. -> {source: ok}"""
    from database import get_master_db
    import socket
    results = []
    for source, reader in (("host", host_reader or read_host), ("app", app_reader or read_app)):
        try:
            results.append((source, True, reader(), None))
        except Exception as exc:
            app_log.warning("health snapshot: %s failed", source, exc_info=True)
            results.append((source, False, {}, _generic_error(source, exc)))
    db = get_master_db()
    cur = db.cursor()
    try:
        try:
            results.append(("postgres", True, read_postgres(cur), None))
        except Exception as exc:
            app_log.warning("health snapshot: postgres failed", exc_info=True)
            with contextlib.suppress(Exception):
                db.rollback()
            results.append(("postgres", False, {}, _generic_error("postgres", exc)))
        host = socket.gethostname()[:64]
        for source, ok, metrics, error in results:
            cur.execute("INSERT INTO health_snapshots (host, source, ok, metrics, error) VALUES (%s,%s,%s,%s,%s)",
                        (host, source, ok, json.dumps(metrics), error))
        db.commit()
    finally:
        cur.close()
        db.close()
    return {source: ok for source, ok, _, _ in results}


def purge_old():
    """Scheduled daily: drop snapshots past HEALTH_RETENTION_DAYS, and the
    scheduler's per-run claim rows older than a week (the every-minute snapshot
    job adds 1,440 of them a day; nothing else ever removed them)."""
    from database import get_master_db
    db = get_master_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM health_snapshots WHERE taken_at < NOW() - (%s * INTERVAL '1 day')", (retention_days(),))
        removed = cur.rowcount
        cur.execute("DELETE FROM scheduler_job_runs WHERE fire_minute < (NOW() - INTERVAL '7 days')::timestamp")
        db.commit()
        return removed
    finally:
        cur.close()
        db.close()


# ── reading it back (the SOC console's Health view) ──────────────────────────
WINDOWS = (1, 6, 24, 72, 168, 720)
STEPS = {1: 60, 6: 300, 24: 900, 72: 3600, 168: 3 * 3600, 720: 12 * 3600}


def hours_arg(value, default=24):
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return default
    return hours if hours in WINDOWS else default


@contextlib.contextmanager
def _read_cursor(timeout_ms=15000):
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


def _iso(ts):
    """The database's own wall clock with no zone, like every console timestamp."""
    return ts.strftime("%Y-%m-%d %H:%M:%S") if ts is not None else None


def _num(v, digits=1):
    return None if v is None else round(float(v), digits)


_BUCKET = "to_timestamp(floor(extract(epoch FROM taken_at) / %s) * %s)::timestamp"


def health(hours=None):
    """Everything the Health view shows, for the last `hours`."""
    hours = hours_arg(hours)
    step = STEPS[hours]
    with _read_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (source) source, taken_at::timestamp, ok, metrics, error, host, "
            "EXTRACT(EPOCH FROM NOW() - taken_at) FROM health_snapshots ORDER BY source, taken_at DESC")
        latest = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute("SELECT source, MAX(taken_at)::timestamp FROM health_snapshots WHERE ok GROUP BY source")
        last_ok = {s: _iso(t) for s, t in cur.fetchall()}

        cur.execute(
            "SELECT " + _BUCKET + " AS b, AVG((metrics->>'cpu_percent')::float), MAX((metrics->>'cpu_percent')::float), "
            "AVG((metrics->>'mem_used_percent')::float), AVG((metrics->>'load1')::float), "
            "MAX((metrics->'disks'->0->>'used_percent')::float) "
            "FROM health_snapshots WHERE source = 'host' AND ok AND taken_at >= NOW() - (%s * INTERVAL '1 hour') "
            "GROUP BY b ORDER BY b", (step, step, hours))
        host_series = [{"t": _iso(b), "cpu_avg": _num(c), "cpu_max": _num(cm), "mem": _num(m), "load1": _num(load, 2),
                        "disk": _num(d)} for b, c, cm, m, load, d in cur.fetchall()]

        cur.execute(
            "WITH s AS (SELECT taken_at, (metrics->>'connections')::int AS conns, (metrics->>'latency_ms')::float AS lat, "
            "  (metrics->>'xact_commit')::bigint - LAG((metrics->>'xact_commit')::bigint) OVER w AS d_commit, "
            "  (metrics->>'xact_rollback')::bigint - LAG((metrics->>'xact_rollback')::bigint) OVER w AS d_rollback, "
            "  (metrics->>'deadlocks')::bigint - LAG((metrics->>'deadlocks')::bigint) OVER w AS d_deadlocks, "
            "  EXTRACT(EPOCH FROM taken_at - LAG(taken_at) OVER w) AS dt "
            "  FROM health_snapshots WHERE source = 'postgres' AND ok "
            "  AND taken_at >= NOW() - (%s * INTERVAL '1 hour') - INTERVAL '10 minutes' WINDOW w AS (ORDER BY taken_at)) "
            "SELECT " + _BUCKET + " AS b, AVG(conns), MAX(conns), AVG(lat), MAX(lat), "
            "SUM(d_commit) FILTER (WHERE d_commit >= 0) * 60.0 / NULLIF(SUM(dt) FILTER (WHERE d_commit >= 0), 0), "
            "SUM(d_rollback) FILTER (WHERE d_rollback >= 0) * 60.0 / NULLIF(SUM(dt) FILTER (WHERE d_rollback >= 0), 0), "
            "COALESCE(SUM(d_deadlocks) FILTER (WHERE d_deadlocks >= 0), 0) "
            "FROM s WHERE taken_at >= NOW() - (%s * INTERVAL '1 hour') GROUP BY b ORDER BY b",
            (hours, step, step, hours))
        db_series = [{"t": _iso(b), "conns_avg": _num(ca), "conns_max": int(cm or 0), "latency_avg": _num(la, 2),
                      "latency_max": _num(lm, 2), "commits_per_min": _num(cpm), "rollbacks_per_min": _num(rpm),
                      "deadlocks": int(dl or 0)} for b, ca, cm, la, lm, cpm, rpm, dl in cur.fetchall()]

    sources = {}
    for source in SOURCES:
        row = latest.get(source)
        if row is None:
            sources[source] = {"state": "never", "at": None, "last_ok": None, "error": None, "metrics": None}
            continue
        taken_at, ok, metrics, error, host, age = row
        state = "offline" if float(age) > OFFLINE_AFTER_SECONDS else ("ok" if ok else "error")
        sources[source] = {"state": state, "at": _iso(taken_at), "last_ok": last_ok.get(source), "error": error,
                           "host": host, "metrics": metrics if ok else None}

    pg = (sources["postgres"]["metrics"] or {})
    hit, read = pg.get("blks_hit"), pg.get("blks_read")
    window = {
        "deadlocks": sum(p["deadlocks"] for p in db_series),
        "cache_hit_percent": round(100.0 * hit / (hit + read), 2) if hit is not None and read is not None and hit + read else None,
    }
    return {"hours": hours, "step_seconds": step, "offline_after_seconds": OFFLINE_AFTER_SECONDS,
            "retention_days": retention_days(), "sources": sources, "host_series": host_series,
            "db_series": db_series, "window": window}
