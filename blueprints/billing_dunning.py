# -*- coding: utf-8 -*-
"""Recurring-bill dunning: detects an unpaid monthly bill, gives a 5-day
grace period, locks the tenant (login still works, every state-changing
request is refused -- app.py's _enforce_billing_lock()) once that deadline
passes with no payment, and unlocks automatically the moment a payment
clears -- via blueprints/webhooks.py's Razorpay dispatcher, so no platform
admin action is needed on either side of the lock/unlock transition.

Distinct from blueprints/billing.py (one-time paid-signup checkout) and
blueprints/auto_debit.py (opt-in recurring auto-debit subscriptions) --
this module is what actually decides "has this tenant paid this month,"
regardless of which of those two collected the payment, and is the only
one of the three that ever changes tenants.billing_state.

Scope: only tenants with payment_option in ('online', 'manual') are ever
dunned -- 'trial' tenants have no bill to miss. A tenant is also skipped
for its first billing_period (the calendar month it signed up in), since
online signups already paid for that month at checkout (blueprints/
billing.py) and nothing here should immediately dun a brand-new company.
"""
import datetime
from flask import Blueprint, request, jsonify, redirect, render_template, session, g as _g

from extensions import app, app_log, log_security_event
from database import get_master_db
from utils.auth import admin_required
from utils.helpers import tpath, coerce_datetime
from utils.plan_limits import calculate_price, format_price_inr, get_tenant_employee_count
from utils.email_utils import get_email_config, get_admin_emails, send_email_async
from utils.razorpay_utils import (
    create_order as razorpay_create_order, verify_payment_signature,
    key_id as razorpay_key_id, create_id_or_demo, verify_or_demo, razorpay_configured,
)
from blueprints.webhooks import register_webhook_handler

billing_dunning_bp = Blueprint("billing_dunning", __name__)

_GRACE_DAYS = 5
_DEMO_ORDER_PREFIX = "demo_dunning_"


# ── Daily scheduled check (registered in wsgi.py) ────────────────────────────

def check_tenant_billing():
    """APScheduler job, no request/app context of its own -- see
    blueprints/daily_report.py's generate_and_send_daily_report() for why
    app.app_context() is required here (get_db_connection()/get_master_db()
    both read g, which doesn't exist outside one)."""
    try:
        with app.app_context():
            _run_billing_check()
    except Exception:
        app_log.exception("billing_dunning: unhandled error in daily check")


def _run_billing_check():
    period_start = datetime.date.today().replace(day=1)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, company_name, subdomain, db_name, payment_option, billing_state, "
        "grace_period_ends_at, created_at FROM tenants "
        "WHERE status='active' AND payment_option IN ('online', 'manual')"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for row in rows:
        (tenant_id, company_name, subdomain, db_name, payment_option,
         billing_state, grace_ends_at, created_at) = row
        try:
            _check_one_tenant(tenant_id, company_name, db_name, billing_state,
                              coerce_datetime(grace_ends_at), coerce_datetime(created_at), period_start)
        except Exception:
            app_log.exception(f"billing_dunning: check failed for tenant '{subdomain}'")


def _check_one_tenant(tenant_id, company_name, db_name, billing_state, grace_ends_at, created_at, period_start):
    # Signup month is covered by the checkout payment itself (blueprints/
    # billing.py) -- dunning only applies from the tenant's second cycle on.
    if created_at and created_at.date().replace(day=1) >= period_start:
        return

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT 1 FROM monthly_invoices WHERE tenant_schema=%s AND billing_period=%s AND status='paid'",
        (db_name, period_start)
    )
    paid = cur.fetchone() is not None
    cur.close()
    conn.close()

    if paid:
        if billing_state != "current":
            _set_billing_state(tenant_id, "current")
            _notify_tenant(db_name, company_name, "reactivated")
        return

    now = datetime.datetime.now()
    if billing_state == "current":
        grace_ends = datetime.datetime.combine(period_start, datetime.time.min) + datetime.timedelta(days=_GRACE_DAYS)
        _set_billing_state(tenant_id, "grace", grace_period_ends_at=grace_ends)
        _notify_tenant(db_name, company_name, "grace", grace_ends=grace_ends)
    elif billing_state == "grace" and grace_ends_at and now > grace_ends_at:
        _set_billing_state(tenant_id, "locked", locked_at=now)
        _notify_tenant(db_name, company_name, "locked")


def _set_billing_state(tenant_id, state, grace_period_ends_at=None, locked_at=None):
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tenants SET billing_state=%s, grace_period_ends_at=%s, locked_at=%s WHERE id=%s",
        (state, grace_period_ends_at, locked_at, tenant_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        "billing.state_changed", f"Tenant {tenant_id} billing_state -> {state}",
        level="ERROR" if state == "locked" else "INFO", tenant_id=tenant_id,
    )


# ── Notification emails ──────────────────────────────────────────────────────

def _dunning_email_content(kind, company_name, grace_ends=None):
    if kind == "grace":
        grace_str = grace_ends.strftime("%d %b %Y, %I:%M %p") if grace_ends else f"{_GRACE_DAYS} days from today"
        subject = "Action needed: your HRzest bill is overdue"
        body = (
            f"<p>This month's bill for <strong>{company_name}</strong> hasn't been paid yet.</p>"
            f"<p>You have until <strong>{grace_str}</strong> to pay before the account is locked. "
            f"Log in to your admin dashboard and use the <strong>Pay Now</strong> banner there -- "
            f"payment unlocks the account automatically, no further steps needed.</p>"
        )
    elif kind == "locked":
        subject = "Your HRzest account has been locked"
        body = (
            f"<p><strong>{company_name}</strong>'s account is now locked due to non-payment.</p>"
            f"<p>You can still log in, but no changes (including payroll) can be made until the "
            f"outstanding bill is paid. Log in and use the <strong>Pay Now</strong> banner to "
            f"reactivate immediately -- it unlocks automatically the moment payment clears.</p>"
        )
    else:  # reactivated
        subject = "Your HRzest account is active again"
        body = (
            f"<p>Payment received -- <strong>{company_name}</strong>'s account is fully active again. "
            f"Thanks for paying promptly.</p>"
        )
    html = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">HRzest.com</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">Billing notice</div>
  </div>
  <div style="padding:28px;font-size:14px;color:#1e293b;line-height:1.6;">{body}</div>
</div>"""
    return subject, html


def _notify_tenant(tenant_schema, company_name, kind, grace_ends=None):
    """Switches g.tenant_db to the target tenant's schema for the duration
    of the send (get_admin_emails()/get_email_config() both resolve against
    whichever schema get_db_connection() currently points to) and restores
    whatever it was before -- same pattern daily_report.py's job uses,
    applied per-tenant instead of once for the single-tenant fallback."""
    prev_tenant_db = getattr(_g, "tenant_db", None)
    try:
        _g.tenant_db = tenant_schema
        config = get_email_config()
        if not config:
            return
        recipients = get_admin_emails()
        if not recipients:
            return
        if not company_name:
            conn = get_master_db()
            cur = conn.cursor(buffered=True)
            cur.execute("SELECT company_name FROM tenants WHERE db_name=%s", (tenant_schema,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            company_name = row[0] if row else tenant_schema
        subject, html = _dunning_email_content(kind, company_name, grace_ends=grace_ends)
        for email in recipients:
            send_email_async(email, subject, html, config)
    except Exception:
        app_log.exception(f"billing_dunning: notify failed for '{tenant_schema}' ({kind})")
    finally:
        _g.tenant_db = prev_tenant_db


# ── Tenant-facing pay page + Razorpay checkout ───────────────────────────────

@billing_dunning_bp.route("/pay_overdue_bill", methods=["GET"])
@admin_required
def pay_overdue_bill_page():
    tenant_db = _g.tenant_db
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT company_name, billing_state, grace_period_ends_at, locked_at FROM tenants WHERE db_name=%s",
        (tenant_db,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return redirect(tpath("/admin"))
    company_name, billing_state, grace_ends_at, locked_at = row

    employee_count = get_tenant_employee_count(tenant_db)
    amount_paise = calculate_price(employee_count)
    return render_template(
        "pay_overdue_bill.html",
        company_name=company_name, billing_state=billing_state,
        grace_period_ends_at=coerce_datetime(grace_ends_at), locked_at=coerce_datetime(locked_at),
        employee_count=employee_count, amount_display=format_price_inr(amount_paise),
        amount_paise=amount_paise, key_id=razorpay_key_id(), razorpay_configured=razorpay_configured(),
    )


@billing_dunning_bp.route("/api/billing/overdue/create_order", methods=["POST"])
@admin_required
def create_overdue_order():
    tenant_db = _g.tenant_db
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT id, company_name FROM tenants WHERE db_name=%s", (tenant_db,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return jsonify({"ok": False, "msg": "Tenant not found."}), 404
    tenant_id, company_name = row

    employee_count = get_tenant_employee_count(tenant_db)
    amount_paise = calculate_price(employee_count)
    period_start = datetime.date.today().replace(day=1)

    receipt_id = f"dunning_{tenant_db}_{int(datetime.datetime.now().timestamp())}"
    order_id, is_demo, error = create_id_or_demo(
        _DEMO_ORDER_PREFIX, lambda: razorpay_create_order(amount_paise, receipt_id)
    )
    if error:
        cur.close()
        conn.close()
        app_log.error("billing_dunning.create_overdue_order failed: %s", error)
        return jsonify({"ok": False, "msg": error}), 502

    cur.execute(
        "INSERT INTO monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
        "status, billing_period, razorpay_order_id) VALUES (%s, %s, %s, %s, 'pending', %s, %s)",
        (tenant_db, company_name, employee_count, amount_paise, period_start, order_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "ok": True, "order_id": order_id, "amount_paise": amount_paise,
        "amount_display": format_price_inr(amount_paise), "currency": "INR",
        "key_id": razorpay_key_id(), "demo": is_demo,
    })


@billing_dunning_bp.route("/api/billing/overdue/verify", methods=["POST"])
@admin_required
def verify_overdue_payment():
    """Redirect-based immediate unlock for a snappy UX right after Checkout
    closes -- the payment.captured webhook below is the durable guarantee
    (fires even if this browser tab closes before the redirect completes),
    and _mark_invoice_paid_and_unlock() is idempotent so both firing is
    harmless."""
    data = request.get_json(silent=True) or request.form
    razorpay_order_id = (data.get("razorpay_order_id") or "").strip()
    razorpay_payment_id = (data.get("razorpay_payment_id") or "").strip()
    razorpay_signature = (data.get("razorpay_signature") or "").strip()

    ok, is_demo, error = verify_or_demo(
        razorpay_order_id, _DEMO_ORDER_PREFIX, razorpay_payment_id, razorpay_signature, verify_payment_signature
    )
    if not ok:
        if not is_demo:
            log_security_event(
                "billing.overdue_signature_invalid", "Razorpay overdue-bill payment signature verification failed",
                level="ERROR", order_id=razorpay_order_id,
            )
        return jsonify({"ok": False, "msg": error}), 400

    if not _mark_invoice_paid_and_unlock(razorpay_order_id, razorpay_payment_id, expected_tenant_db=_g.tenant_db):
        return jsonify({"ok": False, "msg": "Could not confirm this order. Contact support."}), 404
    # Unlock THIS session immediately -- otherwise the paying admin would
    # still be blocked by _enforce_billing_lock() for up to
    # _TENANT_STATUS_RECHECK_SEC (app.py) despite the DB already showing
    # billing_state='current', since that hook trusts the session's cached
    # flag rather than re-querying on every request. Other sessions (a
    # second admin logged in elsewhere) pick it up on their own next
    # recheck -- same staleness tolerance _resolve_tenant() already accepts
    # for a suspend/reactivate.
    session["_billing_locked"] = False
    _g.billing_locked = False
    return jsonify({"ok": True})


def _mark_invoice_paid_and_unlock(razorpay_order_id, razorpay_payment_id, expected_tenant_db=None):
    """Shared by the redirect-verify route above and the webhook handler
    below. Idempotent (checks status before writing) so a webhook delivery
    landing after (or racing) the browser's own verify call never double-
    processes the same payment."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, tenant_schema, status FROM monthly_invoices WHERE razorpay_order_id=%s",
        (razorpay_order_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        app_log.warning(f"billing_dunning: payment for unrecognized order {razorpay_order_id}")
        return False
    invoice_id, tenant_schema, status = row
    if expected_tenant_db and tenant_schema != expected_tenant_db:
        cur.close()
        conn.close()
        log_security_event(
            "billing.overdue_order_tenant_mismatch",
            f"Order {razorpay_order_id} belongs to '{tenant_schema}', not the requesting tenant",
            level="ERROR",
        )
        return False
    if status == "paid":
        cur.close()
        conn.close()
        return True

    # "AND status != 'paid'" is load-bearing, not decorative -- mirrors
    # billing.py's verify_payment() exactly: the browser-side verify call
    # and the payment.captured webhook can both reach here for the same
    # order after both passing the status=='paid' check above. Only the
    # request whose UPDATE actually flips a row (cursor.rowcount == 1)
    # reactivates the tenant and sends the notification; the loser gets
    # the same idempotent True a retried call expects.
    cur.execute(
        "UPDATE monthly_invoices SET status='paid', razorpay_payment_id=%s WHERE id=%s AND status != 'paid'",
        (razorpay_payment_id, invoice_id)
    )
    won_race = cur.rowcount == 1
    if not won_race:
        cur.close()
        conn.close()
        return True
    cur.execute(
        "UPDATE tenants SET billing_state='current', grace_period_ends_at=NULL, locked_at=NULL WHERE db_name=%s",
        (tenant_schema,)
    )
    conn.commit()
    cur.close()
    conn.close()

    log_security_event(
        "billing.overdue_paid_reactivated", f"Tenant '{tenant_schema}' paid its overdue bill; auto-reactivated",
        level="INFO",
    )
    _notify_tenant(tenant_schema, None, "reactivated")
    return True


def _handle_overdue_payment_captured(payload):
    """blueprints/webhooks.py dispatches here for every authentic
    payment.captured event -- including ones for a signup order
    (blueprints/billing.py's payment_orders) or a seat top-up
    (blueprints/seats.py), which simply won't match any row in
    monthly_invoices and no-op harmlessly (separate order-id spaces)."""
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = payment_entity.get("order_id")
    payment_id = payment_entity.get("id")
    if not order_id:
        return
    _mark_invoice_paid_and_unlock(order_id, payment_id)


register_webhook_handler("razorpay", "payment.captured", _handle_overdue_payment_captured)
