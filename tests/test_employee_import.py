# -*- coding: utf-8 -*-
"""Registering employees from an Excel sheet -- blueprints/employees/bulk_import.py.

The template downloads; an uploaded sheet is checked row by row (bad rows are
listed with the reason and skipped, the rest registered); a company with a seat
limit pays one month per employee beyond its free paid seats before they're
added; each employee gets the same set-up as Add Employee.

Employees created here are deleted afterwards; the seat limit is put back.
"""
import io
import os
import secrets

import pytest
from openpyxl import Workbook, load_workbook

from utils.helpers import invalidate_settings_cache
from utils.plan_limits import format_price_inr, get_per_employee_paise, get_tenant_employee_count

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = os.environ["DB_NAME"]
HEADERS = ["Full Name *", "Employee ID (leave blank for automatic)", "Email", "Phone", "Role / Designation",
           "Department", "Date of Joining (YYYY-MM-DD)", "Work Mode (office / wfh)",
           "Gender (Male / Female / Other)", "Date of Birth (YYYY-MM-DD)", "Monthly Salary"]


def _session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _sheet(rows, headers=HEADERS):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _upload(client, url, rows, name="staff.xlsx", headers=HEADERS):
    return client.post(url, data={"file": (_sheet(rows, headers), name)}, content_type="multipart/form-data")


@pytest.fixture
def tag():
    """A unique Employee ID prefix for this test's rows."""
    return "IMP" + secrets.token_hex(3).upper()


@pytest.fixture
def cleanup(db_engine, monkeypatch):
    """Deletes employees made by the test; no real emails; seat limit put back."""
    monkeypatch.setattr("blueprints.employees.bulk_import.validate_employee_email_domain", lambda e: None)
    made = []
    yield made
    cur = db_engine.cursor()
    ids = [m for m in made]
    if ids:
        for table in ("salary_config", "leave_balances", "employee_onboarding_tasks", "employee_onboarding"):
            try:
                cur.execute(f"DELETE FROM {table} WHERE employee_id = ANY(%s)", (ids,))  # nosec B608 -- fixed names
            except Exception:  # nosec B110 -- table may not exist in every schema version
                pass
        cur.execute("DELETE FROM employees WHERE employee_id = ANY(%s)", (ids,))
    cur.execute("UPDATE company_settings SET paid_employee_slots=NULL WHERE id=1")
    cur.execute("DELETE FROM att_master.seat_topup_orders WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()
    invalidate_settings_cache()


def _set_cap(db_engine, cap):
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET paid_employee_slots=%s WHERE id=1", (cap,))
    cur.close()
    invalidate_settings_cache()


class TestTemplate:
    def test_downloads_an_xlsx_with_the_headings(self, client, seed_admin):
        _session(client, seed_admin["username"])
        resp = client.get("/employees/import/template")
        assert resp.status_code == 200
        ws = load_workbook(io.BytesIO(resp.data)).active
        assert [c.value for c in ws[1]] == HEADERS

    def test_needs_a_signed_in_admin(self, client):
        assert client.get("/employees/import/template").status_code in (302, 401)


class TestPreview:
    def test_good_and_bad_rows(self, client, seed_admin, cleanup, tag):
        _session(client, seed_admin["username"])
        resp = _upload(client, "/api/employees/import/preview", [
            ["Asha Rao", f"{tag}1", f"asha.{tag}@example.com", "9876543210", "Clerk", "Ops", "2026-11-15",
             "office", "Female", "1996-01-02", 25000],
            ["", f"{tag}2", "", "", "", "", "", "", "", "", ""],                 # no name
            ["Bad Email", "", "not-an-email", "", "", "", "", "", "", "", ""],
            ["Twin", f"{tag}1", "", "", "", "", "", "", "", "", ""],              # same ID as row 2
            ["Odd Date", "", "", "", "", "", "15th Nov", "", "", "", ""],
            ["Odd Mode", "", "", "", "", "", "", "hybrid", "", "", ""],
            [None] * 11,                                                           # blank: ignored
            ["Ravi Kumar", "", "", "", "", "", "15/11/2026", "wfh", "male", "", "30,000"],
        ])
        data = resp.get_json()
        assert data["ok"]
        assert [r["name"] for r in data["ready"]] == ["Asha Rao", "Ravi Kumar"]
        problems = {p["row"]: p["msg"] for p in data["problems"]}
        assert set(problems) == {3, 4, 5, 6, 7}
        assert "Full Name is missing" in problems[3]
        assert "isn't an email address" in problems[4]
        assert "also on row 2" in problems[5]
        assert "isn't a date" in problems[6]
        assert "office or wfh" in problems[7]
        assert data["payment"]["to_pay"] == 0  # no seat limit

    def test_existing_employee_id_and_email_are_refused(self, client, seed_admin, cleanup, tag):
        _session(client, seed_admin["username"])
        email = f"first.{tag.lower()}@example.com"
        first = _upload(client, "/api/employees/import",
                        [["First", f"{tag}1", email, "", "", "", "", "", "", "", ""]]).get_json()
        cleanup.extend(c["emp_id"] for c in first.get("created", []))
        resp = _upload(client, "/api/employees/import/preview",
                       [["Copy", f"{tag}1", email.upper(), "", "", "", "", "", "", "", ""]])
        (problem,) = resp.get_json()["problems"]
        assert "already taken" in problem["msg"] and "already belongs to an employee" in problem["msg"]

    def test_only_xlsx(self, client, seed_admin):
        _session(client, seed_admin["username"])
        resp = client.post("/api/employees/import/preview", data={"file": (io.BytesIO(b"a,b"), "staff.csv")},
                           content_type="multipart/form-data")
        assert resp.status_code == 400 and ".xlsx" in resp.get_json()["msg"]

    def test_needs_the_headings(self, client, seed_admin):
        _session(client, seed_admin["username"])
        resp = _upload(client, "/api/employees/import/preview", [["x"]], headers=["Something else"])
        assert resp.status_code == 400 and "Full Name" in resp.get_json()["msg"]

    def test_shows_what_to_pay_when_seats_run_out(self, client, db_engine, seed_admin, cleanup, tag):
        _set_cap(db_engine, get_tenant_employee_count(TENANT_SCHEMA) + 1)  # one free seat
        _session(client, seed_admin["username"])
        rows = [[f"Person {i}", f"{tag}{i}", "", "", "", "", "", "", "", "", ""] for i in range(3)]
        pay = _upload(client, "/api/employees/import/preview", rows).get_json()["payment"]
        assert pay["free"] == 1 and pay["to_pay"] == 2
        assert pay["amount_display"] == format_price_inr(2 * get_per_employee_paise())


class TestImport:
    def test_registers_ready_rows_and_skips_the_rest(self, client, db_engine, seed_admin, cleanup, tag, monkeypatch):
        monkeypatch.setattr("blueprints.employees.bulk_import.get_email_config", lambda: {"provider": "smtp"})
        sent = []
        monkeypatch.setattr("blueprints.employees.bulk_import._send_welcome_credentials_email",
                            lambda emp_id, name, email, password: sent.append((emp_id, email)) or True)
        _session(client, seed_admin["username"])
        before = get_tenant_employee_count(TENANT_SCHEMA)
        resp = _upload(client, "/api/employees/import", [
            ["Asha Rao", f"{tag}1", f"asha.{tag.lower()}@example.com", "", "Clerk", "Ops", "2026-11-15",
             "office", "Female", "", 26000],
            ["", "", "", "98765 43210", "", "", "", "", "", "", ""],  # no name
            ["Ravi Kumar", "", "", "", "", "", "", "wfh", "", "", ""],
        ])
        data = resp.get_json()
        cleanup.extend(c["emp_id"] for c in data.get("created", []))
        assert data["ok"], data
        assert [c["name"] for c in data["created"]] == ["Asha Rao", "Ravi Kumar"]
        assert data["created"][0]["emp_id"] == f"{tag}1"
        assert data["created"][1]["emp_id"]  # made automatically
        assert all(c["password"] for c in data["created"])
        assert [p["row"] for p in data["problems"]] == [3]
        assert get_tenant_employee_count(TENANT_SCHEMA) == before + 2
        assert sent == [(f"{tag}1", f"asha.{tag.lower()}@example.com")] and data["emailing"] == 1
        cur = db_engine.cursor()
        cur.execute("SELECT name, work_mode, department, force_pin_change FROM employees WHERE employee_id=%s",
                    (f"{tag}1",))
        assert cur.fetchone() == ("Asha Rao", "office", "Ops", 1)
        cur.execute("SELECT COUNT(*) FROM salary_config WHERE employee_id=%s", (f"{tag}1",))
        assert cur.fetchone()[0] == 1
        cur.close()

    def test_asks_for_payment_first_when_seats_run_out(self, client, db_engine, seed_admin, cleanup, tag):
        count = get_tenant_employee_count(TENANT_SCHEMA)
        _set_cap(db_engine, count)  # every paid seat in use
        _session(client, seed_admin["username"])
        rows = [[f"Person {i}", f"{tag}{i}", "", "", "", "", "", "", "", "", ""] for i in range(2)]
        resp = _upload(client, "/api/employees/import", rows)
        assert resp.status_code == 402 and resp.get_json()["payment"]["to_pay"] == 2
        assert get_tenant_employee_count(TENANT_SCHEMA) == count

        # pay for the two seats (test mode), then the same sheet goes in
        order = client.post("/api/seats/create_order", json={"additional_seats": 2}).get_json()
        assert order["amount_paise"] == 2 * get_per_employee_paise()
        assert client.post("/api/seats/verify_payment", json={
            "razorpay_order_id": order["order_id"], "razorpay_payment_id": "demo_payment",
            "razorpay_signature": "demo_signature"}).get_json()["ok"]
        data = _upload(client, "/api/employees/import", rows).get_json()
        cleanup.extend(c["emp_id"] for c in data.get("created", []))
        assert data["ok"] and len(data["created"]) == 2
        assert get_tenant_employee_count(TENANT_SCHEMA) == count + 2

    def test_automatic_ids_skip_ids_typed_in_the_sheet(self, client, db_engine, seed_admin, cleanup):
        cur = db_engine.cursor()
        cur.execute("SELECT COALESCE(company_code,'') FROM company_settings LIMIT 1")
        prefix = (cur.fetchone()[0] or "").strip().upper() or "EMP"
        cur.close()
        from blueprints.employees.registration import _max_emp_seq
        from database import get_db_connection
        from extensions import app as flask_app
        with flask_app.app_context():
            db = get_db_connection()
            c = db.cursor(buffered=True)
            nxt = _max_emp_seq(c, prefix) + 1
            c.close()
            db.close()
        typed = f"{prefix}{nxt:03d}"  # the ID the first blank row would otherwise get
        _session(client, seed_admin["username"])
        data = _upload(client, "/api/employees/import", [
            ["Auto One", "", "", "", "", "", "", "", "", "", ""],
            ["Typed", typed, "", "", "", "", "", "", "", "", ""],
        ]).get_json()
        cleanup.extend(c["emp_id"] for c in data.get("created", []))
        ids = {c["name"]: c["emp_id"] for c in data["created"]}
        assert ids["Typed"] == typed and ids["Auto One"] != typed and not data["problems"]

    def test_hr_imports_are_assigned_to_that_hr(self, client, db_engine, seed_admin, cleanup, tag):
        _session(client, seed_admin["username"], role="hr")
        data = _upload(client, "/api/employees/import",
                       [["Meena", f"{tag}1", "", "", "", "", "", "", "", "", ""]]).get_json()
        cleanup.extend(c["emp_id"] for c in data.get("created", []))
        cur = db_engine.cursor()
        cur.execute("SELECT assigned_hr_username FROM employees WHERE employee_id=%s", (f"{tag}1",))
        assert cur.fetchone()[0] == seed_admin["username"]
        cur.close()

    def test_nothing_ready_is_refused(self, client, seed_admin, cleanup):
        _session(client, seed_admin["username"])
        resp = _upload(client, "/api/employees/import", [["", "", "", "", "", "", "", "", "", "", ""],
                                                         ["", "X Y", "", "", "", "", "", "", "", "", ""]])
        assert resp.status_code == 400 and resp.get_json()["problems"]


class TestEmployeesPage:
    def test_has_the_import_button_and_modal(self, client, seed_admin):
        _session(client, seed_admin["username"])
        html = client.get("/employees").get_data(as_text=True)
        assert "Import from Excel" in html and 'id="importModal"' in html
        assert "js/employee-import.js" in html and "/employees/import/template" in html
