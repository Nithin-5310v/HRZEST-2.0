# -*- coding: utf-8 -*-
"""Billing emails a paid company's admins get outside the dunning cycle
(blueprints/billing_dunning.py) and the trial timeline (blueprints/
trial_billing.py):

  notify_employee_added()   every time an employee is added -- the new
                            headcount and what the next bill comes to
  send_renewal_reminders()  daily job: RENEWAL_REMINDER_DAYS before a paid
                            month renews -- once per company per renewal

"Paid" means billed every month: payment_option 'online' (auto-debit) or
'manual', and not on a free trial. Bills follow calendar months
(billing_dunning.py's billing_period), so a month renews on the 1st.
"""
import datetime
import html
import os

from flask import g as _g

from extensions import app, app_log
from database import get_master_db
from utils.helpers import platform_today
from utils.plan_limits import calculate_price, format_price_inr, get_tenant_employee_count
from utils.email_utils import get_email_config, get_billing_emails, send_email_async

RENEWAL_REMINDER_DAYS = 3


def email_shell(body, heading="Billing notice"):
    """The HRzest billing-email frame every billing notice shares."""
    return f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">HRzest.com</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">{heading}</div>
  </div>
  <div style="padding:28px;font-size:14px;color:#1e293b;line-height:1.6;">{body}</div>
</div>"""


def company_link(slug, path):
    """<APP_URL>/<slug><path> -- works from a scheduled job too (no request)."""
    base = (os.environ.get("APP_URL") or "https://hrzest.com").strip().rstrip("/")
    return f"{base}/{slug}{path}" if slug else f"{base}{path}"


def next_renewal_date(today=None):
    """The 1st of next month: when the current monthly bill renews."""
    today = today or platform_today()
    return (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)


def billing_profile(db_name):
    """What the billing emails need about one company, or None when it
    isn't billed monthly (still on a trial, free, suspended or unknown)."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    try:
        cur.execute(
            "SELECT company_name, subdomain, payment_option, subscription_status, status "
            "FROM tenants WHERE db_name=%s", (db_name,)
        )
        row = cur.fetchone()
        if not row:
            return None
        company_name, slug, payment_option, subscription_status, status = row
        if status != "active" or payment_option not in ("online", "manual") or subscription_status == "trialing":
            return None
        cur.execute("SELECT status, quantity_synced FROM auto_debit_mandates WHERE tenant_schema=%s", (db_name,))
        mandate = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    employees = get_tenant_employee_count(db_name)
    return {
        "company_name": company_name, "slug": slug,
        "auto_debit": bool(mandate) and mandate[0] == "active",
        "auto_debit_employees": mandate[1] if mandate else None,
        "employees": employees, "amount_paise": calculate_price(employees),
    }


def _send_to_admins(db_name, subject, body, heading="Billing notice"):
    """Sends to every admin email in db_name's schema -- switches
    g.tenant_db for the send, like billing_dunning.py's _notify_tenant()."""
    prev_tenant_db = getattr(_g, "tenant_db", None)
    try:
        _g.tenant_db = db_name
        config = get_email_config()
        if not config:
            return
        html_body = email_shell(body, heading)
        for email in get_billing_emails():
            send_email_async(email, subject, html_body, config)
    finally:
        _g.tenant_db = prev_tenant_db


def _payment_sentence(profile, renews_on):
    date_str = renews_on.strftime("%d %b %Y")
    if profile["auto_debit"]:
        return (f"Auto-Debit is on, so this is charged automatically on <strong>{date_str}</strong> "
                f"-- nothing to do.")
    link = company_link(profile["slug"], "/settings?tab=billing")
    return (f"Pay it by <strong>{date_str}</strong>, or switch on Auto-Debit so it's paid automatically: "
            f"<a href=\"{link}\">Settings &rarr; Billing</a>.")


def notify_employee_added(added=1, db_name=None):
    """Emails a paid company's admins that its headcount, and so its next
    bill, went up. Called after an employee is created; never raises -- the
    employee is already saved."""
    db_name = db_name or getattr(_g, "tenant_db", None)
    try:
        profile = billing_profile(db_name) if db_name else None
        if not profile:
            return
        renews_on = next_renewal_date()
        company = html.escape(profile["company_name"] or "")
        noun = "employee was" if added == 1 else "employees were"
        billing = company_link(profile["slug"], "/settings?tab=billing")
        if profile["auto_debit"]:
            set_for = profile["auto_debit_employees"]
            choice = (
                f"<p><strong>Update your Auto-Debit now</strong> so it charges for all "
                f"{profile['employees']} employee(s)"
                + (f" (it's set for {set_for})" if set_for is not None else "")
                + f": <a href=\"{billing}\">Settings &rarr; Billing &rarr; Update</a>. "
                f"If you don't, it updates itself before the next charge.</p>"
            )
        else:
            choice = (
                f"<p><strong>Switch on Auto-Debit</strong> so the new amount is paid automatically every "
                f"month: <a href=\"{billing}\">Settings &rarr; Billing</a>.</p>"
            )
        body = (
            f"<p>{added} {noun} added to <strong>{company}</strong>.</p>"
            f"<p>You now have <strong>{profile['employees']} employee(s)</strong>, so your monthly bill "
            f"from <strong>{renews_on.strftime('%d %b %Y')}</strong> is "
            f"<strong>{format_price_inr(profile['amount_paise'])}</strong>.</p>"
            f"{choice}"
            f"<p><strong>Adding several people?</strong> Pay for all of them in one go by buying seats "
            f"from the same <a href=\"{billing}\">Billing</a> page.</p>"
        )
        _send_to_admins(db_name, "Your HRzest bill changed: employees added", body, "Plan update")
    except Exception:
        app_log.exception(f"billing_notices: employee-added email failed for '{db_name}'")


def send_renewal_reminders():
    """APScheduler job (wsgi.py), daily: RENEWAL_REMINDER_DAYS before the 1st,
    reminds each paid company's admins what renews and for how much.
    tenants.renewal_reminder_for (the renewal date reminded about) keeps it
    to one email per renewal even if the job runs twice."""
    try:
        with app.app_context():
            _run_renewal_reminders()
    except Exception:
        app_log.exception("billing_notices: renewal reminder job failed")


def _run_renewal_reminders(today=None):
    today = today or platform_today()
    renews_on = next_renewal_date(today)
    if (renews_on - today).days != RENEWAL_REMINDER_DAYS:
        return
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, db_name FROM tenants WHERE status='active' AND payment_option IN ('online','manual') "
        "AND subscription_status != 'trialing' AND schema_missing_tables IS NULL "
        "AND (renewal_reminder_for IS NULL OR renewal_reminder_for < %s)",
        (renews_on,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for tenant_id, db_name in rows:
        try:
            _remind_one(tenant_id, db_name, renews_on)
        except Exception:
            app_log.exception(f"billing_notices: renewal reminder failed for '{db_name}'")


def _remind_one(tenant_id, db_name, renews_on):
    profile = billing_profile(db_name)
    if not profile:
        return
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute("UPDATE tenants SET renewal_reminder_for=%s WHERE id=%s", (renews_on, tenant_id))
    conn.commit()
    cur.close()
    conn.close()
    company = html.escape(profile["company_name"] or "")
    body = (
        f"<p><strong>{company}</strong>'s HRzest plan renews on "
        f"<strong>{renews_on.strftime('%d %b %Y')}</strong>.</p>"
        f"<p>Next month's bill: <strong>{format_price_inr(profile['amount_paise'])}</strong> for "
        f"{profile['employees']} employee(s).</p>"
        f"<p>{_payment_sentence(profile, renews_on)}</p>"
        f"<p>If it isn't paid, admins and HR lose access to attendance, payslips and reports after a "
        f"5-day grace period. Employees can still mark attendance.</p>"
    )
    _send_to_admins(db_name, f"Your HRzest plan renews on {renews_on.strftime('%d %b')}", body, "Renewal reminder")
