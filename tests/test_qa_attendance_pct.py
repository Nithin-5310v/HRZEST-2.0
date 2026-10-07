"""BUG-03 / BUG-04: monthly "Attendance %" counts employee-days.

It divided the number of *distinct employees* who logged in at least once
in the month by (working days x employees), so 10 employees present every
day showed about 5%, and "absent days" was inflated to match.
"""
import calendar
import datetime
import json
import re
import secrets

import pytest

from extensions import app as flask_app
from tests.test_auth_hardening import _make_tenant


def _last_month():
    first_this = datetime.date.today().replace(day=1)
    last = first_this - datetime.timedelta(days=1)
    days = [datetime.date(last.year, last.month, d) for d in range(1, calendar.monthrange(last.year, last.month)[1] + 1)]
    return last.replace(day=1), days


@pytest.fixture
def tenant_with_attendance(db_engine, client):
    slug, schema = _make_tenant(db_engine, "attpct" + secrets.token_hex(3))
    month_start, days = _last_month()
    working = [d for d in days if d.weekday() != 6]
    sunday = next(d for d in days if d.weekday() == 6)
    cur = db_engine.cursor()
    for emp in ("AP1", "AP2"):
        cur.execute(f'INSERT INTO "{schema}".employees (employee_id, name, force_pin_change) VALUES (%s,%s,0)',  # nosec B608
                    (emp, emp))
    rows = [("AP1", d) for d in working + [sunday]]          # every working day, plus a Sunday
    rows += [("AP2", d) for d in working[: len(working) // 2]]  # half the working days
    for emp, day in rows:
        cur.execute(f'INSERT INTO "{schema}".attendance (employee_id, date, login_time, status) '  # nosec B608
                    "VALUES (%s,%s,'09:00','Full Day Login')", (emp, day))
    cur.close()
    expected_pct = round((len(working) + len(working) // 2) / (2 * len(working)) * 100, 1)
    return slug, month_start.strftime("%b %Y"), expected_pct, len(working)


def _month_entry(html, label):
    data = json.loads(re.search(r"const monthlyData = (\[.*?\]);", html, re.S).group(1))
    return next(m for m in data if m["month_label"] == label)


def test_admin_analytics_attendance_pct_counts_employee_days(tenant_with_attendance):
    slug, label, expected_pct, working_days = tenant_with_attendance
    admin = flask_app.test_client()
    admin.post(f"/{slug}/login", data={"identifier": "iso_admin", "password": "IsoAdmin@123"})
    resp = admin.get(f"/{slug}/analytics")
    assert resp.status_code == 200
    month = _month_entry(resp.get_data(as_text=True), label)
    assert month["att_pct"] == expected_pct                  # 75.0, not ~4%
    assert month["present_days"] == working_days + working_days // 2
    assert month["absent_days"] == 2 * working_days - month["present_days"]
