# -*- coding: utf-8 -*-
"""Auto-debit blueprint tests -- /api/auto_debit/{enroll,confirm,cancel}
(blueprints/auto_debit.py), plus direct unit tests of the webhook-driven
internal handlers (_handle_subscription_charged, _handle_subscription_cancelled,
_handle_payment_failed_or_pending) and the daily sync_and_bill_auto_debit()
cron function.

Same demo/live boundary as tests/test_billing.py and tests/test_seats.py:
Razorpay isn't configured here, so utils.razorpay_utils.create_id_or_demo()/
verify_or_demo() take the demo branch by default. A few tests simulate a
"real Razorpay configured" environment by monkeypatching BOTH
utils.razorpay_utils.razorpay_configured (the internal check create_id_or_demo/
verify_or_demo make) and blueprints.auto_debit.razorpay_configured (a
SEPARATE bound name auto_debit.py imported for its own direct use in
sync_and_bill_auto_debit() and enroll()) -- these are two different module-
level bindings of the same underlying function, so patching only one
leaves the other call site still reporting "not configured".

There is exactly one tenant schema in this environment ("att_test", g.tenant_db's
default single-tenant fallback -- see app.py's _resolve_tenant()), so
auto_debit_mandates.tenant_schema='att_test' is shared state across the
whole suite. The `clean_mandate` fixture clears any mandate/invoice rows
for it before and after every test in this file.

Run with:
    python -m pytest tests/test_auto_debit.py -v
"""
import datetime
import secrets
import pytest

from extensions import app as flask_app
from utils.plan_limits import calculate_price, get_tenant_employee_count

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = "att_test"


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _clear_mandate(db_engine, tenant_schema=TENANT_SCHEMA):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (tenant_schema,))
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (tenant_schema,))
    cur.close()


@pytest.fixture
def clean_mandate(db_engine):
    _clear_mandate(db_engine)
    yield
    _clear_mandate(db_engine)
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema LIKE 'fake_tenant_%%'")
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema LIKE 'fake_tenant_%%'")
    cur.close()


def _insert_mandate(db_engine, tenant_schema=TENANT_SCHEMA, company_name="Test Co",
                    customer_id=None, subscription_id=None, quantity_synced=0,
                    status="pending", activated_at=None):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
        "razorpay_subscription_id, quantity_synced, status, activated_at, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
        (tenant_schema, company_name, customer_id, subscription_id, quantity_synced, status, activated_at),
    )
    cur.close()


def _get_mandate_row(db_engine, tenant_schema=TENANT_SCHEMA):
    cur = db_engine.cursor()
    cur.execute(
        "SELECT status, razorpay_customer_id, razorpay_subscription_id, quantity_synced, activated_at, cancelled_at "
        "FROM att_master.auto_debit_mandates WHERE tenant_schema=%s",
        (tenant_schema,),
    )
    row = cur.fetchone()
    cur.close()
    return row


class TestEnroll:
    def test_unauthenticated_rejected(self, client):
        resp = client.post("/api/auto_debit/enroll", json={})
        assert resp.status_code == 401

    def test_happy_path_demo_enroll(self, client, seed_admin, clean_mandate):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/enroll", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        # Razorpay isn't configured in this environment -- create_id_or_demo()
        # must take the demo branch, never a real Razorpay call.
        assert data["subscription_id"].startswith("demo_sub_")
        assert data["demo"] is True

    def test_enroll_persists_pending_mandate(self, client, db_engine, seed_admin, clean_mandate):
        _admin_session(client, seed_admin["username"])
        expected_count = get_tenant_employee_count(TENANT_SCHEMA)
        resp = client.post("/api/auto_debit/enroll", json={})
        assert resp.status_code == 200
        data = resp.get_json()

        row = _get_mandate_row(db_engine)
        assert row is not None
        status, customer_id, subscription_id, quantity_synced, activated_at, cancelled_at = row
        assert status == "pending"
        assert subscription_id == data["subscription_id"]
        assert quantity_synced == expected_count

    def test_enroll_when_already_active_rejected(self, client, db_engine, seed_admin, clean_mandate):
        _insert_mandate(db_engine, subscription_id="demo_sub_" + secrets.token_hex(4), status="active")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/enroll", json={})
        assert resp.status_code == 400
        assert "already enabled" in resp.get_json()["msg"].lower()

    def test_real_mode_enroll_creates_subscription_via_mocked_boundary(
        self, client, db_engine, seed_admin, clean_mandate, monkeypatch
    ):
        # Exercises enroll()'s real (non-demo) branch: plan lookup/creation,
        # customer creation, and subscription creation all go through
        # utils/razorpay_utils.py -- mocked here at blueprints.auto_debit's
        # own bound names (never the DB), same boundary as every other
        # "real mode" test in this suite.
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)

        cur = db_engine.cursor()
        cur.execute("SELECT razorpay_plan_id FROM att_master.billing_config WHERE id=1")
        original_plan_id = cur.fetchone()[0]
        cur.execute("UPDATE att_master.billing_config SET razorpay_plan_id=NULL WHERE id=1")
        cur.close()

        plan_calls, customer_calls, subscription_calls = [], [], []
        monkeypatch.setattr("blueprints.auto_debit.create_plan",
                            lambda *a, **k: plan_calls.append(a) or ("plan_mocked_123", None))
        monkeypatch.setattr("blueprints.auto_debit.create_customer",
                            lambda *a, **k: customer_calls.append(a) or ("cust_mocked_123", None))
        monkeypatch.setattr(
            "blueprints.auto_debit.create_subscription",
            lambda plan_id, customer_id, quantity, **k: subscription_calls.append(
                (plan_id, customer_id, quantity)
            ) or ("sub_mocked_123", None),
        )
        try:
            _admin_session(client, seed_admin["username"])
            resp = client.post("/api/auto_debit/enroll", json={})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["subscription_id"] == "sub_mocked_123"
            assert data["demo"] is False

            assert len(plan_calls) == 1
            assert len(customer_calls) == 1
            assert subscription_calls == [("plan_mocked_123", "cust_mocked_123", subscription_calls[0][2])]

            row = _get_mandate_row(db_engine)
            assert row[0] == "pending"
            assert row[1] == "cust_mocked_123"
            assert row[2] == "sub_mocked_123"

            cur = db_engine.cursor()
            cur.execute("SELECT razorpay_plan_id FROM att_master.billing_config WHERE id=1")
            assert cur.fetchone()[0] == "plan_mocked_123"  # cached for future enrollments
            cur.close()
        finally:
            cur = db_engine.cursor()
            cur.execute("UPDATE att_master.billing_config SET razorpay_plan_id=%s WHERE id=1", (original_plan_id,))
            cur.close()


class TestConfirm:
    def test_unauthenticated_rejected(self, client):
        resp = client.post("/api/auto_debit/confirm", json={"razorpay_subscription_id": "demo_sub_x"})
        assert resp.status_code == 401

    def test_no_mandate_rejected(self, client, seed_admin, clean_mandate):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/confirm", json={
            "razorpay_subscription_id": "demo_sub_" + secrets.token_hex(4),
            "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
        })
        assert resp.status_code == 404

    def test_mismatched_subscription_id_rejected(self, client, db_engine, seed_admin, clean_mandate):
        _insert_mandate(db_engine, subscription_id="demo_sub_real_one", status="pending")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/confirm", json={
            "razorpay_subscription_id": "demo_sub_a_different_one",
            "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
        })
        assert resp.status_code == 404

    def test_demo_confirm_activates_mandate(self, client, db_engine, seed_admin, clean_mandate):
        sub_id = "demo_sub_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="pending")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/confirm", json={
            "razorpay_subscription_id": sub_id, "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        row = _get_mandate_row(db_engine)
        assert row[0] == "active"
        assert row[4] is not None  # activated_at

    def test_real_mode_invalid_signature_rejected(self, client, db_engine, seed_admin, clean_mandate, monkeypatch):
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.auto_debit.verify_subscription_signature", lambda *a, **k: False)

        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="pending")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/confirm", json={
            "razorpay_subscription_id": sub_id, "razorpay_payment_id": "pay_bad", "razorpay_signature": "bad-sig",
        })
        assert resp.status_code == 400

        row = _get_mandate_row(db_engine)
        assert row[0] == "pending"  # untouched

    def test_real_mode_valid_signature_activates_mandate(self, client, db_engine, seed_admin, clean_mandate, monkeypatch):
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.auto_debit.verify_subscription_signature", lambda *a, **k: True)

        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="pending")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/confirm", json={
            "razorpay_subscription_id": sub_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "good-sig",
        })
        assert resp.status_code == 200

        row = _get_mandate_row(db_engine)
        assert row[0] == "active"


class TestCancel:
    def test_unauthenticated_rejected(self, client):
        resp = client.post("/api/auto_debit/cancel", json={})
        assert resp.status_code == 401

    def test_not_active_rejected(self, client, seed_admin, clean_mandate):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/cancel", json={})
        assert resp.status_code == 400
        assert "isn't currently enabled" in resp.get_json()["msg"].lower()

    def test_demo_cancel_skips_real_razorpay_call(self, client, db_engine, seed_admin, clean_mandate, monkeypatch):
        called = []
        monkeypatch.setattr("blueprints.auto_debit.razorpay_cancel_subscription",
                            lambda *a, **k: called.append(a) or (True, None))
        _insert_mandate(db_engine, subscription_id="demo_sub_" + secrets.token_hex(4), status="active")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/cancel", json={})
        assert resp.status_code == 200
        assert called == []  # demo subscriptions never touch the real API

        row = _get_mandate_row(db_engine)
        assert row[0] == "cancelled"
        assert row[5] is not None  # cancelled_at

    def test_real_cancel_calls_razorpay_and_updates_mandate(self, client, db_engine, seed_admin, clean_mandate, monkeypatch):
        called = []
        monkeypatch.setattr("blueprints.auto_debit.razorpay_cancel_subscription",
                            lambda sub_id, **k: called.append(sub_id) or (True, None))
        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/cancel", json={})
        assert resp.status_code == 200
        assert called == [sub_id]

        row = _get_mandate_row(db_engine)
        assert row[0] == "cancelled"

    def test_real_cancel_failure_leaves_mandate_active(self, client, db_engine, seed_admin, clean_mandate, monkeypatch):
        monkeypatch.setattr("blueprints.auto_debit.razorpay_cancel_subscription",
                            lambda sub_id, **k: (False, "Razorpay is down"))
        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/auto_debit/cancel", json={})
        assert resp.status_code == 502

        row = _get_mandate_row(db_engine)
        assert row[0] == "active"  # not cancelled on a failed Razorpay call


class TestWebhookHandlers:
    """Direct unit tests of the internal _handle_* functions
    blueprints/webhooks.py's receive_webhook() dispatches to -- these
    receive an already-signature-verified payload and never touch g.tenant_db
    (see blueprints/webhooks.py's docstring), so they run fine outside a
    request context; wrapped in an app context anyway since they call
    log_security_event/app_log."""

    def test_subscription_charged_records_paid_invoice(self, db_engine, clean_mandate):
        from blueprints.auto_debit import _handle_subscription_charged
        sub_id = "sub_webhook_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=3, status="active")
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": {"id": sub_id}},
                "payment": {"entity": {"id": "pay_webhook_1", "amount": 29700}},
            },
        }
        with flask_app.app_context():
            _handle_subscription_charged(payload)

        cur = db_engine.cursor()
        cur.execute(
            "SELECT status, amount_paise, employee_count FROM att_master.monthly_invoices "
            "WHERE razorpay_payment_id='pay_webhook_1'"
        )
        row = cur.fetchone()
        cur.close()
        assert row == ("paid", 29700, get_tenant_employee_count(TENANT_SCHEMA))

    def test_subscription_charged_redelivery_is_deduplicated(self, db_engine, clean_mandate):
        from blueprints.auto_debit import _handle_subscription_charged
        sub_id = "sub_webhook_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=3, status="active")
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": {"id": sub_id}},
                "payment": {"entity": {"id": "pay_webhook_dup", "amount": 9900}},
            },
        }
        with flask_app.app_context():
            _handle_subscription_charged(payload)
            _handle_subscription_charged(payload)  # Razorpay redelivers on timeout/non-2xx

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_payment_id='pay_webhook_dup'"
        )
        assert cur.fetchone()[0] == 1
        cur.close()

    def test_subscription_charged_unknown_subscription_is_a_noop(self, db_engine, clean_mandate):
        from blueprints.auto_debit import _handle_subscription_charged
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": {"id": "sub_never_registered"}},
                "payment": {"entity": {"id": "pay_orphan", "amount": 9900}},
            },
        }
        with flask_app.app_context():
            _handle_subscription_charged(payload)  # must not raise

        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_payment_id='pay_orphan'")
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_subscription_cancelled_updates_mandate(self, db_engine, clean_mandate):
        from blueprints.auto_debit import _handle_subscription_cancelled
        sub_id = "sub_webhook_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active")
        payload = {"event": "subscription.cancelled", "payload": {"subscription": {"entity": {"id": sub_id}}}}
        with flask_app.app_context():
            _handle_subscription_cancelled(payload)

        row = _get_mandate_row(db_engine)
        assert row[0] == "cancelled"
        assert row[5] is not None

    def test_payment_failed_records_failure_reason(self, db_engine, clean_mandate):
        from blueprints.auto_debit import _handle_payment_failed_or_pending
        sub_id = "sub_webhook_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=2, status="active")
        payload = {
            "event": "payment.failed",
            "payload": {
                "subscription": {"entity": {"id": sub_id}},
                "payment": {"entity": {"id": "pay_failed_1", "amount": 19800, "error_description": "Card declined"}},
            },
        }
        with flask_app.app_context():
            _handle_payment_failed_or_pending(payload)

        cur = db_engine.cursor()
        cur.execute(
            "SELECT status, failure_reason FROM att_master.monthly_invoices WHERE razorpay_payment_id='pay_failed_1'"
        )
        row = cur.fetchone()
        cur.close()
        assert row == ("failed", "Card declined")


class TestSyncAndBillAutoDebit:
    def test_demo_charge_simulated_once_due(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import sync_and_bill_auto_debit
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: False)
        sub_id = "demo_sub_" + secrets.token_hex(4)
        old_activation = datetime.datetime.now() - datetime.timedelta(days=45)
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=0, status="active",
                        activated_at=old_activation)

        with flask_app.app_context():
            sync_and_bill_auto_debit()

        cur = db_engine.cursor()
        cur.execute(
            "SELECT status, amount_paise FROM att_master.monthly_invoices WHERE razorpay_subscription_id=%s",
            (sub_id,),
        )
        row = cur.fetchone()
        cur.close()
        assert row is not None
        assert row[0] == "paid"
        assert row[1] == calculate_price(get_tenant_employee_count(TENANT_SCHEMA))

        mrow = _get_mandate_row(db_engine)
        assert mrow[3] == get_tenant_employee_count(TENANT_SCHEMA)  # quantity_synced updated

    def test_demo_charge_not_due_yet(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import sync_and_bill_auto_debit
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: False)
        sub_id = "demo_sub_" + secrets.token_hex(4)
        recent_activation = datetime.datetime.now() - datetime.timedelta(days=2)
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=get_tenant_employee_count(TENANT_SCHEMA),
                        status="active", activated_at=recent_activation)

        with flask_app.app_context():
            sync_and_bill_auto_debit()

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_subscription_id=%s", (sub_id,)
        )
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_stale_demo_subscription_skipped_once_real_keys_configured(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import sync_and_bill_auto_debit
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: True)
        update_calls = []
        monkeypatch.setattr("blueprints.auto_debit.update_subscription_quantity",
                            lambda *a, **k: update_calls.append(a) or (True, None))
        sub_id = "demo_sub_" + secrets.token_hex(4)
        old_activation = datetime.datetime.now() - datetime.timedelta(days=45)
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=0, status="active",
                        activated_at=old_activation)

        with flask_app.app_context():
            sync_and_bill_auto_debit()

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_subscription_id=%s", (sub_id,)
        )
        assert cur.fetchone()[0] == 0  # never fabricate a paid invoice for a subscription that was never real
        cur.close()
        assert update_calls == []  # and never attempt a real API call for it either

    def test_real_subscription_quantity_resynced_when_headcount_changes(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import sync_and_bill_auto_debit
        update_calls = []
        monkeypatch.setattr("blueprints.auto_debit.update_subscription_quantity",
                            lambda sub_id, qty: update_calls.append((sub_id, qty)) or (True, None))
        sub_id = "sub_real_" + secrets.token_hex(4)
        stale_quantity = get_tenant_employee_count(TENANT_SCHEMA) + 99  # force a mismatch
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=stale_quantity, status="active",
                        activated_at=datetime.datetime.now())

        with flask_app.app_context():
            sync_and_bill_auto_debit()

        current_count = get_tenant_employee_count(TENANT_SCHEMA)
        assert update_calls == [(sub_id, current_count or 1)]

        mrow = _get_mandate_row(db_engine)
        assert mrow[3] == current_count  # quantity_synced now matches actual headcount

    def test_quantity_sync_failure_does_not_update_quantity_synced(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import sync_and_bill_auto_debit
        monkeypatch.setattr("blueprints.auto_debit.update_subscription_quantity",
                            lambda *a, **k: (False, "Razorpay unreachable"))
        sub_id = "sub_real_" + secrets.token_hex(4)
        stale_quantity = get_tenant_employee_count(TENANT_SCHEMA) + 99
        _insert_mandate(db_engine, subscription_id=sub_id, quantity_synced=stale_quantity, status="active",
                        activated_at=datetime.datetime.now())

        with flask_app.app_context():
            sync_and_bill_auto_debit()  # must not raise

        mrow = _get_mandate_row(db_engine)
        assert mrow[3] == stale_quantity  # left untouched -- Razorpay was never told the new quantity

    def test_one_tenants_failure_does_not_sink_others(self, db_engine, clean_mandate, monkeypatch):
        """sync_and_bill_auto_debit()'s per-mandate try/except must isolate
        failures -- one tenant's blown-up get_tenant_employee_count() (a
        real transient-DB-error scenario in production) shouldn't stop the
        loop from reaching the next active mandate."""
        from blueprints.auto_debit import sync_and_bill_auto_debit

        broken_schema = "fake_tenant_" + secrets.token_hex(4)
        real_get_count = get_tenant_employee_count

        def flaky_get_count(schema_name):
            if schema_name == broken_schema:
                raise RuntimeError("simulated transient DB error")
            return real_get_count(schema_name)

        monkeypatch.setattr("blueprints.auto_debit.get_tenant_employee_count", flaky_get_count)
        update_calls = []
        monkeypatch.setattr("blueprints.auto_debit.update_subscription_quantity",
                            lambda sub_id, qty: update_calls.append(sub_id) or (True, None))

        broken_sub = "sub_real_broken_" + secrets.token_hex(4)
        healthy_sub = "sub_real_healthy_" + secrets.token_hex(4)
        _insert_mandate(db_engine, tenant_schema=broken_schema, subscription_id=broken_sub,
                        quantity_synced=999, status="active", activated_at=datetime.datetime.now())
        _insert_mandate(db_engine, tenant_schema=TENANT_SCHEMA, subscription_id=healthy_sub,
                        quantity_synced=real_get_count(TENANT_SCHEMA) + 99, status="active",
                        activated_at=datetime.datetime.now())

        with flask_app.app_context():
            sync_and_bill_auto_debit()  # must not raise despite the broken tenant

        assert healthy_sub in update_calls
        assert broken_sub not in update_calls


class TestReconcileBillingRecords:
    """reconcile_billing_records() -- the nightly safety net for a missed
    subscription.charged webhook. Mocks list_subscription_invoices() at
    blueprints.auto_debit's own bound name (same boundary convention as
    every other real-mode test in this file) rather than making a real
    Razorpay call; _record_charge() itself is never mocked, so these tests
    also exercise the exact same insert path the webhook handler uses."""

    def test_noop_when_razorpay_not_configured(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import reconcile_billing_records
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: False)
        calls = []
        monkeypatch.setattr("blueprints.auto_debit.list_subscription_invoices",
                            lambda *a, **k: calls.append(a) or ([], None))
        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active",
                        activated_at=datetime.datetime.now())

        with flask_app.app_context():
            reconcile_billing_records()

        assert calls == []  # never even looks at mandates when Razorpay isn't configured

    def test_demo_subscription_skipped(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import reconcile_billing_records
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: True)
        calls = []
        monkeypatch.setattr("blueprints.auto_debit.list_subscription_invoices",
                            lambda *a, **k: calls.append(a) or ([], None))
        sub_id = "demo_sub_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active",
                        activated_at=datetime.datetime.now())

        with flask_app.app_context():
            reconcile_billing_records()

        assert calls == []  # a subscription that was never real has nothing to reconcile

    def test_fills_in_a_paid_invoice_the_webhook_missed(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import reconcile_billing_records
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: True)
        sub_id = "sub_real_" + secrets.token_hex(4)
        payment_id = "pay_" + secrets.token_hex(6)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active",
                        activated_at=datetime.datetime.now())
        monkeypatch.setattr(
            "blueprints.auto_debit.list_subscription_invoices",
            lambda subscription_id, **k: (
                [{"status": "paid", "payment_id": payment_id, "amount_paid": 49900}], None
            ),
        )

        with flask_app.app_context():
            reconcile_billing_records()

        cur = db_engine.cursor()
        cur.execute(
            "SELECT status, amount_paise, razorpay_payment_id FROM att_master.monthly_invoices "
            "WHERE razorpay_subscription_id=%s", (sub_id,),
        )
        row = cur.fetchone()
        cur.close()
        assert row is not None, "webhook-missed invoice was not backfilled"
        assert row == ("paid", 49900, payment_id)

    def test_replaying_an_already_recorded_invoice_is_a_noop(self, db_engine, clean_mandate, monkeypatch):
        """The whole point of reusing _record_charge()'s own idempotency
        (a real unique constraint on razorpay_payment_id) -- this job never
        needs to first check what's already recorded before replaying."""
        from blueprints.auto_debit import reconcile_billing_records
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: True)
        sub_id = "sub_real_" + secrets.token_hex(4)
        payment_id = "pay_" + secrets.token_hex(6)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active",
                        activated_at=datetime.datetime.now())
        monkeypatch.setattr(
            "blueprints.auto_debit.list_subscription_invoices",
            lambda subscription_id, **k: (
                [{"status": "paid", "payment_id": payment_id, "amount_paid": 49900}], None
            ),
        )

        with flask_app.app_context():
            reconcile_billing_records()
            reconcile_billing_records()  # second run -- must not duplicate

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_payment_id=%s", (payment_id,),
        )
        assert cur.fetchone()[0] == 1
        cur.close()

    def test_unpaid_invoice_is_not_recorded(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import reconcile_billing_records
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: True)
        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active",
                        activated_at=datetime.datetime.now())
        monkeypatch.setattr(
            "blueprints.auto_debit.list_subscription_invoices",
            lambda subscription_id, **k: (
                [{"status": "issued", "payment_id": None, "amount_paid": 0}], None
            ),
        )

        with flask_app.app_context():
            reconcile_billing_records()  # must not raise

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_subscription_id=%s", (sub_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_fetch_error_for_one_tenant_does_not_raise(self, db_engine, clean_mandate, monkeypatch):
        from blueprints.auto_debit import reconcile_billing_records
        monkeypatch.setattr("blueprints.auto_debit.razorpay_configured", lambda: True)
        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, subscription_id=sub_id, status="active",
                        activated_at=datetime.datetime.now())
        monkeypatch.setattr("blueprints.auto_debit.list_subscription_invoices",
                            lambda subscription_id, **k: (None, "network error contacting Razorpay"))

        with flask_app.app_context():
            reconcile_billing_records()  # must not raise

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_subscription_id=%s", (sub_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.close()
