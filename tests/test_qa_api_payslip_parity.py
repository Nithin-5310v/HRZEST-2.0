"""BUG-34: the mobile admin API computes salary exactly like the web.

/api/salary_report and /api/send_salary_email called compute_salary_entry()
without the month's holidays or the employee's approved leave, and without
incentives, the employee's CTC or the company's PF/PT/TDS settings. A paid
holiday or an approved leave day was paid as an absence, so the mobile
report and the emailed payslip disagreed with the web salary report and
the payslip the employee downloads for the same month.
"""
import calendar
import datetime

import pytest

import blueprints.payroll as payroll

SMTP = {"host": "smtp.invalid", "port": 587, "user": "u", "password": "p",
        "from_name": "QA", "from_email": "qa@test.local"}


def _last_month():
    first_this = datetime.date.today().replace(day=1)
    last = first_this - datetime.timedelta(days=1)
    days = [datetime.date(last.year, last.month, d) for d in range(1, calendar.monthrange(last.year, last.month)[1] + 1)]
    return last.year, last.month, [d for d in days if d.weekday() != 6]


@pytest.fixture
def payroll_month(db_engine, seed_employee):
    year, month, working = _last_month()
    emp = seed_employee["employee_id"]
    holiday, leave_day, present = working[0], working[1], working[2:]
    cur = db_engine.cursor()
    cur.execute("INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc) VALUES (%s, 1000, 30000) "
                "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=1000, monthly_ctc=30000", (emp,))
    cur.execute("UPDATE employees SET email='tst001@test.local' WHERE employee_id=%s", (emp,))
    cur.execute("INSERT INTO holidays (date, name) VALUES (%s, 'QA Holiday') ON CONFLICT DO NOTHING", (holiday,))
    cur.execute("SELECT id FROM leave_types LIMIT 1")
    leave_type = cur.fetchone()[0]
    cur.execute("INSERT INTO leave_requests (employee_id, leave_type_id, leave_date, reason, status) "
                "VALUES (%s,%s,%s,'QA','Approved')", (emp, leave_type, leave_day))
    cur.execute("INSERT INTO incentive_goals (title, incentive_amount) VALUES ('QA goal', 750) RETURNING id")
    goal = cur.fetchone()[0]
    cur.execute("INSERT INTO employee_incentives (employee_id, goal_id, month, year, amount) VALUES (%s,%s,%s,%s,750)",
                (emp, goal, month, year))
    for d in present:
        cur.execute("INSERT INTO attendance (employee_id, date, login_time, logout_time, status, attendance_type) "
                    "VALUES (%s,%s,'09:00','18:00','Full Day Login','Full Day') ON CONFLICT DO NOTHING", (emp, d))
    cur.execute("DELETE FROM payroll_config")
    cur.execute("INSERT INTO payroll_config (pf_employee_pct, pf_employer_pct, professional_tax, tds_annual_pct, pf_basic_cap) "
                "VALUES (10, 10, 150, 0, 15000)")
    cur.close()
    return emp, year, month


def _admin_token(client, seed_admin):
    return client.post("/api/login", json={"username": seed_admin["username"],
                                           "password": seed_admin["password"]}).get_json()["token"]


def test_api_salary_report_matches_the_web_report(client, seed_admin, payroll_month):
    emp, year, month = payroll_month
    token = _admin_token(client, seed_admin)
    api = client.get(f"/api/salary_report?year={year}&month={month}",
                     headers={"Authorization": f"Bearer {token}"}).get_json()
    api_entry = next(e for e in api["salary_data"] if e["emp_id"] == emp)
    web_entry = next(e for e in payroll.compute_salary_data_for_month(year, month) if e["emp_id"] == emp)
    for key in ("net", "absent", "holiday_days", "leave_days", "full_days", "deduction"):
        assert api_entry[key] == web_entry[key], key
    assert api_entry["absent"] == 0                 # the holiday and the leave day aren't absences


def test_api_payslip_email_matches_the_web_payslip_email(client, seed_admin, payroll_month, monkeypatch):
    emp, year, month = payroll_month
    sent = []
    monkeypatch.setattr(payroll, "get_email_config", lambda: SMTP)
    monkeypatch.setattr(payroll, "send_email_smtp", lambda to, subject, html, cfg, **k: sent.append(("web", html)))
    monkeypatch.setattr(payroll, "send_email_async", lambda to, subject, html, cfg, **k: sent.append(("api", html)))
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    assert client.post("/send_salary_email", data={"emp_id": emp, "year": year, "month": month}).get_json()["ok"]
    token = _admin_token(client, seed_admin)
    assert client.post("/api/send_salary_email", json={"emp_id": emp, "year": year, "month": month},
                       headers={"Authorization": f"Bearer {token}"}).get_json()["ok"]
    html = dict(sent)
    assert html["api"] == html["web"]
