# -*- coding: utf-8 -*-
"""platform_admin: dashboard, tenant list/status/delete, costs, rate, leads, CSV exports"""
import os
import re
import time
from database import get_db_connection, get_master_db, get_owner_master_db
from extensions import app_log, log_security_event
from flask import flash, redirect, render_template, request, session
from utils.analytics import get_traffic_stats
from utils.csv_export import csv_response as _csv_response
from utils.helpers import _safe_app_url, coerce_datetime
from utils.payment_history import get_payment_history
from utils.plan_limits import (
    calculate_price,
    format_price_inr,
    get_base_fee_paise,
    get_minimum_monthly_paise,
    get_per_employee_paise,
    get_tenant_employee_count,
    invalidate_extra_cost_cache,
    invalidate_rate_paise_cache,
)
from . import (
    _COUNT_CACHE_TTL_SEC,
    _TENANTS_PER_PAGE,
    _employee_count_cache,
    _paginate,
    _platform_admin_required,
    platform_admin_bp,
)


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
    than being set up by a platform admin: a free registration started from
    this dashboard (tenant_applications.free_registration) or a tenant from
    the former direct-create form, which never touched either table. A
    payment_orders or tenant_applications row with tenant_id set is the
    signal -- no separate 'source' column needed on tenants itself."""
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT DISTINCT tenant_id FROM payment_orders WHERE tenant_id IS NOT NULL")
        ids = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT tenant_id FROM tenant_applications "
                    "WHERE tenant_id IS NOT NULL AND NOT free_registration")
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


@platform_admin_bp.route("/super_admin/payments/<kind_key>/<int:row_id>/invoice.pdf")
@_platform_admin_required
def platform_admin_payment_invoice(kind_key, row_id):
    """Any company's tax invoice for a successful payment (utils/
    tax_invoice.py), issued on first download if it has none yet."""
    from flask import Response
    from utils.tax_invoice import build_tax_invoice_pdf, invoice_filename, issue_invoice
    inv = issue_invoice(kind_key, row_id)
    if not inv:
        return "Invoice not found.", 404
    resp = Response(build_tax_invoice_pdf(inv), mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="{invoice_filename(inv)}"'
    return resp


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
    from utils.razorpay_utils import create_plan, accepting_online_payments
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
    if accepting_online_payments():
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
