"""Server / database / app-service readings (utils/health_telemetry.py): parsing
Linux /proc, the PostgreSQL readings, storing a snapshot, failures that never
leak their message, the Health view's "never" / "offline" / "failed" states,
rates from cumulative counters, and the clean-up.
"""
import datetime
import json
import os

import pytest

from database import get_master_db
from tests import test_soc_console as _console
from tests.test_soc_console import make_user, sign_in
from utils import health_telemetry as ht

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches).
_fresh_soc_state = _console._fresh_soc_state

STAT_1 = "cpu  1000 0 500 8000 500 0 0 0 0 0\ncpu0 1000 0 500 8000 500 0 0 0 0 0\nbtime 1700000000\n"
STAT_2 = "cpu  1600 0 700 8600 600 0 0 0 0 0\ncpu0 1600 0 700 8600 600 0 0 0 0 0\nbtime 1700000000\n"
MEMINFO = "MemTotal:       8000000 kB\nMemFree:  1000000 kB\nMemAvailable:   2000000 kB\nSwapTotal: 1000000 kB\nSwapFree: 750000 kB\n"


class _SizeQueryFails:
    """A real cursor whose pg_database_size() query fails on the server, the way
    it can on Windows mid-drop: it asks for a database that doesn't exist."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, *args):
        if "pg_database_size" in sql:
            return self._cur.execute("SELECT pg_database_size('hrz_no_such_database')")
        return self._cur.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def _master(sql, params=None):
    db = get_master_db()
    cur = db.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else None
        db.commit()
        return rows
    finally:
        cur.close()
        db.close()


@pytest.fixture(autouse=True)
def _clean(_fresh_soc_state, client):
    _master("DELETE FROM health_snapshots")
    yield


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text(STAT_1)
    (proc / "loadavg").write_text("0.52 0.40 0.31 2/345 6789\n")
    (proc / "meminfo").write_text(MEMINFO)
    (proc / "uptime").write_text("98765.43 12345.67\n")
    # the second /proc/stat read comes after the one-second wait
    monkeypatch.setattr(ht.time, "sleep", lambda _s: (proc / "stat").write_text(STAT_2))
    return proc


def _insert(source, ok=True, metrics=None, error=None, minutes_ago=0):
    _master("INSERT INTO health_snapshots (taken_at, host, source, ok, metrics, error) "
            "VALUES (NOW() - (%s * INTERVAL '1 minute'), 'test-host', %s, %s, %s, %s)",
            (minutes_ago, source, ok, json.dumps(metrics or {}), error))


class TestReadings:
    def test_host_readings_from_proc(self, fake_proc, tmp_path):
        m = ht.read_host(proc=str(fake_proc), disk_paths=[str(tmp_path)])
        # 1500 more jiffies, 700 of them idle or iowait -> 53.3 % busy
        assert m["cpu_percent"] == 53.3
        assert (m["load1"], m["load5"], m["load15"]) == (0.52, 0.40, 0.31)
        assert m["mem_total"] == 8000000 * 1024 and m["mem_used_percent"] == 75.0
        assert m["swap_used"] == 250000 * 1024
        assert m["uptime_seconds"] == 98765
        [disk] = m["disks"]
        assert disk["path"] == str(tmp_path) and disk["total"] > 0 and 0 <= disk["used_percent"] <= 100
        assert "missing" not in m

    def test_without_proc_it_says_what_it_could_not_read(self, tmp_path):
        m = ht.read_host(proc=str(tmp_path / "no-proc-here"), disk_paths=[str(tmp_path)], cpu_interval=0)
        assert m["missing"] == ["cpu", "load", "memory", "uptime"]
        assert m["missing_reason"] == "this server has no Linux /proc"
        assert "cpu_percent" not in m and len(m["disks"]) == 1

    def test_the_same_filesystem_is_listed_once(self, tmp_path):
        assert len(ht._disks([str(tmp_path), str(tmp_path)])) == 1

    def test_cpu_percent_edge_cases(self):
        assert ht.cpu_percent(None, (1, 2)) is None
        assert ht.cpu_percent((5, 10), (5, 10)) is None        # no time passed
        assert ht.cpu_percent((0, 0), (0, 100)) == 100.0

    def test_postgres_readings(self, db_engine):
        cur = db_engine.cursor()
        m = ht.read_postgres(cur)
        cur.close()
        assert m["latency_ms"] >= 0 and m["max_connections"] > 0
        assert m["connections"] >= 1 and m["db_size_bytes"] > 0 and m["server_uptime_seconds"] >= 0
        for key in ("active", "idle_in_transaction", "waiting_locks", "xact_commit", "xact_rollback", "deadlocks",
                    "blks_hit", "blks_read", "longest_query_seconds", "longest_transaction_seconds"):
            assert isinstance(m[key], (int, float)), key
        assert isinstance(m["sees_all_sessions"], bool) and m["in_recovery"] is False

    def test_database_size_failure_keeps_the_other_readings(self, db_engine):
        """pg_database_size() can fail on Windows while another connection drops a
        table ("could not stat file ... Permission denied"; main #137). Only the
        size is lost; every other reading is still returned."""
        cur = _SizeQueryFails(db_engine.cursor())
        m = ht.read_postgres(cur)
        cur.close()
        assert m["db_size_bytes"] is None
        assert m["max_connections"] > 0 and m["connections"] >= 1 and "waiting_locks" in m

    def test_database_size_failure_leaves_a_transaction_usable(self):
        """On a connection that isn't autocommit, the failed size query would abort
        the transaction; it's rolled back so the snapshot rows can still be written."""
        db = get_master_db()
        raw_cur = db.cursor()
        raw_cur.connection.autocommit = False     # the real psycopg2 connection behind the pool wrapper
        cur = _SizeQueryFails(raw_cur)
        try:
            assert ht.read_postgres(cur)["db_size_bytes"] is None
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
        finally:
            raw_cur.connection.rollback()
            raw_cur.connection.autocommit = True
            cur.close()
            db.close()

    def test_app_readings(self):
        m = ht.read_app()
        assert m["pid"] == os.getpid() and m["threads"] >= 1 and m["python"]


class TestSnapshot:
    def test_stores_one_row_per_source(self):
        assert ht.take_snapshot(host_reader=lambda: {"cpu_percent": 12.5, "disks": []},
                                app_reader=lambda: {"pid": 1}) == {"host": True, "app": True, "postgres": True}
        rows = {r[0]: r[1:] for r in _master("SELECT source, ok, metrics, error FROM health_snapshots")}
        assert set(rows) == {"host", "postgres", "app"}
        assert rows["host"] == (True, {"cpu_percent": 12.5, "disks": []}, None)
        assert rows["postgres"][1]["connections"] >= 1

    def test_a_failing_source_is_stored_with_a_generic_reason(self):
        def broken():
            raise PermissionError("/secret/path/postgres://user:pw@db.internal")
        ht.take_snapshot(host_reader=broken, app_reader=lambda: {"pid": 1})
        [(ok, error)] = _master("SELECT ok, error FROM health_snapshots WHERE source='host'")
        assert ok is False and error == "Could not read the server's readings (PermissionError)"

    def test_purge(self):
        _insert("host", minutes_ago=60 * 24 * 40)
        _insert("host", minutes_ago=1)
        _master("INSERT INTO scheduler_job_runs (job_id, fire_minute) VALUES "
                "('health_snapshot_test_old', NOW()::timestamp - INTERVAL '9 days'), "
                "('health_snapshot_test_new', NOW()::timestamp - INTERVAL '1 hour') ON CONFLICT DO NOTHING")
        assert ht.purge_old() == 1
        assert _master("SELECT COUNT(*) FROM health_snapshots")[0][0] == 1
        kept = [r[0] for r in _master("SELECT job_id FROM scheduler_job_runs WHERE job_id LIKE 'health_snapshot_test_%'")]
        assert kept == ["health_snapshot_test_new"]
        _master("DELETE FROM scheduler_job_runs WHERE job_id LIKE 'health_snapshot_test_%'")


class TestHealthView:
    def test_never_read(self):
        d = ht.health(24)
        assert {s: v["state"] for s, v in d["sources"].items()} == {"host": "never", "postgres": "never", "app": "never"}
        assert d["host_series"] == [] and d["db_series"] == []

    def test_offline_failed_and_ok(self):
        _insert("host", metrics={"cpu_percent": 40.0}, minutes_ago=10)                 # stopped 10 minutes ago
        _insert("app", ok=True, metrics={"pid": 1}, minutes_ago=5)
        _insert("app", ok=False, error="Could not read the app service (OSError)", minutes_ago=0)
        _insert("postgres", metrics={"connections": 4}, minutes_ago=0)
        s = ht.health(1)["sources"]
        assert s["host"]["state"] == "offline" and s["host"]["metrics"] == {"cpu_percent": 40.0}
        assert s["app"]["state"] == "error" and s["app"]["metrics"] is None and s["app"]["last_ok"]
        assert s["app"]["error"] == "Could not read the app service (OSError)"
        assert s["postgres"]["state"] == "ok" and s["postgres"]["metrics"]["connections"] == 4

    def test_rates_come_from_counter_differences(self):
        for ago, commits, rollbacks, deadlocks in ((3, 1000, 10, 2), (2, 1600, 13, 2), (1, 2200, 16, 3)):
            _insert("postgres", metrics={"connections": 5, "latency_ms": 1.5, "xact_commit": commits,
                                         "xact_rollback": rollbacks, "deadlocks": deadlocks,
                                         "blks_hit": 990, "blks_read": 10}, minutes_ago=ago)
        d = ht.health(1)
        commits = [p["commits_per_min"] for p in d["db_series"] if p["commits_per_min"] is not None]
        assert commits and all(abs(c - 600) < 1 for c in commits)
        assert d["window"]["deadlocks"] == 1 and d["window"]["cache_hit_percent"] == 99.0

    def test_a_counter_reset_is_not_a_negative_rate(self):
        for ago, commits in ((2, 5000), (1, 40)):                # pg_stat_reset() between the readings
            _insert("postgres", metrics={"connections": 1, "xact_commit": commits, "xact_rollback": 0, "deadlocks": 0},
                    minutes_ago=ago)
        assert all(p["commits_per_min"] is None or p["commits_per_min"] >= 0 for p in ht.health(1)["db_series"])

    def test_times_are_on_the_database_clock(self):
        _insert("host", metrics={"cpu_percent": 1.0})
        [(db_now,)] = _master("SELECT NOW()::timestamp")
        shown = datetime.datetime.strptime(ht.health(1)["sources"]["host"]["at"], "%Y-%m-%d %H:%M:%S")
        assert abs((db_now - shown).total_seconds()) < 120

    def test_console_endpoint_needs_a_console_sign_in(self, client):
        assert client.get("/socperson/api/health").status_code in (401, 404)
        email = make_user("health_viewer", "viewer")
        assert sign_in(client, email).status_code == 303
        body = client.get("/socperson/api/health?hours=6").get_json()
        assert body["ok"] is True and body["hours"] == 6 and set(body["sources"]) == {"host", "postgres", "app"}
