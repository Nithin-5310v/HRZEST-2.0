# -*- coding: utf-8 -*-
"""Per-request tenant (company) resolution and session binding.

Split out of app.py; registered on the shared Flask app at import time.
app.py imports the app_setup modules in a fixed order because Flask runs
before_request hooks in registration order (after_request in reverse).
"""
import os
import time
from database import get_master_db
from extensions import app, app_log, log_security_event
from flask import jsonify, make_response, redirect, request, session
from urllib.parse import urlsplit
from utils.helpers import _error_page, wants_json_response
from app_setup.config import _TENANT_STATUS_RECHECK_SEC


@app.before_request
def _normalize_loopback_host():
    """WebAuthn (utils/webauthn_utils.py) refuses "127.0.0.1"/"::1" as an RP
    ID -- only a real hostname, or the spec's special-cased "localhost",
    works -- so local dev needs every page served from "localhost", not the
    IP literal. This used to be a client-side redirect placed on the
    post-login templates only (admin_base.html/employee_portal.html/
    index.html): it fired *after* login had already set a session cookie
    scoped to host "127.0.0.1", then navigated to "localhost", a different
    host as far as the browser's cookie jar is concerned -- so the
    just-issued cookie never came along, the very next request looked
    unauthenticated, and the user was bounced back to login for a second
    full login+OTP cycle. Redirecting here instead, before any session
    handling runs (registered first, ahead of tenant/session/CSRF hooks),
    means every page -- including the login and MFA-verify pages -- is
    already on "localhost" before any cookie is ever set, so no session
    is ever bound to the host that's about to be abandoned.
    """
    host = request.host.partition(":")[0]
    if host in ("127.0.0.1", "::1"):
        new_host = request.host.replace(host, "localhost", 1)
        return redirect(request.url.replace(request.host, new_host, 1), code=302)


@app.before_request
def _resolve_tenant():
    """Determine the tenant database for this request and store it in
    g.tenant_db. Registered second -- right after the perf timer, before
    every other hook -- because get_db_connection() (database.py) reads
    g.tenant_db and silently falls back to the "public" schema if it isn't
    set yet. _enforce_ip_ban and _enforce_admin_mfa_enrollment both call
    get_db_connection() directly; running this hook after either of them
    would make both checks query the wrong tenant's data on every single
    request in a multi-tenant deployment -- a full bypass of both controls,
    not a rare edge case.

    Tenants are identified by URL path (www.hrzest.com/<company-slug>/...),
    not subdomain. utils/tenant_routing.py's WSGI middleware has already
    stripped a recognized slug into SCRIPT_NAME and left its lookup result
    on request.environ before Flask ever routed this request -- this hook
    just reads that, plus enforces session/URL tenant binding (see below)."""
    from flask import g as _g

    # Skip for static files and special paths. "/oauth/callback" is where
    # Google/Microsoft redirect back to after OAuth login
    # (blueprints/oauth_signup.py) -- a single fixed global URL with no
    # tenant slug in it; that route resolves its own tenant from the
    # server-side oauth_signup_states row instead. "/create_org" already
    # covers the OAuth *signup* entry points (/create_org/<provider>[/callback]),
    # since no tenant exists yet at that point.
    skip_prefixes = ("/static/", "/healthz", "/create_org", "/super_admin", "/oauth/callback")
    if any(request.path.startswith(p) for p in skip_prefixes):
        return
    # The SOC console is a platform surface, never tenant-scoped. Exact segment
    # match so a company slug that merely starts with "soc" is unaffected.
    if request.path == "/soc" or request.path.startswith("/soc/"):
        return

    url_slug = request.environ.get("hrz.tenant_slug")
    url_tenant_db = request.environ.get("hrz.tenant_db")

    # A company whose schema lacks a table would read the shared public
    # table -- another company's data -- so refuse it outright (QA SEC-07;
    # flagged by the deploy-time migrations, read by TenantPrefixMiddleware).
    if request.environ.get("hrz.tenant_incomplete"):
        return _tenant_schema_incomplete(url_tenant_db)

    # 0. Cross-tenant session isolation. Subdomains used to give this for
    # free (a host-only cookie for acme.hrzest.com is never sent to
    # beta.hrzest.com by the browser itself). Now every tenant shares one
    # hostname, so the app has to enforce it: if this session is bound to
    # a different company than the one named in the current URL, the
    # cookie is stale for this request -- hard-clear it rather than
    # silently keep serving company A's session under company B's URL.
    # Requests that carry no slug at all (marketing/platform-admin routes,
    # and token-based API/mobile-app calls, which never addressed tenants
    # by URL even in the subdomain era) are untouched by this check.
    if url_slug and session.get("tenant_db"):
        session_slug = session.get("tenant_slug")
        if session_slug and session_slug != url_slug:
            log_security_event(
                "tenant.session_mismatch",
                f"Session bound to tenant slug '{session_slug}' saw a request "
                f"for '{url_slug}' -- session cleared.",
                level="WARNING",
                identifier=session.get("admin_username") or session.get("employee_id"),
            )
            session.clear()
        elif not session_slug:
            # Legacy session predating this migration (or a first-touch
            # backfill) -- trust it, just record which slug it's bound to.
            session["tenant_slug"] = url_slug

    # 1. Already resolved in this session -- but re-validate status every
    # _TENANT_STATUS_RECHECK_SEC instead of trusting the cache forever.
    # Without this, a platform admin suspending a tenant (blueprints/
    # platform_admin.py) would have no effect on any session that had
    # already resolved that tenant: this cache is the only thing standing
    # between "suspended" and "still fully working," since cookie-based
    # sessions have no server-side store to revoke from outside.
    if session.get("tenant_db"):
        # Remembered for _restamp_tenant_session: a URL with no company slug
        # (bare /login) still runs as this company.
        _g.tenant_slug = session.get("tenant_slug")
        last_checked = session.get("_tenant_status_checked_at", 0)
        if (time.time() - last_checked) < _TENANT_STATUS_RECHECK_SEC:
            _g.tenant_db = session["tenant_db"]
            _g.billing_locked = session.get("_billing_locked", False)
            return
        try:
            conn = get_master_db()
            cur = conn.cursor()
            cur.execute("SELECT status, billing_state, schema_missing_tables FROM tenants WHERE db_name=%s",
                        (session["tenant_db"],))
            row = cur.fetchone()
            cur.close()
            conn.close()
        except Exception:
            row = None  # master DB unreachable -- don't punish the session for it, just skip the recheck this time
            app_log.warning(
                "tenant.status_recheck_failed: tenant_db=%s", session.get("tenant_db"), exc_info=True
            )
        if row is not None and row[0] == "active" and row[2]:
            # Not re-stamped, so every request rechecks until it's repaired (QA SEC-07).
            return _tenant_schema_incomplete(session["tenant_db"])
        if row is None or row[0] == "active":
            session["_tenant_status_checked_at"] = time.time()
            # billing_state='locked' does NOT block resolution/login here --
            # only the admin-side guards (utils/auth.py) act on it: employees
            # carry on, admins reach only the pay page.
            session["_billing_locked"] = bool(row) and row[1] == "locked"
            _g.tenant_db = session["tenant_db"]
            _g.billing_locked = session["_billing_locked"]
            return
        session.clear()
        return jsonify({"ok": False, "msg": "This organisation's access has been suspended. Contact support."}), 403

    # 2. Fresh resolution from the URL's company slug -- already validated
    # as an active tenant by utils/tenant_routing.py's WSGI middleware, so
    # reuse its lookup instead of querying the master DB a second time.
    if url_slug and url_tenant_db:
        _g.tenant_db = url_tenant_db
        # Set only by utils/tenant_routing.py's middleware (a real bool);
        # request headers reach environ as HTTP_* keys, so a client can't
        # supply this one.
        _g.billing_locked = request.environ.get("hrz.billing_locked") is True
        session["tenant_db"] = url_tenant_db
        session["tenant_slug"] = url_slug
        session["_tenant_status_checked_at"] = time.time()
        session["_billing_locked"] = _g.billing_locked
        return

    # 3. Default single-tenant fallback (local dev/test, or any request
    # that never carried a company slug: marketing pages, token-based
    # API/mobile-app calls, the platform-admin console).
    _g.tenant_db = os.environ.get("DB_NAME", "employee_attendance")
    _g.billing_locked = False


@app.after_request
def _restamp_tenant_session(response):
    """Login routes (auth.py's admin_login/employee login, etc.) call
    session.clear() to prevent session fixation -- which wipes the
    tenant_db/tenant_slug that _resolve_tenant() (above) already set
    earlier in this SAME request, before the route handler ran. Without
    this, the session cookie sent back with a successful login response
    wouldn't carry the tenant binding at all until a second request came
    in -- a real gap, since a request landing in that gap (any request
    without a company slug in its own URL) would fall back to the
    single-tenant default schema while admin_logged_in is already True.

    g.tenant_db is request-scoped and untouched by session.clear(), so
    it's still correct here regardless of what the route handler did to
    the session -- just re-stamp the session from it before the response
    goes out. No-ops for the overwhelming majority of requests (guarded
    on the session already being correct), so this isn't forcing a
    Set-Cookie on every response.

    The company comes from the URL slug or, on a URL without one (bare
    /login, /mfa_verify), from the session binding _resolve_tenant() ran
    this request under. Without the latter, a bare /login after a
    /<slug>/ visit issued its MFA code in that company's schema and
    /mfa_verify then looked for it in the default one -- no code could
    ever verify -- and a login without MFA landed in the default
    company's data."""
    from flask import g as _g
    tenant_db = getattr(_g, "tenant_db", None)
    slug = request.environ.get("hrz.tenant_slug") or getattr(_g, "tenant_slug", None)
    if tenant_db and slug and session.get("tenant_slug") != slug:
        session["tenant_db"] = tenant_db
        session["tenant_slug"] = slug
        session["_tenant_status_checked_at"] = time.time()
        session["_billing_locked"] = getattr(_g, "billing_locked", False)
    return response


_LOGIN_LOADER_LANDINGS = {"admin", "employee_portal", "hr_dashboard", "leave_holidays"}


@app.after_request
def _flag_login_success(response):
    """Drops a 30-second hrz_login_ok cookie on the redirect that follows a
    login whose credentials (and MFA, if on) were accepted, so
    static/login-loader.js shows the HRZest loader on the dashboard it lands
    on -- and never after a rejected attempt, which re-renders the form (200)
    or redirects back to login/MFA. Readable by JS on purpose (the script
    deletes it); it carries no data."""
    if (request.method == "POST" and response.status_code in (301, 302, 303)
            and request.path.rstrip("/").rsplit("/", 1)[-1] in ("login", "mfa_verify")
            and (session.get("admin_logged_in") or session.get("employee_id"))):
        landing = urlsplit(response.headers.get("Location", "")).path.rstrip("/").rsplit("/", 1)[-1]
        if landing in _LOGIN_LOADER_LANDINGS:
            response.set_cookie("hrz_login_ok", "1", max_age=30, path="/", samesite="Lax",
                                secure=request.is_secure)
    return response


# A billing-locked tenant (billing_state='locked') is enforced by the
# admin-side guards (utils/auth.py's _reject_if_billing_locked: admin_required,
# role_required, api_required): its admins, HR and managers reach only the
# pay page until it's paid, while its employees -- attendance included --
# carry on as normal.


def _tenant_schema_incomplete(tenant_db):
    """503 for a company whose schema lacks a table the migrations create
    (att_master.tenants.schema_missing_tables): its queries would otherwise
    read and write the shared public table, i.e. another company's data
    (QA SEC-07). The deploy-time migrations log which tables are missing."""
    app_log.warning("tenant.schema_incomplete: tenant_db=%s -- request answered 503", tenant_db)
    if wants_json_response():
        resp = jsonify({"ok": False, "msg": "This organisation is temporarily unavailable. Please try again later."})
    else:
        resp = _error_page(503, "🛠️", "Temporarily Unavailable",
                           "This organisation's workspace is temporarily unavailable.",
                           "Please try again later or contact support.")
    resp = make_response(resp)
    resp.status_code = 503
    resp.headers["Retry-After"] = "300"
    return resp
