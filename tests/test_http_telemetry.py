"""Request telemetry (utils/http_telemetry.py): what is counted, how counts from
several workers add up, how percentiles are derived, what the SOC console's
Traffic view gets (including the "never" / "offline" states), and the
http_error_spike detection that reads it.
"""
import datetime
import time

import pytest

import database
from database import get_master_db
from tests import test_soc_console as _console
from tests.test_soc_console import make_user, sign_in
from utils import detections, http_telemetry as ht

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches).
_fresh_soc_state = _console._fresh_soc_state


def _master(sql, params=()):
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
    ht.reset()
    _master("DELETE FROM http_minute_stats")
    _master("DELETE FROM http_telemetry_flushes")
    yield
    ht.reset()


def _stats():
    return _master("SELECT endpoint, method, status, tenant, hits, total_ms, max_ms, buckets FROM http_minute_stats "
                   "ORDER BY endpoint, status")


class TestHistogram:
    def test_bucket_bounds(self):
        assert [ht.bucket_index(v) for v in (0, 5, 5.1, 10, 249, 250, 2500, 2501, 99999)] == [0, 0, 1, 1, 5, 5, 8, 9, 9]

    def test_percentiles_are_upper_bounds(self):
        assert ht.percentile([0] * ht.N_BUCKETS, 95) is None
        hist = [90, 0, 0, 0, 0, 10, 0, 0, 0, 0]          # 90 requests <= 5 ms, 10 requests <= 250 ms
        assert ht.percentile(hist, 50) == {"le": 5}
        assert ht.percentile(hist, 90) == {"le": 5}
        assert ht.percentile(hist, 95) == {"le": 250}
        slow = [0] * 9 + [4]
        assert ht.percentile(slow, 99, max_ms=3100) == {"gt": 2500, "max": 3100}


class TestCollection:
    def test_counts_from_several_flushes_add_up(self):
        now = time.time()
        ht.record("/r", "GET", 200, "acme", 3, now=now)
        ht.record("/r", "GET", 200, "acme", 40, now=now)
        assert ht.flush() == 1
        ht.record("/r", "GET", 200, "acme", 3000, now=now)    # another worker, same minute
        ht.flush()
        [(endpoint, method, status, tenant, hits, total_ms, max_ms, buckets)] = _stats()
        assert (endpoint, method, status, tenant, hits) == ("/r", "GET", 200, "acme", 3)
        assert total_ms == 3043 and max_ms == 3000
        assert buckets == [1, 0, 0, 1, 0, 0, 0, 0, 0, 1]

    def test_requests_are_counted_by_route_pattern_not_raw_url(self, client):
        client.get("/view_payslip/TST001/2025/1")
        client.get("/no-such-page-anywhere")
        for skipped in ("/static/brand/hrzest-mark.svg", "/healthz", "/socperson/login"):
            client.get(skipped)
        ht.flush()
        rows = {(r[0], r[1], r[2]) for r in _stats()}
        assert ("/view_payslip/<emp_id>/<int:year>/<int:month>", "GET", 302) in rows
        assert ("(no matching route)", "GET", 404) in rows
        assert not any("TST001" in r[0] or r[0].startswith(("/static", "/healthz", "/socperson")) for r in rows)

    def test_a_failed_write_keeps_the_counts_for_the_next_one(self, monkeypatch):
        ht.record("/r", "POST", 500, "", 12)

        def down():
            raise RuntimeError("database down")
        monkeypatch.setattr(database, "get_master_db", down)
        assert ht.flush() == 0
        monkeypatch.undo()
        assert ht.flush() == 1
        assert [r[4] for r in _stats()] == [1]

    def test_switched_off(self, client, monkeypatch):
        monkeypatch.setenv("HTTP_TELEMETRY", "off")
        client.get("/no-such-page-anywhere")
        ht.flush()
        assert _stats() == []

    def test_old_rows_are_purged(self):
        for age in ("20 days", "1 hour"):
            _master("INSERT INTO http_minute_stats (minute, endpoint, method, status, hits, total_ms, max_ms, buckets) "
                    f"VALUES (date_trunc('minute', NOW() - INTERVAL '{age}'), '/x', 'GET', 200, 1, 1, 1, "  # nosec B608
                    "'{1,0,0,0,0,0,0,0,0,0}')")
        assert ht.purge_old() == 1
        assert len(_stats()) == 1


class TestTrafficView:
    def test_never_written(self):
        d = ht.traffic(24)
        assert d["source"] == {"state": "never", "last_write": None}
        assert d["totals"]["requests"] == 0 and d["series"] == [] and d["endpoints"] == []

    def test_a_stalled_collector_shows_as_offline_with_its_last_write(self):
        ht.flush()
        assert ht.traffic(1)["source"]["state"] == "ok"
        _master("UPDATE http_telemetry_flushes SET flushed_at = NOW() - INTERVAL '10 minutes'")
        src = ht.traffic(1)["source"]
        assert src["state"] == "offline" and src["last_write"]

    def test_times_are_on_the_database_clock_like_the_rest_of_the_console(self):
        # soc-core.js reads every timestamp as the database's wall clock; a UTC
        # value from a database on another zone showed "last written 5h ago".
        ht.record("/r", "GET", 200, "", 1)
        ht.flush()
        [(db_now,)] = _master("SELECT NOW()::timestamp")
        d = ht.traffic(1)
        for value in (d["source"]["last_write"], d["last_request_minute"], d["series"][0]["t"]):
            shown = datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            assert abs((db_now - shown).total_seconds()) < 120, value

    def test_summary_matches_what_was_recorded(self):
        for _ in range(18):
            ht.record("/busy", "GET", 200, "", 4)
        ht.record("/busy", "GET", 200, "", 400)
        ht.record("/busy", "GET", 403, "", 2)
        ht.record("/quiet", "POST", 500, "", 1200)
        ht.flush()
        d = ht.traffic(1)
        t = d["totals"]
        assert (t["requests"], t["ok"], t["client_errors"], t["server_errors"], t["s403"]) == (21, 19, 1, 1, 1)
        assert t["p50"] == {"le": 5} and t["p95"] == {"le": 500}
        assert sum(p["ok"] + p["4xx"] + p["5xx"] for p in d["series"]) == 21
        busy, quiet = d["endpoints"]
        assert (busy["endpoint"], busy["hits"], busy["s403"], busy["errors"]) == ("/busy", 20, 1, 1)
        assert (quiet["endpoint"], quiet["method"], quiet["s5xx"], quiet["max_ms"]) == ("/quiet", "POST", 1, 1200)
        assert {s["status"]: s["hits"] for s in d["statuses"]} == {200: 19, 403: 1, 500: 1}

    def test_console_endpoint_needs_a_console_sign_in(self, client):
        assert client.get("/socperson/api/traffic").status_code in (401, 404)
        email = make_user("traffic_viewer", "viewer")
        assert sign_in(client, email).status_code == 303
        resp = client.get("/socperson/api/traffic?hours=6")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True and body["hours"] == 6 and body["source"]["state"] in ("never", "ok", "offline")


class TestErrorSpikeRule:
    RULE = next(r for r in detections.RULES if r.name == "http_error_spike")

    def _rows(self, status, hits, minutes_ago):
        _master("INSERT INTO http_minute_stats (minute, endpoint, method, status, hits, total_ms, max_ms, buckets) "
                "VALUES (date_trunc('minute', NOW() - (%s * INTERVAL '1 minute')), '/login', 'POST', %s, %s, 1, 1, "
                "'{1,0,0,0,0,0,0,0,0,0}')", (minutes_ago, status, hits))

    def test_fires_only_for_a_status_far_above_its_usual_rate(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("DELETE FROM detection_state WHERE rule='http_error_spike'")
        self._rows(403, 150, 2)            # sudden: nothing like it in the day before
        self._rows(404, 150, 2)            # normal for this site: ~300 per 10 minutes all day
        self._rows(404, 300 * 144, 120)
        self._rows(401, 20, 2)             # far above usual, but below the minimum count
        events = []
        fired = detections.evaluate(cur, self.RULE, None, lambda et, msg, **f: events.append((et, msg, f)))
        cur.close()
        assert [f["key"] for f in fired] == ["http_403"]
        (event_type, message, fields), = events
        assert event_type == "detection.http_error_spike" and "150 HTTP 403 responses" in message
        assert fields["count"] == 150
