# -*- coding: utf-8 -*-
"""Billing emails and the ended-trial lock:

- utils/billing_notices.py: the email a paid company's admins get every time
  an employee is added (new headcount, next bill, update Auto-Debit now or
  buy seats in bulk), and the renewal reminder 3 days before the 1st;
- /api/auto_debit/update_quantity: "Update Auto-Debit now";
- blueprints/trial_billing.py: a trial that ends with no payment method gets
  5 days' grace with a reminder each day, then the billing lock -- lifted by
  paying the overdue bill or switching on Auto-Debit.

The suite's one tenant row (conftest.py) is shared: every test restores the
columns it changes.
"""
import datetime
import os

import pytest

import utils.billing_notices as notices
import utils.email_utils as email_utils
from blueprints import trial_billing

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")


@pytest.fixture(autouse=True)
def _app_context():
    """The jobs normally run under their own app.app_context() (see each
    scheduler entry point); these tests call the inner steps directly."""
    from extensions import app as flask_app
    with flask_app.app_context():
        yield


TENANT_SCHEMA = os.environ["DB_NAME"]
_COLUMNS = ("billing_state", "grace_period_ends_at", "locked_at", "payment_option", "subscription_status",
            "renewal_reminder_for", "grace_reminder_sent_on", "trial_end_date")


@pytest.fixture
def tenant(db_engine):
    """The suite's tenant row, put back afterwards; its mandate is cleared."""
    cur = db_engine.cursor()
    cur.execute(f"SELECT {', '.join(_COLUMNS)} FROM att_master.tenants WHERE db_name=%s",  # nosec B608 -- fixed columns
                (TENANT_SCHEMA,))
    before = cur.fetchone()
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()
    yield
    cur = db_engine.cursor()
    cur.execute(f"UPDATE att_master.tenants SET {', '.join(c + '=%s' for c in _COLUMNS)} WHERE db_name=%s",  # nosec B608
                (*before, TENANT_SCHEMA))
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()


def _set(db_engine, **cols):
    cur = db_engine.cursor()
    cur.execute(f"UPDATE att_master.tenants SET {', '.join(c + '=%s' for c in cols)} WHERE db_name=%s",  # nosec B608
                (*cols.values(), TENANT_SCHEMA))
    cur.close()


def _row(db_engine, *cols):
    cur = db_engine.cursor()
    cur.execute(f"SELECT {', '.join(cols)} FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))  # nosec B608
    row = cur.fetchone()
    cur.close()
    return row


def _mandate(db_engine, quantity_synced, status="active"):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
        "razorpay_subscription_id, quantity_synced, status, activated_at, created_at) "
        "VALUES (%s,'Test Co','demo_cust_x','demo_sub_x',%s,%s,NOW(),NOW())",
        (TENANT_SCHEMA, quantity_synced, status),
    )
    cur.close()


@pytest.fixture
def mails(monkeypatch):
    """Every billing email sent, as (to, subject, html)."""
    sent = []

    def send(to, subject, body, cfg, **_):
        sent.append((to, subject, body))
    for mod in (notices, email_utils):
        monkeypatch.setattr(mod, "get_email_config", lambda: {"host": "smtp.test"})
        monkeypatch.setattr(mod, "get_billing_emails", lambda: ["owner@example.test"])
        monkeypatch.setattr(mod, "send_email_async", send)
    return sent


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


class TestEmployeeAddedEmail:
    def test_paid_company_on_auto_debit_is_offered_the_update(self, db_engine, tenant, mails):
        _set(db_engine, payment_option="online", subscription_status="active")
        _mandate(db_engine, quantity_synced=1)
        notices.notify_employee_added(db_name=TENANT_SCHEMA)
        (to, subject, body), = mails
        assert to == "owner@example.test" and "employees added" in subject
        assert "Update your Auto-Debit now" in body and "it's set for 1" in body
        assert "/att-test-suite/settings?tab=billing" in body
        assert "buying seats" in body  # pay for many at once

    def test_paid_company_without_auto_debit_is_offered_auto_debit(self, db_engine, tenant, mails):
        _set(db_engine, payment_option="manual", subscription_status="active")
        notices.notify_employee_added(added=3, db_name=TENANT_SCHEMA)
        (_, _, body), = mails
        assert "3 employees were added" in body and "Switch on Auto-Debit" in body

    @pytest.mark.parametrize("cols", [
        {"payment_option": "trial", "subscription_status": "trialing"},
        {"payment_option": "online", "subscription_status": "trialing"},
        {"payment_option": "free", "subscription_status": "active"},
    ])
    def test_trial_and_free_companies_get_nothing(self, db_engine, tenant, mails, cols):
        _set(db_engine, **cols)
        notices.notify_employee_added(db_name=TENANT_SCHEMA)
        assert mails == []

    def test_adding_an_employee_sends_it(self, client, db_engine, tenant, mails, seed_admin):
        _set(db_engine, payment_option="online", subscription_status="active")
        token = client.post("/api/login", json={"username": seed_admin["username"],
                                                "password": seed_admin["password"]}).get_json()["token"]
        emp_id = "BILLMAIL1"
        try:
            resp = client.post("/api/employee/signup", headers={"Authorization": f"Bearer {token}"}, json={
                "employee_id": emp_id, "name": "Bill Mail", "password": "Str0ng!Passw0rd"})
            assert resp.get_json()["ok"] is True
            assert len(mails) == 1 and "employees added" in mails[0][1]
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
            cur.close()


class TestUpdateAutoDebitNow:
    def test_updates_to_the_current_headcount(self, client, db_engine, tenant, seed_admin):
        from utils.plan_limits import get_tenant_employee_count
        _mandate(db_engine, quantity_synced=0)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/update_quantity", json={})
        count = get_tenant_employee_count(TENANT_SCHEMA)
        assert resp.status_code == 200 and resp.get_json()["employees"] == count
        cur = db_engine.cursor()
        cur.execute("SELECT quantity_synced FROM att_master.auto_debit_mandates WHERE tenant_schema=%s",
                    (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == count
        cur.close()

    def test_needs_an_active_mandate(self, client, tenant, seed_admin):
        _admin_session(client, seed_admin["username"])
        assert client.post("/api/auto_debit/update_quantity", json={}).status_code == 400

    def test_billing_tab_offers_the_button_when_behind(self, client, db_engine, tenant, seed_admin):
        from utils.plan_limits import get_tenant_employee_count
        _mandate(db_engine, quantity_synced=get_tenant_employee_count(TENANT_SCHEMA) + 3)
        _admin_session(client, seed_admin["username"])
        html = client.get("/settings?tab=billing").get_data(as_text=True)
        assert 'id="updateAutoDebitBtn"' in html and "Update it now" in html

    def test_no_button_when_up_to_date(self, client, db_engine, tenant, seed_admin):
        from utils.plan_limits import get_tenant_employee_count
        _mandate(db_engine, quantity_synced=get_tenant_employee_count(TENANT_SCHEMA))
        _admin_session(client, seed_admin["username"])
        html = client.get("/settings?tab=billing").get_data(as_text=True)
        assert 'id="updateAutoDebitBtn"' not in html


class TestRenewalReminder:
    def test_sent_three_days_before_the_first_once(self, db_engine, tenant, mails):
        _set(db_engine, payment_option="manual", subscription_status="active", renewal_reminder_for=None)
        today = datetime.date(2026, 11, 28)  # renews 1 Dec
        notices._run_renewal_reminders(today=today)
        notices._run_renewal_reminders(today=today)  # a second run sends nothing more
        (_, subject, body), = mails
        assert "01 Dec" in subject and "01 Dec 2026" in body
        assert "Employees can still mark attendance" in body
        assert _row(db_engine, "renewal_reminder_for")[0] == datetime.date(2026, 12, 1)

    def test_not_on_other_days(self, db_engine, tenant, mails):
        _set(db_engine, payment_option="manual", subscription_status="active", renewal_reminder_for=None)
        notices._run_renewal_reminders(today=datetime.date(2026, 11, 20))
        assert mails == []

    def test_auto_debit_companies_are_told_it_is_automatic(self, db_engine, tenant, mails):
        _set(db_engine, payment_option="online", subscription_status="active", renewal_reminder_for=None)
        _mandate(db_engine, quantity_synced=1)
        notices._run_renewal_reminders(today=datetime.date(2026, 11, 28))
        (_, _, body), = mails
        assert "charged automatically" in body


class TestEndedTrialGraceAndLock:
    def _ended_trial(self, db_engine):
        _set(db_engine, payment_option="trial", subscription_status="trialing", billing_state="current",
             grace_period_ends_at=None, locked_at=None, grace_reminder_sent_on=None)
        # On the database's clock, as app.py stamps it and _run_trial_check()
        # reads it: CI's test process runs on Asia/Kolkata (conftest), its
        # Postgres on UTC, so an hour ago by datetime.now() is still ahead.
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.tenants SET trial_end_date=NOW() - INTERVAL '1 hour' WHERE db_name=%s",
                    (TENANT_SCHEMA,))
        cur.close()

    def test_no_payment_method_starts_five_days_grace(self, db_engine, tenant, mails):
        self._ended_trial(db_engine)
        trial_billing._run_trial_check()
        state, sub, grace_ends = _row(db_engine, "billing_state", "subscription_status", "grace_period_ends_at")
        assert (state, sub) == ("grace", "past_due")
        assert datetime.timedelta(days=4, hours=23) < grace_ends - datetime.datetime.now() <= datetime.timedelta(days=5)
        (_, subject, body), = mails  # one email, not a second "reminder" the same day
        assert "trial has ended" in subject and "Employees can still mark their attendance" in body

    def test_reminds_once_a_day_during_grace(self, db_engine, tenant, mails):
        self._ended_trial(db_engine)
        trial_billing._run_trial_check()
        tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
        trial_billing._run_ended_trial_grace_check(now=tomorrow)
        trial_billing._run_ended_trial_grace_check(now=tomorrow)
        assert [m[1].split(":")[0] for m in mails] == ["Action needed", "Reminder"]

    def test_locks_after_grace(self, db_engine, tenant, mails):
        self._ended_trial(db_engine)
        trial_billing._run_trial_check()
        trial_billing._run_ended_trial_grace_check(now=datetime.datetime.now() + datetime.timedelta(days=6))
        assert _row(db_engine, "billing_state")[0] == "locked"
        assert "locked" in mails[-1][1]

    def test_paying_the_overdue_bill_makes_it_a_normal_payer(self, db_engine, tenant, mails):
        from blueprints.billing_dunning import _mark_invoice_paid_and_unlock
        _set(db_engine, payment_option="trial", subscription_status="past_due", billing_state="locked",
             locked_at=datetime.datetime.now())
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.monthly_invoices (tenant_schema, company_name, employee_count, "
                    "amount_paise, razorpay_order_id, status, billing_period) "
                    "VALUES (%s,'Test Co',1,9800,'order_trialpay','pending',%s)",
                    (TENANT_SCHEMA, datetime.date.today().replace(day=1)))
        cur.close()
        assert _mark_invoice_paid_and_unlock("order_trialpay", "pay_trialpay")
        assert _row(db_engine, "billing_state", "subscription_status", "payment_option") == \
            ("current", "active", "manual")
        trial_billing._run_ended_trial_grace_check()  # nothing left to chase
        assert _row(db_engine, "billing_state")[0] == "current"

    def test_switching_on_auto_debit_unlocks(self, client, db_engine, tenant, seed_admin):
        _set(db_engine, payment_option="trial", subscription_status="past_due", billing_state="locked",
             locked_at=datetime.datetime.now())
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
            "razorpay_subscription_id, quantity_synced, status, created_at) "
            "VALUES (%s,'Test Co','demo_cust_y','demo_sub_unlock',1,'pending',NOW())", (TENANT_SCHEMA,))
        cur.close()
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/confirm", json={
            "razorpay_subscription_id": "demo_sub_unlock", "razorpay_payment_id": "pay_demo", "razorpay_signature": "x"})
        assert resp.get_json()["ok"] is True
        assert _row(db_engine, "billing_state", "subscription_status", "payment_option") == \
            ("current", "active", "online")


class TestBillingRecipients:
    def test_only_admins_get_billing_mail_not_hr_or_managers(self, db_engine):
        """HR and manager staff have admin-panel accounts carrying their own
        email (auth.py); billing mail goes to the company's admins only."""
        from werkzeug.security import generate_password_hash
        rows = [("BILLADM", "admin", "owner-bill@example.test"), ("BILLHR1", "hr", "hr-bill@example.test"),
                ("BILLMGR", "manager", "mgr-bill@example.test")]
        cur = db_engine.cursor()
        for username, role, email in rows:
            cur.execute("INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,1)",
                        (username, generate_password_hash("x"), role, email))
        cur.close()
        try:
            billing = email_utils.get_billing_emails()
            assert "owner-bill@example.test" in billing
            assert "hr-bill@example.test" not in billing and "mgr-bill@example.test" not in billing
            assert "hr-bill@example.test" in email_utils.get_admin_emails()  # leave/document mail still reaches HR
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM admin_users WHERE username = ANY(%s)", ([r[0] for r in rows],))
            cur.close()
