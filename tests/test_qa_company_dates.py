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


# ── BUG-18: HR dashboard ─────────────────────────────────────────────────────

def test_hr_onboarding_overdue_flag_uses_the_companys_today(client, db_engine, seed_hr_admin, seed_assigned_employee, company_date):
    from utils.auth import HR_ROLE
    server_today = datetime.date.today()
    # The one date the two calendars disagree about: overdue for one, not the other.
    due = server_today if company_date > server_today else company_date
    cur = db_engine.cursor()
    cur.execute("INSERT INTO onboarding_templates (name) VALUES ('QA template') RETURNING id")
    template_id = cur.fetchone()[0]
    cur.execute("INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date, status) "
                "VALUES (%s,%s,%s,%s,'In Progress') RETURNING id",
                (seed_assigned_employee["employee_id"], template_id, due - datetime.timedelta(days=7), due))
    onboarding_id = cur.fetchone()[0]
    cur.close()
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    rows = client.get("/api/hr_dashboard/onboarding").get_json()["rows"]
    row = next(r for r in rows if r["id"] == onboarding_id)
    assert row["is_overdue"] is (due < company_date)


# ── BUG-19: attendance ───────────────────────────────────────────────────────

def test_bulk_mark_without_a_date_marks_the_companys_today(client, db_engine, seed_admin, seed_employee, company_date):
    token = client.post("/api/login", json={"username": seed_admin["username"],
                                            "password": seed_admin["password"]}).get_json()["token"]
    resp = client.post("/api/bulk_mark_attendance", json={"records": [
        {"employee_id": seed_employee["employee_id"], "attendance_type": "Full Day",
         "login_time": "09:00", "logout_time": "18:00"},
    ]}, headers={"Authorization": f"Bearer {token}"})
    assert resp.get_json()["saved"] == 1
    cur = db_engine.cursor()
    cur.execute("SELECT date FROM attendance WHERE employee_id=%s AND status='Manual'", (seed_employee["employee_id"],))
    assert [r[0] for r in cur.fetchall()] == [company_date]
    cur.close()


# ── BUG-20: leave & overtime ─────────────────────────────────────────────────

def test_overtime_request_without_a_date_is_for_the_companys_today(client, db_engine, seed_employee, company_date):
    token = client.post("/api/employee/login", json={"employee_id": seed_employee["employee_id"],
                                                     "password": seed_employee["password"]}).get_json()["token"]
    resp = client.post("/api/employee/request_overtime", json={"reason": "QA release night"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.get_json()["ok"] is True, resp.get_json()
    cur = db_engine.cursor()
    cur.execute("SELECT date FROM overtime_records WHERE employee_id=%s AND requested_by_employee=1",
                (seed_employee["employee_id"],))
    assert [r[0] for r in cur.fetchall()] == [company_date]
    cur.close()


# ── BUG-23: onboarding ───────────────────────────────────────────────────────

def test_onboarding_is_assigned_on_the_companys_today(client, db_engine, seed_admin, seed_employee, company_date):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO onboarding_templates (name) VALUES ('QA template') RETURNING id")
    template_id = cur.fetchone()[0]
    _admin(client, seed_admin)
    client.post("/onboarding_assign", data={"employee_id": seed_employee["employee_id"], "template_id": template_id})
    cur.execute("SELECT assigned_date FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
                (seed_employee["employee_id"], template_id))
    assert [r[0] for r in cur.fetchall()] == [company_date]
    cur.close()


# ── BUG-26: the nightly attendance report ───────────────────────────────────

def test_daily_report_covers_the_companys_day(monkeypatch, company_date):
    import blueprints.daily_report as dr
    sent = []
    monkeypatch.setattr(dr, "get_admin_emails", lambda: ["admin@test.local"])
    monkeypatch.setattr(dr, "get_email_config", lambda: {"host": "smtp.invalid"})
    monkeypatch.setattr(dr, "send_email_async", lambda to, subject, html, cfg: sent.append(subject))
    dr.generate_and_send_daily_report()
    assert sent and company_date.strftime("%d %B %Y") in sent[0]


def test_daily_report_is_scheduled_at_2359_in_the_company_timezone():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(__file__).resolve().parents[1].joinpath("wsgi.py").read_text(encoding="utf-8"))
    job = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_job"
               and any(k.arg == "id" and getattr(k.value, "value", "") == "daily_attendance_report" for k in n.keywords))
    assert any(k.arg == "timezone" for k in job.keywords)


# ── BUG-39: mobile admin dashboard ──────────────────────────────────────────

def test_mobile_dashboard_counts_the_companys_today(client, seed_admin, seed_employee, checked_in_on_company_date):
    token = client.post("/api/login", json={"username": seed_admin["username"],
                                            "password": seed_admin["password"]}).get_json()["token"]
    data = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"}).get_json()
    assert data["today"] == checked_in_on_company_date.strftime("%d %b %Y")
    assert data["present"] >= 1
