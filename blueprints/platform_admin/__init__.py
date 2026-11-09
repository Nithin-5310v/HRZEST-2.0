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

Package layout (split from one module; routes register in this order):
  auth           login (password + emailed MFA code) and logout
  dashboard      dashboard, tenant list/status/delete, costs, rate, leads, CSV exports
  applications   gated company-signup review queue (approve/reject, documents)
  alerts         duplicate-signup alerts
  audit          audit log over security_events, SOC dashboard link
  crm            mini-CRM: company profile, notes, ticket creation
  tickets        support ticket queue, detail, updates, comments
  chat           internal chat with a company's admin/HR
"""
import functools
import os
import time
from database import get_master_db
from flask import Blueprint, flash, redirect, session

platform_admin_bp = Blueprint("platform_admin", __name__)


@platform_admin_bp.context_processor
def _inject_soc_flag():
    """templates/_platform_sidebar.html is on every console page, and only
    links the SOC page when it's configured."""
    return {"soc_dashboard_configured": bool(os.environ.get("SOC_DASHBOARD_URL"))}


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


def platform_admin_session_active():
    """Whether this browser holds a live platform-admin session (logged in
    and not idle past _IDLE_TIMEOUT_SEC) -- for pages outside /super_admin
    that offer a platform-admin-only option, like org.py's free company
    registration. No side effects, unlike _platform_admin_required."""
    if not session.get("platform_admin_logged_in"):
        return False
    return (time.time() - session.get("platform_admin_last_activity", 0)) <= _IDLE_TIMEOUT_SEC


def _tenant_schema_for_id(tenant_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT db_name, company_name FROM tenants WHERE id=%s", (tenant_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


# Route modules -- importing them registers their routes on the blueprint above.
from . import auth, dashboard, applications, alerts, audit, crm, tickets, chat  # noqa: E402,F401

# Re-exported so `blueprints.platform_admin.<name>` keeps resolving for existing callers.
from .auth import (  # noqa: E402,F401
    _complete_platform_admin_login,
    _discard_platform_challenge,
    platform_admin_login,
    platform_admin_logout,
)
from .dashboard import (  # noqa: E402,F401
    _SAFE_SCHEMA_NAME_RE,
    _SUGGESTED_MARGIN_PCT,
    _SUGGESTED_ROUND_TO_PAISE,
    _compute_pnl,
    _filter_tenants,
    _get_platform_costs,
    _load_all_tenants,
    _recent_feedback,
    _recent_leads,
    _recent_payments,
    _recent_platform_activity,
    _self_signup_tenant_ids,
    _tenant_employee_count,
    platform_admin_bulk_set_status,
    platform_admin_dashboard,
    platform_admin_delete_tenant,
    platform_admin_export_payments_csv,
    platform_admin_export_tenants_csv,
    platform_admin_set_costs,
    platform_admin_set_lead_status,
    platform_admin_set_rate,
    platform_admin_set_status,
)
from .applications import (  # noqa: E402,F401
    _APPLICATION_COLS,
    _APPLICATION_DOC_KINDS,
    _APPLICATION_DOC_SQL,
    _APPLICATION_STATUS_COLS,
    _fetch_application,
    platform_admin_application_detail,
    platform_admin_applications_queue,
    platform_admin_approve_application,
    platform_admin_reject_application,
    platform_admin_view_document,
)
from .alerts import (  # noqa: E402,F401
    platform_admin_acknowledge_duplicate_alert,
    platform_admin_block_duplicate_alert,
    platform_admin_bulk_acknowledge_duplicate_alerts,
    platform_admin_bulk_block_duplicate_alerts,
    platform_admin_delete_duplicate_alert,
    platform_admin_duplicate_alerts,
    platform_admin_unblock_signup,
)
from .audit import (  # noqa: E402,F401
    platform_admin_audit_log,
    platform_admin_soc_dashboard,
)
from .crm import (  # noqa: E402,F401
    _fetch_tenant,
    _load_tenant_admins,
    _load_tenant_notes,
    platform_admin_add_tenant_note,
    platform_admin_company_profile,
    platform_admin_create_ticket,
)
from .tickets import (  # noqa: E402,F401
    _fetch_ticket,
    platform_admin_add_ticket_comment,
    platform_admin_ticket_detail,
    platform_admin_tickets_queue,
    platform_admin_update_ticket,
)
from .chat import (  # noqa: E402,F401
    platform_admin_chat_messages,
    platform_admin_chat_send,
)
