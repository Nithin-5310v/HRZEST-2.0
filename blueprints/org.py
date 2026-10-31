# -*- coding: utf-8 -*-
"""Org blueprint -- multi-tenant org self-registration."""
import os
import re
import html
import time
import secrets
import difflib
from flask import Blueprint, request, redirect, render_template, flash, jsonify, session, g, abort
from extensions import app_log, limiter, log_security_event
from utils.auth import (
    generate_password_hash, _hash_token, turnstile_enabled, verify_turnstile, _TURNSTILE_SITE_KEY,
    validate_new_password, admin_required, api_required, api_role_required,
)
from utils.plan_limits import PLAN_LABEL, get_per_employee_paise, format_price_inr
from utils.email_utils import get_email_config, send_email_async, notify_support
from utils.tenant_routing import RESERVED_PATH_SEGMENTS
from utils.helpers import clean_email_domain, validate_email_domain_format, _safe_app_url, save_application_document, tpath

org_bp = Blueprint("org", __name__)

_SUBDOMAIN_RE = re.compile(r'^[a-z0-9\-]+$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# Tenants are addressed by URL path now (<apex-domain>/<subdomain>/...),
# not subdomain -- portal_url below builds that apex from _safe_app_url()
# (the current request's own host in dev, APP_URL in production) rather
# than a hardcoded domain. The DB column and every internal variable are
# still named "subdomain" (no migration needed, it's just a slug string
# either way).

# Slugs that must never be self-registered -- shared with
# utils/tenant_routing.py's WSGI middleware, which uses the exact same set
# to decide whether a URL's first path segment is a company slug or a real
# top-level route (e.g. registering "login" as a company would otherwise
# make www.hrzest.com/login ambiguous between the global login-picker page
# and a company's own portal).
_RESERVED_SUBDOMAINS = RESERVED_PATH_SEGMENTS


def _clean_subdomain_slug(raw, company_name=""):
    if not raw and company_name:
        raw = company_name
    s = str(raw).strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = s.split('/')[0].split(':')[0]
    for suffix in [".hrzest.com", ".hrms.gradzest.com", ".gradzest.com", ".gradzest.in", ".com", ".in", ".org", ".net", ".io"]:
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    s = re.sub(r'[^a-z0-9\-]', '', s)
    return s


_MAX_SLUG_LEN = 40


def _slug_for_company(company_name):
    """The company URL for a new signup, made from its name -- step 1 asks
    for the name, never the URL: lowercase letters and digits, words joined
    by hyphens ("Acme Pvt. Ltd." -> "acme-pvt-ltd"), with -2, -3 ... added
    when that URL is reserved, already a company's, or would land on an
    existing schema (provision_tenant()'s own check, since "a-b" and "a_b"
    share att_a_b). Picked when the code is verified, the moment the company
    is created, so two pending signups never hold the same URL. None if the
    name has no letters or digits at all."""
    base = re.sub(r'[^a-z0-9]+', '-', (company_name or '').lower()).strip('-')[:_MAX_SLUG_LEN].strip('-')
    if not base:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    try:
        for n in range(1, 1000):
            slug = base if n == 1 else f"{base[:_MAX_SLUG_LEN - len(str(n)) - 1].rstrip('-')}-{n}"
            if slug in _RESERVED_SUBDOMAINS:
                continue
            mcur.execute("SELECT 1 FROM tenants WHERE subdomain=%s", (slug,))
            if mcur.fetchone():
                continue
            mcur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s",
                         ("att_" + slug.replace("-", "_"),))
            if mcur.fetchone():
                continue
            return slug
    finally:
        mcur.close()
        mconn.close()
    return None


def _validate_new_tenant_fields(company_name, subdomain, admin_username, admin_password, admin_email,
                                email_domain=None):
    """Shared field validation for all three tenant-creation entry points
    (/create_org, /api/create_org, and the platform-admin-initiated create
    flow in platform_admin.py) -- keeps the reserved-subdomain and
    email-format checks from drifting out of sync across call sites.
    Returns an error message, or None if every field is valid.

    email_domain is required for every new company (e.g. "acme.com") --
    it's what utils/helpers.py's validate_employee_email_domain() later
    checks new employees' emails against. Existing tenants provisioned
    before this field existed simply have none set, which keeps that
    check a no-op for them (see validate_employee_email_domain's
    docstring) -- this requirement only applies going forward."""
    subdomain = _clean_subdomain_slug(subdomain, company_name)
    if not all([company_name, subdomain, admin_username, admin_password, admin_email]):
        return "All fields (company name, subdomain, admin email/username/password) are required."
    if not _EMAIL_RE.match(admin_email):
        return "Enter a valid admin email address."
    if not _SUBDOMAIN_RE.match(subdomain):
        return "Subdomain may only contain lowercase letters, digits, and hyphens."
    if subdomain in _RESERVED_SUBDOMAINS:
        return f"Subdomain '{subdomain}' is reserved. Choose another."
    _pw_ok, _pw_err = validate_new_password(admin_password)
    if not _pw_ok:
        return _pw_err
    domain_error = validate_email_domain_format(clean_email_domain(email_domain))
    if domain_error:
        return domain_error
    return None


_MOBILE_RE = re.compile(r'^\+?[0-9]{10,15}$')
# The web signup's admin login username IS their email, and
# admin_users.username is VARCHAR(50) in every company schema.
_MAX_SIGNUP_EMAIL_LEN = 50


def _clean_mobile(raw):
    """A mobile number as digits with an optional leading +, dropping the
    spaces, dashes, dots and brackets people type in them."""
    return re.sub(r'[\s\-.()]', '', str(raw or ''))


def _validate_signup_account(company_name, admin_email, admin_mobile, admin_password):
    """Step 1 of the signup (POST /create_org, the app's /api/create_org):
    the company's name -- its URL is made from it (_slug_for_company) --
    and the admin's email (also their login username), mobile number and
    password. Everything else (GSTIN, logo, documents, payment) is done
    from the dashboard afterwards. Returns an error message, or None."""
    if not all([company_name, admin_email, admin_mobile, admin_password]):
        return "Company name, email, mobile number and password are all required."
    if len(company_name) > 200:
        return "Company name must be at most 200 characters."
    if not re.search(r'[A-Za-z0-9]', company_name):
        return "Company name must contain at least one letter or digit."
    if not _EMAIL_RE.match(admin_email):
        return "Enter a valid email address."
    if len(admin_email) > _MAX_SIGNUP_EMAIL_LEN:
        return f"Use an email address of at most {_MAX_SIGNUP_EMAIL_LEN} characters -- it's also your login username."
    if not _MOBILE_RE.match(admin_mobile):
        return "Enter a valid mobile number: 10 to 15 digits, optionally starting with +."
    _pw_ok, _pw_err = validate_new_password(admin_password)
    if not _pw_ok:
        return _pw_err
    return validate_email_domain_format(clean_email_domain(admin_email))


def _validate_company_profile(company_name, subdomain, gst_number):
    """The old step 3 (POST /create_org/profile), still served for an
    application whose email was verified before step 1 asked for the
    company name: the company's name, URL slug (already through
    _clean_subdomain_slug) and GSTIN (through _clean_gst). Returns an error
    message, or None."""
    if not company_name or not subdomain:
        return "Company name and company URL are required."
    if len(company_name) > 200:
        return "Company name must be at most 200 characters."
    if not _SUBDOMAIN_RE.match(subdomain) or len(subdomain) > 100:
        return "Company URL may only contain lowercase letters, digits, and hyphens (up to 100)."
    if subdomain in _RESERVED_SUBDOMAINS:
        return f"Company URL '{subdomain}' is reserved. Choose another."
    return _validate_gst_number(gst_number)


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
            "logo_url=COALESCE(%s, logo_url), contact_phone=COALESCE(%s, contact_phone), setup_done=1 WHERE id=1",
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


_COMPANY_SUFFIX_RE = re.compile(
    r'\b(private\s+limited|pvt\.?\s*ltd\.?|public\s+limited|limited|ltd\.?|llp|inc\.?|incorporated|'
    r'corp\.?|corporation|co\.?|company|llc)\b'
)
# A pure-normalization-based match still catches most real re-registration
# attempts (same legal name, different casing/punctuation/legal suffix)
# with zero false-positive risk. A second, SequenceMatcher-based pass below
# catches near-identical names a registrant deliberately tweaks (a typo, a
# dropped/added plural) -- no Postgres extension (pg_trgm) required, so
# this works identically in every deployment without needing CREATE
# EXTENSION privileges.
#
# Deliberately high (0.95, not the more obvious ~0.85): this blocks a real
# SIGNUP outright, not just a soft admin-review flag, so a false positive
# means refusing a legitimate, genuinely-different business. Two real
# companies that happen to share most of their name (regional arms, near-
# namesakes -- "Acme Logistics" vs "Acme Logistics West") are far more
# common than this threshold's headroom suggests: at a looser threshold,
# a single differing word/character in an otherwise-templated name (e.g.
# "Northgate Retail A" vs "Northgate Retail B") already scores ~0.92 and
# would be wrongly blocked. 0.95 still reliably catches the actual abuse
# case this exists for -- a registrant re-submitting the near-identical
# name with a cosmetic tweak (a typo, a dropped/added "s") -- which scores
# ~0.97+ in practice.
_FUZZY_MATCH_THRESHOLD = 0.95


def _normalize_company_name(name):
    """Strips legal-entity suffixes and punctuation so "Acme Pvt Ltd" and
    "Acme Private Limited" collapse to the same core string before either
    the exact or the fuzzy comparison in check_duplicate_name() runs."""
    s = re.sub(r'\s+', ' ', (name or '').strip()).lower()
    s = _COMPANY_SUFFIX_RE.sub('', s)
    s = re.sub(r'[^a-z0-9 ]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def check_duplicate_name(company_name):
    """Looks for an existing, already-provisioned tenant whose name is the
    same business as `company_name` -- either an exact (post-normalization)
    match, or a fuzzy one close enough that it's almost certainly the same
    registrant trying again under a slightly different name (e.g. "Acme
    Pvt Ltd" vs "Acme India"). Returns ((tenant_id, company_name,
    admin_email), match_type) if found, else (None, None).

    Deliberately NOT folded into _validate_new_tenant_fields(), which the
    mobile signup API shares -- only the gated step-1 signup route calls
    this (including a platform admin's free registration)."""
    normalized = _normalize_company_name(company_name)
    if not normalized:
        return None, None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants")
    rows = mcur.fetchall()
    mcur.close()
    mconn.close()

    best_row, best_ratio = None, 0.0
    for row in rows:
        existing_normalized = _normalize_company_name(row[1])
        if not existing_normalized:
            continue
        if existing_normalized == normalized:
            return row, "exact"
        ratio = difflib.SequenceMatcher(None, normalized, existing_normalized).ratio()
        if ratio > best_ratio:
            best_row, best_ratio = row, ratio
    if best_row is not None and best_ratio >= _FUZZY_MATCH_THRESHOLD:
        return best_row, "fuzzy"
    return None, None


_GST_RE = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')


def _clean_gst(raw):
    return re.sub(r'\s+', '', (raw or '')).upper()


def _validate_gst_number(gst_number):
    """15-character GSTIN format (state code + PAN + entity code + 'Z' +
    checksum) -- the standard Indian business-registration identifier.
    Required at public signup (create_org()/api_create_org()) specifically
    so check_duplicate_gst() has something real to dedup on; NOT required
    by _validate_new_tenant_fields() since that's shared with the Platform
    Admin direct-create path, which doesn't collect one."""
    if not gst_number:
        return "GST / business registration number is required."
    if not _GST_RE.match(gst_number):
        return "Enter a valid 15-character GSTIN (e.g. 27AAAAA0000A1Z5)."
    return None


def check_duplicate_gst(gst_number):
    """Exact match against every already-provisioned tenant's GSTIN.
    Catches the case check_duplicate_name() can't: the same real business
    registering again under a genuinely different company name after a
    prior trial locked/expired. Returns (tenant_id, company_name,
    admin_email) if found, else None."""
    if not gst_number:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE gst_number=%s", (gst_number,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def check_duplicate_pan(pan):
    """Exact match against every already-provisioned tenant's PAN --
    same purpose as check_duplicate_gst() above, but PAN is never typed at
    signup (only GST is); this is populated purely from OCR-extracted
    document content (utils/document_verification.py) and only ever
    compared against other tenants' OCR-extracted PANs, never against
    anything a registrant typed. Returns (tenant_id, company_name,
    admin_email) if found, else None."""
    if not pan:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE pan=%s", (pan,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def check_duplicate_cin(cin):
    """Exact match against every already-provisioned tenant's CIN --
    same shape/purpose as check_duplicate_pan() above."""
    if not cin:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE cin=%s", (cin,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def check_duplicate_admin_email(admin_email):
    """Exact (case-insensitive) match against every already-provisioned
    tenant's admin_email. A trial-abuse registrant reusing their own email
    under a new company name/GST is still caught here even if the other
    two checks somehow miss it. Returns (tenant_id, company_name,
    admin_email) if found, else None."""
    normalized = (admin_email or '').strip().lower()
    if not normalized:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE LOWER(admin_email)=%s", (normalized,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def _record_duplicate_alert(application_id, attempted_company_name, attempted_admin_email, conflicting,
                            match_type="exact"):
    """Internal-only record of a signup blocked by check_duplicate_name()/
    check_duplicate_gst()/check_duplicate_admin_email() (match_type
    identifies which one fired: 'exact'/'fuzzy' for a company-name match,
    'gst' or 'email' for the other two). The registrant never sees any of
    `conflicting`'s contents -- only the platform-admin duplicate-alerts
    screen (blueprints/platform_admin.py) ever reads this table.
    Best-effort: a logging failure must not be able to block (or un-block)
    the signup rejection itself."""
    conflicting_id, conflicting_name, conflicting_email = conflicting
    try:
        from database import get_master_db
        mconn = get_master_db()
        mcur = mconn.cursor()
        mcur.execute(
            "INSERT INTO tenant_duplicate_alerts "
            "(application_id, attempted_company_name, attempted_admin_email, conflicting_tenant_id, "
            "conflicting_company_name, conflicting_admin_email, match_type, source_ip) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (application_id, attempted_company_name, attempted_admin_email, conflicting_id, conflicting_name,
             conflicting_email, match_type, request.remote_addr)
        )
        mconn.commit()
        mcur.close()
        mconn.close()
    except Exception as exc:
        app_log.error("_record_duplicate_alert failed: %s", exc)
    log_security_event(
        "org.duplicate_company_blocked",
        f"Signup blocked: {match_type} match against an existing tenant",
        level="WARNING", attempted_company_name=attempted_company_name,
        conflicting_tenant_id=conflicting_id,
    )


_OTP_TTL_MINUTES = 5
_OTP_MAX_ATTEMPTS = 5
_GENERIC_DUPLICATE_MSG = "A company with this name appears to already be registered. If this is your company, please contact support."
_APPLICATION_DOC_KINDS = ("registration_cert", "address_proof", "visiting_card", "name_board_photo")


def _generate_otp():
    return f"{secrets.randbelow(900000) + 100000}"


def send_org_signup_otp_email(to_email, company_name, otp_code):
    """Email the signup-verification OTP for a new company application.
    Deliberately a separate function from utils/totp.py's
    send_mfa_login_email() -- that one's copy ("Login Verification Code" /
    "finish signing in") is written for an existing account's login step;
    reusing it here for a brand-new registrant who hasn't created anything
    yet would be a confusing email to receive."""
    try:
        email_cfg = get_email_config()
        if not email_cfg:
            if os.environ.get("APP_ENV", "production") == "development":
                # Local dev only (same situation MANDATORY_PLATFORM_ADMIN_MFA's
                # .env comment documents for login MFA) -- this OTP step is a
                # required business gate, not an optional hardening layer, so
                # there's no bypass flag for it the way login MFA has one.
                # Logging the code here (never in a response body, and only
                # reached when there's genuinely no other way to deliver it)
                # is what makes the flow testable locally without real SMTP
                # creds. Explicitly gated on APP_ENV -- a production
                # deployment with SMTP creds missing/expired/misconfigured
                # must never fall into this branch: anyone with log/SIEM
                # read access (a broader population than DB access, often
                # including junior ops/on-call) could otherwise complete the
                # email-verification step for an arbitrary address during an
                # SMTP outage.
                app_log.warning("send_org_signup_otp_email: no SMTP configured -- OTP for %s (%s) is %s",
                                to_email, company_name, otp_code)
            else:
                # Fails closed in production: no code anywhere an operator's
                # log/alert pipeline (utils/alerts.py) would surface it, so
                # this is visible to ops without being a usable bypass. The
                # applicant is stuck at the OTP-entry screen with no way to
                # receive a code -- a real signup is blocked, which is the
                # correct outcome for a genuinely broken SMTP config, versus
                # silently handing out a working credential to anyone
                # reading the logs.
                app_log.error(
                    "send_org_signup_otp_email: SMTP not configured in production -- OTP delivery to %s "
                    "for '%s' failed. Fix SMTP configuration; applicant cannot complete signup until then.",
                    to_email, company_name,
                )
            return False
        # No company name yet on the web signup's first step.
        _company = html.escape(str(company_name)) if company_name else "your company"
        _code = html.escape(str(otp_code))
        html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:480px;margin:auto;background:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #1e293b;">
  <div style="background:#1d4ed8;padding:22px 26px;color:#fff;">
    <div style="font-size:18px;font-weight:700;">Verify Your Email</div>
    <div style="font-size:12px;opacity:0.8;margin-top:4px;">Registering {_company}</div>
  </div>
  <div style="padding:26px;">
    <p style="color:#cbd5e1;font-size:13px;">Use this code to confirm your email address and continue
      registering your company. It expires in {_OTP_TTL_MINUTES} minutes and can only be used once.</p>
    <div style="text-align:center;margin:22px 0;padding:16px;background:#090d16;border-radius:12px;">
      <span style="font-size:32px;font-weight:800;letter-spacing:6px;color:#60a5fa;">{_code}</span>
    </div>
    <p style="font-size:12px;color:#64748b;">If you didn't request this, you can safely ignore this email.</p>
  </div>
</div>"""
        send_email_async(to_email, "Verify your email to register your company", html_body, email_cfg)
        return True
    except Exception as exc:
        app_log.error("send_org_signup_otp_email failed: %s", exc)
        return False


def send_application_rejected_email(to_email, company_name, reason, status_url=None):
    """status_url (when given) is a fresh magic link -- see
    blueprints/platform_admin.py's platform_admin_reject_application(),
    which rotates access_token_hash specifically so this link works even
    once the applicant's original browser session is long gone. Lets them
    re-upload corrected documents directly from this email instead of
    restarting the whole application from scratch."""
    try:
        email_cfg = get_email_config()
        if not email_cfg:
            return False
        _company = html.escape(str(company_name))
        _reason = html.escape(str(reason or "It did not meet our verification requirements."))
        _link_block = ""
        if status_url:
            _link_block = f"""
    <a href="{status_url}" style="display:block;text-align:center;padding:13px 20px;background:#1e3a8a;color:#fff;
       border-radius:10px;text-decoration:none;font-size:14px;font-weight:700;margin:18px 0 6px;">
      Re-upload Corrected Documents
    </a>"""
        html_body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:480px;margin:auto;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e2e8f0;">
  <div style="background:#7f1d1d;padding:22px 26px;color:#fff;">
    <div style="font-size:18px;font-weight:700;">Application Not Approved</div>
    <div style="font-size:12px;opacity:0.85;margin-top:4px;">{_company}</div>
  </div>
  <div style="padding:26px;">
    <p style="color:#334155;font-size:14px;">We're unable to approve your company registration at this time.</p>
    <div style="background:#fef2f2;border-left:4px solid #dc2626;padding:14px;border-radius:6px;margin:16px 0;font-size:13px;color:#7f1d1d;">{_reason}</div>
    <p style="color:#334155;font-size:13px;">You don't need to start over -- fix what's noted above and re-upload just your documents.</p>
    {_link_block}
    <p style="color:#64748b;font-size:12px;">If you believe this is an error, please contact support.</p>
  </div>
</div>"""
        send_email_async(to_email, f"Your company registration was not approved -- {company_name}", html_body, email_cfg)
        return True
    except Exception as exc:
        app_log.error("send_application_rejected_email failed: %s", exc)
        return False


def _generate_access_token():
    return secrets.token_urlsafe(32)


def _load_application(application_id, access_token):
    """Fetch a tenant_applications row by id, verifying the caller presents
    the matching access_token (see app.py's schema comment on
    access_token_hash) -- the single mechanism (used identically by web
    and mobile) that stops someone from guessing a sequential application
    id and hijacking or peeking at someone else's in-progress signup.
    Returns a dict of the row's columns, or None if not found / token
    mismatch."""
    if not application_id or not access_token:
        return None
    application = _fetch_application_row(application_id)
    if not application:
        return None
    if not secrets.compare_digest(application["access_token_hash"], _hash_token(access_token)):
        return None
    return application


def _fetch_application_row(application_id):
    """The raw tenant_applications row as a dict, with NO access check --
    only ever call this after the caller's access to application_id has
    been proven (_load_application's token check, or
    _verify_application_access's session marker)."""
    try:
        application_id = int(application_id)
    except (TypeError, ValueError):
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "SELECT id, company_name, subdomain, admin_username, admin_email, admin_password_hash, "
        "email_domain, employee_count, payment_option, logo_path, access_token_hash, otp_code_hash, "
        "otp_expires_at, otp_attempts, email_verified_at, doc_registration_cert, doc_address_proof, "
        "doc_visiting_card, doc_name_board_photo, documents_submitted_at, status, reviewed_by, "
        "reviewed_at, rejection_reason, tenant_id, doc_review_status, gst_number, free_registration, admin_mobile "
        "FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return None
    cols = ["id", "company_name", "subdomain", "admin_username", "admin_email", "admin_password_hash",
            "email_domain", "employee_count", "payment_option", "logo_path", "access_token_hash", "otp_code_hash",
            "otp_expires_at", "otp_attempts", "email_verified_at", "doc_registration_cert", "doc_address_proof",
            "doc_visiting_card", "doc_name_board_photo", "documents_submitted_at", "status", "reviewed_by",
            "reviewed_at", "rejection_reason", "tenant_id", "doc_review_status", "gst_number", "free_registration",
            "admin_mobile"]
    return dict(zip(cols, row))


def _verify_application_access(application_id):
    """Whether this browser has proven it holds application_id's access
    token -- via the emailed link's ?token=, the signup session, or an
    earlier successful check in this session. On success only the
    (non-secret) application id is remembered in the session, never the
    token, so later same-page API calls (create_subscription/confirm) can
    be authorised without the page's JavaScript ever seeing the token."""
    try:
        application_id = int(application_id)
    except (TypeError, ValueError):
        return False
    for candidate in (request.args.get("token", ""), session.get("org_access_token", "")):
        if candidate and _load_application(application_id, candidate):
            session["org_verified_application_id"] = application_id
            return True
    return session.get("org_verified_application_id") == application_id


@org_bp.route("/get-started", methods=["GET"])
def get_started_page():
    """Retired standalone chooser page -- the landing page ("/") now shows
    its own "Create Your Company" / "Login" links directly instead of
    sending visitors through this extra hop. Kept as a redirect (rather
    than removed outright) so old bookmarks/links/emails pointing here
    still land somewhere useful; "get-started" also stays in
    RESERVED_PATH_SEGMENTS so no company can ever claim it as a slug."""
    from utils.analytics import track_page_view
    track_page_view("/get-started")
    return redirect("/")


@org_bp.route("/create_org", methods=["GET"])
def create_org_page():
    # Flashed messages live in the session cookie, not scoped to any one
    # page -- an unrelated admin-session-expiry notice (category
    # "warning", queued by app.py's _enforce_session_lifetime /
    # _enforce_idle_timeout / _enforce_csrf hooks on some earlier request
    # in this same browser, possibly a different tab entirely) must never
    # surface on this public signup page just because it shares a cookie
    # with that admin session. This page's own flashes (validation
    # errors, captcha failures, from the POST handler below) are always
    # category "error" -- keep only those, drop everything else.
    if session.get("_flashes"):
        session["_flashes"] = [f for f in session["_flashes"] if f[0] == "error"]
    from utils.analytics import track_page_view
    track_page_view("/create_org")
    return render_template(
        "create_org.html",
        per_employee_paise=get_per_employee_paise(),
        show_captcha=turnstile_enabled(),
        turnstile_site_key=_TURNSTILE_SITE_KEY,
        free_registration=request.args.get("free") == "1" and _free_registration_allowed(),
        portal_base=_safe_app_url(),
    )


def _free_registration_allowed():
    """A free registration -- the same signup wizard with no payment step
    (signup_plan=free) -- can only be started by a logged-in platform admin,
    from the dashboard's "New Company (Free)" link to /create_org?free=1."""
    from blueprints.platform_admin import platform_admin_session_active
    return platform_admin_session_active()


def _verify_application_otp(application, otp_code):
    """Shared OTP-check logic for both the web and mobile verify_otp
    routes. Returns (True, None) on success (advances the row to
    'otp_verified'), or (False, error_message) -- the caller decides how
    to surface that (flash+redirect for web, a JSON 400 for mobile)."""
    if application["status"] != "started":
        return False, "This application has already moved past email verification."
    if application["otp_attempts"] >= _OTP_MAX_ATTEMPTS:
        return False, "Too many incorrect attempts. Please request a new code."

    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    stored_hash = application["otp_code_hash"] or ""
    # Always the real code, in every APP_ENV: an APP_ENV=development bypass
    # that accepted any code let a server left in development mode create
    # companies for email addresses nobody had verified. Local dev without
    # SMTP reads the code from the log instead (send_org_signup_otp_email).
    code_matches = bool(otp_code) and secrets.compare_digest(_hash_token(otp_code), stored_hash)
    if not code_matches:
        mcur.execute("UPDATE tenant_applications SET otp_attempts=otp_attempts+1, updated_at=NOW() WHERE id=%s",
                     (application["id"],))
        mconn.commit()
        mcur.close()
        mconn.close()
        return False, "Incorrect code. Please try again."

    mcur.execute("SELECT otp_expires_at < NOW() FROM tenant_applications WHERE id=%s", (application["id"],))
    expired = mcur.fetchone()[0]
    if expired:
        mcur.close()
        mconn.close()
        return False, "This code has expired. Please request a new one."

    _mark_application_otp_verified(application["id"], mconn, mcur)
    return True, None


def _mark_application_otp_verified(application_id, mconn, mcur):
    """Advances the application to 'otp_verified' once its emailed code
    has been checked; commits and closes mconn/mcur."""
    mcur.execute(
        "UPDATE tenant_applications SET status='otp_verified', email_verified_at=NOW(), updated_at=NOW() WHERE id=%s",
        (application_id,)
    )
    mconn.commit()
    mcur.close()
    mconn.close()


def _provision_application_instantly(application):
    """Called right after OTP verification succeeds, for EVERY
    payment_option -- every self-serve signup lane now provisions the
    tenant immediately, with KYC document collection/review moved fully
    async (see _save_application_documents()'s 'provisioned' guard below,
    and blueprints/platform_admin.py's approve/reject branches for an
    application whose tenant_id is already set). subscription_status is
    forced 'trialing' regardless of payment_option, since none of the
    three lanes collect payment upfront anymore -- see provision_tenant()'s
    own docstring for why this can't just be provision_tenant()'s default
    behavior (Platform Admin's own always-free tenant-creation tool shares
    that function and must NOT become trial-capped).

    Idempotent: a retried/duplicate request against an already-provisioned
    application (status=='provisioned') is a no-op success reusing the
    existing tenant, not a second schema-creation attempt.

    Returns (ok, error, portal_url_or_None, checkin_url_or_None) -- same
    shape as provision_tenant() itself."""
    from database import get_master_db

    if application["status"] == "provisioned":
        portal_url = f"{_safe_app_url()}/{application['subdomain']}/login"
        checkin_url = f"{_safe_app_url()}/{application['subdomain']}/checkin"
        return True, None, portal_url, checkin_url

    # A platform admin's free registration is a normal manually-invoiced
    # company (provision_tenant()'s default status), never a capped trial.
    ok, error, portal_url, checkin_url = provision_tenant(
        application["company_name"], application["subdomain"], application["admin_username"],
        application["admin_password_hash"], application["admin_email"],
        payment_option=application["payment_option"], email_domain=application["email_domain"],
        employee_count=application["employee_count"], logo_path=application["logo_path"],
        gst_number=application["gst_number"],
        subscription_status=None if application.get("free_registration") else "trialing",
        contact_phone=application.get("admin_mobile"),
    )
    if not ok:
        return False, error, None, None

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT id FROM tenants WHERE subdomain=%s", (application["subdomain"],))
    tenant_row = mcur.fetchone()
    tenant_id = tenant_row[0] if tenant_row else None
    mcur.execute(
        "UPDATE tenant_applications SET status='provisioned', tenant_id=%s, updated_at=NOW() WHERE id=%s",
        (tenant_id, application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    log_security_event(
        "org.tenant_provisioned_instant",
        f"{'Free registration' if application.get('free_registration') else 'Self-serve signup'} "
        f"'{application['company_name']}' provisioned instantly "
        f"(payment_option={application['payment_option']}) -- KYC document review still pending, async",
        level="INFO", subdomain=application["subdomain"], application_id=application["id"], tenant_id=tenant_id,
    )
    send_portal_ready_email(
        application["admin_email"], application["company_name"], application["admin_username"],
        portal_url, checkin_url=checkin_url,
    )
    return True, None, portal_url, checkin_url


def _create_signup_company(application):
    """Creates the company for an application whose step 1 named it, once
    its email code is verified: re-checks the name against companies
    registered since step 1, picks the company URL (_slug_for_company),
    records it and the billing option on the application, and provisions
    it (_provision_application_instantly). Retries with the next free URL if
    a concurrent signup took this one between the pick and the insert.
    Returns (ok, error, application) -- the refreshed row on success."""
    conflicting, match_type = check_duplicate_name(application["company_name"])
    if conflicting:
        _record_duplicate_alert(application["id"], application["company_name"], application["admin_email"],
                                conflicting, match_type=match_type)
        return False, _GENERIC_DUPLICATE_MSG, None

    from utils.razorpay_utils import razorpay_configured
    from database import get_master_db
    # A platform admin's free registration is billed by manual invoice; a
    # self-serve company sets up auto-debit from its dashboard's checklist.
    online = not application["free_registration"] and razorpay_configured()
    error = "Could not pick a company URL. Please contact support."
    for _attempt in range(3):
        slug = _slug_for_company(application["company_name"])
        if not slug:
            return False, "Company name must contain at least one letter or digit.", None
        mconn = get_master_db()
        mcur = mconn.cursor()
        mcur.execute(
            "UPDATE tenant_applications SET subdomain=%s, payment_option=%s, updated_at=NOW() "
            "WHERE id=%s AND status='otp_verified'",
            (slug, "online" if online else "manual", application["id"])
        )
        mconn.commit()
        mcur.close()
        mconn.close()
        ok, error, _portal_url, _checkin_url = _provision_application_instantly(
            _fetch_application_row(application["id"]))
        if ok:
            return True, None, _fetch_application_row(application["id"])
        if "already taken" not in (error or ""):
            break
    return False, error, None


def _approve_application_reviewed(application, reviewed_by):
    """Approves an already-provisioned application's KYC review
    (doc_review_status 'pending_review' -> 'approved'), reinstating the
    tenant if it was previously suspended for a rejected review. Shared by
    blueprints/platform_admin.py's human-reviewer route AND
    utils/document_verification.py's automated worker -- reviewed_by is
    what distinguishes the two in the audit trail (a platform-admin
    username, or the literal string 'system:automated_verification').

    Returns (ok, msg) -- ok is False only if the application wasn't
    actually awaiting review (stale/double-submitted call); msg is a
    human-readable result either way, safe to flash() as-is."""
    if application["tenant_id"] is None:
        return False, "Application is not yet provisioned."
    if application["doc_review_status"] != "pending_review":
        return False, "This application's documents aren't awaiting review."
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    # "AND doc_review_status='pending_review'" is load-bearing, not
    # decorative -- this is called from both the platform-admin route AND
    # utils/document_verification.py's automated worker, which can race
    # each other for the same application. Only the request whose UPDATE
    # actually flips a row (cursor.rowcount == 1) proceeds to reinstate/
    # log; the loser gets the same success result a retried call expects,
    # without a second reinstate or a duplicate security-event pair.
    cur.execute(
        "UPDATE tenant_applications SET doc_review_status='approved', reviewed_by=%s, reviewed_at=NOW(), "
        "updated_at=NOW() WHERE id=%s AND doc_review_status='pending_review'",
        (reviewed_by, application["id"])
    )
    if cur.rowcount != 1:
        conn.commit()
        cur.close()
        conn.close()
        return True, f"'{application['company_name']}'s documents approved."
    cur.execute("SELECT status FROM tenants WHERE id=%s", (application["tenant_id"],))
    tenant_row = cur.fetchone()
    reinstated = bool(tenant_row and tenant_row[0] == "suspended")
    if reinstated:
        cur.execute("UPDATE tenants SET status='active' WHERE id=%s", (application["tenant_id"],))
    conn.commit()
    cur.close()
    conn.close()
    if reinstated:
        log_security_event(
            "platform_admin.tenant_reinstated",
            f"Tenant {application['tenant_id']} reinstated -- corrected KYC documents approved",
            level="WARNING", identifier=reviewed_by, application_id=application["id"], tenant_id=application["tenant_id"],
        )
    log_security_event(
        "platform_admin.application_approved",
        f"Application {application['id']} KYC documents approved (tenant already provisioned)",
        level="INFO", identifier=reviewed_by, application_id=application["id"], tenant_id=application["tenant_id"],
    )
    return True, f"'{application['company_name']}'s documents approved."


def _reject_application_reviewed(application, reviewed_by, reason):
    """Rejects an already-provisioned application's KYC review, suspending
    the live tenant (utils/tenant_routing.py stops resolving a
    status!='active' tenant's slug at all -- the same mechanism
    platform_admin_set_status() relies on elsewhere) and emailing a fresh
    magic-link re-upload URL so the applicant has a way back in even
    though they can no longer log in to use the in-portal verification
    page. Shared by blueprints/platform_admin.py's human-reviewer route
    AND utils/document_verification.py's automated worker.

    Returns (ok, msg) -- ok is False only if the application isn't
    actually provisioned yet (nothing to suspend)."""
    if application["tenant_id"] is None:
        return False, "Application is not yet provisioned."
    from database import get_master_db
    from utils.auth import _hash_token
    new_token = secrets.token_urlsafe(32)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE tenant_applications SET doc_review_status='rejected', reviewed_by=%s, reviewed_at=NOW(), "
        "rejection_reason=%s, access_token_hash=%s, updated_at=NOW() WHERE id=%s",
        (reviewed_by, reason, _hash_token(new_token), application["id"])
    )
    cur.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (application["tenant_id"],))
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        "platform_admin.tenant_suspended_kyc_rejected",
        f"Tenant {application['tenant_id']} suspended -- KYC documents rejected: {reason}",
        level="WARNING", identifier=reviewed_by, application_id=application["id"],
        tenant_id=application["tenant_id"], reason=reason,
    )
    status_url = f"{_safe_app_url()}/create_org/status/{application['id']}?token={new_token}"
    send_application_rejected_email(application["admin_email"], application["company_name"], reason, status_url)
    log_security_event("platform_admin.application_rejected", f"Application {application['id']} rejected",
                       level="INFO", identifier=reviewed_by, application_id=application["id"], reason=reason)
    return True, "Application rejected."


def _save_application_documents(application, files):
    """Shared document-upload logic for both web and mobile. `files` is a
    dict-like of {doc_kind: FileStorage} (request.files works directly).
    Returns (True, None) on success -- documents are saved and the
    application is auto-approved in the same call (see the
    _approve_application_reviewed() call at the end of this function),
    unblocking login immediately rather than waiting on a review -- or
    (False, error_message).

    'rejected' is deliberately allowed back in here -- re-uploading
    corrected documents is the ONLY recovery path for a rejected applicant;
    without it they'd have to restart the entire application (company_name/
    subdomain/admin_username/password, all of it) from scratch at
    POST /create_org, re-triggering the duplicate-name check and a fresh
    OTP for no reason, since none of that was ever wrong.

    'provisioned' is also allowed -- every self-serve application reaches
    this state right after OTP verification now (see
    _provision_application_instantly()), before ever uploading a single
    document, so this is the common case going forward, not an edge case.
    For a 'provisioned' application, `status` itself is left alone below
    (it means "has a tenant" now, not "where's the KYC review at") and
    only `doc_review_status` advances -- see that column's own comment."""
    if application["status"] not in ("otp_verified", "pending_review", "rejected", "provisioned"):
        return False, "This application isn't ready for document upload."

    saved_paths = {}
    for doc_kind in _APPLICATION_DOC_KINDS:
        file_storage = files.get(doc_kind)
        if not file_storage or not file_storage.filename:
            return False, f"Please upload your {doc_kind.replace('_', ' ')}."
        path, err = save_application_document(file_storage, application["id"], doc_kind)
        if err:
            return False, f"{doc_kind.replace('_', ' ').title()}: {err}"
        saved_paths[doc_kind] = path

    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        # rejection_reason/reviewed_by/reviewed_at reset to NULL -- a
        # re-upload from 'rejected' must look like a fresh submission to
        # the platform admin's queue/detail view, not carry forward stale
        # rejection context from the prior review. `status` only advances
        # to 'pending_review' for the legacy (pre-instant-provisioning)
        # in-flight applications -- an already-'provisioned' application
        # keeps that status (it now means "has a tenant", tracked
        # separately from KYC review progress) and only doc_review_status
        # moves, to 'pending_review'.
        # verification_job_status='pending' hands this straight to
        # utils/document_verification.py's automated worker -- a re-upload
        # after rejection resets verification_attempts/ocr_extraction too,
        # same "must look like a fresh submission" reasoning as
        # rejection_reason/reviewed_by/reviewed_at just below.
        "UPDATE tenant_applications SET doc_registration_cert=%s, doc_address_proof=%s, doc_visiting_card=%s, "
        "doc_name_board_photo=%s, documents_submitted_at=NOW(), "
        "status = CASE WHEN status='provisioned' THEN status ELSE 'pending_review' END, "
        "doc_review_status='pending_review', updated_at=NOW(), "
        "rejection_reason=NULL, reviewed_by=NULL, reviewed_at=NULL, "
        "verification_job_status='pending', verification_attempts=0, verification_error=NULL, ocr_extraction=NULL "
        "WHERE id=%s",
        (saved_paths["registration_cert"], saved_paths["address_proof"], saved_paths["visiting_card"],
         saved_paths["name_board_photo"], application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    # Auto-approve immediately on submission -- KYC review no longer
    # gates login (matches the OAuth signup path in
    # blueprints/oauth_signup.py, which never collects documents at all).
    # verification_job_status stays 'pending' above, so
    # utils/document_verification.py's automated Claude-vision worker
    # still runs afterward and can still suspend the tenant via
    # _reject_application_reviewed() if it finds fraud (duplicate GST/PAN/
    # CIN) -- this removes the WAIT for a human/AI reviewer before access,
    # not the fraud check itself, which now runs as a retroactive safety
    # net instead of a precondition. Mutate the in-memory dict to match
    # what the UPDATE above just wrote (doc_review_status='pending_review')
    # -- _approve_application_reviewed()'s own guard reads this cached
    # field, not a fresh row, and the caller's `application` here still
    # holds whatever it looked like before this function ran.
    application["doc_review_status"] = "pending_review"
    _approve_application_reviewed(application, "system:auto_approved_on_submit")
    return True, None


def _back_to_signup_form():
    """Where a rejected POST /create_org sends the visitor: back to the
    form they filled in. The home page's registration form posts
    return_to=home -- an allowlisted value, never a URL, so this can't be
    used as an open redirect. Anything else keeps the original
    /create_org."""
    if request.form.get("return_to") == "home":
        return redirect("/?registration=failed#register")
    if request.form.get("signup_plan") == "free":
        return redirect("/create_org?free=1")
    return redirect("/create_org")


@org_bp.route("/create_org", methods=["POST"])
@limiter.limit("10 per hour")
def create_org():
    """The whole web signup is one step: the company's name and the admin's
    account -- email (also their login username), mobile number and
    password -- then, on the same page, the code emailed to verify the
    address. This creates the tenant_applications row and emails the code;
    create_org_verify_otp() then creates the company and logs the admin
    straight into its dashboard, whose checklist (app.py's
    inject_registration_checklist) collects the rest: company details,
    documents, payment."""
    free_registration = request.form.get("signup_plan", "").strip().lower() == "free"
    if free_registration and not _free_registration_allowed():
        flash("Free registration can only be started from the Platform Admin dashboard.", "error")
        return redirect("/create_org")

    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response", "")
        if not verify_turnstile(token, request.remote_addr):
            flash("Captcha verification failed. Please try again.", "error")
            return _back_to_signup_form()

    company_name = re.sub(r'\s+', ' ', request.form.get("company_name", "")).strip()
    # Lowercase: it's the login username, matched exactly at /login.
    admin_email = request.form.get("admin_email", "").strip().lower()
    admin_mobile = _clean_mobile(request.form.get("admin_mobile", ""))
    admin_password = request.form.get("admin_password", "").strip()

    error = _check_new_signup(company_name, admin_email, admin_mobile, admin_password)
    if error:
        flash(error, "error")
        return _back_to_signup_form()

    application_id, access_token = _start_signup_application(company_name, admin_email, admin_mobile,
                                                             admin_password, free_registration)
    # access_token lives ONLY in the session cookie from here on for the
    # web flow -- never in a URL/query string, so it can't leak via
    # referrer headers or browser history. application_id (a bare
    # sequential id, not a secret) is fine to carry in the URL.
    session["org_access_token"] = access_token
    # Display only (drops the Payment step from the wizard's indicator);
    # what's provisioned follows the application row's free_registration.
    session["org_free_registration"] = free_registration
    return redirect(f"/create_org/verify_otp?application_id={application_id}")


def _check_new_signup(company_name, admin_email, admin_mobile, admin_password):
    """Step 1's checks, shared by the web form (create_org()) and the app
    (_api_start_account()): the fields, then whether this company or this
    admin email is already registered (both recorded as duplicate alerts
    for Platform Admin). Returns an error message, or None."""
    error = _validate_signup_account(company_name, admin_email, admin_mobile, admin_password)
    if error:
        return error
    conflicting, match_type = check_duplicate_name(company_name)
    if not conflicting:
        conflicting, match_type = check_duplicate_admin_email(admin_email), "email"
    if conflicting:
        _record_duplicate_alert(None, company_name, admin_email, conflicting, match_type=match_type)
        return _GENERIC_DUPLICATE_MSG
    return None


def _start_signup_application(company_name, admin_email, admin_mobile, admin_password, free_registration=False):
    """Step 1's shared tail (web create_org(), the app's api_create_org()),
    once _check_new_signup() passes: creates the application and emails
    the verification code. The company URL stays blank ('' -- the column is
    NOT NULL) until the code is verified (_create_signup_company). Returns
    (application_id, access_token)."""
    access_token = _generate_access_token()
    otp_code = _generate_otp()
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    # payment_option is settled when the company is created
    # (_create_signup_company).
    mcur.execute(
        "INSERT INTO tenant_applications (company_name, subdomain, admin_username, admin_email, admin_mobile, "
        "admin_password_hash, email_domain, payment_option, access_token_hash, otp_code_hash, "
        "otp_expires_at, source_ip, free_registration) "
        "VALUES (%s, '', %s,%s,%s,%s,%s,'manual',%s,%s, NOW() + (%s * INTERVAL '1 minute'), %s, %s) RETURNING id",
        (company_name, admin_email, admin_email, admin_mobile, generate_password_hash(admin_password),
         clean_email_domain(admin_email), _hash_token(access_token), _hash_token(otp_code),
         _OTP_TTL_MINUTES, request.remote_addr, free_registration)
    )
    application_id = mcur.fetchone()[0]
    mconn.commit()
    mcur.close()
    mconn.close()

    # Email verification is never skipped, in any APP_ENV: an
    # APP_ENV=development shortcut here once jumped straight to the document
    # upload, which also skipped the provisioning that only runs after a
    # verified code, stranding the applicant.
    send_org_signup_otp_email(admin_email, company_name, otp_code)
    return application_id, access_token


@org_bp.route("/create_org/verify_otp", methods=["GET"])
def create_org_verify_otp_page():
    """The signup page again, with what was entered locked in and a box for
    the emailed code -- verification belongs to the one signup step."""
    application = _load_application(request.args.get("application_id", ""), session.get("org_access_token"))
    if not application or application["status"] != "started":
        flash("Your registration session has ended. Please start again.", "error")
        return redirect("/create_org")
    return render_template(
        "create_org.html", code_step=True, application=application,
        free_registration=bool(application["free_registration"]),
        portal_base=_safe_app_url(),
    )


@org_bp.route("/create_org/verify_otp", methods=["POST"])
@limiter.limit("20 per hour")
def create_org_verify_otp():
    """Checks the emailed code, creates the company (_create_signup_company)
    and logs its admin in -- the dashboard's checklist takes it from there.
    A platform admin's free registration goes back to Platform Admin
    instead: the company's own admin logs in with their email."""
    application_id = request.form.get("application_id", "")
    otp_code = request.form.get("otp_code", "").strip()
    application = _load_application(application_id, session.get("org_access_token"))
    if not application:
        flash("Application not found. Please start again.", "error")
        return redirect("/create_org")
    if application["status"] == "otp_verified" and not application["company_name"]:
        # Verified before step 1 asked for the company name: the old
        # company-profile step still finishes it.
        return redirect(f"/create_org/profile?application_id={application['id']}")

    ok, error = _verify_application_otp(application, otp_code)
    if not ok:
        flash(error, "error")
        return redirect(f"/create_org/verify_otp?application_id={application['id']}")
    if not application["company_name"]:
        return redirect(f"/create_org/profile?application_id={application['id']}")

    # Re-fetch: _verify_application_otp() just advanced status to
    # 'otp_verified', but `application` is the pre-verification snapshot.
    ok, error, application = _create_signup_company(_fetch_application_row(application["id"]))
    if not ok:
        app_log.warning("create_org_verify_otp: could not create the company for application %s: %s",
                        application_id, error)
        flash(error if error == _GENERIC_DUPLICATE_MSG else
              f"Could not set up your company: {error}. Please contact support.", "error")
        return redirect("/create_org")

    session.pop("org_access_token", None)
    session.pop("org_free_registration", None)
    if application["free_registration"]:
        flash(f"'{application['company_name']}' is created at {_safe_app_url()}/{application['subdomain']}/login. "
              f"Its admin logs in with {application['admin_email']} and finishes registering from the dashboard.",
              "success")
        return redirect("/super_admin")
    return _log_in_new_company_admin(application)


def _log_in_new_company_admin(application):
    """Signs the new company's admin straight into its dashboard: binds the
    session to the company just provisioned (this request ran on a URL with
    no company in it, so under the default one) and starts the admin
    session the way a password login does (auth.begin_admin_session). The
    code they just entered was emailed to this same address, so it stands
    in for the emailed login code when MANDATORY_LOGIN_MFA is on, exactly as
    auth.mfa_verify() treats one."""
    from flask import current_app
    from blueprints.auth import begin_admin_session
    from database import get_master_db
    from utils.totp import mark_totp_enabled

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT db_name, subdomain FROM tenants WHERE id=%s", (application["tenant_id"],))
    db_name, slug = mcur.fetchone()
    mcur.close()
    mconn.close()

    g.tenant_db, g.tenant_slug = db_name, slug
    username = application["admin_username"]
    if current_app.config["MANDATORY_LOGIN_MFA"]:
        mark_totp_enabled(username)
    begin_admin_session(username, "admin")
    session["tenant_db"] = db_name
    session["tenant_slug"] = slug
    session["_tenant_status_checked_at"] = time.time()
    session["_billing_locked"] = False
    log_security_event(
        "auth.admin_login_success", f"Admin login for '{username}' (role=admin) right after registering",
        level="INFO", identifier=username, subdomain=slug,
    )
    flash("Your company is ready. Finish registering from the checklist below.", "success")
    return redirect(f"/{slug}/admin")


def _load_profile_application(application_id, access_token):
    """The application if access_token proves access, its email is
    verified and it's still waiting for its company profile, else None."""
    application = _load_application(application_id, access_token)
    if application and application["status"] == "otp_verified" and not application["tenant_id"]:
        return application
    return None


def _save_company_profile(application, fields, logo_file):
    """Step 3 of the signup wizard, shared by the web form and the mobile
    app's /api/create_org/profile: validates the company profile -- name,
    GSTIN, company URL, optional logo, the plan -- saves it on the verified
    application and provisions the company right away (as the code step
    used to). Returns (ok, error, portal_url, checkin_url); on failure the
    application is left verified, so the profile can be submitted again."""
    company_name = (fields.get("company_name") or "").strip()
    subdomain = _clean_subdomain_slug(fields.get("subdomain") or "", company_name)
    gst_number = _clean_gst(fields.get("gst_number") or "")
    error = _validate_company_profile(company_name, subdomain, gst_number)
    if error:
        return False, error, None, None

    conflicting, match_type = check_duplicate_name(company_name)
    if not conflicting:
        conflicting, match_type = check_duplicate_gst(gst_number), "gst"
    if conflicting:
        _record_duplicate_alert(application["id"], company_name, application["admin_email"], conflicting,
                                match_type=match_type)
        return False, _GENERIC_DUPLICATE_MSG, None, None

    logo_path = application["logo_path"]
    if logo_file and logo_file.filename:
        from utils.helpers import save_uploaded_logo
        logo_path, logo_err = save_uploaded_logo(logo_file, subdomain)
        if logo_err:
            return False, f"Company logo: {logo_err}", None, None

    from utils.razorpay_utils import razorpay_configured
    # "trial" is only ever chosen explicitly (the "2-Day Free Trial" option,
    # blueprints/trial_billing.TRIAL_DURATION_DAYS long, beside "Pay now").
    # A platform admin's free registration has no payment step and is
    # billed by manual invoice.
    signup_plan = (fields.get("signup_plan") or "pay_now").strip().lower()
    if application["free_registration"]:
        payment_option = "manual"
    elif signup_plan == "trial":
        payment_option = "trial"
    else:
        payment_option = "online" if razorpay_configured() else "manual"

    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "UPDATE tenant_applications SET company_name=%s, subdomain=%s, gst_number=%s, logo_path=%s, "
        "payment_option=%s, updated_at=NOW() WHERE id=%s AND status='otp_verified'",
        (company_name, subdomain, gst_number, logo_path, payment_option, application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    ok, error, portal_url, checkin_url = _provision_application_instantly(_fetch_application_row(application["id"]))
    if not ok:
        app_log.warning("company profile: provisioning failed for application %s: %s", application["id"], error)
    return ok, error, portal_url, checkin_url


def _render_profile_page(application):
    return render_template(
        "create_org.html", profile_step=True, application_id=application["id"],
        free_registration=bool(application["free_registration"]),
        per_employee_paise=get_per_employee_paise(),
        portal_base=_safe_app_url(),
    )


@org_bp.route("/create_org/profile", methods=["GET"])
def create_org_profile_page():
    application = _load_profile_application(request.args.get("application_id", ""), session.get("org_access_token"))
    if not application:
        flash("Verify your email first, or start a new registration.", "error")
        return redirect("/create_org")
    return _render_profile_page(application)


@org_bp.route("/create_org/profile", methods=["POST"])
@limiter.limit("20 per hour")
def create_org_profile():
    """Step 3 of the web signup wizard, after the email code (see
    _save_company_profile). A rejected profile -- a taken URL, a duplicate
    company -- comes back to this form, application intact; documents
    come next."""
    application = _load_profile_application(request.form.get("application_id", ""), session.get("org_access_token"))
    if not application:
        flash("Verify your email first, or start a new registration.", "error")
        return redirect("/create_org")
    ok, error, _portal_url, _checkin_url = _save_company_profile(application, request.form, request.files.get("logo"))
    if not ok:
        flash(error, "error")
        return redirect(f"/create_org/profile?application_id={application['id']}")
    return redirect(f"/create_org/upload_documents?application_id={application['id']}")


@org_bp.route("/create_org/resend_otp", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def create_org_resend_otp():
    application_id = request.form.get("application_id", "")
    application = _load_application(application_id, session.get("org_access_token"))
    if not application or application["status"] != "started":
        flash("This application can no longer receive a new code.", "error")
        return redirect("/create_org")

    otp_code = _generate_otp()
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "UPDATE tenant_applications SET otp_code_hash=%s, otp_expires_at=NOW() + (%s * INTERVAL '1 minute'), "
        "otp_attempts=0, updated_at=NOW() WHERE id=%s",
        (_hash_token(otp_code), _OTP_TTL_MINUTES, application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    send_org_signup_otp_email(application["admin_email"], application["company_name"], otp_code)
    flash("A new code has been sent to your email.", "info")
    return redirect(f"/create_org/verify_otp?application_id={application['id']}")


@org_bp.route("/create_org/upload_documents", methods=["GET"])
def create_org_upload_documents_page():
    # token: carries a re-upload magic link through from create_org_status.html's
    # rejected branch (a rejected application's original session is very
    # often long gone by the time someone acts on the rejection email) --
    # forwarded into the form as a hidden field so the POST below can use
    # it too. Falls back to nothing for the normal same-session flow, which
    # doesn't need it (session["org_access_token"] already covers that).
    return render_template("create_org_upload_documents.html",
                           application_id=request.args.get("application_id", ""),
                           token=request.args.get("token", ""),
                           free_registration=session.get("org_free_registration", False))


@org_bp.route("/create_org/upload_documents", methods=["POST"])
def create_org_upload_documents():
    application_id = request.form.get("application_id", "")
    access_token = session.get("org_access_token") or request.form.get("token", "")
    application = _load_application(application_id, access_token)
    if not application:
        flash("Application not found. Please start again.", "error")
        return redirect("/create_org")

    ok, error = _save_application_documents(application, request.files)
    if not ok:
        flash(error, "error")
        _token_qs = f"&token={request.form.get('token', '')}" if request.form.get("token") else ""
        return redirect(f"/create_org/upload_documents?application_id={application['id']}{_token_qs}")

    # Every self-serve application reaches status='provisioned' right after
    # OTP now (see _provision_application_instantly()) -- there's nothing
    # left to "wait" for once documents are in, so send them straight to
    # their live company's status/login page instead of the old
    # please-wait screen, which only still applies to a legacy in-flight
    # application that somehow reached here pre-provisioned.
    if application["status"] == "provisioned":
        flash("Documents submitted -- your company is live. Log in below.", "info")
        return redirect(f"/create_org/status/{application['id']}")
    return redirect(f"/create_org/pending?application_id={application['id']}")


@org_bp.route("/create_org/pending", methods=["GET"])
def create_org_pending_page():
    return render_template("create_org_pending.html", application_id=request.args.get("application_id", ""))


@org_bp.route("/create_org/status/<int:application_id>", methods=["GET"])
def create_org_status_page(application_id):
    """Lets a returning registrant check their application's status later
    (e.g. from a link in the approval/rejection email) without needing a
    live session -- the token travels in the URL for this one read-only,
    self-scoped endpoint (a deliberate, common "magic link" tradeoff; it
    reveals only the requester's own application, nothing about anyone
    else's). session's org_access_token is tried first so the same-tab
    "just finished uploading, want to see it" case never needs a token
    in the URL at all.

    The URL token is tried before the session's: approval/rejection both
    rotate the application's token, so a session token saved at signup may
    be stale while the emailed link is current. A session that already
    proved access on the pay/trial page (_verify_application_access) is
    also accepted, so that page's redirect here never needs a token."""
    application, access_token = None, ""
    for candidate in (request.args.get("token", ""), session.get("org_access_token", "")):
        if candidate:
            application = _load_application(application_id, candidate)
            if application:
                access_token = candidate
                break
    if not application and session.get("org_verified_application_id") == application_id:
        application = _fetch_application_row(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/create_org")
    portal_url = f"{_safe_app_url()}/{application['subdomain']}/login" if application["status"] == "provisioned" else None
    return render_template("create_org_status.html", application=application, portal_url=portal_url,
                           access_token=access_token)


def _load_own_tenant_application():
    """Looks up the logged-in admin's own tenant's tenant_applications row
    by session identity (g.tenant_db) rather than a token -- unlike
    _load_application() above, which is for the pre-login/token-based
    magic-link flow. Used by /settings/company_verification below, the
    in-portal counterpart that exists because provisioning no longer waits
    on KYC review: an admin can be fully logged in with their documents
    still unreviewed and needs a way back to that step that doesn't depend
    on finding the original signup email. Returns None if none found
    (shouldn't happen for a real tenant, but fails soft rather than 500ing
    a settings page over it)."""
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute(
        "SELECT ta.id, ta.company_name, ta.subdomain, ta.admin_username, ta.admin_email, ta.admin_password_hash, "
        "ta.email_domain, ta.employee_count, ta.payment_option, ta.logo_path, ta.access_token_hash, ta.otp_code_hash, "
        "ta.otp_expires_at, ta.otp_attempts, ta.email_verified_at, ta.doc_registration_cert, ta.doc_address_proof, "
        "ta.doc_visiting_card, ta.doc_name_board_photo, ta.documents_submitted_at, ta.status, ta.reviewed_by, "
        "ta.reviewed_at, ta.rejection_reason, ta.tenant_id, ta.doc_review_status, "
        "t.created_at, t.gst_number, t.pan, t.cin "
        "FROM tenant_applications ta JOIN tenants t ON ta.tenant_id=t.id "
        "WHERE t.db_name=%s ORDER BY ta.id DESC LIMIT 1",
        (g.tenant_db,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return None
    cols = ["id", "company_name", "subdomain", "admin_username", "admin_email", "admin_password_hash",
            "email_domain", "employee_count", "payment_option", "logo_path", "access_token_hash", "otp_code_hash",
            "otp_expires_at", "otp_attempts", "email_verified_at", "doc_registration_cert", "doc_address_proof",
            "doc_visiting_card", "doc_name_board_photo", "documents_submitted_at", "status", "reviewed_by",
            "reviewed_at", "rejection_reason", "tenant_id", "doc_review_status",
            # Added for the Profile page's Company Info tab (account creation
            # date + KYC dedup identifiers) -- from `tenants`, not
            # `tenant_applications`, since that's where they're backfilled to.
            "tenant_created_at", "gst_number", "pan", "cin"]
    return dict(zip(cols, row))


@org_bp.route("/settings/company_verification", methods=["GET"])
@admin_required
def company_verification_page():
    application = _load_own_tenant_application()
    if not application:
        flash("No company verification record found.", "error")
        return redirect(tpath("/admin"))
    return render_template(
        "company_verification.html", application=application, doc_kinds=_APPLICATION_DOC_KINDS,
    )


@org_bp.route("/settings/company_verification", methods=["POST"])
@admin_required
def company_verification_upload():
    application = _load_own_tenant_application()
    if not application:
        flash("No company verification record found.", "error")
        return redirect(tpath("/admin"))

    ok, error = _save_application_documents(application, request.files)
    if not ok:
        flash(error, "error")
    else:
        flash("Documents submitted -- your account is fully active again.", "success")
    return redirect(tpath("/settings/company_verification"))


_DOC_STEP_STATUS = {"approved": "done", "pending_review": "review", "rejected": "rejected"}


def registration_checklist(tenant_db):
    """What a company still has to do after registering -- signup itself
    only asks for its name and the admin's account, so these come from the
    dashboard: a list of {"key", "status"} for "company" (GSTIN, via
    save_company_details), "documents" (_save_application_documents) and
    "payment" (auto-debit, Settings > Billing). status is "done" or "todo";
    documents can also be "review" (submitted, awaiting review -- nothing
    for the company to do) or "rejected" (with "reason"). Payment is left
    out for a platform admin's free registration, billed by manual invoice.

    None for a company with no application at all (Platform Admin's direct
    create tool, or registered before company verification existed):
    nothing to finish. Shared by admin_base.html's checklist (app.py's
    inject_registration_checklist) and the app's /api/admin/registration."""
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    try:
        mcur.execute(
            "SELECT ta.doc_review_status, ta.rejection_reason, ta.free_registration, t.gst_number, "
            "t.subscription_status FROM tenant_applications ta JOIN tenants t ON ta.tenant_id=t.id "
            "WHERE t.db_name=%s ORDER BY ta.id DESC LIMIT 1",
            (tenant_db,)
        )
        row = mcur.fetchone()
        if not row:
            return None
        doc_status, rejection_reason, free_registration, gst_number, subscription_status = row
        mandate_status = None
        if not free_registration:
            mcur.execute("SELECT status FROM auto_debit_mandates WHERE tenant_schema=%s", (tenant_db,))
            mandate = mcur.fetchone()
            mandate_status = mandate[0] if mandate else None
    finally:
        mcur.close()
        mconn.close()

    doc_step_status = _DOC_STEP_STATUS.get(doc_status, "todo")
    steps = [
        {"key": "company", "status": "done" if gst_number else "todo"},
        {"key": "documents", "status": doc_step_status,
         "reason": rejection_reason if doc_step_status == "rejected" else None},
    ]
    if not free_registration:
        paid = mandate_status in ("active", "pending_cancellation") or subscription_status == "active"
        steps.append({"key": "payment", "status": "done" if paid else "todo"})
    return steps


def _own_tenant():
    """The logged-in company's own tenants row: (id, company_name,
    subdomain, gst_number), or None."""
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT id, company_name, subdomain, gst_number FROM tenants WHERE db_name=%s", (g.tenant_db,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def save_company_details(tenant, fields, logo_file):
    """The registration checklist's "Company details" step (web
    /settings/company_details, the app's /api/admin/registration/company):
    the GSTIN -- required, and must not already belong to another company
    (check_duplicate_gst) -- and an optional logo. The GSTIN goes on the
    tenants row, where duplicate checks read it, and on the company's
    application; the logo becomes company_settings.logo_url. `tenant` is
    _own_tenant()'s row. Returns an error message, or None."""
    from database import get_master_db, get_db_connection
    from utils.helpers import save_uploaded_logo, invalidate_settings_cache

    tenant_id, company_name, slug, _current_gst = tenant
    gst_number = _clean_gst(fields.get("gst_number") or "")
    error = _validate_gst_number(gst_number)
    if error:
        return error
    conflicting = check_duplicate_gst(gst_number)
    if conflicting and conflicting[0] != tenant_id:
        application = _load_own_tenant_application()
        _record_duplicate_alert(application["id"] if application else None, company_name,
                                application["admin_email"] if application else "", conflicting, match_type="gst")
        return _GENERIC_DUPLICATE_MSG

    logo_url = None
    if logo_file and logo_file.filename:
        logo_path, logo_err = save_uploaded_logo(logo_file, slug)
        if logo_err:
            return f"Company logo: {logo_err}"
        logo_url = f"/static/{logo_path}"

    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("UPDATE tenants SET gst_number=%s WHERE id=%s", (gst_number, tenant_id))
    mcur.execute("UPDATE tenant_applications SET gst_number=%s, updated_at=NOW() WHERE tenant_id=%s",
                 (gst_number, tenant_id))
    mconn.commit()
    mcur.close()
    mconn.close()
    if logo_url:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE company_settings SET logo_url=%s WHERE id=1", (logo_url,))
        conn.commit()
        cur.close()
        conn.close()
        invalidate_settings_cache()
    log_security_event("org.company_details_saved", f"Company details saved for '{company_name}'",
                       level="INFO", subdomain=slug, tenant_id=tenant_id, logo_changed=bool(logo_url))
    return None


@org_bp.route("/api/admin/registration", methods=["GET"])
@api_required
@api_role_required("admin")
def api_admin_registration():
    """The app's registration checklist (registration_checklist()), for the
    company its /<company_slug>/ prefix resolved to. steps is empty once
    there is nothing left to finish."""
    tenant = _own_tenant()
    if not tenant:
        return jsonify({"ok": False, "msg": "No company record found."}), 404
    steps = registration_checklist(g.tenant_db) or []
    return jsonify({
        "ok": True, "company_name": tenant[1], "company_slug": tenant[2], "gst_number": tenant[3] or "",
        "portal_url": f"{_safe_app_url()}/{tenant[2]}/login",
        "steps": [] if all(step["status"] == "done" for step in steps) else steps,
    })


@org_bp.route("/api/admin/registration/company", methods=["POST"])
@api_required
@api_role_required("admin")
def api_admin_registration_company():
    """The checklist's company details from the app: gst_number, as JSON
    or as multipart fields when the optional logo comes along."""
    tenant = _own_tenant()
    if not tenant:
        return jsonify({"ok": False, "msg": "No company record found."}), 404
    data = request.form if request.form else (request.get_json(silent=True) or {})
    error = save_company_details(tenant, data, request.files.get("logo"))
    if error:
        return jsonify({"ok": False, "msg": error}), 400
    return jsonify({"ok": True, "msg": "Company details saved."})


@org_bp.route("/api/admin/registration/documents", methods=["POST"])
@api_required
@api_role_required("admin")
def api_admin_registration_documents():
    """The checklist's business documents from the app (multipart, the same
    four files as /settings/company_verification)."""
    application = _load_own_tenant_application()
    if not application:
        return jsonify({"ok": False, "msg": "No company verification record found."}), 404
    ok, error = _save_application_documents(application, request.files)
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400
    return jsonify({"ok": True, "msg": "Documents submitted -- we'll review them shortly."})


@org_bp.route("/settings/company_details", methods=["GET"])
@admin_required
def company_details_page():
    tenant = _own_tenant()
    if not tenant:
        flash("No company record found.", "error")
        return redirect(tpath("/admin"))
    return render_template("company_details.html", gst_number=tenant[3] or "",
                           portal_url=f"{_safe_app_url()}/{tenant[2]}/login")


@org_bp.route("/settings/company_details", methods=["POST"])
@admin_required
def company_details_save():
    tenant = _own_tenant()
    if not tenant:
        flash("No company record found.", "error")
        return redirect(tpath("/admin"))
    error = save_company_details(tenant, request.form, request.files.get("logo"))
    if error:
        flash(error, "error")
        return redirect(tpath("/settings/company_details"))
    flash("Company details saved.", "success")
    return redirect(tpath("/admin"))


@org_bp.route("/create_org/pay/<int:application_id>", methods=["GET"])
def create_org_pay_page(application_id):
    """Landing page linked from the approval email for online-payment
    applications (blueprints/platform_admin.py's approve action, once
    payment_option=='online') -- pre-filled/locked to this one approved
    application rather than an editable signup form, since every other
    field was already collected and reviewed. Requires the application's
    access token (from the emailed link, or the signup session) so a
    sequential application_id alone reveals nothing; 404 otherwise."""
    if not _verify_application_access(application_id):
        abort(404)
    if request.args.get("token"):
        return redirect(f"/create_org/pay/{application_id}")  # drop the token from the address bar
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT company_name, status FROM tenant_applications WHERE id=%s", (application_id,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        flash("Application not found.", "error")
        return redirect("/create_org")
    company_name, status = row
    if status != "approved_pending_payment":
        return redirect(f"/create_org/status/{application_id}")
    return render_template(
        "create_org_pay.html", application_id=application_id, company_name=company_name,
        per_employee_paise=get_per_employee_paise(),
    )


@org_bp.route("/create_org/setup_trial/<int:application_id>", methods=["GET"])
def create_org_setup_trial_page(application_id):
    """Landing page linked from the approval email for trial applications
    (blueprints/platform_admin.py's approve action, once payment_option==
    'trial') -- same shape as create_org_pay_page above, just collecting a
    payment MANDATE (authorized now, not charged) instead of an immediate
    payment. Provisioning happens once create_org_setup_trial_confirm()
    below verifies the authorization -- the trial clock itself only starts
    later, at first login (see app.py's inject_billing_lock_status()).
    Same access-token requirement as create_org_pay_page()."""
    if not _verify_application_access(application_id):
        abort(404)
    if request.args.get("token"):
        return redirect(f"/create_org/setup_trial/{application_id}")
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "SELECT company_name, status, payment_option FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        flash("Application not found.", "error")
        return redirect("/create_org")
    company_name, status, payment_option = row
    if payment_option != "trial" or status != "approved_pending_payment":
        return redirect(f"/create_org/status/{application_id}")
    return render_template(
        "create_org_setup_trial.html", application_id=application_id, company_name=company_name,
        per_employee_paise=get_per_employee_paise(),
    )


@org_bp.route("/api/create_org/setup_trial/<int:application_id>/create_subscription", methods=["POST"])
@limiter.limit("10 per minute")
def create_org_setup_trial_create_subscription(application_id):
    """Authorizes (but does not charge) a Razorpay Subscription for a
    trial applicant -- same create_id_or_demo/create_customer/
    create_subscription calls blueprints/auto_debit.py's enroll() makes for
    an already-provisioned tenant, reused here (via that module's plan-id
    cache) before the tenant schema even exists yet. Quantity starts at 1
    as a placeholder -- there are no employees yet, and the recurring daily
    sync (blueprints/auto_debit.py's sync_and_bill_auto_debit(), or the
    one-off sync in trial_billing.py's check_trial_expirations()) corrects
    it to the real headcount well before the trial ends and any charge
    happens."""
    if not _verify_application_access(application_id):
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    from database import get_master_db
    from blueprints.auto_debit import _get_or_create_plan_id, _DEMO_SUBSCRIPTION_PREFIX
    from utils.razorpay_utils import create_customer, create_subscription, create_id_or_demo, key_id as razorpay_key_id

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute(
        "SELECT company_name, admin_email, status, payment_option FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    company_name, admin_email, status, payment_option = row
    if payment_option != "trial" or status != "approved_pending_payment":
        return jsonify({"ok": False, "msg": "This application isn't ready for trial setup."}), 400

    def _create_real_subscription():
        plan_id, error = _get_or_create_plan_id()
        if error:
            return None, error
        customer_id, error = create_customer(company_name, admin_email)
        if error:
            return None, error
        return create_subscription(plan_id, customer_id, 1)

    subscription_id, is_demo, error = create_id_or_demo(_DEMO_SUBSCRIPTION_PREFIX, _create_real_subscription)
    if error:
        app_log.error("create_org_setup_trial_create_subscription: subscription setup failed: %s", error)
        return jsonify({"ok": False, "msg": "Could not start trial setup. Please try again."}), 502

    # Bind this exact subscription to this application: the confirm step
    # below only accepts the id recorded here, so a validly-signed
    # authorization for some OTHER subscription can't provision it.
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "UPDATE tenant_applications SET pending_subscription_id=%s, updated_at=NOW() "
        "WHERE id=%s AND status='approved_pending_payment'",
        (subscription_id, application_id)
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    return jsonify({
        "ok": True, "subscription_id": subscription_id, "key_id": razorpay_key_id(), "demo": is_demo,
    })


@org_bp.route("/api/create_org/setup_trial/<int:application_id>/confirm", methods=["POST"])
@limiter.limit("20 per minute")
def create_org_setup_trial_confirm(application_id):
    """Verifies the subscription-authorization callback (same signature
    check blueprints/auto_debit.py's confirm() uses for an existing
    tenant), then provisions the tenant -- provision_tenant() itself sets
    subscription_status='trialing' for payment_option=='trial' (dates left
    NULL until first login). The authorized mandate is armed and waiting
    (auto_debit_mandates.status='active') but nothing is charged until
    blueprints/trial_billing.py's check_trial_expirations() runs, which
    only ever fires once trial_end_date is actually set and past."""
    from database import get_master_db
    from blueprints.auto_debit import _DEMO_SUBSCRIPTION_PREFIX
    from utils.razorpay_utils import verify_subscription_signature, verify_or_demo

    data = request.get_json(silent=True) or request.form
    subscription_id = (data.get("razorpay_subscription_id") or "").strip()
    payment_id = (data.get("razorpay_payment_id") or "").strip()
    signature = (data.get("razorpay_signature") or "").strip()
    if not subscription_id:
        return jsonify({"ok": False, "msg": "Missing subscription id."}), 400

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute(
        "SELECT company_name, subdomain, admin_username, admin_email, admin_password_hash, email_domain, "
        "logo_path, status, payment_option, gst_number, pending_subscription_id "
        "FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    (company_name, subdomain, admin_username, admin_email, admin_password_hash, email_domain,
     logo_path, status, payment_option, gst_number, pending_subscription_id) = row

    # Idempotent: a retried/duplicate client POST for an already-provisioned
    # application must not attempt to re-provision.
    if status == "provisioned":
        portal_url = f"{_safe_app_url()}/{subdomain}/login"
        return jsonify({"ok": True, "portal_url": portal_url, "already_provisioned": True})
    if payment_option != "trial" or status != "approved_pending_payment":
        return jsonify({"ok": False, "msg": "This application isn't ready for trial setup."}), 400
    if not pending_subscription_id or not secrets.compare_digest(
            str(pending_subscription_id), subscription_id):
        log_security_event(
            "org.trial_subscription_mismatch",
            "Trial confirm presented a subscription that was not created for this application",
            level="ERROR", application_id=application_id,
        )
        return jsonify({"ok": False, "msg": "Payment verification failed."}), 400

    ok, is_demo, error = verify_or_demo(
        subscription_id, _DEMO_SUBSCRIPTION_PREFIX, payment_id, signature, verify_subscription_signature
    )
    if not ok:
        if not is_demo:
            log_security_event(
                "org.trial_signature_invalid", "Razorpay subscription-authorization signature verification failed",
                level="ERROR", subscription_id=subscription_id, application_id=application_id,
            )
        return jsonify({"ok": False, "msg": error}), 400

    ok, error, portal_url, checkin_url = provision_tenant(
        company_name, subdomain, admin_username, admin_password_hash, admin_email,
        payment_option="trial", email_domain=email_domain, logo_path=logo_path, gst_number=gst_number,
    )
    if not ok:
        app_log.error("create_org_setup_trial_confirm: provisioning failed for application %s: %s", application_id, error)
        return jsonify({"ok": False, "msg": f"Trial setup failed: {error}. Contact support."}), 500

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT id, db_name FROM tenants WHERE subdomain=%s", (subdomain,))
    tenant_row = mcur.fetchone()
    tenant_id, tenant_db_name = tenant_row if tenant_row else (None, None)
    mcur.execute(
        "UPDATE tenant_applications SET status='provisioned', tenant_id=%s, updated_at=NOW() WHERE id=%s",
        (tenant_id, application_id)
    )
    # Mandate is authorized and ready, but the tenant has no employees yet
    # (schema was just created) -- quantity_synced starts at 0 so the very
    # first daily sync (or trial_billing.py's own one-off sync at trial end)
    # PATCHes it to whatever headcount actually exists by then.
    if tenant_db_name:
        mcur.execute(
            "INSERT INTO auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
            "razorpay_subscription_id, quantity_synced, status, requested_by, created_at, activated_at) "
            "VALUES (%s, %s, NULL, %s, 0, 'active', %s, NOW(), NOW()) "
            "ON CONFLICT (tenant_schema) DO UPDATE SET razorpay_subscription_id=%s, status='active', "
            "activated_at=NOW(), cancelled_at=NULL",
            (tenant_db_name, company_name, subscription_id, admin_username, subscription_id)
        )
    mconn.commit()
    mcur.close()
    mconn.close()

    if is_demo:
        log_security_event(
            "org.trial_demo_enrolled", "Demo/test-mode trial mandate authorized (no real mandate)",
            level="INFO", subscription_id=subscription_id, application_id=application_id,
        )
    log_security_event(
        "org.trial_tenant_provisioned", f"Trial tenant '{company_name}' provisioned, trial clock starts at first login",
        level="INFO", subdomain=subdomain, application_id=application_id, tenant_id=tenant_id,
    )
    send_portal_ready_email(admin_email, company_name, admin_username, portal_url, checkin_url=checkin_url)

    return jsonify({"ok": True, "portal_url": portal_url})


@org_bp.route("/api/create_org", methods=["POST"])
@limiter.limit("10 per hour")
def api_create_org():
    """JSON step 1 of the gated signup flow (mobile app's company
    registration screen). Mirrors /create_org's validation, duplicate-name
    check, and pending-application creation -- this is a second,
    independent path into the same logic and must not be a softer bypass
    of those protections. Unlike the old version of this route, this no
    longer provisions a tenant -- it returns an application_id + access_token
    that the app must carry through verify_otp -> upload_documents ->
    status (there's no session cookie on this side to hold it implicitly,
    the way the web flow's does).

    Current app builds send what the web form does -- company_name,
    admin_email, admin_mobile, admin_password -- and the code step creates
    the company and signs the admin in (api_create_org_verify_otp). A
    request carrying a subdomain is an older build sending everything at
    once (URL, GSTIN, username), which still works the old way: its
    company is provisioned at the code step, documents follow."""
    try:
        data = request.get_json() or {}
        if "subdomain" not in data:
            return _api_start_account(data)
        company_name = data.get("company_name", "").strip()
        raw_subdomain = data.get("subdomain", "").strip().lower()
        subdomain = re.sub(r'[^a-z0-9\-]', '-', raw_subdomain).strip('-')
        admin_username = data.get("admin_username", "").strip()
        admin_password = data.get("admin_password", "").strip()
        admin_email = data.get("admin_email", "").strip()
        email_domain = clean_email_domain(data.get("email_domain", ""))
        gst_number = _clean_gst(data.get("gst_number", ""))

        error = _validate_new_tenant_fields(company_name, subdomain, admin_username, admin_password, admin_email,
                                            email_domain)
        if error:
            return jsonify({"ok": False, "msg": error}), 400

        gst_error = _validate_gst_number(gst_number)
        if gst_error:
            return jsonify({"ok": False, "msg": gst_error}), 400

        conflicting, match_type = check_duplicate_name(company_name)
        if not conflicting:
            conflicting, match_type = check_duplicate_gst(gst_number), "gst"
        if not conflicting:
            conflicting, match_type = check_duplicate_admin_email(admin_email), "email"
        if conflicting:
            _record_duplicate_alert(None, company_name, admin_email, conflicting, match_type=match_type)
            return jsonify({"ok": False, "msg": _GENERIC_DUPLICATE_MSG}), 409

        from utils.razorpay_utils import razorpay_configured
        payment_option = "online" if razorpay_configured() else "manual"

        access_token = _generate_access_token()
        otp_code = _generate_otp()
        from database import get_master_db
        mconn = get_master_db()
        mcur = mconn.cursor()
        mcur.execute(
            "INSERT INTO tenant_applications (company_name, subdomain, admin_username, admin_email, "
            "admin_password_hash, email_domain, gst_number, payment_option, access_token_hash, otp_code_hash, "
            "otp_expires_at, source_ip) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW() + (%s * INTERVAL '1 minute'), %s) "
            "RETURNING id",
            (company_name, subdomain, admin_username, admin_email, generate_password_hash(admin_password),
             email_domain, gst_number, payment_option, _hash_token(access_token), _hash_token(otp_code),
             _OTP_TTL_MINUTES, request.remote_addr)
        )
        application_id = mcur.fetchone()[0]
        mconn.commit()
        mcur.close()
        mconn.close()

        send_org_signup_otp_email(admin_email, company_name, otp_code)

        return jsonify({
            "ok": True,
            "msg": f"We've emailed a verification code to {admin_email}.",
            "application_id": application_id,
            "access_token": access_token,
            "step": "otp",
        })
    except Exception as global_exc:
        app_log.error("api_create_org global exception: %s", global_exc)
        return jsonify({"ok": False, "msg": f"Server error: {global_exc}"}), 500


def _api_start_account(data):
    """api_create_org()'s signup step: the same checks as the web form's
    create_org()."""
    company_name = re.sub(r'\s+', ' ', str(data.get("company_name") or "")).strip()
    admin_email = (data.get("admin_email") or "").strip().lower()
    admin_mobile = _clean_mobile(data.get("admin_mobile"))
    admin_password = (data.get("admin_password") or "").strip()
    error = _check_new_signup(company_name, admin_email, admin_mobile, admin_password)
    if error:
        return jsonify({"ok": False, "msg": error}), 409 if error == _GENERIC_DUPLICATE_MSG else 400
    application_id, access_token = _start_signup_application(company_name, admin_email, admin_mobile,
                                                             admin_password)
    return jsonify({
        "ok": True,
        "msg": f"We've emailed a verification code to {admin_email}.",
        "application_id": application_id,
        "access_token": access_token,
        "step": "otp",
    })


@org_bp.route("/api/create_org/verify_otp", methods=["POST"])
@limiter.limit("20 per hour")
def api_create_org_verify_otp():
    data = request.get_json() or {}
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    if not application:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    if application["status"] == "otp_verified" and not application["company_name"]:
        return jsonify({"ok": True, "step": "profile", "msg": "Email already verified."})

    ok, error = _verify_application_otp(application, str(data.get("otp_code", "")).strip())
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400

    # Verified before step 1 asked for the company name: the old profile
    # step (/api/create_org/profile) still finishes it.
    if not application["company_name"]:
        return jsonify({"ok": True, "step": "profile", "msg": "Email verified -- now set up your company."})

    # Re-fetch: _verify_application_otp() just advanced status in the DB,
    # but `application` here is still the pre-verification snapshot.
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    if not application["subdomain"]:
        return _api_create_company_and_sign_in(application)

    # An older app build's one-request signup already chose its URL.
    ok, error, portal_url, checkin_url = _provision_application_instantly(application)
    if not ok:
        app_log.error("api_create_org_verify_otp: instant provisioning failed for application %s: %s",
                      application["id"], error)
        return jsonify({"ok": False, "msg": f"Could not set up your company: {error}. Contact support."}), 500

    return jsonify({"ok": True, "step": "documents", "portal_url": portal_url, "checkin_url": checkin_url})


def _api_create_company_and_sign_in(application):
    """The app's counterpart of create_org_verify_otp()'s tail: creates the
    company (_create_signup_company) and signs its admin in with a Bearer
    token issued in the new company's schema. The app sends every later
    request under /<company_slug>/ -- tenant routing then resolves the
    company from that prefix, as it does for the website -- and finishes
    the registration checklist from its dashboard (/api/admin/registration).
    The token counts as MFA-verified: the code just checked was emailed to
    this admin's address, the same factor /api/login/verify accepts."""
    from blueprints.core import _issue_admin_api_token
    from database import get_master_db

    ok, error, application = _create_signup_company(application)
    if not ok:
        app_log.warning("api_create_org_verify_otp: could not create the company for application %s: %s",
                        application and application["id"], error)
        if error == _GENERIC_DUPLICATE_MSG:
            return jsonify({"ok": False, "msg": error}), 409
        return jsonify({"ok": False, "msg": f"Could not set up your company: {error}. Contact support."}), 500

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT db_name, subdomain, company_name FROM tenants WHERE id=%s", (application["tenant_id"],))
    db_name, slug, company_name = mcur.fetchone()
    mcur.close()
    mconn.close()

    g.tenant_db = db_name
    username = application["admin_username"]
    token = _issue_admin_api_token(username, mfa_verified=True)
    log_security_event(
        "auth.admin_login_success", f"App sign-in for '{username}' (role=admin) right after registering",
        level="INFO", identifier=username, subdomain=slug,
    )
    return jsonify({
        "ok": True, "step": "done", "token": token, "username": username, "role": "admin",
        "company_slug": slug, "company_name": company_name,
        "portal_url": f"{_safe_app_url()}/{slug}/login",
    })


@org_bp.route("/api/create_org/profile", methods=["POST"])
@limiter.limit("20 per hour")
def api_create_org_profile():
    """The mobile app's company profile step (see _save_company_profile):
    application_id, access_token, company_name, gst_number, subdomain,
    signup_plan, as JSON -- or multipart form fields when the optional
    logo file comes along."""
    data = request.form if request.form else (request.get_json(silent=True) or {})
    application = _load_profile_application(data.get("application_id"), data.get("access_token", ""))
    if not application:
        return jsonify({"ok": False, "msg": "Verify your email first, or start a new registration."}), 400
    ok, error, portal_url, checkin_url = _save_company_profile(application, data, request.files.get("logo"))
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400
    return jsonify({"ok": True, "step": "documents", "portal_url": portal_url, "checkin_url": checkin_url})


@org_bp.route("/api/create_org/resend_otp", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def api_create_org_resend_otp():
    data = request.get_json() or {}
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    if not application or application["status"] != "started":
        return jsonify({"ok": False, "msg": "This application can no longer receive a new code."}), 400

    otp_code = _generate_otp()
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "UPDATE tenant_applications SET otp_code_hash=%s, otp_expires_at=NOW() + (%s * INTERVAL '1 minute'), "
        "otp_attempts=0, updated_at=NOW() WHERE id=%s",
        (_hash_token(otp_code), _OTP_TTL_MINUTES, application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    send_org_signup_otp_email(application["admin_email"], application["company_name"], otp_code)
    return jsonify({"ok": True, "msg": "A new code has been sent to your email."})


@org_bp.route("/api/create_org/upload_documents", methods=["POST"])
def api_create_org_upload_documents():
    application = _load_application(request.form.get("application_id"), request.form.get("access_token", ""))
    if not application:
        return jsonify({"ok": False, "msg": "Application not found."}), 404

    ok, error = _save_application_documents(application, request.files)
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400

    if application["status"] == "provisioned":
        portal_url = f"{_safe_app_url()}/{application['subdomain']}/login"
        return jsonify({"ok": True, "step": "provisioned", "portal_url": portal_url,
                        "msg": "Documents submitted -- your company is live. Log in below."})
    return jsonify({"ok": True, "step": "pending_review",
                    "msg": "Your application has been submitted and is under review. We'll email you once it's approved."})


@org_bp.route("/api/create_org/status/<int:application_id>", methods=["GET"])
def api_create_org_status(application_id):
    access_token = request.args.get("access_token", "")
    application = _load_application(application_id, access_token)
    if not application:
        return jsonify({"ok": False, "msg": "Application not found."}), 404

    portal_url = None
    if application["status"] == "provisioned":
        portal_url = f"{_safe_app_url()}/{application['subdomain']}/login"

    return jsonify({
        "ok": True,
        "status": application["status"],
        "company_name": application["company_name"],
        "rejection_reason": application["rejection_reason"],
        "portal_url": portal_url,
    })


# /superadmin is superseded by /super_admin -- redirect for backwards compat
@org_bp.route("/superadmin")
def superadmin_redirect():
    return redirect("/super_admin/login")


@org_bp.route("/api/leads", methods=["POST"])
@limiter.limit("5 per minute")
def submit_lead():
    """"Request info" and "Contact us" forms on the public landing page
    (templates/landing.html) -- for visitors who want more details before
    self-registering via /create_org. Stored in att_master.leads, surfaced
    on the Platform Admin dashboard for manual follow-up, and emailed to the
    support inbox (utils/email_utils.py's support_inbox()). The Contact form
    adds a subject. Deliberately has no admin-facing error detail beyond
    "required" -- this is a public, unauthenticated endpoint."""
    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response") or (request.get_json(silent=True) or {}).get("cf_turnstile_response", "")
        if not verify_turnstile(token, request.remote_addr):
            return jsonify({"ok": False, "msg": "Captcha verification failed. Please try again."}), 400

    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()[:200]
    email = (data.get("email") or "").strip()[:200]
    phone = (data.get("phone") or "").strip()[:30] or None
    company_name = (data.get("company_name") or "").strip()[:200] or None
    subject = (data.get("subject") or "").strip()[:200]
    message = (data.get("message") or "").strip()[:2000] or None
    if subject:
        # leads has no subject column: the Contact form's subject heads the message.
        message = f"{subject}\n\n{message or ''}".strip()

    if not name or not email:
        return jsonify({"ok": False, "msg": "Name and email are required."}), 400
    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "msg": "Enter a valid email address."}), 400

    try:
        from database import get_master_db
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO leads (name, email, phone, company_name, message) VALUES (%s, %s, %s, %s, %s)",
            (name, email, phone, company_name, message)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.error("submit_lead failed: %s", exc)
        return jsonify({"ok": False, "msg": "Could not submit right now. Please try again."}), 500

    notify_support(
        f"New enquiry: {subject or company_name or name}"[:200],
        _enquiry_email("New enquiry from the website", [
            ("Name", name), ("Email", email), ("Phone", phone), ("Company", company_name),
        ], message),
    )
    return jsonify({"ok": True, "msg": "Thanks! We'll be in touch shortly."})


def _enquiry_email(heading, rows, message):
    """The support inbox's copy of a landing-page form. Every value is the
    visitor's own input, so all of it is escaped; their email is a mailto
    link, since a reply from the noreply mailbox would otherwise go nowhere."""
    esc = html.escape
    cells = []
    for label, value in rows:
        if not value:
            continue
        shown = (f'<a href="mailto:{esc(str(value), quote=True)}">{esc(str(value))}</a>'
                 if label == "Email" else esc(str(value)))
        cells.append(f'<tr><td style="padding:4px 12px 4px 0;color:#455163">{esc(label)}</td>'
                     f'<td style="padding:4px 0"><b>{shown}</b></td></tr>')
    body = (f'<p style="white-space:pre-wrap;background:#F5F8FC;border-radius:8px;padding:12px">{esc(message)}</p>'
            if message else "")
    return (f'<div style="font-family:Arial,sans-serif;color:#131C2A">'
            f'<h2 style="color:#0B5CAD;margin:0 0 12px">{esc(heading)}</h2>'
            f'<table style="border-collapse:collapse;font-size:14px">{"".join(cells)}</table>{body}'
            f'<p style="font-size:12px;color:#8794A5">Reply to the visitor at the email above.</p></div>')


_FEEDBACK_TYPES = frozenset({"Feature Request", "User Experience", "Biometrics", "General Praise"})


@org_bp.route("/api/feedback", methods=["POST"])
@limiter.limit("5 per minute")
def submit_feedback():
    """Product feedback / star-rating form on the public landing page
    (templates/landing.html's #feedback section) -- was previously a pure
    client-side fake-success simulation (the form was hidden and a "Thanks!"
    banner shown on submit with no request ever sent). Stored in
    att_master.feedback, surfaced read-only on the Platform Admin dashboard
    (blueprints/platform_admin.py's _recent_feedback()). Same public/
    unauthenticated/rate-limited/optional-Turnstile posture as submit_lead()
    above -- co-located here rather than in its own module since it's the
    same "public landing page form" family."""
    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response") or (request.get_json(silent=True) or {}).get("cf_turnstile_response", "")
        if not verify_turnstile(token, request.remote_addr):
            return jsonify({"ok": False, "msg": "Captcha verification failed. Please try again."}), 400

    data = request.get_json(silent=True) or request.form
    feedback_type = (data.get("feedback_type") or "").strip()
    if feedback_type not in _FEEDBACK_TYPES:
        feedback_type = "Feature Request"
    email = (data.get("email") or "").strip()[:200] or None
    message = (data.get("message") or "").strip()[:2000]
    rating_raw = data.get("rating")

    if not message:
        return jsonify({"ok": False, "msg": "Please enter your feedback."}), 400
    if email and not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "msg": "Enter a valid email address, or leave it blank."}), 400
    rating = None
    if rating_raw not in (None, "", "0"):
        try:
            rating = int(rating_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "Rating must be a number."}), 400
        if rating < 1 or rating > 5:
            return jsonify({"ok": False, "msg": "Rating must be between 1 and 5."}), 400

    try:
        from database import get_master_db
        conn = get_master_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO feedback (feedback_type, rating, email, message) VALUES (%s, %s, %s, %s)",
            (feedback_type, rating, email, message)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        app_log.error("submit_feedback failed: %s", exc)
        return jsonify({"ok": False, "msg": "Could not submit right now. Please try again."}), 500

    notify_support(
        f"New feedback: {feedback_type}" + (f" ({rating}/5)" if rating else ""),
        _enquiry_email("New product feedback from the website", [
            ("Type", feedback_type), ("Rating", f"{rating} of 5" if rating else None), ("Email", email),
        ], message),
    )
    return jsonify({"ok": True, "msg": "Thanks for the feedback! It goes straight to our product team."})
