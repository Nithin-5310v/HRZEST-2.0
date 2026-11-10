# -*- coding: utf-8 -*-
"""Auto-debit blueprint -- monthly per-employee billing (utils/plan_limits.py's
"₹99/employee/month, flat" rate, already shown in admin_base.html's
"₹X/MO" badge) collected automatically via a Razorpay Subscription instead
of manually. Separate concern from blueprints/seats.py's paid_employee_slots
cap: seats.py gates HOW MANY employees a tenant may register at all;
this blueprint bills them for however many they actually have, every month,
without an admin needing to do anything once enrolled.

Design (Razorpay's documented pattern for seat-based recurring billing):
  - One shared Plan ("₹99/employee/month"), created once via the Plans API
    and cached in att_master.billing_config -- not a per-tenant object.
  - One Subscription per enrolled tenant, with `quantity` = that tenant's
    employee count. Total billed each cycle = plan amount * quantity.
    Razorpay's own recurring engine (real mode) or this module's
    sync_and_bill_auto_debit() (demo mode, run by wsgi.py's scheduler)
    charges it on schedule -- no per-request code triggers a charge.
  - quantity is kept in sync with actual headcount by the same daily job,
    via PATCH .../subscriptions/{id} (real) since Razorpay has no way to
    know a tenant's employee table changed.
  - Real-mode charges are confirmed asynchronously via Razorpay's
    "subscription.charged" webhook (POST /webhooks/razorpay, owned by
    blueprints/webhooks.py -- this module just registers handlers for the
    events it cares about, near the bottom of this file), not by any
    browser round-trip -- the browser is only present for the initial
    enrollment authorization.
"""
import datetime
import secrets
from flask import Blueprint, request, session, jsonify, g

from extensions import app_log, limiter, log_security_event
from database import get_db_connection, get_master_db
from utils.auth import admin_required
from utils.helpers import get_company_settings, coerce_datetime, platform_today
from utils.plan_limits import get_per_employee_paise, calculate_price, format_price_inr, get_tenant_employee_count
from utils.billing_cycle import (
    anchor_from, tenant_anchor, period_start as cycle_period_start, next_billing_date, first_period_prepaid,
    period_is_paid, charge_period, charge_epoch,
)
from utils.tax_invoice import on_payment_completed
from utils.razorpay_utils import (
    create_plan, create_customer, create_subscription, update_subscription_quantity, fetch_subscription,
    cancel_subscription as razorpay_cancel_subscription, verify_subscription_signature,
    key_id as razorpay_key_id, razorpay_configured,
    create_id_or_demo, verify_or_demo, list_subscription_invoices,
)
from blueprints.webhooks import register_webhook_handler

auto_debit_bp = Blueprint("auto_debit", __name__)

_DEMO_SUBSCRIPTION_PREFIX = "demo_sub_"
_PLAN_ITEM_NAME = "HRzest per-employee monthly fee"

# Razorpay refuses to change the amount of a subscription authorized with
# UPI Autopay, e-mandate or a card issued in India ("subscriptions cannot be
# updated when payment mode is UPI" / "... is emandate", "Only offers can be
# updated for subscriptions when payment mode is domestic card."): a new
# headcount needs a new authorization -- enroll with replace=True below.
_FIXED_AMOUNT_MARKERS = ("payment mode is upi", "payment mode is emandate", "payment mode is domestic card")
FIXED_AMOUNT_MSG = ("Razorpay can't change the amount of an Auto-Debit set up with UPI, an Indian card or "
                    "net banking (e-mandate). Set it up again for {count} employee{s} — your current "
                    "Auto-Debit keeps working until the new one is authorized, and nothing is charged twice.")


# Subscription payment methods whose mandate is for a fixed amount.
_FIXED_AMOUNT_METHODS = ("upi", "emandate", "nach")


def _is_fixed_amount_error(error):
    return any(m in (error or "").lower() for m in _FIXED_AMOUNT_MARKERS)


def _is_fixed_amount_subscription(subscription_id, error):
    """Whether a refused quantity update means the subscription's amount
    can't change. Razorpay's message isn't always the "payment mode is UPI"
    one (an authorized subscription that hasn't charged yet is refused with
    other wording), so when the message doesn't say, ask Razorpay how the
    subscription is paid."""
    if _is_fixed_amount_error(error):
        return True
    sub, fetch_error = fetch_subscription(subscription_id)
    if fetch_error or not sub:
        app_log.warning("auto_debit: couldn't look up subscription %s after a refused update: %s",
                        subscription_id, fetch_error)
        return False
    return (sub.get("payment_method") or "").strip().lower() in _FIXED_AMOUNT_METHODS


def fixed_amount_msg(count):
    return FIXED_AMOUNT_MSG.format(count=count, s="" if count == 1 else "s")


def _get_or_create_plan_id():
    """Returns (plan_id, error). Cached in billing_config so every tenant's
    subscription shares one Plan -- a fresh Plan per tenant would work too,
    but would mean N near-identical Razorpay dashboard objects for no
    benefit, since `quantity` is what actually varies per tenant."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT razorpay_plan_id FROM billing_config WHERE id=1")
    row = cur.fetchone()
    existing = row[0] if row else None
    if existing:
        cur.close()
        conn.close()
        return existing, None

    # NOTE: Razorpay Plans are immutable once created -- this only picks up
    # the admin's current rate the FIRST time any tenant enrolls in
    # auto-debit (which is when this Plan gets created and cached in
    # billing_config.razorpay_plan_id, below, forever after). A rate change
    # made after that point does NOT retroactively reprice existing
    # auto-debit subscribers; they'd need a new Plan + subscription
    # migration, which is out of scope here. Every other billing path
    # (signup checkout, seat top-ups, manual/dunning bills, the Platform
    # Admin P&L) reads the live rate on every calculation, no such caveat.
    plan_id, error = create_plan(get_per_employee_paise(), _PLAN_ITEM_NAME)
    if error:
        cur.close()
        conn.close()
        return None, error
    cur.execute("UPDATE billing_config SET razorpay_plan_id=%s WHERE id=1", (plan_id,))
    conn.commit()
    cur.close()
    conn.close()
    return plan_id, None


def _get_mandate(tenant_schema: str):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT status, razorpay_customer_id, razorpay_subscription_id, quantity_synced, fixed_amount, "
        "pending_subscription_id, pending_quantity, pending_start_at, first_charge_on "
        "FROM auto_debit_mandates WHERE tenant_schema=%s",
        (tenant_schema,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {"status": row[0], "customer_id": row[1], "subscription_id": row[2], "quantity_synced": row[3],
            "fixed_amount": bool(row[4]), "pending_subscription_id": row[5], "pending_quantity": row[6],
            "pending_start_at": row[7], "first_charge_on": coerce_datetime(row[8])}


def _first_charge_date(tenant_schema):
    """When a new Auto-Debit should first charge: the next billing date
    (utils/billing_cycle.py) if the current period is already paid -- so
    nothing is charged twice -- or None to charge as soon as it's authorized
    (a trial or an unpaid period), which makes that day the plan date."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    try:
        cur.execute("SELECT subscription_status, billing_anchor_date, created_at FROM tenants WHERE db_name=%s",
                    (tenant_schema,))
        row = cur.fetchone()
        if not row or row[0] in ("trialing", "past_due"):
            return None
        today = platform_today()
        anchor, explicit = anchor_from(row[1], row[2], today)
        if today < anchor:
            return anchor
        start = cycle_period_start(anchor, today)
        covered = (start == anchor and first_period_prepaid(anchor, explicit, row[2])) \
            or period_is_paid(cur, tenant_schema, start)
        return next_billing_date(anchor, today) if covered else None
    finally:
        cur.close()
        conn.close()


@auto_debit_bp.route("/api/auto_debit/enroll", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def enroll():
    co = get_company_settings()
    mandate = _get_mandate(g.tenant_db)
    data = request.get_json(silent=True) or {}
    replace = str(data.get("replace", "")).lower() in ("1", "true", "yes", "on")
    if mandate and mandate["status"] == "active" and not replace:
        return jsonify({"ok": False, "msg": "Auto-debit is already enabled for your company."}), 400
    if replace and not (mandate and mandate["status"] == "active"):
        return jsonify({"ok": False, "msg": "Auto-debit isn't currently enabled."}), 400
    if replace:
        return _enroll_replacement(mandate)

    employee_count = get_tenant_employee_count(g.tenant_db)
    first_charge_on = _first_charge_date(g.tenant_db)

    customer_id = None

    def _create_real_subscription():
        nonlocal customer_id
        plan_id, error = _get_or_create_plan_id()
        if error:
            return None, error

        cur_db = get_db_connection()
        c = cur_db.cursor(buffered=True)
        c.execute("SELECT email FROM admin_users WHERE username=%s", (session.get("admin_username"),))
        row = c.fetchone()
        c.close()
        cur_db.close()
        admin_email = (row[0] if row else None) or "billing@example.com"

        customer_id, error = create_customer(co.get("company_name") or g.tenant_db, admin_email)
        if error:
            return None, error

        return create_subscription(plan_id, customer_id, employee_count or 1,
                                   start_at=charge_epoch(first_charge_on) if first_charge_on else None)

    subscription_id, is_demo, error = create_id_or_demo(_DEMO_SUBSCRIPTION_PREFIX, _create_real_subscription)
    if error:
        app_log.error("auto_debit.enroll: subscription setup failed: %s", error)
        return jsonify({"ok": False, "msg": error}), 502

    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
        "razorpay_subscription_id, quantity_synced, status, requested_by, created_at, first_charge_on) "
        "VALUES (%s, %s, %s, %s, %s, 'pending', %s, NOW(), %s) "
        "ON CONFLICT (tenant_schema) DO UPDATE SET company_name=%s, razorpay_customer_id=%s, "
        "razorpay_subscription_id=%s, quantity_synced=%s, status='pending', requested_by=%s, "
        "created_at=NOW(), cancelled_at=NULL, first_charge_on=%s",
        (g.tenant_db, co.get("company_name") or "", customer_id, subscription_id, employee_count,
         session.get("admin_username"), first_charge_on, co.get("company_name") or "", customer_id,
         subscription_id, employee_count, session.get("admin_username"), first_charge_on)
    )
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "ok": True,
        "subscription_id": subscription_id,
        "key_id": razorpay_key_id(),
        "demo": is_demo,
        "employee_count": employee_count,
        "amount_display": format_price_inr(calculate_price(employee_count)),
        "first_charge_on": first_charge_on.isoformat() if first_charge_on else None,
        "first_charge_display": first_charge_on.strftime("%d %b %Y") if first_charge_on else None,
    })


def _enroll_replacement(mandate):
    """A new subscription for today's headcount to take over from the active
    one, which keeps billing until this one is authorized. It starts on the
    current subscription's next charge date, and confirm() cancels the
    current one at its cycle end -- no gap and no month charged twice."""
    employee_count = get_tenant_employee_count(g.tenant_db)
    start_at = None

    def _create_real_subscription():
        nonlocal start_at
        current, error = fetch_subscription(mandate["subscription_id"])
        if error:
            return None, error
        start_at = current.get("charge_at") or current.get("current_end")
        if start_at and start_at <= datetime.datetime.now().timestamp():
            start_at = None
        plan_id, error = _get_or_create_plan_id()
        if error:
            return None, error
        return create_subscription(plan_id, mandate["customer_id"], employee_count or 1, start_at=start_at)

    subscription_id, is_demo, error = create_id_or_demo(_DEMO_SUBSCRIPTION_PREFIX, _create_real_subscription)
    if error:
        app_log.error("auto_debit.enroll(replace): subscription setup failed for %s: %s", g.tenant_db, error)
        return jsonify({"ok": False, "msg": error}), 502

    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE auto_debit_mandates SET pending_subscription_id=%s, pending_quantity=%s, pending_start_at=%s "
        "WHERE tenant_schema=%s",
        (subscription_id, employee_count, start_at, g.tenant_db)
    )
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({
        "ok": True,
        "subscription_id": subscription_id,
        "key_id": razorpay_key_id(),
        "demo": is_demo,
        "replace": True,
        "employee_count": employee_count,
        "amount_display": format_price_inr(calculate_price(employee_count)),
    })


def _confirm_replacement(mandate, subscription_id):
    """The replacement subscription was authorized: it becomes the company's
    Auto-Debit, and the one it replaces is cancelled at its cycle end (or
    now, when the new one starts now). A failed cancel is logged loudly --
    the new subscription is already authorized, so it must not be lost."""
    old_id = mandate["subscription_id"] or ""
    if old_id and not old_id.startswith(_DEMO_SUBSCRIPTION_PREFIX):
        ok, error = razorpay_cancel_subscription(old_id, cancel_at_cycle_end=bool(mandate["pending_start_at"]))
        if not ok:
            app_log.error("auto_debit: replaced subscription %s for %s could not be cancelled (%s) -- cancel it "
                          "in the Razorpay dashboard", old_id, g.tenant_db, error)
            log_security_event("auto_debit.replaced_cancel_failed", "Old auto-debit subscription not cancelled",
                               level="ERROR", subscription_id=old_id)
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE auto_debit_mandates SET razorpay_subscription_id=%s, quantity_synced=COALESCE(pending_quantity, "
        "quantity_synced), status='active', activated_at=NOW(), fixed_amount=FALSE, pending_subscription_id=NULL, "
        "pending_quantity=NULL, pending_start_at=NULL, reauth_notified_quantity=NULL WHERE tenant_schema=%s",
        (subscription_id, g.tenant_db)
    )
    conn.commit()
    cur.close()
    conn.close()
    log_security_event("auto_debit.replaced", f"Auto-debit set up again (replaces {old_id})",
                       level="INFO", subscription_id=subscription_id)
    return jsonify({"ok": True, "replaced": True})


@auto_debit_bp.route("/api/auto_debit/confirm", methods=["POST"])
@admin_required
@limiter.limit("20 per minute")
def confirm():
    data = request.get_json(silent=True) or request.form
    subscription_id = (data.get("razorpay_subscription_id") or "").strip()
    payment_id = (data.get("razorpay_payment_id") or "").strip()
    signature = (data.get("razorpay_signature") or "").strip()

    mandate = _get_mandate(g.tenant_db)
    replacing = bool(mandate and subscription_id and mandate["pending_subscription_id"] == subscription_id)
    if not mandate or (mandate["subscription_id"] != subscription_id and not replacing):
        return jsonify({"ok": False, "msg": "Unknown or mismatched subscription."}), 404

    ok, is_demo, error = verify_or_demo(
        subscription_id, _DEMO_SUBSCRIPTION_PREFIX, payment_id, signature, verify_subscription_signature
    )
    if is_demo and ok:
        log_security_event(
            "auto_debit.demo_enrolled", "Demo/test-mode auto-debit enrollment completed (no real mandate)",
            level="INFO", subscription_id=subscription_id,
        )
    elif not ok:
        if not is_demo:
            log_security_event(
                "auto_debit.signature_invalid", "Razorpay subscription-authorization signature verification failed",
                level="ERROR", subscription_id=subscription_id,
            )
        return jsonify({"ok": False, "msg": error}), 400

    if replacing:
        return _confirm_replacement(mandate, subscription_id)

    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE auto_debit_mandates SET status='active', activated_at=NOW() "
        "WHERE tenant_schema=%s AND razorpay_subscription_id=%s",
        (g.tenant_db, subscription_id)
    )
    # The company is paid for from here on. A trial that ended with no
    # payment method (trial_billing.py) comes back to normal, out of its
    # grace period or lock; a trial still running ends now rather than at
    # trial_end_date -- this is the upgrade its 2-employee cap points to
    # (utils/helpers.add_employee_seat_cap_check()).
    cur.execute(
        "UPDATE tenants SET subscription_status='active', payment_option='online', billing_state='current', "
        "grace_period_ends_at=NULL, locked_at=NULL "
        "WHERE db_name=%s AND subscription_status IN ('past_due', 'trialing')",
        (g.tenant_db,)
    )
    reactivated = cur.rowcount == 1
    if not mandate["first_charge_on"]:
        # It charges now (a trial ending, or an unpaid period): today is the
        # plan date, and Auto-Debit charges on this day every month.
        cur.execute("UPDATE tenants SET billing_anchor_date=%s WHERE db_name=%s", (platform_today(), g.tenant_db))
    conn.commit()
    cur.close()
    conn.close()
    if reactivated:
        session["_billing_locked"] = False
        g.billing_locked = False

    log_security_event(
        "auto_debit.activated", "Monthly auto-debit enabled", level="INFO", subscription_id=subscription_id,
    )
    return jsonify({"ok": True})


@auto_debit_bp.route("/api/auto_debit/update_quantity", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def update_quantity():
    """Settings > Billing's "Update Auto-Debit" (and the employee-added
    email's link to it): bill the current headcount from the next charge
    on, now, instead of at the daily sync."""
    mandate = _get_mandate(g.tenant_db)
    if not mandate or mandate["status"] != "active":
        return jsonify({"ok": False, "msg": "Auto-debit isn't currently enabled."}), 400
    count = get_tenant_employee_count(g.tenant_db)
    subscription_id = mandate["subscription_id"] or ""
    errors = []
    if not _sync_mandate_quantity(g.tenant_db, subscription_id, mandate["quantity_synced"], count,
                                  is_demo_sub=subscription_id.startswith(_DEMO_SUBSCRIPTION_PREFIX),
                                  errors=errors):
        if (_get_mandate(g.tenant_db) or {}).get("fixed_amount"):
            return jsonify({"ok": False, "needs_reauth": True, "employees": count,
                            "amount_display": format_price_inr(calculate_price(count)),
                            "msg": fixed_amount_msg(count)}), 409
        reason = (errors[0] if errors else "").replace("Razorpay request failed: ", "").strip()
        return jsonify({"ok": False, "msg": "Razorpay didn't accept the update"
                        + (f" ({reason})" if reason else "") + ". Please try again in a few minutes."}), 502
    log_security_event("auto_debit.quantity_updated", f"Auto-debit updated to {count} employees",
                       level="INFO", subscription_id=subscription_id)
    return jsonify({"ok": True, "employees": count, "amount_display": format_price_inr(calculate_price(count))})


@auto_debit_bp.route("/api/auto_debit/cancel", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def cancel():
    mandate = _get_mandate(g.tenant_db)
    if not mandate or mandate["status"] != "active":
        return jsonify({"ok": False, "msg": "Auto-debit isn't currently enabled."}), 400

    data = request.get_json(silent=True) or request.form
    at_period_end = str(data.get("at_period_end", "")).lower() in ("1", "true", "yes", "on")

    if not mandate["subscription_id"].startswith(_DEMO_SUBSCRIPTION_PREFIX):
        ok, error = razorpay_cancel_subscription(mandate["subscription_id"], cancel_at_cycle_end=at_period_end)
        if not ok:
            app_log.error("auto_debit.cancel: Razorpay cancel failed: %s", error)
            return jsonify({"ok": False, "msg": error}), 502

    conn = get_master_db()
    cur = conn.cursor()
    if at_period_end:
        # Stays 'pending_cancellation' (still billed normally) until
        # Razorpay's subscription.cancelled webhook fires at the end of the
        # current cycle -- _handle_subscription_cancelled() below is what
        # actually flips this to 'cancelled'. Demo mandates have no real
        # Razorpay engine to fire that webhook, so they're cancelled
        # immediately -- there's no "current cycle" to honor for them.
        if mandate["subscription_id"].startswith(_DEMO_SUBSCRIPTION_PREFIX):
            cur.execute(
                "UPDATE auto_debit_mandates SET status='cancelled', cancelled_at=NOW() WHERE tenant_schema=%s",
                (g.tenant_db,)
            )
        else:
            cur.execute(
                "UPDATE auto_debit_mandates SET status='pending_cancellation' WHERE tenant_schema=%s",
                (g.tenant_db,)
            )
    else:
        cur.execute(
            "UPDATE auto_debit_mandates SET status='cancelled', cancelled_at=NOW() WHERE tenant_schema=%s",
            (g.tenant_db,)
        )
    conn.commit()
    cur.close()
    conn.close()

    log_security_event(
        "auto_debit.cancelled",
        "Monthly auto-debit cancellation scheduled for period end" if at_period_end else "Monthly auto-debit disabled",
        level="INFO",
    )
    return jsonify({"ok": True, "at_period_end": at_period_end})


def _handle_subscription_charged(payload):
    sub_entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    subscription_id = sub_entity.get("id")
    amount_paise = payment_entity.get("amount")
    payment_id = payment_entity.get("id")
    if subscription_id:
        _record_charge(subscription_id, amount_paise, payment_id, status="paid",
                       charged_at=payment_entity.get("created_at"))
        _migrate_mandate_if_needed(subscription_id)


def _migrate_mandate_if_needed(subscription_id):
    """Called right after a subscription.charged webhook records a paid
    cycle -- if the platform admin changed the rate since this mandate's
    last renewal (needs_rate_migration, set by blueprints/platform_admin.py's
    platform_admin_set_rate()), the cycle that was JUST paid was still
    correctly billed at the old rate (never touch an in-flight charge);
    this is the safe moment to cancel the subscription so it can't silently
    renew again at that stale price, and prompt the tenant to re-authorize
    a fresh one on the new rate. No-ops for every mandate not flagged."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, tenant_schema, company_name FROM auto_debit_mandates "
        "WHERE razorpay_subscription_id=%s AND status='active' AND needs_rate_migration=TRUE",
        (subscription_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return
    mandate_id, tenant_schema, company_name = row

    ok, error = razorpay_cancel_subscription(subscription_id, cancel_at_cycle_end=False)
    if not ok:
        app_log.error("auto_debit._migrate_mandate_if_needed: failed to cancel %s: %s", subscription_id, error)
        cur.close()
        conn.close()
        return

    cur.execute(
        "UPDATE auto_debit_mandates SET status='rate_changed', needs_rate_migration=FALSE, "
        "cancelled_at=NOW() WHERE id=%s",
        (mandate_id,)
    )
    conn.commit()
    cur.close()
    conn.close()

    log_security_event(
        "auto_debit.rate_migration_cancelled",
        f"Auto-debit subscription for '{tenant_schema}' cancelled for rate-change migration",
        level="WARNING", subscription_id=subscription_id,
    )
    _notify_rate_change(tenant_schema, company_name)


def _notify_rate_change(tenant_schema, company_name):
    """Switches g.tenant_db to the target tenant's schema for the duration
    of the send (get_billing_emails()/get_email_config() both resolve against
    whichever schema get_db_connection() currently points to), same
    pattern as blueprints/billing_dunning.py's _notify_tenant()."""
    from flask import g as _g
    from utils.email_utils import get_email_config, get_billing_emails, send_email_async
    from utils.plan_limits import get_per_employee_paise
    prev_tenant_db = getattr(_g, "tenant_db", None)
    try:
        _g.tenant_db = tenant_schema
        config = get_email_config()
        if not config:
            return
        recipients = get_billing_emails()
        if not recipients:
            return
        rate_display = format_price_inr(get_per_employee_paise())
        subject = "Your HRzest auto-debit rate has changed -- please re-authorize"
        html = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">HRzest.com</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">Billing notice</div>
  </div>
  <div style="padding:28px;font-size:14px;color:#1e293b;line-height:1.6;">
    <p>The per-employee rate for <strong>{company_name}</strong> has changed to <strong>{rate_display}/employee/month</strong>.</p>
    <p>Your previous auto-debit authorization has ended, right after your last successful payment at the old rate --
    no partial or surprise charge. To keep billing automatic, log in and re-enable auto-debit from
    <strong>Settings &rarr; Seats &amp; Billing</strong>. If you'd rather pay manually going forward, no action is needed.</p>
  </div>
</div>"""
        for email in recipients:
            send_email_async(email, subject, html, config)
    except Exception:
        app_log.exception(f"auto_debit._notify_rate_change failed for '{tenant_schema}'")
    finally:
        _g.tenant_db = prev_tenant_db


def _handle_subscription_cancelled(payload):
    subscription_id = payload.get("payload", {}).get("subscription", {}).get("entity", {}).get("id")
    if subscription_id:
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE auto_debit_mandates SET status='cancelled', cancelled_at=NOW() "
            "WHERE razorpay_subscription_id=%s", (subscription_id,)
        )
        conn.commit()
        cur.close()
        conn.close()


def _handle_payment_failed_or_pending(payload):
    sub_entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    subscription_id = sub_entity.get("id")
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if subscription_id:
        _record_charge(
            subscription_id, payment_entity.get("amount"), payment_entity.get("id"),
            status="failed", failure_reason=payment_entity.get("error_description"),
        )
        # Trial-originated tenants get subscription_status flipped from
        # 'trialing' to 'active'/'online' at trial end (blueprints/
        # trial_billing.py's check_trial_expirations()) specifically so a
        # failed charge here is picked up by blueprints/billing_dunning.py's
        # existing daily cron the same way any other tenant's missed bill
        # is -- that cron already provides the grace period, proactive
        # emails, and lock/unlock cycle the failed-charge case needs, so
        # there's no separate state machine to drive from here.


# Registered at import time -- blueprints/webhooks.py's generic
# /webhooks/razorpay route dispatches here once signature verification
# passes. See that module's docstring for why the route itself doesn't
# live in this file.
register_webhook_handler("razorpay", "subscription.charged", _handle_subscription_charged)
register_webhook_handler("razorpay", "subscription.cancelled", _handle_subscription_cancelled)
register_webhook_handler("razorpay", "payment.failed", _handle_payment_failed_or_pending)
register_webhook_handler("razorpay", "subscription.pending", _handle_payment_failed_or_pending)


def _charged_on(charged_at):
    """Razorpay epoch seconds -> the platform date (today when missing)."""
    try:
        if charged_at:
            from utils.helpers.settings import PLATFORM_TZ
            return datetime.datetime.fromtimestamp(int(charged_at), PLATFORM_TZ).date()
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return platform_today()


def _record_charge(subscription_id, amount_paise, payment_id, status, failure_reason=None, charged_at=None):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT tenant_schema, company_name FROM auto_debit_mandates WHERE razorpay_subscription_id=%s",
        (subscription_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        app_log.warning("auto_debit._record_charge: unknown subscription_id %s", subscription_id)
        return
    tenant_schema, company_name = row
    employee_count = get_tenant_employee_count(tenant_schema)
    anchor, explicit = tenant_anchor(tenant_schema, cur)
    billing_period = charge_period(anchor, explicit, _charged_on(charged_at))
    rate_paise = get_per_employee_paise()
    # Razorpay explicitly redelivers webhooks on timeout/non-2xx/network
    # retry -- idx_monthly_invoices_payment_id (app.py) is a real DB-level
    # unique constraint on razorpay_payment_id (partial: only when it's not
    # NULL), so a redelivered subscription.charged event can never insert a
    # second invoice row for the same payment, even under a race between
    # two overlapping webhook deliveries.
    cur.execute(
        "INSERT INTO monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
        "razorpay_payment_id, razorpay_subscription_id, status, billing_period, failure_reason, rate_paise) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (razorpay_payment_id) WHERE razorpay_payment_id IS NOT NULL DO NOTHING RETURNING id",
        (tenant_schema, company_name, employee_count, amount_paise or 0, payment_id, subscription_id,
         status, billing_period, failure_reason, rate_paise)
    )
    inserted = cur.fetchone()
    if payment_id and not inserted:
        conn.commit()
        cur.close()
        conn.close()
        app_log.info("auto_debit._record_charge: duplicate webhook delivery for payment_id %s, skipping", payment_id)
        return
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        f"auto_debit.charge_{status}", f"Monthly auto-debit {status} for {company_name}",
        level="INFO" if status == "paid" else "WARNING",
        subscription_id=subscription_id, amount_paise=amount_paise,
    )
    if status == "paid" and inserted:
        on_payment_completed("invoice", inserted[0])
        from blueprints.seats import reset_paid_seats
        reset_paid_seats(tenant_schema)


def sync_and_bill_auto_debit():
    """Scheduled once daily (wsgi.py). Two jobs in one pass over every
    active mandate:
      1. Real mode: keep each Subscription's `quantity` in sync with the
         tenant's actual current headcount (Razorpay has no way to know an
         employee table changed -- nothing else ever calls
         update_subscription_quantity()). Real charging itself happens on
         Razorpay's own schedule, reported back via the webhook above.
      2. Demo mode: there is no real Razorpay engine to charge a
         "demo_sub_..." subscription on a schedule, so this simulates it --
         once a mandate has gone >=30 days since its last invoice (or since
         activation, if none yet), it writes a paid monthly_invoices row
         itself. This is what makes the whole enroll -> auto-charge ->
         billing-history flow demonstrable with zero Razorpay credentials,
         same as this codebase's existing demo-order conventions elsewhere.
    Every per-tenant step is wrapped so one tenant's failure can't sink the
    others -- same fail-soft posture as utils/plan_limits.py's
    get_tenant_employee_count().
    """
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute(
            "SELECT tenant_schema, company_name, razorpay_subscription_id, quantity_synced, activated_at "
            "FROM auto_debit_mandates WHERE status='active'"
        )
        mandates = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.error("sync_and_bill_auto_debit: failed to load mandates: %s", exc)
        return

    for tenant_schema, company_name, subscription_id, quantity_synced, activated_at in mandates:
        try:
            current_count = get_tenant_employee_count(tenant_schema)
            is_demo_sub = (subscription_id or "").startswith(_DEMO_SUBSCRIPTION_PREFIX)

            if is_demo_sub and not razorpay_configured():
                _maybe_simulate_demo_charge(tenant_schema, company_name, subscription_id, current_count, activated_at)
                _sync_mandate_quantity(tenant_schema, subscription_id, quantity_synced, current_count, is_demo_sub=True)
            elif is_demo_sub:
                # Real Razorpay keys were configured after this tenant
                # enrolled in demo mode -- its subscription was never
                # actually created with Razorpay, so there's nothing to
                # bill (real or simulated) until they re-enroll. Keep
                # skipping rather than fabricating further paid invoices
                # for a subscription that was never real.
                app_log.warning(
                    "sync_and_bill_auto_debit: tenant %s has a stale demo subscription (%s) now that Razorpay "
                    "is configured -- ask them to re-enroll in auto-debit.", tenant_schema, subscription_id
                )
            elif not _sync_mandate_quantity(tenant_schema, subscription_id, quantity_synced, current_count,
                                            is_demo_sub=False):
                _notify_fixed_amount(tenant_schema, current_count)
        except Exception as exc:
            app_log.error("sync_and_bill_auto_debit: failed for tenant %s: %s", tenant_schema, exc)


def _notify_fixed_amount(tenant_schema, count):
    """Tell the company's admins -- once per headcount -- that their
    Auto-Debit can't follow the new headcount and needs setting up again,
    so a UPI/e-mandate subscription doesn't quietly keep billing too few
    employees."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT m.fixed_amount, m.reauth_notified_quantity, t.subdomain FROM auto_debit_mandates m "
        "LEFT JOIN tenants t ON t.db_name = m.tenant_schema WHERE m.tenant_schema=%s", (tenant_schema,))
    row = cur.fetchone()
    if not row or not row[0] or row[1] == count:
        cur.close()
        conn.close()
        return
    cur.execute("UPDATE auto_debit_mandates SET reauth_notified_quantity=%s WHERE tenant_schema=%s",
                (count, tenant_schema))
    conn.commit()
    cur.close()
    conn.close()
    from utils.billing_notices import _send_to_admins, company_link
    link = company_link(row[2], "/settings?tab=billing")
    body = (f"<p>{fixed_amount_msg(count)}</p>"
            "<p>Until then, Auto-Debit keeps charging for the number of employees it was set up with.</p>"
            f'<p style="margin-top:18px;"><a href="{link}" style="background:#1d4ed8;color:#fff;padding:10px 18px;'
            f'border-radius:8px;text-decoration:none;">Set up Auto-Debit for {count} employees</a></p>')
    _send_to_admins(tenant_schema, "Action needed: set up Auto-Debit again for your new headcount", body,
                    heading="Auto-Debit")


def reconcile_billing_records():
    """Scheduled nightly (wsgi.py) -- a safety net for sync_and_bill_auto_debit()'s
    real-mode charges, which normally land in monthly_invoices via the
    subscription.charged webhook (blueprints/webhooks.py), not via any
    scheduled job. If that webhook delivery is ever missed entirely (a
    brief outage during Razorpay's own retry window, a bug in the webhook
    endpoint) the tenant's payment still succeeds on Razorpay's side, but
    this app never finds out -- silently drifting the two systems apart,
    with a real, concrete consequence: blueprints/billing_dunning.py's
    check_tenant_billing() would treat a genuinely-paid tenant as overdue
    and eventually lock their account.

    For every active, non-demo mandate, asks Razorpay directly (the
    authoritative source, not a guess) for its recent invoice history and
    replays each one Razorpay shows as paid through _record_charge() --
    the exact same function the webhook handler itself calls, so there is
    only one place that ever maps "Razorpay's fields" to this app's
    monthly_invoices columns, and its existing idempotency (a real DB
    unique constraint on razorpay_payment_id, see app.py's
    idx_monthly_invoices_payment_id) means replaying an already-recorded
    invoice is always a safe no-op -- this never needs to first check
    what's already there.

    Never invents a charge Razorpay doesn't itself show as paid; a
    genuine payment failure is check_tenant_billing()'s job to act on,
    not this one's."""
    if not razorpay_configured():
        return
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT tenant_schema, razorpay_subscription_id FROM auto_debit_mandates "
        "WHERE status='active' AND razorpay_subscription_id IS NOT NULL "
        "AND razorpay_subscription_id NOT LIKE %s",
        (_DEMO_SUBSCRIPTION_PREFIX + "%",)
    )
    mandates = cur.fetchall()
    cur.close()
    conn.close()

    for tenant_schema, subscription_id in mandates:
        try:
            invoices, error = list_subscription_invoices(subscription_id)
            if error:
                app_log.warning(
                    "reconcile_billing_records: could not fetch invoices for %s (%s): %s",
                    tenant_schema, subscription_id, error,
                )
                continue
            for inv in invoices:
                if inv.get("status") != "paid":
                    continue
                payment_id = inv.get("payment_id")
                if not payment_id:
                    continue
                amount_paise = inv.get("amount_paid") or inv.get("amount") or 0
                _record_charge(subscription_id, amount_paise, payment_id, status="paid",
                               charged_at=inv.get("paid_at") or inv.get("date"))
        except Exception as exc:
            app_log.error("reconcile_billing_records: failed for tenant %s: %s", tenant_schema, exc)


def _sync_mandate_quantity(tenant_schema, subscription_id, quantity_synced, current_count, is_demo_sub,
                           errors=None):
    """Keeps one mandate's Razorpay subscription `quantity` in sync with the
    tenant's actual current headcount, and records that in quantity_synced.
    Shared by sync_and_bill_auto_debit()'s daily pass above and
    blueprints/trial_billing.py's check_trial_expirations() (which needs
    the same sync done once, immediately, right as a trial converts, rather
    than waiting for this job's own daily run). Real mode only calls
    Razorpay when the count actually changed; demo mode has no live
    subscription to PATCH, so it just records the new count. Returns
    whether the mandate now matches current_count; Razorpay's reason for a
    refusal is appended to `errors` when given."""
    if current_count == quantity_synced:
        return True
    if not is_demo_sub:
        ok, error = update_subscription_quantity(subscription_id, current_count or 1)
        if not ok:
            app_log.warning("_sync_mandate_quantity: quantity sync failed for %s: %s", tenant_schema, error)
            if errors is not None:
                errors.append(error)
            if _is_fixed_amount_subscription(subscription_id, error):
                conn = get_master_db()
                cur = conn.cursor()
                cur.execute("UPDATE auto_debit_mandates SET fixed_amount=TRUE WHERE tenant_schema=%s",
                            (tenant_schema,))
                conn.commit()
                cur.close()
                conn.close()
            return False
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE auto_debit_mandates SET quantity_synced=%s WHERE tenant_schema=%s",
        (current_count, tenant_schema)
    )
    conn.commit()
    cur.close()
    conn.close()
    return True


def _maybe_simulate_demo_charge(tenant_schema, company_name, subscription_id, employee_count, activated_at):
    """Demo mode's stand-in for Razorpay's schedule: charges on the
    company's billing date (utils/billing_cycle.py) when the current period
    isn't paid yet -- never during a trial, and not for a first period the
    signup checkout already paid."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT subscription_status, billing_anchor_date, created_at FROM tenants WHERE db_name=%s",
                (tenant_schema,))
    t = cur.fetchone()
    today = platform_today()
    anchor, explicit = anchor_from(t[1] if t else None, t[2] if t else None, today)
    start = cycle_period_start(anchor, today)
    due = (bool(t) and t[0] != "trialing" and today >= anchor
           and not (start == anchor and first_period_prepaid(anchor, explicit, t[2]))
           and not period_is_paid(cur, tenant_schema, start))
    if not due:
        cur.close()
        conn.close()
        return

    amount_paise = calculate_price(employee_count)
    billing_period = start
    # Each simulated demo charge needs its own unique payment id -- a fixed
    # literal here would collide with idx_monthly_invoices_payment_id
    # (app.py) the very next time ANY tenant's demo subscription renews.
    demo_payment_id = f"demo_payment_{secrets.token_hex(10)}"
    cur.execute(
        "INSERT INTO monthly_invoices (tenant_schema, company_name, employee_count, amount_paise, "
        "razorpay_payment_id, razorpay_subscription_id, status, billing_period, rate_paise) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'paid', %s, %s) RETURNING id",
        (tenant_schema, company_name, employee_count, amount_paise, demo_payment_id, subscription_id,
         billing_period, get_per_employee_paise())
    )
    invoice_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    app_log.info("sync_and_bill_auto_debit: simulated demo monthly charge for %s (%s employees, %s paise)",
                 tenant_schema, employee_count, amount_paise)
    on_payment_completed("invoice", invoice_id)
    from blueprints.seats import reset_paid_seats
    reset_paid_seats(tenant_schema)
