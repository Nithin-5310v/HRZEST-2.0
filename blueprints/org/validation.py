# -*- coding: utf-8 -*-
"""org: signup field validation: slugs, company/admin fields, mobile, GST"""
import re
from utils.auth import validate_new_password
from utils.helpers import clean_email_domain, validate_email_domain_format
from utils.tenant_routing import RESERVED_PATH_SEGMENTS


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
