# -*- coding: utf-8 -*-
"""Billing on the plan date -- utils/billing_cycle.py and everything that
follows it: a company that took its plan on the 10th is billed, chased,
reminded and charged by Auto-Debit on the 10th of every month; companies from
before plan dates (billing_anchor_date NULL) keep renewing on the 1st.

The suite's one tenant row (conftest.py) is shared: every test restores the
columns it changes.
"""
import datetime
import os
import secrets

import pytest

from extensions import app as flask_app
from utils import billing_cycle as bc

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = os.environ["DB_NAME"]
D = datetime.date
_COLUMNS = ("billing_state", "grace_period_ends_at", "locked_at", "payment_option", "subscription_status",
            "renewal_reminder_for", "created_at", "billing_anchor_date")


@pytest.fixture
def tenant(db_engine):
    """The suite's tenant row, put back afterwards; its mandate and bills are
    cleared before and after."""
    cur = db_engine.cursor()
    cur.execute(f"SELECT id, {', '.join(_COLUMNS)} FROM att_master.tenants WHERE db_name=%s",  # nosec B608
                (TENANT_SCHEMA,))
    before = cur.fetchone()
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()
    yield before[0]
    cur = db_engine.cursor()
    cur.execute(f"UPDATE att_master.tenants SET {', '.join(c + '=%s' for c in _COLUMNS)} WHERE db_name=%s",  # nosec B608
                (*before[1:], TENANT_SCHEMA))
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()


def _set(db_engine, **cols):
    cur = db_engine.cursor()
    cur.execute(f"UPDATE att_master.tenants SET {', '.join(c + '=%s' for c in cols)} WHERE db_name=%s",  # nosec B608
                (*cols.values(), TENANT_SCHEMA))
    cur.close()


def _tenant_value(db_engine, col):
    cur = db_engine.cursor()
    cur.execute(f"SELECT {col} FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))  # nosec B608
    value = cur.fetchone()[0]
    cur.close()
    return value


def _paid_invoice(db_engine, billing_period, created_at):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
        "razorpay_payment_id, status, billing_period, created_at) VALUES (%s,'Test Co',1,9900,%s,'paid',%s,%s)",
        (TENANT_SCHEMA, "pay_" + secrets.token_hex(6), billing_period, created_at))
    cur.close()


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


class TestCycleDates:
    def test_same_day_every_month(self):
        anchor = D(2026, 11, 10)
        assert bc.period_start(anchor, D(2026, 11, 25)) == D(2026, 11, 10)
        assert bc.next_billing_date(anchor, D(2026, 11, 25)) == D(2026, 12, 10)
        assert bc.period_start(anchor, D(2026, 12, 9)) == D(2026, 11, 10)
        assert bc.period_start(anchor, D(2026, 12, 10)) == D(2026, 12, 10)
        assert bc.next_billing_date(anchor, D(2027, 1, 3)) == D(2027, 1, 10)
        assert bc.period_end(anchor, D(2026, 11, 10)) == D(2026, 12, 9)

    def test_31st_uses_the_last_day_and_does_not_drift(self):
        anchor = D(2026, 1, 31)
        assert bc.next_billing_date(anchor, D(2026, 2, 10)) == D(2026, 2, 28)
        assert bc.next_billing_date(anchor, D(2026, 3, 1)) == D(2026, 3, 31)
        assert bc.period_start(anchor, D(2026, 4, 30)) == D(2026, 4, 30)
        assert bc.next_billing_date(anchor, D(2026, 4, 30)) == D(2026, 5, 31)

    def test_before_the_plan_starts(self):
        anchor = D(2026, 12, 1)
        assert bc.period_start(anchor, D(2026, 11, 20)) == anchor
        assert bc.next_billing_date(anchor, D(2026, 11, 20)) == anchor

    def test_old_companies_renew_on_the_1st(self):
        anchor, explicit = bc.anchor_from(None, datetime.datetime(2026, 9, 15, 12, 0))
        assert (anchor, explicit) == (D(2026, 9, 1), False)
        assert bc.next_billing_date(anchor, D(2026, 11, 20)) == D(2026, 12, 1)

    def test_auto_debit_starts_at_10am_india_time(self):
        start_at = bc.charge_epoch(D(2026, 12, 10))
        assert datetime.datetime.fromtimestamp(start_at, datetime.timezone.utc) == \
            datetime.datetime(2026, 12, 10, 4, 30, tzinfo=datetime.timezone.utc)

    def test_which_period_a_charge_pays_for(self):
        # on the day, a little late, a little early (Razorpay on a 31st plan)
        assert bc.charge_period(D(2026, 11, 10), True, D(2026, 12, 10)) == D(2026, 12, 10)
        assert bc.charge_period(D(2026, 11, 10), True, D(2026, 12, 14)) == D(2026, 12, 10)
        assert bc.charge_period(D(2026, 1, 31), True, D(2026, 3, 29)) == D(2026, 3, 31)
        # old companies: the calendar month, as before
        assert bc.charge_period(D(2026, 11, 1), False, D(2026, 11, 28)) == D(2026, 11, 1)

    def test_first_period_paid_at_signup(self):
        assert bc.first_period_prepaid(D(2026, 11, 10), True, datetime.datetime(2026, 11, 10, 9, 0))
        # a trial that turned paid a month after the company was created
        assert not bc.first_period_prepaid(D(2026, 12, 10), True, datetime.datetime(2026, 11, 10, 9, 0))
        assert bc.first_period_prepaid(D(2026, 11, 1), False, datetime.datetime(2026, 11, 18, 9, 0))


class TestOverdueOnThePlanDate:
    @pytest.fixture
    def notices(self, monkeypatch):
        sent = []
        monkeypatch.setattr("blueprints.billing_dunning._notify_tenant", lambda *a, **k: sent.append(a[2]))
        return sent

    def _check(self, tenant_id, today, created_at, anchor):
        from blueprints.billing_dunning import _check_one_tenant
        with flask_app.app_context():
            _check_one_tenant(tenant_id, "Test Co", TENANT_SCHEMA, "current", None, created_at, today,
                              anchor_date=anchor)

    def test_unpaid_period_is_chased_from_the_plan_date(self, db_engine, tenant, notices):
        _set(db_engine, billing_state="current")
        self._check(tenant, D(2026, 11, 12), datetime.datetime(2026, 9, 5), D(2026, 10, 10))
        assert _tenant_value(db_engine, "billing_state") == "grace"
        assert _tenant_value(db_engine, "grace_period_ends_at") == datetime.datetime(2026, 11, 15)
        assert notices == ["grace"]

    def test_first_period_after_a_paid_signup_is_not_chased(self, db_engine, tenant, notices):
        _set(db_engine, billing_state="current")
        self._check(tenant, D(2026, 11, 25), datetime.datetime(2026, 11, 10, 11, 0), D(2026, 11, 10))
        assert _tenant_value(db_engine, "billing_state") == "current"
        assert notices == []

    def test_paid_period_stays_current(self, db_engine, tenant, notices):
        _set(db_engine, billing_state="current")
        _paid_invoice(db_engine, D(2026, 11, 10), datetime.datetime(2026, 11, 10, 10, 5))
        self._check(tenant, D(2026, 11, 12), datetime.datetime(2026, 10, 10), D(2026, 10, 10))
        assert _tenant_value(db_engine, "billing_state") == "current"

    def test_old_company_is_still_chased_from_the_1st(self, db_engine, tenant, notices):
        _set(db_engine, billing_state="current")
        self._check(tenant, D(2026, 11, 3), datetime.datetime(2026, 9, 15), None)
        assert _tenant_value(db_engine, "grace_period_ends_at") == datetime.datetime(2026, 11, 6)

    def test_nothing_due_before_the_plan_starts(self, db_engine, tenant, notices):
        _set(db_engine, billing_state="current")
        self._check(tenant, D(2026, 11, 20), datetime.datetime(2026, 10, 1), D(2026, 12, 1))
        assert _tenant_value(db_engine, "billing_state") == "current"


class TestAutoDebitStartsOnTheBillingDate:
    def test_paid_period_first_charge_waits_for_the_next_billing_date(
            self, client, db_engine, tenant, seed_admin, monkeypatch):
        from utils.helpers import platform_today
        today = platform_today()
        _set(db_engine, subscription_status="active", billing_anchor_date=today,
             created_at=datetime.datetime.combine(today, datetime.time(9, 0)))
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.auto_debit._get_or_create_plan_id", lambda: ("plan_x", None))
        monkeypatch.setattr("blueprints.auto_debit.create_customer", lambda *a, **k: ("cust_x", None))
        calls = []
        monkeypatch.setattr("blueprints.auto_debit.create_subscription",
                            lambda *a, **k: calls.append(k) or ("sub_" + secrets.token_hex(4), None))
        _admin_session(client, seed_admin["username"])
        data = client.post("/api/auto_debit/enroll", json={}).get_json()
        next_date = bc.next_billing_date(today, today)
        assert data["ok"] and data["first_charge_on"] == next_date.isoformat()
        assert calls == [{"start_at": bc.charge_epoch(next_date)}]
        cur = db_engine.cursor()
        cur.execute("SELECT first_charge_on FROM att_master.auto_debit_mandates WHERE tenant_schema=%s",
                    (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == next_date
        cur.close()

    def test_unpaid_period_charges_now_and_that_day_becomes_the_plan_date(
            self, client, db_engine, tenant, seed_admin):
        from utils.helpers import platform_today
        today = platform_today()
        _set(db_engine, subscription_status="active", billing_anchor_date=today - datetime.timedelta(days=40),
             created_at=datetime.datetime.combine(today - datetime.timedelta(days=70), datetime.time(9, 0)))
        _admin_session(client, seed_admin["username"])
        data = client.post("/api/auto_debit/enroll", json={}).get_json()
        assert data["ok"] and data["first_charge_on"] is None
        resp = client.post("/api/auto_debit/confirm", json={
            "razorpay_subscription_id": data["subscription_id"],
            "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x"})
        assert resp.get_json()["ok"]
        assert _tenant_value(db_engine, "billing_anchor_date") == today

    def test_trial_charges_now(self, client, db_engine, tenant, seed_admin):
        _set(db_engine, subscription_status="trialing")
        _admin_session(client, seed_admin["username"])
        assert client.post("/api/auto_debit/enroll", json={}).get_json()["first_charge_on"] is None


class TestChargeRecordedForItsPeriod:
    def test_charge_a_day_early_pays_the_coming_period(self, db_engine, tenant, monkeypatch):
        from blueprints.auto_debit import _record_charge
        _set(db_engine, billing_anchor_date=D(2026, 10, 10))
        sub_id = "sub_" + secrets.token_hex(4)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
            "razorpay_subscription_id, quantity_synced, status, activated_at, created_at) "
            "VALUES (%s,'Test Co','cust_x',%s,1,'active',NOW(),NOW())", (TENANT_SCHEMA, sub_id))
        cur.close()
        monkeypatch.setattr("blueprints.auto_debit.on_payment_completed", lambda *a: None)
        payment_id = "pay_" + secrets.token_hex(6)
        with flask_app.app_context():
            _record_charge(sub_id, 9900, payment_id, "paid", charged_at=bc.charge_epoch(D(2026, 12, 9)))
        cur = db_engine.cursor()
        cur.execute("SELECT billing_period FROM att_master.monthly_invoices WHERE razorpay_payment_id=%s",
                    (payment_id,))
        assert cur.fetchone()[0] == D(2026, 12, 10)
        cur.close()


class TestRemindersAndBillingPage:
    def test_renewal_reminder_three_days_before_the_plan_date(self, db_engine, tenant, monkeypatch):
        import utils.billing_notices as notices
        _set(db_engine, payment_option="online", subscription_status="active", renewal_reminder_for=None,
             billing_anchor_date=D(2026, 10, 13))
        reminded = []
        monkeypatch.setattr(notices, "_remind_one", lambda tid, db, renews_on: reminded.append((db, renews_on)))
        with flask_app.app_context():
            notices._run_renewal_reminders(today=D(2026, 11, 10))
            notices._run_renewal_reminders(today=D(2026, 11, 28))  # the 1st isn't this company's date
        assert [r for r in reminded if r[0] == TENANT_SCHEMA] == [(TENANT_SCHEMA, D(2026, 11, 13))]

    def test_billing_page_period_follows_the_plan_date(self, db_engine, tenant):
        from utils.helpers import platform_today
        from utils.payment_history import get_plan_details
        today = platform_today()
        anchor = today - datetime.timedelta(days=5)
        _set(db_engine, payment_option="online", subscription_status="active", billing_anchor_date=anchor)
        with flask_app.app_context():
            plan = get_plan_details(TENANT_SCHEMA)
        assert plan["period_start"] == bc.period_start(anchor, today)
        assert plan["renews_on"] == bc.next_billing_date(anchor, today)
