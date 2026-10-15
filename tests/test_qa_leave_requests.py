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


def _working_days(db_engine, start, end):
    cur = db_engine.cursor()
    cur.execute("SELECT date FROM holidays WHERE date BETWEEN %s AND %s", (start, end))
    holidays = {r[0] for r in cur.fetchall()}
    cur.close()
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]
    return [d for d in days if d.weekday() != 6 and d not in holidays]


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
    friday = MONDAY + datetime.timedelta(days=4)
    _request(client, MONDAY, friday)
    assert len(_leave_dates(db_engine, employee_session)) in (5, len(_working_days(db_engine, MONDAY, friday)))


# ── BUG-07: working days only, no duplicates ────────────────────────────────

@pytest.fixture
def holiday_wednesday(db_engine):
    wednesday = MONDAY + datetime.timedelta(days=2)
    cur = db_engine.cursor()
    cur.execute("INSERT INTO holidays (date, name) VALUES (%s, 'QA Holiday') ON CONFLICT DO NOTHING", (wednesday,))
    cur.close()
    return wednesday


def test_sundays_and_holidays_get_no_leave_rows(client, employee_session, db_engine, holiday_wednesday):
    next_monday = MONDAY + datetime.timedelta(days=7)
    _request(client, MONDAY, next_monday)
    dates = _leave_dates(db_engine, employee_session)
    assert holiday_wednesday not in dates
    assert all(d.weekday() != 6 for d in dates)
    assert dates == _working_days(db_engine, MONDAY, next_monday)


def test_a_day_already_requested_is_refused(client, employee_session, db_engine):
    _request(client, MONDAY, MONDAY + datetime.timedelta(days=2))
    _request(client, MONDAY + datetime.timedelta(days=1), MONDAY + datetime.timedelta(days=4))
    assert _leave_dates(db_engine, employee_session) == _working_days(db_engine, MONDAY, MONDAY + datetime.timedelta(days=2))


def test_a_request_with_no_working_day_is_refused(client, employee_session, db_engine):
    _request(client, MONDAY - datetime.timedelta(days=1))               # a Sunday
    assert _leave_dates(db_engine, employee_session) == []


def _mobile_request(client, seed_employee, leave_date):
    token = client.post("/api/employee/login", json={"employee_id": seed_employee["employee_id"],
                                                     "password": seed_employee["password"]}).get_json()["token"]
    return client.post("/api/employee/leave_request", json={"leave_date": str(leave_date), "reason": "QA"},
                       headers={"Authorization": f"Bearer {token}"})


def test_mobile_request_follows_the_same_rules(client, seed_employee, employee_session, db_engine):
    assert _mobile_request(client, seed_employee, MONDAY - datetime.timedelta(days=1)).status_code == 400   # Sunday
    assert _mobile_request(client, seed_employee, "not-a-date").status_code == 400
    assert _mobile_request(client, seed_employee, MONDAY).get_json()["ok"] is True
    assert _mobile_request(client, seed_employee, MONDAY).status_code == 409                             # duplicate
    assert _leave_dates(db_engine, employee_session) == [MONDAY]
