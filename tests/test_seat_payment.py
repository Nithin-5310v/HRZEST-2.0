# -*- coding: utf-8 -*-
"""Paying for a new employee: when every paid seat is in use, the Employees
page asks for one month at the per-employee rate before the employee is
registered (static/js/seat-pay.js -> blueprints/seats.py); from the next
billing date the monthly bill / Auto-Debit includes them, all employees in
one payment; and each paid period starts over with the employees it was
paid for.

The suite's one tenant (conftest.py) is shared: every test puts back what it
changes.
"""
import os
import secrets

import pytest

from extensions import app as flask_app
from utils.helpers import invalidate_settings_cache
from utils.plan_limits import get_per_employee_paise, get_tenant_employee_count

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = os.environ["DB_NAME"]


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


@pytest.fixture
def seat_cap(db_engine):
    def _set(cap):
        cur = db_engine.cursor()
        cur.execute("UPDATE company_settings SET paid_employee_slots=%s WHERE id=1", (cap,))
        cur.close()
        invalidate_settings_cache()
    yield _set
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET paid_employee_slots=NULL WHERE id=1")
    cur.execute("DELETE FROM att_master.seat_topup_orders WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()
    invalidate_settings_cache()


def _paid_seats(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT paid_employee_slots FROM company_settings WHERE id=1")
    value = cur.fetchone()[0]
    cur.close()
    return value


class TestSeatPrice:
    def test_one_seat_is_one_month_at_the_per_employee_rate(self, client, seed_admin, seat_cap, monkeypatch):
        # a base fee / minimum belongs to the monthly bill, not to each extra employee
        monkeypatch.setattr("utils.plan_limits.get_base_fee_paise", lambda: 50000)
        monkeypatch.setattr("utils.plan_limits.get_minimum_monthly_paise", lambda: 100000)
        seat_cap(1)
        _admin_session(client, seed_admin["username"])
        data = client.post("/api/seats/create_order", json={"additional_seats": 3}).get_json()
        assert data["ok"] and data["amount_paise"] == 3 * get_per_employee_paise()


class TestEmployeesPage:
    def test_asks_for_payment_when_every_seat_is_in_use(self, client, seed_admin, seat_cap):
        seat_cap(get_tenant_employee_count(TENANT_SCHEMA))
        _admin_session(client, seed_admin["username"])
        resp = client.get("/employees")
        html = resp.get_data(as_text=True)
        assert 'data-seat-pay="1"' in html and "for this month, paid now" in html
        assert "js/seat-pay.js" in html
        # Razorpay Checkout may load on this page
        assert "https://checkout.razorpay.com" in resp.headers["Content-Security-Policy"]

    def test_no_payment_while_a_paid_seat_is_free(self, client, seed_admin, seat_cap):
        seat_cap(get_tenant_employee_count(TENANT_SCHEMA) + 1)
        _admin_session(client, seed_admin["username"])
        html = client.get("/employees").get_data(as_text=True)
        assert 'data-seat-pay="1"' not in html and "Register Employee" in html

    def test_no_payment_without_a_seat_limit(self, client, seed_admin, seat_cap):
        seat_cap(None)
        _admin_session(client, seed_admin["username"])
        assert 'data-seat-pay="1"' not in client.get("/employees").get_data(as_text=True)

    def test_paying_frees_a_seat_for_the_new_employee(self, client, seed_admin, seat_cap):
        count = get_tenant_employee_count(TENANT_SCHEMA)
        seat_cap(count)
        _admin_session(client, seed_admin["username"])
        order = client.post("/api/seats/create_order", json={"additional_seats": 1}).get_json()
        resp = client.post("/api/seats/verify_payment", json={
            "razorpay_order_id": order["order_id"], "razorpay_payment_id": "demo_payment",
            "razorpay_signature": "demo_signature"})
        assert resp.get_json()["ok"]
        with flask_app.test_request_context():
            from flask import g
            g.tenant_db = TENANT_SCHEMA
            from blueprints.seats import seat_payment_info
            info = seat_payment_info()
        assert info["needed"] is False and info["free"] == 1

    def test_registration_still_refused_without_payment(self, client, seed_admin, seat_cap, monkeypatch):
        monkeypatch.setattr("blueprints.employees.registration.validate_employee_email_domain", lambda e: None)
        count = get_tenant_employee_count(TENANT_SCHEMA)
        seat_cap(count)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_employee_page", data={
            "name": "Unpaid Person", "emp_id": "UNPAID" + secrets.token_hex(2), "role": "Staff"},
            follow_redirects=True)
        assert "paid employee seat" in resp.get_data(as_text=True)
        assert get_tenant_employee_count(TENANT_SCHEMA) == count


class TestPaidPeriodStartsOver:
    def test_a_paid_charge_sets_seats_to_the_employees_paid_for(self, db_engine, seat_cap, monkeypatch):
        from blueprints.auto_debit import _record_charge
        count = get_tenant_employee_count(TENANT_SCHEMA)
        seat_cap(count + 3)  # three seats freed by people who left
        sub_id = "sub_" + secrets.token_hex(4)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
            "razorpay_subscription_id, quantity_synced, status, activated_at, created_at) "
            "VALUES (%s,'Test Co','cust_x',%s,%s,'active',NOW(),NOW())", (TENANT_SCHEMA, sub_id, count))
        cur.close()
        monkeypatch.setattr("blueprints.auto_debit.on_payment_completed", lambda *a: None)
        with flask_app.app_context():
            _record_charge(sub_id, count * get_per_employee_paise(), "pay_" + secrets.token_hex(6), "paid")
        assert _paid_seats(db_engine) == count

    def test_companies_without_a_limit_stay_unlimited(self, db_engine, seat_cap):
        from blueprints.seats import reset_paid_seats
        seat_cap(None)
        reset_paid_seats(TENANT_SCHEMA)
        assert _paid_seats(db_engine) is None
