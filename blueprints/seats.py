# -*- coding: utf-8 -*-
"""Seats blueprint -- lets an existing tenant's Admin/HR buy more employee
seats once they hit company_settings.paid_employee_slots (the cap
add_employee_seat_cap_check() in utils/helpers.py enforces at employee-
registration time, in blueprints/employees.py's add_employee_page() and
api_register_employee()).

Mirrors blueprints/billing.py's Razorpay order/verify pattern used for the
public /create_org signup, but simpler: the tenant already exists here, so
there's no provisioning step -- verify_payment() just tops up this exact
tenant's own paid_employee_slots and returns. Orders are staged in the
master att_master.seat_topup_orders table (not the tenant schema) purely so
the Platform Admin can see seat-purchase history the same way they already
see signup payments via billing.py's payment_orders (see
blueprints/platform_admin.py's _recent_payments()).
"""
import datetime
from flask import Blueprint, request, session, jsonify, redirect, g

from extensions import app_log, limiter, log_security_event
from database import get_db_connection, get_master_db
from utils.auth import admin_required
from utils.helpers import get_company_settings, invalidate_settings_cache, tpath
from utils.plan_limits import get_per_employee_paise, format_price_inr, get_tenant_employee_count
from utils.tax_invoice import on_payment_completed
from utils.razorpay_utils import (
    create_order as razorpay_create_order, verify_payment_signature,
    key_id as razorpay_key_id, create_id_or_demo, verify_or_demo,
)

seats_bp = Blueprint("seats", __name__)


def seat_payment_info(tenant_schema=None):
    """What adding employees costs right now, for the Employees page's Add
    Employee form and Excel import: a company with a seat limit pays one
    month (the per-employee rate) for each employee beyond the seats it has
    already paid for this period, before they're registered; from the next
    billing date its monthly bill / Auto-Debit includes them. No limit
    (paid_employee_slots NULL) or a trial (its own cap): nothing to pay."""
    from utils.helpers.gates import _tenant_is_trialing
    from utils.billing_notices import next_renewal_date
    tenant_schema = tenant_schema or g.tenant_db
    cap = get_company_settings().get("paid_employee_slots")
    if cap is None or _tenant_is_trialing():
        return {"limited": False, "needed": False}
    count = get_tenant_employee_count(tenant_schema)
    rate = get_per_employee_paise()
    try:
        renews_on = next_renewal_date(db_name=tenant_schema).strftime("%d %b %Y")
    except Exception:
        renews_on = None
    return {"limited": True, "needed": count >= cap, "free": max(0, cap - count), "cap": cap,
            "employees": count, "rate_paise": rate, "rate_display": format_price_inr(rate),
            "renews_on": renews_on}


def reset_paid_seats(tenant_schema):
    """A new billing period was just paid, for the employees the company has
    now: those are its paid seats for this period. Seats freed by people who
    left don't carry over -- they aren't in this period's bill. Companies
    without a seat limit are left alone. Never raises: the payment is
    already recorded."""
    from database import get_tenant_db
    try:
        conn = get_tenant_db(tenant_schema)
        cur = conn.cursor()
        cur.execute("UPDATE company_settings SET paid_employee_slots=(SELECT COUNT(*) FROM employees) "
                    "WHERE paid_employee_slots IS NOT NULL")
        conn.commit()
        cur.close()
        conn.close()
        invalidate_settings_cache()
    except Exception as exc:
        app_log.error("seats.reset_paid_seats(%s) failed: %s", tenant_schema, exc)

# Same convention as blueprints/billing.py's _DEMO_ORDER_PREFIX -- lets the
# full buy-seats flow be exercised locally without real Razorpay keys, and
# is refused outright by verify_payment() once real keys ARE configured.
_DEMO_ORDER_PREFIX = "demo_seat_order_"


@seats_bp.route("/settings/seats", methods=["GET"])
@admin_required
def seats_page():
    # Retired as a standalone page -- embedded into Settings > Finances >
    # Billing instead (templates/settings.html's #ss-billing) so it behaves
    # like every other Finances sub-section (no full page nav) rather than
    # navigating away. Redirecting (not deleting the route) keeps old
    # links/bookmarks working, same pattern as blueprints/payroll.py's
    # retired GET /email_config.
    return redirect(tpath("/settings?tab=billing"))


@seats_bp.route("/api/seats/create_order", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def create_order():
    co = get_company_settings()
    cap = co.get("paid_employee_slots")
    if cap is None:
        return jsonify({"ok": False, "msg": "Your plan already allows unlimited employees -- no seat purchase needed."}), 400

    data = request.get_json(silent=True) or request.form
    try:
        additional_seats = int(data.get("additional_seats") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Number of seats must be a number."}), 400
    if additional_seats < 1:
        return jsonify({"ok": False, "msg": "Enter at least 1 additional seat."}), 400
    if additional_seats > 10000:
        return jsonify({"ok": False, "msg": "That's too many seats for a single order. Contact support for a bulk upgrade."}), 400

    # One month per seat at the per-employee rate -- the company's base fee
    # or minimum (utils/plan_limits.calculate_price) is part of its monthly
    # bill, not of each extra employee.
    amount_paise = additional_seats * get_per_employee_paise()

    receipt_id = f"seats_{g.tenant_db}_{int(datetime.datetime.now().timestamp())}"
    order_id, is_demo, error = create_id_or_demo(
        _DEMO_ORDER_PREFIX, lambda: razorpay_create_order(amount_paise, receipt_id)
    )
    if error:
        app_log.error("seats.create_order failed: %s", error)
        return jsonify({"ok": False, "msg": error}), 502

    try:
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO seat_topup_orders (tenant_schema, company_name, razorpay_order_id, "
            "seats_purchased, amount_paise, requested_by, status) VALUES (%s, %s, %s, %s, %s, %s, 'created')",
            (g.tenant_db, co.get("company_name") or "", order_id, additional_seats, amount_paise,
             session.get("admin_username"))
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.error("seats.create_order: failed to stage seat_topup_orders row: %s", exc)
        return jsonify({"ok": False, "msg": "Could not start checkout. Please try again."}), 500

    return jsonify({
        "ok": True,
        "order_id": order_id,
        "amount_paise": amount_paise,
        "amount_display": format_price_inr(amount_paise),
        "currency": "INR",
        "key_id": razorpay_key_id(),
        "demo": is_demo,
        "additional_seats": additional_seats,
    })


@seats_bp.route("/api/seats/verify_payment", methods=["POST"])
@admin_required
@limiter.limit("20 per minute")
def verify_payment():
    data = request.get_json(silent=True) or request.form
    razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
    razorpay_payment_id = (data.get("razorpay_payment_id") or "").strip()
    razorpay_signature = (data.get("razorpay_signature") or "").strip()

    ok, is_demo, error = verify_or_demo(
        razorpay_order_id, _DEMO_ORDER_PREFIX, razorpay_payment_id, razorpay_signature, verify_payment_signature
    )
    if is_demo and ok:
        log_security_event(
            "seats.demo_checkout_completed", "Demo/test-mode seat top-up completed (no real payment)",
            level="INFO", order_id=razorpay_order_id,
        )
    elif not ok:
        if not is_demo:
            log_security_event(
                "seats.signature_invalid", "Razorpay payment signature verification failed for a seat top-up",
                level="ERROR", order_id=razorpay_order_id,
            )
        return jsonify({"ok": False, "msg": error}), 400

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    # tenant_schema=%s scopes the lookup to THIS tenant -- an order created
    # by one company can never be redeemed against another's seat cap, even
    # if its razorpay_order_id were somehow guessed.
    #
    # The UPDATE's "AND status != 'paid'" is load-bearing, not decorative:
    # two near-simultaneous requests for the same order (a flaky-network
    # client retry, a double-tap) must not both pass a SELECT-then-UPDATE
    # race and each credit seats once. Only the request whose UPDATE
    # actually flips a row (cur.rowcount == 1) gets to credit seats below;
    # the loser sees rowcount == 0 and falls through to the idempotent
    # "already paid" response instead.
    cur.execute(
        "UPDATE seat_topup_orders SET status='paid', razorpay_payment_id=%s, paid_at=NOW() "
        "WHERE razorpay_order_id=%s AND tenant_schema=%s AND status != 'paid'",
        (razorpay_payment_id, razorpay_order_id, g.tenant_db)
    )
    credited = cur.rowcount == 1
    conn.commit()

    cur.execute(
        "SELECT seats_purchased, id FROM seat_topup_orders WHERE razorpay_order_id=%s AND tenant_schema=%s",
        (razorpay_order_id, g.tenant_db)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"ok": False, "msg": "Unknown order."}), 404
    if not credited:
        # Already paid -- possibly by a concurrent request that won the
        # race above. Idempotent: never credit seats twice for one order.
        co = get_company_settings()
        return jsonify({"ok": True, "already_paid": True, "paid_employee_slots": co.get("paid_employee_slots")})

    seats_purchased = row[0]

    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute(
            "UPDATE company_settings SET paid_employee_slots = COALESCE(paid_employee_slots, 0) + %s WHERE id=1",
            (seats_purchased,)
        )
        db.commit()
        cur.close()
        db.close()
    except Exception as exc:
        app_log.error("seats.verify_payment: payment captured but failed to update paid_employee_slots for %s: %s",
                      g.tenant_db, exc)
        return jsonify({"ok": False, "msg": f"Payment received, but seat activation failed. Contact support with order ID {razorpay_order_id}."}), 500

    invalidate_settings_cache()
    co = get_company_settings()

    log_security_event(
        "seats.purchased", f"{seats_purchased} additional employee seat(s) purchased",
        level="INFO", order_id=razorpay_order_id, seats_purchased=seats_purchased,
        new_cap=co.get("paid_employee_slots"),
    )
    on_payment_completed("seat_topup", row[1])

    return jsonify({
        "ok": True,
        "seats_purchased": seats_purchased,
        "paid_employee_slots": co.get("paid_employee_slots"),
    })
