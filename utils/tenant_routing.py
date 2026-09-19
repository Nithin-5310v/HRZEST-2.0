# -*- coding: utf-8 -*-
"""Path-based multi-tenancy: WSGI-level prefix stripping.

Replaces the old subdomain scheme (acme.hrzest.com) with a URL-path scheme
(www.hrzest.com/acme/...). This has to happen at the WSGI layer, before
Flask's router ever sees the request, rather than by adding a
"<company_slug>" route parameter to every one of the ~300 existing routes:
app.py's before/after_request hooks do exact/prefix matches on
request.path in ~18 places (Turnstile CAPTCHA gating, the WiFi-posture-
agent CSP carve-out, healthz/static/api exemptions, CSRF login-route
exemption) and all of those would silently break if request.path suddenly
carried a tenant prefix everywhere. Stripping the slug into SCRIPT_NAME
here means every one of those checks, and every existing route decorator,
keeps seeing exactly the bare path it sees today -- "/admin_login", not
"/acme/admin_login".

RESERVED_PATH_SEGMENTS is the single source of truth for "is this first
path segment a real top-level route, or a company slug" -- both this
middleware and blueprints/org.py's subdomain-format validation import it
from here, so a new top-level route only needs to be added to one place.
"""
import logging

_log = logging.getLogger("attendance")

RESERVED_PATH_SEGMENTS = frozenset({
    # infra / static
    "static", "healthz", "favicon.ico", "csp-report", "api", "robots.txt", "sitemap.xml",
    # auth / tenant-picker pages (global, not company-scoped)
    "login", "admin_login", "admin-login", "employee_login", "get-started", "create_org", "org_payment_success",
    "mobile_bridge_login",
    # server-to-server callbacks (no tenant slug, no session -- authenticated
    # by request signature instead; blueprints/webhooks.py's generic
    # /webhooks/<provider>, dispatching to whichever feature registered
    # for that provider/event)
    "webhooks",
    # operator consoles (excluded from this migration entirely)
    "super_admin", "superadmin", "sp_admin",
    # legacy reserved words carried over from the subdomain era
    "hrms", "www", "admin", "app", "mail", "master",
    "assets", "cdn", "ns1", "ns2",
})


class TenantPrefixMiddleware:
    """WSGI middleware: if the first path segment is a real, active
    tenant's slug, strip it into SCRIPT_NAME and stash the resolved
    tenant on the environ for app.py's _resolve_tenant() to pick up.
    Anything else (reserved segments, unknown slugs, bare "/") passes
    through completely unchanged."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        try:
            seg = environ.get("PATH_INFO", "").lstrip("/").split("/", 1)[0] or None
        except Exception:
            seg = None

        if seg and seg.lower() not in RESERVED_PATH_SEGMENTS:
            row = self._lookup_tenant(seg.lower())
            if row:
                self._pop_path_info(environ)
                environ["hrz.tenant_slug"] = seg.lower()
                environ["hrz.tenant_db"] = row[0]
                environ["hrz.billing_locked"] = (row[1] == "locked")

        return self.wsgi_app(environ, start_response)

    @staticmethod
    def _pop_path_info(environ):
        """Shift the first PATH_INFO segment into SCRIPT_NAME, in place."""
        path = environ.get("PATH_INFO", "").lstrip("/")
        script_name = environ.get("SCRIPT_NAME", "")
        if "/" in path:
            segment, rest = path.split("/", 1)
            environ["PATH_INFO"] = "/" + rest
        else:
            segment = path
            environ["PATH_INFO"] = ""
        if segment:
            environ["SCRIPT_NAME"] = script_name.rstrip("/") + "/" + segment
        return segment or None

    @staticmethod
    def _lookup_tenant(slug):
        """Best-effort: a master-DB hiccup here should fail open to
        "not a tenant slug" (pass the request through unchanged) rather
        than 500 every request, matching _resolve_tenant()'s own
        fail-open behavior for the equivalent lookup."""
        try:
            from database import get_master_db
            conn = get_master_db()
            cur = conn.cursor(buffered=True)
            cur.execute(
                "SELECT db_name, billing_state FROM tenants WHERE subdomain=%s AND status='active'",
                (slug,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            return row
        except Exception:
            # Fail open by design (see docstring) -- but that previously
            # meant a master-DB outage degraded every tenant-prefixed
            # request with zero signal. Log so it's visible to monitoring
            # instead of only showing up as "tenant slugs mysteriously stop
            # resolving."
            _log.warning("tenant_routing.lookup_failed: slug=%s", slug, exc_info=True)
            return None
