# -*- coding: utf-8 -*-
"""Trial-billing tests -- blueprints/trial_billing.py's check_trial_expirations()
(trial-to-paid conversion) and utils/plan_limits.py's calculate_price() (the
new base-fee/minimum-floor pricing knobs).

Same demo/live boundary and shared-single-tenant-schema caveats as
tests/test_auto_debit.py: att_master.tenants has exactly one row
(db_name='att_test') that the whole suite shares, so every test here
restores its trial_start_date/trial_end_date/subscription_status/
payment_option back to what they were before, the same way test_auto_debit.py
restores auto_debit_mandates.

Run with:
    python -m pytest tests/test_trial_billing.py -v
"""
import os
import io
import time
import datetime
import secrets
import pytest

from extensions import app as flask_app
from utils.plan_limits import (
    calculate_price, get_tenant_employee_count,
    invalidate_rate_paise_cache, invalidate_extra_cost_cache,
)


TENANT_SCHEMA = os.environ["DB_NAME"]  # the suite's default tenant schema (see conftest)


def _drop_schema(db_engine, schema_name):
    cur = db_engine.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (schema_name,))
    cur.close()


def _random_gst():
    """A syntactically valid, but not otherwise meaningful, 15-character
    GSTIN -- matches blueprints/org.py's _GST_RE, now required by
    POST /create_org (see tests/test_org.py's identical helper)."""
    letters = ''.join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(5))
    return (
        f"{secrets.randbelow(100):02d}{letters}{secrets.randbelow(10000):04d}"
        f"{secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{secrets.choice('123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
        f"Z{secrets.choice('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
    )


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _get_tenant_row(db_engine, schema=TENANT_SCHEMA):
    cur = db_engine.cursor()
    cur.execute(
        "SELECT id, subscription_status, payment_option, trial_end_date FROM att_master.tenants WHERE db_name=%s",
        (schema,),
    )
    row = cur.fetchone()
    cur.close()
    return row


@pytest.fixture
def restore_tenant_state(db_engine):
    """att_master.tenants has exactly one row in this test environment,
    shared across the whole suite -- capture it and put it back afterward,
    same reasoning as test_auto_debit.py's clean_mandate fixture."""
    before = _get_tenant_row(db_engine)
    yield
    cur = db_engine.cursor()
    cur.execute(
        "UPDATE att_master.tenants SET subscription_status=%s, payment_option=%s, trial_end_date=%s, "
        "trial_start_date=NULL WHERE db_name=%s",
        (before[1], before[2], before[3], TENANT_SCHEMA),
    )
    cur.close()


@pytest.fixture
def clean_mandate(db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()
    yield
    cur = db_engine.cursor()
    cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.execute("DELETE FROM att_master.monthly_invoices WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    cur.close()


def _insert_mandate(db_engine, subscription_id, status="active", quantity_synced=0):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
        "razorpay_subscription_id, quantity_synced, status, created_at, activated_at) "
        "VALUES (%s, 'Test Co', 'cust_x', %s, %s, %s, NOW(), NOW())",
        (TENANT_SCHEMA, subscription_id, quantity_synced, status),
    )
    cur.close()


@pytest.fixture(autouse=True)
def _reset_rate_caches():
    """utils/plan_limits.py's rate/base-fee/minimum caches are module-level
    and process-wide (30s TTL) -- without invalidating them, a test that
    just wrote a new platform_costs value could read a stale cached one."""
    invalidate_rate_paise_cache()
    invalidate_extra_cost_cache()
    yield
    invalidate_rate_paise_cache()
    invalidate_extra_cost_cache()


@pytest.fixture
def restore_platform_costs(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT per_employee_paise, base_fee_paise, minimum_monthly_paise FROM att_master.platform_costs WHERE id=1")
    before = cur.fetchone()
    cur.close()
    yield
    cur = db_engine.cursor()
    cur.execute(
        "UPDATE att_master.platform_costs SET per_employee_paise=%s, base_fee_paise=%s, minimum_monthly_paise=%s WHERE id=1",
        before,
    )
    cur.close()
    invalidate_rate_paise_cache()
    invalidate_extra_cost_cache()


class TestCalculatePrice:
    def test_default_config_matches_old_flat_rate_behavior(self, db_engine, restore_platform_costs):
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.platform_costs SET per_employee_paise=9900, base_fee_paise=0, minimum_monthly_paise=0 WHERE id=1")
        cur.close()
        invalidate_rate_paise_cache()
        invalidate_extra_cost_cache()
        assert calculate_price(0) == 0
        assert calculate_price(10) == 99000
        assert calculate_price(1) == 9900

    def test_base_fee_added_on_top_of_per_employee_rate(self, db_engine, restore_platform_costs):
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.platform_costs SET per_employee_paise=9900, base_fee_paise=50000, minimum_monthly_paise=0 WHERE id=1")
        cur.close()
        invalidate_rate_paise_cache()
        invalidate_extra_cost_cache()
        assert calculate_price(10) == 50000 + 99000
        assert calculate_price(0) == 50000  # base fee still applies with zero employees

    def test_minimum_floor_applied_for_small_headcount(self, db_engine, restore_platform_costs):
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.platform_costs SET per_employee_paise=9900, base_fee_paise=0, minimum_monthly_paise=500000 WHERE id=1")
        cur.close()
        invalidate_rate_paise_cache()
        invalidate_extra_cost_cache()
        assert calculate_price(1) == 500000  # 9900 < floor -> floor wins
        assert calculate_price(2) == 500000  # 19800 < floor -> floor wins

    def test_minimum_floor_does_not_reduce_a_larger_bill(self, db_engine, restore_platform_costs):
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.platform_costs SET per_employee_paise=9900, base_fee_paise=0, minimum_monthly_paise=500000 WHERE id=1")
        cur.close()
        invalidate_rate_paise_cache()
        invalidate_extra_cost_cache()
        assert calculate_price(1000) == 1000 * 9900  # well above the floor -> floor irrelevant

    def test_negative_employee_count_clamped_to_zero(self, db_engine, restore_platform_costs):
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.platform_costs SET per_employee_paise=9900, base_fee_paise=0, minimum_monthly_paise=0 WHERE id=1")
        cur.close()
        invalidate_rate_paise_cache()
        invalidate_extra_cost_cache()
        assert calculate_price(-5) == 0


class TestCheckTrialExpirations:
    def test_due_trial_with_active_mandate_converts_to_active(self, db_engine, clean_mandate, restore_tenant_state):
        from blueprints.trial_billing import check_trial_expirations
        sub_id = "demo_sub_" + secrets.token_hex(4)
        _insert_mandate(db_engine, sub_id, status="active", quantity_synced=0)
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE att_master.tenants SET subscription_status='trialing', payment_option='trial', "
            "trial_start_date=NOW() - INTERVAL '14 days', trial_end_date=NOW() - INTERVAL '1 hour' "
            "WHERE db_name=%s",
            (TENANT_SCHEMA,),
        )
        cur.close()

        with flask_app.app_context():
            check_trial_expirations()

        row = _get_tenant_row(db_engine)
        assert row[1] == "active"       # subscription_status
        assert row[2] == "online"       # payment_option flipped -- now covered by billing_dunning.py's cron

        cur = db_engine.cursor()
        cur.execute("SELECT quantity_synced FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
        quantity_synced = cur.fetchone()[0]
        cur.close()
        assert quantity_synced == get_tenant_employee_count(TENANT_SCHEMA)

    def test_not_yet_due_trial_is_untouched(self, db_engine, clean_mandate, restore_tenant_state):
        from blueprints.trial_billing import check_trial_expirations
        sub_id = "demo_sub_" + secrets.token_hex(4)
        _insert_mandate(db_engine, sub_id, status="active")
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE att_master.tenants SET subscription_status='trialing', payment_option='trial', "
            "trial_start_date=NOW(), trial_end_date=NOW() + INTERVAL '13 days' WHERE db_name=%s",
            (TENANT_SCHEMA,),
        )
        cur.close()

        with flask_app.app_context():
            check_trial_expirations()

        row = _get_tenant_row(db_engine)
        assert row[1] == "trialing"  # untouched -- not due yet
        assert row[2] == "trial"

    def test_due_trial_with_no_mandate_degrades_to_past_due_without_crashing(self, db_engine, clean_mandate, restore_tenant_state):
        from blueprints.trial_billing import check_trial_expirations
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE att_master.tenants SET subscription_status='trialing', payment_option='trial', "
            "trial_start_date=NOW() - INTERVAL '14 days', trial_end_date=NOW() - INTERVAL '1 hour' "
            "WHERE db_name=%s",
            (TENANT_SCHEMA,),
        )
        cur.close()

        with flask_app.app_context():
            check_trial_expirations()  # must not raise despite no auto_debit_mandates row

        row = _get_tenant_row(db_engine)
        assert row[1] == "past_due"
        assert row[2] == "trial"  # payment_option only flips on a successful conversion

    def test_non_trialing_tenant_is_never_touched(self, db_engine, clean_mandate, restore_tenant_state):
        from blueprints.trial_billing import check_trial_expirations
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE att_master.tenants SET subscription_status='active', payment_option='online', "
            "trial_start_date=NULL, trial_end_date=NULL WHERE db_name=%s",
            (TENANT_SCHEMA,),
        )
        cur.close()

        with flask_app.app_context():
            check_trial_expirations()  # query only matches subscription_status='trialing'

        row = _get_tenant_row(db_engine)
        assert row[1] == "active"

    def test_one_tenants_failure_does_not_sink_others(self, db_engine, clean_mandate, restore_tenant_state, monkeypatch):
        """Mirrors test_auto_debit.py's equivalent isolation test -- one
        tenant's blown-up get_tenant_employee_count() must not stop the
        loop from reaching the next due trial."""
        from blueprints.trial_billing import check_trial_expirations, _convert_one_trial

        cur = db_engine.cursor()
        cur.execute(
            "UPDATE att_master.tenants SET subscription_status='trialing', payment_option='trial', "
            "trial_start_date=NOW() - INTERVAL '14 days', trial_end_date=NOW() - INTERVAL '1 hour' "
            "WHERE db_name=%s",
            (TENANT_SCHEMA,),
        )
        cur.close()
        _insert_mandate(db_engine, "demo_sub_" + secrets.token_hex(4), status="active")

        calls = []
        real_convert = _convert_one_trial

        def flaky_convert(tenant_id, company_name, db_name):
            calls.append(db_name)
            if db_name == TENANT_SCHEMA:
                raise RuntimeError("simulated transient DB error")
            return real_convert(tenant_id, company_name, db_name)

        monkeypatch.setattr("blueprints.trial_billing._convert_one_trial", flaky_convert)

        with flask_app.app_context():
            check_trial_expirations()  # must not raise

        assert TENANT_SCHEMA in calls


class TestCancelAtPeriodEnd:
    def test_cancel_at_period_end_keeps_mandate_billing_until_cycle_end(self, client, db_engine, seed_admin, clean_mandate, monkeypatch):
        called = []
        monkeypatch.setattr("blueprints.auto_debit.razorpay_cancel_subscription",
                            lambda sub_id, **k: called.append((sub_id, k)) or (True, None))
        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, sub_id, status="active")
        _admin_session(client, seed_admin["username"])

        resp = client.post("/api/auto_debit/cancel", json={"at_period_end": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["at_period_end"] is True
        assert called == [(sub_id, {"cancel_at_cycle_end": True})]

        cur = db_engine.cursor()
        cur.execute("SELECT status, cancelled_at FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
        row = cur.fetchone()
        cur.close()
        assert row[0] == "pending_cancellation"
        assert row[1] is None  # not cancelled yet -- subscription.cancelled webhook does that at cycle end

    def test_cancel_at_period_end_on_demo_mandate_cancels_immediately(self, client, db_engine, seed_admin, clean_mandate):
        sub_id = "demo_sub_" + secrets.token_hex(4)
        _insert_mandate(db_engine, sub_id, status="active")
        _admin_session(client, seed_admin["username"])

        resp = client.post("/api/auto_debit/cancel", json={"at_period_end": True})
        assert resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
        assert cur.fetchone()[0] == "cancelled"  # no real Razorpay engine to fire a cycle-end webhook for a demo mandate
        cur.close()

    def test_immediate_cancel_without_flag_still_cancels_now(self, client, db_engine, seed_admin, clean_mandate, monkeypatch):
        monkeypatch.setattr("blueprints.auto_debit.razorpay_cancel_subscription", lambda sub_id, **k: (True, None))
        sub_id = "sub_real_" + secrets.token_hex(4)
        _insert_mandate(db_engine, sub_id, status="active")
        _admin_session(client, seed_admin["username"])

        resp = client.post("/api/auto_debit/cancel", json={})
        assert resp.status_code == 200
        assert resp.get_json()["at_period_end"] is False

        cur = db_engine.cursor()
        cur.execute("SELECT status, cancelled_at FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
        row = cur.fetchone()
        cur.close()
        assert row[0] == "cancelled"
        assert row[1] is not None


class TestWebhookIdempotencyHardening:
    def test_duplicate_payment_id_insert_is_rejected_at_db_level(self, db_engine, clean_mandate):
        """idx_monthly_invoices_payment_id (app.py) must make a second
        insert with the same razorpay_payment_id a no-op at the database
        level, independent of any application-level check-then-write --
        this is what _record_charge()'s ON CONFLICT DO NOTHING relies on."""
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
            "razorpay_payment_id, status, billing_period) VALUES (%s, 'Test Co', 1, 9900, 'pay_dup_test', 'paid', CURRENT_DATE)",
            (TENANT_SCHEMA,),
        )
        cur.execute(
            "INSERT INTO att_master.monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
            "razorpay_payment_id, status, billing_period) VALUES (%s, 'Test Co', 1, 9900, 'pay_dup_test', 'paid', CURRENT_DATE) "
            "ON CONFLICT (razorpay_payment_id) WHERE razorpay_payment_id IS NOT NULL DO NOTHING",
            (TENANT_SCHEMA,),
        )
        cur.execute("SELECT COUNT(*) FROM att_master.monthly_invoices WHERE razorpay_payment_id='pay_dup_test'")
        count = cur.fetchone()[0]
        cur.execute("DELETE FROM att_master.monthly_invoices WHERE razorpay_payment_id='pay_dup_test'")
        cur.close()
        assert count == 1


class TestTrialSignupIntegration:
    """End-to-end: /create_org (signup_plan=trial) -> OTP -> KYC docs ->
    platform-admin approval -> /create_org/setup_trial/<id> mandate setup
    (demo mode) -> a real, provisioned tenant with the 14-day trial clock
    started. Same "heavy but worth one real test" posture as
    tests/test_org.py's test_full_flow_provisions_real_tenant_schema."""

    _FAKE_PDF = b"%PDF-1.4\n" + b"x" * 20
    _FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20

    def _login_platform_admin(self, client):
        with client.session_transaction() as sess:
            sess["platform_admin_logged_in"] = True
            sess["platform_admin_username"] = "test_platform_admin"
            sess["platform_admin_last_activity"] = time.time()

    def _run_pipeline_to_approval(self, client, monkeypatch, subdomain, company_name, admin_username, admin_email):
        import blueprints.org as org_module
        captured = {}
        monkeypatch.setattr(
            org_module, "send_org_signup_otp_email",
            lambda email, company, otp: captured.setdefault("otp", otp) or True
        )
        resp = client.post("/create_org", data={
            "company_name": company_name, "subdomain": subdomain,
            "admin_username": admin_username, "admin_password": "password123",
            "admin_email": admin_email, "email_domain": "test.local",
            "signup_plan": "trial", "gst_number": _random_gst(),
        }, follow_redirects=False)
        assert resp.status_code in (301, 302), resp.data
        application_id = int(resp.headers["Location"].rsplit("=", 1)[-1])
        otp = captured.get("otp")
        assert otp is not None

        resp = client.post("/create_org/verify_otp", data={"application_id": application_id, "otp_code": otp})
        assert resp.status_code in (301, 302)

        resp = client.post("/create_org/upload_documents", data={
            "application_id": str(application_id),
            "registration_cert": (io.BytesIO(self._FAKE_PDF), "cert.pdf"),
            "address_proof": (io.BytesIO(self._FAKE_PDF), "address.pdf"),
            "visiting_card": (io.BytesIO(self._FAKE_PNG), "card.png"),
            "name_board_photo": (io.BytesIO(self._FAKE_PNG), "board.png"),
        }, content_type="multipart/form-data", follow_redirects=False)
        assert resp.status_code in (301, 302)

        self._login_platform_admin(client)
        resp = client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)
        assert resp.status_code in (301, 302)
        return application_id

    def test_trial_signup_provisions_instantly_with_no_upfront_mandate_and_first_login_clock(self, client, db_engine, monkeypatch):
        """Every self-serve signup lane (including 'trial') now provisions
        the tenant instantly right after OTP verification -- see
        blueprints/org.py's _provision_application_instantly(). The old
        mandate-first flow this test used to exercise
        (/api/create_org/setup_trial/<id>/create_subscription -> /confirm)
        never gets a chance to run anymore: by the time
        _run_pipeline_to_approval() finishes (OTP -> docs -> platform-admin
        review), the application is ALREADY 'provisioned'. Those two old
        routes are left in place (harmless dead code for this lane) rather
        than removed -- the second half of this test confirms they fail
        closed (400, not a silent double-provision) against an
        already-provisioned application, instead of just asserting they're
        merely unreached."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-trial-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = self._run_pipeline_to_approval(
                client, monkeypatch, subdomain, "E2E Trial Org", "e2e_trial_admin", "e2e-trial@test.local",
            )

            cur = db_engine.cursor()
            cur.execute(
                "SELECT status, payment_option, doc_review_status FROM att_master.tenant_applications WHERE id=%s",
                (application_id,)
            )
            app_status, app_payment_option, doc_review_status = cur.fetchone()
            assert app_payment_option == "trial"
            assert app_status == "provisioned"
            assert doc_review_status == "approved"  # the platform-admin approve step in _run_pipeline_to_approval
            cur.close()

            cur = db_engine.cursor()
            cur.execute(
                "SELECT trial_start_date, trial_end_date, subscription_status, payment_option "
                "FROM att_master.tenants WHERE db_name=%s",
                (schema_name,),
            )
            row = cur.fetchone()
            assert row is not None, "trial tenant was not provisioned"
            trial_start, trial_end, subscription_status, payment_option = row
            assert subscription_status == "trialing"
            assert payment_option == "trial"
            # Deliberately NULL until first login (app.py's
            # inject_billing_lock_status()) -- not stamped at provisioning.
            assert trial_start is None and trial_end is None

            # No auto_debit_mandates row exists yet -- nothing is collected
            # upfront under this flow anymore. A mandate only shows up
            # later if/when the admin voluntarily enrolls via
            # blueprints/auto_debit.py's enroll() (Settings -> Billing).
            cur.execute("SELECT 1 FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (schema_name,))
            assert cur.fetchone() is None

            # The old mandate-first setup routes are still reachable but
            # correctly refuse to do anything for an already-provisioned
            # application, rather than silently double-provisioning.
            resp = client.post(f"/api/create_org/setup_trial/{application_id}/create_subscription")
            assert resp.status_code == 400
            assert resp.get_json()["ok"] is False

            # Tenant-prefixed path -- a bare /login resolves to this test
            # environment's default single-tenant fallback ("att_test"),
            # not the newly provisioned "att_e2e_trial_org" schema, and the
            # login would fail (wrong schema's admin_users table).
            login_resp = client.post(f"/{subdomain}/login", data={
                "identifier": "e2e_trial_admin", "password": "password123",
            }, follow_redirects=True)
            assert login_resp.status_code == 200

            cur.execute(
                "SELECT trial_start_date, trial_end_date FROM att_master.tenants WHERE db_name=%s",
                (schema_name,),
            )
            trial_start, trial_end = cur.fetchone()
            assert trial_start is not None and trial_end is not None, "trial clock did not start at first login"
            from blueprints.trial_billing import TRIAL_DURATION_DAYS
            delta = trial_end - trial_start
            lower = datetime.timedelta(days=TRIAL_DURATION_DAYS) - datetime.timedelta(minutes=1)
            upper = datetime.timedelta(days=TRIAL_DURATION_DAYS) + datetime.timedelta(minutes=1)
            assert lower < delta < upper
            cur.close()
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (schema_name,))
            cur.close()
            _drop_schema(db_engine, schema_name)


class TestNullPaymentIdRows:
    def test_null_payment_id_rows_are_never_deduplicated(self, db_engine, clean_mandate):
        """Failed charges with no payment_id yet must still each get their
        own row -- the partial index only applies when razorpay_payment_id
        IS NOT NULL."""
        cur = db_engine.cursor()
        for _ in range(2):
            cur.execute(
                "INSERT INTO att_master.monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
                "status, billing_period) VALUES (%s, 'Test Co', 1, 9900, 'failed', CURRENT_DATE)",
                (TENANT_SCHEMA,),
            )
        cur.execute(
            "SELECT COUNT(*) FROM att_master.monthly_invoices WHERE tenant_schema=%s AND razorpay_payment_id IS NULL AND status='failed'",
            (TENANT_SCHEMA,),
        )
        count = cur.fetchone()[0]
        cur.close()
        assert count == 2


class TestTrialEmployeeCap:
    """utils/helpers.py's add_employee_seat_cap_check() -> _get_trial_employee_cap_error()
    -- the hard, server-side 2-employee cap for subscription_status='trialing'
    tenants, completely independent of company_settings.paid_employee_slots
    (which is always NULL for a trial tenant -- see provision_tenant()).
    Exercised through the real POST /add_employee_page route, not just the
    helper function directly, so this also proves no partial employee
    record is ever created on a blocked attempt."""

    @pytest.fixture
    def as_trialing(self, db_engine, restore_tenant_state):
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE att_master.tenants SET subscription_status='trialing' WHERE db_name=%s",
            (TENANT_SCHEMA,),
        )
        cur.close()

    @pytest.fixture
    def clean_cap_test_employees(self, db_engine):
        """The shared att_test.employees table is NOT reliably empty
        between test files (confirmed: other suites leave rows behind,
        e.g. 'ADDPAGE003'/'ADDPAGE004' from an unrelated add-employee test)
        -- TRIAL_EMPLOYEE_CAP is a hardcoded absolute (2), not relative to
        whatever this tenant already has, so this class needs a genuinely
        known-empty table to test the boundary at all. Snapshots every
        existing row (whatever it is, from whichever other suite), deletes
        them for the duration of this class's tests, and restores the
        exact same rows afterward -- this class never needs to know or
        care what those rows actually contain."""
        cur = db_engine.cursor()
        cur.execute("SELECT * FROM employees")
        cols = [d[0] for d in cur.description]
        existing_rows = cur.fetchall()
        cur.execute("DELETE FROM employees")
        cur.close()
        db_engine.commit()

        ids = ("CAPT01", "CAPT02", "CAPT03")
        yield ids

        cur = db_engine.cursor()
        cur.execute("DELETE FROM employees WHERE employee_id = ANY(%s)", (list(ids),))
        if existing_rows:
            placeholders = ",".join(["%s"] * len(cols))
            col_list = ",".join(cols)
            cur.executemany(
                f"INSERT INTO employees ({col_list}) VALUES ({placeholders})",
                existing_rows,
            )
        cur.close()
        db_engine.commit()

    def _fill_to(self, db_engine, ids, target_count, baseline_count):
        """Inserts however many of `ids` are needed to bring the tenant's
        live employee count from `baseline_count` up to exactly
        `target_count`."""
        needed = target_count - baseline_count
        assert 0 <= needed <= len(ids), f"test setup can't reach {target_count} from baseline {baseline_count}"
        cur = db_engine.cursor()
        for i in range(needed):
            cur.execute(
                "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
                "VALUES (%s,%s,%s,'x',0)",
                (ids[i], f"Cap Test {i}", f"{ids[i].lower()}@test.local"),
            )
        cur.close()
        db_engine.commit()

    # Explicitly targets this shared test environment's actual seeded
    # tenant slug (tests/conftest.py's _init_test_db, subdomain=
    # "att-test-suite") rather than relying on the single-tenant fallback.
    # g.tenant_db itself was never actually the problem -- when this class's
    # tests failed as part of the full file, it was utils/helpers.py's
    # get_company_settings()/get_auth_config()/get_companies_list()/
    # get_pending_counts() caches, which used to be bare process-wide
    # singletons with no tenant key at all: TestTrialSignupIntegration's own
    # real login to its freshly provisioned "e2e-trial-org" tenant populated
    # those caches with THAT tenant's data, and this class's requests --
    # correctly resolved to att_test the whole time -- served the stale,
    # wrong-tenant cached company_settings/paid_employee_slots for up to
    # their TTL window. Fixed at the source (helpers.py now keys every one
    # of those caches by tenant_db), so this prefix is just belt-and-braces
    # explicitness now, not a workaround.
    _TENANT_PREFIX = "/att-test-suite"

    def _admin_session_for_att_test(self, client, username):
        _admin_session(client, username)

    def test_third_employee_blocked_during_trial_with_upgrade_message(
        self, client, db_engine, seed_admin, as_trialing, clean_cap_test_employees
    ):
        from utils.plan_limits import get_tenant_employee_count
        baseline = get_tenant_employee_count(TENANT_SCHEMA)
        self._fill_to(db_engine, clean_cap_test_employees, 2, baseline)

        self._admin_session_for_att_test(client, seed_admin["username"])
        resp = client.post(f"{self._TENANT_PREFIX}/add_employee_page", data={
            "name": "Third Trial Hire", "emp_id": "CAPT03",
        }, follow_redirects=True)
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", "ignore")
        assert "trial allows up to 2 employees" in body.lower()

        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM employees WHERE employee_id='CAPT03'")
        assert cur.fetchone() is None, "a partial employee record was created despite the block"
        cur.close()

    _FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32

    def _post_add_employee(self, client, monkeypatch, name, emp_id):
        # add_employee_page() unconditionally requires a face photo (and,
        # if face_recognition is actually installed in this environment, a
        # detectable face in it) before it even reaches the seat-cap
        # INSERT -- unrelated to what this class tests, so bypassed the
        # same way tests/test_attendance_checkin.py already does for the
        # equivalent check elsewhere.
        monkeypatch.setattr("blueprints.employees._face_recognition_available", False)
        return client.post(f"{self._TENANT_PREFIX}/add_employee_page", data={
            "name": name, "emp_id": emp_id,
            "face": (io.BytesIO(self._FAKE_PNG), "face.png"),
        }, content_type="multipart/form-data", follow_redirects=True)

    def test_second_employee_allowed_during_trial(
        self, client, db_engine, seed_admin, as_trialing, clean_cap_test_employees, monkeypatch
    ):
        from utils.plan_limits import get_tenant_employee_count
        baseline = get_tenant_employee_count(TENANT_SCHEMA)
        self._fill_to(db_engine, clean_cap_test_employees, 1, baseline)

        self._admin_session_for_att_test(client, seed_admin["username"])
        resp = self._post_add_employee(client, monkeypatch, "Second Trial Hire", "CAPT02")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", "ignore")
        assert "trial allows up to 2 employees" not in body.lower()

        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM employees WHERE employee_id='CAPT02'")
        assert cur.fetchone() is not None, "employee was not created despite being under the trial cap"
        cur.close()

    def test_trial_cap_does_not_apply_to_non_trial_tenants(
        self, client, db_engine, seed_admin, restore_tenant_state, clean_cap_test_employees, monkeypatch
    ):
        """subscription_status='active' (the default for every tenant that
        isn't currently mid-trial) must never hit the trial-specific cap --
        only the ordinary paid_employee_slots check (unset -> unlimited for
        this tenant) applies."""
        from utils.plan_limits import get_tenant_employee_count
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE att_master.tenants SET subscription_status='active' WHERE db_name=%s",
            (TENANT_SCHEMA,),
        )
        cur.close()
        baseline = get_tenant_employee_count(TENANT_SCHEMA)
        self._fill_to(db_engine, clean_cap_test_employees, 2, baseline)

        self._admin_session_for_att_test(client, seed_admin["username"])
        resp = self._post_add_employee(client, monkeypatch, "Third Non-Trial Hire", "CAPT03")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", "ignore")
        assert "trial allows up to 2 employees" not in body.lower()

        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM employees WHERE employee_id='CAPT03'")
        assert cur.fetchone() is not None, "non-trial tenant was incorrectly capped at 2 employees"
        cur.close()
