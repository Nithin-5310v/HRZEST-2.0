# -*- coding: utf-8 -*-
"""PAYMENTS_ENABLED=false (utils/razorpay_utils.payments_enabled()): online
payments switched off until live Razorpay keys are in place. No checkout or
auto-debit set-up starts, the billing screens say payments aren't available
instead of offering a button that fails, and the production key checks
don't stop the app from starting. The dunning and registration-checklist
sides are in tests/test_billing_dunning.py and tests/test_org.py."""
import importlib.util
import os

import pytest

import utils.razorpay_utils as rz

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")

TENANT_SCHEMA = os.environ["DB_NAME"]


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


@pytest.fixture
def payments_off(monkeypatch):
    monkeypatch.setenv("PAYMENTS_ENABLED", "false")


@pytest.fixture
def no_mandate(db_engine):
    def clear():
        cur = db_engine.cursor()
        cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
        cur.close()
    clear()
    yield
    clear()


@pytest.mark.parametrize("value, enabled", [
    (None, True), ("", True), ("true", True), ("1", True), ("yes", True),
    ("false", False), ("FALSE", False), (" off ", False), ("0", False), ("no", False),
])
def test_payments_enabled_reads_the_switch(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("PAYMENTS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PAYMENTS_ENABLED", value)
    assert rz.payments_enabled() is enabled


def test_nothing_is_created_while_off(payments_off, monkeypatch):
    monkeypatch.setattr(rz, "_KEY_ID", "rzp_test_x")
    monkeypatch.setattr(rz, "_KEY_SECRET", "secret")
    called = []
    assert rz.create_id_or_demo("demo_", lambda: called.append(1) or ("order_1", None)) == (
        None, False, rz.PAYMENTS_OFF_MSG)
    assert called == []
    assert rz.accepting_online_payments() is False
    # Verifying a payment already made still works with the keys.
    assert rz.razorpay_configured() is True


def _load_fresh_copy():
    """Runs utils/razorpay_utils.py's import-time checks again, under
    whatever environment the test set."""
    spec = importlib.util.spec_from_file_location("_razorpay_utils_copy", rz.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("key_id", ["rzp_test_abc", ""])
def test_production_starts_with_payments_off(monkeypatch, key_id):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RAZORPAY_KEY_ID", key_id)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret" if key_id else "")
    monkeypatch.setenv("PAYMENTS_ENABLED", "true")
    with pytest.raises(RuntimeError):
        _load_fresh_copy()
    monkeypatch.setenv("PAYMENTS_ENABLED", "false")
    assert _load_fresh_copy().payments_enabled() is False


def test_auto_debit_enroll_says_payments_are_off(client, db_engine, seed_admin, no_mandate, payments_off):
    _admin_session(client, seed_admin["username"])
    resp = client.post("/api/auto_debit/enroll", json={})
    assert resp.get_json() == {"ok": False, "msg": rz.PAYMENTS_OFF_MSG}
    cur = db_engine.cursor()
    cur.execute("SELECT 1 FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (TENANT_SCHEMA,))
    assert cur.fetchone() is None
    cur.close()


@pytest.fixture
def seat_cap(db_engine):
    from utils.helpers import invalidate_settings_cache

    def set_cap(cap):
        cur = db_engine.cursor()
        cur.execute("UPDATE company_settings SET paid_employee_slots=%s WHERE id=1", (cap,))
        cur.close()
        invalidate_settings_cache()
    set_cap(10)
    yield
    set_cap(None)


def test_seat_checkout_says_payments_are_off(client, seed_admin, seat_cap, payments_off):
    _admin_session(client, seed_admin["username"])
    resp = client.post("/api/seats/create_order", json={"additional_seats": 2})
    assert resp.get_json()["msg"] == rz.PAYMENTS_OFF_MSG


class TestBillingSettingsPage:
    def _page(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/settings?tab=billing")
        assert resp.status_code == 200
        return resp.get_data(as_text=True)

    def test_notice_instead_of_buttons_while_off(self, client, seed_admin, seat_cap, no_mandate, payments_off):
        page = self._page(client, seed_admin)
        assert 'id="paymentsOffNotice"' in page and "Online payments aren't available yet" in page
        assert 'id="enableAutoDebitBtn"' not in page and 'id="buySeatsForm"' not in page

    def test_buttons_while_on(self, client, seed_admin, seat_cap, no_mandate):
        page = self._page(client, seed_admin)
        assert 'id="paymentsOffNotice"' not in page
        assert 'id="enableAutoDebitBtn"' in page and 'id="buySeatsForm"' in page

    def test_an_active_mandate_can_still_be_cancelled_while_off(self, client, db_engine, seed_admin,
                                                                no_mandate, payments_off):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, status) "
                    "VALUES (%s, 'Test Co', 'active')", (TENANT_SCHEMA,))
        cur.close()
        page = self._page(client, seed_admin)
        assert 'id="cancelAtPeriodEndBtn"' in page and 'id="disableAutoDebitBtn"' in page


def test_overdue_bill_page_has_no_pay_button_while_off(client, seed_admin, payments_off):
    _admin_session(client, seed_admin["username"])
    page = client.get("/pay_overdue_bill").get_data(as_text=True)
    assert 'id="payBtn"' not in page and "contact support to settle this bill" in page
