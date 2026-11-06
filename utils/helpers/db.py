# -*- coding: utf-8 -*-
"""helpers: DB context manager, audit log, in-app notifications"""
from contextlib import contextmanager
from database import get_db_connection
from extensions import app_log
from flask import request, session


# ── DB context manager ────────────────────────────────────────────────────────
@contextmanager
def _db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        yield cursor, conn
    finally:
        try:
            cursor.close()
        except Exception as _e:
            app_log.debug("cursor.close() failed: %s", _e)
        try:
            conn.close()
        except Exception as _e:
            app_log.debug("conn.close() failed: %s", _e)


# ── Audit logging ──────────────────────────────────────────────────────────────
def _audit(action, table=None, record_id=None, detail=None):
    try:
        actor = session.get("admin_username") or session.get("employee_id") or "system"
        actor_type = "admin" if session.get("admin_logged_in") else "employee"
        ip = request.remote_addr or ""
        db = get_db_connection()
        cursor = db.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_logs (actor, actor_type, action, target_table, target_id, detail, ip_address) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (actor, actor_type, action, table, str(record_id) if record_id is not None else None, detail, ip)
            )
            db.commit()
        finally:
            cursor.close()
            db.close()
    except Exception as exc:
        # A silently-lost audit_logs row undermines the whole point of an
        # audit trail -- worth a trace even though _audit() itself must
        # never raise and block the action it's recording.
        app_log.warning("_audit failed (action=%s, table=%s, record_id=%s): %s", action, table, record_id, exc, exc_info=True)


# ── Notification helper ───────────────────────────────────────────────────────
def _create_notification(recipient_type, title, message, employee_id=None, tenant_schema=None):
    """tenant_schema is only needed by a caller that has no flask.g.tenant_db
    of its own to rely on -- get_db_connection() falls back to the "public"
    schema when g.tenant_db is unset, which is exactly platform_admin.py's
    /super_admin/* routes (deliberately no resolved tenant there). Passing
    tenant_schema explicitly routes the INSERT at the right tenant's
    notifications table instead of silently landing in "public". Every
    existing call site leaves this None and is unaffected."""
    try:
        if tenant_schema is not None:
            from database import get_tenant_db
            db = get_tenant_db(tenant_schema)
        else:
            db = get_db_connection()
        cursor = db.cursor()
        try:
            cursor.execute(
                "INSERT INTO notifications (recipient_type, employee_id, title, message) VALUES (%s,%s,%s,%s)",
                (recipient_type, employee_id, title, message)
            )
            db.commit()
        finally:
            cursor.close()
            db.close()
    except Exception as exc:
        app_log.warning("_create_notification failed (recipient_type=%s, employee_id=%s): %s", recipient_type, employee_id, exc, exc_info=True)
        return
    # Mobile push -- best-effort, and only attempted once the in-app
    # notification row above actually landed. Handed to the same in-
    # process background writer utils/async_writer.py already uses for
    # high-frequency security bookkeeping (never Postgres-backed like
    # utils/email_utils.py's email_queue -- losing a push on a crash is an
    # acceptable tradeoff for a best-effort channel, the same call
    # async_writer.py's own docstring already makes for login-failure
    # counters) -- _send_expo_push()'s blocking urllib call (up to
    # _TIMEOUT_SECONDS) would otherwise run on the request thread handling
    # whatever leave/ticket/announcement action triggered this. Imported
    # lazily to avoid a circular import (utils/push_utils.py imports _db
    # from this module).
    from utils.async_writer import enqueue_write
    from utils.push_utils import send_push_notification, send_push_notification_broadcast
    if recipient_type == "employee" and employee_id:
        enqueue_write(send_push_notification, "employee", employee_id, title, message)
    elif recipient_type == "admin":
        enqueue_write(send_push_notification_broadcast, "admin", title, message)
