# -*- coding: utf-8 -*-
"""platform_admin: support ticket queue, detail, updates, comments"""
from database import get_master_db
from extensions import log_security_event
from flask import flash, redirect, render_template, request, session
from utils.helpers import _create_notification
from utils.ticket_constants import (
    TICKET_PRIORITIES as _TICKET_PRIORITIES,
    TICKET_STATUSES as _TICKET_STATUSES,
)
from . import _platform_admin_required, _tenant_schema_for_id, platform_admin_bp


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
