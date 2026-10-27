# -*- coding: utf-8 -*-
"""Platform admin blueprint -- cross-tenant SaaS operator panel.

/super_admin/* is deliberately in app.py's _resolve_tenant() skip list, so
g.tenant_db is never set here: every DB access below must go through
get_master_db() (for platform_admins/tenants) or get_tenant_db(schema)
(for one specific tenant's employee count) -- never the bare
get_db_connection(), which would silently operate on the "public" schema.

This identity is deliberately NOT admin_logged_in/admin_role (the
tenant-admin session shape) -- those flags are what app.py's tenant-
oriented before_request hooks (_enforce_admin_mfa_enrollment,
_enforce_idle_timeout) key off of, and this identity has no matching
admin_users row in any schema for them to look up. Using a separate
session key means those hooks simply no-op here, and this blueprint
enforces its own (lighter) idle timeout below.
"""
import os
import re
import time
import secrets
import functools
from flask import Blueprint, request, session, redirect, render_template, flash, jsonify, current_app

from database import get_master_db, get_db_connection, get_tenant_db, get_owner_master_db
from extensions import app_log, log_security_event, limiter
from utils.auth import check_password_hash, dummy_password_check, _mask_identifier, _hash_token
from utils.login_otp import issue_challenge, verify_challenge, discard_challenge
from utils.totp import send_mfa_login_email
from utils.plan_limits import (
    get_per_employee_paise, invalidate_rate_paise_cache, calculate_price, format_price_inr,
    get_tenant_employee_count, get_base_fee_paise, get_minimum_monthly_paise, invalidate_extra_cost_cache,
)
from utils.helpers import coerce_datetime, _safe_app_url, _create_notification
from utils.analytics import get_traffic_stats
from utils.csv_export import csv_response as _csv_response
from utils.payment_history import get_payment_history
from utils.ticket_constants import TICKET_STATUSES as _TICKET_STATUSES, TICKET_PRIORITIES as _TICKET_PRIORITIES
from utils import chat_utils

platform_admin_bp = Blueprint("platform_admin", __name__)

_MFA_OTP_TTL_SEC = 300  # 5 minutes, same window every other emailed-OTP login uses
_IDLE_TIMEOUT_SEC = 30 * 60  # 30 minutes of inactivity
# Short-TTL cache for the dashboard's per-tenant employee counts -- each
# one opens a real connection to that tenant's schema (database.py's pool
# caps at maxconn=20), so a bare page refresh shouldn't re-open N of them
# every time. Same 60s-cache idea as utils/helpers.py's _co_expired pattern.
_COUNT_CACHE_TTL_SEC = 60
_employee_count_cache = {}
_TENANTS_PER_PAGE = 20
_AUDIT_LOG_PER_PAGE = 50
# Same prefixes _recent_platform_activity() scopes its dashboard preview
# to -- every event_type a platform admin action or org-signup security
# check can emit. security_events is one global table (see that
# function's docstring), so this filter is what keeps the audit log
# platform-admin-relevant instead of showing every tenant's routine
# admin/employee logins too.
_AUDIT_LOG_PREFIXES = ("platform_admin.", "auth.platform_admin_", "billing.", "org.", "secops.")


def _paginate(items, page, per_page):
    """Slices an already-fetched list for display -- used where the
    filtering happens in Python (see platform_admin_dashboard()) rather
    than in SQL. Returns (page_items, total_pages), clamping page into
    range so an out-of-bounds ?page= value degrades to the nearest valid
    page instead of a blank result."""
    total = len(items)
    total_pages = max(1, -(-total // per_page))  # ceil
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return items[start:start + per_page], page, total_pages


def _complete_platform_admin_login(username):
    """Build the real session once credentials (and MFA, when required)
    have checked out -- shared by the MFA-verify completion below and the
    direct-login path taken when MANDATORY_PLATFORM_ADMIN_MFA is off."""
    session.clear()
    session["platform_admin_logged_in"] = True
    session["platform_admin_username"] = username
    session["platform_admin_last_activity"] = time.time()
    # Picked up by app.py's _enforce_session_lifetime before_request hook
    # (not gated on admin_logged_in -- it checks this key on every
    # request), giving this identity the same 8-hour absolute session cap
    # as every other login flow, for free.
    session["_session_created"] = time.time()
    session.permanent = True
    log_security_event(
        "auth.platform_admin_login_success",
        f"Platform admin session established for '{username}'",
        level="INFO", identifier=username,
    )
    return redirect("/super_admin")


def _discard_platform_challenge(challenge_id):
    if not challenge_id:
        return
    try:
        conn = get_master_db()
        cur = conn.cursor()
        try:
            discard_challenge(cur, challenge_id)
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        app_log.warning("Could not discard platform admin login challenge: %s", type(exc).__name__)


def _platform_admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("platform_admin_logged_in"):
            return redirect("/super_admin/login")
        last_activity = session.get("platform_admin_last_activity", 0)
        if (time.time() - last_activity) > _IDLE_TIMEOUT_SEC:
            session.clear()
            flash("Your session expired due to inactivity. Please log in again.", "error")
            return redirect("/super_admin/login")
        session["platform_admin_last_activity"] = time.time()
        return view(*args, **kwargs)
    wrapped._hrz_auth_guard = "platform_admin"  # read by tests/test_route_auth_guards.py
    return wrapped


@platform_admin_bp.route("/super_admin/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes")
def platform_admin_login():
    """Single-page credentials + emailed-OTP MFA login -- one URL, one
    template. Which of the two steps renders is driven entirely by session
    state (platform_mfa_pending), not by a second page: submitting
    username/password here starts the OTP step in place (re-renders this
    same route); submitting otp_code here while that session state is set
    completes it via _complete_platform_admin_login."""
    if session.get("platform_admin_logged_in"):
        return redirect("/super_admin")

    if request.args.get("restart"):
        _discard_platform_challenge(session.get("platform_mfa_challenge_id"))
        session.pop("platform_mfa_pending", None)
        session.pop("platform_mfa_user", None)
        session.pop("platform_mfa_challenge_id", None)
        session.pop("platform_mfa_issued_at", None)
        return redirect("/super_admin/login")

    mfa_pending = bool(session.get("platform_mfa_pending"))
    if mfa_pending:
        issued_at = session.get("platform_mfa_issued_at") or 0
        if (time.time() - issued_at) > _MFA_OTP_TTL_SEC:
            _discard_platform_challenge(session.get("platform_mfa_challenge_id"))
            session.clear()
            return render_template("super_admin_login.html", error="Your code expired. Please log in again.")

    if request.method == "POST" and mfa_pending and "otp_code" in request.form:
        display_user = session.get("platform_mfa_user")
        submitted = (request.form.get("otp_code") or "").strip()
        # The code is checked against the att_master challenge row; the
        # session only ever held the opaque challenge id.
        conn = get_master_db()
        cur = conn.cursor()
        try:
            verified = verify_challenge(cur, session.get("platform_mfa_challenge_id", ""), submitted,
                                        ("platform_admin",))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        if verified:
            return _complete_platform_admin_login(verified[1])

        log_security_event(
            "auth.platform_admin_mfa_failure", "Invalid platform admin login MFA code",
            level="WARNING", identifier=_mask_identifier(display_user),
        )
        return render_template("super_admin_login.html", mfa_step=True, error="Invalid verification code.")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        row = None
        if username:
            conn = get_master_db()
            cur = conn.cursor(buffered=True)
            cur.execute("SELECT password, email FROM platform_admins WHERE username=%s", (username,))
            row = cur.fetchone()
            cur.close()
            conn.close()

        if row and check_password_hash(row[0], password):
            # Local-dev escape hatch, same convention as blueprints/auth.py's
            # MANDATORY_LOGIN_MFA -- defaults True (secure by default), only
            # off when explicitly set in .env. Password is still checked
            # above either way; this only skips the emailed-OTP step.
            if not current_app.config["MANDATORY_PLATFORM_ADMIN_MFA"]:
                return _complete_platform_admin_login(username)

            email = row[1]
            conn = get_master_db()
            cur = conn.cursor()
            try:
                challenge_id, otp_code = issue_challenge(cur, "platform_admin", username, _MFA_OTP_TTL_SEC)
                conn.commit()
            finally:
                cur.close()
                conn.close()
            try:
                send_mfa_login_email(email, username, "Platform Administrator", "", otp_code)
            except Exception as exc:
                app_log.warning("Platform admin MFA email send error: %s", type(exc).__name__)

            session.clear()
            session["platform_mfa_pending"] = True
            session["platform_mfa_user"] = username  # display only
            session["platform_mfa_challenge_id"] = challenge_id
            session["platform_mfa_issued_at"] = time.time()
            return render_template("super_admin_login.html", mfa_step=True)

        if not row:
            dummy_password_check(password)
        log_security_event(
            "auth.platform_admin_login_failed",
            "Failed platform admin login attempt",
            level="WARNING", identifier=_mask_identifier(username),
        )
        return render_template("super_admin_login.html", error="Invalid credentials.")

    return render_template("super_admin_login.html", mfa_step=mfa_pending)


@platform_admin_bp.route("/super_admin/logout", methods=["POST"])
def platform_admin_logout():
    session.clear()
    return redirect("/super_admin/login")


def _tenant_employee_count(schema_name: str) -> int:
    now = time.time()
    cached = _employee_count_cache.get(schema_name)
    if cached and (now - cached[1]) < _COUNT_CACHE_TTL_SEC:
        return cached[0]
    count = get_tenant_employee_count(schema_name)
    _employee_count_cache[schema_name] = (count, now)
    return count


def _recent_platform_activity(limit=20):
    """Best-effort recent-activity feed for the dashboard. security_events
    rows written from within this blueprint land in whatever schema
    get_db_connection() resolves to when g.tenant_db is unset (the
    single-tenant DB_NAME fallback -- see app.py's _resolve_tenant()) since
    /super_admin/* deliberately never sets g.tenant_db; that's an existing
    property of how log_security_event()'s async writer persists events,
    not something specific to this query. Never raises -- an activity feed
    failing to load shouldn't take the whole dashboard down with it."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(buffered=True)
        # secops.% is blueprints/honeypot_routes.py's deception-honeypot trap
        # hits (a scanner bot probing /.env, /wp-admin/, ...) -- cross-tenant
        # perimeter events with a source IP, which is exactly what a platform
        # admin needs to see and had no way to before.
        cur.execute(
            "SELECT event_type, level, message, identifier, ip, created_at FROM security_events "
            "WHERE event_type LIKE 'platform_admin.%%' OR event_type LIKE 'auth.platform_admin_%%' "
            "OR event_type LIKE 'billing.%%' OR event_type LIKE 'secops.%%' "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {"event_type": r[0], "level": r[1], "message": r[2], "identifier": r[3], "ip": r[4], "created_at": r[5]}
            for r in rows
        ]
    except Exception as exc:
        app_log.warning("platform_admin: recent activity feed failed: %s", exc)
        return []


def _self_signup_tenant_ids():
    """Tenant IDs that came through the public self-service flow -- either
    the paid path (blueprints/billing.py's verify_payment, real Razorpay or
    the demo checkout) or the gated free/manual path (a tenant_applications
    row approved directly by platform_admin_approve_application()) -- rather
    than being created directly by a platform admin via
    platform_admin_create_tenant(), which never touches either table. A
    payment_orders or tenant_applications row with tenant_id set is only
    ever written by one of those two self-service paths, so its presence
    is the signal -- no separate 'source' column needed on tenants itself."""
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT DISTINCT tenant_id FROM payment_orders WHERE tenant_id IS NOT NULL")
        ids = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT tenant_id FROM tenant_applications WHERE tenant_id IS NOT NULL")
        ids |= {r[0] for r in cur.fetchall()}
        cur.close()
        conn.close()
        return ids
    except Exception as exc:
        app_log.warning("platform_admin: self-signup lookup failed: %s", exc)
        return set()


def _recent_payments(limit=20, tenant_id=None, tenant_schema=None):
    """Thin wrapper over utils/payment_history.py's get_payment_history() --
    the merge-three-tables-into-one-feed logic used to live here, now shared
    with the Company Admin Profile page's Payment History tab so the two
    can't drift on what "this tenant's payment history" means. Kept as a
    wrapper (rather than repointing every call site) purely to avoid
    touching this file's existing call sites' argument shape."""
    return get_payment_history(tenant_id=tenant_id, tenant_schema=tenant_schema, limit=limit)["payments"]


def _recent_leads(limit=20):
    """'Request info' submissions from the public landing page
    (templates/landing.html's contact form -> POST /api/leads)."""
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute(
            "SELECT id, name, email, phone, company_name, message, status, created_at "
            "FROM leads ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {
                "id": r[0], "name": r[1], "email": r[2], "phone": r[3], "company_name": r[4],
                "message": r[5], "status": r[6], "created_at": r[7],
            }
            for r in rows
        ]
    except Exception as exc:
        app_log.warning("platform_admin: leads lookup failed: %s", exc)
        return []


def _recent_feedback(limit=20):
    """Product feedback / star ratings from the public landing page
    (templates/landing.html's #feedback form -> POST /api/feedback). Purely
    informational (no status workflow like leads above -- there's no
    "convert" action for a feature request), so this is read-only on the
    dashboard."""
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute(
            "SELECT id, feedback_type, rating, email, message, created_at "
            "FROM feedback ORDER BY created_at DESC LIMIT %s",
            (limit,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [
            {"id": r[0], "feedback_type": r[1], "rating": r[2], "email": r[3],
             "message": r[4], "created_at": r[5]}
            for r in rows
        ]
    except Exception as exc:
        app_log.warning("platform_admin: feedback lookup failed: %s", exc)
        return []


# Minimum profit margin the suggested rate targets above break-even --
# purely a "don't suggest exactly zero margin" cushion, not tied to any
# external benchmark.
_SUGGESTED_MARGIN_PCT = 15
# Suggested rates are rounded up to the nearest ₹5 (500 paise) so the
# number reads as a deliberate price point, not a raw division result.
_SUGGESTED_ROUND_TO_PAISE = 500


def _get_platform_costs():
    """Monthly operating costs the platform admin has entered (AWS
    hosting + website maintenance) -- there's no billing API wired up
    that could discover these automatically, so this is admin-entered
    and simply compared against MRR. Fails to zero costs rather than
    raising, so a DB hiccup shows "no known costs" instead of crashing
    the dashboard."""
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT monthly_aws_paise, monthly_maintenance_paise FROM platform_costs WHERE id=1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return {"aws_paise": row[0], "maintenance_paise": row[1]}
    except Exception as exc:
        app_log.warning("platform_admin: cost lookup failed: %s", exc)
    return {"aws_paise": 0, "maintenance_paise": 0}


def _compute_pnl(mrr_paise, active_employee_count, costs):
    """Revenue (MRR) vs. admin-entered costs, plus -- only when running at
    a loss and there's an active employee base to spread the suggestion
    across -- a suggested per-employee rate that would clear costs with
    _SUGGESTED_MARGIN_PCT of headroom, rounded to a clean price point."""
    total_cost_paise = costs["aws_paise"] + costs["maintenance_paise"]
    net_paise = mrr_paise - total_cost_paise
    is_loss = net_paise < 0

    suggested_rate_paise = None
    if is_loss and active_employee_count > 0:
        breakeven_rate = total_cost_paise / active_employee_count
        target_rate = breakeven_rate * (1 + _SUGGESTED_MARGIN_PCT / 100)
        suggested_rate_paise = int(
            -(-target_rate // _SUGGESTED_ROUND_TO_PAISE) * _SUGGESTED_ROUND_TO_PAISE  # ceil to nearest ₹5
        )

    # Bar-fill percentages for the two-sided revenue/cost visualization --
    # whichever side is larger anchors at 100%, the other scales relative
    # to it, so the bar always shows the true ratio regardless of scale.
    larger = max(mrr_paise, total_cost_paise, 1)
    return {
        "revenue_paise": mrr_paise,
        "cost_paise": total_cost_paise,
        "net_paise": net_paise,
        "revenue_display": format_price_inr(mrr_paise),
        "cost_display": format_price_inr(total_cost_paise),
        "net_display": format_price_inr(abs(net_paise)),
        "is_loss": is_loss,
        "revenue_pct": round(mrr_paise / larger * 100),
        "cost_pct": round(total_cost_paise / larger * 100),
        "suggested_rate_paise": suggested_rate_paise,
        "suggested_rate_display": format_price_inr(suggested_rate_paise) if suggested_rate_paise else None,
        "suggested_margin_pct": _SUGGESTED_MARGIN_PCT,
    }


def _load_all_tenants():
    """Fetches every tenant plus its live employee count/monthly bill --
    the full, unfiltered set the dashboard's revenue/headcount KPIs are
    computed over. Search/status filtering and pagination for the on-page
    table happen in Python on top of this (see platform_admin_dashboard()
    and the CSV export below), so those KPIs stay accurate regardless of
    which page or search the admin currently has open."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, company_name, subdomain, db_name, payment_option, status, created_at, "
        "billing_state, grace_period_ends_at "
        "FROM tenants ORDER BY created_at DESC"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    self_signup_ids = _self_signup_tenant_ids()
    tenants = []
    for r in rows:
        (tid, company_name, subdomain, db_name, payment_option, status, created_at,
         billing_state, grace_period_ends_at) = r
        employee_count = _tenant_employee_count(db_name)
        monthly_bill_paise = calculate_price(employee_count)
        tenants.append({
            "id": tid, "company_name": company_name, "subdomain": subdomain,
            "db_name": db_name, "payment_option": payment_option or "online",
            "status": status, "created_at": created_at,
            "employee_count": employee_count,
            "monthly_bill_paise": monthly_bill_paise,
            "monthly_bill_display": format_price_inr(monthly_bill_paise),
            "self_signup": tid in self_signup_ids,
            "billing_state": billing_state or "current",
            "grace_period_ends_at": coerce_datetime(grace_period_ends_at),
        })
    return tenants


def _filter_tenants(tenants, q, status_filter):
    """Company-name/subdomain substring search plus an exact status match,
    applied in Python over the already-fetched full tenant list -- the
    same pattern _load_all_tenants()'s docstring describes."""
    q = (q or "").strip().lower()
    status_filter = (status_filter or "all").strip().lower()
    out = tenants
    if q:
        out = [t for t in out if q in t["company_name"].lower() or q in t["subdomain"].lower()]
    if status_filter in ("active", "suspended"):
        out = [t for t in out if t["status"] == status_filter]
    return out


@platform_admin_bp.route("/super_admin")
@_platform_admin_required
def platform_admin_dashboard():
    all_tenants = _load_all_tenants()

    mrr_paise = sum(t["monthly_bill_paise"] for t in all_tenants if t["status"] == "active")
    active_employee_count = sum(t["employee_count"] for t in all_tenants if t["status"] == "active")

    q = request.args.get("q", "")
    status_filter = request.args.get("status", "all")
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    filtered_tenants = _filter_tenants(all_tenants, q, status_filter)
    tenants, page, total_pages = _paginate(filtered_tenants, page, _TENANTS_PER_PAGE)

    costs = _get_platform_costs()
    pnl = _compute_pnl(mrr_paise, active_employee_count, costs)

    conn2 = get_master_db()
    cur2 = conn2.cursor(buffered=True)
    cur2.execute("SELECT COUNT(*) FROM tenant_applications WHERE status='pending_review'")
    pending_applications_count = cur2.fetchone()[0]
    cur2.execute("SELECT COUNT(*) FROM tenant_duplicate_alerts WHERE acknowledged=0")
    unacknowledged_alerts_count = cur2.fetchone()[0]
    cur2.execute("SELECT COUNT(*) FROM tenant_support_tickets WHERE status IN ('open','in_progress')")
    open_tickets_count = cur2.fetchone()[0]
    cur2.close()
    conn2.close()

    return render_template(
        "super_admin_dashboard.html", tenants=tenants,
        portal_base_url=_safe_app_url(),
        per_employee_paise=get_per_employee_paise(),
        base_fee_paise=get_base_fee_paise(),
        minimum_monthly_paise=get_minimum_monthly_paise(),
        mrr_display=format_price_inr(mrr_paise),
        active_tenant_count=sum(1 for t in all_tenants if t["status"] == "active"),
        total_tenant_count=len(all_tenants),
        filtered_tenant_count=len(filtered_tenants),
        total_employee_count=sum(t["employee_count"] for t in all_tenants),
        self_signup_count=sum(1 for t in all_tenants if t["self_signup"]),
        q=q, status_filter=status_filter, page=page, total_pages=total_pages,
        recent_activity=_recent_platform_activity(),
        recent_payments=_recent_payments(),
        leads=_recent_leads(),
        feedback_items=_recent_feedback(),
        traffic=get_traffic_stats(),
        pnl=pnl,
        costs=costs,
        pending_applications_count=pending_applications_count,
        unacknowledged_alerts_count=unacknowledged_alerts_count,
        open_tickets_count=open_tickets_count,
        # Link to the standalone Grafana log-analysis dashboard (see
        # observability/README.md) -- unset by default so the button below
        # only appears once that stack is actually deployed. Points at the
        # log server's own SG-restricted UI (trusted_admin_cidrs only), so
        # this link is a convenience for someone already on an allowed IP,
        # not a way around that restriction -- clicking it from anywhere
        # else just gets a connection timeout, same as typing the URL in
        # directly would.
        grafana_url=os.environ.get("GRAFANA_URL", "").rstrip("/") or None,
        # Gates the "SOC Dashboard" nav button -- points at the standalone
        # cross-app SOC service (soc-dashboard/, see its README) that
        # aggregates security events from the 4 separate apps into one
        # MongoDB collection. Distinct from this table's own
        # security_events (Postgres, platform-admin-only audit log above).
        soc_dashboard_configured=bool(os.environ.get("SOC_DASHBOARD_URL")),
    )


@platform_admin_bp.route("/super_admin/export/tenants.csv", methods=["GET"])
@_platform_admin_required
def platform_admin_export_tenants_csv():
    """Same q/status filters as the dashboard table, but unpaginated --
    exports every matching row, not just the current page."""
    all_tenants = _load_all_tenants()
    tenants = _filter_tenants(all_tenants, request.args.get("q", ""), request.args.get("status", "all"))
    rows = [
        [t["company_name"], t["subdomain"], t["status"], t["billing_state"], t["payment_option"],
         t["employee_count"], t["monthly_bill_display"], "Self-Signup" if t["self_signup"] else "Platform Admin",
         t["created_at"].strftime("%Y-%m-%d %H:%M") if t["created_at"] else ""]
        for t in tenants
    ]
    log_security_event(
        "platform_admin.tenants_exported", f"Tenant list exported to CSV ({len(rows)} rows)",
        level="INFO", identifier=session.get("platform_admin_username"),
    )
    return _csv_response(
        "tenants.csv",
        ["Company", "Subdomain", "Status", "Billing State", "Payment Option", "Employees",
         "Monthly Bill", "Source", "Created At"],
        rows,
    )


@platform_admin_bp.route("/super_admin/export/payments.csv", methods=["GET"])
@_platform_admin_required
def platform_admin_export_payments_csv():
    payments = _recent_payments(limit=100000)
    rows = [
        [p["kind"], p["company_name"], p["subdomain"], p["employee_count"], p["amount_display"],
         p["status"], "Demo" if p["is_demo"] else "Razorpay", p["order_id"] or "", p["payment_id"] or "",
         p["admin_username"] or "", p["admin_email"] or "",
         p["created_at"].strftime("%Y-%m-%d %H:%M") if p["created_at"] else "",
         p["paid_at"].strftime("%Y-%m-%d %H:%M") if p["paid_at"] else ""]
        for p in payments
    ]
    log_security_event(
        "platform_admin.payments_exported", f"Payment history exported to CSV ({len(rows)} rows)",
        level="INFO", identifier=session.get("platform_admin_username"),
    )
    return _csv_response(
        "payments.csv",
        ["Kind", "Company", "Subdomain", "Employees", "Amount", "Status", "Payment Type",
         "Order ID", "Payment ID", "Admin Username", "Admin Email", "Created At", "Paid At"],
        rows,
    )


@platform_admin_bp.route("/super_admin/costs", methods=["POST"])
@_platform_admin_required
def platform_admin_set_costs():
    """Lets the platform admin update the monthly AWS/maintenance cost
    inputs that _compute_pnl() compares MRR against."""
    def _rupees_to_paise(field):
        raw = request.form.get(field, "0").strip()
        try:
            value = float(raw)
        except ValueError:
            return None
        if value < 0:
            return None
        return round(value * 100)

    aws_paise = _rupees_to_paise("monthly_aws")
    maintenance_paise = _rupees_to_paise("monthly_maintenance")
    if aws_paise is None or maintenance_paise is None:
        flash("Enter valid non-negative amounts for both cost fields.", "error")
        return redirect("/super_admin")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE platform_costs SET monthly_aws_paise=%s, monthly_maintenance_paise=%s, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=1",
        (aws_paise, maintenance_paise)
    )
    conn.commit()
    cur.close()
    conn.close()

    log_security_event(
        "platform_admin.costs_updated",
        f"Operating costs updated (aws={format_price_inr(aws_paise)}, "
        f"maintenance={format_price_inr(maintenance_paise)})",
        level="INFO", identifier=session.get("platform_admin_username"),
    )
    flash("Operating costs updated.", "success")
    return redirect("/super_admin")


@platform_admin_bp.route("/super_admin/rate", methods=["POST"])
@_platform_admin_required
def platform_admin_set_rate():
    """Updates the flat per-employee monthly rate every price calculation
    in the app reads (utils/plan_limits.py's get_per_employee_paise()) --
    takes effect for new signups, seat top-ups, and manual/dunning bills on
    the very next calculation (invalidate_rate_paise_cache() below), no
    redeploy needed.

    Existing recurring auto-debit subscribers are migrated at their own
    next renewal, not immediately: a fresh Razorpay Plan is created here at
    the new rate (Plans are immutable, so the old one keeps its old price
    forever) and every currently-active mandate is flagged
    needs_rate_migration -- blueprints/auto_debit.py's
    _handle_subscription_charged() checks that flag after each successful
    charge, cancels that subscription once its current (old-rate) cycle is
    paid, and emails the tenant to re-authorize a new one. Nobody is
    charged mid-cycle or has a payment silently change amount."""
    from utils.razorpay_utils import create_plan, razorpay_configured
    from blueprints.auto_debit import _PLAN_ITEM_NAME

    raw = request.form.get("per_employee_rupees", "").strip()
    try:
        rupees = float(raw)
    except ValueError:
        flash("Enter a valid rate in rupees.", "error")
        return redirect("/super_admin")
    if rupees <= 0:
        flash("The rate must be greater than zero.", "error")
        return redirect("/super_admin")

    new_paise = round(rupees * 100)
    old_paise = get_per_employee_paise()

    # Both optional -- blank/omitted leaves the existing value untouched
    # rather than resetting it to 0, since this route only ever receives
    # one form submission at a time (the rate field above is always
    # present; these two are additive knobs a platform admin may never
    # touch). See utils/plan_limits.py's calculate_price() for the formula.
    base_fee_paise = get_base_fee_paise()
    base_fee_raw = request.form.get("base_fee_rupees", "").strip()
    if base_fee_raw:
        try:
            base_fee_paise = round(float(base_fee_raw) * 100)
        except ValueError:
            flash("Enter a valid base fee in rupees.", "error")
            return redirect("/super_admin")

    minimum_monthly_paise = get_minimum_monthly_paise()
    minimum_raw = request.form.get("minimum_monthly_rupees", "").strip()
    if minimum_raw:
        try:
            minimum_monthly_paise = round(float(minimum_raw) * 100)
        except ValueError:
            flash("Enter a valid minimum monthly amount in rupees.", "error")
            return redirect("/super_admin")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE platform_costs SET per_employee_paise=%s, base_fee_paise=%s, minimum_monthly_paise=%s, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=1",
        (new_paise, base_fee_paise, minimum_monthly_paise)
    )

    migration_note = ""
    if razorpay_configured():
        new_plan_id, plan_error = create_plan(new_paise, _PLAN_ITEM_NAME)
        if plan_error:
            app_log.error("platform_admin.set_rate: failed to create new Razorpay plan: %s", plan_error)
            migration_note = (
                " Warning: creating the new Razorpay plan failed, so auto-debit migration could not be armed -- "
                "existing subscribers will keep their old rate until this is retried."
            )
        else:
            cur.execute("UPDATE billing_config SET razorpay_plan_id=%s WHERE id=1", (new_plan_id,))
            cur.execute("UPDATE auto_debit_mandates SET needs_rate_migration=TRUE WHERE status='active'")
            migrated_count = cur.rowcount
            if migrated_count:
                migration_note = f" {migrated_count} active auto-debit subscriber(s) will be migrated to the new rate at their next renewal."

    conn.commit()
    cur.close()
    conn.close()
    invalidate_rate_paise_cache()
    invalidate_extra_cost_cache()

    log_security_event(
        "platform_admin.rate_updated",
        f"Per-employee rate changed from {format_price_inr(old_paise)} to {format_price_inr(new_paise)}",
        level="WARNING", identifier=session.get("platform_admin_username"),
    )
    flash(f"Rate updated to {format_price_inr(new_paise)}/employee/month.{migration_note}", "success")
    return redirect("/super_admin")


@platform_admin_bp.route("/super_admin/leads/<int:lead_id>/status", methods=["POST"])
@_platform_admin_required
def platform_admin_set_lead_status(lead_id):
    status = request.form.get("status", "").strip()
    if status not in ("new", "contacted", "converted"):
        flash("Invalid lead status.", "error")
        return redirect("/super_admin")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("UPDATE leads SET status=%s WHERE id=%s", (status, lead_id))
    conn.commit()
    cur.close()
    conn.close()
    flash("Lead updated.", "success")
    return redirect("/super_admin")


# Creating a company here requires the code emailed to its admin address,
# like every self-serve signup. The code lives in att_master's
# login_otp_challenges (HMAC only); the session holds just the challenge id.
_NEW_COMPANY_CODE_KIND = "new_company_email"
_NEW_COMPANY_CODE_SESSION_KEY = "new_company_email_challenge_id"


@platform_admin_bp.route("/super_admin/tenants/send_email_code", methods=["POST"])
@_platform_admin_required
@limiter.limit("10 per 10 minutes")
def platform_admin_send_new_company_code():
    """Emails a 6-digit code to the admin address typed into the New Company
    form; platform_admin_create_tenant() won't create the company without it."""
    from blueprints.org import _EMAIL_RE, _OTP_TTL_MINUTES, send_org_signup_otp_email
    data = request.get_json(silent=True) or {}
    admin_email = str(data.get("admin_email") or "").strip().lower()
    company_name = str(data.get("company_name") or "").strip() or "your company"
    if not _EMAIL_RE.match(admin_email) or len(admin_email) > 150:
        return jsonify({"ok": False, "msg": "Enter a valid admin email first."}), 400

    conn = get_master_db()
    cur = conn.cursor()
    try:
        challenge_id, otp_code = issue_challenge(cur, _NEW_COMPANY_CODE_KIND, admin_email, _OTP_TTL_MINUTES * 60)
        conn.commit()
    finally:
        cur.close()
        conn.close()
    session[_NEW_COMPANY_CODE_SESSION_KEY] = challenge_id
    if not send_org_signup_otp_email(admin_email, company_name, otp_code):
        return jsonify({"ok": False, "msg": "The code email could not be sent -- check the SMTP settings."}), 502
    return jsonify({"ok": True, "msg": f"Code sent to {admin_email}. It expires in {_OTP_TTL_MINUTES} minutes."})


def _check_new_company_email_code(admin_email, submitted):
    """None when `submitted` is the code this session emailed to admin_email
    (used up on success), else the error to show."""
    challenge_id = session.get(_NEW_COMPANY_CODE_SESSION_KEY)
    if not challenge_id:
        return "Click 'Send code' to email a verification code to the admin email, then enter it."
    if not (submitted or "").strip():
        return f"Enter the verification code emailed to {admin_email}."
    conn = get_master_db()
    cur = conn.cursor()
    try:
        verified = verify_challenge(cur, challenge_id, submitted, (_NEW_COMPANY_CODE_KIND,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    if not verified:
        return "Incorrect or expired verification code. Check the email, or send a new code."
    session.pop(_NEW_COMPANY_CODE_SESSION_KEY, None)
    if verified[1] != admin_email.strip().lower():
        return f"That code was sent to a different address. Send a new code to {admin_email}."
    return None


@platform_admin_bp.route("/super_admin/tenants/create", methods=["POST"])
@_platform_admin_required
def platform_admin_create_tenant():
    """Operator-initiated company creation -- the same provisioning core
    the public /create_org signup uses, so a sales-assisted or manually
    onboarded company gets an identical, fully-isolated schema rather than
    a second, softer path into tenant creation."""
    from blueprints.org import _validate_new_tenant_fields, provision_tenant, send_portal_ready_email
    from utils.auth import generate_password_hash
    from utils.helpers import clean_email_domain

    company_name = request.form.get("company_name", "").strip()
    subdomain = request.form.get("subdomain", "").strip().lower()
    admin_username = request.form.get("admin_username", "").strip()
    admin_password = request.form.get("admin_password", "").strip()
    admin_email = request.form.get("admin_email", "").strip()
    payment_option = request.form.get("payment_option", "manual").strip()
    email_domain = clean_email_domain(request.form.get("email_domain", ""))

    error = _validate_new_tenant_fields(company_name, subdomain, admin_username, admin_password, admin_email,
                                        email_domain)
    if error:
        flash(error, "error")
        return redirect("/super_admin")

    code_error = _check_new_company_email_code(admin_email, request.form.get("email_code", ""))
    if code_error:
        flash(code_error, "error")
        return redirect("/super_admin")

    logo_path = None
    logo_file = request.files.get("logo")
    if logo_file and logo_file.filename:
        from utils.helpers import save_uploaded_logo
        logo_path, logo_err = save_uploaded_logo(logo_file, subdomain)
        if logo_err:
            flash(f"Company logo: {logo_err}", "error")
            return redirect("/super_admin")

    ok, error, portal_url, checkin_url = provision_tenant(company_name, subdomain, admin_username,
                                                          generate_password_hash(admin_password),
                                                          admin_email, payment_option, email_domain=email_domain,
                                                          logo_path=logo_path)
    if not ok:
        flash(error, "error")
        return redirect("/super_admin")

    send_portal_ready_email(admin_email, company_name, admin_username, portal_url, admin_password,
                            checkin_url=checkin_url)

    log_security_event(
        "platform_admin.tenant_created",
        f"Platform admin created tenant '{company_name}' (subdomain={subdomain}, "
        f"payment_option={payment_option})",
        level="INFO", identifier=session.get("platform_admin_username"), subdomain=subdomain,
    )
    flash(f"Company '{company_name}' created. Portal: {portal_url}", "success")
    return redirect("/super_admin")


@platform_admin_bp.route("/super_admin/tenants/<int:tenant_id>/status", methods=["POST"])
@_platform_admin_required
def platform_admin_set_status(tenant_id):
    status = request.form.get("status", "").strip()
    if status not in ("active", "suspended"):
        flash("Invalid status.", "error")
        return redirect("/super_admin")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("UPDATE tenants SET status=%s WHERE id=%s", (status, tenant_id))
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        "platform_admin.status_changed", f"Tenant {tenant_id} status changed to '{status}'",
        level="WARNING" if status == "suspended" else "INFO",
        identifier=session.get("platform_admin_username"), tenant_id=tenant_id, status=status,
    )
    flash("Status updated.", "success")
    return redirect("/super_admin")


@platform_admin_bp.route("/super_admin/tenants/bulk-status", methods=["POST"])
@_platform_admin_required
def platform_admin_bulk_set_status():
    """Same activate/deactivate action as platform_admin_set_status(), applied
    to every selected tenant in one request -- for clearing out a batch of
    stale trial accounts, etc., without one click per row."""
    status = request.form.get("status", "").strip()
    tenant_ids = [int(v) for v in request.form.getlist("tenant_ids") if v.isdigit()]
    if status not in ("active", "suspended"):
        flash("Invalid status.", "error")
        return redirect("/super_admin")
    if not tenant_ids:
        flash("No companies were selected.", "error")
        return redirect("/super_admin")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    placeholders = ",".join(["%s"] * len(tenant_ids))
    cur.execute(
        f"UPDATE tenants SET status=%s WHERE id IN ({placeholders})",  # nosec B608 -- placeholders is a fixed-width %s list sized to tenant_ids, all values still bound as params  # noqa: E501
        [status] + tenant_ids,
    )
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    log_security_event(
        "platform_admin.bulk_status_changed",
        f"{updated} tenant(s) bulk-changed to status '{status}': {tenant_ids}",
        level="WARNING" if status == "suspended" else "INFO",
        identifier=session.get("platform_admin_username"), status=status, tenant_ids=tenant_ids,
    )
    flash(f"{updated} compan{'y' if updated == 1 else 'ies'} updated.", "success")
    return redirect("/super_admin")


# schema names are always "att_" + a subdomain slug limited to [a-z0-9-]
# (blueprints/org.py's _clean_subdomain_slug + provision_tenant's
# db_name = "att_" + subdomain.replace("-", "_")) -- this is a hard gate on
# top of that existing guarantee, checked immediately before the one place
# in this codebase that runs DROP SCHEMA, so a latent bug anywhere upstream
# in slug generation can never turn into arbitrary DDL here.
_SAFE_SCHEMA_NAME_RE = re.compile(r"^att_[a-z0-9_]+$")


@platform_admin_bp.route("/super_admin/tenants/<int:tenant_id>/delete", methods=["POST"])
@_platform_admin_required
def platform_admin_delete_tenant(tenant_id):
    """Permanently deletes a tenant: DROP SCHEMA on its entire Postgres
    schema (every employee, attendance/payroll/leave record, uploaded
    document -- everything company-specific), then removes its att_master
    row. Irreversible, so the UI only ever reaches this route after the
    admin has typed the company's exact subdomain into a confirmation
    field -- re-checked here server-side too, never trusted from a hidden
    field alone.

    Billing/payment history (payment_orders, seat_topup_orders,
    monthly_invoices, auto_debit_mandates) is untouched: those tables
    already carry their own denormalized company_name/subdomain columns
    rather than a live foreign key to tenants (see _recent_payments()'s
    docstring), specifically so the Payments feed keeps showing a
    deleted company's transaction history correctly after this runs."""
    # Owner role: DROP SCHEMA is DDL the runtime role deliberately lacks
    # (database.owner_privileges; same connection when no split is configured).
    conn = get_owner_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT company_name, subdomain, db_name FROM tenants WHERE id=%s", (tenant_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        flash("Company not found.", "error")
        return redirect("/super_admin")
    company_name, subdomain, db_name = row

    confirm = (request.form.get("confirm_subdomain") or "").strip().lower()
    if confirm != subdomain.lower():
        cur.close()
        conn.close()
        flash(f"Deletion cancelled: you must type '{subdomain}' exactly to confirm.", "error")
        return redirect("/super_admin")

    if not _SAFE_SCHEMA_NAME_RE.match(db_name):
        cur.close()
        conn.close()
        app_log.error("platform_admin.delete_tenant: refusing to drop schema with unexpected name %r", db_name)
        flash("Could not delete this company: its schema name failed a safety check. Contact engineering.", "error")
        return redirect("/super_admin")

    try:
        cur.execute(f'DROP SCHEMA IF EXISTS "{db_name}" CASCADE')  # nosec B608 -- db_name is read back from the tenants row (never from this request) and re-validated against _SAFE_SCHEMA_NAME_RE immediately above  # noqa: E501
    except Exception as exc:
        conn.rollback()
        cur.close()
        conn.close()
        app_log.error("platform_admin.delete_tenant: schema drop failed for %s: %s", db_name, exc, exc_info=True)
        flash("Could not delete company data. The error has been logged -- try again or contact engineering.", "error")
        return redirect("/super_admin")

    cur.execute("DELETE FROM tenants WHERE id=%s", (tenant_id,))
    conn.commit()
    cur.close()
    conn.close()
    _employee_count_cache.pop(db_name, None)

    log_security_event(
        "platform_admin.tenant_deleted",
        f"Tenant '{company_name}' (subdomain={subdomain}, schema={db_name}) permanently deleted -- "
        f"all company data removed; payment/billing history retained",
        level="WARNING", identifier=session.get("platform_admin_username"),
        tenant_id=tenant_id, subdomain=subdomain,
    )
    flash(f"'{company_name}' and all its company data have been permanently deleted. Its payment history is still visible below.", "success")
    return redirect("/super_admin")


# ── Gated company-signup review queue (blueprints/org.py's tenant_applications) ──
# Every applicant now goes through email OTP + KYC document upload before
# reaching this queue; provision_tenant() is only ever called from the
# approve action below (or, for paid plans, from billing.py's
# verify_payment() after the post-approval payment step completes).

_APPLICATION_DOC_KINDS = ("registration_cert", "address_proof", "visiting_card", "name_board_photo")
# One fixed statement per document column: doc_kind only ever selects among
# these, so nothing from the request is formatted into SQL (QA SEC-12).
_APPLICATION_DOC_SQL = {kind: "SELECT doc_" + kind + " FROM tenant_applications WHERE id=%s"  # nosec B608
                        for kind in _APPLICATION_DOC_KINDS}
_APPLICATION_STATUS_COLS = (
    "id, company_name, subdomain, admin_username, admin_email, email_domain, gst_number, employee_count, "
    "payment_option, logo_path, email_verified_at, doc_registration_cert, doc_address_proof, "
    "doc_visiting_card, doc_name_board_photo, documents_submitted_at, status, reviewed_by, "
    "reviewed_at, rejection_reason, tenant_id, created_at, doc_review_status"
)
_APPLICATION_COLS = [c.strip() for c in _APPLICATION_STATUS_COLS.split(",")]


def _fetch_application(application_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications WHERE id=%s", (application_id,))  # nosec B608 -- _APPLICATION_STATUS_COLS is a fixed module-level constant, never built from request input  # noqa: E501
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return dict(zip(_APPLICATION_COLS, row))


@platform_admin_bp.route("/super_admin/applications", methods=["GET"])
@_platform_admin_required
def platform_admin_applications_queue():
    # "pending_review" here now means "KYC review outstanding", which since
    # every self-serve application provisions instantly right after OTP
    # (blueprints/org.py's _provision_application_instantly()) covers two
    # shapes: the now-rare legacy row (status='pending_review', no tenant
    # yet) and the common case (status='provisioned', doc_review_status=
    # 'pending_review'). Every other filter value still matches doc_review_status
    # directly, which is what actually tracks review state for a provisioned
    # application.
    status_filter = request.args.get("status", "pending_review")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    if status_filter == "all":
        cur.execute(f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications ORDER BY created_at DESC")  # nosec B608 -- fixed constant, see _fetch_application  # noqa: E501
    elif status_filter == "pending_review":
        cur.execute(
            f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications "  # nosec B608
            "WHERE status='pending_review' OR (status='provisioned' AND doc_review_status='pending_review') "
            "ORDER BY created_at ASC"
        )
    else:
        cur.execute(
            f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications WHERE status=%s ORDER BY created_at ASC",  # nosec B608
            (status_filter,)
        )
    rows = cur.fetchall()
    cur.execute(
        "SELECT COUNT(*) FROM tenant_applications "
        "WHERE status='pending_review' OR (status='provisioned' AND doc_review_status='pending_review')"
    )
    pending_count = cur.fetchone()[0]
    cur.close()
    conn.close()
    applications = [dict(zip(_APPLICATION_COLS, r)) for r in rows]
    return render_template(
        "super_admin_applications.html", applications=applications,
        status_filter=status_filter, pending_count=pending_count,
    )


@platform_admin_bp.route("/super_admin/applications/<int:application_id>", methods=["GET"])
@_platform_admin_required
def platform_admin_application_detail(application_id):
    application = _fetch_application(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/super_admin/applications")
    return render_template("super_admin_application_detail.html", application=application,
                           doc_kinds=_APPLICATION_DOC_KINDS)


@platform_admin_bp.route("/super_admin/applications/<int:application_id>/documents/<doc_kind>", methods=["GET"])
@_platform_admin_required
def platform_admin_view_document(application_id, doc_kind):
    """Streams one uploaded KYC document. This route + the
    @_platform_admin_required guard above is the ENTIRE access control for
    these files -- they're saved outside static/ (see utils/helpers.py's
    save_application_document()) specifically so there is no other way to
    reach them. doc_kind is checked against a fixed whitelist and the
    actual filesystem path/S3 ref always comes from the DB row, never from
    anything client-supplied, so this can't be tricked into serving an
    arbitrary path.

    Reads via utils/storage.py's open_private() rather than send_file(path)
    directly, since the stored ref may be a local path OR an "s3://..."
    reference (whichever save_application_document() actually used) --
    open_private() handles both transparently, this route doesn't need to
    know or care which mode is active."""
    if doc_kind not in _APPLICATION_DOC_KINDS:
        flash("Invalid document type.", "error")
        return redirect(f"/super_admin/applications/{application_id}")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(_APPLICATION_DOC_SQL[doc_kind], (application_id,))  # nosec B608 -- doc_kind is allowlist-checked against _APPLICATION_DOC_KINDS above, never interpolated from an unchecked value  # noqa: E501
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row[0]:
        flash("Document not found.", "error")
        return redirect(f"/super_admin/applications/{application_id}")
    import io
    import mimetypes
    from flask import send_file
    from utils.storage import open_private
    try:
        data = open_private(row[0])
    except Exception as exc:
        app_log.warning("Could not read application document %s (application_id=%s): %s", row[0], application_id, exc, exc_info=True)
        flash("Could not read this document.", "error")
        return redirect(f"/super_admin/applications/{application_id}")
    mimetype = mimetypes.guess_type(row[0])[0] or "application/octet-stream"
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=False)


@platform_admin_bp.route("/super_admin/applications/<int:application_id>/approve", methods=["POST"])
@_platform_admin_required
def platform_admin_approve_application(application_id):
    from blueprints.org import provision_tenant, check_duplicate_name, check_duplicate_gst, \
        check_duplicate_admin_email, _record_duplicate_alert, send_portal_ready_email, _GENERIC_DUPLICATE_MSG, \
        _approve_application_reviewed

    application = _fetch_application(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/super_admin/applications")

    if application["tenant_id"] is not None:
        # Every self-serve application now provisions instantly right
        # after OTP verification (blueprints/org.py's
        # _provision_application_instantly()) -- by the time it reaches
        # this queue, the tenant has been live for a while already, and
        # utils/document_verification.py's automated worker has usually
        # already resolved most applications one way or the other before a
        # human ever sees this route -- this only fires for whatever the
        # automation left at 'pending_review' (or the rare case a platform
        # admin wants to override before the automated check finishes).
        # _approve_application_reviewed() is the exact same logic the
        # automated worker itself calls, just with reviewed_by set to this
        # platform admin's username instead of the worker's system identity.
        ok, msg = _approve_application_reviewed(application, session.get("platform_admin_username"))
        flash(msg, "success" if ok else "error")
        return redirect("/super_admin/applications")

    if application["status"] != "pending_review":
        flash("This application isn't awaiting review.", "error")
        return redirect("/super_admin/applications")

    # Re-check for a name/GST/email collision -- state may have shifted
    # since this application was submitted (e.g. a different applicant
    # with the same name, GST, or email was approved first).
    conflicting, match_type = check_duplicate_name(application["company_name"])
    if not conflicting:
        conflicting, match_type = check_duplicate_gst(application["gst_number"]), "gst"
    if not conflicting:
        conflicting, match_type = check_duplicate_admin_email(application["admin_email"]), "email"
    if conflicting:
        _record_duplicate_alert(application_id, application["company_name"], application["admin_email"],
                                conflicting, match_type=match_type)
        flash(_GENERIC_DUPLICATE_MSG + " (duplicate detected at approval time)", "error")
        return redirect(f"/super_admin/applications/{application_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    reviewer = session.get("platform_admin_username")

    if application["payment_option"] == "online":
        # Payment happens AFTER approval for paid plans -- provisioning is
        # deferred until blueprints/billing.py's verify_payment() confirms
        # the charge (see create_order()'s new application_id requirement).
        # Rotate the application's access token (same one-live-token model
        # as the rejection email below) so the emailed pay link can carry
        # it -- /create_org/pay/<id> now requires it (blueprints/org.py).
        access_token = secrets.token_urlsafe(32)
        cur.execute(
            "UPDATE tenant_applications SET status='approved_pending_payment', reviewed_by=%s, reviewed_at=NOW(), "
            "access_token_hash=%s, updated_at=NOW() WHERE id=%s",
            (reviewer, _hash_token(access_token), application_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        from utils.email_utils import get_email_config, send_email_async
        pay_url = f"{request.url_root.rstrip('/')}/create_org/pay/{application_id}?token={access_token}"
        email_cfg = get_email_config()
        if email_cfg:
            send_email_async(
                application["admin_email"], f"Your application was approved -- complete payment for {application['company_name']}",
                f"<p>Your company registration for <strong>{application['company_name']}</strong> has been approved.</p>"
                f"<p><a href=\"{pay_url}\">Complete payment to activate your portal</a></p>",
                email_cfg,
            )
        log_security_event("platform_admin.application_approved", f"Application {application_id} approved (awaiting payment)",
                           level="INFO", identifier=reviewer, application_id=application_id)
        flash("Application approved. The applicant has been emailed a private payment link.", "success")
        return redirect("/super_admin/applications")

    if application["payment_option"] == "trial":
        # Same deferred-provisioning shape as the online branch above --
        # provisioning only starts once the applicant authorizes a payment
        # mandate via blueprints/org.py's create_org_setup_trial_confirm(),
        # not at approval time. The trial clock itself starts even later,
        # at the tenant's first login (app.py's inject_billing_lock_status()).
        # Reuses the
        # 'approved_pending_payment' status (same meaning here: "approved,
        # one external step still outstanding") rather than adding a
        # parallel status string every status-aware view would also need
        # to learn -- create_org_status.html branches on payment_option to
        # show trial-appropriate copy for it.
        access_token = secrets.token_urlsafe(32)
        cur.execute(
            "UPDATE tenant_applications SET status='approved_pending_payment', reviewed_by=%s, reviewed_at=NOW(), "
            "access_token_hash=%s, updated_at=NOW() WHERE id=%s",
            (reviewer, _hash_token(access_token), application_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        from utils.email_utils import get_email_config, send_email_async
        setup_url = f"{request.url_root.rstrip('/')}/create_org/setup_trial/{application_id}?token={access_token}"
        email_cfg = get_email_config()
        if email_cfg:
            from blueprints.trial_billing import TRIAL_DURATION_DAYS
            send_email_async(
                application["admin_email"],
                f"Your application was approved -- set up your {TRIAL_DURATION_DAYS}-day free trial for {application['company_name']}",
                f"<p>Your company registration for <strong>{application['company_name']}</strong> has been approved.</p>"
                f"<p><a href=\"{setup_url}\">Set up your free trial</a> -- you'll authorize a payment method now, "
                f"and your {TRIAL_DURATION_DAYS}-day trial starts the first time you log in. You can cancel anytime "
                f"before it ends with no charge.</p>",
                email_cfg,
            )
        log_security_event("platform_admin.application_approved", f"Application {application_id} approved (awaiting trial mandate setup)",
                           level="INFO", identifier=reviewer, application_id=application_id)
        flash("Application approved. The applicant has been emailed a private trial setup link.", "success")
        return redirect("/super_admin/applications")

    cur.execute("SELECT admin_password_hash FROM tenant_applications WHERE id=%s", (application_id,))
    admin_password_hash = cur.fetchone()[0]
    cur.close()
    conn.close()

    ok, error, portal_url, checkin_url = provision_tenant(
        application["company_name"], application["subdomain"], application["admin_username"], admin_password_hash,
        application["admin_email"], payment_option=application["payment_option"],
        email_domain=application["email_domain"], employee_count=application["employee_count"],
        logo_path=application["logo_path"], gst_number=application["gst_number"],
    )
    if not ok:
        flash(f"Approval failed: {error}", "error")
        return redirect(f"/super_admin/applications/{application_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT id FROM tenants WHERE subdomain=%s", (application["subdomain"],))
    tenant_id = cur.fetchone()[0]
    cur.execute(
        "UPDATE tenant_applications SET status='provisioned', reviewed_by=%s, reviewed_at=NOW(), tenant_id=%s, "
        "updated_at=NOW() WHERE id=%s",
        (reviewer, tenant_id, application_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    send_portal_ready_email(application["admin_email"], application["company_name"], application["admin_username"],
                            portal_url, checkin_url=checkin_url)
    log_security_event("platform_admin.application_approved", f"Application {application_id} approved and provisioned",
                       level="INFO", identifier=reviewer, application_id=application_id, tenant_id=tenant_id)
    flash(f"Company '{application['company_name']}' approved and provisioned. Portal: {portal_url}", "success")
    return redirect("/super_admin/applications")


@platform_admin_bp.route("/super_admin/applications/<int:application_id>/reject", methods=["POST"])
@_platform_admin_required
def platform_admin_reject_application(application_id):
    from blueprints.org import send_application_rejected_email, _reject_application_reviewed
    from utils.auth import _hash_token
    from utils.helpers import _safe_app_url
    reason = request.form.get("reason", "").strip()[:1000]
    application = _fetch_application(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/super_admin/applications")

    reviewer = session.get("platform_admin_username")

    if application["tenant_id"] is not None:
        # Already provisioned -- reuse the exact same suspend+re-upload-link
        # logic utils/document_verification.py's automated worker itself
        # calls for a confident duplicate match, just with reviewed_by set
        # to this platform admin instead of the worker's system identity.
        ok, msg = _reject_application_reviewed(application, reviewer, reason)
        flash(msg, "success" if ok else "error")
        return redirect("/super_admin/applications")

    # A fresh access_token is issued here specifically so the rejection
    # email can carry a working re-upload link -- the original raw token
    # (from application submission) was never persisted anywhere except
    # that first session, which is very often long gone by the time
    # someone acts on a rejection email days later. Rotating it here is
    # safe: the old token stops working the moment this UPDATE lands,
    # same one-token-live-at-a-time model as every other flow that reuses
    # access_token_hash.
    new_token = secrets.token_urlsafe(32)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE tenant_applications SET status='rejected', doc_review_status='rejected', reviewed_by=%s, "
        "reviewed_at=NOW(), rejection_reason=%s, access_token_hash=%s, updated_at=NOW() WHERE id=%s",
        (reviewer, reason, _hash_token(new_token), application_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    status_url = f"{_safe_app_url()}/create_org/status/{application_id}?token={new_token}"
    send_application_rejected_email(application["admin_email"], application["company_name"], reason, status_url)
    log_security_event("platform_admin.application_rejected", f"Application {application_id} rejected",
                       level="INFO", identifier=reviewer, application_id=application_id, reason=reason)
    flash("Application rejected.", "success")
    return redirect("/super_admin/applications")


@platform_admin_bp.route("/super_admin/duplicate-alerts", methods=["GET"])
@_platform_admin_required
def platform_admin_duplicate_alerts():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, application_id, attempted_company_name, attempted_admin_email, conflicting_tenant_id, "
        "conflicting_company_name, conflicting_admin_email, match_type, acknowledged, acknowledged_by, "
        "acknowledged_at, source_ip, created_at "
        "FROM tenant_duplicate_alerts ORDER BY acknowledged ASC, created_at DESC"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    cols = ["id", "application_id", "attempted_company_name", "attempted_admin_email", "conflicting_tenant_id",
            "conflicting_company_name", "conflicting_admin_email", "match_type", "acknowledged", "acknowledged_by",
            "acknowledged_at", "source_ip", "created_at"]
    alerts = [dict(zip(cols, r)) for r in rows]
    return render_template("super_admin_duplicate_alerts.html", alerts=alerts)


@platform_admin_bp.route("/super_admin/duplicate-alerts/<int:alert_id>/acknowledge", methods=["POST"])
@_platform_admin_required
def platform_admin_acknowledge_duplicate_alert(alert_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE tenant_duplicate_alerts SET acknowledged=1, acknowledged_by=%s, acknowledged_at=NOW() WHERE id=%s",
        (session.get("platform_admin_username"), alert_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    flash("Alert acknowledged.", "success")
    return redirect("/super_admin/duplicate-alerts")


@platform_admin_bp.route("/super_admin/duplicate-alerts/bulk-acknowledge", methods=["POST"])
@_platform_admin_required
def platform_admin_bulk_acknowledge_duplicate_alerts():
    alert_ids = [int(v) for v in request.form.getlist("alert_ids") if v.isdigit()]
    if not alert_ids:
        flash("No alerts were selected.", "error")
        return redirect("/super_admin/duplicate-alerts")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    placeholders = ",".join(["%s"] * len(alert_ids))
    cur.execute(
        f"UPDATE tenant_duplicate_alerts SET acknowledged=1, acknowledged_by=%s, acknowledged_at=NOW() "  # nosec B608 -- placeholders is a fixed-width %s list sized to alert_ids, all values still bound as params  # noqa: E501
        f"WHERE id IN ({placeholders})",
        [session.get("platform_admin_username")] + alert_ids,
    )
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    flash(f"{updated} alert(s) acknowledged.", "success")
    return redirect("/super_admin/duplicate-alerts")


# ── Audit log: browsable view over security_events ──────────────────────
# security_events already exists (extensions.py's log_security_event) and
# every platform-admin action in this blueprint already writes to it --
# this just gives the admin a page to search/filter/page through that
# history instead of only ever seeing the dashboard's 20-row preview.

@platform_admin_bp.route("/super_admin/audit-log", methods=["GET"])
@_platform_admin_required
def platform_admin_audit_log():
    q = request.args.get("q", "").strip()
    level_filter = request.args.get("level", "").strip().upper()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    prefix_clause = " OR ".join(["event_type LIKE %s"] * len(_AUDIT_LOG_PREFIXES))
    conditions = [f"({prefix_clause})"]
    params = [f"{p}%" for p in _AUDIT_LOG_PREFIXES]
    if level_filter in ("INFO", "WARNING", "ERROR", "CRITICAL"):
        conditions.append("level=%s")
        params.append(level_filter)
    if q:
        conditions.append("(message ILIKE %s OR identifier ILIKE %s OR event_type ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    where_sql = " AND ".join(conditions)  # nosec B608 -- conditions are fixed literals built above, never from unescaped request input; all variable values are bound params  # noqa: E501

    conn = get_db_connection()
    cur = conn.cursor(buffered=True)
    cur.execute(f"SELECT COUNT(*) FROM security_events WHERE {where_sql}", params)  # nosec B608 -- see where_sql note above
    total = cur.fetchone()[0]
    total_pages = max(1, -(-total // _AUDIT_LOG_PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * _AUDIT_LOG_PER_PAGE
    cur.execute(
        f"SELECT event_type, level, message, identifier, ip, created_at FROM security_events "  # nosec B608 -- see where_sql note above
        f"WHERE {where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        params + [_AUDIT_LOG_PER_PAGE, offset],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    events = [
        {"event_type": r[0], "level": r[1], "message": r[2], "identifier": r[3], "ip": r[4], "created_at": r[5]}
        for r in rows
    ]
    return render_template(
        "super_admin_audit_log.html", events=events, q=q, level_filter=level_filter,
        page=page, total_pages=total_pages, total=total,
    )


@platform_admin_bp.route("/super_admin/soc", methods=["GET"])
@_platform_admin_required
def platform_admin_soc_dashboard():
    """Embeds the standalone cross-app SOC dashboard (soc-dashboard/, a
    separate Node/Express + React service -- see its README) in an iframe.

    That service aggregates security events (login attempts, failed
    logins, unauthorized access, suspicious activity) from the 4 separate
    apps -- HRMS Portal, Job Portal, Gradzest Portal, Food Delivery App --
    into one shared MongoDB collection. It is unrelated to this app's own
    `security_events` Postgres table used above by the audit log: that one
    is this Flask app's internal platform-admin action log.

    SOC_ADMIN_TOKEN is read server-side and appended to the iframe URL just
    once on page load so the SOC dashboard's own token gate never has to be
    shown to the platform admin -- the token itself never touches this
    app's own session or database.
    """
    soc_url = os.environ.get("SOC_DASHBOARD_URL", "").rstrip("/")
    if not soc_url:
        flash("SOC dashboard is not configured. Set SOC_DASHBOARD_URL in the environment.", "error")
        return redirect("/super_admin")
    soc_token = os.environ.get("SOC_ADMIN_TOKEN", "")
    iframe_src = f"{soc_url}/?token={soc_token}" if soc_token else soc_url
    return render_template("super_admin_soc_dashboard.html", iframe_src=iframe_src)


# ── Mini-CRM: company profile (financials + notes + tickets) ───────────────
# One page per company pulling together everything the Platform Admin
# already tracks about them (payment history, live billing state) plus two
# new things: free-text internal notes, and a trackable support-ticket
# queue -- distinct from the real-time chat below, which has no status or
# history a report could be built on.


def _fetch_tenant(tenant_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, company_name, subdomain, db_name, payment_option, status, created_at, "
        "billing_state, grace_period_ends_at, admin_email FROM tenants WHERE id=%s",
        (tenant_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    (tid, company_name, subdomain, db_name, payment_option, status, created_at,
     billing_state, grace_period_ends_at, admin_email) = row
    employee_count = _tenant_employee_count(db_name)
    return {
        "id": tid, "company_name": company_name, "subdomain": subdomain, "db_name": db_name,
        "payment_option": payment_option or "online", "status": status, "created_at": created_at,
        "billing_state": billing_state or "current", "grace_period_ends_at": coerce_datetime(grace_period_ends_at),
        "admin_email": admin_email, "employee_count": employee_count,
        "monthly_bill_display": format_price_inr(calculate_price(employee_count)),
    }


def _load_tenant_admins(schema):
    """Best-effort list of this company's own portal admin accounts, for
    the profile page's Contacts card. Never raises -- a schema that's
    since been deleted (platform_admin_delete_tenant()), or a transient DB
    hiccup, just shows an empty contacts list instead of breaking the
    page the platform admin is trying to view."""
    try:
        conn = get_tenant_db(schema)
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT username, email FROM admin_users ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"username": r[0], "email": r[1]} for r in rows]
    except Exception as exc:
        app_log.warning("platform_admin: tenant admin lookup failed for schema %s: %s", schema, exc)
        return []


def _load_tenant_notes(tenant_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, author, note, created_at FROM tenant_notes WHERE tenant_id=%s ORDER BY created_at DESC",
        (tenant_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "author": r[1], "note": r[2], "created_at": r[3]} for r in rows]


@platform_admin_bp.route("/super_admin/companies/<int:tenant_id>", methods=["GET"])
@_platform_admin_required
def platform_admin_company_profile(tenant_id):
    tenant = _fetch_tenant(tenant_id)
    if not tenant:
        flash("Company not found.", "error")
        return redirect("/super_admin")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, subject, status, priority, created_at, updated_at FROM tenant_support_tickets "
        "WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 50",
        (tenant_id,)
    )
    tickets = [
        {"id": r[0], "subject": r[1], "status": r[2], "priority": r[3], "created_at": r[4], "updated_at": r[5]}
        for r in cur.fetchall()
    ]
    cur.close()
    conn.close()

    return render_template(
        "super_admin_company_profile.html",
        tenant=tenant,
        portal_base_url=_safe_app_url(),
        contacts=_load_tenant_admins(tenant["db_name"]),
        notes=_load_tenant_notes(tenant_id),
        payments=_recent_payments(limit=200, tenant_id=tenant_id, tenant_schema=tenant["db_name"]),
        tickets=tickets,
        ticket_priorities=_TICKET_PRIORITIES,
    )


@platform_admin_bp.route("/super_admin/companies/<int:tenant_id>/notes", methods=["POST"])
@_platform_admin_required
def platform_admin_add_tenant_note(tenant_id):
    note = (request.form.get("note") or "").strip()[:4000]
    if not note:
        flash("Note cannot be empty.", "error")
        return redirect(f"/super_admin/companies/{tenant_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT 1 FROM tenants WHERE id=%s", (tenant_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        flash("Company not found.", "error")
        return redirect("/super_admin")
    cur.execute(
        "INSERT INTO tenant_notes (tenant_id, author, note) VALUES (%s,%s,%s)",
        (tenant_id, session.get("platform_admin_username"), note),
    )
    conn.commit()
    cur.close()
    conn.close()
    flash("Note added.", "success")
    return redirect(f"/super_admin/companies/{tenant_id}")


@platform_admin_bp.route("/super_admin/companies/<int:tenant_id>/tickets", methods=["POST"])
@_platform_admin_required
def platform_admin_create_ticket(tenant_id):
    tenant = _fetch_tenant(tenant_id)
    if not tenant:
        flash("Company not found.", "error")
        return redirect("/super_admin")

    subject = (request.form.get("subject") or "").strip()[:200]
    description = (request.form.get("description") or "").strip()[:4000]
    priority = request.form.get("priority", "normal").strip()
    if priority not in _TICKET_PRIORITIES:
        priority = "normal"
    if not subject or not description:
        flash("Subject and description are required.", "error")
        return redirect(f"/super_admin/companies/{tenant_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "INSERT INTO tenant_support_tickets (tenant_id, subject, description, priority, created_by) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (tenant_id, subject, description, priority, session.get("platform_admin_username")),
    )
    ticket_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        "platform_admin.ticket_created", f"Support ticket #{ticket_id} opened for '{tenant['company_name']}': {subject}",
        level="INFO", identifier=session.get("platform_admin_username"), tenant_id=tenant_id,
    )
    flash("Ticket created.", "success")
    return redirect(f"/super_admin/tickets/{ticket_id}")


def _fetch_ticket(ticket_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT tk.id, tk.tenant_id, t.company_name, t.subdomain, tk.subject, tk.description, tk.status, "
        "tk.priority, tk.created_by, tk.resolved_at, tk.created_at, tk.updated_at "
        "FROM tenant_support_tickets tk LEFT JOIN tenants t ON t.id = tk.tenant_id WHERE tk.id=%s",
        (ticket_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "tenant_id": row[1], "company_name": row[2] or "(deleted company)", "subdomain": row[3] or "—",
        "subject": row[4], "description": row[5], "status": row[6], "priority": row[7], "created_by": row[8],
        "resolved_at": row[9], "created_at": row[10], "updated_at": row[11],
    }


@platform_admin_bp.route("/super_admin/tickets", methods=["GET"])
@_platform_admin_required
def platform_admin_tickets_queue():
    status_filter = request.args.get("status", "open")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    if status_filter == "all":
        cur.execute(
            "SELECT tk.id, tk.tenant_id, t.company_name, t.subdomain, tk.subject, tk.status, tk.priority, "
            "tk.created_at, tk.updated_at FROM tenant_support_tickets tk "
            "LEFT JOIN tenants t ON t.id = tk.tenant_id ORDER BY tk.created_at DESC"
        )
    else:
        cur.execute(
            "SELECT tk.id, tk.tenant_id, t.company_name, t.subdomain, tk.subject, tk.status, tk.priority, "
            "tk.created_at, tk.updated_at FROM tenant_support_tickets tk "
            "LEFT JOIN tenants t ON t.id = tk.tenant_id WHERE tk.status=%s ORDER BY tk.created_at DESC",
            (status_filter,)
        )
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM tenant_support_tickets WHERE status IN ('open','in_progress')")
    open_count = cur.fetchone()[0]
    cur.close()
    conn.close()
    tickets = [
        {"id": r[0], "tenant_id": r[1], "company_name": r[2] or "(deleted company)", "subdomain": r[3] or "—",
         "subject": r[4], "status": r[5], "priority": r[6], "created_at": r[7], "updated_at": r[8]}
        for r in rows
    ]
    return render_template(
        "super_admin_tickets.html", tickets=tickets, status_filter=status_filter, open_count=open_count,
    )


@platform_admin_bp.route("/super_admin/tickets/<int:ticket_id>", methods=["GET"])
@_platform_admin_required
def platform_admin_ticket_detail(ticket_id):
    ticket = _fetch_ticket(ticket_id)
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect("/super_admin/tickets")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT author, comment, created_at FROM tenant_ticket_comments WHERE ticket_id=%s ORDER BY created_at ASC",
        (ticket_id,)
    )
    comments = [{"author": r[0], "comment": r[1], "created_at": r[2]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return render_template(
        "super_admin_ticket_detail.html", ticket=ticket, comments=comments,
        statuses=_TICKET_STATUSES, priorities=_TICKET_PRIORITIES,
    )


@platform_admin_bp.route("/super_admin/tickets/<int:ticket_id>/update", methods=["POST"])
@_platform_admin_required
def platform_admin_update_ticket(ticket_id):
    status = request.form.get("status", "").strip()
    priority = request.form.get("priority", "").strip()
    if status not in _TICKET_STATUSES or priority not in _TICKET_PRIORITIES:
        flash("Invalid status or priority.", "error")
        return redirect(f"/super_admin/tickets/{ticket_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    if status in ("resolved", "closed"):
        cur.execute(
            "UPDATE tenant_support_tickets SET status=%s, priority=%s, resolved_at=NOW() WHERE id=%s",
            (status, priority, ticket_id)
        )
    else:
        cur.execute(
            "UPDATE tenant_support_tickets SET status=%s, priority=%s, resolved_at=NULL WHERE id=%s",
            (status, priority, ticket_id)
        )
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if not updated:
        flash("Ticket not found.", "error")
        return redirect("/super_admin/tickets")
    log_security_event(
        "platform_admin.ticket_updated", f"Ticket #{ticket_id} set to status={status}, priority={priority}",
        level="INFO", identifier=session.get("platform_admin_username"),
    )
    flash("Ticket updated.", "success")
    return redirect(f"/super_admin/tickets/{ticket_id}")


@platform_admin_bp.route("/super_admin/tickets/<int:ticket_id>/comment", methods=["POST"])
@_platform_admin_required
def platform_admin_add_ticket_comment(ticket_id):
    comment = (request.form.get("comment") or "").strip()[:2000]
    if not comment:
        flash("Comment cannot be empty.", "error")
        return redirect(f"/super_admin/tickets/{ticket_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT tenant_id, subject FROM tenant_support_tickets WHERE id=%s", (ticket_id,))
    ticket_row = cur.fetchone()
    if not ticket_row:
        cur.close()
        conn.close()
        flash("Ticket not found.", "error")
        return redirect("/super_admin/tickets")
    tenant_id, subject = ticket_row
    cur.execute(
        "INSERT INTO tenant_ticket_comments (ticket_id, author, comment, author_kind) VALUES (%s,%s,%s,'platform_admin')",
        (ticket_id, session.get("platform_admin_username"), comment),
    )
    cur.execute("UPDATE tenant_support_tickets SET updated_at=NOW() WHERE id=%s", (ticket_id,))
    conn.commit()
    cur.close()
    conn.close()
    # Notify the company admin in-app -- resolved via the same
    # tenant_id->schema lookup the chat feature already uses below, since
    # this route (like every /super_admin/* route) has no g.tenant_db of
    # its own for _create_notification()'s default path to fall back on.
    schema_row = _tenant_schema_for_id(tenant_id)
    if schema_row:
        schema_name, _company_name = schema_row
        _create_notification(
            "admin", f"Support ticket reply: {subject}", comment, tenant_schema=schema_name,
        )
    flash("Comment added.", "success")
    return redirect(f"/super_admin/tickets/{ticket_id}")


# ── Internal chat with a company's admin/HR (utils/chat_utils.py) ──────────
# One shared thread per tenant, keyed by that tenant's db_name -- resolved
# fresh from tenant_id on every call rather than trusted from the client, so
# a platform admin can never read/write a thread for a tenant_id that
# doesn't actually exist.

def _tenant_schema_for_id(tenant_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT db_name, company_name FROM tenants WHERE id=%s", (tenant_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


@platform_admin_bp.route("/super_admin/chat/<int:tenant_id>/messages", methods=["GET"])
@_platform_admin_required
def platform_admin_chat_messages(tenant_id):
    row = _tenant_schema_for_id(tenant_id)
    if not row:
        return jsonify({"ok": False, "msg": "Unknown company."}), 404
    schema_name, company_name = row
    chat_utils.mark_read(schema_name, "platform")
    return jsonify({"ok": True, "company_name": company_name, "messages": chat_utils.list_messages(schema_name)})


@platform_admin_bp.route("/super_admin/chat/<int:tenant_id>/send", methods=["POST"])
@_platform_admin_required
def platform_admin_chat_send(tenant_id):
    row = _tenant_schema_for_id(tenant_id)
    if not row:
        return jsonify({"ok": False, "msg": "Unknown company."}), 404
    schema_name, _company_name = row
    data = request.get_json(silent=True) or {}
    username = session["platform_admin_username"]
    sent = chat_utils.send_message(schema_name, "platform_admin", username, data.get("message"))
    if not sent:
        return jsonify({"ok": False, "msg": "Message cannot be empty."}), 400
    return jsonify({"ok": True})
