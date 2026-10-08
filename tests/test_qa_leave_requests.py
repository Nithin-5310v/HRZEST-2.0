"""Leave requests are validated (QA BUG-06 / BUG-07).

A request inserted one row per calendar day with no upper bound (one
request for 2026-2099 inserted ~27k rows), crashed with a 500 on a bad
date, created rows for Sundays and holidays, and accepted the same day
twice.
"""
import datetime

import pytest


def _next_weekday(start, weekday):
    d = start
    while d.weekday() != weekday:
        d += datetime.timedelta(days=1)
    return d


MONDAY = _next_weekday(datetime.date.today() + datetime.timedelta(days=14), 0)


@pytest.fixture
def employee_session(client, seed_employee, db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM leave_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.close()
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    return seed_employee["employee_id"]


def _request(client, start, end=None, **extra):
    data = {"leave_date_start": str(start), "leave_date_end": str(end or start), "reason": "QA", **extra}
    return client.post("/request_leave", data=data)


def _leave_dates(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("SELECT leave_date FROM leave_requests WHERE employee_id=%s ORDER BY leave_date", (emp_id,))
    dates = [r[0] for r in cur.fetchall()]
    cur.close()
    return dates


# ── BUG-06: bounded, validated ───────────────────────────────────────────────

def test_a_decades_long_request_is_refused(client, employee_session, db_engine):
    resp = _request(client, "2026-01-01", "2099-12-31")
    assert resp.status_code == 302
    assert _leave_dates(db_engine, employee_session) == []


def test_a_request_longer_than_60_days_is_refused(client, employee_session, db_engine):
    _request(client, MONDAY, MONDAY + datetime.timedelta(days=60))
    assert _leave_dates(db_engine, employee_session) == []


def test_a_malformed_date_is_refused_without_a_server_error(client, employee_session, db_engine):
    resp = _request(client, "2026-13-45")
    assert resp.status_code == 302
    assert _leave_dates(db_engine, employee_session) == []


def test_a_normal_week_is_accepted(client, employee_session, db_engine):
    _request(client, MONDAY, MONDAY + datetime.timedelta(days=4))
    assert len(_leave_dates(db_engine, employee_session)) == 5
