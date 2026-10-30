# -*- coding: utf-8 -*-
"""Notifications blueprint -- in-app notification feeds.

Migrated from app.py (lines 13255–13354). Routes:
  GET  /api/notifications                    → api_get_notifications         (admin, Bearer)
  POST /api/notifications/mark_read          → api_mark_notifications_read   (admin, Bearer)
  POST /api/notifications/broadcast          → api_broadcast_notification    (admin, Bearer)
  POST /api/push_token                       → api_register_push_token       (admin, Bearer)
  GET  /api/employee/notifications           → api_employee_get_notifications (employee, Bearer)
  POST /api/employee/notifications/mark_read → api_employee_mark_notifications_read (employee, Bearer)
  POST /api/employee/push_token              → api_employee_register_push_token (employee, Bearer)
  POST /web/notifications/mark_read          → web_employee_mark_notifications_read (employee, session)
  GET  /web/notifications/list               → web_employee_notifications_list      (employee, session)
"""
from flask import Blueprint, jsonify, session, request, g as _g
from database import get_db_connection
from utils.auth import admin_required, any_user_required, api_required, api_role_required, employee_api_required, employee_required
from utils.helpers import post_announcement
from utils.push_utils import register_push_token

notifications_bp = Blueprint("notifications", __name__)


def _fmt(row):
    return {
        "id": row[0],
        "title": row[1],
        "message": row[2],
        "is_read": bool(row[3]),
        "created_at": row[4].strftime("%d %b %Y, %I:%M %p") if row[4] else "",
    }


# ── Admin notifications (Bearer-token API) ────────────────────────────────────

@notifications_bp.route("/api/notifications", methods=["GET"])
@api_required
def api_get_notifications():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, title, message, is_read, created_at FROM notifications "
        "WHERE recipient_type='admin' ORDER BY created_at DESC LIMIT 50"
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "notifications": [_fmt(r) for r in rows]})


@notifications_bp.route("/api/notifications/mark_read", methods=["POST"])
@api_required
def api_mark_notifications_read():
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("UPDATE notifications SET is_read=TRUE WHERE recipient_type='admin'")
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True})


@notifications_bp.route("/api/notifications/broadcast", methods=["POST"])
@api_required
@api_role_required("admin")
def api_broadcast_notification():
    """Bearer-token twin of blueprints/admin_views.py's announcements_admin()
    (session-only /announcements POST action=add) -- same two side effects
    (an `announcements` row so it shows up on the web Performance &
    Communications > Announcements tab, and one `notifications` row per
    recipient) so a broadcast sent from mobile is indistinguishable from
    one sent from web, not a second, lesser notification mechanism.
    audience='all' (default) fans out to every active employee (public);
    any other value is treated as a specific employee_id (private)."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    message = (data.get("message") or "").strip()
    audience = (data.get("audience") or "all").strip()
    if not title or not message:
        return jsonify({"ok": False, "msg": "Title and message are required."}), 400

    is_public = audience == "all"
    target_emp = None if is_public else audience

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    if not is_public:
        cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (target_emp,))
        if not cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": f"Unknown employee_id '{target_emp}'."}), 400

    post_announcement(cursor, db, title, message, "Normal", "public" if is_public else "private", target_emp)
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Announcement posted."})


@notifications_bp.route("/api/push_token", methods=["POST"])
@api_required
def api_register_push_token():
    """Registers this device's Expo push token for the current admin/hr/
    manager session (identified by _g.api_user, the same admin_users
    identity every other Bearer-token admin route uses) -- see
    utils/push_utils.py for delivery. Any admin-tier role may register
    (no api_role_required) since every one of them can receive
    notifications.recipient_type='admin' rows today."""
    data = request.get_json(silent=True) or {}
    token = (data.get("expo_push_token") or "").strip()
    platform = (data.get("platform") or "").strip() or None
    if not token:
        return jsonify({"ok": False, "msg": "expo_push_token is required."}), 400
    register_push_token("admin", _g.api_user, token, platform)
    return jsonify({"ok": True})


# ── Employee notifications (Bearer-token API) ─────────────────────────────────

@notifications_bp.route("/api/employee/notifications", methods=["GET"])
@employee_api_required
def api_employee_get_notifications():
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, title, message, is_read, created_at FROM notifications "
        "WHERE recipient_type='employee' AND employee_id=%s ORDER BY created_at DESC LIMIT 50",
        (emp_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "notifications": [_fmt(r) for r in rows]})


@notifications_bp.route("/api/employee/notifications/mark_read", methods=["POST"])
@employee_api_required
def api_employee_mark_notifications_read():
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE notifications SET is_read=TRUE WHERE recipient_type='employee' AND employee_id=%s",
        (emp_id,),
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True})


@notifications_bp.route("/api/employee/push_token", methods=["POST"])
@employee_api_required
def api_employee_register_push_token():
    """Employee twin of api_register_push_token() above."""
    data = request.get_json(silent=True) or {}
    token = (data.get("expo_push_token") or "").strip()
    platform = (data.get("platform") or "").strip() or None
    if not token:
        return jsonify({"ok": False, "msg": "expo_push_token is required."}), 400
    register_push_token("employee", _g.api_emp_id, token, platform)
    return jsonify({"ok": True})


# ── Employee notifications (session-based web) ────────────────────────────────

@notifications_bp.route("/web/notifications/mark_read", methods=["POST"])
@employee_required
def web_employee_mark_notifications_read():
    emp_id = session["employee_id"]
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE notifications SET is_read=TRUE WHERE recipient_type='employee' AND employee_id=%s",
        (emp_id,),
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True})


@notifications_bp.route("/web/notifications/list")
@employee_required
def web_employee_notifications_list():
    emp_id = session["employee_id"]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, title, message, is_read, created_at FROM notifications "
        "WHERE recipient_type='employee' AND employee_id=%s ORDER BY created_at DESC LIMIT 30",
        (emp_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "notifications": [_fmt(r) for r in rows]})


# ── Announcement ribbon (session; admin + employee dashboards) ────────────────

@notifications_bp.route("/web/ribbon", methods=["GET"])
@any_user_required
def web_announcement_ribbon():
    """Latest announcement (last 14 days) for the slim ribbon at the top of
    every dashboard. Admin sessions see public announcements; an employee
    also sees private ones addressed to them."""
    emp_id = None if session.get("admin_logged_in") else session.get("employee_id")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        cursor.execute("""
            SELECT id, title, content, priority, created_at FROM announcements
            WHERE (COALESCE(visibility,'public') = 'public'
                   OR (visibility = 'private' AND target_employee_id = %s))
              AND created_at >= NOW() - INTERVAL '14 days'
            ORDER BY created_at DESC LIMIT 1
        """, (emp_id,))
        row = cursor.fetchone()
    finally:
        cursor.close()
        db.close()
    if not row:
        return jsonify({"ok": True, "ribbon": None})
    text = (row[2] or "").strip().replace("\n", " ")
    return jsonify({"ok": True, "ribbon": {
        "id": row[0], "title": row[1], "text": text[:160],
        "priority": row[3] or "Normal",
    }})


# ── Admin notifications (session; the admin top bar's bell) ──────────────────

@notifications_bp.route("/web/admin/notifications/list")
@admin_required
def web_admin_notifications_list():
    """Same admin feed as GET /api/notifications, for the web session -- the
    bell in templates/admin_base.html's top bar."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        cursor.execute(
            "SELECT id, title, message, is_read, created_at FROM notifications "
            "WHERE recipient_type='admin' ORDER BY created_at DESC LIMIT 20"
        )
        rows = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE recipient_type='admin' AND NOT COALESCE(is_read, FALSE)")
        unread = cursor.fetchone()[0]
    finally:
        cursor.close()
        db.close()
    return jsonify({"ok": True, "unread": unread, "notifications": [_fmt(r) for r in rows]})


@notifications_bp.route("/web/admin/notifications/mark_read", methods=["POST"])
@admin_required
def web_admin_notifications_mark_read():
    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute("UPDATE notifications SET is_read=TRUE WHERE recipient_type='admin' AND NOT COALESCE(is_read, FALSE)")
        db.commit()
    finally:
        cursor.close()
        db.close()
    return jsonify({"ok": True})
