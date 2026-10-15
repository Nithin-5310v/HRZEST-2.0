# -*- coding: utf-8 -*-
"""Billing blueprint -- Razorpay-backed paid signup for the public
/create_org flow (templates/create_org.html).

Flow: an applicant first goes through blueprints/org.py's gated signup
(OTP email verification, KYC document upload, platform-admin review) --
only once an application is approved (status='approved_pending_payment')
does this blueprint come into play. create_order() creates a Razorpay
order + a staging `payment_orders` row (att_master schema) keyed off that
already-approved application; the tenant schema only gets provisioned once
verify_payment() confirms the payment signature, using the
admin_password_hash already stored on the application (set, hashed, at
signup step 1 -- well before payment, once OTP + documents were reviewed)
so there's no separate "set your password" reset-link step needed anymore
for this flow, unlike the old instant-provisioning design this replaced.

The Platform Admin's own "Create a new company" form (blueprints/
platform_admin.py) is untouched and stays free/unmetered -- this billing
flow only gates the public self-service signup path.
"""
import secrets
import datetime
from flask import Blueprint, request, jsonify
from extensions import app_log, limiter, log_security_event
from database import get_master_db
from utils.plan_limits import PLAN_LABEL, calculate_price, format_price_inr
from utils.razorpay_utils import (
    create_order as razorpay_create_order, verify_payment_signature,
    key_id as razorpay_key_id, create_id_or_demo, verify_or_demo,
)
from blueprints.webhooks import register_webhook_handler
billing_bp = Blueprint("billing", __name__)

# Demo/test-mode checkout, used only while RAZORPAY_KEY_ID/SECRET aren't
# configured -- lets the full register -> pay -> provisioned flow be
# exercised end-to-end (e.g. templates/create_org.html's mock checkout
# modal) without real payment credentials. Real Razorpay order IDs are
# always "order_<their own id>" and never carry this prefix, and
# verify_payment() below additionally refuses to honor this prefix at all
# once real keys ARE configured -- so this can never become a bypass in
# production, only a local/demo convenience before Razorpay is wired up.
_DEMO_ORDER_PREFIX = "demo_order_"


@billing_bp.route("/api/billing/create_order", methods=["POST"])
@limiter.limit("10 per minute")
def create_order():
    """Payment now happens AFTER a platform admin approves the applicant's
    gated signup (blueprints/org.py's tenant_applications), not before --
    so this no longer collects fresh company/admin fields from the client
    at all (those were already validated, duplicate-checked, and OTP/KYC
    verified during the application). It only needs application_id
    (identifying an already-approved application) and employee_count
    (the one thing genuinely decided at checkout time).

    Requires proof of the application's access token, exactly like the
    pay page that calls this (blueprints/org.py's
    _verify_application_access -- the page's own check leaves that proof
    in the session), so a sequential application_id alone can neither
    stage an order nor reveal the application's status; 404 otherwise."""
    from blueprints.org import _verify_application_access
    data = request.get_json(silent=True) or request.form
    try:
        application_id = int(data.get("application_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Missing or invalid application_id."}), 400
    if not _verify_application_access(application_id):
        return jsonify({"ok": False, "msg": "Application not found."}), 404

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT company_name, subdomain, admin_username, admin_email, email_domain, logo_path, status "
        "FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    company_name, subdomain, admin_username, admin_email, email_domain, logo_path, status = row
    if status != "approved_pending_payment":
        return jsonify({"ok": False, "msg": "This application isn't ready for payment."}), 400

    try:
        employee_count = int(data.get("employee_count") or 1)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Employee count must be a number."}), 400
    if employee_count < 1:
        return jsonify({"ok": False, "msg": "Employee count must be at least 1."}), 400

    amount_paise = calculate_price(employee_count)

    receipt_id = f"signup_{subdomain}_{int(datetime.datetime.now().timestamp())}"
    order_id, is_demo, error = create_id_or_demo(
        _DEMO_ORDER_PREFIX, lambda: razorpay_create_order(amount_paise, receipt_id)
    )
    if error:
        app_log.error("billing.create_order failed: %s", error)
        return jsonify({"ok": False, "msg": error}), 502

    try:
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO payment_orders (razorpay_order_id, plan, employee_count, amount_paise, "
            "company_name, subdomain, admin_username, admin_email, email_domain, logo_path, application_id, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'created')",
            (order_id, PLAN_LABEL, employee_count, amount_paise, company_name, subdomain, admin_username, admin_email,
             email_domain, logo_path, application_id)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.error("billing.create_order: failed to stage payment_orders row: %s", exc)
        return jsonify({"ok": False, "msg": "Could not start checkout. Please try again."}), 500

    return jsonify({
        "ok": True,
        "order_id": order_id,
        "amount_paise": amount_paise,
        "amount_display": format_price_inr(amount_paise),
        "currency": "INR",
        "key_id": razorpay_key_id(),
        "demo": is_demo,
    })


def _finalize_signup_order(razorpay_order_id, razorpay_payment_id):
    """Look up a signup payment_orders row by order id and, if it's not
    already provisioned, provision the tenant. Shared by verify_payment()
    below (the browser's own post-checkout callback, already signature-
    verified by the time it calls this) and _handle_signup_payment_captured()
    (the server-to-server payment.captured webhook, verified upstream by
    blueprints/webhooks.py's receive_webhook() before dispatch) -- this
    function itself does no signature verification of its own, and must
    only ever be called after the caller has confirmed authenticity by
    whichever means is appropriate to it.

    Returns a plain result dict (not a Flask response) so both callers can
    shape their own reply -- the webhook caller doesn't need to reply to
    Razorpay at all, it just logs the outcome.

    result["status"] is one of:
      "not_found"           -- no payment_orders row for this order id
      "already_provisioned" -- no-op, tenant already exists
      "already_paid"        -- lost the provisioning race to a concurrent
                                call; the winner already handled it
      "provisioning_failed" -- provision_tenant() itself errored
      "provisioned"         -- this call just provisioned it
    """
    from blueprints.org import provision_tenant, send_payment_confirmation_email
    from utils.auth import generate_password_hash
    from utils.helpers import _safe_app_url

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT employee_count, company_name, subdomain, admin_username, admin_email, email_domain, logo_path, "
        "status, application_id "
        "FROM payment_orders WHERE razorpay_order_id=%s",
        (razorpay_order_id,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"status": "not_found"}

    (employee_count, company_name, subdomain, admin_username, admin_email, email_domain, logo_path, status,
     application_id) = row

    # Idempotent: a retried/duplicate call for an already-provisioned order
    # must not attempt to re-provision (the subdomain is now taken, which
    # provision_tenant() would correctly reject as "already taken" -- but
    # there's no reason to even try).
    if status == "provisioned":
        cur.close()
        conn.close()
        portal_url = f"{_safe_app_url()}/{subdomain}/login"  # "/admin_login" hasn't been a real route since an earlier rename
        return {"status": "already_provisioned", "portal_url": portal_url}

    # "AND status != 'paid'" is load-bearing, not decorative -- mirrors
    # seats.py's verify_payment() exactly: two near-simultaneous calls for
    # the same order (a flaky-network client retry, a double-tap, OR now
    # the browser callback racing the payment.captured webhook for the
    # exact same order) must not both pass the status=='provisioned' check
    # above and each go on to call provision_tenant() for the same
    # subdomain. Only the call whose UPDATE actually flips a row
    # (cur.rowcount == 1) gets to provision below; the loser re-reads the
    # now-committed status and returns the same idempotent "already
    # provisioned"/"already paid" outcome a retried call expects, instead
    # of racing into provision_tenant()'s own subdomain-taken rejection.
    cur.execute(
        "UPDATE payment_orders SET status='paid', razorpay_payment_id=%s, paid_at=NOW() "
        "WHERE razorpay_order_id=%s AND status != 'paid'",
        (razorpay_payment_id, razorpay_order_id)
    )
    won_race = cur.rowcount == 1
    conn.commit()
    cur.close()
    conn.close()

    if not won_race:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT status FROM payment_orders WHERE razorpay_order_id=%s", (razorpay_order_id,))
        _row = cur.fetchone()
        cur.close()
        conn.close()
        if _row and _row[0] == "provisioned":
            portal_url = f"{_safe_app_url()}/{subdomain}/login"
            return {"status": "already_provisioned", "portal_url": portal_url}
        return {"status": "already_paid"}

    # The applicant already set (and it was hashed at) their real admin
    # password during the gated signup application -- OTP-verified and
    # KYC-document-reviewed well before payment ever happens now, unlike
    # the old flow where payment was the very first step and no password
    # had been collected yet. So there's no more random-password +
    # "set your password" reset-link dance needed: provision_tenant() just
    # gets the hash that's already on file.
    admin_password_hash = None
    gst_number = None
    if application_id:
        aconn = get_master_db()
        acur = aconn.cursor(buffered=True)
        acur.execute("SELECT admin_password_hash, gst_number FROM tenant_applications WHERE id=%s", (application_id,))
        arow = acur.fetchone()
        acur.close()
        aconn.close()
        admin_password_hash = arow[0] if arow else None
        gst_number = arow[1] if arow else None
    if not admin_password_hash:
        # Defensive fallback for a payment_orders row with no linked
        # application (shouldn't happen via create_order() above, which
        # now requires one) -- still provisions correctly with a random
        # password the admin can recover via the normal forgot-password flow.
        app_log.warning("billing.verify_payment: order %s has no linked application; using a random password", razorpay_order_id)
        admin_password_hash = generate_password_hash(secrets.token_urlsafe(24))

    ok, error, portal_url, checkin_url = provision_tenant(
        company_name, subdomain, admin_username, admin_password_hash, admin_email,
        email_domain=email_domain, employee_count=employee_count, logo_path=logo_path, gst_number=gst_number,
    )
    if not ok:
        app_log.error("billing.verify_payment: provisioning failed for order %s: %s", razorpay_order_id, error)
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute("UPDATE payment_orders SET status='failed' WHERE razorpay_order_id=%s", (razorpay_order_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "provisioning_failed", "error": error}

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT id, amount_paise FROM payment_orders WHERE razorpay_order_id=%s", (razorpay_order_id,))
    tenant_row = None
    order_row = cur.fetchone()
    if order_row:
        cur.execute("SELECT id FROM tenants WHERE subdomain=%s", (subdomain,))
        tenant_row = cur.fetchone()
    amount_paise = order_row[1] if order_row else 0
    cur.execute(
        "UPDATE payment_orders SET status='provisioned', tenant_id=%s WHERE razorpay_order_id=%s",
        (tenant_row[0] if tenant_row else None, razorpay_order_id)
    )
    if application_id and tenant_row:
        cur.execute(
            "UPDATE tenant_applications SET status='provisioned', tenant_id=%s, updated_at=NOW() WHERE id=%s",
            (tenant_row[0], application_id)
        )
    conn.commit()
    cur.close()
    conn.close()

    send_payment_confirmation_email(
        admin_email, company_name, portal_url,
        employee_count, amount_paise, razorpay_payment_id, checkin_url=checkin_url,
    )

    log_security_event(
        "billing.tenant_provisioned", f"Paid tenant '{company_name}' provisioned via Razorpay",
        level="INFO", subdomain=subdomain, employee_count=employee_count, order_id=razorpay_order_id,
    )

    return {
        "status": "provisioned", "portal_url": portal_url,
        "employee_count": employee_count, "amount_display": format_price_inr(amount_paise),
    }


@billing_bp.route("/api/billing/verify_payment", methods=["POST"])
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
            "billing.demo_checkout_completed", "Demo/test-mode checkout completed (no real payment)",
            level="INFO", order_id=razorpay_order_id,
        )
    elif not ok:
        if not is_demo:
            log_security_event(
                "billing.signature_invalid", "Razorpay payment signature verification failed",
                level="ERROR", order_id=razorpay_order_id,
            )
        return jsonify({"ok": False, "msg": error}), 400

    result = _finalize_signup_order(razorpay_order_id, razorpay_payment_id)
    st = result["status"]
    if st == "not_found":
        return jsonify({"ok": False, "msg": "Unknown order."}), 404
    if st == "already_provisioned":
        return jsonify({"ok": True, "portal_url": result["portal_url"], "already_provisioned": True})
    if st == "already_paid":
        return jsonify({"ok": True, "already_paid": True})
    if st == "provisioning_failed":
        return jsonify({
            "ok": False,
            "msg": f"Payment received, but provisioning failed: {result['error']}. "
                   f"Contact support with order ID {razorpay_order_id}.",
        }), 500
    # "provisioned"
    return jsonify({
        "ok": True, "portal_url": result["portal_url"],
        "employee_count": result["employee_count"], "amount_display": result["amount_display"],
    })


def _handle_signup_payment_captured(payload):
    """blueprints/webhooks.py dispatches here for every authentic
    payment.captured event -- including ones for an overdue-dunning
    repayment (blueprints/billing_dunning.py) or a seat top-up
    (blueprints/seats.py), which simply won't match any row in
    payment_orders and no-op harmlessly (separate order-id spaces),
    same pattern as billing_dunning.py's own handler for this event.

    This is the server-side safety net for signup checkout: verify_payment()
    above depends entirely on the browser's own post-checkout JS callback
    reaching this server, which can fail to fire (closed tab, network
    blip, ad-blocker) even though Razorpay itself successfully captured
    the payment -- leaving payment_orders stuck at status='created'
    forever with nothing else to notice. This webhook means the payment
    gets finalized (and the tenant provisioned) even if that callback
    never arrives."""
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = payment_entity.get("order_id")
    payment_id = payment_entity.get("id")
    if not order_id:
        return
    result = _finalize_signup_order(order_id, payment_id)
    if result["status"] == "provisioning_failed":
        app_log.error(
            "billing.webhook: payment.captured for order %s verified but provisioning failed: %s",
            order_id, result.get("error"),
        )
    elif result["status"] == "provisioned":
        log_security_event(
            "billing.tenant_provisioned_via_webhook",
            f"Signup order {order_id} provisioned via payment.captured webhook "
            "(browser callback never reached the server)",
            level="WARNING", order_id=order_id,
        )


register_webhook_handler("razorpay", "payment.captured", _handle_signup_payment_captured)
