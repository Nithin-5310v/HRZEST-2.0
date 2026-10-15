# -*- coding: utf-8 -*-
"""Tickets blueprint -- support ticket lifecycle."""
from flask import Blueprint, request, session, redirect, jsonify, flash
from database import get_db_connection
from utils.auth import admin_required, employee_required, api_required, employee_api_required, api_role_required
from utils.email_utils import get_email_config, send_email_async
from utils.helpers import tpath, _create_notification, hr_scope_denied, invalidate_pending_counts_cache

# Roles allowed to read or act on company-wide support tickets via the
# Bearer-token API below (/api/tickets, /api/tickets/<id>/action) -- the
# web /tickets and /ticket_action routes are only @admin_required with no
# stricter role split of their own, so this mirrors the same admin/hr
# split used elsewhere for HR-adjacent, company-wide data (see
# blueprints/leave.py's _LEAVE_APPROVER_ROLES, minus 'manager' -- there's
# no per-team ticket ownership/assignment concept in this codebase for a
# manager's involvement to scope down to).
_TICKET_ADMIN_ROLES = ("admin", "hr")

tickets_bp = Blueprint("tickets", __name__)


@tickets_bp.route("/raise_ticket", methods=["POST"])
@employee_required
def raise_ticket():
    emp_id = session["employee_id"]
    category = request.form.get("category", "").strip()
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    priority = request.form.get("priority", "Medium").strip()
    if not category or not subject or not description:
        return redirect(tpath("/employee_portal#tickets"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO tickets (employee_id, category, subject, description, priority) "
        "VALUES (%s,%s,%s,%s,%s)",
        (emp_id, category, subject, description, priority)
    )
    db.commit()
    invalidate_pending_counts_cache()   # refresh the sidebar badges now (QA UX-01)
    cursor.close()
    db.close()
    _create_notification('admin', "🎫 New Support Ticket",
                         f"{emp_id} raised a {priority.lower()}-priority {category} ticket: {subject}")
    return redirect(tpath("/employee_portal?ticket_sent=1#tickets"))


@tickets_bp.route("/tickets")
@admin_required
def tickets_view():
    """Ticket management now lives solely on the Leaves & Holidays page's
    Tickets tab (templates/leave_holidays.html) -- both pages used to
    render their own full copy of the same list, posting to the same
    /ticket_action/<tid> handler below, which meant two separately-
    maintained UIs for one feature. This route stays as a redirect (not
    deleted) so old bookmarks/links to /tickets keep working."""
    return redirect(tpath("/leave_holidays?tab=tickets"))


@tickets_bp.route("/ticket_action/<int:tid>", methods=["POST"])
@admin_required
def ticket_action(tid):
    new_status = request.form.get("status", "").strip()
    admin_response = request.form.get("admin_response", "").strip()
    allowed = ("Open", "In Progress", "Resolved", "Closed")
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if new_status not in allowed:
        return (jsonify({"ok": False, "msg": "Invalid status."}), 400) if is_ajax else redirect(tpath("/tickets"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    cursor.execute("""
        SELECT t.subject, t.category, t.priority, t.description,
               e.name, e.email, t.employee_id
        FROM tickets t
        JOIN employees e ON t.employee_id = e.employee_id
        WHERE t.id = %s
    """, (tid,))
    row = cursor.fetchone()
    if row and hr_scope_denied(row[6]):
        cursor.close()
        db.close()
        return (jsonify({"ok": False, "msg": "Ticket not found."}), 404) if is_ajax else ("Forbidden", 403)

    cursor.execute(
        "UPDATE tickets SET status=%s, admin_response=%s WHERE id=%s",
        (new_status, admin_response or None, tid)
    )
    db.commit()
    invalidate_pending_counts_cache()   # refresh the sidebar badges now (QA UX-01)
    cursor.close()
    db.close()

    if row:
        _create_notification(
            'employee', f"🎫 Ticket Update: {row[0]}",
            f"Your ticket status is now {new_status}." + (f" -- {admin_response}" if admin_response else ""),
            row[6]
        )

    msg = ""
    msg_type = "success"
    if row and admin_response:
        subject_text, category, priority, description, emp_name, emp_email, _emp_id = row
        if emp_email:
            _ecfg = get_email_config()
            if _ecfg:
                status_color = {"Resolved": "#16a34a", "Closed": "#64748b",
                                "In Progress": "#d97706"}.get(new_status, "#2563eb")
                _html = f"""
<div style="font-family:'Segoe UI',sans-serif;max-width:560px;margin:0 auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:linear-gradient(135deg,#1e3a8a,#2563eb);padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">🎫 Ticket Update</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">HRzest.com</div>
  </div>
  <div style="padding:28px;">
    <p style="font-size:15px;color:#1e293b;margin-bottom:20px;">Hi <strong>{emp_name}</strong>, your ticket has been updated.</p>
    <div style="background:#fff;border:1px solid #dbeafe;border-radius:12px;padding:18px 20px;margin-bottom:20px;">
      <div style="font-size:12px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">Ticket Subject</div>
      <div style="font-size:15px;color:#1e293b;font-weight:700;margin-bottom:14px;">{subject_text}</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;">
        <span style="background:#dbeafe;color:#1d4ed8;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;">{category}</span>
        <span style="background:#fef9c3;color:#92400e;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;">{priority} Priority</span>
        <span style="background:{status_color}22;color:{status_color};padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;">{new_status}</span>
      </div>
    </div>
    <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:12px;padding:18px 20px;margin-bottom:20px;">
      <div style="font-size:12px;color:#15803d;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Admin Response</div>
      <div style="font-size:14px;color:#1e293b;white-space:pre-line;">{admin_response}</div>
    </div>
    <p style="font-size:12px;color:#94a3b8;text-align:center;margin:0;">This is an automated message -- please do not reply.</p>
  </div>
</div>"""
                send_email_async(emp_email, f"Ticket Update: {subject_text}", _html, _ecfg)
                msg = f"✅ Ticket updated -- notification queued for {emp_email}"
            else:
                msg = "Ticket updated. SMTP not configured -- email not sent."
                msg_type = "warning"
        else:
            msg = "Ticket updated. Employee has no email on record."
            msg_type = "warning"
    else:
        msg = "✅ Ticket status updated."

    if is_ajax:
        return jsonify({"ok": True, "msg": msg, "type": msg_type, "new_status": new_status})
    flash(msg, msg_type)
    return redirect(tpath("/tickets"))


@tickets_bp.route("/api/employee/tickets", methods=["GET"])
@employee_api_required
def api_employee_tickets():
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT id, category, subject, description, priority, status, admin_response, created_at
        FROM tickets WHERE employee_id=%s ORDER BY created_at DESC LIMIT 30
    """, (emp_id,))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "tickets": [
        {"id": r[0], "category": r[1], "subject": r[2], "description": r[3],
         "priority": r[4], "status": r[5], "admin_response": r[6],
         "created_at": str(r[7])}
        for r in rows
    ]})


@tickets_bp.route("/api/employee/raise_ticket", methods=["POST"])
@employee_api_required
def api_employee_raise_ticket():
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json() or {}
    category = data.get("category", "").strip()
    subject = data.get("subject", "").strip()
    description = data.get("description", "").strip()
    priority = data.get("priority", "Medium").strip()
    if not category or not subject or not description:
        return jsonify({"ok": False, "msg": "category, subject and description required"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO tickets (employee_id, category, subject, description, priority) VALUES (%s,%s,%s,%s,%s)",
        (emp_id, category, subject, description, priority)
    )
    db.commit()
    invalidate_pending_counts_cache()   # refresh the sidebar badges now (QA UX-01)
    cursor.close()
    db.close()
    _create_notification('admin', "🎫 New Support Ticket",
                         f"{emp_id} raised a {priority.lower()}-priority {category} ticket: {subject}")
    return jsonify({"ok": True, "msg": "Ticket raised successfully."})


@tickets_bp.route("/api/tickets", methods=["GET"])
@api_required
@api_role_required(*_TICKET_ADMIN_ROLES)
def api_tickets():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT t.id, t.employee_id, e.name, t.category, t.subject, t.description,
               t.priority, t.status, t.admin_response, t.created_at, t.updated_at
        FROM tickets t
        JOIN employees e ON t.employee_id = e.employee_id
        ORDER BY CASE WHEN t.status='Open' THEN 0 WHEN t.status='In Progress' THEN 1 WHEN t.status='Resolved' THEN 2 WHEN t.status='Closed' THEN 3 ELSE 4 END,
                 CASE WHEN t.priority='High' THEN 0 WHEN t.priority='Medium' THEN 1 WHEN t.priority='Low' THEN 2 ELSE 3 END, t.created_at DESC
    """)
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "tickets": [
        {"id": r[0], "employee_id": r[1], "name": r[2], "category": r[3],
         "subject": r[4], "description": r[5], "priority": r[6],
         "status": r[7], "admin_response": r[8],
         "created_at": str(r[9]), "updated_at": str(r[10])}
        for r in rows
    ]})


@tickets_bp.route("/api/tickets/<int:tid>/action", methods=["POST"])
@api_required
@api_role_required(*_TICKET_ADMIN_ROLES)
def api_ticket_action(tid):
    data = request.get_json(silent=True) or {}
    new_status = data.get("status", "").strip()
    admin_response = data.get("admin_response", "").strip()
    allowed = ("Open", "In Progress", "Resolved", "Closed")
    if new_status not in allowed:
        return jsonify({"ok": False, "msg": f"status must be one of {allowed}"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT subject, employee_id FROM tickets WHERE id=%s", (tid,))
    row = cursor.fetchone()
    cursor.execute(
        "UPDATE tickets SET status=%s, admin_response=%s WHERE id=%s",
        (new_status, admin_response or None, tid)
    )
    db.commit()
    invalidate_pending_counts_cache()   # refresh the sidebar badges now (QA UX-01)
    cursor.close()
    db.close()
    if row:
        _create_notification(
            'employee', f"🎫 Ticket Update: {row[0]}",
            f"Your ticket status is now {new_status}." + (f" -- {admin_response}" if admin_response else ""),
            row[1]
        )
    return jsonify({"ok": True, "status": new_status})
