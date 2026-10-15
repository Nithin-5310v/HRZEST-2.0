# -*- coding: utf-8 -*-
import sys
import traceback as _traceback
# Runs before app_log is importable -- harmless either way (fails only on a
# stream that doesn't support .reconfigure(), e.g. Python <3.7 or a fully
# redirected/piped stdout that's already fixed-encoding). See wsgi.py's
# identical guard for the same rationale.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# When this file is run directly (`python app.py`, the local-dev entrypoint),
# Python loads it as module "__main__" -- a *different* module identity than
# "app". Every lazy `from app import init_tenant_db` (etc.) done deep inside
# a request handler (blueprints/org.py's provision_tenant() among others)
# then finds no "app" module cached, so Python re-imports this entire file
# from scratch as a second "app" module. extensions.app is the same Flask
# object either way (extensions.py is cached under its own name), so that
# second execution re-runs every @app.route/@app.context_processor/
# @app.errorhandler in this file against the one, already-serving Flask
# instance -- which Flask rejects once it's handled a single request
# ("The setup method ... can no longer be called"). Registering this
# execution under the name "app" up front makes any later `from app import
# X` resolve to this exact already-fully-loaded module instead of
# re-executing it. wsgi.py already imports this file as "app" normally, so
# it never hits this branch -- only a bare `python app.py` needs the alias.
if __name__ == "__main__":
    sys.modules.setdefault("app", sys.modules[__name__])

import os
import re
import secrets
import threading
import hashlib
import time
import base64
from urllib.parse import urlsplit
from werkzeug.exceptions import HTTPException
from dotenv import load_dotenv

load_dotenv()
_HASHI_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hashi", ".env")
load_dotenv(_HASHI_ENV)

from flask import request, session, jsonify, redirect, url_for, flash, current_app, abort, g as _g
import datetime
import html as _html
from database import get_db_connection, get_master_db

# ── Startup: warn if critical env vars are missing ──
_missing_env = [k for k in ("DB_HOST", "DB_USER", "DB_PASS", "DB_NAME") if not os.environ.get(k)]
if _missing_env:
    import warnings
    warnings.warn(
        f"Missing required environment variables: {', '.join(_missing_env)}. "
        "Copy .env.example to .env and fill in the values.",
        stacklevel=2
    )

from extensions import app, app_log, limiter, log_security_event  # noqa: F401 -- app/limiter re-exported: tests/conftest.py does app.limiter.enabled = False
# Single source of truth for email -- app.py used to carry its own complete
# duplicate of every one of these, including _email_queue_worker. wsgi.py
# (the production entrypoint) already starts utils.email_utils's worker
# thread, then imports app.py as a side effect, which used to
# unconditionally start ITS OWN second worker thread on top -- two threads
# racing on the same email_queue table with no row locking, a live
# duplicate-delivery risk in production (every payslip, every security
# alert, sent up to twice). The worker is started exactly once now, see
# the __name__ == "__main__" guard near the bottom of this file -- it only
# fires for a bare `python app.py`, never when wsgi.py imports this module.
from utils.email_utils import (
    get_email_config, get_admin_emails, send_email_async,
    _email_queue_worker,
)
# Single source of truth for auth -- app.py used to carry its own duplicate
# copies of every one of these (password hashing, lockout, session/API
# guards), which had drifted from utils/auth.py's versions and meant three
# rounds of security work (structured event logging, BOLA risk-scoring,
# the session kill switch) were silently not reaching any route in this
# file. Consolidated onto one implementation; see utils/auth.py.
from utils.auth import HR_ROLE
from utils.helpers import (
    _error_page, invalidate_settings_cache, get_company_settings,
    get_companies_list, get_overdue_onboarding_count, coerce_datetime,
    tpath as _tpath, static_url as _static_url,
)
# Shift timings / deduction rates / office geo-fence -- app.py used to carry
# its own separate SHIFT_START / LATE_DEDUCTION_RATE / OFFICE_LAT etc.
# globals, mutated by its own separate load_default_shift()/
# load_salary_rules(). utils/config.py's docstring already stated the
# intent ("blueprints should always access them through this module") --
# app.py just never migrated. Now the single source both use, referenced
# throughout this file as cfg.SHIFT_START etc.
import utils.config as cfg
import utils.waf as waf
from utils.client_ip import is_protected_ip, warn_if_client_ip_collapsed, apply_proxy_fix

# ── Trusted base URL for email links (avoids Host-header injection) ───────────
# Set APP_URL=https://yourdomain.com in .env for production.
# Falls back to request.host_url only when the env var is absent (local dev).
# _APP_URL / _safe_app_url / _safe_redirect / _safe_referrer_redirect moved
# to utils/helpers.py -- used across multiple routes still in app.py, not
# just the auth/admin_views blueprints.
# _INJECTION_PATTERN_RE moved to blueprints/auth.py -- its only caller
# (admin_login) migrated there.


@app.context_processor
def inject_common_vars():
    return dict(
        shift_start=cfg.SHIFT_START.strftime("%I:%M %p"),
        shift_end=cfg.SHIFT_END.strftime("%I:%M %p"),
        # templates/admin_base.html's sidebar "Dashboard" link reads this
        # (`tpath('/employees') if _is_hr else tpath('/admin')`) -- it was
        # referencing this exact name already, just never actually
        # defined anywhere, so Jinja silently treated it as falsy and
        # every admin-side session, HR-role included, always got sent to
        # /admin. HR sessions (whether a real admin_users role='hr'
        # account, or an employee auto-routed here because their
        # employees.role is exactly "HR" -- see blueprints/auth.py's
        # _finish_employee_login) land on /employees instead, which is
        # also where their login already redirects them to.
        _is_hr=(session.get("admin_role") == HR_ROLE),
        hr_has_own_employee=_hr_has_own_employee(),
    )


def _hr_has_own_employee():
    """True if the current HR-role session's admin_username is backed by
    a real employees row -- gates templates/admin_base.html's "My
    Profile" button (blueprints/auth.py's switch_to_my_employee_portal()
    404s harmlessly without this, but showing the button at all when
    there's nothing to switch to is just a dead click). Some admin_users
    role='hr' accounts are standalone (created via /hr_accounts) rather
    than auto-provisioned from an employee whose own role is "HR" -- see
    blueprints/auth.py's _ensure_hr_admin_account -- and have no
    employees row of their own to switch to.

    Cached on flask.g per-request since inject_common_vars() runs once
    per template render but this can be called from more than one context
    processor in the future; cheap either way (one indexed lookup on
    employees.employee_id's UNIQUE constraint)."""
    if session.get("admin_role") != HR_ROLE or not session.get("admin_username"):
        return False
    if not hasattr(_g, "_hr_has_own_employee"):
        try:
            db = get_db_connection()
            cursor = db.cursor(buffered=True)
            cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (session["admin_username"],))
            _g._hr_has_own_employee = cursor.fetchone() is not None
            cursor.close()
            db.close()
        except Exception:
            _g._hr_has_own_employee = False
    return _g._hr_has_own_employee


# /favicon.ico and /healthz are served by blueprints/health.py


# Jinja2 filter: handles both datetime.time and datetime.timedelta.
# psycopg2 returns TIME columns as datetime.time (hits the strftime branch
# below); the timedelta branch is a defensive fallback kept from when this
# ran against mysql-connector, which returned TIME columns as timedelta.
@app.template_filter("fmt_time")
def fmt_time_filter(value):
    if value is None:
        return "--"
    if isinstance(value, str):
        return value
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S")
    # timedelta fallback -- see comment above
    total = int(value.total_seconds())
    return "{:02d}:{:02d}:{:02d}".format(total // 3600, (total % 3600) // 60, total % 60)

# Templates that need arithmetic on a TIME value (elapsed-time math, HH/MM/SS
# breakdowns) used to rely on mysql-connector's timedelta.seconds. psycopg2
# returns datetime.time instead, which has no .seconds -- this filter gives
# templates a type-agnostic "total seconds" so that math still works.


@app.template_filter("time_seconds")
def time_seconds_filter(value):
    if value is None:
        return 0
    if hasattr(value, "hour"):
        return value.hour * 3600 + value.minute * 60 + value.second
    return int(value.total_seconds())


@app.template_filter("plan_price")
def plan_price_filter(paise):
    """paise -> "₹1,999" for billing displays (super_admin_dashboard.html,
    create_org.html) -- thin wrapper so templates don't import utils.plan_limits."""
    from utils.plan_limits import format_price_inr
    return format_price_inr(paise)

# ---------------- CONFIG ----------------
# secret_key, session cookie flags, and PERMANENT_SESSION_LIFETIME are
# authoritative in extensions.py. Do not duplicate them here.


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ---------------- CSRF PROTECTION ----------------
_EMP_ID_RE = re.compile(r'^[A-Za-z0-9_\-]+$')


def _csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = _csrf_token
app.jinja_env.globals["timedelta"] = datetime.timedelta

app.jinja_env.globals["tpath"] = _tpath
app.jinja_env.globals["static_url"] = _static_url


@app.context_processor
def inject_companies_context():
    """Inject active company and companies list into every admin template.
    Reads through get_companies_list()'s 30s cache rather than querying the
    companies table on every single admin page render."""
    if not session.get("admin_logged_in"):
        return {}
    try:
        rows = get_companies_list()
        active_cid = session.get("active_company_id")
        active_company = None
        for r in rows:
            if r[0] == active_cid:
                active_company = {"id": r[0], "name": r[1], "code": r[2]}
                break
        return {
            "all_companies": [{"id": r[0], "name": r[1], "code": r[2], "has_pin": bool(r[3])} for r in rows],
            "active_company": active_company,
        }
    except Exception:
        return {"all_companies": [], "active_company": None}


@app.context_processor
def inject_overdue_onboardings():
    """Reads through get_overdue_onboarding_count()'s 20s cache rather than
    running a COUNT query on every single admin page render."""
    if not session.get("admin_logged_in"):
        return {}
    try:
        return {"overdue_onboardings": get_overdue_onboarding_count()}
    except Exception:
        return {"overdue_onboardings": 0}


@app.context_processor
def inject_billing_lock_status():
    """Lets templates/admin_base.html show a proactive grace/locked banner
    without every route handler having to fetch it -- _enforce_billing_lock()
    only warns reactively (on the first blocked write attempt), this is what
    lets an admin see the deadline *before* they hit that block. Cheap
    single-row lookup, same posture as inject_overdue_onboardings above
    (only runs for a logged-in admin session, fails soft to "nothing to
    show" rather than ever breaking a page render)."""
    if not session.get("admin_logged_in"):
        return {}
    try:
        from flask import g as _g
        db = get_master_db()
        cur = db.cursor(buffered=True)
        cur.execute(
            "SELECT id, billing_state, grace_period_ends_at, subscription_status, trial_start_date "
            "FROM tenants WHERE db_name=%s",
            (_g.tenant_db,)
        )
        row = cur.fetchone()
        cur.close()
        db.close()
        if not row:
            return {"billing_lock_status": None}
        tenant_id, billing_state, grace_period_ends_at, subscription_status, trial_start_date = row
        # First-login trial-start stamp: deliberately here, not in
        # blueprints/auth.py's admin_login(), since that route has two
        # separate points where a session actually gets established
        # (direct login when MANDATORY_LOGIN_MFA is off, vs after a
        # separate /mfa_verify completion when it's on) -- this context
        # processor runs on every admin-authenticated page render
        # regardless of which path was used, so it's the one place
        # guaranteed to see "this tenant's first real login" exactly once,
        # via the trial_start_date IS NULL guard below. Fires at most once
        # ever per tenant; every render after that just no-ops on the
        # subscription_status/trial_start_date check.
        if subscription_status == "trialing" and trial_start_date is None:
            from blueprints.trial_billing import TRIAL_DURATION_DAYS
            db2 = get_master_db()
            cur2 = db2.cursor()
            cur2.execute(
                "UPDATE tenants SET trial_start_date=NOW(), "
                "trial_end_date=NOW() + (%s * INTERVAL '1 day') WHERE id=%s AND trial_start_date IS NULL",
                (TRIAL_DURATION_DAYS, tenant_id)
            )
            db2.commit()
            cur2.close()
            db2.close()
        if billing_state == "current":
            return {"billing_lock_status": None}
        return {"billing_lock_status": {"state": billing_state, "grace_period_ends_at": coerce_datetime(grace_period_ends_at)}}
    except Exception:
        return {"billing_lock_status": None}


@app.context_processor
def inject_company_verification_status():
    """Lets templates/admin_base.html show a "finish your company
    verification" banner without every route handler fetching it -- same
    fail-soft, admin-session-only posture as inject_billing_lock_status()
    just above. Needed because provisioning no longer waits on KYC
    document review (blueprints/org.py's _provision_application_instantly()):
    an admin can be fully logged in and using the portal with their
    company's documents still unreviewed, or even rejected (though a
    rejected+suspended tenant can't reach this render at all -- see
    utils/tenant_routing.py -- so 'rejected' realistically only shows up
    here in the brief window between rejection and the tenant_routing
    re-check catching up, per _TENANT_STATUS_RECHECK_SEC)."""
    if not session.get("admin_logged_in"):
        return {}
    try:
        from flask import g as _g
        db = get_master_db()
        cur = db.cursor(buffered=True)
        cur.execute(
            "SELECT ta.id, ta.doc_review_status, ta.rejection_reason FROM tenant_applications ta "
            "JOIN tenants t ON ta.tenant_id=t.id WHERE t.db_name=%s ORDER BY ta.id DESC LIMIT 1",
            (_g.tenant_db,)
        )
        row = cur.fetchone()
        cur.close()
        db.close()
        if not row or row[1] == "approved":
            return {"company_verification_status": None}
        return {"company_verification_status": {
            "application_id": row[0], "doc_review_status": row[1], "rejection_reason": row[2],
        }}
    except Exception:
        return {"company_verification_status": None}


_SESSION_MAX_AGE = 8 * 3600  # 8 hours absolute -- stolen cookie cannot be used indefinitely
# How often _resolve_tenant() re-checks tenants.status for an
# already-session-cached tenant. Bounds how long a platform-admin
# suspension (blueprints/platform_admin.py) takes to actually lock out an
# already-logged-in session, rather than never (cookie sessions have no
# server-side store to revoke from outside).
_TENANT_STATUS_RECHECK_SEC = 5 * 60


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

    url_slug = request.environ.get("hrz.tenant_slug")
    url_tenant_db = request.environ.get("hrz.tenant_db")

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
        last_checked = session.get("_tenant_status_checked_at", 0)
        if (time.time() - last_checked) < _TENANT_STATUS_RECHECK_SEC:
            _g.tenant_db = session["tenant_db"]
            _g.billing_locked = session.get("_billing_locked", False)
            return
        try:
            conn = get_master_db()
            cur = conn.cursor()
            cur.execute("SELECT status, billing_state FROM tenants WHERE db_name=%s", (session["tenant_db"],))
            row = cur.fetchone()
            cur.close()
            conn.close()
        except Exception:
            row = None  # master DB unreachable -- don't punish the session for it, just skip the recheck this time
            app_log.warning(
                "tenant.status_recheck_failed: tenant_db=%s", session.get("tenant_db"), exc_info=True
            )
        if row is None or row[0] == "active":
            session["_tenant_status_checked_at"] = time.time()
            # billing_state='locked' does NOT block resolution/login here --
            # only _enforce_billing_lock() (below) blocks state-changing
            # requests, per this feature's "can log in, can't act" design.
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
        # Set only by utils/tenant_routing.py's middleware; request headers
        # reach environ as HTTP_* keys, so a client can't supply this one.
        # nosemgrep: python.flask.security.injection.nan-injection.nan-injection
        _g.billing_locked = bool(request.environ.get("hrz.billing_locked"))
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
    Set-Cookie on every response."""
    from flask import g as _g
    tenant_db = getattr(_g, "tenant_db", None)
    url_slug = request.environ.get("hrz.tenant_slug")
    if tenant_db and url_slug and session.get("tenant_slug") != url_slug:
        session["tenant_db"] = tenant_db
        session["tenant_slug"] = url_slug
        session["_tenant_status_checked_at"] = time.time()
        session["_billing_locked"] = getattr(_g, "billing_locked", False)
    return response


# Exempt from the billing-lock write-block below: the pages/actions a
# locked tenant must still be able to reach to pay their way back out
# (blueprints/billing_dunning.py), plus the universal login/logout/static
# exemptions every other gate in this file also carries.
_BILLING_LOCK_EXEMPT_PATHS = {
    "/logout", "/login", "/admin_login", "/employee_login",
    "/pay_overdue_bill", "/api/billing/overdue/create_order", "/api/billing/overdue/verify",
}


@app.before_request
def _enforce_billing_lock():
    """A tenant past its 5-day payment grace period (billing_state='locked',
    set by blueprints/billing_dunning.py's daily check) can still log in and
    browse -- _resolve_tenant() above deliberately doesn't block resolution
    on this -- but every state-changing request is refused here until a
    payment clears it, automatically, via that module's Razorpay webhook.
    No admin action is needed on either side of that transition.

    GET/HEAD/OPTIONS always pass (read-only viewing, including payroll
    pages themselves -- the user's requirement is "can't make changes",
    not "can't see the page"); only mutating methods on non-exempt paths
    are refused."""
    if not getattr(_g, "billing_locked", False):
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.path.startswith("/static/") or request.path == "/healthz" or request.path in _BILLING_LOCK_EXEMPT_PATHS:
        return
    # An employee session hitting this has no way to pay the bill --
    # /pay_overdue_bill is @admin_required, so redirecting them there just
    # bounces them straight to the admin login with no explanation of why.
    # Point them back at their own portal with a message telling them who
    # actually needs to act, instead.
    is_employee_session = bool(session.get("employee_id")) and not session.get("admin_logged_in")
    if is_employee_session:
        msg = "Your company's account needs attention -- please contact your admin."
    else:
        msg = "Your account is locked because of an overdue payment. Pay your outstanding bill to unlock it."
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "msg": msg, "billing_locked": True}), 402
    flash(msg, "error")
    return redirect(_tpath("/employee_portal") if is_employee_session else _tpath("/pay_overdue_bill"))


@app.before_request
def _enforce_ip_ban():
    """Application-layer IP ban, enforced before every other before_request
    hook (registered second, right after the perf timer) so a banned source
    never reaches session/auth logic, let alone a route handler. Backs the
    SOC dashboard's one-click ban action (blueprints/admin_views.py). Static
    assets stay reachable -- banning is about stopping active app usage
    (login attempts, API calls), not making the banned party's browser look
    broken in a way that itself signals "you got blocked, try harder.\""""
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    ip = request.remote_addr
    if not ip:
        return
    if is_protected_ip(ip):
        # A non-routable/shared source address (container gateway, LAN,
        # allow-listed probe) can't identify one client, so a ban row for
        # it -- including one written before this guard existed -- would
        # lock out every visitor behind it. See utils/client_ip.py.
        warn_if_client_ip_collapsed(ip)
        return
    db = get_db_connection()
    cursor = db.cursor()
    try:
        # banned_ips exists once per tenant schema AND in public, and the
        # search_path puts the tenant's copy first -- so a plain
        # `FROM banned_ips` only ever sees that tenant's own bans. Automated
        # bans (utils/waf.ban_ip) are written to public.banned_ips so an
        # attacker can't hop to another tenant's URL to shake them off;
        # check both. With no tenant active the two names are one table.
        try:
            cursor.execute(
                "SELECT reason FROM banned_ips WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW()) "
                "UNION ALL "
                "SELECT reason FROM public.banned_ips WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW()) "
                "LIMIT 1",
                (ip, ip),
            )
        except Exception:
            # Schema-qualified name unsupported (SQLite dev fallback).
            cursor.execute(
                "SELECT reason FROM banned_ips WHERE ip=%s AND (expires_at IS NULL OR expires_at > NOW())",
                (ip,),
            )
        row = cursor.fetchone()
    finally:
        cursor.close()
        db.close()
    if row:
        return jsonify({"ok": False, "msg": "Access denied."}), 403


# Credential-check endpoints are deliberately exempt from the blanket WAF
# block below. blueprints/auth.py's admin_login already detects
# injection-shaped identifiers itself (_INJECTION_PATTERN_RE) and responds
# with the same generic "Invalid credentials" any wrong password gets -- a
# real, tested design choice (tests/test_auth_routes.py,
# tests/test_comprehensive.py's TestInputValidation) so a probing attacker
# can't use a distinguishing WAF-block response to tell "malicious-shaped
# input" apart from "wrong password" on the one surface where that
# distinction would be most valuable to them. A hard 403 here would both
# leak that signal and short-circuit the existing detection before it runs.
# Not a real exposure either way -- every one of these routes only ever
# uses the identifier in a parameterized query, never executes or reflects
# it -- so exempting them costs no actual protection.
_WAF_EXEMPT_PATHS = {"/login", "/admin_login", "/employee_login", "/api/login", "/api/employee/login"}


@app.before_request
def _waf_inspect_request():
    """Native signature-based WAF: rejects requests whose query string,
    form fields, JSON body, uploaded filenames, or path segments match a
    known SQLi/XSS/path-traversal shape. Runs immediately after the IP ban
    check (before session/CSRF/route logic) so a malicious payload is
    rejected before it can reach anything else. See utils/waf.py."""
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    if request.path in _WAF_EXEMPT_PATHS:
        return
    hit = waf.inspect_request(request)
    if hit:
        event_type, field, matched = hit
        ip = request.remote_addr
        log_security_event(
            event_type, f"WAF blocked request: signature matched in {field}",
            level="ERROR", field=field, matched=matched[:100],
        )
        waf.record_breach_and_maybe_ban(ip, f"Repeated WAF blocks ({event_type})")
        return jsonify({"ok": False, "msg": "Request blocked by security policy."}), 403


@app.before_request
def _enforce_session_lifetime():
    """Expire sessions that are older than the absolute max age, regardless of activity."""
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    created = session.get("_session_created")
    if created and (time.time() - created) > _SESSION_MAX_AGE:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "msg": "Session expired. Please log in again."}), 401
        flash("Your session expired. Please log in again.", "warning")
        return redirect(url_for("auth.admin_login"))


@app.before_request
def _enforce_idle_timeout():
    """Expire sessions after N minutes of *inactivity* -- distinct from the
    absolute max-age check above, which only catches a session once it's
    lived 8 hours regardless of how recently it was used. The threshold
    itself (company_settings.session_timeout, admin-configurable 5-1440 min
    via Settings)
    used to be stored and displayed in the UI but was never actually
    enforced anywhere -- this closes that gap. Reads through
    get_company_settings()'s existing 60s cache rather than querying the DB
    on every request.
    """
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    if not (session.get("admin_logged_in") or session.get("employee_id")):
        return
    now = time.time()
    last_activity = session.get("_last_activity")
    if last_activity:
        timeout_minutes = get_company_settings().get("session_timeout", 30)
        if (now - last_activity) > timeout_minutes * 60:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "msg": "Session expired due to inactivity. Please log in again."}), 401
            flash("Your session expired due to inactivity. Please log in again.", "warning")
            return redirect(url_for("auth.admin_login"))
    session["_last_activity"] = now


# Roles that count as "administrative/HR" for the mandatory-MFA requirement
# below -- every role that can reach admin-side data or actions.
_MANDATORY_MFA_ROLES = {"admin", "manager", "hr"}

# Routes reachable by an admin/manager/soc_analyst/hr session that has NOT
# yet enrolled TOTP -- must stay small and deliberate. Anything not on this
# list is unreachable until enrollment is complete, which is the point (a
# genuine "mandatory," not a step-up an admin can defer indefinitely).
_MANDATORY_MFA_EXEMPT_PATHS = {
    "/admin/mfa-required", "/api/settings/2fa/setup", "/api/settings/2fa/enable",
    "/logout", "/admin_login", "/hr_login"
}

# All four MFA/2FA gates below default ON (secure by default) -- set any of
# them to "false" in .env only for a deliberate, documented reason (e.g. a
# local dev box with no authenticator app handy). This used to default OFF
# despite every docstring/comment near these gates claiming otherwise (see
# _enforce_admin_mfa_enrollment below, and the .get(key, True) fallbacks
# throughout blueprints/auth.py and blueprints/platform_admin.py that could
# never actually apply -- app.config[...] is unconditionally set here on
# every boot, so those fallback defaults were dead code, not a real
# secure-by-default posture).
app.config["MANDATORY_ADMIN_MFA"] = os.environ.get("MANDATORY_ADMIN_MFA", "True").lower() in ("true", "1", "yes")
app.config["MANDATORY_LOGIN_MFA"] = os.environ.get("MANDATORY_LOGIN_MFA", "True").lower() in ("true", "1", "yes")
app.config["MANDATORY_PLATFORM_ADMIN_MFA"] = os.environ.get("MANDATORY_PLATFORM_ADMIN_MFA", "True").lower() in ("true", "1", "yes")
# Email Settings step-up gate (utils/auth.py's require_email_2fa) -- same
# secure-by-default posture as the three flags above.
app.config["REQUIRE_EMAIL_2FA"] = os.environ.get("REQUIRE_EMAIL_2FA", "True").lower() in ("true", "1", "yes")


@app.before_request
def _enforce_admin_mfa_enrollment():
    """Hard requirement: an admin/manager/soc_analyst session with TOTP not
    yet enrolled can reach nothing except the enrollment flow itself (and
    login/logout) -- no grace period, no dismissible nag. This is distinct
    from the existing TOTP *step-up* gates (Email Settings, Security hub,
    SOC), which only apply once already enrolled; this is what forces
    enrollment to happen in the first place.

    MANDATORY_ADMIN_MFA defaults on (app.config[...] is set unconditionally
    at boot, above -- direct dict access here, not a .get(..., True)
    fallback that could never actually apply). Tests disable it globally
    (matching the existing pattern of disabling flask-limiter under pytest)
    since most of the suite logs in admin sessions directly via
    session_transaction without walking through enrollment, and re-enable
    it only in the tests that specifically exercise this gate.
    """
    if not current_app.config["MANDATORY_ADMIN_MFA"]:
        return
    if request.path.startswith("/static/") or request.path == "/healthz":
        return
    if request.path in _MANDATORY_MFA_EXEMPT_PATHS:
        return
    username = session.get("admin_username")
    if not (session.get("admin_logged_in") and username):
        return
    if session.get("admin_role", "admin") not in _MANDATORY_MFA_ROLES:
        return
    from utils.totp import is_totp_enabled_cached
    if is_totp_enabled_cached(username):
        return
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "msg": "MFA enrollment required before continuing.",
                        "redirect": "/admin/mfa-required"}), 403
    return redirect("/admin/mfa-required")


@app.before_request
def _block_private_uploads():
    """Uploaded HR documents used to live under static/ (and the `documents`
    volume is still mounted at static/employee_docs), so a legacy file left in
    either folder would be handed to anyone who can guess its URL by Flask's
    static handler. They are only ever served through authenticated routes."""
    if request.path.startswith(("/static/employee_docs/", "/static/onboarding_docs/")):
        abort(404)


_CSRF_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CSRF_LOGIN_PATHS = frozenset({"/login", "/admin_login", "/hr_login"})
# Browser telemetry sinks (blueprints/core.py). The browser's own report-uri
# mechanism fires /csp-report automatically on every CSP violation -- there is
# no page JS in the loop to attach an X-CSRF-Token and no <form> to carry one,
# so it can never pass the check (every report used to be silently 403'd).
# /api/client-log is the error beacon and must also work for logged-in users.
# Both are write-only log sinks anyone can already POST to, so a forged request
# gains an attacker nothing.
_CSRF_BEACON_PATHS = frozenset({"/csp-report", "/api/client-log"})


def _session_is_authenticated():
    return bool(session.get("admin_logged_in") or session.get("employee_id")
                or session.get("platform_admin_logged_in"))


def _csrf_exempt(path, has_bearer, session_authenticated):
    """Whether a state-changing request skips the CSRF token check.

    CSRF only matters when the browser attaches an ambient credential (the
    session cookie) that a forged cross-site request can ride. Under /api/
    that is NOT true of every route -- dozens of them (hr_accounts, settings,
    email-blast, ...) are @admin_required/@employee_required and authenticate
    by session, so a blanket /api/ exemption left them relying on
    SameSite=Strict alone. An /api/ request is therefore exempt only if it
    carries a Bearer token (can't be forged cross-site without a CORS
    preflight) or has no logged-in session to ride (mobile/pre-login calls:
    /api/login, /api/employee/signup, /api/leads, ...)."""
    if path in _CSRF_LOGIN_PATHS or path in _CSRF_BEACON_PATHS:
        return True  # login routes handle credential verification & rate-limiting
    if path.startswith("/api/"):
        return has_bearer or not session_authenticated
    return False


@app.before_request
def _enforce_csrf():
    if request.method not in _CSRF_METHODS:
        return
    if current_app.testing:
        return  # CSRF disabled in test mode; Bearer-token tests handle auth separately
    if request.path.startswith("/webhooks/"):
        # Server-to-server callbacks (Razorpay etc.) -- no session cookie
        # exists to carry a CSRF token in the first place; authenticated by
        # request signature instead (blueprints/webhooks.py's generic
        # /webhooks/<provider> route).
        return
    if _csrf_exempt(request.path,
                    request.headers.get("Authorization", "").startswith("Bearer "),
                    _session_is_authenticated()):
        return
    # NOTE: We intentionally do NOT skip JSON requests here. The auto-inject
    # script (_inject_csrf_meta) adds X-CSRF-Token to every fetch() call, so
    # legitimate JSON POSTs from the web UI already carry the token.
    # Skipping CSRF for is_json would allow XSS payloads to forge state-changing
    # JSON requests without a token.
    token = session.get("_csrf")
    submitted = (request.form.get("_csrf_token")
                 or request.headers.get("X-CSRF-Token")
                 or request.headers.get("X-CSRFToken"))
    if not token or not submitted or not secrets.compare_digest(str(token), str(submitted)):
        # Browser form submissions: redirect to login so the user gets a fresh
        # session+token. Gated on the request's actual Content-Type, not
        # Accept/X-Requested-With -- those are unreliable signals for
        # "this is a real full-page form submission, not a background
        # fetch() call": a bare fetch() with no explicit headers sends
        # Accept: */* (which accept_mimetypes.accept_html treats as
        # accepting HTML too) and never sets X-Requested-With on its own,
        # so a JSON-posting fetch() call whose CSRF token expired was
        # taking this branch by mistake. Its JS never follows the redirect
        # or renders the flash, but flash() still queued the message into
        # the session -- silently, repeatedly, once per failed background
        # call -- until the user's next *real* page load (one that calls
        # get_flashed_messages()) dumped every accumulated copy at once.
        # A genuine <form method="post"> is the only thing that can ever
        # carry these two Content-Types; every legitimate fetch() POST in
        # this app sends JSON instead (see the comment above on why JSON
        # isn't exempted from the CSRF check itself).
        if (request.mimetype in ("application/x-www-form-urlencoded", "multipart/form-data")
                and request.accept_mimetypes.accept_html):
            flash("Your session expired. Please log in again.", "warning")
            # There is no standalone "employee_login" endpoint -- employee and
            # admin credentials are both checked by the one unified /login
            # page (auth.admin_login).
            if request.path.startswith("/super_admin"):
                login_url = "/super_admin/login"
            else:
                login_url = url_for("auth.admin_login")
            return redirect(login_url)
        return jsonify({"ok": False, "msg": "Session expired. Please refresh and try again."}), 403


_CSRF_HEAD_RE = re.compile(rb'</head>', re.IGNORECASE)
_CSRF_BODY_RE = re.compile(rb'</body>', re.IGNORECASE)
# Matches <script>/<style> tags without a nonce -- used to inject CSP nonces
_SCRIPT_TAG_RE = re.compile(rb'<script(?!\s[^>]*\bnonce\b)(?=[\s>])', re.IGNORECASE)
_STYLE_TAG_RE = re.compile(rb'<style(?!\s[^>]*\bnonce\b)(?=[\s>])', re.IGNORECASE)
# Capture inline event-handler values for dynamic CSP sha256 hash generation.
# Two patterns: double-quoted and single-quoted attribute values.
_CSP_EV_DQ = re.compile(
    rb'\bon(?:animationend|blur|change|click|contextmenu|copy|cut|dblclick|drag|dragend'
    rb'|dragenter|dragleave|dragover|dragstart|drop|error|focus|input|invalid|keydown|keypress'
    rb'|keyup|load|mousedown|mousemove|mouseout|mouseover|mouseup|paste|pointerdown'
    rb'|pointermove|pointerup|reset|scroll|select|submit|touchend|touchmove|touchstart'
    rb'|transitionend|wheel)\s*=\s*"([^"]*)"',
    re.IGNORECASE,
)
_CSP_EV_SQ = re.compile(
    rb"\bon(?:animationend|blur|change|click|contextmenu|copy|cut|dblclick|drag|dragend"
    rb"|dragenter|dragleave|dragover|dragstart|drop|error|focus|input|invalid|keydown|keypress"
    rb"|keyup|load|mousedown|mousemove|mouseout|mouseover|mouseup|paste|pointerdown"
    rb"|pointermove|pointerup|reset|scroll|select|submit|touchend|touchmove|touchstart"
    rb"|transitionend|wheel)\s*=\s*'([^']*)'",
    re.IGNORECASE,
)
# Same idea for inline style="" attribute values, so style-src-attr can drop
# 'unsafe-inline' in favor of per-response sha256 hashes.
_CSP_STYLE_DQ = re.compile(rb'\bstyle\s*=\s*"([^"]*)"', re.IGNORECASE)
_CSP_STYLE_SQ = re.compile(rb"\bstyle\s*=\s*'([^']*)'", re.IGNORECASE)
_CSRF_SCRIPT = (
    b'<script>(function(){'
    b'var m=document.querySelector(\'meta[name="csrf-token"]\');'
    b'if(!m)return;'
    b'window._csrfToken=function(){return m.content;};'
    b'var _of=window.fetch;'
    b'window.fetch=function(u,o){'
    b'o=o||{};'
    b'var mt=(o.method||"GET").toUpperCase();'
    b'if(mt==="POST"||mt==="PUT"||mt==="PATCH"||mt==="DELETE"){'
    b'if(o.headers instanceof Headers){'
    b'if(!o.headers.has("X-CSRF-Token"))o.headers.set("X-CSRF-Token",m.content);'
    b'}else{o.headers=Object.assign({},o.headers||{});'
    b'if(!o.headers["X-CSRF-Token"])o.headers["X-CSRF-Token"]=m.content;}}'
    b'return _of.call(this,u,o);};'
    b'document.addEventListener("DOMContentLoaded",function(){'
    b'document.querySelectorAll("form").forEach(function(f){'
    b'if(f.method.toLowerCase()==="post"&&!f.querySelector(\'[name="_csrf_token"]\')){'
    b'var i=document.createElement("input");'
    b'i.type="hidden";i.name="_csrf_token";i.value=m.content;'
    b'f.prepend(i);}});});})();</script>'
)
# Session kill-switch listener -- only injected on pages rendered for an
# authenticated session (see _inject_csrf_meta below), since the SSE
# endpoint itself requires auth and there's nothing to listen for on public
# pages. EventSource auto-reconnects on its own when a bounded-duration
# stream closes normally (see /api/session/risk-stream), so onerror is
# deliberately a no-op rather than manual reconnect logic.
#
# IMPORTANT, and worth being explicit about: this client-side wipe/redirect
# is a UX nicety, not the security boundary. The session cookie is
# HttpOnly by design (extensions.py), so this script cannot read or clear
# it -- that's what HttpOnly means, and weakening it to let JS touch the
# session cookie would be a strictly worse trade for a cosmetic gain. The
# real kill switch is server-side: utils/auth.py's _reject_if_compromised()
# rejects every request on a compromised session regardless of whether
# this script ever runs, and the server's own redirect response is what
# actually clears the session cookie via Set-Cookie. The cookie-wipe loop
# below only ever affects non-HttpOnly cookies (e.g. anything analytics-
# related some future page might add) -- harmless to include, not load-
# bearing for security.
# Frontend (browser) error reporter -> POST /api/client-log (blueprints/core.py).
# Capped at 8 reports per page load, de-duplicated, and sends only the page
# *path* (never the query string, which can carry reset tokens).
_CLIENT_LOG_SCRIPT = (
    b'<script>(function(){'
    b'var n=0,seen={};'
    b'function send(t,m,s,l){'
    b'if(n>=8)return;var k=t+"|"+m+"|"+s+"|"+l;if(seen[k])return;seen[k]=1;n++;'
    b'try{var b=JSON.stringify({type:t,message:String(m||"").slice(0,300),'
    b'source:String(s||"").slice(0,200),line:l||"",page:location.pathname});'
    b'if(navigator.sendBeacon){navigator.sendBeacon("/api/client-log",new Blob([b],{type:"application/json"}));}'
    b'else{fetch("/api/client-log",{method:"POST",headers:{"Content-Type":"application/json"},body:b,keepalive:true});}'
    b'}catch(x){}}'
    b'window.addEventListener("error",function(e){'
    b'if(e.target&&e.target!==window){var u=e.target.src||e.target.href;'
    b'if(u)send("resource_error","Failed to load "+String(e.target.tagName||"resource").toLowerCase(),String(u).split("?")[0],"");}'
    b'else send("js_error",e.message,String(e.filename||"").split("?")[0],e.lineno);},true);'
    b'window.addEventListener("unhandledrejection",function(e){'
    b'var r=e.reason;send("unhandled_rejection",r&&r.message?r.message:r,"","");});'
    b'})();</script>'
)
_KILLSWITCH_SCRIPT = (
    b'<script>(function(){'
    b'if(typeof EventSource==="undefined")return;'
    b'var es=new EventSource("/api/session/risk-stream");'
    b'function kill(){'
    b'es.close();'
    b'try{alert("Security alert: unusual activity was detected on your account and this session has been '
    b'ended. Please contact your administrator.");}catch(e){}'
    b'try{localStorage.clear();}catch(e){}'
    b'try{sessionStorage.clear();}catch(e){}'
    b'try{document.cookie.split(";").forEach(function(c){'
    b'var n=c.split("=")[0].trim();'
    b'if(n)document.cookie=n+"=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";'
    b'});}catch(e){}'
    b'location.replace("/security_lockout");'
    b'}'
    b'es.addEventListener("compromised",kill);'
    b'es.onerror=function(){};'
    b'})();</script>'
)


@app.before_request
def _set_csp_nonce():
    from flask import g
    g.csp_nonce = secrets.token_urlsafe(16)


_SETTINGS_PATHS = {"/settings", "/admin_set_recovery_email",
                   "/save_security_settings", "/toggle_auth_feature",
                   "/toggle_fingerprint", "/save_company_code", "/save_geo_settings",
                   "/save_company_info", "/toggle_feature"}


@app.after_request
def _security_headers(response):
    # ── Invariant headers (every response, every content-type) ──────────────
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=(self)"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Server"] = "HRzest"
    # ── HSTS -- always set so dev tools and scanners see the policy ──────────
    # Short max-age in dev (5 min) avoids bricking non-HTTPS local access if
    # the cert is later removed; 2 years in prod meets HSTS preload requirements.
    is_prod = os.environ.get("APP_ENV", "production") != "development"
    hsts_age = 63072000 if is_prod else 300
    hsts_directives = f"max-age={hsts_age}; includeSubDomains"
    if is_prod:
        hsts_directives += "; preload"
    response.headers["Strict-Transport-Security"] = hsts_directives
    # ── CSP -- set on ALL response types, not just text/html ─────────────────
    # API/JSON responses also need CSP (prevents MIME-sniffing abuse) and
    # provides a consistent security surface for scanners.
    ct = response.content_type or ""
    if "text/html" in ct:
        from flask import g
        nonce = getattr(g, "csp_nonce", "")
        # Scan for inline event-handler values and compute sha256 hashes so
        # they pass CSP without needing 'unsafe-inline'.
        try:
            data = response.get_data()
            _ev_hashes: set = set()
            for _pat in (_CSP_EV_DQ, _CSP_EV_SQ):
                for _m in _pat.finditer(data):
                    _body = _html.unescape(_m.group(1).decode("utf-8", errors="replace"))
                    _ev_hashes.add(
                        "'sha256-" + base64.b64encode(
                            hashlib.sha256(_body.encode("utf-8")).digest()
                        ).decode() + "'"
                    )
        except Exception:
            _ev_hashes = set()
        _unsafe_hashes = " 'unsafe-hashes'" if _ev_hashes else ""
        _hash_src = (" " + " ".join(sorted(_ev_hashes))) if _ev_hashes else ""
        # Same treatment for inline style="" attribute values, so
        # style-src-attr can drop 'unsafe-inline' -- every remaining
        # dynamic style/cssText write in app JS was migrated to
        # el.style.setProperty()/Object.assign(el.style, ...), which CSP
        # doesn't restrict, so only these server-rendered attributes need
        # hashing.
        try:
            _style_hashes: set = set()
            for _pat in (_CSP_STYLE_DQ, _CSP_STYLE_SQ):
                for _m in _pat.finditer(data):
                    _body = _html.unescape(_m.group(1).decode("utf-8", errors="replace"))
                    _style_hashes.add(
                        "'sha256-" + base64.b64encode(
                            hashlib.sha256(_body.encode("utf-8")).digest()
                        ).decode() + "'"
                    )
        except Exception:
            _style_hashes = set()
        _style_unsafe_hashes = " 'unsafe-hashes'" if _style_hashes else ""
        _style_hash_src = (" " + " ".join(sorted(_style_hashes))) if _style_hashes else ""
        # Cloudflare Turnstile (admin_login CAPTCHA) and employee portal
        # loopback agent get path-scoped exceptions only.
        _is_turnstile_page = request.path in ("/admin_login", "/login")
        _turnstile_src = " https://challenges.cloudflare.com" if _is_turnstile_page else ""
        # Razorpay Checkout.js (org.py's /create_org/pay, /create_org/
        # setup_trial; billing_dunning.py's /pay_overdue_bill; seats.py's
        # /settings/seats; admin_views.py's /settings) -- without this, the
        # default 'self'-only script-src silently blocks
        # checkout.razorpay.com/v1/checkout.js from ever loading, so the
        # "Pay" button's onclick handler runs but `new Razorpay(...)` never
        # gets a Razorpay global to construct, and clicking it does
        # nothing. frame-src/connect-src additions are what Checkout.js's
        # own embedded iframe (card/UPI entry) and its API/analytics calls
        # need once the script itself loads.
        _is_razorpay_page = (
            request.path in ("/pay_overdue_bill", "/settings/seats", "/settings")
            or request.path.startswith("/create_org/pay/")
            or request.path.startswith("/create_org/setup_trial/")
        )
        _razorpay_script_src = " https://checkout.razorpay.com https://cdn.razorpay.com" if _is_razorpay_page else ""
        _razorpay_frame_src = "https://checkout.razorpay.com https://api.razorpay.com" if _is_razorpay_page else ""
        _razorpay_connect_src = " https://api.razorpay.com https://lumberjack.razorpay.com" if _is_razorpay_page else ""
        # /super_admin/soc (blueprints/platform_admin.py) embeds the standalone
        # SOC dashboard in an iframe. Without this, the default frame-src 'none'
        # makes the browser refuse to load it and the page shows an empty box.
        # Only that service's own origin (scheme://host:port of SOC_DASHBOARD_URL,
        # never the path or the token) is allowed, and only on this one page.
        _soc_frame_src = ""
        if request.path == "/super_admin/soc":
            _soc_url = urlsplit(os.environ.get("SOC_DASHBOARD_URL", ""))
            if _soc_url.scheme in ("http", "https") and _soc_url.netloc:
                _soc_frame_src = f"{_soc_url.scheme}://{_soc_url.netloc}"
        if _is_turnstile_page and _is_razorpay_page:
            _frame_src = "https://challenges.cloudflare.com " + _razorpay_frame_src
        elif _is_turnstile_page:
            _frame_src = "https://challenges.cloudflare.com"
        elif _is_razorpay_page:
            _frame_src = _razorpay_frame_src
        elif _soc_frame_src:
            _frame_src = _soc_frame_src
        else:
            _frame_src = "'none'"
        _is_employee_portal_page = request.path == "/employee_portal"
        _agent_src = " http://127.0.0.1:47823" if _is_employee_portal_page else ""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'{_unsafe_hashes}{_hash_src}{_turnstile_src}{_razorpay_script_src}; "
            f"style-src-elem 'self' 'nonce-{nonce}'; "
            f"style-src-attr{_style_unsafe_hashes}{_style_hash_src}; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            f"connect-src 'self'{_turnstile_src}{_agent_src}{_razorpay_connect_src}; "
            f"frame-src {_frame_src}; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "report-uri /csp-report;"
        )
    else:
        # Non-HTML responses: minimal restrictive CSP -- blocks MIME sniffing
        # and any attempt to embed API responses as documents or frames.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; object-src 'none';"
        )
    return response


# csp_report migrated to blueprints/core.py


@app.after_request
def _bust_settings_cache(response):
    if request.method == "POST" and request.path in _SETTINGS_PATHS:
        invalidate_settings_cache()
    return response


@app.after_request
def _inject_csrf_meta(response):
    """Inject CSRF meta tag and auto-inject script into every HTML page.

    CSP nonce injection (the second half below) must run for EVERY
    text/html response regardless of status code -- _security_headers sets
    a nonce-requiring CSP header unconditionally, including on error pages
    (404/403/500). This function used to skip everything for status>=300,
    which meant every error page shipped a <style> tag with no nonce while
    the CSP header demanded one -- the browser correctly blocked it, which
    is the "Applying inline style violates CSP" error seen on any page that
    hit an error handler. CSRF meta-tag/script injection still only makes
    sense on normal (status<300) pages, so that part stays gated.
    """
    if not response.content_type.startswith("text/html"):
        return response
    try:
        from flask import g
        data = response.get_data()
        if response.status_code < 300:
            token = _csrf_token()
            meta = f'<meta name="csrf-token" content="{token}" />'.encode()
            data = _CSRF_HEAD_RE.sub(meta + b'</head>', data, count=1)
            _body_scripts = _CSRF_SCRIPT + _CLIENT_LOG_SCRIPT
            if session.get("admin_logged_in") or session.get("employee_id"):
                _body_scripts += _KILLSWITCH_SCRIPT
            data = _CSRF_BODY_RE.sub(_body_scripts + b'</body>', data, count=1)
        nonce = getattr(g, "csp_nonce", None)
        if nonce:
            nb = nonce.encode()
            data = _SCRIPT_TAG_RE.sub(b'<script nonce="' + nb + b'"', data)
            data = _STYLE_TAG_RE.sub(b'<style nonce="' + nb + b'"', data)
        response.set_data(data)
    except Exception as exc:
        # Silent failure here means CSRF-token/killswitch injection and CSP
        # nonce rewriting both silently no-op on this response -- worth
        # knowing about even though the response still goes out.
        app_log.warning("Response HTML injection (CSRF token/CSP nonce) failed: %s", exc, exc_info=True)
    return response


# ---------------- AUDIT LOGGING ----------------
# (Consolidated onto utils/helpers.py -- see import block above.)

# ---------------- FILE UPLOAD VALIDATION ----------------
# (Consolidated onto utils/helpers.py -- see the import block above. app.py
# used to carry its own duplicate of _scan_for_malware/_validate_upload/
# _validate_image_file, which meant the security-event logging added to
# the utils/helpers.py versions never reached any of app.py's 10 real
# upload call sites. Same bug class as the auth-decorator duplication
# fixed earlier this session, found the same way: by checking whether an
# edited function's call sites actually resolved to the edited copy.)

# ---------------- COMPANY SETTINGS (with 60-second TTL cache) ----------------
# Consolidated onto utils/helpers.py -- app.py used to carry its own
# separate _co_cache/_auth_cache dicts. Both copies were logically
# identical, but being separate meant a settings change saved through
# app.py's real routes (which call app.py's own invalidate_settings_cache())
# would never clear a cache a future blueprint read through
# utils.helpers.get_company_settings() -- up to 60 seconds of serving a
# stale company name/logo/setup_done flag to any code path using the
# other copy. One cache now, so one invalidation reaches everyone.

# _VALID_CFS_COLS / _upsert_co_feature / _upsert_co_features consolidated
# onto utils/helpers.py (see import block near the top of this file) -- that
# copy double-gates column names (frozenset membership + identifier regex)
# where this one only checked the frozenset. Not independently exploitable
# on its own (the frozenset is an exact-match allowlist, not a pattern, so
# nothing outside the 19 known-safe column names could ever reach the
# f-string SQL below either way) but the two copies had also drifted
# functionally: this file's allowlist had 5 columns
# (shift_start/shift_half/shift_end/holiday_pay/leave_pay) that the
# utils/helpers.py copy was missing, since added there to match.


@app.context_processor
def inject_company():
    return {"co": get_company_settings()}


# Office location, shift timings, and deduction rates now live solely in
# utils/config.py -- see the `import utils.config as cfg` note above.
# Startup load still happens here (same timing as before: once, at import,
# inside an app context) since nothing else in this file's import order
# guarantees the DB is reachable earlier than this point.
with app.app_context():
    try:
        cfg.load_default_shift()
        cfg.load_salary_rules()
    except Exception as exc:
        # Falls back to cfg's hardcoded defaults, silently -- worth logging
        # since a DB that isn't reachable yet at this exact import-time
        # point means shift/salary config stays wrong until the next
        # successful load, not just this one startup.
        app_log.warning("Startup load of default shift/salary config failed: %s", exc, exc_info=True)

# ── PII Encryption ────────────────────────────────────────────────
# Consolidated onto utils/helpers.py (see import block near the top of this
# file) -- that was the weaker of the two copies (silent no-op on a missing
# key, in every environment); it's now the strict, fail-secure canonical
# version instead of being deleted, since app.py importing at module load
# time means its bootstrap check already runs before this file finishes
# loading either way.


# ---------------- DB CONTEXT MANAGER ----------------
# (Consolidated onto utils/helpers.py -- see import block above. Note:
# utils/auth.py also carries its own small, identical copy of this same
# contextmanager -- out of scope for this pass, which covers app.py +
# utils/helpers.py + email_utils.py + attendance_utils.py + config.py;
# low priority since it's self-contained within the utils package and
# behaviorally identical.)

# ---------------- DB MIGRATION ----------------
# Extracted into migrations/ (Wave 3 item 19) -- custom, hand-rolled,
# idempotent startup migrations (CREATE TABLE IF NOT EXISTS / ALTER TABLE
# ADD COLUMN IF NOT EXISTS), not Alembic/Flask-Migrate. Re-exported here so
# every existing `from app import init_db` (etc.) call site -- wsgi.py,
# blueprints/org.py, tests/ -- keeps working unchanged.
from migrations import init_db, init_master_db, init_tenant_db, _run_migrations_for_all_tenants  # noqa: F401


# ---------------- NOTIFICATION HELPER ----------------
# (Consolidated onto utils/helpers.py -- see import block above.)

# ---------------- ATTENDANCE HELPERS ----------------
# (Consolidated onto utils/attendance_utils.py -- see import block above.
# Became a safe mechanical merge only after the cfg.SHIFT_START migration
# above: before that, this file's copies used bare SHIFT_START/etc. globals
# while utils/attendance_utils.py's copies already used cfg.SHIFT_START --
# genuinely different behavior under a stale-cache scenario, not just
# duplicated source. Now both reference the same cfg module state, so
# there's nothing left to diverge.)

# ---------------- EMAIL HELPERS ----------------
# (Consolidated onto utils/email_utils.py -- see the import block above.)

# build_salary_slip_html consolidated onto utils/salary_utils.py
# compute_salary_entry consolidated onto utils/salary_utils.py


# ---------------- ERROR HANDLERS ----------------

# _error_page consolidated onto utils/helpers.py -- that copy rendered a
# template (templates/error.html) that doesn't exist anywhere in this
# project; every call would have raised TemplateNotFound. Replaced with
# this file's working implementation instead of fixing the missing
# template, since this is what every real error page has actually used.

# ---------------- ERROR ALERTING (malfunction detection) ----------------
# The catch-all exception handler below tells users "the team has been
# notified" -- this is what actually makes that true. Emails admins on
# unhandled errors, deduped by error signature so one hot failing endpoint
# can't flood inboxes or the email_queue table.
_error_alert_cache = {}
_error_alert_lock = threading.Lock()
_ERROR_ALERT_COOLDOWN = 900  # 15 min -- same error signature won't re-alert sooner


def _alert_on_error(tb_text, context=""):
    """Best-effort: any failure here is swallowed so alerting can never
    mask or crash on top of the original error being reported."""
    try:
        # Dedup key = exception type + the line that actually raised it, not
        # the full traceback (which varies request-to-request -- different
        # IDs, line numbers in called code, etc. would defeat deduping).
        last_line = tb_text.strip().splitlines()[-1] if tb_text.strip() else "unknown"
        sig = hashlib.sha256(last_line.encode()).hexdigest()[:16]
        now = time.time()
        with _error_alert_lock:
            last_sent = _error_alert_cache.get(sig)
            if last_sent and now - last_sent < _ERROR_ALERT_COOLDOWN:
                return
            _error_alert_cache[sig] = now
            if len(_error_alert_cache) > 500:  # cap unbounded growth
                _error_alert_cache.clear()

        cfg = get_email_config()
        if not cfg:
            return
        admins = get_admin_emails()
        if not admins:
            return

        body = (
            "<pre style='white-space:pre-wrap;font-family:monospace;font-size:13px'>"
            f"Time: {datetime.datetime.now().isoformat()}\n"
            f"Route: {_html.escape(context or 'unknown')}\n"
            f"Method: {_html.escape(request.method if request else 'n/a')}\n"
            f"Remote IP: {_html.escape(request.remote_addr or '') if request else 'n/a'}\n\n"
            f"{_html.escape(tb_text)}</pre>"
        )
        subject = f"⚠️ Application error -- {context or 'unknown route'}"
        for admin_email in admins:
            send_email_async(admin_email, subject, body, cfg)
    except Exception as _alert_err:
        app_log.error("Failed to send error alert email: %s", _alert_err)


@app.errorhandler(429)
def rate_limit_exceeded(e):
    """Flask-Limiter raises this (a werkzeug HTTPException) whenever any
    @limiter.limit(...) threshold is crossed. Distinct from _check_login_lockout's
    DB-backed account lockout (utils/auth.py) -- this is the generic per-route
    rate ceiling. Logs every breach (feeds the same auto-ban counter WAF
    blocks use -- see utils/waf.py) instead of letting it pass through as a
    silent, unlogged 429."""
    waf.record_breach_and_maybe_ban(request.remote_addr, "Repeated rate-limit breaches")
    log_security_event("ratelimit.exceeded", f"Rate limit exceeded: {getattr(e, 'description', '')}",
                       level="WARNING")
    is_ajax = (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    if is_ajax:
        return jsonify({"ok": False, "msg": "Too many requests. Please slow down and try again shortly."}), 429
    return _error_page(429, "⏳", "Too Many Requests",
                       "You've made too many requests in a short period.",
                       "Please wait a moment before trying again.")


@app.errorhandler(404)
def not_found(e):
    return _error_page(404, "🔍", "Page Not Found",
                       "The page you're looking for doesn't exist or has been moved.",
                       "Check the URL or use one of the links below to get back on track.")


@app.errorhandler(403)
def forbidden(e):
    return _error_page(403, "🔒", "Access Denied",
                       "You don't have permission to access this page.",
                       "Please log in with the right account or contact your administrator.")


@app.errorhandler(500)
def internal_error(e):
    tb = _traceback.format_exc()
    app_log.error("500 error: %s", tb.replace('\n', '\\n'))
    _alert_on_error(tb, context=request.path if request else "")
    # WARNING, not ERROR: _alert_on_error above already alerts. This row is for
    # log analysis (error type only -- never the traceback).
    log_security_event("app.server_error", "Unhandled server error", level="WARNING",
                       error_type=type(getattr(e, "original_exception", None) or e).__name__)
    return _error_page(500, "⚙️", "Internal Server Error",
                       "Something went wrong on our end. The error has been logged.",
                       "Please try again in a moment or contact your administrator.")


@app.errorhandler(Exception)
def unhandled_exception(e):
    if isinstance(e, HTTPException):
        return _error_page(e.code, "⚠️", e.name,
                           "The page you requested could not be processed.",
                           "Use the buttons below to navigate back.")
    tb = _traceback.format_exc()
    app_log.error("Unhandled exception: %s", tb.replace('\n', '\\n'))
    _alert_on_error(tb, context=request.path if request else "")
    log_security_event("app.server_error", "Unhandled server error", level="WARNING",
                       error_type=type(e).__name__)
    return _error_page(500, "⚙️", "Internal Server Error",
                       "An unexpected error occurred. The team has been notified.",
                       "Please try again or contact your administrator.")

# ---------------- HOME ----------------
# home migrated to blueprints/core.py

# ---------------- ADMIN LOGIN ----------------
# setup_wizard migrated to blueprints/auth.py


# admin_login migrated to blueprints/auth.py

# ---------------- LOGOUT ----------------
# logout migrated to blueprints/auth.py

# ---------------- SESSION KILL-SWITCH: SSE PUSH ----------------
# session_risk_stream migrated to blueprints/core.py

# ---------------- SESSION KILL-SWITCH: LOCKOUT PAGE ----------------
# security_lockout migrated to blueprints/core.py

# ---------------- ADMIN DASHBOARD ----------------
# admin migrated to blueprints/admin_views.py

# ---------------- LIVE DASHBOARD API ----------------
# dashboard_live migrated to blueprints/admin_views.py

# ---------------- CHART DATA API ----------------
# attendance_chart_data migrated to blueprints/admin_views.py


# ---------------- TODAY FILTERED VIEWS ----------------
# _today_pending_counts migrated to blueprints/attendance.py

# today_present migrated to blueprints/attendance.py

# today_absent migrated to blueprints/attendance.py

# today_late migrated to blueprints/attendance.py

# ---------------- ADMIN ACTIONS ----------------
# admin_action migrated to blueprints/employees.py

# ---------------- SETTINGS (unified) ----------------
# settings_page migrated to blueprints/admin_views.py

# ---------------- SAVE DEFAULT ONBOARDING TEMPLATE ----------------
# save_default_onboarding_template migrated to blueprints/admin_views.py

# ---------------- SAVE SALARY RULES ----------------
# save_salary_rules migrated to blueprints/admin_views.py

# ---------------- TOGGLE AUTH METHOD ----------------
# _TOGGLE_COLUMN_MAP / _TOGGLE_LABEL_MAP moved to blueprints/admin_views.py

# toggle_auth_method migrated to blueprints/admin_views.py

# toggle_fingerprint migrated to blueprints/admin_views.py

# ---------------- SAVE COMPANY CODE ----------------
# save_company_code migrated to blueprints/admin_views.py

# ---------------- SAVE COMPANY INFO ----------------
# save_company_info migrated to blueprints/admin_views.py

# ---------------- TOGGLE FEATURE (AJAX) ----------------
# toggle_feature migrated to blueprints/admin_views.py

# ---------------- SAVE GEO RADIUS ----------------
# save_geo_radius migrated to blueprints/admin_views.py

# ---------------- SAVE SECURITY SETTINGS ----------------
# save_security_settings migrated to blueprints/admin_views.py


# ---------------- COMPANIES ----------------

# switch_company migrated to blueprints/admin_views.py

# clear_company migrated to blueprints/admin_views.py

# set_company_pin migrated to blueprints/admin_views.py

# view_companies migrated to blueprints/admin_views.py


# add_company migrated to blueprints/admin_views.py


# edit_company migrated to blueprints/admin_views.py


# delete_company migrated to blueprints/admin_views.py


# ---------------- ANNOUNCEMENTS ----------------
# announcements_admin migrated to blueprints/admin_views.py

# ---------------- INDIAN PUBLIC HOLIDAYS ----------------
# get_indian_holidays moved to utils/leave_utils.py

# ---------------- VIEW HOLIDAYS ----------------
# view_holidays migrated to blueprints/leave.py

# add_holiday migrated to blueprints/leave.py

# delete_employee migrated to blueprints/employees.py


# edit_employee_page migrated to blueprints/employees.py


# employee_profile migrated to blueprints/employees.py


# edit_employee migrated to blueprints/employees.py


# api_employee_info migrated to blueprints/employees.py


# view_employees migrated to blueprints/employees.py


# ---------------- EMPLOYEE DETAIL PAGE ----------------
# employee_detail migrated to blueprints/employees.py


# ---------------- ADD EMPLOYEE (from employees page) ----------------
# add_employee_page migrated to blueprints/employees.py


# ---------------- UPDATE EMPLOYEE PHOTO ----------------
# update_employee_photo migrated to blueprints/employees.py


# ---------------- REGENERATE QR ----------------
# regenerate_qr migrated to blueprints/employees.py


# ---------------- LEAVE TYPES ADMIN ----------------


# change_admin_password migrated to blueprints/auth.py


# admin_set_recovery_email migrated to blueprints/auth.py


# admin_forgot_password migrated to blueprints/auth.py


# admin_reset_password migrated to blueprints/auth.py


# employee_forgot_password migrated to blueprints/auth.py


# employee_reset_password migrated to blueprints/auth.py


# serve_dataset migrated to blueprints/employees.py


# my_photo migrated to blueprints/employees.py


# ---------------- SHIFTS (redirect to settings) ----------------
# shifts migrated to blueprints/attendance.py

# add_shift migrated to blueprints/attendance.py

# delete_shift_form migrated to blueprints/attendance.py

# delete_shift migrated to blueprints/attendance.py

# edit_shift migrated to blueprints/attendance.py

# bulk_assign_shift migrated to blueprints/attendance.py

# update_default_shift migrated to blueprints/attendance.py

# assign_shift migrated to blueprints/attendance.py


# ──────────────────────── SHIFT SWAP REQUESTS ────────────────────────


# import_indian_holidays migrated to blueprints/leave.py

# delete_holiday migrated to blueprints/leave.py

# ---------------- AUTO GENERATE EMPLOYEE ID ----------------
# generate_emp_id migrated to blueprints/employees.py


# ---------------- BREAK CONFIG ----------------
# api_breaks migrated to blueprints/attendance.py

# view_break_config migrated to blueprints/attendance.py

# add_break migrated to blueprints/attendance.py

# update_break migrated to blueprints/attendance.py

# delete_break migrated to blueprints/attendance.py

# ---------------- VIEW SALARY CONFIG ----------------
# view_salary migrated to blueprints/payroll.py
# update_salary migrated to blueprints/payroll.py
# monthly_report migrated to blueprints/attendance.py

# ---------------- EMPLOYEE ATTENDANCE DETAIL ----------------
# employee_attendance_detail migrated to blueprints/attendance.py

# ---------------- MANUAL ATTENDANCE CORRECTION ----------------
# correct_attendance migrated to blueprints/attendance.py


# ---------------- BULK MARK ATTENDANCE ----------------
# bulk_mark_attendance migrated to blueprints/attendance.py


# ---------------- MONTHLY REPORT EXCEL EXPORT ----------------
# monthly_report_export migrated to blueprints/attendance.py

# ---------------- ABSENTEE REPORT EMAIL ----------------
# send_absentee_report migrated to blueprints/attendance.py

# ---------------- SALARY REPORT ----------------
# salary_report migrated to blueprints/payroll.py
# salary_report_export migrated to blueprints/payroll.py
# email_config migrated to blueprints/payroll.py
# send_salary_email migrated to blueprints/payroll.py
# send_all_salary_emails migrated to blueprints/payroll.py
# lock_payroll migrated to blueprints/payroll.py
# unlock_payroll migrated to blueprints/payroll.py
# test_email migrated to blueprints/admin_views.py

# ---------------- LOCATION ----------------
# location migrated to blueprints/attendance.py

# ---------------- DISTANCE CHECK ----------------
# is_within_range moved to utils/attendance_utils.py

# ---------------- ATTENDANCE (LOGIN + LOGOUT) ----------------
# attendance migrated to blueprints/attendance.py

# ================================================================
#  EMPLOYEE PORTAL
# ================================================================

# employee_login migrated to blueprints/auth.py


# employee_logout migrated to blueprints/auth.py


# change_password migrated to blueprints/auth.py


# force_change_pin migrated to blueprints/auth.py


# update_my_profile migrated to blueprints/employee_portal.py


# update_my_bank_details migrated to blueprints/employee_portal.py


# add_experience migrated to blueprints/employee_portal.py


# delete_experience migrated to blueprints/employee_portal.py


# add_education_entry migrated to blueprints/employee_portal.py


# delete_education_entry migrated to blueprints/employee_portal.py


# update_my_photo migrated to blueprints/employee_portal.py


# my_qr migrated to blueprints/employee_portal.py


# my_id_card migrated to blueprints/employee_portal.py


# _build_id_card_buf migrated to blueprints/employees.py


# admin_id_card migrated to blueprints/employees.py


# admin_view_id_card migrated to blueprints/employees.py


# employee_portal migrated to blueprints/employee_portal.py


# my_payslip_summary migrated to blueprints/payroll.py
# my_attendance_pdf migrated to blueprints/payroll.py
# request_leave migrated to blueprints/leave.py


# leave_balance migrated to blueprints/leave.py


# set_leave_balance migrated to blueprints/leave.py


# ─────────────────────────── PERFORMANCE MANAGEMENT ───────────────────────────
# RATING_LABELS moved to blueprints/performance.py

# performance migrated to blueprints/performance.py


# performance_review migrated to blueprints/performance.py


# performance_save_review migrated to blueprints/performance.py


# performance_add_kpi migrated to blueprints/performance.py


# performance_rate_kpi migrated to blueprints/performance.py


# performance_delete_kpi migrated to blueprints/performance.py


# my_performance migrated to blueprints/performance.py


# performance_employee_comment migrated to blueprints/performance.py


# performance_export migrated to blueprints/performance.py


# performance_import migrated to blueprints/performance.py


# apply_hike migrated to blueprints/payroll.py
# award_performance_bonus migrated to blueprints/payroll.py
# save_hike_config migrated to blueprints/payroll.py
# leave_requests_redirect migrated to blueprints/leave.py
# view_holidays_redirect removed -- was a dead, unreferenced duplicate of
# /view_holidays that had been silently shadowed by the real view_holidays()
# (now blueprints/leave.py) since before this migration; moving the real
# route into a blueprint flipped Werkzeug's rule tie-break order and made
# this stub reachable, so it's deleted rather than preserved.
# leave_holidays migrated to blueprints/leave.py


# leave_action migrated to blueprints/leave.py


# leave_calendar migrated to blueprints/leave.py


# request_resignation migrated to blueprints/leave.py


# resignation_action migrated to blueprints/leave.py


# bulk_leave_action migrated to blueprints/leave.py


# ================================================================
#  TICKETS  (web)
# ================================================================

# raise_ticket migrated to blueprints/tickets.py


# tickets_view migrated to blueprints/tickets.py


# ticket_action migrated to blueprints/tickets.py


# ================================================================
#  REST API  (used by the Flutter mobile app)
# ================================================================

# api_login migrated to blueprints/core.py


# api_logout migrated to blueprints/core.py


# api_dashboard migrated to blueprints/core.py


# api_employees migrated to blueprints/employees.py


# api_register_employee migrated to blueprints/employees.py


# api_employee_detail migrated to blueprints/employees.py


# api_edit_employee migrated to blueprints/employees.py


# api_delete_employee migrated to blueprints/employees.py


# api_holidays migrated to blueprints/leave.py


# api_add_holiday migrated to blueprints/core.py


# api_salary_config_get migrated to blueprints/payroll.py
# api_salary_config_post migrated to blueprints/payroll.py
# api_monthly_report migrated to blueprints/payroll.py
# api_salary_report migrated to blueprints/payroll.py
# api_get_email_config migrated to blueprints/payroll.py
# api_save_email_config migrated to blueprints/payroll.py
# api_send_salary_email migrated to blueprints/payroll.py
# api_checkin migrated to blueprints/attendance.py


# ---------------- API: LEAVE REQUESTS ----------------

# api_leave_requests migrated to blueprints/leave.py


# api_leave_action migrated to blueprints/leave.py


# ---------------- API: RESIGNATION REQUESTS ----------------

# api_resignation_requests migrated to blueprints/leave.py


# api_resignation_action migrated to blueprints/leave.py


# api_employee_login migrated to blueprints/core.py


# api_employee_logout migrated to blueprints/core.py


# api_employee_change_password migrated to blueprints/employee_portal.py


# _fmt_t moved to blueprints/employee_portal.py

# api_employee_portal migrated to blueprints/employee_portal.py

# api_employee_checkin migrated to blueprints/employee_portal.py


# api_employee_sync_punches migrated to blueprints/employee_portal.py


# api_employee_auth_config migrated to blueprints/employee_portal.py


# WebAuthn/mobile-biometric helper functions moved to utils/webauthn_utils.py
# _enroll_fingerprint_from_form moved to utils/webauthn_utils.py -- its only
# two callers (admin_action, add_employee_page) migrated to blueprints/employees.py.
# webauthn_status migrated to blueprints/auth.py


# webauthn_registration_options migrated to blueprints/auth.py


# webauthn_authentication_options migrated to blueprints/auth.py


# webauthn_verify_challenge migrated to blueprints/auth.py


# webauthn_register migrated to blueprints/auth.py


# webauthn_unenroll migrated to blueprints/auth.py
# webauthn_register_kiosk migrated to blueprints/auth.py (was missing from
# the original auth.py migration manifest -- found via a pyflakes undefined-
# name sweep after the fact; it referenced _webauthn_available and
# _wa_verify_and_store_registration, which app.py no longer imports).
# admin_reset_employee_fingerprint migrated to blueprints/auth.py
# get_employee_webauthn_credential migrated to blueprints/auth.py


# api_mobile_biometric_nonce migrated to blueprints/auth.py


# api_mobile_biometric_attest migrated to blueprints/auth.py


# api_employee_qr_face_checkin migrated to blueprints/employee_portal.py


# api_employee_leave_request migrated to blueprints/leave.py


# api_employee_resign migrated to blueprints/leave.py


# ---------------- API: TICKETS (employee) ----------------

# api_employee_tickets migrated to blueprints/tickets.py


# api_employee_raise_ticket migrated to blueprints/tickets.py


# api_employee_salary migrated to blueprints/employee_portal.py


# ---------------- API: EMPLOYEE -- ATTENDANCE HISTORY ----------------

# api_employee_attendance migrated to blueprints/employee_portal.py


# ---------------- API: EMPLOYEE -- LEAVE HISTORY + BALANCE ----------------

# api_employee_leaves migrated to blueprints/leave.py


# ---------------- API: EMPLOYEE -- CANCEL LEAVE ----------------

# api_employee_cancel_leave migrated to blueprints/leave.py


# ---------------- WEB: EMPLOYEE -- CANCEL LEAVE ----------------

# cancel_leave_web migrated to blueprints/leave.py


# ---------------- API: EMPLOYEE -- REQUEST OVERTIME ----------------

# api_employee_request_overtime migrated to blueprints/leave.py


# api_employee_my_overtime migrated to blueprints/leave.py


# ---------------- API: ADMIN -- DOCUMENT EXPIRY ALERTS ----------------

# api_expiring_documents migrated to blueprints/admin_views.py


# ---------------- API: EMPLOYEE -- HOLIDAYS ----------------

# api_employee_holidays migrated to blueprints/leave.py


# ---------------- API: EMPLOYEE -- PROFILE ----------------

# api_employee_profile migrated to blueprints/employee_portal.py


# api_employee_upload_photo migrated to blueprints/employee_portal.py


# ---------------- API: TICKETS (admin) ----------------

# api_tickets migrated to blueprints/tickets.py


# api_ticket_action migrated to blueprints/tickets.py


# ---------------- PAY SLIPS ----------------
# view_payslip migrated to blueprints/payroll.py
# download_payslip migrated to blueprints/payroll.py
# admin_payslips migrated to blueprints/payroll.py
# payroll_settings migrated to blueprints/payroll.py
# api_shifts_get migrated to blueprints/attendance.py


# api_shifts_create migrated to blueprints/attendance.py


# api_shifts_delete migrated to blueprints/attendance.py


# api_shifts_assign migrated to blueprints/attendance.py


# ================================================================
#  FEATURE 1: ANALYTICS
# ================================================================

# analytics migrated to blueprints/admin_views.py


# ================================================================
#  FEATURE 2: DOCUMENT MANAGEMENT
# ================================================================

# _DOC_ALLOWED_EXT moved to blueprints/documents.py

# _doc_admin_ctx migrated to blueprints/documents.py


# documents migrated to blueprints/documents.py


# upload_document migrated to blueprints/documents.py


# delete_document migrated to blueprints/documents.py


# download_document migrated to blueprints/documents.py


# upload_my_document migrated to blueprints/documents.py


# delete_my_document migrated to blueprints/documents.py


# ================================================================
#  FEATURE 3: OVERTIME TRACKING
# ================================================================

# overtime migrated to blueprints/leave.py


# overtime_action migrated to blueprints/leave.py


# ─────────────────────────── COMP-OFF MANAGEMENT ───────────────────────────

# compoff migrated to blueprints/leave.py

# compoff_old migrated to blueprints/leave.py


# compoff_settings migrated to blueprints/leave.py


# my_compoff migrated to blueprints/leave.py


# Notification routes migrated to blueprints/notifications.py


# ── Tenant Provisioning ──────────────────────────────────────────────────────

# _SUBDOMAIN_RE moved to blueprints/org.py
# Signup is open by default (Turnstile-protected, not gated behind a
# shared secret) -- see blueprints/org.py and utils/plan_limits.py.

# create_org_page migrated to blueprints/org.py


# create_org migrated to blueprints/org.py


# ─────────────────────────────────────────
#  ONBOARDING WORKFLOW
# ─────────────────────────────────────────

# onboarding migrated to blueprints/onboarding.py

# onboarding_template_save migrated to blueprints/onboarding.py

# bulk_assign_onboarding migrated to blueprints/onboarding.py


# export_onboarding_csv migrated to blueprints/onboarding.py


# onboarding_template_duplicate migrated to blueprints/onboarding.py


# onboarding_template_delete migrated to blueprints/onboarding.py

# onboarding_task_save migrated to blueprints/onboarding.py

# onboarding_task_delete migrated to blueprints/onboarding.py

# onboarding_template_detail migrated to blueprints/onboarding.py

# onboarding_assign migrated to blueprints/onboarding.py

# onboarding_detail migrated to blueprints/onboarding.py

# onboarding_admin_task_update migrated to blueprints/onboarding.py

# onboarding_close migrated to blueprints/onboarding.py

# ── OFFER LETTER ──────────────────────────────────────────────────────────────
# offer_letter migrated to blueprints/onboarding.py

# offer_letter_save migrated to blueprints/onboarding.py

# offer_letter_view migrated to blueprints/onboarding.py

# _generate_offer_letter_pdf migrated to blueprints/onboarding.py


# offer_letter_send migrated to blueprints/onboarding.py


# offer_letter_pdf migrated to blueprints/onboarding.py


# offer_letter_respond migrated to blueprints/onboarding.py

# Employee portal onboarding
# my_onboarding migrated to blueprints/onboarding.py

# my_onboarding_task_done migrated to blueprints/onboarding.py


# ---------------- ADMIN TOOLS (Org Chart + Audit Logs combined) ----------------
# org_chart_page migrated to blueprints/admin_views.py

# audit_logs_redirect migrated to blueprints/admin_views.py

# admin_tools migrated to blueprints/admin_views.py


# old standalone routes kept for API


# api_org_chart_data migrated to blueprints/admin_views.py


# ── /api/v1/ aliases ──────────────────────────────────────────────────────────
# Register every /api/<path> route also under /api/v1/<path>.  Existing mobile
# clients keep using /api/ with no changes; new integrations can start on v1.
# The view functions (and their decorators: @limiter, @api_required, etc.) are
# shared, so rate-limits and auth are identical on both prefixes.
def _register_api_v1_aliases():
    _seen = set()
    for _rule in list(app.url_map.iter_rules()):
        if not _rule.rule.startswith("/api/") or _rule.rule.startswith("/api/v"):
            continue
        _v1_rule = "/api/v1" + _rule.rule[4:]
        _vf = app.view_functions.get(_rule.endpoint)
        if _vf is None:
            continue
        _ep_v1 = "v1_" + _rule.endpoint
        if _ep_v1 in _seen:
            continue
        _seen.add(_ep_v1)
        app.add_url_rule(
            _v1_rule,
            endpoint=_ep_v1,
            view_func=_vf,
            methods=_rule.methods,
        )


# ── Self-register blueprints when app.py is the entrypoint ────────────────────
# wsgi.py and tests/conftest.py both register every blueprint on the shared
# `app` instance BEFORE importing this module (documented in conftest.py) --
# in that case core.home is already present and this is a no-op. Only a bare
# `python app.py` reaches this branch, so it's the one path where app.py must
# register the blueprints itself before _register_api_v1_aliases() runs,
# otherwise every route (including "/") would 404 and v1 aliases would be
# built from an empty url_map.
if "core.home" not in app.view_functions:
    from blueprints.health import health_bp
    from blueprints.notifications import notifications_bp
    from blueprints.payroll import payroll_bp
    from blueprints.leave import leave_bp
    from blueprints.admin_views import admin_views_bp
    from blueprints.auth import auth_bp
    from blueprints.employees import employees_bp
    from blueprints.attendance import attendance_bp
    from blueprints.tickets import tickets_bp
    from blueprints.performance import performance_bp
    from blueprints.documents import documents_bp
    from blueprints.org import org_bp
    from blueprints.oauth_signup import oauth_signup_bp
    from blueprints.onboarding import onboarding_bp
    from blueprints.employee_portal import employee_portal_bp
    from blueprints.core import core_bp
    from blueprints.ai_hrms import ai_hrms_bp
    from blueprints.email_blast import email_blast_bp
    from blueprints.daily_report import daily_report_bp
    from blueprints.billing import billing_bp
    from blueprints.webhooks import webhooks_bp
    from blueprints.seats import seats_bp
    from blueprints.auto_debit import auto_debit_bp
    from blueprints.billing_dunning import billing_dunning_bp
    from blueprints.platform_admin import platform_admin_bp
    from blueprints.honeypot_routes import honeypot_bp
    from blueprints.disbursement import disbursement_bp
    from blueprints.hr_dashboard import hr_dashboard_bp
    from blueprints.policies import policies_bp
    from blueprints.company_profile import company_profile_bp
    from blueprints.seo import seo_bp
    from blueprints.privacy import privacy_bp
    for _bp in (health_bp, notifications_bp, payroll_bp, leave_bp, admin_views_bp,
                auth_bp, employees_bp, attendance_bp, tickets_bp, performance_bp,
                documents_bp, org_bp, onboarding_bp, employee_portal_bp, core_bp,
                ai_hrms_bp, email_blast_bp, daily_report_bp, billing_bp, webhooks_bp, seats_bp, auto_debit_bp,
                billing_dunning_bp, platform_admin_bp, honeypot_bp, disbursement_bp, hr_dashboard_bp, policies_bp,
                company_profile_bp, seo_bp, oauth_signup_bp, privacy_bp):
        app.register_blueprint(_bp)


# ── Billing context (flat per-employee rate) ─────────────────────────────────
@app.context_processor
def inject_billing_context():
    try:
        from flask import g as _g
        from utils.plan_limits import get_tenant_employee_count, calculate_price, format_price_inr, get_per_employee_paise
        employee_count = get_tenant_employee_count(_g.tenant_db)
        monthly_bill_paise = calculate_price(employee_count)
        return dict(
            employee_count=employee_count,
            per_employee_paise=get_per_employee_paise(),
            monthly_bill_display=format_price_inr(monthly_bill_paise),
        )
    except Exception:
        return dict(employee_count=0, per_employee_paise=9900, monthly_bill_display="₹0")


_register_api_v1_aliases()

# ---------------- RUN ----------------
if __name__ == "__main__":
    init_master_db()
    init_db()
    _run_migrations_for_all_tenants()
    cfg.load_default_shift()
    cfg.load_salary_rules()
    # wsgi.py wraps app.wsgi_app with this at import time -- running app.py
    # directly (`python app.py`) never goes through wsgi.py, so without this
    # every path-based tenant link (/<company-slug>/...) 404s: nothing ever
    # strips the slug into SCRIPT_NAME for Flask's router to see the bare
    # route underneath. Guarded so re-running this block (shouldn't happen,
    # but __main__ only executes once anyway) can't double-wrap.
    from utils.tenant_routing import TenantPrefixMiddleware
    if not isinstance(app.wsgi_app, TenantPrefixMiddleware):
        app.wsgi_app = TenantPrefixMiddleware(app.wsgi_app)
    # Same story as TenantPrefixMiddleware just above: wsgi.py wraps this
    # at import time, so running app.py directly never gets it. Without
    # it, every request tunneled through something like ngrok (testing a
    # Razorpay webhook against a local server, say) looks to Flask like it
    # came from 127.0.0.1 -- ngrok's local agent is the actual TCP peer --
    # so the WAF's rate-limit-breach auto-ban ends up banning localhost
    # itself, locking out the developer along with whatever tripped it.
    # x_for=1 trusts exactly one forwarded-for hop, matching wsgi.py's own
    # setting; harmless for plain direct localhost access, which never
    # sends the header at all.
    from werkzeug.middleware.proxy_fix import ProxyFix
    if not isinstance(app.wsgi_app, ProxyFix):
        app.wsgi_app = apply_proxy_fix(app.wsgi_app)
    # Only started here -- when app.py is run directly (`python app.py`,
    # local dev). wsgi.py (the real production entrypoint) already starts
    # this exact worker itself before importing app.py; starting it again
    # unconditionally at module level (the old behavior) meant production
    # ran two of these per process, racing on the same email_queue table
    # with no row locking -- a live duplicate-delivery bug, not a
    # hypothetical one.
    threading.Thread(target=_email_queue_worker, daemon=True, name="email-queue-worker").start()
    # Same "only started here" reasoning as email-queue-worker just above.
    from utils.document_verification import _document_verification_worker
    threading.Thread(target=_document_verification_worker, daemon=True, name="document-verification-worker").start()
    import os as _os
    from werkzeug.serving import WSGIRequestHandler

    class _QuietRequestHandler(WSGIRequestHandler):
        """Werkzeug's dev server writes its own 'Server: Werkzeug/x.x Python/x.x'
        header straight onto the socket (via BaseHTTPRequestHandler.send_response)
        regardless of what _security_headers already set on the response object --
        so the real fix has to happen here, not by adding another header.
        Overriding version_string() replaces that raw value instead of leaking
        the exact Werkzeug/Python versions (only relevant to `python app.py` dev/
        kiosk runs; the gunicorn path in wsgi.py never uses this handler)."""

        def version_string(self):
            return "AttendanceApp"

    _cert = _os.environ.get("SSL_CERT_PATH") or _os.path.join(_os.path.dirname(__file__), "cert.pem")
    _key = _os.environ.get("SSL_KEY_PATH") or _os.path.join(_os.path.dirname(__file__), "key.pem")
    # Was hardcoded to 5000 in both branches below -- gunicorn.conf.py (the
    # real production path, via wsgi.py) already reads PORT from the
    # environment, but this `python app.py` dev-server path never did,
    # making a local port collision with anything else already bound to
    # 5000 unfixable without editing source. Same env var, same default.
    _port = int(_os.environ.get("PORT", "5000"))
    # threaded=True: /api/session/risk-stream (blueprints/core.py) holds an
    # SSE connection open for ~20s, and Werkzeug's dev server is single-
    # threaded by default -- without this, one open stream blocks every
    # other request until it closes.
    if _os.path.exists(_cert) and _os.path.exists(_key):
        # app.run(..., ssl_context=(...)) wraps the *listening* socket
        # (Werkzeug's serving.py), so every TLS handshake runs synchronously
        # inside the single accept() loop, before threaded=True's per-
        # connection threading ever kicks in. A client that completes the
        # TCP connect but stalls or never sends its ClientHello (a browser's
        # abandoned speculative/prefetch connection is enough) leaves that
        # accept() call blocked forever -- wedging the loop and freezing the
        # *entire* server for every subsequent request, not just that one
        # connection: process stays alive and CPU-idle, but even /healthz
        # times out. run_dev.py hit and fixed this exact freeze; ported here
        # so `python app.py` doesn't carry the same live bug -- the TLS wrap
        # is deferred into finish_request(), which always runs inside the
        # freshly spawned per-connection worker thread, so a stalled
        # handshake only ever ties up its own disposable thread.
        import ssl as _ssl
        from werkzeug.serving import ThreadedWSGIServer, load_ssl_context

        print(f"🔒  SSL cert found -- starting on https://0.0.0.0:{_port}")
        _tls_ctx = load_ssl_context(_cert, _key)

        class _DeferredHandshakeServer(ThreadedWSGIServer):
            def finish_request(self, request, client_address):
                request.settimeout(30)
                try:
                    request = _tls_ctx.wrap_socket(request, server_side=True)
                except (_ssl.SSLError, OSError):
                    request.close()
                    return
                super().finish_request(request, client_address)

        _srv = _DeferredHandshakeServer("0.0.0.0", _port, app, handler=_QuietRequestHandler,
                                        ssl_context=None)
        # Socket itself stays unwrapped (see finish_request above); this
        # attribute only drives wsgi.url_scheme detection and SSL-error-log
        # suppression elsewhere in werkzeug/serving.py, both of which still
        # need to know this is actually an HTTPS server.
        _srv.ssl_context = _tls_ctx
        _srv.log_startup()
        try:
            _srv.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        print(f"⚠   No cert.pem / key.pem -- starting on http://0.0.0.0:{_port}")
        print("    Fingerprint / WebAuthn requires HTTPS. Run: python generate_cert.py")
        app.run(host='0.0.0.0', port=_port, debug=False, use_reloader=False, threaded=True,  # nosec B104
                request_handler=_QuietRequestHandler)
