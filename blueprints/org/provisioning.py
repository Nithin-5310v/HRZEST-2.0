# -*- coding: utf-8 -*-
"""org: tenant provisioning and portal-ready / payment-confirmation emails"""
from extensions import app_log
from utils.email_utils import get_email_config, send_email_async
from utils.helpers import _safe_app_url, clean_email_domain
from utils.plan_limits import PLAN_LABEL, format_price_inr, get_per_employee_paise


_PAYMENT_OPTIONS = frozenset({"online", "manual", "trial"})


def provision_tenant(company_name, subdomain, admin_username, admin_password_hash, admin_email,
                     payment_option="online", email_domain=None, employee_count=None, logo_path=None,
                     gst_number=None, subscription_status=None, contact_phone=None):
    """Shared tenant-provisioning core: schema creation, admin-user seed,
    and master-registry insert. Callers must run
    _validate_new_tenant_fields() first -- this only does the actual
    provisioning, which is where the three call sites would otherwise
    duplicate ~80 near-identical lines.

    admin_password_hash is an ALREADY-HASHED password (bcrypt, via
    utils/auth.py's generate_password_hash()) -- this function used to
    accept and hash the plaintext itself, but callers now hash one level
    up. That's because the gated signup flow (blueprints/org.py's
    /create_org*) stores the application in tenant_applications for a
    possibly multi-day pending-review window before provision_tenant() is
    ever called, and the password must never sit there in plaintext.

    payment_option is a record of how the tenant is billed ("online" via
    Razorpay, "manual" bank-transfer/invoice, or "trial") -- it doesn't
    trigger any charge itself, that already happened (or didn't, for
    "manual"/"trial") before this function is called.

    email_domain (e.g. "acme.com") is stored on the new tenant's own
    company_settings row -- utils/helpers.py's validate_employee_email_domain()
    reads it from there to require/check new employees' emails going
    forward. NOT written onto the master tenants row here (see the
    comment on that INSERT below) -- blueprints/oauth_signup.py sets that
    copy itself, directly, only for the tenant it just created.

    logo_path is a relative static/ path (e.g. "company_logos/acme.png",
    as returned by utils/helpers.py's save_uploaded_logo()) for the
    company logo collected on the registration form -- written to
    company_settings.logo_url so it's already in place the first time
    anyone (employee, admin, or HR) opens a dashboard. None/omitted means
    no logo was uploaded at signup; the default "no logo" rendering
    (templates already fall back to a generic building icon) applies.

    employee_count is the number of seats actually paid for -- written to
    company_settings.paid_employee_slots (a column that already existed in
    the schema migrations but was never populated or enforced anywhere;
    see utils/helpers.py's add_employee_seat_cap_check() for where it's
    checked). None means unlimited -- used by the free/unmetered callers
    (the local-dev fallback POST /create_org, the mobile app's own
    /api/create_org registration flow, and Platform Admin's own tenant
    creation), which intentionally don't gate on payment.

    gst_number (already format-validated and normalized by the caller --
    see _clean_gst()/_validate_gst_number()) is written onto the new
    tenants row so check_duplicate_gst() can block a second signup under
    the same real-world business later. None for the Platform Admin
    direct-create path, which doesn't collect one.

    contact_phone (the admin's mobile number from the web signup) goes to
    company_settings.contact_phone; None leaves it unset.

    subscription_status overrides the default "'trialing' iff
    payment_option=='trial', else 'active'" rule -- explicit None (the
    default) preserves that original rule for every existing caller
    (billing.py's paid-verified provisioning, Platform Admin's own
    always-free direct-create tool). Pass 'trialing' explicitly to force
    trial-lifecycle status (the 2-employee cap, the 2-day expiry cron)
    regardless of payment_option -- used by org.py's self-serve
    create_org_verify_otp(), since every self-serve lane now provisions
    before any payment is collected, so "online"/"manual" no longer imply
    "already paid" the way they did when provisioning happened after
    checkout. Deliberately NOT the new default for every caller: Platform
    Admin's own tenant-creation tool is an intentionally-free/unmetered
    override with paid_employee_slots=None (unlimited) already, and
    _tenant_is_trialing() checks subscription_status before
    paid_employee_slots in add_employee_seat_cap_check() -- forcing it
    trialing there would wrongly cap an "unlimited" tenant at 2 employees.

    Returns (ok, error_message_or_None, portal_url_or_None, checkin_url_or_None).
    """
    if payment_option not in _PAYMENT_OPTIONS:
        payment_option = "online"
    db_name = "att_" + subdomain.replace("-", "_")

    # See the long comment this replaced in the original /create_org route:
    # checking only the tenants registry misses (1) a subdomain deriving a
    # db_name that collides with an existing schema (e.g. "master"), and
    # (2) a schema orphaned by a previously failed provisioning attempt.
    try:
        from database import get_master_db
        mconn = get_master_db()
        mcur = mconn.cursor(buffered=True)
        mcur.execute("SELECT id FROM tenants WHERE subdomain=%s", (subdomain,))
        taken = mcur.fetchone() is not None
        if not taken:
            mcur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (db_name,))
            taken = mcur.fetchone() is not None
        mcur.close()
        mconn.close()
        if taken:
            return False, f"Subdomain '{subdomain}' is already taken. Choose another.", None, None
    except Exception as exc:
        app_log.error("provision_tenant subdomain check failed: %s", exc)
        return False, "Could not check subdomain availability. Please try again.", None, None

    try:
        from database import create_tenant_schema
        create_tenant_schema(db_name)
    except Exception as exc:
        app_log.error("provision_tenant schema creation failed: %s", exc)
        return False, "Failed to create organisation. Please contact support.", None, None

    try:
        from flask import g as _g
        _g.tenant_db = db_name
        from app import init_tenant_db
        init_tenant_db(db_name)
    except Exception as exc:
        app_log.error("provision_tenant schema init failed: %s", exc)
        return False, "Failed to initialise organisation schema. Please contact support.", None, None

    try:
        from database import get_tenant_db
        tconn = get_tenant_db(db_name)
        tcur = tconn.cursor()
        # save_uploaded_logo() (utils/helpers.py) returns a bare relative
        # path, local-disk only.
        if not logo_path:
            logo_url = None
        elif logo_path.startswith("http://") or logo_path.startswith("https://"):
            logo_url = logo_path
        else:
            logo_url = f"/static/{logo_path}"
        tcur.execute(
            "UPDATE company_settings SET company_name=%s, email_domain=%s, paid_employee_slots=%s, "
            "logo_url=COALESCE(%s, logo_url), contact_phone=COALESCE(%s, contact_phone), setup_done=1, "
            "org_profile_pending=1 WHERE id=1",
            (company_name, clean_email_domain(email_domain) or None, employee_count, logo_url, contact_phone)
        )
        # Plain INSERT, no ON CONFLICT: the schema-existence check above
        # guarantees this is a brand-new schema, so a conflict here means a
        # genuine race rather than legitimate reuse.
        tcur.execute(
            "INSERT INTO admin_users (username, password, email) VALUES (%s, %s, %s)",
            (admin_username, admin_password_hash, admin_email)
        )
        tconn.commit()
        tcur.close()
        tconn.close()
    except Exception as exc:
        return False, f"Failed to seed tenant data: {exc}", None, None

    try:
        from database import get_master_db
        mconn = get_master_db()
        mcur = mconn.cursor()
        _effective_subscription_status = (
            subscription_status if subscription_status is not None
            else ("trialing" if payment_option == "trial" else "active")
        )
        # NOTE: the master tenants.email_domain column (migrations/master.py)
        # is deliberately NOT populated here. It exists only for
        # blueprints/oauth_signup.py's "one free trial per company domain"
        # check, enforced by a partial unique index -- writing it
        # unconditionally for every caller of this shared function used to
        # break any two tenants sharing an email_domain (e.g. two entirely
        # unrelated companies both using "test.local" in this test suite,
        # or in practice any placeholder/shared domain) with a raw
        # constraint-violation error on the SECOND provision_tenant() call.
        # blueprints/oauth_signup.py sets this column itself, directly,
        # only on the tenant row it just created.
        if _effective_subscription_status == "trialing":
            # trial_start_date/trial_end_date are deliberately left NULL
            # here -- the trial clock starts at the tenant's FIRST admin
            # login (app.py's inject_billing_lock_status() stamps both
            # columns then, once, using
            # blueprints/trial_billing.TRIAL_DURATION_DAYS), not at
            # provisioning, so a company that gets provisioned but
            # doesn't log in for a few days isn't silently burning trial
            # time it never used. A tenant that never sets up a mandate
            # before its trial clock runs out safely degrades to
            # subscription_status='past_due' rather than crashing or
            # silently staying free forever -- see
            # blueprints/trial_billing.py's _convert_one_trial().
            mcur.execute(
                "INSERT INTO tenants (company_name, subdomain, db_name, admin_email, plan, payment_option, "
                "status, subscription_status, gst_number) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'active', 'trialing', %s)",
                (company_name, subdomain, db_name, admin_email, PLAN_LABEL, payment_option, gst_number)
            )
        else:
            mcur.execute(
                "INSERT INTO tenants (company_name, subdomain, db_name, admin_email, plan, payment_option, "
                "status, gst_number) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'active', %s)",
                (company_name, subdomain, db_name, admin_email, PLAN_LABEL, payment_option, gst_number)
            )
        mconn.commit()
        mcur.close()
        mconn.close()
    except Exception as exc:
        return False, f"Tenant registered in DB but master registry failed: {exc}", None, None

    # NOTE: the live tenant-admin login route is "/login" (auth.py's
    # admin_login() view) -- "/admin_login" hasn't been a real route since
    # an earlier rename and 404s. Fixed here since this exact line already
    # needed touching for the path-based URL migration; not otherwise
    # in scope for this change.
    #
    # _safe_app_url() instead of a hardcoded apex domain: the old
    # "https://www.hrzest.com/..." constant meant every successful signup
    # (once /api/create_org actually returns portal_url -- see that route)
    # would redirect the browser to a real external domain instead of
    # wherever this app is actually running (localhost in dev, a staging
    # host, etc).
    portal_url = f"{_safe_app_url()}/{subdomain}/login"
    # Public, unauthenticated kiosk scanner for this company (blueprints/
    # core.py's checkin_page()) -- separate from portal_url above, which is
    # the admin login. Employees mark attendance here without ever logging
    # into a dashboard first.
    checkin_url = f"{_safe_app_url()}/{subdomain}/checkin"
    return True, None, portal_url, checkin_url


def send_portal_ready_email(admin_email, company_name, admin_username, portal_url, admin_password="", checkin_url=None):  # nosec B107
    """Welcome email with the tenant's dedicated portal link and login credentials.

    checkin_url is the company's own public attendance-scanner page
    (blueprints/core.py's checkin_page()) -- included so whoever registered
    the company can immediately hand it to employees / post it at the
    office without first finding it themselves in Settings."""
    try:
        email_cfg = get_email_config()
        if not email_cfg:
            return False

        creds_block = ""
        if admin_password:
            creds_block = f"""
            <div style="background:#f1f5f9;border-left:4px solid #3b82f6;padding:14px;border-radius:6px;margin:18px 0;font-size:14px;color:#334155;">
              <div style="font-weight:700;margin-bottom:6px;color:#1e293b;">🔑 Login Credentials</div>
              <div><strong>Username:</strong> {admin_username}</div>
              <div><strong>Password:</strong> {admin_password}</div>
            </div>
            """
        else:
            creds_block = f"""
            <div style="background:#f1f5f9;border-left:4px solid #3b82f6;padding:14px;border-radius:6px;margin:18px 0;font-size:14px;color:#334155;">
              <div><strong>Admin Username:</strong> {admin_username}</div>
            </div>
            """

        checkin_block = ""
        if checkin_url:
            checkin_block = f"""
            <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:14px;border-radius:6px;margin:18px 0;font-size:13px;color:#166534;">
              <div style="font-weight:700;margin-bottom:6px;">&#128205; Employee Check-In Page</div>
              <div>Share this link with your employees, or post it as a QR code at the office -- no login required to mark attendance:</div>
              <div style="margin-top:8px;"><a href="{checkin_url}" style="color:#15803d;font-weight:700;">{checkin_url}</a></div>
            </div>
            """

        html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:540px;margin:auto;background:#ffffff;border-radius:16px;
     overflow:hidden;border:1px solid #e2e8f0;box-shadow:0 10px 30px rgba(0,0,0,0.08);">
  <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 100%);padding:28px;color:white;text-align:center;">
    <div style="font-size:24px;font-weight:800;">🏢 {company_name}</div>
    <div style="font-size:14px;opacity:0.85;margin-top:6px;">Your Dedicated HRzest.com Portal is Ready</div>
  </div>
  <div style="padding:28px;">
    <p style="font-size:15px;color:#334155;margin-bottom:16px;line-height:1.6;">
      Welcome to HRzest.com! Your company workspace has been fully configured and set up by our onboarding team.
    </p>

    {creds_block}

    <a href="{portal_url}" style="display:block;text-align:center;padding:16px 28px;
       background:linear-gradient(135deg,#0284c7,#2563eb);color:white;border-radius:12px;text-decoration:none;
       font-size:16px;font-weight:700;margin:24px 0;box-shadow:0 6px 20px rgba(37,99,235,0.35);">
      🚀 Launch Your Branded Dashboard
    </a>

    <p style="font-size:12px;color:#94a3b8;text-align:center;">Or copy this link to your browser:
      <br><a href="{portal_url}" style="color:#2563eb;">{portal_url}</a></p>

    {checkin_block}
  </div>
</div>"""
        send_email_async(admin_email, f"Your HRzest.com Portal is Ready -- {company_name}", html_body, email_cfg)
        return True
    except Exception as exc:
        app_log.error("send_portal_ready_email failed: %s", exc)
        return False


def send_payment_confirmation_email(admin_email, company_name, portal_url,
                                    employee_count, amount_paise, razorpay_payment_id, checkin_url=None):
    """Post-payment welcome email for the paid /create_org flow
    (blueprints/billing.py's verify_payment). The admin logs in with the
    password they already set during the gated signup application (well
    before payment, once OTP + documents were reviewed) -- unlike the old
    flow, there's no "set your password" link needed here anymore, just a
    portal link and a payment receipt summary. Never raises; billing.py
    doesn't gate the API response on this succeeding, same best-effort
    contract as send_portal_ready_email.

    checkin_url is the company's own public attendance-scanner page, same
    as send_portal_ready_email's -- included here too so a paid signup
    gets it just as readily as a manually-provisioned one."""
    try:
        email_cfg = get_email_config()
        if not email_cfg:
            return False
        import datetime as _dt
        amount_display = format_price_inr(amount_paise)
        plan_display_name = f"₹{get_per_employee_paise() // 100}/employee × {employee_count}"
        paid_on = _dt.datetime.now().strftime("%d %b %Y")
        checkin_block = ""
        if checkin_url:
            checkin_block = f"""
    <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:14px;border-radius:6px;margin-bottom:16px;font-size:13px;color:#166534;">
      <div style="font-weight:700;margin-bottom:6px;">&#128205; Employee Check-In Page</div>
      <div>Share this link with your employees, or post it as a QR code at the office -- no login required to mark attendance:</div>
      <div style="margin-top:8px;"><a href="{checkin_url}" style="color:#15803d;font-weight:700;">{checkin_url}</a></div>
    </div>
            """
        html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;
     overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">Payment received -- your HRzest.com portal is ready</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">{company_name}</div>
  </div>
  <div style="padding:28px;">
    <p style="font-size:15px;color:#1e293b;margin-bottom:20px;">Your payment was successful and your
      organisation's dedicated HRzest.com portal has been created.</p>
    <a href="{portal_url}" style="display:block;text-align:center;padding:14px 28px;background:#1e3a8a;color:white;
       border-radius:10px;text-decoration:none;font-size:15px;font-weight:700;margin-bottom:12px;">
      Sign In to Your Portal
    </a>
    <p style="font-size:12px;color:#94a3b8;margin-bottom:20px;">Use the admin password you set when you registered. Or copy the link: {portal_url}</p>
    <div style="background:#f1f5f9;border-radius:10px;padding:16px 18px;margin-bottom:16px;">
      <div style="font-size:13px;font-weight:700;color:#1e293b;margin-bottom:8px;">Payment Receipt</div>
      <div style="font-size:13px;color:#475569;display:flex;justify-content:space-between;margin-bottom:4px;">
        <span>Billing</span><strong>{plan_display_name}</strong></div>
      <div style="font-size:13px;color:#475569;display:flex;justify-content:space-between;margin-bottom:4px;">
        <span>Amount Paid</span><strong>{amount_display}</strong></div>
      <div style="font-size:13px;color:#475569;display:flex;justify-content:space-between;margin-bottom:4px;"><span>Date</span><strong>{paid_on}</strong></div>
      <div style="font-size:13px;color:#475569;display:flex;justify-content:space-between;"><span>Payment ID</span><strong>{razorpay_payment_id}</strong></div>
    </div>

    {checkin_block}

    <p style="font-size:12px;color:#94a3b8;">Your portal: {portal_url}</p>
  </div>
</div>"""
        send_email_async(admin_email, f"Payment confirmed -- set up your HRzest.com portal for {company_name}", html_body, email_cfg)
        return True
    except Exception as exc:
        app_log.error("send_payment_confirmation_email failed: %s", exc)
        return False
