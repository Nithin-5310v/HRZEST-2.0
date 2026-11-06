# -*- coding: utf-8 -*-
"""platform_admin: mini-CRM: company profile, notes, ticket creation"""
from database import get_master_db, get_tenant_db
from extensions import app_log, log_security_event
from flask import flash, redirect, render_template, request, session
from utils.helpers import _safe_app_url, coerce_datetime
from utils.plan_limits import calculate_price, format_price_inr
from utils.ticket_constants import TICKET_PRIORITIES as _TICKET_PRIORITIES
from . import _platform_admin_required, platform_admin_bp
from .dashboard import _recent_payments, _tenant_employee_count


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
