"""BUG-17..23: "today" is the company's date, not the server's.

Check-ins are stored under company_today() (the tenant's timezone,
Asia/Kolkata by default) but many screens computed "today" from the server
clock (datetime.date.today(), UTC in the container). Between 00:00 and
05:30 IST the dashboards showed yesterday, and non-IST tenants were off
all day.

The fixture puts the company in whichever of UTC+14 / UTC-12 has a
different date from the server right now (at least one always does), so
the server date and the company date really differ during the test.
"""
import datetime

import pytest
import pytz

from utils.helpers import invalidate_settings_cache


@pytest.fixture
def company_date(db_engine):
    server_today = datetime.date.today()
    for tz_name in ("Pacific/Kiritimati", "Etc/GMT+12"):
        company_today = datetime.datetime.now(pytz.timezone(tz_name)).date()
        if company_today != server_today:
            break
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET timezone=%s", (tz_name,))
    cur.close()
    invalidate_settings_cache()
    return company_today


@pytest.fixture
def checked_in_on_company_date(db_engine, seed_employee, company_date):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s", (seed_employee["employee_id"], company_date))
    cur.execute("INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,'09:00','Late Login')",
                (seed_employee["employee_id"], company_date))
    cur.close()
    return company_date


def _admin(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"


# ── BUG-17: admin dashboards ─────────────────────────────────────────────────

def test_live_dashboard_counts_the_companys_today(client, seed_admin, seed_employee, checked_in_on_company_date):
    _admin(client, seed_admin)
    data = client.get("/api/dashboard_live").get_json()
    row = next(r for r in data["rows"] if r["emp_id"] == seed_employee["employee_id"])
    assert row["login_t"] == "09:00:00"
    assert data["present"] >= 1 and data["late"] >= 1


def test_attendance_chart_ends_on_the_companys_today(client, seed_admin, seed_employee, checked_in_on_company_date):
    _admin(client, seed_admin)
    data = client.get("/api/attendance_chart_data").get_json()
    assert data["trend"]["labels"][-1] == checked_in_on_company_date.strftime("%d %b")
    assert data["trend"]["present"][-1] >= 1
