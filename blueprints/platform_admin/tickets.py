# -*- coding: utf-8 -*-
"""platform_admin: support ticket queue, detail, updates, comments"""
from html import escape

from database import get_master_db
from extensions import log_security_event
from flask import flash, redirect, render_template, request, session
from utils import support_tickets
from utils.helpers import _create_notification
from utils.ticket_constants import (
    TICKET_PRIORITIES as _TICKET_PRIORITIES,
    TICKET_STATUSES as _TICKET_STATUSES,
)
from . import _platform_admin_required, _tenant_schema_for_id, platform_admin_bp

# What the company is told a status means, in the email and notification.
_STATUS_WORDS = {
    "open": "is open again",
    "in_progress": "is being worked on",
    "resolved": "has been resolved",
    "closed": "has been closed",
}


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


def _notify_company(tenant_id, title, message):
    """In-app notification for the company's admins. This route has no
    g.tenant_db of its own, so the schema is looked up from the tenant id."""
    schema_row = _tenant_schema_for_id(tenant_id)
    if schema_row:
        schema_name, _company_name = schema_row
        _create_notification("admin", title, message, tenant_schema=schema_name)


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
    cur.execute("SELECT ticket_id, COUNT(*) FROM tenant_ticket_attachments GROUP BY ticket_id")
    file_counts = dict(cur.fetchall())
    cur.close()
    conn.close()
    tickets = [
        {"id": r[0], "tenant_id": r[1], "company_name": r[2] or "(deleted company)", "subdomain": r[3] or "—",
         "subject": r[4], "status": r[5], "priority": r[6], "created_at": r[7], "updated_at": r[8],
         "file_count": file_counts.get(r[0], 0)}
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
        "SELECT author, comment, created_at, author_kind, id FROM tenant_ticket_comments "
        "WHERE ticket_id=%s ORDER BY created_at ASC",
        (ticket_id,)
    )
    comments = [{"author": r[0], "comment": r[1], "created_at": r[2], "author_kind": r[3], "id": r[4]}
                for r in cur.fetchall()]
    cur.close()
    conn.close()
    return render_template(
        "super_admin_ticket_detail.html", ticket=ticket, comments=comments,
        statuses=_TICKET_STATUSES, priorities=_TICKET_PRIORITIES,
        attachments=support_tickets.attachments_for(ticket_id),
        ticket_file_accept=support_tickets.ACCEPT_ATTR, ticket_max_files=support_tickets.MAX_FILES,
    )


@platform_admin_bp.route("/super_admin/tickets/<int:ticket_id>/files/<int:attachment_id>", methods=["GET"])
@_platform_admin_required
def platform_admin_ticket_file(ticket_id, attachment_id):
    resp = support_tickets.send_attachment(ticket_id, attachment_id, inline=request.args.get("view") == "1")
    return resp or ("Not found.", 404)


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
    cur.execute("SELECT status, tenant_id, subject FROM tenant_support_tickets WHERE id=%s", (ticket_id,))
    before = cur.fetchone()
    if not before:
        cur.close()
        conn.close()
        flash("Ticket not found.", "error")
        return redirect("/super_admin/tickets")
    previous_status, tenant_id, subject = before
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
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        "platform_admin.ticket_updated", f"Ticket #{ticket_id} set to status={status}, priority={priority}",
        level="INFO", identifier=session.get("platform_admin_username"),
    )

    if status != previous_status:
        words = _STATUS_WORDS.get(status, f"is now {status.replace('_', ' ')}")
        note = (request.form.get("note") or "").strip()[:2000]
        message = f"<p>Your support ticket <strong>{escape(subject)}</strong> {words}.</p>"
        if note:
            message += (f'<p style="white-space:pre-wrap;background:#f1f5f9;border-radius:8px;padding:12px;">'
                        f"{escape(note)}</p>")
            _add_comment(ticket_id, note)
        if status in ("resolved", "closed"):
            message += ("<p>If something still isn't right, reply on the ticket"
                        + (" or raise a new one" if status == "closed" else "") + ".</p>")
        _notify_company(tenant_id, f"Support ticket #{ticket_id} {words}", subject)
        emailed = support_tickets.email_raiser(ticket_id, f"Your ticket {words}", message)
        flash("Ticket updated. " + ("The person who raised it has been emailed." if emailed
                                    else "No email could be sent to the person who raised it."), "success")
    else:
        flash("Ticket updated.", "success")
    return redirect(f"/super_admin/tickets/{ticket_id}")


def _add_comment(ticket_id, comment):
    """Adds a platform-admin reply to the ticket's conversation; returns its id."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "INSERT INTO tenant_ticket_comments (ticket_id, author, comment, author_kind) "
        "VALUES (%s,%s,%s,'platform_admin') RETURNING id",
        (ticket_id, session.get("platform_admin_username"), comment),
    )
    comment_id = cur.fetchone()[0]
    cur.execute("UPDATE tenant_support_tickets SET updated_at=NOW() WHERE id=%s", (ticket_id,))
    conn.commit()
    cur.close()
    conn.close()
    return comment_id


@platform_admin_bp.route("/super_admin/tickets/<int:ticket_id>/comment", methods=["POST"])
@_platform_admin_required
def platform_admin_add_ticket_comment(ticket_id):
    """A reply to the company: shown in their ticket's conversation, sent as
    an in-app notification, and emailed to whoever raised the ticket."""
    comment = (request.form.get("comment") or "").strip()[:2000]
    files = support_tickets.chosen_files(request)
    if not comment and not files:
        flash("Comment cannot be empty.", "error")
        return redirect(f"/super_admin/tickets/{ticket_id}")
    file_error = support_tickets.check_files(files)
    if file_error:
        flash(file_error, "error")
        return redirect(f"/super_admin/tickets/{ticket_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT tenant_id, subject FROM tenant_support_tickets WHERE id=%s", (ticket_id,))
    ticket_row = cur.fetchone()
    cur.close()
    conn.close()
    if not ticket_row:
        flash("Ticket not found.", "error")
        return redirect("/super_admin/tickets")
    tenant_id, subject = ticket_row
    comment_id = _add_comment(ticket_id, comment or "Attached files.")
    saved = support_tickets.save_files(files, ticket_id, comment_id, session.get("platform_admin_username"),
                                       "platform_admin")

    _notify_company(tenant_id, f"Support ticket reply: {subject}", comment or "HRZEST support attached files.")
    message = (f'<p>HRZEST support replied to your ticket:</p>'
               f'<p style="white-space:pre-wrap;background:#f1f5f9;border-radius:8px;padding:12px;">'
               f"{escape(comment)}</p>" if comment else "<p>HRZEST support attached files to your ticket.</p>")
    if saved:
        message += f"<p>{saved} file{'s' if saved != 1 else ''} attached -- open the ticket to see them.</p>"
    support_tickets.email_raiser(ticket_id, "New reply", message)
    if saved < len(files):
        flash("Reply sent, but some files couldn't be attached.", "error")
    else:
        flash("Reply sent to the company.", "success")
    return redirect(f"/super_admin/tickets/{ticket_id}")
