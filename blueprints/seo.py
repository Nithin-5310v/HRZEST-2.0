# -*- coding: utf-8 -*-
"""SEO blueprint -- /robots.txt, /sitemap.xml, and crawler-facing headers.

Only the two public marketing pages (the landing page and the company signup
form) are meant to appear in search results. Everything else -- the tenant
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
from xml.sax.saxutils import escape  # nosec B406 -- escapes sitemap output; nothing here parses XML

from flask import Blueprint, Response, request

seo_bp = Blueprint("seo", __name__)

# Used when APP_URL isn't set. Matches the og:url already on the landing page.
_DEFAULT_BASE_URL = "https://www.hrzest.com"

# Paths a search engine may index. Anything else gets noindex.
INDEXABLE_PATHS = ("/", "/create_org")

# Responses that must stay fetchable and un-tagged.
_UNTAGGED_PATHS = ("/robots.txt", "/sitemap.xml")
_UNTAGGED_PREFIXES = ("/static/",)

# Never useful to crawl: JSON APIs, payment/webhook callbacks, the operator console.
_ROBOTS_DISALLOW = ("/api/", "/webhooks/", "/csp-report", "/super_admin")


def _base_url():
    """Canonical site origin. Deliberately from config, never the Host header --
    that header is attacker-controlled and would let anyone poison the sitemap."""
    return os.environ.get("APP_URL", "").strip().rstrip("/") or _DEFAULT_BASE_URL


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
        return response
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response
