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


# ── HR-scoping WHERE fragments + ownership check ────────────────────────────
# Same shape as the company-scoping pair above, but for an HR-role admin
# session's assigned_hr_username instead of a selected company -- session-
# driven (no active_cid-style param needed) since "which HR is this" is
# always exactly session["admin_username"] when session["admin_role"] is
# HR_ROLE, never a value a caller picks. blueprints/employees.py's
# view_employees() had this exact pattern hand-inlined 3x before these
# existed; new call sites should use these instead of re-inlining again.
def hr_scope_subquery(alias=""):
    """WHERE fragment + params scoping to an HR session's assigned
    employees via a subquery, for tables that don't have their own
    assigned_hr_username column (attendance, leave_requests, tickets, ...).
    Returns ("", ()) for a non-HR (or unauthenticated) session -- 'admin'
    and every other admin-side role stay fully unscoped, exactly as
    before this existed."""
    from utils.auth import HR_ROLE
    if session.get("admin_role") != HR_ROLE:
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN (SELECT employee_id FROM employees WHERE assigned_hr_username=%s)", (session.get("admin_username"),)  # nosec B608


def hr_scope_column(alias=""):
    """WHERE fragment + params scoping by a direct assigned_hr_username
    column (e.g. the employees table itself)."""
    from utils.auth import HR_ROLE
    if session.get("admin_role") != HR_ROLE:
        return "", ()
    col = f"{alias}.assigned_hr_username" if alias else "assigned_hr_username"
    return f"AND {col}=%s", (session.get("admin_username"),)


def hr_scope_denied(emp_id):
    """True if the current session is HR-role and emp_id is NOT one of its
    assigned employees -- i.e. this request should be rejected. Always
    False for 'admin' (and any other non-HR admin-side role). Generalized
    from blueprints/employees.py's original _hr_scope_denied (that file now
    imports this instead of keeping its own copy) so every blueprint with a
    single-record action route (approve a leave, resolve a ticket, correct
    an attendance row, ...) can guard it the same way, rather than each
    silently allowing an HR session to act on any employee's record by URL/
    ID edit -- which is what every one of those routes did before this.

    Re-queries the DB on every call rather than trusting anything cached in
    the session -- an HR session reaching a DIFFERENT HR's employee by
    editing an id in the request must be caught against the current, real
    row every time."""
    from utils.auth import HR_ROLE
    if session.get("admin_role") != HR_ROLE:
        return False
    if emp_id == session.get("admin_username"):
        return False
    with _db() as (cursor, _conn):
        cursor.execute("SELECT assigned_hr_username FROM employees WHERE employee_id=%s", (emp_id,))
        row = cursor.fetchone()
    return not row or row[0] != session.get("admin_username")


# ── Manager-scoping WHERE fragments + ownership check ───────────────────────
# Same shape as the HR-scoping trio above, but for a manager-role session's
# direct reports instead of an HR's assigned employees. Reuses the existing
# employees.manager_id column (already populated for the org chart --
# blueprints/employees.py's add/edit forms, blueprints/admin_views.py's
# api_org_chart_data()) rather than adding a new link table: a manager's
# admin_users.username is always that same person's own employee_id (see
# blueprints/auth.py's _ensure_role_admin_account -- it's provisioned
# FROM an employee row, sharing that row's ID), so
# "employees reporting to this manager" is just employees.manager_id
# matching session["admin_username"], no new schema needed.
def manager_scope_subquery(alias=""):
    """WHERE fragment + params scoping to a manager session's direct
    reports via a subquery, for tables without their own manager_id
    column (leave_requests, resignation_requests, overtime). Returns
    ("", ()) for a non-manager (or unauthenticated) session."""
    from utils.auth import MANAGER_ROLE
    if session.get("admin_role") != MANAGER_ROLE:
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN (SELECT employee_id FROM employees WHERE manager_id=%s)", (session.get("admin_username"),)  # nosec B608


def manager_scope_column(alias=""):
    """WHERE fragment + params scoping by a direct manager_id column
    (the employees table itself)."""
    from utils.auth import MANAGER_ROLE
    if session.get("admin_role") != MANAGER_ROLE:
        return "", ()
    col = f"{alias}.manager_id" if alias else "manager_id"
    return f"AND {col}=%s", (session.get("admin_username"),)


def manager_scope_denied(emp_id):
    """True if the current session is manager-role and emp_id is NOT one
    of its direct reports -- mirrors hr_scope_denied's shape exactly,
    including the same self-access exception (a manager acting on their
    own employee_id, e.g. their own leave request appearing in a shared
    list, is never denied by this check alone)."""
    from utils.auth import MANAGER_ROLE
    if session.get("admin_role") != MANAGER_ROLE:
        return False
    if emp_id == session.get("admin_username"):
        return False
    with _db() as (cursor, _conn):
        cursor.execute("SELECT manager_id FROM employees WHERE employee_id=%s", (emp_id,))
        row = cursor.fetchone()
    return not row or row[0] != session.get("admin_username")


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
