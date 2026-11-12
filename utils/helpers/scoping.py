# -*- coding: utf-8 -*-
"""helpers: company / HR / manager scoping WHERE fragments and ownership checks"""
from flask import session
from .db import _db


# ── Company-scoping WHERE fragments ─────────────────────────────────────────
# Was hand-repeated (6 near-identical copies) across admin_views.py/leave.py --
# the fragment is always a hardcoded literal chosen by whether an active
# company is selected, never user input; the actual value is always the
# single %s-bound param returned alongside it.
def co_scope_subquery(active_cid, alias=""):
    """WHERE fragment + params scoping by company via a subquery, for tables
    that don't have their own company_id column (attendance, leave_requests,
    tickets, ...). Returns ("", ()) when no active company is selected."""
    if not active_cid:
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN (SELECT employee_id FROM employees WHERE company_id=%s)", (active_cid,)  # nosec B608


def co_scope_column(active_cid, alias=""):
    """WHERE fragment + params scoping by company via a direct company_id
    column (e.g. the employees table itself)."""
    if not active_cid:
        return "", ()
    col = f"{alias}.company_id" if alias else "company_id"
    return f"AND {col}=%s", (active_cid,)


# ── Team-scoping WHERE fragments + ownership check ──────────────────────────
# A role whose data scope is 'team' (Settings -> Roles & Access; see
# utils.helpers.rbac.role_data_scope) sees only its own people: everyone
# attached to that person -- by Reporting manager (employees.manager_id) or
# Assigned HR (employees.assigned_hr_username) -- at any depth. So a Senior
# HR sees the Junior HRs under them and every employee under those Junior
# HRs; a Junior HR sees only their own. 'company' roles, the owner 'admin'
# and superadmin are never scoped. A role-holder's username is their own
# employee_id, so "who is this" is always session["admin_username"], never a
# value a caller picks.
#
# The hr_scope_* names are kept from when only the built-in 'hr' role was
# scoped (to its assigned employees); every call site already uses them, so
# they now carry the team scope for any 'team' role. The manager_scope_*
# trio that sat beside them is covered by the same team and is a no-op.
TEAM_SUBQUERY = (
    "WITH RECURSIVE _team(id) AS ("
    "SELECT employee_id FROM employees WHERE manager_id=%s OR assigned_hr_username=%s "
    "UNION SELECT e.employee_id FROM employees e JOIN _team t "
    "ON e.manager_id = t.id OR e.assigned_hr_username = t.id"
    ") SELECT id FROM _team"
)


def team_scope_active():
    """True if the admin-side session is limited to its own team. (A session
    with no admin role -- anonymous, or a plain employee -- never is.)"""
    role = session.get("admin_role")
    if not role or role in ("admin", "superadmin"):
        return False
    from .rbac import role_data_scope
    return role_data_scope(role) == "team"


def _team_params():
    me = session.get("admin_username")
    return (me, me)


def hr_scope_subquery(alias=""):
    """WHERE fragment + params scoping a table with an employee_id column
    (attendance, leave_requests, tickets, ...) to the session's team.
    Returns ("", ()) for a whole-company (or unauthenticated) session."""
    if not team_scope_active():
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN ({TEAM_SUBQUERY})", _team_params()  # nosec B608 -- fixed SQL fragments


def hr_scope_column(alias=""):
    """WHERE fragment + params scoping the employees table itself (alias =
    its alias in the query) to the session's team."""
    return hr_scope_subquery(alias)


def hr_scope_denied(emp_id):
    """True if the session is team-scoped and emp_id is NOT in its team --
    i.e. this request should be rejected. Always False for whole-company
    roles. Every single-record action route (approve a leave, resolve a
    ticket, correct an attendance row, ...) guards with this, so a
    team-scoped session can't act on someone else's record by editing an id.
    A role-holder acting on their own record is never denied by this alone.
    Re-queries on every call rather than trusting anything cached."""
    if not team_scope_active():
        return False
    if emp_id == session.get("admin_username"):
        return False
    with _db() as (cursor, _conn):
        cursor.execute(f"SELECT 1 WHERE %s IN ({TEAM_SUBQUERY})", (emp_id,) + _team_params())  # nosec B608
        return cursor.fetchone() is None


# Covered by the team scope above (a manager's direct and indirect reports
# are part of their team); kept so existing call sites stay unchanged.
def manager_scope_subquery(alias=""):
    return "", ()


def manager_scope_column(alias=""):
    return "", ()


def manager_scope_denied(emp_id):
    return False


# ── Pending-action header counters ──────────────────────────────────────────
# Was hand-repeated (15+ near-identical copies, some drifted to a narrower
# tickets filter) across admin_views.py/attendance.py/documents.py/
# employees.py/leave.py/payroll.py/performance.py/tickets.py/core.py — the
# small badge counts most admin pages show in their header/sidebar.
def get_pending_action_counts(cursor, active_cid=None, tickets_open_only=False):
    """Returns (pending_leaves, pending_resignations, pending_tickets).

    `active_cid` scopes all three counts to one company (via
    co_scope_subquery) when given, else counts across all companies.
    `tickets_open_only` narrows the ticket count to status='Open' instead of
    the default 'Open'+'In Progress' -- a handful of pages intentionally
    show the narrower count."""
    co_sub, co_args = co_scope_subquery(active_cid)
    cursor.execute(f"SELECT COUNT(*) FROM leave_requests WHERE status='Pending' {co_sub}", co_args)  # nosec B608
    pending_leaves = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM resignation_requests WHERE status='Pending' {co_sub}", co_args)  # nosec B608
    pending_resignations = cursor.fetchone()[0]
    ticket_filter = "status='Open'" if tickets_open_only else "status IN ('Open','In Progress')"
    cursor.execute(f"SELECT COUNT(*) FROM tickets WHERE {ticket_filter} {co_sub}", co_args)  # nosec B608
    pending_tickets = cursor.fetchone()[0]
    return pending_leaves, pending_resignations, pending_tickets
