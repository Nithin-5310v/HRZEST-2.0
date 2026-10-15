"""SEC-10: only the admin role sends payslip emails, as with the salary reports.

/send_salary_email (web, @admin_required) and /api/send_salary_email
(@api_required) accepted any admin-side role -- HR, manager, SOC analyst --
for any employee, while the salary report routes they belong to are
admin-only.
"""
import pytest

import blueprints.payroll as payroll

SMTP = {"host": "smtp.invalid", "port": 587, "user": "u", "password": "p",
        "from_name": "QA", "from_email": "qa@test.local"}


@pytest.fixture
def mail(monkeypatch, db_engine, seed_employee):
    sent = []
    monkeypatch.setattr(payroll, "get_email_config", lambda: SMTP)
    monkeypatch.setattr(payroll, "send_email_smtp", lambda to, *a, **k: sent.append(to))
    monkeypatch.setattr(payroll, "send_email_async", lambda to, *a, **k: sent.append(to))
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET email='tst001@test.local' WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.close()
    return sent


def _session(client, username, role):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _token(client, user):
    return client.post("/api/login", json={"username": user["username"], "password": user["password"]}).get_json()["token"]


def test_hr_cannot_send_payslip_emails_on_the_web(client, seed_hr_admin, seed_employee, mail):
    from utils.auth import HR_ROLE
    _session(client, seed_hr_admin["username"], HR_ROLE)
    resp = client.post("/send_salary_email", data={"emp_id": seed_employee["employee_id"], "year": 2026, "month": 9})
    assert resp.status_code == 403 and mail == []


def test_hr_token_cannot_send_payslip_emails(client, seed_hr_admin, seed_employee, mail):
    resp = client.post("/api/send_salary_email", json={"emp_id": seed_employee["employee_id"], "year": 2026, "month": 9},
                       headers={"Authorization": f"Bearer {_token(client, seed_hr_admin)}"})
    assert resp.status_code == 403 and mail == []


def test_admin_still_sends_them(client, seed_admin, seed_employee, mail):
    _session(client, seed_admin["username"], "admin")
    assert client.post("/send_salary_email", data={"emp_id": seed_employee["employee_id"], "year": 2026,
                                                   "month": 9}).get_json()["ok"] is True
    resp = client.post("/api/send_salary_email", json={"emp_id": seed_employee["employee_id"], "year": 2026, "month": 9},
                       headers={"Authorization": f"Bearer {_token(client, seed_admin)}"})
    assert resp.get_json()["ok"] is True and len(mail) == 2
