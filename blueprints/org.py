# -*- coding: utf-8 -*-
"""Org blueprint -- multi-tenant org self-registration."""
import os
import re
import html
import secrets
import difflib
from flask import Blueprint, request, redirect, render_template, flash, jsonify, session, g
from extensions import app_log, limiter, log_security_event
from utils.auth import generate_password_hash, _hash_token, turnstile_enabled, verify_turnstile, _TURNSTILE_SITE_KEY, validate_new_password, admin_required
from utils.plan_limits import PLAN_LABEL, get_per_employee_paise, format_price_inr
from utils.email_utils import get_email_config, send_email_async
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


_PAYMENT_OPTIONS = frozenset({"online", "manual", "trial"})


def provision_tenant(company_name, subdomain, admin_username, admin_password_hash, admin_email,
                     payment_option="online", email_domain=None, employee_count=None, logo_path=None,
                     gst_number=None, subscription_status=None):
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
    reads it from there to require/check new employees' emails going forward.

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
        # path today (local-disk mode) but a full https:// URL once
        # S3_BUCKET is configured (utils/storage.py) -- forward-compatible
        # so activating S3 later doesn't silently produce a broken
        # "/static/https://..." URL here.
        if not logo_path:
            logo_url = None
        elif logo_path.startswith("http://") or logo_path.startswith("https://"):
            logo_url = logo_path
        else:
            logo_url = f"/static/{logo_path}"
        tcur.execute(
            "UPDATE company_settings SET company_name=%s, email_domain=%s, paid_employee_slots=%s, "
            "logo_url=COALESCE(%s, logo_url), setup_done=1 WHERE id=1",
            (company_name, clean_email_domain(email_domain) or None, employee_count, logo_url)
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


def send_portal_ready_email(admin_email, company_name, admin_username, portal_url, admin_password="", checkin_url=None):
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

    Deliberately NOT folded into _validate_new_tenant_fields() -- that
    function is also called by platform_admin_create_tenant(), where a
    platform admin legitimately re-creating/fixing a tenant must not be
    blocked by this. Only the gated step-1 signup route calls this."""
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
        _company = html.escape(str(company_name))
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
        "reviewed_at, rejection_reason, tenant_id, doc_review_status, gst_number "
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
            "reviewed_at", "rejection_reason", "tenant_id", "doc_review_status", "gst_number"]
    application = dict(zip(cols, row))
    if not secrets.compare_digest(application["access_token_hash"], _hash_token(access_token)):
        return None
    return application


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
    )


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    # Local-dev-only bypass: accepts any code (even blank) so the signup
    # flow can be tested end-to-end without real SMTP -- same APP_ENV gate
    # utils/helpers.py already uses for the malware-scan fail-open and the
    # missing-APP_URL warning. Never active unless APP_ENV=development, so
    # production (default "production") is unaffected.
    _dev_bypass = os.environ.get("APP_ENV", "production") == "development"
    if _dev_bypass:
        app_log.warning("_verify_application_otp: APP_ENV=development -- OTP check bypassed for application %s",
                        application["id"])
    code_matches = _dev_bypass or (bool(otp_code) and secrets.compare_digest(_hash_token(otp_code), stored_hash))
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
    """Shared with the APP_ENV=development skip-OTP path in create_org()
    below -- same DB transition, whether a real code was checked or the
    step was bypassed entirely for local testing."""
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

    ok, error, portal_url, checkin_url = provision_tenant(
        application["company_name"], application["subdomain"], application["admin_username"],
        application["admin_password_hash"], application["admin_email"],
        payment_option=application["payment_option"], email_domain=application["email_domain"],
        employee_count=application["employee_count"], logo_path=application["logo_path"],
        gst_number=application["gst_number"], subscription_status="trialing",
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
        f"Self-serve signup '{application['company_name']}' provisioned instantly "
        f"(payment_option={application['payment_option']}) -- KYC document review still pending, async",
        level="INFO", subdomain=application["subdomain"], application_id=application["id"], tenant_id=tenant_id,
    )
    send_portal_ready_email(
        application["admin_email"], application["company_name"], application["admin_username"],
        portal_url, checkin_url=checkin_url,
    )
    return True, None, portal_url, checkin_url


def _save_application_documents(application, files):
    """Shared document-upload logic for both web and mobile. `files` is a
    dict-like of {doc_kind: FileStorage} (request.files works directly).
    Returns (True, None) on success (advances the row to 'pending_review'),
    or (False, error_message).

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
        "UPDATE tenant_applications SET doc_registration_cert=%s, doc_address_proof=%s, doc_visiting_card=%s, "
        "doc_name_board_photo=%s, documents_submitted_at=NOW(), "
        "status = CASE WHEN status='provisioned' THEN status ELSE 'pending_review' END, "
        "doc_review_status='pending_review', updated_at=NOW(), "
        "rejection_reason=NULL, reviewed_by=NULL, reviewed_at=NULL "
        "WHERE id=%s",
        (saved_paths["registration_cert"], saved_paths["address_proof"], saved_paths["visiting_card"],
         saved_paths["name_board_photo"], application["id"])
    )
    mconn.commit()
    mcur.close()
    mconn.close()
    return True, None


@org_bp.route("/create_org", methods=["POST"])
@limiter.limit("10 per hour")
def create_org():
    """Step 1 of the gated signup flow: validate fields, block a
    duplicate company name, and -- if clear -- create a pending
    tenant_applications row and email an OTP. No tenant is provisioned
    here anymore; provision_tenant() is only ever called later, from
    platform_admin.py's approve action (or billing.py's verify_payment
    for paid plans, once payment completes after that approval). This
    replaces the old instant-provisioning behavior entirely -- since
    something like a name-board photo can't be validated by code, manual
    review is a required step, not an optional hardening layer that's
    safe to bypass in dev (unlike the Turnstile captcha below, which
    fails open when unconfigured)."""
    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response", "")
        if not verify_turnstile(token, request.remote_addr):
            flash("Captcha verification failed. Please try again.", "error")
            return redirect("/create_org")

    company_name = request.form.get("company_name", "").strip()
    subdomain = _clean_subdomain_slug(request.form.get("subdomain", ""), company_name)
    admin_username = request.form.get("admin_username", "").strip()
    admin_password = request.form.get("admin_password", "").strip()
    admin_email = request.form.get("admin_email", "").strip()
    email_domain = clean_email_domain(request.form.get("email_domain", ""))
    gst_number = _clean_gst(request.form.get("gst_number", ""))

    error = _validate_new_tenant_fields(company_name, subdomain, admin_username, admin_password, admin_email,
                                        email_domain)
    if error:
        flash(error, "error")
        return redirect("/create_org")

    gst_error = _validate_gst_number(gst_number)
    if gst_error:
        flash(gst_error, "error")
        return redirect("/create_org")

    conflicting, match_type = check_duplicate_name(company_name)
    if not conflicting:
        conflicting, match_type = check_duplicate_gst(gst_number), "gst"
    if not conflicting:
        conflicting, match_type = check_duplicate_admin_email(admin_email), "email"
    if conflicting:
        _record_duplicate_alert(None, company_name, admin_email, conflicting, match_type=match_type)
        flash(_GENERIC_DUPLICATE_MSG, "error")
        return redirect("/create_org")

    logo_path = None
    logo_file = request.files.get("logo")
    if logo_file and logo_file.filename:
        from utils.helpers import save_uploaded_logo
        logo_path, logo_err = save_uploaded_logo(logo_file, subdomain)
        if logo_err:
            flash(f"Company logo: {logo_err}", "error")
            return redirect("/create_org")

    from utils.razorpay_utils import razorpay_configured
    # "trial" is only ever chosen explicitly on the signup form (a "Start a
    # free trial" option, blueprints/trial_billing.TRIAL_DURATION_DAYS long,
    # alongside "Pay now") -- any caller that doesn't send signup_plan (the
    # mobile app, an older cached form) keeps the original online/manual
    # auto-detect behavior unchanged.
    signup_plan = request.form.get("signup_plan", "pay_now").strip().lower()
    if signup_plan == "trial":
        payment_option = "trial"
    else:
        payment_option = "online" if razorpay_configured() else "manual"

    access_token = _generate_access_token()
    otp_code = _generate_otp()
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "INSERT INTO tenant_applications (company_name, subdomain, admin_username, admin_email, "
        "admin_password_hash, email_domain, gst_number, payment_option, logo_path, access_token_hash, otp_code_hash, "
        "otp_expires_at, source_ip) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW() + (%s * INTERVAL '1 minute'), %s) "
        "RETURNING id",
        (company_name, subdomain, admin_username, admin_email, generate_password_hash(admin_password),
         email_domain, gst_number, payment_option, logo_path, _hash_token(access_token), _hash_token(otp_code),
         _OTP_TTL_MINUTES, request.remote_addr)
    )
    application_id = mcur.fetchone()[0]

    # access_token lives ONLY in the session cookie from here on for the
    # web flow -- never in a URL/query string, so it can't leak via
    # referrer headers or browser history. application_id (a bare
    # sequential id, not a secret) is fine to carry in the URL.
    session["org_access_token"] = access_token

    if os.environ.get("APP_ENV", "production") == "development":
        # Local-dev-only: skip the verify-email screen entirely instead of
        # just accepting any code on it -- there's no SMTP to receive a
        # real email against anyway. Same APP_ENV gate as everywhere else
        # in this codebase that relaxes local dev; production (default
        # "production") always goes through the real verify_otp step.
        app_log.warning("create_org: APP_ENV=development -- OTP screen skipped entirely for application %s",
                        application_id)
        _mark_application_otp_verified(application_id, mconn, mcur)
        return redirect(f"/create_org/upload_documents?application_id={application_id}")

    mconn.commit()
    mcur.close()
    mconn.close()

    send_org_signup_otp_email(admin_email, company_name, otp_code)
    return redirect(f"/create_org/verify_otp?application_id={application_id}")


@org_bp.route("/create_org/verify_otp", methods=["GET"])
def create_org_verify_otp_page():
    return render_template("create_org_verify_otp.html", application_id=request.args.get("application_id", ""))


@org_bp.route("/create_org/verify_otp", methods=["POST"])
@limiter.limit("20 per hour")
def create_org_verify_otp():
    application_id = request.form.get("application_id", "")
    otp_code = request.form.get("otp_code", "").strip()
    application = _load_application(application_id, session.get("org_access_token"))
    if not application:
        flash("Application not found. Please start again.", "error")
        return redirect("/create_org")

    ok, error = _verify_application_otp(application, otp_code)
    if not ok:
        flash(error, "error")
        return redirect(f"/create_org/verify_otp?application_id={application_id}")

    # Re-fetch: _verify_application_otp() just advanced status to
    # 'otp_verified' in the DB, but `application` here is still the
    # pre-verification snapshot loaded at the top of this request.
    application = _load_application(application_id, session.get("org_access_token"))
    ok, error, portal_url, checkin_url = _provision_application_instantly(application)
    if not ok:
        app_log.error("create_org_verify_otp: instant provisioning failed for application %s: %s",
                      application_id, error)
        flash(f"Could not set up your company: {error}. Please contact support.", "error")
        return redirect("/create_org")

    return redirect(f"/create_org/upload_documents?application_id={application_id}")


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
    return redirect(f"/create_org/verify_otp?application_id={application_id}")


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
                           token=request.args.get("token", ""))


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
        return redirect(f"/create_org/upload_documents?application_id={application_id}{_token_qs}")

    # Every self-serve application reaches status='provisioned' right after
    # OTP now (see _provision_application_instantly()) -- there's nothing
    # left to "wait" for once documents are in, so send them straight to
    # their live company's status/login page instead of the old
    # please-wait screen, which only still applies to a legacy in-flight
    # application that somehow reached here pre-provisioned.
    if application["status"] == "provisioned":
        flash("Documents submitted -- your company is live. Log in below.", "info")
        return redirect(f"/create_org/status/{application_id}")
    return redirect(f"/create_org/pending?application_id={application_id}")


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
    in the URL at all."""
    access_token = session.get("org_access_token") or request.args.get("token", "")
    application = _load_application(application_id, access_token)
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
        "SELECT id, company_name, subdomain, admin_username, admin_email, admin_password_hash, "
        "email_domain, employee_count, payment_option, logo_path, access_token_hash, otp_code_hash, "
        "otp_expires_at, otp_attempts, email_verified_at, doc_registration_cert, doc_address_proof, "
        "doc_visiting_card, doc_name_board_photo, documents_submitted_at, status, reviewed_by, "
        "reviewed_at, rejection_reason, tenant_id, doc_review_status "
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
            "reviewed_at", "rejection_reason", "tenant_id", "doc_review_status"]
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
        flash("Documents submitted for review. We'll notify you once they're checked.", "success")
    return redirect(tpath("/settings/company_verification"))


@org_bp.route("/create_org/pay/<int:application_id>", methods=["GET"])
def create_org_pay_page(application_id):
    """Landing page linked from the approval email for online-payment
    applications (blueprints/platform_admin.py's approve action, once
    payment_option=='online') -- pre-filled/locked to this one approved
    application rather than an editable signup form, since every other
    field was already collected and reviewed. No access_token needed in
    the URL: application_id alone is enough here because this page only
    ever *displays* the company name and hands off to
    /api/billing/create_order, which independently re-checks the
    application's own status='approved_pending_payment' before letting
    any charge happen -- there's nothing sensitive this page itself could
    leak or let someone tamper with."""
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
    later, at first login (see app.py's inject_billing_lock_status())."""
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
        return jsonify({"ok": False, "msg": error}), 502

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
        "logo_path, status, payment_option, gst_number FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    (company_name, subdomain, admin_username, admin_email, admin_password_hash, email_domain,
     logo_path, status, payment_option, gst_number) = row

    # Idempotent: a retried/duplicate client POST for an already-provisioned
    # application must not attempt to re-provision.
    if status == "provisioned":
        portal_url = f"{_safe_app_url()}/{subdomain}/login"
        return jsonify({"ok": True, "portal_url": portal_url, "already_provisioned": True})
    if payment_option != "trial" or status != "approved_pending_payment":
        return jsonify({"ok": False, "msg": "This application isn't ready for trial setup."}), 400

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
    the way the web flow's does)."""
    try:
        data = request.get_json() or {}
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


@org_bp.route("/api/create_org/verify_otp", methods=["POST"])
@limiter.limit("20 per hour")
def api_create_org_verify_otp():
    data = request.get_json() or {}
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    if not application:
        return jsonify({"ok": False, "msg": "Application not found."}), 404

    ok, error = _verify_application_otp(application, str(data.get("otp_code", "")).strip())
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400

    # Re-fetch: _verify_application_otp() just advanced status in the DB,
    # but `application` here is still the pre-verification snapshot.
    application = _load_application(data.get("application_id"), data.get("access_token", ""))
    ok, error, portal_url, checkin_url = _provision_application_instantly(application)
    if not ok:
        app_log.error("api_create_org_verify_otp: instant provisioning failed for application %s: %s",
                      application["id"], error)
        return jsonify({"ok": False, "msg": f"Could not set up your company: {error}. Contact support."}), 500

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
    """"Request info" form on the public landing page (templates/landing.html)
    -- for visitors who want more details before self-registering via
    /create_org. Stored in att_master.leads, surfaced on the Platform Admin
    dashboard for manual follow-up. Deliberately has no admin-facing error
    detail beyond "required" -- this is a public, unauthenticated endpoint."""
    if turnstile_enabled():
        token = request.form.get("cf-turnstile-response") or (request.get_json(silent=True) or {}).get("cf_turnstile_response", "")
        if not verify_turnstile(token, request.remote_addr):
            return jsonify({"ok": False, "msg": "Captcha verification failed. Please try again."}), 400

    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()[:200]
    email = (data.get("email") or "").strip()[:200]
    phone = (data.get("phone") or "").strip()[:30] or None
    company_name = (data.get("company_name") or "").strip()[:200] or None
    message = (data.get("message") or "").strip()[:2000] or None

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

    return jsonify({"ok": True, "msg": "Thanks! We'll be in touch shortly."})


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

    return jsonify({"ok": True, "msg": "Thanks for the feedback! It goes straight to our product team."})
