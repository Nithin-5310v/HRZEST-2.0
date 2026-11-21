# -*- coding: utf-8 -*-
"""helpers: DB context manager, audit log, in-app notifications"""
import datetime
import json
from contextlib import contextmanager
from decimal import Decimal
from database import get_db_connection
from extensions import app_log
from flask import g, has_request_context, request, session


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
def _audit_actor():
    """(actor, actor_type, ip) for the current request.

    A Bearer-token API call has no session: utils/auth.py's guards put its
    identity on g (api_user for an admin token, api_emp_id for an employee
    one). Without reading those, every mobile-app action was recorded as
    "system", and the mass-export detection could not tell who did it.
    Outside a request (a scheduled job) the actor is "system"."""
    if not has_request_context():
        return "system", "system", ""
    api_admin = getattr(g, "api_user", None)
    api_emp = getattr(g, "api_emp_id", None)
    actor = session.get("admin_username") or session.get("employee_id") or api_admin or api_emp or "system"
    if session.get("admin_logged_in") or (api_admin and not session.get("employee_id")):
        actor_type = "admin"
    elif session.get("employee_id") or api_emp:
        actor_type = "employee"
    else:
        actor_type = "system"
    return actor, actor_type, request.remote_addr or ""


def _audit(action, table=None, record_id=None, detail=None):
    try:
        actor, actor_type, ip = _audit_actor()
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


def _audit_value(value):
    """One field's value in a comparable, JSON-safe form: dates as ISO text,
    numbers as numbers (so 12.5 from the form equals Decimal('12.50') from the
    database), blank text as None."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return int(number) if number.is_integer() else number
    text = str(value).strip()
    return text or None


def audit_changes(action, table, record_id, before, after, hidden=(), **facts):
    """Record what an edit changed, as JSON in audit_logs.detail:

        {"changes": {"department": {"from": "Ops", "to": "Finance"},
                     "address": {"changed": true}}}

    Compares every field in `after` with the same field in `before` (None or
    {} for a new record). Fields in `hidden` hold personal data that is
    encrypted at rest, or pay: the log says they changed but never stores
    their values, so audit_logs (plain text, readable by admins, kept in
    backups) never becomes an unencrypted copy of them. Pass decrypted values
    for those fields, or every save would look like a change. `facts` add
    context next to the changes (e.g. hike_pct=10), never the hidden values.

    Writes nothing when nothing changed. Returns the changes."""
    before = before or {}
    changes = {}
    for field, new_value in after.items():
        old, new = _audit_value(before.get(field)), _audit_value(new_value)
        if old == new:
            continue
        changes[field] = {"changed": True} if field in hidden else {"from": old, "to": new}
    if changes:
        _audit(action, table, record_id,
               json.dumps({"changes": changes, **facts}, default=str, ensure_ascii=False, sort_keys=True))
    return changes


def audit_read(action, table, record_id=None, **facts):
    """Record that someone viewed or exported other people's sensitive data:
    salary lists and reports, payslips, tax statements, employee documents,
    bulk exports. `facts` describe the access (period, row count, channel)
    and go into detail as JSON; never pass the data itself.

    Actions whose names contain "export" or "download" are what the
    mass_export detection counts (utils/detections.py)."""
    detail = json.dumps(facts, default=str, ensure_ascii=False, sort_keys=True) if facts else None
    _audit(action, table, record_id, detail)


def _audit_shown(value):
    if value is None or value == "":
        return "(empty)"
    return str(value)


def describe_audit_detail(detail):
    """audit_logs.detail as one readable line for the Activity Log. JSON
    written by audit_changes() / audit_read() is spelled out; any other
    detail (older rows, free-text callers) is returned as it is."""
    if not detail or not str(detail).lstrip().startswith("{"):
        return detail
    try:
        data = json.loads(detail)
    except ValueError:
        return detail
    if not isinstance(data, dict):
        return detail
    parts = []
    changes = data.get("changes")
    if isinstance(changes, dict):
        for field, change in changes.items():
            label = field.replace("_", " ")
            if not isinstance(change, dict):
                continue
            if change.get("changed"):
                parts.append(f"{label} changed (value not recorded)")
            else:
                parts.append(f"{label}: {_audit_shown(change.get('from'))} → {_audit_shown(change.get('to'))}")
    for key, value in data.items():
        if key != "changes":
            parts.append(f"{key.replace('_', ' ')}: {_audit_shown(value)}")
    return "; ".join(parts) or detail


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
