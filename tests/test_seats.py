# -*- coding: utf-8 -*-
"""Seats blueprint tests -- GET /settings/seats and /api/seats/create_order
+ /api/seats/verify_payment (blueprints/seats.py). Lets an existing
tenant's admin buy additional employee seats, raising
company_settings.paid_employee_slots.

Like tests/test_billing.py, Razorpay isn't configured here, so the demo
branch of utils.razorpay_utils.create_id_or_demo()/verify_or_demo() is what
normally runs; a couple of tests monkeypatch
utils.razorpay_utils.razorpay_configured (the internal check those helpers
make) plus blueprints.seats.verify_payment_signature (the name seats.py
itself calls) to exercise the real signature-check branch instead.

company_settings is a SINGLE shared row (id=1) for the whole test suite
(there's exactly one tenant schema, "att_test", used everywhere) --
paid_employee_slots defaults to NULL (unlimited). The `seat_cap` fixture
below sets it for the duration of one test and always restores NULL
afterward, invalidating utils.helpers.py's 60s get_company_settings()
cache on both sides so no other test in a full-suite run ever observes a
stale cap.

Run with:
    python -m pytest tests/test_seats.py -v
"""
import os
import secrets
import threading
import pytest

from extensions import app as flask_app
from utils.plan_limits import calculate_price, format_price_inr
from utils.helpers import get_company_settings, invalidate_settings_cache

pytestmark = pytest.mark.usefixtures("razorpay_demo_mode")


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


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
    cur.close()
    invalidate_settings_cache()


def _cleanup_orders(db_engine, *order_ids):
    cur = db_engine.cursor()
    for order_id in order_ids:
        cur.execute("DELETE FROM att_master.seat_topup_orders WHERE razorpay_order_id=%s", (order_id,))
    cur.close()


class TestSeatsPage:
    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.get("/settings/seats", follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_admin_view_redirects_into_unified_settings_billing_tab(self, client, seed_admin):
        # Retired as a standalone page -- now redirects into Settings >
        # Finances > Billing (templates/settings.html's #ss-billing) so old
        # links/bookmarks (and the mobile bridge-login target) still land
        # somewhere useful, same pattern as GET /email_config's retirement.
        _admin_session(client, seed_admin["username"])
        resp = client.get("/settings/seats", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/settings" in resp.headers["Location"]
        assert "tab=billing" in resp.headers["Location"]

        followed = client.get("/settings/seats", follow_redirects=True)
        assert followed.status_code == 200
        assert b"Seats" in followed.data


class TestCreateOrder:
    def test_unauthenticated_rejected(self, client):
        resp = client.post("/api/seats/create_order", json={"additional_seats": 5})
        assert resp.status_code == 401

    def test_unlimited_plan_rejects_purchase(self, client, seed_admin, seat_cap):
        seat_cap(None)  # explicit, though this is also the default
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/seats/create_order", json={"additional_seats": 5})
        assert resp.status_code == 400
        assert "unlimited" in resp.get_json()["msg"].lower()

    def test_zero_seats_rejected(self, client, seed_admin, seat_cap):
        seat_cap(50)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/seats/create_order", json={"additional_seats": 0})
        assert resp.status_code == 400

    def test_negative_seats_rejected(self, client, seed_admin, seat_cap):
        seat_cap(50)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/seats/create_order", json={"additional_seats": -3})
        assert resp.status_code == 400

    def test_non_numeric_seats_rejected(self, client, seed_admin, seat_cap):
        seat_cap(50)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/seats/create_order", json={"additional_seats": "a lot"})
        assert resp.status_code == 400

    def test_too_many_seats_rejected(self, client, seed_admin, seat_cap):
        seat_cap(50)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/seats/create_order", json={"additional_seats": 10001})
        assert resp.status_code == 400
        assert "too many" in resp.get_json()["msg"].lower()

    def test_happy_path_stages_demo_order(self, client, db_engine, seed_admin, seat_cap):
        seat_cap(20)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/seats/create_order", json={"additional_seats": 5})
        order_id = None
        try:
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["order_id"].startswith("demo_seat_order_")
            assert data["demo"] is True
            assert data["additional_seats"] == 5
            assert data["amount_paise"] == calculate_price(5)
            assert data["amount_display"] == format_price_inr(calculate_price(5))
            order_id = data["order_id"]

            cur = db_engine.cursor()
            cur.execute(
                "SELECT tenant_schema, seats_purchased, amount_paise, status, requested_by "
                "FROM att_master.seat_topup_orders WHERE razorpay_order_id=%s",
                (order_id,),
            )
            row = cur.fetchone()
            cur.close()
            assert row == (os.environ["DB_NAME"], 5, calculate_price(5), "created", seed_admin["username"])
        finally:
            if order_id:
                _cleanup_orders(db_engine, order_id)


class TestVerifyPayment:
    def test_unauthenticated_rejected(self, client):
        resp = client.post("/api/seats/verify_payment", json={"razorpay_order_id": "x"})
        assert resp.status_code == 401

    def test_unknown_order_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/api/seats/verify_payment", json={
            "razorpay_order_id": "demo_seat_order_" + secrets.token_hex(8),
            "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
        })
        assert resp.status_code == 404

    def test_order_from_another_tenant_not_redeemable(self, client, db_engine, seed_admin):
        # tenant_schema scoping: an order staged under a different tenant
        # schema must never be redeemable against this tenant's cap, even
        # with a real order id and a signature that would otherwise verify.
        order_id = "demo_seat_order_" + secrets.token_hex(6)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.seat_topup_orders (tenant_schema, company_name, razorpay_order_id, "
            "seats_purchased, amount_paise, status) VALUES ('some_other_tenant_schema', 'Other Co', %s, 3, %s, 'created')",
            (order_id, calculate_price(3)),
        )
        cur.close()
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.post("/api/seats/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_x", "razorpay_signature": "sig_x",
            })
            assert resp.status_code == 404
        finally:
            _cleanup_orders(db_engine, order_id)

    def test_real_mode_invalid_signature_rejected(self, client, db_engine, seed_admin, monkeypatch):
        monkeypatch.setattr("utils.razorpay_utils.razorpay_configured", lambda: True)
        monkeypatch.setattr("blueprints.seats.verify_payment_signature", lambda *a, **k: False)

        order_id = "order_real_" + secrets.token_hex(6)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.seat_topup_orders (tenant_schema, company_name, razorpay_order_id, "
            "seats_purchased, amount_paise, status) VALUES (%s, 'Test Co', %s, 4, %s, 'created')",
            (os.environ["DB_NAME"], order_id, calculate_price(4)),
        )
        cur.close()
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.post("/api/seats/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_bad", "razorpay_signature": "bad-sig",
            })
            assert resp.status_code == 400

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.seat_topup_orders WHERE razorpay_order_id=%s", (order_id,))
            assert cur.fetchone()[0] == "created"
            cur.close()
        finally:
            _cleanup_orders(db_engine, order_id)

    def test_happy_path_credits_seats_and_is_idempotent(self, client, db_engine, seed_admin, seat_cap):
        seat_cap(10)
        _admin_session(client, seed_admin["username"])

        resp = client.post("/api/seats/create_order", json={"additional_seats": 6})
        assert resp.status_code == 200
        order_id = resp.get_json()["order_id"]

        try:
            resp = client.post("/api/seats/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "sig_ok",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["seats_purchased"] == 6
            assert data["paid_employee_slots"] == 16  # 10 + 6

            co = get_company_settings()
            assert co["paid_employee_slots"] == 16

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.seat_topup_orders WHERE razorpay_order_id=%s", (order_id,))
            assert cur.fetchone()[0] == "paid"
            cur.close()

            # Idempotent replay -- must not credit seats a second time.
            resp2 = client.post("/api/seats/verify_payment", json={
                "razorpay_order_id": order_id, "razorpay_payment_id": "pay_ok", "razorpay_signature": "sig_ok",
            })
            assert resp2.status_code == 200
            data2 = resp2.get_json()
            assert data2["ok"] is True
            assert data2["already_paid"] is True
            assert data2["paid_employee_slots"] == 16

            co = get_company_settings()
            assert co["paid_employee_slots"] == 16
        finally:
            _cleanup_orders(db_engine, order_id)


class TestSeatCapConcurrency:
    """utils/helpers.py's add_employee_seat_cap_check() used to be a plain
    check-then-insert with no locking: two concurrent employee-creation
    requests racing for the last available seat could both read
    COUNT(*) < cap before either had inserted, and both succeed --
    exceeding the cap. It's now lock-protected (a row lock on the single
    company_settings row, held from an authoritative recheck through the
    employee INSERT, in one transaction) -- this fires genuinely
    concurrent signups at a tenant with exactly one seat free and asserts
    only one of them wins, regardless of how the two threads interleave."""

    def test_concurrent_signups_at_cap_only_one_succeeds(self, db_engine, seat_cap):
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM employees")
        baseline = cur.fetchone()[0]
        seat_cap(baseline + 1)  # exactly one more seat available than employees that exist today

        suffix = secrets.token_hex(3).upper()
        emp_ids = [f"RACE{suffix}A", f"RACE{suffix}B"]
        results = {}

        def _signup(emp_id):
            with flask_app.test_client() as c:
                resp = c.post("/api/employee/signup", json={
                    "employee_id": emp_id, "name": "Race Test",
                    "password": "RacePass@1",
                })
                results[emp_id] = (resp.status_code, resp.get_json())

        threads = [threading.Thread(target=_signup, args=(eid,)) for eid in emp_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(results) == 2, f"a signup thread didn't finish: {results}"
        successes = [eid for eid, (_status, body) in results.items() if body and body.get("ok")]
        assert len(successes) == 1, (
            f"expected exactly one of the two concurrent signups to succeed against a "
            f"single free seat, got: {results}"
        )
        failure_eid = [eid for eid in emp_ids if eid not in successes][0]
        fail_status, fail_body = results[failure_eid]
        assert fail_status == 403
        assert "limit" in (fail_body.get("msg") or "").lower()

        cur.execute("SELECT COUNT(*) FROM employees")
        assert cur.fetchone()[0] == baseline + 1

        cur.execute("DELETE FROM employees WHERE employee_id = ANY(%s)", (emp_ids,))
        cur.execute("DELETE FROM api_tokens WHERE identity = ANY(%s)", (emp_ids,))
        cur.close()
