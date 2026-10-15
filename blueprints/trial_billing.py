# -*- coding: utf-8 -*-
"""Trial-to-paid conversion -- the daily jobs that watch a tenant's free
trial (blueprints/org.py's create_org_setup_trial_confirm(), which sets
tenants.subscription_status='trialing' at trial signup; trial_start_date/
trial_end_date are left NULL until first login -- see app.py's
inject_billing_lock_status()) and hand it off to the ordinary billing
machinery every other tenant already uses once it ends.

Distinct from blueprints/auto_debit.py (the recurring engine itself) and
blueprints/billing_dunning.py (the grace/lock cycle for a missed bill) --
this module only owns the trial-specific timeline: the "ending soon"
reminder and the one-time "trial is over" conversion. Once a trial
converts, it becomes an indistinguishable auto_debit_mandates subscriber,
and (since payment_option is flipped to 'online' below) comes under
billing_dunning.py's daily cron for every bill after that -- no separate
trial-specific dunning path is needed.
"""
from extensions import app, app_log, log_security_event
from database import get_master_db
from utils.plan_limits import get_tenant_employee_count, get_per_employee_paise, format_price_inr

# Trial length, in days, from first login (not from approval/provisioning --
# see app.py's inject_billing_lock_status(), which is what actually stamps
# trial_start_date/trial_end_date the first time an admin session for this
# tenant is seen). Named here (not inlined) since it's referenced from both
# that first-login stamp and check_trial_ending_soon()'s reminder window
# below, and from user-facing copy in templates/create_org_setup_trial.html
# and templates/create_org_status.html.
TRIAL_DURATION_DAYS = 2

# How long before trial_end_date the one-time "ending soon" reminder fires.
TRIAL_REMINDER_WINDOW_HOURS = 24


def check_trial_ending_soon():
    """APScheduler job (registered in wsgi.py) -- sends the one-time
    "your trial ends soon" reminder, TRIAL_REMINDER_WINDOW_HOURS before
    trial_end_date. Separate job (not folded into check_trial_expirations()
    above) since the two fire on genuinely different conditions and this
    one must never re-fire for the same tenant -- trial_reminder_sent_at
    is the dedup guard."""
    try:
        with app.app_context():
            _run_trial_reminder_check()
    except Exception:
        app_log.exception("trial_billing: unhandled error in ending-soon check")


def _run_trial_reminder_check():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, company_name, db_name FROM tenants "
        "WHERE subscription_status='trialing' AND trial_reminder_sent_at IS NULL "
        "AND schema_missing_tables IS NULL "  # its admins would be read from public (QA SEC-07)
        "AND trial_end_date IS NOT NULL AND trial_end_date <= NOW() + (%s * INTERVAL '1 hour')",
        (TRIAL_REMINDER_WINDOW_HOURS,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for tenant_id, company_name, db_name in rows:
        try:
            _send_trial_reminder(tenant_id, company_name, db_name)
        except Exception:
            app_log.exception(f"trial_billing: ending-soon reminder failed for tenant '{db_name}'")


def _send_trial_reminder(tenant_id, company_name, db_name):
    # Marked sent BEFORE the actual send (not after) -- same "never double-
    # send" posture as every other dedup-guarded notification in this app;
    # a crash mid-send is far less costly than a duplicate reminder email
    # on the next cron pass.
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute("UPDATE tenants SET trial_reminder_sent_at=NOW() WHERE id=%s", (tenant_id,))
    conn.commit()
    cur.close()
    conn.close()
    _notify_trial_tenant(db_name, company_name, "ending_soon")


def check_trial_expirations():
    """APScheduler job (registered in wsgi.py), no request/app context of
    its own -- same app.app_context() requirement as blueprints/
    billing_dunning.py's check_tenant_billing()."""
    try:
        with app.app_context():
            _run_trial_check()
    except Exception:
        app_log.exception("trial_billing: unhandled error in daily check")


def _run_trial_check():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, company_name, db_name FROM tenants "
        "WHERE subscription_status='trialing' AND trial_end_date <= NOW()"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for tenant_id, company_name, db_name in rows:
        try:
            _convert_one_trial(tenant_id, company_name, db_name)
        except Exception:
            app_log.exception(f"trial_billing: conversion failed for tenant '{db_name}'")


def _convert_one_trial(tenant_id, company_name, db_name):
    from blueprints.auto_debit import _sync_mandate_quantity

    # Recompute fresh -- never reuse a stale count, same requirement
    # billing_dunning.py's own cycle already honors.
    employee_count = get_tenant_employee_count(db_name)

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT razorpay_subscription_id, quantity_synced, status FROM auto_debit_mandates WHERE tenant_schema=%s",
        (db_name,)
    )
    mandate = cur.fetchone()
    cur.close()
    conn.close()

    if not mandate or mandate[2] != "active":
        # Shouldn't happen via the self-serve trial signup (which always
        # authorizes a mandate before provisioning), but the Platform
        # Admin "Free Signup" panel can still create a payment_option=
        # 'trial' tenant with no mandate at all -- degrade safely instead
        # of crashing: flag it so the admin can add a payment method, no
        # charge is ever silently skipped or fabricated.
        _set_subscription_status(tenant_id, "past_due")
        log_security_event(
            "trial_billing.no_mandate_at_expiry",
            f"Trial ended for '{db_name}' with no payment mandate on file",
            level="WARNING", tenant_id=tenant_id,
        )
        _notify_trial_tenant(db_name, company_name, "no_mandate")
        return

    subscription_id, quantity_synced, _status = mandate
    _sync_mandate_quantity(db_name, subscription_id, quantity_synced, employee_count, is_demo_sub=subscription_id.startswith("demo_sub_"))

    # Mandate is authorized and quantity is synced -- actual charge
    # success/failure is reported asynchronously via the existing
    # subscription.charged / payment.failed webhooks (blueprints/
    # auto_debit.py). Flipping payment_option to 'online' here is what
    # brings this tenant into blueprints/billing_dunning.py's existing
    # daily cron for every billing cycle from now on -- 'trial' is purely
    # a signup-time label, not a permanent billing category.
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE tenants SET subscription_status='active', payment_option='online' WHERE id=%s",
        (tenant_id,)
    )
    conn.commit()
    cur.close()
    conn.close()

    log_security_event(
        "trial_billing.converted", f"Trial converted to paid for '{db_name}' ({employee_count} employees)",
        level="INFO", tenant_id=tenant_id,
    )
    _notify_trial_tenant(db_name, company_name, "converted", employee_count=employee_count)


def _set_subscription_status(tenant_id, status):
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute("UPDATE tenants SET subscription_status=%s WHERE id=%s", (status, tenant_id))
    conn.commit()
    cur.close()
    conn.close()


def _notify_trial_tenant(tenant_schema, company_name, kind, employee_count=None):
    """Switches g.tenant_db to the target tenant's schema for the duration
    of the send -- same pattern as blueprints/billing_dunning.py's
    _notify_tenant() / blueprints/auto_debit.py's _notify_rate_change()."""
    from flask import g as _g
    from utils.email_utils import get_email_config, get_admin_emails, send_email_async
    prev_tenant_db = getattr(_g, "tenant_db", None)
    try:
        _g.tenant_db = tenant_schema
        config = get_email_config()
        if not config:
            return
        recipients = get_admin_emails()
        if not recipients:
            return
        if kind == "converted":
            amount_display = format_price_inr(employee_count * get_per_employee_paise())
            subject = "Your HRzest free trial has ended"
            body = (
                f"<p>Your {TRIAL_DURATION_DAYS}-day free trial for <strong>{company_name}</strong> has ended.</p>"
                f"<p>Billing is now active: approximately <strong>{amount_display}/month</strong> for "
                f"{employee_count} employee(s) at the current rate, charged automatically going forward. "
                f"Your billing history is always visible from <strong>Settings &rarr; Finances &rarr; Billing</strong>.</p>"
            )
        elif kind == "ending_soon":
            subject = f"Your HRzest trial ends in about {TRIAL_REMINDER_WINDOW_HOURS} hours"
            body = (
                f"<p>Your free trial for <strong>{company_name}</strong> ends in about "
                f"{TRIAL_REMINDER_WINDOW_HOURS} hours.</p>"
                f"<p>Billing will start automatically using the payment method already on file -- no action "
                f"needed to continue. If you'd rather not continue, you can cancel any time before then from "
                f"<strong>Settings &rarr; Finances &rarr; Billing</strong>.</p>"
            )
        else:  # no_mandate
            subject = "Action needed: your HRzest trial has ended"
            body = (
                f"<p>Your {TRIAL_DURATION_DAYS}-day free trial for <strong>{company_name}</strong> has ended, "
                f"but we don't have a payment method on file.</p>"
                f"<p>Log in and add one from <strong>Settings &rarr; Finances &rarr; Billing</strong> to keep your "
                f"account active.</p>"
            )
        html = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">HRzest.com</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">Billing notice</div>
  </div>
  <div style="padding:28px;font-size:14px;color:#1e293b;line-height:1.6;">{body}</div>
</div>"""
        for email in recipients:
            send_email_async(email, subject, html, config)
    except Exception:
        app_log.exception(f"trial_billing: notify failed for '{tenant_schema}' ({kind})")
    finally:
        _g.tenant_db = prev_tenant_db
