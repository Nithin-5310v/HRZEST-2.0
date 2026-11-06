# -*- coding: utf-8 -*-
"""helpers: holiday/announcement fan-out, employee sidebar info, pending-approval counts"""
from database import get_db_connection
from extensions import app_log
from .db import _create_notification
from .scoping import get_pending_action_counts
from .settings import _CACHE_MISS, _cache_get, _cache_set, _redis_clear_prefix, _settings_lock


def notify_new_holiday(cursor, db, holiday_name, date):
    """In-app notification for a newly added holiday, sent to every active
    employee only -- the admin/HR who posted it don't get a bell entry. Uses the caller's cursor/connection, like
    post_announcement(). A "Leave:"-prefixed entry is a personal leave block,
    not a company holiday, so it is skipped."""
    name = (holiday_name or "").strip()
    if not name or name.startswith("Leave:"):
        return
    title = f"🎉 Holiday: {name}"
    message = f"{name} on {date}."
    cursor.execute("SELECT employee_id FROM employees WHERE is_active=1")
    emp_ids = [eid for (eid,) in cursor.fetchall()]
    if emp_ids:
        cursor.executemany(
            "INSERT INTO notifications (recipient_type, employee_id, title, message) "
            "VALUES ('employee', %s, %s, %s)",
            [(eid, title, message) for eid in emp_ids]
        )
    db.commit()


def post_announcement(cursor, db, title, content, priority, visibility, target_emp=None,
                      attachment_original_name=None, attachment_stored_ref=None):
    """Insert an `announcements` row and fan out the matching `notifications`
    row(s) -- shared by the web admin form (blueprints/admin_views.py's
    announcements_admin) and the Bearer-token API twin (blueprints/
    notifications.py's api_broadcast_notification), which previously each
    hand-rolled this identical insert-then-fan-out sequence. Uses the
    caller's own open cursor/connection so the public-audience fan-out stays
    one batched executemany() round-trip rather than _create_notification's
    one-connection-per-call pattern (deliberate -- see the perf note this
    replaced in announcements_admin).

    attachment_original_name/attachment_stored_ref (set by the caller after
    saving the upload via utils.storage.save_private) also drive an email
    to every recipient -- the in-app notification alone doesn't reach an
    employee who isn't currently logged in to check it."""
    cursor.execute(
        "INSERT INTO announcements (title, content, priority, visibility, target_employee_id, "
        "attachment_original_name, attachment_stored_ref) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (title, content, priority, visibility, target_emp, attachment_original_name, attachment_stored_ref)
    )
    db.commit()
    snippet = (content[:117] + "...") if len(content) > 120 else content
    if visibility == "private":
        _create_notification('employee', f"📢 {title}", snippet, target_emp)
        cursor.execute("SELECT email FROM employees WHERE employee_id=%s AND email IS NOT NULL AND email != ''",
                       (target_emp,))
        recipient_emails = [row[0] for row in cursor.fetchall()]
    else:
        cursor.execute("SELECT employee_id FROM employees WHERE is_active=1")
        emp_ids = [eid for (eid,) in cursor.fetchall()]
        if emp_ids:
            cursor.executemany(
                "INSERT INTO notifications (recipient_type, employee_id, title, message) "
                "VALUES ('employee', %s, %s, %s)",
                [(eid, f"📢 {title}", snippet) for eid in emp_ids]
            )
            db.commit()
            # This branch bypasses _create_notification() entirely (the
            # executemany() above is the whole point -- one round-trip for
            # the whole company instead of one per employee), so it needs
            # its own push dispatch too, via the bulk twin of
            # _create_notification's own enqueue_write() call below.
            from utils.async_writer import enqueue_write
            from utils.push_utils import send_push_notification_bulk
            enqueue_write(send_push_notification_bulk, "employee", emp_ids, f"📢 {title}", snippet)
        cursor.execute("SELECT email FROM employees WHERE is_active=1 AND email IS NOT NULL AND email != ''")
        recipient_emails = [row[0] for row in cursor.fetchall()]
    _email_announcement(title, content, priority, recipient_emails,
                        attachment_original_name, attachment_stored_ref)


def _email_announcement(title, content, priority, recipient_emails, attachment_name, attachment_ref):
    """Best-effort email fan-out for a newly posted announcement, queued
    through the same DB-backed email worker every other transactional email
    in this app uses (see utils/email_utils.py). Local imports avoid a
    circular import -- email_utils imports from this module already."""
    if not recipient_emails:
        return
    from utils.email_utils import get_email_config, send_email_async
    cfg = get_email_config()
    if not cfg:
        app_log.info("Announcement '%s' posted but SMTP isn't configured -- skipping email fan-out.", title)
        return
    attachment_bytes = None
    if attachment_ref:
        from utils.storage import open_private
        try:
            attachment_bytes = open_private(attachment_ref)
        except Exception as exc:
            app_log.warning("Could not read announcement attachment %s for email: %s", attachment_ref, exc)
    import html as _html_mod
    safe_title = _html_mod.escape(title)
    safe_content = _html_mod.escape(content).replace("\n", "<br>")
    attachment_note = (
        f'<div style="padding:0 20px 16px;color:#64748b;font-size:12px;">'
        f'📎 Attached: {_html_mod.escape(attachment_name)}</div>'
        if attachment_name else ""
    )
    html_body = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:600px;margin:auto;padding:20px;
         border:1px solid #e2e8f0;border-radius:12px;background:#ffffff;">
      <div style="background:#1e3a8a;padding:16px 20px;border-radius:8px 8px 0 0;color:#ffffff;">
        <h2 style="margin:0;font-size:18px;">📢 {safe_title}</h2>
      </div>
      <div style="padding:20px;color:#334155;font-size:14px;line-height:1.6;">
        {safe_content}
      </div>
      {attachment_note}
      <div style="border-top:1px solid #e2e8f0;padding:12px 20px;font-size:11px;color:#94a3b8;">
        Priority: {priority}. Sent via HRzest.com. Please do not reply directly to this automated email.
      </div>
    </div>
    """
    for email in recipient_emails:
        send_email_async(email, f"📢 {title}", html_body, cfg,
                         attachment_bytes=attachment_bytes, attachment_filename=attachment_name)


def get_employee_sidebar_info(cursor, emp_id):
    """(name, role, department, face_image) for the employee-portal sidebar
    -- byte-identical query previously duplicated in blueprints/performance.py
    (my_performance) and blueprints/leave.py (my_compoff)."""
    cursor.execute(
        "SELECT name, COALESCE(role,''), COALESCE(department,''), face_image FROM employees WHERE employee_id=%s",
        (emp_id,)
    )
    return cursor.fetchone()


_pending_counts_cache = {}
_PENDING_COUNTS_CACHE_TTL = 30  # shorter than company_settings' 60s -- these
# counts (leave/resignation/ticket approvals) change far more often, so a
# tighter window keeps sidebar badges from looking stale for too long.


def invalidate_pending_counts_cache():
    # Clears every tenant's entry -- see invalidate_settings_cache()'s
    # docstring for why (a caller without an active request context must
    # not silently invalidate the wrong key).
    with _settings_lock:
        _pending_counts_cache.clear()
    _redis_clear_prefix("settings_pending")


def get_pending_counts():
    """(pending_leaves, pending_resignations, pending_tickets) for sidebar
    badges -- the single-tenant/unscoped count, cached for
    _PENDING_COUNTS_CACHE_TTL seconds. This used to be copy-pasted as three
    separate sequential cursor.execute() calls across a dozen route
    handlers (admin dashboard, employees, documents, attendance, tickets,
    leave, payroll, performance...), each paying that cost freshly on every
    single page load -- collapsing it to one shared cache is what actually
    fixes "clicking between sidebar modules feels slow," since almost every
    module page was re-running the identical query trio on every click.

    NOT for routes that scope these counts by active_company_id (multi-
    tenant) -- those need a live per-request query via co_scope_subquery,
    not this shared unscoped cache."""
    # Through Redis when configured, so every worker shows the same badges
    # and an invalidation reaches all of them (QA SCALE-04).
    cached = _cache_get(_pending_counts_cache, "settings_pending")
    if cached is not _CACHE_MISS:
        return tuple(cached)
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        # get_pending_action_counts() (below) is the actual query logic --
        # this just adds a cache on top of its default (unscoped,
        # open+in-progress) case, which is what every uncached call site
        # this replaced was already computing.
        result = tuple(get_pending_action_counts(cursor))
        cursor.close()
        db.close()
        _cache_set(_pending_counts_cache, "settings_pending", list(result), _PENDING_COUNTS_CACHE_TTL)
        return result
    except Exception:
        return (0, 0, 0)
