# -*- coding: utf-8 -*-
"""SEO blueprint -- /robots.txt, /sitemap.xml, and crawler-facing headers.

Only the public pages (the landing page, the company sign-up form, the
privacy policy, the terms and the security policy) are meant to appear in
search results. Everything else -- the tenant
portals, every login/forgot-password page, the check-in kiosk, the platform
admin console -- is private, so every response carries
`X-Robots-Tag: noindex, nofollow` unless its path is in INDEXABLE_PATHS. The
default is deliberately "hidden": a new public page has to be added to that
list on purpose before Google is allowed to index it.

Login pages are NOT listed under Disallow in robots.txt on purpose: a crawler
that is blocked from fetching a page never sees its noindex header, so the URL
could still show up in results as a bare link.
"""
import os
import re
from urllib.parse import urlsplit
from xml.sax.saxutils import escape  # nosec B406 -- escapes sitemap output; nothing here parses XML

from flask import Blueprint, Response, request

seo_bp = Blueprint("seo", __name__)

# Used when APP_URL isn't set. hrzest.com (no www) is the canonical host:
# www.hrzest.com redirects to it (utils/canonical_host.py, and nginx).
_DEFAULT_BASE_URL = "https://hrzest.com"

# Paths a search engine may index. Anything else gets noindex. They are also
# the pages whose views utils/analytics counts (see count_public_page_view).
INDEXABLE_PATHS = ("/", "/signup", "/privacy", "/terms", "/security-policy")

# Responses that must stay fetchable and un-tagged.
_UNTAGGED_PATHS = ("/robots.txt", "/sitemap.xml", "/llms.txt", "/.well-known/security.txt")

# Crawlers, link-preview fetchers, uptime checks and scripts: not visitors.
_BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|lighthouse|pingdom|uptime|monitor|"
                     r"headless|curl|wget|python-|go-http|java/|okhttp|zap|scanner", re.IGNORECASE)
_UNTAGGED_PREFIXES = ("/static/",)

# Never useful to crawl: the JSON APIs. robots.txt is public, so it must not
# name anything private (security review OBS-07 removed /super_admin,
# /webhooks/ and /csp-report). Private pages are kept out of search results
# by the X-Robots-Tag: noindex header below, and the operator console
# answers 404 to anyone without a super-admin session.
_ROBOTS_DISALLOW = ("/api/",)


def _base_url():
    """Canonical site origin. Deliberately from config, never the Host header --
    that header is attacker-controlled and would let anyone poison the sitemap."""
    url = os.environ.get("APP_URL", "").strip().rstrip("/")
    # A raw IP / localhost APP_URL (local dev) must never leak into the
    # landing page's canonical link or the sitemap.
    host = urlsplit(url).hostname or ""
    if not url or host == "localhost" or re.fullmatch(r"[\d.]+|[0-9a-f:]+", host):
        return _DEFAULT_BASE_URL
    return url


@seo_bp.route("/robots.txt")
def robots_txt():
    if os.environ.get("APP_ENV", "production") == "development":
        # A dev/staging copy must never be indexed.
        lines = ["User-agent: *", "Disallow: /"]
    else:
        lines = ["User-agent: *", "Allow: /"]
        lines += [f"Disallow: {path}" for path in _ROBOTS_DISALLOW]
        lines += ["", f"Sitemap: {_base_url()}/sitemap.xml"]
    resp = Response("\n".join(lines) + "\n", mimetype="text/plain")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@seo_bp.route("/sitemap.xml")
def sitemap_xml():
    base = _base_url()
    urls = "".join(f"  <url><loc>{escape(base + path)}</loc></url>\n" for path in INDEXABLE_PATHS)
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n'
    resp = Response(body, mimetype="application/xml")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@seo_bp.route("/llms.txt")
def llms_txt():
    """A plain-language summary of the site for AI assistants (llmstxt.org)."""
    from blueprints.trial_billing import TRIAL_DURATION_DAYS
    base = _base_url()
    s = public_business_details()
    try:
        from utils.plan_limits import get_per_employee_paise
        price = f"Rs {get_per_employee_paise() // 100} per employee per month"
    except Exception:
        price = "a flat price per employee per month"
    lines = [
        "# HRzest",
        "",
        "> HRzest is a cloud HR platform for companies in India: employee attendance with face recognition, "
        "QR codes and GPS geo-fencing; leave and holidays; payroll and payslips; onboarding; performance "
        "reviews; and a self-service portal and mobile app for employees.",
        "",
        f"Pricing: {price}, with a {TRIAL_DURATION_DAYS}-day free trial.",
        "",
        "## Pages",
        f"- [Home]({base}/): what HRzest does, features and pricing",
        f"- [Sign up]({base}/signup): register a company and start the free trial",
        f"- [Privacy Policy]({base}/privacy): what personal data HRzest handles and why",
        f"- [Terms of Service]({base}/terms): the terms for using HRzest",
        f"- [Security policy]({base}/security-policy): how to report a security vulnerability",
        "",
        "## Contact",
        f"- Email: {s['email']}",
        f"- Phone: {s['phone']}",
        f"- Address: {', '.join(s['address_lines'])}, India",
        "",
    ]
    resp = Response("\n".join(lines), mimetype="text/plain")
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


def public_business_details():
    """The business details the public pages show: the invoice seller
    details (utils/tax_invoice.seller_details: address, phone), with the
    email the website shows visitors (utils/email_utils.public_contact_email,
    CONTACT_EMAIL) rather than the invoice email."""
    from utils.email_utils import public_contact_email
    from utils.tax_invoice import seller_details
    return {**seller_details(), "email": public_contact_email()}


def _postal_address(s):
    """schema.org PostalAddress from the invoice address lines
    ("street|area, city|State - PIN")."""
    lines = list(s["address_lines"])
    pin = re.search(r"\b\d{6}\b", lines[-1]) if lines else None
    street_lines = lines[:-1] if len(lines) > 1 else lines
    parts = ", ".join(street_lines).split(", ")
    locality = parts[-1] if len(parts) > 1 else ""
    street = ", ".join(parts[:-1]) if locality else ", ".join(parts)
    address = {"@type": "PostalAddress", "streetAddress": street, "addressRegion": s["state"], "addressCountry": "IN"}
    if locality:
        address["addressLocality"] = locality
    if pin:
        address["postalCode"] = pin.group(0)
    return address


@seo_bp.app_template_global()
def organization_schema():
    """JSON-LD for the landing page: the company (Organization), its office
    (ProfessionalService, a schema.org LocalBusiness type) and the website.
    Every fact comes from public_business_details() -- the details printed
    on invoices and the public contact email; nothing here is made up for SEO."""
    s = public_business_details()
    base = _base_url()
    org_id = f"{base}/#organization"
    address = _postal_address(s)
    return {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Organization", "@id": org_id, "name": "HRzest", "url": f"{base}/",
             "logo": f"{base}/static/brand/icon-512.png", "email": s["email"], "telephone": s["phone"],
             "address": address,
             "contactPoint": {"@type": "ContactPoint", "contactType": "customer support", "email": s["email"],
                              "telephone": s["phone"], "areaServed": "IN", "availableLanguage": ["en"]}},
            {"@type": "ProfessionalService", "@id": f"{base}/#office", "name": "HRzest", "url": f"{base}/",
             "image": f"{base}/static/brand/og-image.png", "email": s["email"], "telephone": s["phone"],
             "address": address, "parentOrganization": {"@id": org_id}},
            {"@type": "WebSite", "@id": f"{base}/#website", "url": f"{base}/", "name": "HRzest",
             "publisher": {"@id": org_id}},
        ],
    }


@seo_bp.app_template_global()
def breadcrumb_schema(items):
    """JSON-LD BreadcrumbList for [(name, path), ...], home first."""
    base = _base_url()
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i, "name": name, "item": base + path}
                            for i, (name, path) in enumerate(items, 1)],
    }


@seo_bp.app_context_processor
def inject_seo_context():
    return {
        "seo_base_url": _base_url(),
        # Optional: the token Google Search Console gives for "HTML tag" ownership verification.
        "google_site_verification": os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip(),
    }


@seo_bp.after_app_request
def tag_private_pages_noindex(response):
    path = request.path
    if path in _UNTAGGED_PATHS or path.startswith(_UNTAGGED_PREFIXES):
        return response
    # request.script_root is non-empty for a tenant-prefixed URL (/acme/...), which
    # serves a copy of the same page under a different address -- not indexable.
    if path in INDEXABLE_PATHS and not request.script_root and response.status_code == 200:
        count_public_page_view(path)
        return response
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def count_public_page_view(path):
    """Server-side analytics (utils/analytics.py): one counter per public page
    per day. No cookies, no script, no IP address stored. Only real page
    loads count: GET (not HEAD), and not crawlers, previews or scripts."""
    if request.method != "GET" or _BOT_UA.search(request.headers.get("User-Agent", "")):
        return
    from utils.analytics import track_page_view
    track_page_view(path)
