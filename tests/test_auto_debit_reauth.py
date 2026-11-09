# -*- coding: utf-8 -*-
"""Auto-Debit set up with UPI Autopay or e-mandate (blueprints/auto_debit.py):
Razorpay refuses to change those subscriptions' amount, so a new headcount
needs a new authorization.

- "Update to N employees" answers needs_reauth (409) and remembers the
  mandate is fixed-amount, instead of "try again in a few minutes";
- enroll(replace) starts a replacement subscription while the current one
  keeps billing; it starts on the current one's next charge date, and
  confirming it cancels the current one at its cycle end;
- the daily job emails the admins once per headcount.

Shares the suite's tenant schema with tests/test_auto_debit.py, so every
test clears its mandate before and after.
"""
import datetime
import os
import secrets

import pytest

from blueprints import auto_debit
from utils.plan_limits import get_tenant_employee_count

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = os.environ["DB_NAME"]
UPI_ERROR = "Razorpay request failed: subscriptions cannot be updated when payment mode is UPI"
DOMESTIC_CARD_ERROR = ("Razorpay request failed: Only offers can be updated for subscriptions "
                       "when payment mode is domestic card.")


@pytest.fixture
def mandate(db_engine):
    """An active Auto-Debit set up for more employees than the company has
    now, so there's always a headcount change to apply."""
    def _make(subscription_id=None, customer_id="cust_reauth"):
        sub = subscription_id or "sub_reauth_" + secrets.token_hex(4)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
            "razorpay_subscription_id, quantity_synced, status, activated_at, created_at) "
            "VALUES (%s,'Test Co',%s,%s,%s,'active',NOW(),NOW())",
            (TENANT_SCHEMA, customer_id, sub, get_tenant_employee_count(TENANT_SCHEMA) + 5))
        cur.close()
        return sub
    _clear(db_engine)
    yield _make
    _clear(db_engine)


def _clear(db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()


def _row(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT status, razorpay_subscription_id, quantity_synced, fixed_amount, pending_subscription_id, "
                "pending_quantity, pending_start_at, reauth_notified_quantity FROM att_master.auto_debit_mandates "
                "WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    row = cur.fetchone()
    cur.close()
    return row


def _admin(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"


def test_recognises_razorpays_fixed_amount_errors():
    assert auto_debit._is_fixed_amount_error(UPI_ERROR)
    assert auto_debit._is_fixed_amount_error("subscriptions cannot be updated when payment mode is emandate.")
    assert auto_debit._is_fixed_amount_error(
        "Razorpay request failed: Only offers can be updated for subscriptions when payment mode is domestic card.")
    assert not auto_debit._is_fixed_amount_error("Request failed because another subscription operation is in progress")
    assert not auto_debit._is_fixed_amount_error(None)


class TestUpdateQuantity:
    def test_upi_mandate_asks_to_set_up_again(self, client, db_engine, seed_admin, mandate, monkeypatch):
        mandate()
        monkeypatch.setattr(auto_debit, "update_subscription_quantity", lambda *a: (False, UPI_ERROR))
        _admin(client, seed_admin)
        resp = client.post("/api/auto_debit/update_quantity", json={})
        body = resp.get_json()
        assert resp.status_code == 409 and body["needs_reauth"] is True
        assert "UPI, an Indian card or net banking" in body["msg"] and "nothing is charged twice" in body["msg"]
        status, _, quantity, fixed, *_ = _row(db_engine)
        assert fixed is True and quantity == get_tenant_employee_count(TENANT_SCHEMA) + 5

    def test_indian_card_asks_to_set_up_again(self, client, db_engine, seed_admin, mandate, monkeypatch):
        mandate()
        monkeypatch.setattr(auto_debit, "update_subscription_quantity", lambda *a: (False, DOMESTIC_CARD_ERROR))
        _admin(client, seed_admin)
        resp = client.post("/api/auto_debit/update_quantity", json={})
        assert resp.status_code == 409 and resp.get_json()["needs_reauth"] is True
        assert _row(db_engine)[3] is True

    def test_upi_mandate_recognised_whatever_razorpay_says(self, client, db_engine, seed_admin, mandate, monkeypatch):
        """An authorized subscription that hasn't charged yet is refused
        with other wording; how it's paid decides."""
        sub = mandate()
        monkeypatch.setattr(auto_debit, "update_subscription_quantity",
                            lambda *a: (False, "Razorpay request failed: The requested operation is not allowed"))
        looked_up = []
        monkeypatch.setattr(auto_debit, "fetch_subscription",
                            lambda sid: looked_up.append(sid) or ({"payment_method": "upi"}, None))
        _admin(client, seed_admin)
        resp = client.post("/api/auto_debit/update_quantity", json={})
        assert resp.status_code == 409 and resp.get_json()["needs_reauth"] is True
        assert looked_up == [sub] and _row(db_engine)[3] is True

    def test_other_failures_stay_a_retry(self, client, db_engine, seed_admin, mandate, monkeypatch):
        mandate()
        monkeypatch.setattr(auto_debit, "update_subscription_quantity",
                            lambda *a: (False, "Razorpay request failed: another subscription operation is in progress"))
        monkeypatch.setattr(auto_debit, "fetch_subscription", lambda sid: ({"payment_method": "card"}, None))
        _admin(client, seed_admin)
        resp = client.post("/api/auto_debit/update_quantity", json={})
        body = resp.get_json()
        assert resp.status_code == 502 and "needs_reauth" not in body
        assert "(another subscription operation is in progress)" in body["msg"]   # Razorpay's reason shown
        assert _row(db_engine)[3] is False


class TestSetUpAgain:
    def test_demo_replacement_takes_over_only_once_confirmed(self, client, db_engine, seed_admin, mandate):
        old = mandate(subscription_id="demo_sub_" + secrets.token_hex(4))
        _admin(client, seed_admin)
        resp = client.post("/api/auto_debit/enroll", json={"replace": True})
        body = resp.get_json()
        assert resp.status_code == 200 and body["replace"] is True
        status, current, _, _, pending, pending_qty, _, _ = _row(db_engine)
        assert status == "active" and current == old       # still billing on the old one
        assert pending == body["subscription_id"] and pending_qty == get_tenant_employee_count(TENANT_SCHEMA)

        resp = client.post("/api/auto_debit/confirm", json={"razorpay_subscription_id": pending,
                                                            "razorpay_payment_id": "demo_payment"})
        assert resp.get_json() == {"ok": True, "replaced": True}
        status, current, quantity, fixed, pending, *_ = _row(db_engine)
        assert (status, current, fixed, pending) == ("active", body["subscription_id"], False, None)
        assert quantity == get_tenant_employee_count(TENANT_SCHEMA)

    def test_needs_an_active_auto_debit(self, client, db_engine, seed_admin, mandate):
        _admin(client, seed_admin)
        resp = client.post("/api/auto_debit/enroll", json={"replace": True})
        assert resp.status_code == 400

    def test_plain_enroll_still_refused_while_active(self, client, db_engine, seed_admin, mandate):
        mandate()
        _admin(client, seed_admin)
        assert client.post("/api/auto_debit/enroll", json={}).status_code == 400

    def test_real_replacement_starts_at_the_next_charge_and_cancels_at_cycle_end(
            self, client, db_engine, seed_admin, mandate, monkeypatch):
        old = mandate(customer_id="cust_keep")
        next_charge = int((datetime.datetime.now() + datetime.timedelta(days=12)).timestamp())
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr(auto_debit, "fetch_subscription", lambda sid: ({"charge_at": next_charge}, None))
        monkeypatch.setattr(auto_debit, "_get_or_create_plan_id", lambda: ("plan_x", None))
        created, cancelled = [], []
        monkeypatch.setattr(auto_debit, "create_subscription",
                            lambda plan, cust, qty, **k: created.append((plan, cust, qty, k)) or ("sub_new_1", None))
        monkeypatch.setattr(auto_debit, "razorpay_cancel_subscription",
                            lambda sid, **k: cancelled.append((sid, k)) or (True, None))
        monkeypatch.setattr(auto_debit, "verify_subscription_signature", lambda *a, **k: True)
        _admin(client, seed_admin)

        assert client.post("/api/auto_debit/enroll", json={"replace": True}).status_code == 200
        assert created == [("plan_x", "cust_keep", get_tenant_employee_count(TENANT_SCHEMA) or 1,
                            {"start_at": next_charge})]
        assert cancelled == []                               # nothing cancelled until authorized

        resp = client.post("/api/auto_debit/confirm", json={"razorpay_subscription_id": "sub_new_1",
                                                            "razorpay_payment_id": "pay_1", "razorpay_signature": "s"})
        assert resp.status_code == 200
        assert cancelled == [(old, {"cancel_at_cycle_end": True})]
        assert _row(db_engine)[1] == "sub_new_1"


class TestDailyJob:
    def test_admins_emailed_once_per_headcount(self, db_engine, mandate, monkeypatch):
        mandate()
        monkeypatch.setattr(auto_debit, "razorpay_configured", lambda: True)
        monkeypatch.setattr(auto_debit, "update_subscription_quantity", lambda *a: (False, UPI_ERROR))
        sent = []
        import utils.billing_notices as notices
        monkeypatch.setattr(notices, "_send_to_admins", lambda db, subject, body, heading=None: sent.append(subject))
        auto_debit.sync_and_bill_auto_debit()
        auto_debit.sync_and_bill_auto_debit()
        assert sent == ["Action needed: set up Auto-Debit again for your new headcount"]
        row = _row(db_engine)
        assert row[3] is True and row[7] == get_tenant_employee_count(TENANT_SCHEMA)
