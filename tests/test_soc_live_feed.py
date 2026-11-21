"""The SOC console's live feed (utils/live_feed.py, GET /socperson/api/stream):
short Server-Sent Events responses that carry new events and the "right now"
numbers, resume from Last-Event-ID, and never count as activity for the idle
sign-out.
"""
import json
import time

import pytest

from database import get_master_db
from tests import test_soc_console as _console
from tests.test_soc_console import PUBLIC_IP, add_event, make_user, sign_in
from utils import live_feed

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches).
_fresh_soc_state = _console._fresh_soc_state


@pytest.fixture(autouse=True)
def _setup(_fresh_soc_state, client):
    live_feed._kpi_cache.update(at=0.0, value=None)
    yield
    live_feed._kpi_cache.update(at=0.0, value=None)


@pytest.fixture
def analyst(client):
    email = make_user("live_viewer", "viewer")
    assert sign_in(client, email).status_code == 303
    return client


def _messages(body):
    """Parse an SSE body into [{"event":..., "id":..., "data":..., "retry":...}]."""
    out = []
    for block in body.split("\n\n"):
        msg = {}
        for line in block.splitlines():
            field, _, value = line.partition(": ")
            msg[field] = value
        if msg:
            out.append(msg)
    return out


def _max_id(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM security_events")
    n = cur.fetchone()[0]
    cur.close()
    return n


def test_the_stream_needs_a_console_sign_in(client):
    assert client.get("/socperson/api/stream").status_code in (401, 404)


def test_first_connection_sends_status_but_no_backlog(analyst, db_engine):
    add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="before_connect", minutes_ago=0)
    resp = analyst.get("/socperson/api/stream")
    assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
    body = resp.get_data(as_text=True)
    assert body.startswith("retry: 5000\n\n")
    msgs = _messages(body)
    assert [m.get("event") for m in msgs if "event" in m] == ["pulse"]          # no "events" message
    pulse = next(m for m in msgs if m.get("event") == "pulse")
    assert int(pulse["id"]) == _max_id(db_engine)
    data = json.loads(pulse["data"])
    assert {"threat", "alerts_24h", "open_cases", "kpis"} <= set(data)


def test_new_events_arrive_after_the_last_event_id(analyst, db_engine):
    first = next(m for m in _messages(analyst.get("/socperson/api/stream").get_data(as_text=True)) if m.get("event") == "pulse")
    last = int(first["id"])
    eid = add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="arrived_live", minutes_ago=0)
    msgs = _messages(analyst.get("/socperson/api/stream", headers={"Last-Event-ID": str(last)}).get_data(as_text=True))
    batch = next(m for m in msgs if m.get("event") == "events")
    events = json.loads(batch["data"])["events"]
    assert eid in [e["id"] for e in events] and any(e["identifier"] == "arrived_live" for e in events)
    assert int(batch["id"]) >= eid
    # the next reconnection resumes from there: nothing new, so no events message
    again = _messages(analyst.get("/socperson/api/stream", headers={"Last-Event-ID": batch["id"]}).get_data(as_text=True))
    assert "events" not in [m.get("event") for m in again]


def test_a_malformed_last_event_id_is_treated_as_a_first_connection(analyst):
    msgs = _messages(analyst.get("/socperson/api/stream", headers={"Last-Event-ID": "abc; DROP"}).get_data(as_text=True))
    assert [m.get("event") for m in msgs if "event" in m] == ["pulse"]


def test_the_stream_is_not_activity_for_the_idle_sign_out(analyst):
    old = int(time.time()) - 120
    with analyst.session_transaction() as sess:
        sess["soc_last"] = old
    assert analyst.get("/socperson/api/stream").status_code == 200
    with analyst.session_transaction() as sess:
        assert sess["soc_last"] == old
    assert analyst.get("/socperson/api/me").status_code == 200
    with analyst.session_transaction() as sess:
        assert sess["soc_last"] > old


def test_pulse_carries_the_live_numbers_too(analyst):
    body = analyst.get("/socperson/api/pulse").get_json()
    assert body["ok"] is True and "failed_logins_1h" in body["kpis"]


def test_right_now_numbers_count_what_was_recorded(db_engine):
    master = get_master_db()
    mcur = master.cursor()
    try:
        before = live_feed.kpis()
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="kpi_a", minutes_ago=1)
        add_event(db_engine, "auth.mfa_failure", ip=PUBLIC_IP, identifier="kpi_b", minutes_ago=2)
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="kpi_c", minutes_ago=180)      # in 24 h, not 1 h
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="kpi_d", minutes_ago=60 * 30)  # outside both
        add_event(db_engine, "waf.sqli_blocked", level="ERROR", ip=PUBLIC_IP, minutes_ago=3)          # an alert, 1 h
        add_event(db_engine, "detection.credential_stuffing", ip=PUBLIC_IP, minutes_ago=300)        # an alert, 24 h
        mcur.execute("INSERT INTO http_minute_stats (minute, endpoint, method, status, tenant, hits, total_ms, max_ms, buckets) "
                     "VALUES (date_trunc('minute', NOW() - INTERVAL '5 minutes'), '/live-test', 'GET', 401, 'live', 3, 3, 1, "
                     "'{3,0,0,0,0,0,0,0,0,0}'), (date_trunc('minute', NOW() - INTERVAL '5 minutes'), '/live-test', 'GET', 403, "
                     "'live', 2, 2, 1, '{2,0,0,0,0,0,0,0,0,0}')")
        mcur.execute("INSERT INTO http_telemetry_flushes (host, pid, flushed_at) VALUES ('live-test', 1, NOW()) "
                     "ON CONFLICT (host, pid) DO UPDATE SET flushed_at = NOW()")
        master.commit()
        cur = db_engine.cursor()
        cur.execute("INSERT INTO public.banned_ips (ip, reason, banned_by, expires_at) VALUES "
                    "('45.33.32.157', 'live test', 'test', NOW() + INTERVAL '1 hour') ON CONFLICT DO NOTHING")
        cur.close()
        live_feed._kpi_cache.update(at=0.0, value=None)
        after = live_feed.kpis()
        delta = {k: after[k] - before[k] for k in ("failed_logins_1h", "failed_logins_24h", "alerts_1h", "alerts_24h", "blocked_ips")}
        assert delta == {"failed_logins_1h": 2, "failed_logins_24h": 3, "alerts_1h": 1, "alerts_24h": 2, "blocked_ips": 1}
        assert after["denied_1h"]["s401"] - before["denied_1h"]["s401"] == 3
        assert after["denied_1h"]["s403"] - before["denied_1h"]["s403"] == 2
        assert after["denied_1h"]["source"] == "ok"
    finally:
        mcur.execute("DELETE FROM http_minute_stats WHERE tenant='live'")
        mcur.execute("DELETE FROM http_telemetry_flushes WHERE host='live-test'")
        master.commit()
        mcur.close()
        master.close()


def test_numbers_are_cached_briefly_so_many_tabs_cost_one_query(monkeypatch):
    first = live_feed.kpis()
    monkeypatch.setattr(live_feed, "KPI_TTL_SECONDS", 3600)
    assert live_feed.kpis() is first
