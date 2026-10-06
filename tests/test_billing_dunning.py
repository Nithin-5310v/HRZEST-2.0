# -*- coding: utf-8 -*-
"""Billing-dunning blueprint tests -- blueprints/billing_dunning.py.

Covers the 3 HTTP routes (/pay_overdue_bill, /api/billing/overdue/create_order,
/api/billing/overdue/verify), the daily grace/lock state machine
(check_tenant_billing() / _run_billing_check() / _check_one_tenant()), the
shared idempotent _mark_invoice_paid_and_unlock() helper, and the
payment.captured webhook handler. This blueprint had zero test coverage
before this file.

Same demo/live boundary as tests/test_billing.py and tests/test_auto_debit.py:
Razorpay isn't configured in this environment, so create_id_or_demo()/
verify_or_demo() take the demo branch by default; a few tests monkeypatch
utils.razorpay_utils.razorpay_configured to True to exercise the real
signature-verification path, mocking only the Razorpay boundary, never the
database -- no real charges anywhere in this file.

There is exactly one tenant schema in this environment ("att_test", g.tenant_db's
default single-tenant fallback), so att_master.tenants' single row for it is
shared state across the whole suite -- every test here snapshots and restores
billing_state/grace_period_ends_at/locked_at/payment_option/created_at, the
same way tests/test_auto_debit.py's clean_mandate fixture restores
auto_debit_mandates.

Run with:
    python -m pytest tests/test_billing_dunning.py -v
"""
import datetime
import secrets
import pytest

from extensions import app as flask_app
from utils.plan_limits import calculate_price, get_tenant_employee_count

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = "att_test"


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


def _get_tenant_row(db_engine, schema=TENANT_SCHEMA):
    cur = db_engine.cursor()
    cur.execute(
        "SELECT id, billing_state, grace_period_ends_at, locked_at, payment_option, created_at "
        "FROM att_master.tenants WHERE db_name=%s",
        (schema,),
    )
    row = cur.fetchone()
    cur.close()
    return row


@pytest.fixture
def restore_tenant_billing_state(db_engine):
    """att_master.tenants has exactly one row in this test environment,
    shared across the whole suite -- capture its billing-relevant columns
    and put them back afterward, same reasoning as test_auto_debit.py's
    clean_mandate fixture."""
    before = _get_tenant_row(db_engine)
    yield
    cur = db_engine.cursor()
    cur.execute(
        "UPDATE att_master.tenants SET billing_state=%s, grace_period_ends_at=%s, locked_at=%s, "
        "payment_option=%s, created_at=%s WHERE db_name=%s",
        (before[1], before[2], before[3], before[4], before[5], TENANT_SCHEMA),
    )
    cur.close()


@pytest.fixture
def clean_invoices(db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()
    yield
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()


def _set_billing_state(db_engine, billing_state="current", grace_period_ends_at=None, locked_at=None,
                       payment_option="online", created_at=None, schema=TENANT_SCHEMA):
    cur = db_engine.cursor()
    cur.execute(
        "UPDATE att_master.tenants SET billing_state=%s, grace_period_ends_at=%s, locked_at=%s, "
        "payment_option=%s, created_at=COALESCE(%s, created_at) WHERE db_name=%s",
        (billing_state, grace_period_ends_at, locked_at, payment_option, created_at, schema),
    )
    cur.close()


def _insert_invoice(db_engine, status="pending", billing_period=None, order_id=None, payment_id=None,
                    employee_count=5, schema=TENANT_SCHEMA):
    billing_period = billing_period or datetime.date.today().replace(day=1)
    amount_paise = calculate_price(employee_count)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
        "razorpay_order_id, razorpay_payment_id, status, billing_period) "
        "VALUES (%s, 'Test Co', %s, %s, %s, %s, %s, %s) RETURNING id",
        (schema, employee_count, amount_paise, order_id, payment_id, status, billing_period),
    )
    invoice_id = cur.fetchone()[0]
    cur.close()
    return invoice_id


class TestPayOverdueBillPage:
    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.get("/pay_overdue_bill", follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_renders_current_billing_state_and_amount(self, client, seed_admin, restore_tenant_billing_state):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/pay_overdue_bill")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", "ignore")
        # Amount shown must reflect the tenant's CURRENT employee count, not
        # any value cached from whenever the bill was originally generated.
        current_count = get_tenant_employee_count(TENANT_SCHEMA)
        expected_amount = calculate_price(current_count)
        assert str(expected_amount // 100) in body or f"{expected_amount // 100:,}" in body


class TestEnforceBillingLockSessionBranch:
    """app.py's _enforce_billing_lock() -- confirms an employee session
    blocked by a locked account gets a real explanation instead of
    bouncing to the admin login it can never complete (regression test for
    the fix; /pay_overdue_bill is @admin_required, so an employee hitting
    it just lands back at the admin login with nothing useful shown)."""

    def _make_billing_locked(self, client):
        """app.py's _resolve_tenant() only respects session["_billing_locked"]
        via its branch-1 cached-session path (last checked within
        _TENANT_STATUS_RECHECK_SEC) -- without tenant_db/_tenant_status_checked_at
        also set, it falls through to branch 3 (the default single-tenant
        fallback this test environment normally uses), which hardcodes
        billing_locked=False unconditionally and would silently make this
        whole test a false positive."""
        import time as _time
        with client.session_transaction() as sess:
            sess["tenant_db"] = TENANT_SCHEMA
            sess["_tenant_status_checked_at"] = _time.time()
            sess["_billing_locked"] = True

    def test_locked_admin_session_redirects_to_pay_overdue_bill(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        self._make_billing_locked(client)
        resp = client.post("/toggle_feature", json={"feature": "qr_enabled", "value": True}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/pay_overdue_bill" in resp.headers["Location"]

    def test_locked_employee_session_redirects_to_employee_portal_with_message(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        self._make_billing_locked(client)
        resp = client.post("/update_my_profile", data={}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/employee_portal" in resp.headers["Location"]
        assert "/pay_overdue_bill" not in resp.headers["Location"]

        followed = client.get(resp.headers["Location"], follow_redirects=True)
        body = followed.data.decode("utf-8", "ignore")
        assert "contact your admin" in body.lower()


class TestCreateOverdueOrder:
    def test_unauthenticated_rejected(self, client):
        # admin_required (utils/auth.py) only returns a JSON 401 for
        # AJAX-shaped requests (X-Requested-With / Accept / Content-Type:
        # application/json) -- a bare POST with no such header is treated as
        # plain browser navigation and gets a 302 to the login page instead.
        # json={} makes this request AJAX-shaped so the API-appropriate
        # 401 path is what's actually exercised here.
        resp = client.post("/api/billing/overdue/create_order", json={})
        assert resp.status_code == 401

    def test_happy_path_stages_demo_order_with_current_employee_count(
        self, client, seed_admin, clean_invoices, restore_tenant_billing_state
    ):
        _admin_session(client, seed_admin["username"])
        current_count = get_tenant_employee_count(TENANT_SCHEMA)
        resp = client.post("/api/billing/overdue/create_order")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        # Razorpay isn't configured in this environment -- create_id_or_demo()
        # must take the demo branch, never a real Razorpay call.
        assert data["order_id"].startswith("demo_dunning_")
        assert data["demo"] is True
        assert data["amount_paise"] == calculate_price(current_count)
        assert data["amount_display"]
        assert "key_id" in data

    def test_amount_reflects_headcount_change_since_last_bill(
        self, client, db_engine, seed_admin, clean_invoices, restore_tenant_billing_state
    ):
        """The overdue amount is always computed fresh from the CURRENT
        headcount at order-creation time -- never reused from whatever
        employee_count an earlier (unpaid) monthly_invoices row recorded.
        Deliberately inserts the extra employee directly (not via the
        seed_employee fixture) since ALL fixtures a test requests are set up
        before the test body runs -- injecting via a fixture would make
        get_tenant_employee_count() already reflect the new hire on its
        very first call, leaving nothing to diff against."""
        baseline_count = get_tenant_employee_count(TENANT_SCHEMA)
        # A stale/unpaid invoice from "last month" recorded the OLD count --
        # must not be what create_order() uses.
        _insert_invoice(db_engine, status="pending", employee_count=baseline_count,
                        billing_period=datetime.date.today().replace(day=1) - datetime.timedelta(days=32))

        from utils.auth import generate_password_hash
        new_emp_id = "DUN" + secrets.token_hex(4).upper()
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
            "VALUES (%s,%s,%s,%s,0)",
            (new_emp_id, "Dunning Test Hire", f"{new_emp_id.lower()}@test.local", generate_password_hash("Test@1234")),
        )
        cur.close()

        try:
            _admin_session(client, seed_admin["username"])
            resp = client.post("/api/billing/overdue/create_order")
            assert resp.status_code == 200
            data = resp.get_json()
            new_count = get_tenant_employee_count(TENANT_SCHEMA)
            assert new_count == baseline_count + 1
            assert data["amount_paise"] == calculate_price(new_count)
            assert data["amount_paise"] != calculate_price(baseline_count)
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM employees WHERE employee_id=%s", (new_emp_id,))
            cur.close()

    def test_creates_pending_invoice_row_for_current_billing_period(
        self, client, db_engine, seed_admin, clean_invoices, restore_tenant_billing_state
    ):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/billing/overdue/create_order")
        data = resp.get_json()

        cur = db_engine.cursor()
        cur.execute(
            "SELECT employee_count, amount_paise, status, billing_period "
            "FROM att_master.monthly_invoices WHERE razorpay_order_id=%s",
            (data["order_id"],),
        )
        row = cur.fetchone()
        cur.close()
        assert row is not None
        current_count = get_tenant_employee_count(TENANT_SCHEMA)
        assert row[0] == current_count
        assert row[1] == calculate_price(current_count)
        assert row[2] == "pending"
        assert row[3] == datetime.date.today().replace(day=1)

    def test_repeated_calls_stage_independent_orders(
        self, client, db_engine, seed_admin, clean_invoices, restore_tenant_billing_state
    ):
        """create_order() has no idempotency guard of its own (unlike
        verify/webhook processing below) -- each call mints a fresh Razorpay
        order, matching blueprints/billing.py's create_order() posture. Two
        calls must not collide or silently reuse the first order id."""
        _admin_session(client, seed_admin["username"])
        resp1 = client.post("/api/billing/overdue/create_order")
        resp2 = client.post("/api/billing/overdue/create_order")
        order1 = resp1.get_json()["order_id"]
        order2 = resp2.get_json()["order_id"]
        assert order1 != order2

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_order_id IN (%s, %s)",
            (order1, order2),
        )
        assert cur.fetchone()[0] == 2
        cur.close()


class TestVerifyOverduePayment:
    def test_unauthenticated_rejected(self, client):
        resp = client.post("/api/billing/overdue/verify", json={})
        assert resp.status_code == 401

    def test_unknown_order_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/billing/overdue/verify", json={
            "razorpay_order_id": "demo_dunning_" + secrets.token_hex(8),
            "razorpay_payment_id": "pay_x", "razorpay_signature": "",
        })
        assert resp.status_code == 404

    def test_real_mode_invalid_signature_rejected(self, client, db_engine, seed_admin, clean_invoices,
                                                  restore_tenant_billing_state, monkeypatch):
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.billing_dunning.verify_payment_signature", lambda *a, **k: False)

        _set_billing_state(db_engine, billing_state="grace",
                           grace_period_ends_at=datetime.datetime.now() + datetime.timedelta(days=1))
        order_id = "order_real_" + secrets.token_hex(6)
        _insert_invoice(db_engine, status="pending", order_id=order_id)

        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/billing/overdue/verify", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": "pay_bad", "razorpay_signature": "bad-sig",
        })
        assert resp.status_code == 400

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.monthly_invoices WHERE razorpay_order_id=%s", (order_id,))
        assert cur.fetchone()[0] == "pending"  # untouched by a failed verification
        cur.execute("SELECT billing_state FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == "grace"  # not unlocked
        cur.close()

    def test_demo_order_rejected_once_real_keys_configured(self, client, db_engine, seed_admin, clean_invoices,
                                                           restore_tenant_billing_state, monkeypatch):
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        order_id = "demo_dunning_" + secrets.token_hex(6)
        _insert_invoice(db_engine, status="pending", order_id=order_id)

        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/billing/overdue/verify", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
        })
        assert resp.status_code == 400
        assert "demo" in resp.get_json()["msg"].lower()

    def test_valid_payment_unlocks_and_is_idempotent(self, client, db_engine, seed_admin, clean_invoices,
                                                     restore_tenant_billing_state):
        _set_billing_state(db_engine, billing_state="locked", locked_at=datetime.datetime.now())
        order_id = "demo_dunning_" + secrets.token_hex(6)
        _insert_invoice(db_engine, status="pending", order_id=order_id)

        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/billing/overdue/verify", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "",
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("SELECT status, razorpay_payment_id FROM att_master.monthly_invoices WHERE razorpay_order_id=%s",
                    (order_id,))
        assert cur.fetchone() == ("paid", "pay_ok")
        cur.execute("SELECT billing_state, grace_period_ends_at, locked_at FROM att_master.tenants WHERE db_name=%s",
                    (TENANT_SCHEMA,))
        row = cur.fetchone()
        cur.close()
        assert row == ("current", None, None)

        # Idempotent replay -- a retried client POST for the already-paid
        # order must not error or double-process.
        resp2 = client.post("/api/billing/overdue/verify", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "",
        })
        assert resp2.status_code == 200
        assert resp2.get_json()["ok"] is True

    def test_verify_unlocks_current_session_immediately(self, client, db_engine, seed_admin, clean_invoices,
                                                        restore_tenant_billing_state):
        """The redirect-verify path additionally clears the SESSION's own
        cached billing_locked flag so the paying admin isn't still blocked
        by _enforce_billing_lock() until the next periodic recheck (app.py's
        _TENANT_STATUS_RECHECK_SEC) -- the webhook is the durable unlock,
        this is the snappy-UX one."""
        _set_billing_state(db_engine, billing_state="locked", locked_at=datetime.datetime.now())
        order_id = "demo_dunning_" + secrets.token_hex(6)
        _insert_invoice(db_engine, status="pending", order_id=order_id)

        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["_billing_locked"] = True
        client.post("/api/billing/overdue/verify", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "",
        })
        with client.session_transaction() as sess:
            assert sess.get("_billing_locked") is False


class TestMarkInvoicePaidAndUnlockCrossTenantGuard:
    def test_order_belonging_to_different_tenant_not_redeemable(self, db_engine, clean_invoices,
                                                                restore_tenant_billing_state):
        from blueprints.billing_dunning import _mark_invoice_paid_and_unlock

        order_id = "demo_dunning_" + secrets.token_hex(6)
        _insert_invoice(db_engine, status="pending", order_id=order_id, schema="att_some_other_tenant")

        with flask_app.app_context():
            ok = _mark_invoice_paid_and_unlock(order_id, "pay_x", expected_tenant_db=TENANT_SCHEMA)
        assert ok is False

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.monthly_invoices WHERE razorpay_order_id=%s", (order_id,))
        assert cur.fetchone()[0] == "pending"  # never marked paid via the mismatched tenant
        cur.execute("DELETE FROM att_master.monthly_invoices WHERE razorpay_order_id=%s", (order_id,))
        cur.close()


class TestOverduePaymentCapturedWebhook:
    def test_matching_order_marks_paid_and_unlocks(self, db_engine, clean_invoices, restore_tenant_billing_state):
        from blueprints.billing_dunning import _handle_overdue_payment_captured

        _set_billing_state(db_engine, billing_state="grace",
                           grace_period_ends_at=datetime.datetime.now() + datetime.timedelta(days=1))
        order_id = "demo_dunning_" + secrets.token_hex(6)
        _insert_invoice(db_engine, status="pending", order_id=order_id)

        payload = {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "pay_webhook_1", "order_id": order_id}}},
        }
        with flask_app.app_context():
            _handle_overdue_payment_captured(payload)

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.monthly_invoices WHERE razorpay_order_id=%s", (order_id,))
        assert cur.fetchone()[0] == "paid"
        cur.execute("SELECT billing_state FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == "current"
        cur.close()

    def test_unrelated_order_id_is_a_harmless_noop(self, db_engine, clean_invoices, restore_tenant_billing_state):
        """payment.captured also fires for signup-checkout orders
        (blueprints/billing.py's payment_orders) and seat top-ups
        (blueprints/seats.py's seat_topup_orders) -- separate order-id
        spaces that simply won't match any monthly_invoices row and must
        no-op harmlessly here, never raise."""
        from blueprints.billing_dunning import _handle_overdue_payment_captured

        payload = {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "pay_orphan", "order_id": "order_never_registered"}}},
        }
        with flask_app.app_context():
            _handle_overdue_payment_captured(payload)  # must not raise

        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_payment_id='pay_orphan'")
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_redelivered_webhook_is_idempotent(self, db_engine, clean_invoices, restore_tenant_billing_state):
        from blueprints.billing_dunning import _handle_overdue_payment_captured

        _set_billing_state(db_engine, billing_state="locked", locked_at=datetime.datetime.now())
        order_id = "demo_dunning_" + secrets.token_hex(6)
        _insert_invoice(db_engine, status="pending", order_id=order_id)
        payload = {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "pay_redelivered", "order_id": order_id}}},
        }
        with flask_app.app_context():
            _handle_overdue_payment_captured(payload)
            _handle_overdue_payment_captured(payload)  # Razorpay redelivers on timeout/non-2xx

        cur = db_engine.cursor()
        cur.execute("SELECT status, razorpay_payment_id FROM att_master.monthly_invoices WHERE razorpay_order_id=%s",
                    (order_id,))
        assert cur.fetchone() == ("paid", "pay_redelivered")
        cur.close()


class TestGraceAndLockStateMachine:
    """_run_billing_check() / _check_one_tenant() -- the daily cron's core
    decision logic. Called directly (not via the scheduler) inside an
    app_context, same pattern as tests/test_auto_debit.py's webhook-handler
    tests."""

    def test_unpaid_bill_starts_grace_period(self, db_engine, clean_invoices, restore_tenant_billing_state):
        from blueprints.billing_dunning import _run_billing_check
        # created_at well before this month so the "signup month is
        # already covered by checkout" skip doesn't apply.
        _set_billing_state(db_engine, billing_state="current", payment_option="online",
                           created_at=datetime.datetime.now() - datetime.timedelta(days=90))
        # No paid invoice for the current billing_period at all.

        with flask_app.app_context():
            _run_billing_check()

        cur = db_engine.cursor()
        cur.execute("SELECT billing_state, grace_period_ends_at FROM att_master.tenants WHERE db_name=%s",
                    (TENANT_SCHEMA,))
        state, grace_ends = cur.fetchone()
        cur.close()
        assert state == "grace"
        assert grace_ends is not None
        period_start = datetime.date.today().replace(day=1)
        expected_deadline = datetime.datetime.combine(period_start, datetime.time.min) + datetime.timedelta(days=5)
        assert grace_ends == expected_deadline

    def test_grace_period_expires_into_locked(self, db_engine, clean_invoices, restore_tenant_billing_state):
        from blueprints.billing_dunning import _run_billing_check
        # grace_period_ends_at computed from the DB's own NOW(), not Python's
        # local-tz datetime.now() -- this column is compared against NOW()
        # in the DB session's timezone, and a Python-side "now() - 1h" in a
        # local timezone ahead of it (e.g. IST, UTC+5:30) can still land in
        # the DB's future, making an intentionally-passed deadline look not
        # yet due.
        cur = db_engine.cursor()
        cur.execute("SELECT NOW() - INTERVAL '1 hour'")
        deadline_already_passed = cur.fetchone()[0]
        cur.close()
        _set_billing_state(
            db_engine, billing_state="grace",
            grace_period_ends_at=deadline_already_passed,
            payment_option="online",
            created_at=datetime.datetime.now() - datetime.timedelta(days=90),
        )

        with flask_app.app_context():
            _run_billing_check()

        cur = db_engine.cursor()
        cur.execute("SELECT billing_state, locked_at FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
        state, locked_at = cur.fetchone()
        cur.close()
        assert state == "locked"
        assert locked_at is not None

    def test_grace_period_not_yet_expired_stays_in_grace(self, db_engine, clean_invoices, restore_tenant_billing_state):
        from blueprints.billing_dunning import _run_billing_check
        _set_billing_state(
            db_engine, billing_state="grace",
            grace_period_ends_at=datetime.datetime.now() + datetime.timedelta(days=2),  # not due yet
            payment_option="online",
            created_at=datetime.datetime.now() - datetime.timedelta(days=90),
        )

        with flask_app.app_context():
            _run_billing_check()

        cur = db_engine.cursor()
        cur.execute("SELECT billing_state, locked_at FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
        state, locked_at = cur.fetchone()
        cur.close()
        assert state == "grace"
        assert locked_at is None

    def test_paid_invoice_reactivates_a_locked_tenant(self, db_engine, clean_invoices, restore_tenant_billing_state):
        from blueprints.billing_dunning import _run_billing_check
        _set_billing_state(db_engine, billing_state="locked", locked_at=datetime.datetime.now(),
                           payment_option="online", created_at=datetime.datetime.now() - datetime.timedelta(days=90))
        _insert_invoice(db_engine, status="paid", billing_period=datetime.date.today().replace(day=1))

        with flask_app.app_context():
            _run_billing_check()

        cur = db_engine.cursor()
        cur.execute("SELECT billing_state, grace_period_ends_at, locked_at FROM att_master.tenants WHERE db_name=%s",
                    (TENANT_SCHEMA,))
        row = cur.fetchone()
        cur.close()
        assert row == ("current", None, None)

    def test_already_current_and_paid_tenant_is_untouched(self, db_engine, clean_invoices, restore_tenant_billing_state):
        from blueprints.billing_dunning import _run_billing_check
        _set_billing_state(db_engine, billing_state="current", payment_option="online",
                           created_at=datetime.datetime.now() - datetime.timedelta(days=90))
        _insert_invoice(db_engine, status="paid", billing_period=datetime.date.today().replace(day=1))

        with flask_app.app_context():
            _run_billing_check()

        cur = db_engine.cursor()
        cur.execute("SELECT billing_state FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == "current"
        cur.close()

    def test_trial_tenant_never_dunned(self, db_engine, clean_invoices, restore_tenant_billing_state):
        """'trial' tenants have no bill to miss (blueprints/billing_dunning.py's
        own module docstring) -- _run_billing_check()'s query filters
        payment_option IN ('online', 'manual') explicitly."""
        from blueprints.billing_dunning import _run_billing_check
        _set_billing_state(db_engine, billing_state="current", payment_option="trial",
                           created_at=datetime.datetime.now() - datetime.timedelta(days=90))
        # No paid invoice at all -- would normally trigger grace for an
        # online/manual tenant.

        with flask_app.app_context():
            _run_billing_check()

        cur = db_engine.cursor()
        cur.execute("SELECT billing_state FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == "current"  # untouched -- never entered dunning at all
        cur.close()

    def test_signup_month_is_never_dunned(self, db_engine, clean_invoices, restore_tenant_billing_state):
        """A tenant is skipped for its own signup calendar month -- online
        signups already paid for that month at checkout (blueprints/billing.py),
        and nothing here should immediately dun a brand-new company."""
        from blueprints.billing_dunning import _run_billing_check
        _set_billing_state(db_engine, billing_state="current", payment_option="online",
                           created_at=datetime.datetime.now())  # signed up this month
        # No paid invoice -- would normally trigger grace, except for the
        # signup-month exemption.

        with flask_app.app_context():
            _run_billing_check()

        cur = db_engine.cursor()
        cur.execute("SELECT billing_state FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == "current"
        cur.close()

    def test_one_tenants_failure_does_not_sink_others(self, db_engine, clean_invoices, restore_tenant_billing_state, monkeypatch):
        """Mirrors test_auto_debit.py's equivalent isolation test -- one
        tenant's blown-up lookup must not stop the loop from reaching the
        next tenant."""
        from blueprints.billing_dunning import _run_billing_check, _check_one_tenant

        _set_billing_state(db_engine, billing_state="current", payment_option="online",
                           created_at=datetime.datetime.now() - datetime.timedelta(days=90))

        calls = []
        real_check = _check_one_tenant

        def flaky_check(tenant_id, company_name, db_name, *a, **k):
            calls.append(db_name)
            if db_name != TENANT_SCHEMA:
                raise RuntimeError("simulated transient DB error")
            return real_check(tenant_id, company_name, db_name, *a, **k)

        monkeypatch.setattr("blueprints.billing_dunning._check_one_tenant", flaky_check)

        # Insert a second, broken tenant row ahead of att_test alphabetically
        # isn't guaranteed by id order, but any exception inside the loop
        # for ANY row must not prevent att_test's own row from being
        # processed -- assert both were attempted and att_test still
        # transitioned correctly despite whichever row raised.
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, plan, status, payment_option, "
            "billing_state, created_at) VALUES ('Broken Co', %s, %s, 'per_employee', 'active', 'online', "
            "'current', NOW() - INTERVAL '90 days') RETURNING id",
            ("broken-dunning-" + secrets.token_hex(4), "att_broken_dunning_" + secrets.token_hex(4)),
        )
        broken_tenant_id = cur.fetchone()[0]
        cur.close()

        try:
            with flask_app.app_context():
                _run_billing_check()  # must not raise despite the broken tenant
            assert TENANT_SCHEMA in calls

            cur = db_engine.cursor()
            cur.execute("SELECT billing_state FROM att_master.tenants WHERE db_name=%s", (TENANT_SCHEMA,))
            assert cur.fetchone()[0] == "grace"  # still correctly processed
            cur.close()
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (broken_tenant_id,))
            cur.close()
