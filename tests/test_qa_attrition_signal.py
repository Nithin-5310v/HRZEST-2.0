"""BUG-28 / PERF-02: the attrition "late/absent" signal counts real data.

It counted attendance rows with status 'Late' or 'Absent', but check-ins
store 'Late Login' and an absence has no attendance row at all, so the
signal was always 0 and every employee scored the minimum. It also ran two
queries per employee.
"""
import datetime

import pytest

import database
from extensions import app as flask_app
from utils.helpers import company_today


def _analytics():
    from utils.ai_attrition_analytics import compute_attrition_and_burnout_analytics
    with flask_app.app_context():
        return compute_attrition_and_burnout_analytics()


def _employee(result, emp_id):
    # High/Medium-risk employees are always in flagged_burnout_risks.
    return next(e for e in result["flagged_burnout_risks"] + result["all_employee_analytics"]
                if e["employee_id"] == emp_id)


def _recent_working_days(cur, n):
    cur.execute("SELECT date FROM holidays")
    holidays = {r[0] for r in cur.fetchall()}
    day, days = company_today() - datetime.timedelta(days=1), []
    while len(days) < n:
        if day.weekday() != 6 and day not in holidays:
            days.append(day)
        day -= datetime.timedelta(days=1)
    return days


@pytest.fixture
def irregular_employee(db_engine, seed_employee):
    """Joined 6 weeks ago; in the last 30 days: 2 late logins, 3 working days with
    no check-in, every other working day on time."""
    emp = seed_employee["employee_id"]
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s", (emp,))
    cur.execute("UPDATE employees SET date_of_joining=%s WHERE employee_id=%s",
                (company_today() - datetime.timedelta(days=42), emp))
    days = _recent_working_days(cur, 30)
    window = [d for d in days if d >= company_today() - datetime.timedelta(days=30)]
    late, on_time = window[:2], window[5:]           # window[2:5]: no check-in at all
    for d in late:
        cur.execute("INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,'10:30','Late Login')", (emp, d))
    for d in on_time:
        cur.execute("INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,'09:00','Full Day Login')", (emp, d))
    cur.close()
    return emp


def test_late_logins_and_missed_days_raise_the_risk_score(irregular_employee):
    row = _employee(_analytics(), irregular_employee)
    assert row["burnout_risk_score"] == min(92, 5 * 15 + 12)       # 2 late + 3 absent
    assert row["risk_level"] == "High"


def test_query_count_does_not_grow_with_headcount(db_engine, seed_employee, monkeypatch):
    log = []
    real_cursor = database._PooledConnection.cursor

    class _Counting:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, params=None):
            log.append(sql)
            return self._cur.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._cur, name)

    _analytics()                       # warm the settings cache company_today() reads
    monkeypatch.setattr(database._PooledConnection, "cursor", lambda self, *a, **k: _Counting(real_cursor(self, *a, **k)))
    _analytics()
    few = len(log)
    cur = db_engine.cursor()
    for i in range(5):
        cur.execute("INSERT INTO employees (employee_id, name, force_pin_change) VALUES (%s,%s,0)", (f"QAP{i}", f"QA {i}"))
    cur.close()
    log.clear()
    _analytics()
    assert len(log) == few
