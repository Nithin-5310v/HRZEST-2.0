"""The Employees page's payment flows in a real browser (Chromium, the app's
own CSP, every request served by the Flask test client -- same set-up as
tests/test_settings_a11y_tabs.py), Razorpay in test mode:

- every paid seat in use: Add Employee asks for one month, then registers;
- Import from Excel: check the sheet, pay for the extra seats, register.
"""
import io
import os
import secrets

import pytest
from openpyxl import Workbook

from utils.helpers import invalidate_settings_cache
from utils.plan_limits import get_tenant_employee_count

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = os.environ["DB_NAME"]


@pytest.fixture
def full_seats(db_engine):
    """Every paid seat in use; employees this test adds are deleted after."""
    tag = "BRW" + secrets.token_hex(3).upper()
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET paid_employee_slots=%s WHERE id=1",
                (get_tenant_employee_count(TENANT_SCHEMA),))
    cur.close()
    invalidate_settings_cache()
    yield tag
    cur = db_engine.cursor()
    cur.execute("SELECT employee_id FROM employees WHERE employee_id LIKE %s OR name LIKE %s",
                (tag + "%", tag + "%"))
    ids = [r[0] for r in cur.fetchall()]
    for table in ("salary_config", "leave_balances"):
        try:
            cur.execute(f"DELETE FROM {table} WHERE employee_id = ANY(%s)", (ids,))  # nosec B608 -- fixed names
        except Exception:  # nosec B110 -- table may not exist in every schema version
            pass
    cur.execute("DELETE FROM employees WHERE employee_id = ANY(%s)", (ids,))
    cur.execute("UPDATE company_settings SET paid_employee_slots=NULL WHERE id=1")
    cur.execute("DELETE FROM att_master.seat_topup_orders WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()
    invalidate_settings_cache()


@pytest.fixture
def employees_page(client, seed_admin, full_seats):
    from extensions import app as flask_app
    client = flask_app.test_client()
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})

    def forward(route):
        req = route.request
        # Redirects are followed here: Chromium follows a fulfilled redirect
        # outside this route, where the made-up host doesn't resolve.
        resp = client.open(req.url.split("hz.test", 1)[1], method=req.method, data=req.post_data_buffer,
                           headers={"Content-Type": req.headers.get("content-type", "")}, follow_redirects=True)
        route.fulfill(status=resp.status_code, body=resp.get_data(),
                      headers={k: v for k, v in resp.headers.items()
                               if k.lower() not in ("content-length", "set-cookie", "location")})

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page()
        page.route("http://hz.test/**", forward)
        dialogs = []

        def _accept(dialog):
            dialogs.append(dialog.message)
            dialog.accept()
        page.on("dialog", _accept)
        page.goto("http://hz.test/employees")
        page.wait_for_function("() => !!window.HZSeatPay")
        yield page, full_seats, dialogs
        browser.close()


def test_add_employee_pays_then_registers(employees_page, monkeypatch):
    monkeypatch.setattr("blueprints.employees.registration.validate_employee_email_domain", lambda e: None)
    page, tag, dialogs = employees_page
    before = get_tenant_employee_count(TENANT_SCHEMA)
    page.evaluate("() => openModal('addModal')")
    assert page.locator("#addSeatPayNote").is_visible()
    form = page.locator("#addModal form")
    submit = form.locator("button[type=submit]")
    assert "Pay" in submit.inner_text()
    page.wait_for_function("() => document.getElementById('addEmpId').value !== ''")  # auto ID filled in
    form.locator("input[name=name]").fill(tag + " Asha")
    form.locator("input[name=emp_id]").fill(tag + "1")
    form.locator("input[name=email]").fill(tag.lower() + "@example.com")
    form.locator("input[name=date_of_joining]").fill("2026-11-15")
    submit.click()
    page.wait_for_selector("text=registered! ID: " + tag + "1")  # back on the list, registered
    assert dialogs and "Test mode" in dialogs[0]  # the payment step came first
    assert get_tenant_employee_count(TENANT_SCHEMA) == before + 1


def _sheet(tag):
    wb = Workbook()
    ws = wb.active
    ws.append(["Full Name *", "Employee ID (leave blank for automatic)", "Email"])
    ws.append([tag + " Ravi", tag + "2", ""])
    ws.append([tag + " Meena", tag + "3", ""])
    ws.append(["", tag + "4", "bad"])  # skipped: no name, bad email
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_checks_pays_and_registers(employees_page):
    page, tag, dialogs = employees_page
    before = get_tenant_employee_count(TENANT_SCHEMA)
    page.evaluate("() => openModal('importModal')")
    page.set_input_files("#importFile", files=[{
        "name": "staff.xlsx", "buffer": _sheet(tag),
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}])
    page.click("#importCheckBtn")
    page.wait_for_selector("#importPreview:not([hidden])")
    assert "2 employees ready" in page.inner_text("#importSummary")
    assert "Row 4" in page.inner_text("#importProblemList")
    assert "Pay" in page.inner_text("#importGoBtn")
    page.click("#importGoBtn")
    page.wait_for_selector("#importResult:not([hidden])")
    assert dialogs and "Test mode" in dialogs[0]
    assert "2 employees registered" in page.inner_text("#importResultText")
    assert page.locator("#importCredsBtn").is_visible()
    assert get_tenant_employee_count(TENANT_SCHEMA) == before + 2
