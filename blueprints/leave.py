# -*- coding: utf-8 -*-
"""Leave blueprint -- requests, types, holidays, resignation, overtime, comp-off.

Bandit B608 audit note: the nosec-marked queries below interpolate
`_co_sub`/`_co_join`, a company-scoping fragment that's always a hardcoded
literal chosen by a bool (`active_cid`) -- never user input. Actual values
are always %s-bound params.
"""
import datetime
import calendar
import html as _html
import psycopg2
from flask import (
    Blueprint, request, session, redirect, jsonify, render_template,
    flash, g,
)

from extensions import app_log
from database import get_db_connection
from utils.auth import (
    admin_required, employee_required, api_required, employee_api_required, api_role_required,
    role_required, MANAGER_ROLE, HR_ROLE,
)
from utils.helpers import (
    tpath, _audit, _create_notification, get_company_settings, co_scope_subquery, co_scope_column,
    hr_scope_subquery, hr_scope_column, hr_scope_denied, get_pending_counts, company_today,
    get_employee_sidebar_info, coerce_datetime,
    manager_scope_subquery, manager_scope_column, manager_scope_denied,
)
from utils.email_utils import send_email_async, get_email_config, get_admin_emails
from utils.leave_utils import get_indian_holidays
import utils.config as cfg

leave_bp = Blueprint("leave", __name__)

# Roles allowed to read or act on another employee's leave/resignation/
# overtime request. Previously these routes only checked @admin_required /
# @api_required (any logged-in admin-side session or valid admin API
# token, regardless of role) with no role restriction at all -- meaning a
# soc_analyst account, which every other sensitive admin route treats as
# lower-privilege (see utils/dlp.py's PII-clearance tier), could read and
# approve/reject every employee's leave, resignation, and overtime data
# company-wide. There is no per-team/manager-owns-employee scoping in this
# codebase today (admin_users carries a role, not a link to specific
# employees), so this is a role-tier gate, not team-scoped visibility --
# see the PR description for why that's the deliberate scope of this fix.
_LEAVE_APPROVER_ROLES = ("admin", "hr", "manager")


@leave_bp.route("/add_holiday", methods=["POST"])
@admin_required
def add_holiday():
    date = request.form["date"]
    year = date[:4]
    entry_type = request.form.get("type", "Holiday")
    holiday_name = request.form["holiday_name"].strip()
    if entry_type == "Leave":
        holiday_name = "Leave:" + holiday_name
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        cursor.execute("INSERT INTO holidays (date, name) VALUES (%s,%s)", (date, holiday_name))
        db.commit()
    except psycopg2.IntegrityError:
        pass  # duplicate date -- silently ignore
    cursor.close()
    db.close()
    return redirect(tpath(f"/leave_holidays?tab=holidays&year={year}"))


@leave_bp.route("/import_indian_holidays", methods=["POST"])
@admin_required
def import_indian_holidays():
    year = int(request.form.get("year", company_today().year))
    holidays_list = get_indian_holidays(year)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    for date_obj, name in holidays_list:
        try:
            cursor.execute(
                "INSERT INTO holidays (date, name) VALUES (%s, %s) ON CONFLICT (date) DO NOTHING",
                (date_obj, name)
            )
        except Exception as exc:
            app_log.warning("Import Indian holiday '%s' (%s) failed: %s", name, date_obj, exc, exc_info=True)
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath(f"/leave_holidays?tab=holidays&year={year}"))


@leave_bp.route("/delete_holiday/<int:hid>", methods=["POST"])
@admin_required
def delete_holiday(hid):
    year = request.form.get("year", company_today().year)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM holidays WHERE id=%s", (hid,))
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath(f"/leave_holidays?tab=holidays&year={year}"))


@leave_bp.route("/api/holidays/<int:hid>", methods=["DELETE"])
@api_required
@api_role_required("admin", "hr")
def api_delete_holiday(hid):
    """Bearer-token twin of delete_holiday() -- adding one already exists
    at POST /api/holidays (api_holidays() below), deleting one didn't."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM holidays WHERE id=%s", (hid,))
    deleted = cursor.rowcount
    db.commit()
    cursor.close()
    db.close()
    if not deleted:
        return jsonify({"ok": False, "msg": "Holiday not found."}), 404
    return jsonify({"ok": True, "msg": "Holiday removed."})


@leave_bp.route("/request_leave", methods=["POST"])
@employee_required
def request_leave():
    emp_id = session["employee_id"]
    emp_name = session["employee_name"]
    leave_start = request.form.get("leave_date_start", "").strip()
    leave_end = request.form.get("leave_date_end", "").strip() or leave_start
    reason = request.form.get("reason", "").strip()
    leave_type_id_raw = request.form.get("leave_type_id", "").strip()
    leave_type_id = int(leave_type_id_raw) if leave_type_id_raw.isdigit() else None
    is_half_day = 1 if request.form.get("is_half_day") else 0
    half_day_session = request.form.get("half_day_session", "Morning") if is_half_day else None
    if not reason or not leave_start:
        return redirect(tpath("/employee_portal"))

    start_dt = datetime.date.fromisoformat(leave_start)
    # Half-day is always a single date; ignore end date
    if is_half_day:
        end_dt = start_dt
    else:
        end_dt = datetime.date.fromisoformat(leave_end) if leave_end else start_dt
        if end_dt < start_dt:
            end_dt = start_dt

    num_days = (end_dt - start_dt).days + 1
    if is_half_day:
        date_label = f"{leave_start} (Half Day – {half_day_session})"
    else:
        date_label = (leave_start if num_days == 1
                      else f"{leave_start} – {leave_end} ({num_days} days)")

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cur = start_dt
    while cur <= end_dt:
        cursor.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, leave_type_id, is_half_day, half_day_session) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (emp_id, cur, reason, leave_type_id, is_half_day, half_day_session)
        )
        cur += datetime.timedelta(days=1)
    db.commit()
    cursor.close()
    db.close()

    config = get_email_config()
    if config:
        # reason is free text straight from the employee's own request form --
        # escape everything user-supplied before it lands in an HTML email an
        # admin will open, or it's a stored-XSS/content-injection vector.
        _safe_name = _html.escape(str(emp_name))
        _safe_eid = _html.escape(str(emp_id))
        _safe_period = _html.escape(str(date_label))
        _safe_reason = _html.escape(str(reason))
        html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fff;border-radius:12px;
     overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">
  <div style="background:linear-gradient(135deg,#667eea,#764ba2);padding:24px;color:white;text-align:center;">
    <h2 style="margin:0;font-size:20px;">Leave Request Received</h2>
    <p style="margin:4px 0 0;opacity:.85;font-size:13px;">HRzest.com</p>
  </div>
  <div style="padding:24px;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <tr style="background:#f8f9fc;"><td style="padding:10px 14px;color:#555;font-weight:600;width:130px;">Employee</td>
          <td style="padding:10px 14px;">{_safe_name}</td></tr>
      <tr><td style="padding:10px 14px;color:#555;font-weight:600;">Employee ID</td><td style="padding:10px 14px;">{_safe_eid}</td></tr>
      <tr style="background:#f8f9fc;"><td style="padding:10px 14px;color:#555;font-weight:600;">Leave Period</td>
          <td style="padding:10px 14px;">{_safe_period}</td></tr>
      <tr><td style="padding:10px 14px;color:#555;font-weight:600;">Reason</td><td style="padding:10px 14px;">{_safe_reason}</td></tr>
    </table>
    <p style="margin-top:20px;padding:12px 16px;background:#fef9c3;border-radius:8px;color:#854d0e;font-size:13px;">
      Please log in to the <strong>Admin Panel</strong> to approve or reject this leave request.
    </p>
  </div>
</div>"""
        try:
            admin_emails = get_admin_emails()
            for admin_email in admin_emails:
                send_email_async(
                    admin_email,
                    f"Leave Request -- {emp_name} ({date_label})",
                    html_body, config
                )
        except Exception as e:
            app_log.error("Leave request notification email failed: %s", e)

    return redirect(tpath("/employee_portal?leave_sent=1#apply-leave"))


@leave_bp.route("/leave_balance")
@admin_required
def leave_balance():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    year = int(request.args.get("year", company_today().year))

    co = get_company_settings()
    pending_leaves, pending_resignations, pending_tickets = get_pending_counts()

    cursor.execute("SELECT id, name, annual_quota FROM leave_types WHERE is_active=1 ORDER BY id")
    leave_types = cursor.fetchall()

    # Auto-assign balances for employees who don't have them yet -- one
    # set-based upsert for every (employee, active leave type) pair instead
    # of a Python loop calling assign_leave_balances_for_employee() per
    # employee, which re-ran "SELECT ... FROM leave_types" and one INSERT
    # per leave type on every single page load (500 employees x 5 leave
    # types was ~3,000 round-trips for a single GET). Same net effect --
    # same ON CONFLICT semantics (only refresh total_days while the balance
    # is still untouched) -- just expressed as one statement.
    cursor.execute("""
        INSERT INTO leave_balances (employee_id, leave_type_id, year, total_days, used_days)
        SELECT e.employee_id, lt.id, %s, lt.annual_quota, 0
        FROM employees e CROSS JOIN leave_types lt
        WHERE lt.is_active = 1
        ON CONFLICT (employee_id, leave_type_id, year) DO UPDATE SET
            total_days = CASE WHEN leave_balances.used_days = 0
                              THEN EXCLUDED.total_days ELSE leave_balances.total_days END
    """, (year,))
    db.commit()

    # Fetch all balances
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT e.employee_id, e.name, e.department,
               lt.id, lt.name, lb.total_days, lb.used_days
        FROM employees e
        JOIN leave_types lt ON lt.is_active=1
        LEFT JOIN leave_balances lb ON lb.employee_id=e.employee_id
            AND lb.leave_type_id=lt.id AND lb.year=%s
        WHERE 1=1 {_hr}
        ORDER BY e.name, lt.id
    """, (year,) + _hr_args)  # nosec B608
    rows = cursor.fetchall()

    # Group by employee
    from collections import OrderedDict
    emp_balances = OrderedDict()
    for emp_id, emp_name, dept, lt_id, lt_name, total, used in rows:
        if emp_id not in emp_balances:
            emp_balances[emp_id] = {'name': emp_name, 'dept': dept or '--', 'leaves': []}
        used = float(used or 0)
        total = int(total or 0)
        remaining = max(0, total - used)
        emp_balances[emp_id]['leaves'].append({
            'lt_id': lt_id, 'lt_name': lt_name,
            'total': total, 'used': used, 'remaining': remaining
        })

    cursor.close()
    db.close()
    return render_template("leave_balance.html",
                           co=co, year=year,
                           leave_types=leave_types,
                           emp_balances=emp_balances,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           shift_start="09:00 AM", shift_end="06:00 PM",
                           active_nav="leaves",
                           )


@leave_bp.route("/set_leave_balance", methods=["POST"])
@admin_required
def set_leave_balance():
    emp_id = request.form.get("employee_id")
    lt_id = int(request.form.get("leave_type_id"))
    total = int(request.form.get("total_days", 0))
    year = int(request.form.get("year", company_today().year))
    if hr_scope_denied(emp_id):
        flash("Employee not found.", "error")
        return redirect(tpath(f"/leave_balance?year={year}"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        INSERT INTO leave_balances (employee_id, leave_type_id, year, total_days, used_days)
        VALUES (%s, %s, %s, %s, 0)
        ON CONFLICT (employee_id, leave_type_id, year) DO UPDATE SET total_days=%s
    """, (emp_id, lt_id, year, total, total))
    db.commit()
    cursor.close()
    db.close()
    flash("Leave balance updated successfully.", "success")
    return redirect(tpath(f"/leave_balance?year={year}"))


@leave_bp.route("/leave_requests")
def leave_requests_redirect():
    return redirect(tpath("/leave_holidays?tab=leaves"))


@leave_bp.route("/resignation_requests")
def resignation_requests_redirect():
    # Resignations were consolidated into leave_holidays.html's own
    # "Resignations" tab (same page that already serves /api/resignation_requests'
    # data), same retirement pattern as leave_requests_redirect() above and
    # /tickets (blueprints/tickets.py) -- kept as a redirect (not deleted)
    # so old bookmarks/links still resolve, rather than duplicating a
    # second standalone page for data that already has a home.
    return redirect(tpath("/leave_holidays?tab=resignations"))


@leave_bp.route("/leave_holidays")
@admin_required
def leave_holidays():
    tab = request.args.get("tab", "leaves")
    year = int(request.args.get("year", company_today().year))
    today = company_today()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    # Leaves data
    active_cid = session.get("active_company_id")
    _co_join, _co_args = co_scope_column(active_cid, alias="e")
    _co_sub, _ = co_scope_subquery(active_cid)
    _hr_join, _hr_args = hr_scope_column(alias="e")
    _hr_sub, _ = hr_scope_subquery()
    _mgr_join, _mgr_args = manager_scope_column(alias="e")
    _mgr_sub, _ = manager_scope_subquery()
    _is_manager = session.get("admin_role") == MANAGER_ROLE

    cursor.execute(f"""
        SELECT lr.id, e.name, lr.employee_id, lr.leave_date, lr.reason, lr.status, lr.created_at,
               COALESCE(lt.name, 'Leave Request') AS leave_type_name,
               COALESCE(lr.is_half_day, 0) AS is_half_day,
               lr.half_day_session
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.employee_id {_co_join} {_hr_join} {_mgr_join}
        LEFT JOIN leave_types lt ON lr.leave_type_id = lt.id
        ORDER BY CASE WHEN lr.status='Pending' THEN 0 WHEN lr.status='Approved' THEN 1 WHEN lr.status='Rejected' THEN 2 ELSE 3 END, lr.created_at DESC
    """, _co_args + _hr_args + _mgr_args)  # nosec B608
    leaves = cursor.fetchall()
    cursor.execute(f"""
        SELECT employee_id, SUM(CASE WHEN COALESCE(is_half_day,0)=1 THEN 0.5 ELSE 1 END)
        FROM leave_requests WHERE EXTRACT(YEAR FROM leave_date)=EXTRACT(YEAR FROM CURRENT_DATE) AND status='Approved'
        {_co_sub} {_hr_sub} {_mgr_sub} GROUP BY employee_id
    """, _co_args + _hr_args + _mgr_args)  # nosec B608
    leave_used = {row[0]: float(row[1]) for row in cursor.fetchall()}
    cursor.execute("SELECT id, name, annual_quota FROM leave_types WHERE is_active=1 ORDER BY id")
    leave_types_list = cursor.fetchall()
    # Tickets have no per-team ownership concept (see _LEAVE_APPROVER_ROLES'
    # own docstring above and blueprints/tickets.py's _TICKET_ADMIN_ROLES,
    # which excludes 'manager' entirely) -- a manager session gets none of
    # this tab's data at all, not just company/HR-scoped data.
    if _is_manager:
        all_tickets = []
        pending_tickets = 0
    else:
        cursor.execute(f"""
            SELECT t.id, t.employee_id, e.name, t.category, t.subject, t.description,
                   t.priority, t.status, t.admin_response, t.created_at, t.updated_at
            FROM tickets t JOIN employees e ON t.employee_id = e.employee_id {_co_join} {_hr_join}
            ORDER BY CASE WHEN t.status='Open' THEN 0 WHEN t.status='In Progress' THEN 1
                          WHEN t.status='Resolved' THEN 2 WHEN t.status='Closed' THEN 3 ELSE 4 END, t.created_at DESC
        """, _co_args + _hr_args)  # nosec B608
        all_tickets = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) FROM tickets WHERE status='Open' {_co_sub} {_hr_sub}", _co_args + _hr_args)  # nosec B608
        pending_tickets = cursor.fetchone()[0]
    cursor.execute(f"""
        SELECT rr.id, e.name, rr.employee_id, rr.last_working_day, rr.reason, rr.status, rr.created_at
        FROM resignation_requests rr JOIN employees e ON rr.employee_id = e.employee_id {_co_join} {_hr_join} {_mgr_join}
        ORDER BY CASE WHEN rr.status='Pending' THEN 0 WHEN rr.status='Accepted' THEN 1 WHEN rr.status='Declined' THEN 2 ELSE 3 END, rr.created_at DESC
    """, _co_args + _hr_args + _mgr_args)  # nosec B608
    resignations = cursor.fetchall()
    cursor.execute(f"SELECT COUNT(*) FROM leave_requests WHERE status='Pending' {_co_sub} {_hr_sub} {_mgr_sub}", _co_args + _hr_args + _mgr_args)  # nosec B608
    pending_leaves = cursor.fetchone()[0]
    cursor.execute(
        f"SELECT COUNT(*) FROM resignation_requests WHERE status='Pending' {_co_sub} {_hr_sub} {_mgr_sub}",  # nosec B608
        _co_args + _hr_args + _mgr_args)
    pending_resignations = cursor.fetchone()[0]

    # Holidays data
    cursor.execute("SELECT * FROM holidays ORDER BY date")
    holidays_data = cursor.fetchall()
    holiday_map = {}
    for row in holidays_data:
        date_val = row[1]
        if isinstance(date_val, datetime.date):
            holiday_map[date_val] = (row[0], row[2])
    sun_cal = calendar.Calendar(firstweekday=6)
    cal_data = []
    for month in range(1, 13):
        month_holidays = {}
        for date_obj, (hid, hname) in holiday_map.items():
            if date_obj.year == year and date_obj.month == month:
                month_holidays[date_obj.day] = (hid, hname)
        cal_data.append({'month_num': month, 'month_name': calendar.month_name[month],
                         'weeks': sun_cal.monthdayscalendar(year, month), 'holidays': month_holidays})

    co = get_company_settings()

    # Announcements (admin sees all) -- same shared table/route as
    # blueprints/performance.py's Announcements tab (blueprints/
    # admin_views.py's announcements_admin() handles create/delete for both).
    cursor.execute("""
        SELECT a.id, a.title, a.content, a.priority, a.created_at,
               COALESCE(a.visibility,'public'), COALESCE(a.target_employee_id,''), COALESCE(e.name,''),
               a.attachment_original_name
        FROM announcements a
        LEFT JOIN employees e ON e.employee_id = a.target_employee_id
        ORDER BY a.created_at DESC
    """)
    ann_list = [r[:4] + (coerce_datetime(r[4]),) + r[5:] for r in cursor.fetchall()]
    pub_anns = [r for r in ann_list if r[5] == 'public']
    priv_anns = [r for r in ann_list if r[5] == 'private']

    cursor.execute("SELECT employee_id, name FROM employees WHERE is_active=1 ORDER BY name")
    ann_emp_list = cursor.fetchall()

    cursor.close()
    db.close()
    return render_template("leave_holidays.html",
                           co=co, tab=tab,
                           leaves=leaves, leave_used=leave_used, leave_types_list=leave_types_list,
                           all_tickets=all_tickets, resignations=resignations,
                           pending_leaves=pending_leaves, pending_tickets=pending_tickets,
                           pending_resignations=pending_resignations,
                           holidays=holidays_data, cal_data=cal_data, year=year, today=today,
                           ann_list=ann_list, pub_anns=pub_anns, priv_anns=priv_anns,
                           ann_emp_list=ann_emp_list,
                           active_nav="leaves", is_manager=_is_manager,
                           )


def _decide_leave(cursor, db, lid, action):
    """Approve or reject one Pending leave request -- the single path for the
    web, bulk and mobile approvals (QA BUG-31/32/33, BUG-36). The request
    leaves Pending in the same statement that checks it, so approving twice
    can't deduct the balance twice, and a decided request can't be flipped.
    On approval the day is marked 'Approved Leave' (or 'Half Day') and the
    leave balance -- and comp-off minutes for a Comp-off leave -- deducted.
    Returns (employee_id, leave_date) or None if the request isn't Pending.
    The caller commits."""
    cursor.execute(
        "UPDATE leave_requests SET status=%s WHERE id=%s AND status='Pending' "
        "RETURNING employee_id, leave_date, COALESCE(is_half_day, 0), leave_type_id",
        (action, lid),
    )
    row = cursor.fetchone()
    if not row:
        return None
    emp_id, leave_date, is_half, leave_type_id = row
    if action != "Approved":
        return emp_id, leave_date
    att_type = 'Half Day' if is_half else 'Approved Leave'
    cursor.execute("""
        INSERT INTO attendance (employee_id, date, attendance_type)
        VALUES (%s, %s, %s)
        ON CONFLICT (employee_id, date) DO UPDATE SET attendance_type=%s
    """, (emp_id, leave_date, att_type, att_type))
    deduction = 0.5 if is_half else 1
    if leave_type_id:
        year = leave_date.year if hasattr(leave_date, 'year') else company_today().year
        cursor.execute("""
            INSERT INTO leave_balances (employee_id, leave_type_id, year, total_days, used_days)
            VALUES (%s, %s, %s,
                (SELECT annual_quota FROM leave_types WHERE id=%s),
                %s)
            ON CONFLICT (employee_id, leave_type_id, year) DO UPDATE SET
                used_days = leave_balances.used_days + %s
        """, (emp_id, leave_type_id, year, leave_type_id, deduction, deduction))
        cursor.execute("SELECT name FROM leave_types WHERE id=%s", (leave_type_id,))
        lt_name_row = cursor.fetchone()
        if lt_name_row and lt_name_row[0] == 'Comp-off':
            cfg_cur = db.cursor(buffered=True)
            cfg_cur.execute("SELECT COALESCE(compoff_minutes_per_day,480) FROM company_settings LIMIT 1")
            mpd_row = cfg_cur.fetchone()
            cfg_cur.close()
            mpd = int(mpd_row[0]) if mpd_row else 480
            deduct_minutes = int(deduction * mpd)
            cursor.execute("""
                INSERT INTO compoff_balance (employee_id, earned_minutes, used_minutes)
                VALUES (%s, 0, %s)
                ON CONFLICT (employee_id) DO UPDATE SET
                    used_minutes = compoff_balance.used_minutes + %s
            """, (emp_id, deduct_minutes, deduct_minutes))
    return emp_id, leave_date


@leave_bp.route("/leave_action/<int:lid>", methods=["POST"])
@role_required(*_LEAVE_APPROVER_ROLES)
def leave_action(lid):
    action = request.form.get("action", "")
    if action not in ("Approved", "Rejected"):
        return redirect(tpath("/leave_holidays?tab=leaves"))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    # Fetch leave + employee details before updating
    cursor.execute("""
        SELECT lr.employee_id, lr.leave_date, lr.reason,
               e.name, e.email, COALESCE(lr.is_half_day, 0), COALESCE(e.email_alerts_enabled, 1)
        FROM leave_requests lr
        JOIN employees e ON e.employee_id = lr.employee_id
        WHERE lr.id = %s
    """, (lid,))
    leave_row = cursor.fetchone()
    if leave_row and (hr_scope_denied(leave_row[0]) or manager_scope_denied(leave_row[0])
                      or _approving_own_record(leave_row[0])):
        cursor.close()
        db.close()
        return "Forbidden", 403

    decided = _decide_leave(cursor, db, lid, action) if leave_row else None
    if not decided:
        cursor.close()
        db.close()
        flash("This leave request was already processed." if leave_row else "Leave request not found.", "warning")
        return redirect(tpath("/leave_holidays?tab=leaves"))

    db.commit()
    cursor.close()
    db.close()
    if leave_row:
        _audit(f"leave_{action.lower()}", "leave_requests", lid,
               f"Employee {leave_row[0]} leave on {leave_row[1]} -- {action}")

    # Send email + in-app notification to employee
    if leave_row:
        emp_id, leave_date, reason, emp_name, emp_email, _, email_alerts_enabled = leave_row
        icon = "✅" if action == "Approved" else "❌"
        _create_notification(
            'employee',
            f"{icon} Leave Request {action}",
            f"Your leave request for {leave_date} has been {action.lower()}.",
            emp_id
        )
        if not email_alerts_enabled:
            flash(f"Leave {action} -- email alerts are disabled for {emp_name}, only in-app notification sent.", "success")
        elif not emp_email:
            flash(f"Leave {action} but no email on record for {emp_name} -- notification not sent.", "warning")
        else:
            cfg_row = get_email_config()
            if not cfg_row:
                flash("Leave updated but SMTP not configured -- email not sent.", "warning")
            else:
                color = "#16a34a" if action == "Approved" else "#dc2626"
                icon = "✅" if action == "Approved" else "❌"
                date_str = leave_date.strftime('%d %b %Y') if hasattr(leave_date, 'strftime') else str(leave_date)
                _safe_name = _html.escape(str(emp_name))
                _safe_reason = _html.escape(str(reason)) if reason else '--'
                html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fff;border-radius:12px;
     overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">
  <div style="background:linear-gradient(135deg,{color},{color}cc);padding:24px;color:white;text-align:center;">
    <h2 style="margin:0;font-size:22px;">{icon} Leave {action}</h2>
    <p style="margin:4px 0 0;opacity:.85;font-size:13px;">HRzest.com</p>
  </div>
  <div style="padding:28px 32px;">
    <p style="font-size:15px;color:#1e293b;">Hi <strong>{_safe_name}</strong>,</p>
    <p style="font-size:14px;color:#475569;margin-top:10px;">
      Your leave request for <strong>{date_str}</strong> has been
      <strong style="color:{color};">{action.lower()}</strong>.
    </p>
    <div style="background:#f8fafc;border-left:4px solid {color};border-radius:8px;padding:14px 18px;margin:20px 0;">
      <p style="margin:0;font-size:13px;color:#64748b;">📅 <strong>Date:</strong> {date_str}</p>
      <p style="margin:6px 0 0;font-size:13px;color:#64748b;">📝 <strong>Reason:</strong> {_safe_reason}</p>
      <p style="margin:6px 0 0;font-size:13px;color:#64748b;">📌 <strong>Status:</strong> <span style="color:{color};font-weight:700;">{action}</span></p>
    </div>
    <p style="font-size:13px;color:#94a3b8;margin-top:20px;">For queries, contact your HR administrator.</p>
  </div>
  <div style="background:#f1f5f9;padding:14px;text-align:center;font-size:11px;color:#94a3b8;">
    HRzest.com &bull; Automated Notification
  </div>
</div>"""
                send_email_async(emp_email, f"Leave {action} -- {date_str}", html_body, cfg_row)
                flash(f"{icon} Leave {action} -- notification queued for {emp_email}", "success")

    return redirect(tpath("/leave_holidays?tab=leaves"))


@leave_bp.route("/leave_calendar")
@admin_required
def leave_calendar():
    import calendar as cal_mod
    from collections import defaultdict
    today = company_today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    if month < 1:
        month = 12
        year -= 1
    if month > 12:
        month = 1
        year += 1

    _, last_day = cal_mod.monthrange(year, month)
    start_date = datetime.date(year, month, 1)
    end_date = datetime.date(year, month, last_day)

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column(alias="e")
    _mgr, _mgr_args = manager_scope_column(alias="e")
    cursor.execute(f"""
        SELECT lr.leave_date, e.name, lr.employee_id,
               COALESCE(lr.is_half_day,0),
               COALESCE(lt.name,'Leave') AS leave_type,
               lr.half_day_session
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.employee_id
        LEFT JOIN leave_types lt ON lr.leave_type_id = lt.id
        WHERE lr.status = 'Approved'
          AND lr.leave_date BETWEEN %s AND %s {_hr} {_mgr}
        ORDER BY lr.leave_date, e.name
    """, (start_date, end_date) + _hr_args + _mgr_args)  # nosec B608
    cal_data = defaultdict(list)
    for ld, name, eid, half, ltype, sess in cursor.fetchall():
        day = ld.day if hasattr(ld, 'day') else int(str(ld)[8:10])
        cal_data[day].append({"name": name, "emp_id": eid,
                              "is_half": bool(half), "leave_type": ltype,
                              "session": sess or "Morning"})

    pending_leaves, pending_resignations, pending_tickets = get_pending_counts()
    cursor.close()
    db.close()

    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1

    return render_template("leave_calendar.html",
                           cal_weeks=cal_mod.monthcalendar(year, month),
                           active_nav="leaves",
                           cal_data=dict(cal_data),
                           year=year, month=month,
                           month_name=cal_mod.month_name[month],
                           today=today,
                           prev_m=prev_m, prev_y=prev_y,
                           next_m=next_m, next_y=next_y,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           )


@leave_bp.route("/request_resignation", methods=["POST"])
@employee_required
def request_resignation():
    emp_id = session["employee_id"]
    emp_name = session["employee_name"]
    last_working_day = request.form.get("last_working_day", "").strip()
    reason = request.form.get("resign_reason", "").strip()
    if not reason or not last_working_day:
        return redirect(tpath("/employee_portal#resign"))

    try:
        lwd = datetime.datetime.strptime(last_working_day, "%Y-%m-%d").date()
    except ValueError:
        return redirect(tpath("/employee_portal#resign"))

    min_lwd = company_today() + datetime.timedelta(days=30)
    if lwd < min_lwd:
        return redirect(tpath("/employee_portal#resign"))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s)",
        (emp_id, last_working_day, reason)
    )
    db.commit()
    cursor.close()
    db.close()

    config = get_email_config()
    if config:
        _safe_name = _html.escape(str(emp_name))
        _safe_eid = _html.escape(str(emp_id))
        _safe_lwd = _html.escape(str(last_working_day))
        _safe_reason = _html.escape(str(reason))
        html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fff;border-radius:12px;
     overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">
  <div style="background:linear-gradient(135deg,#ef4444,#b91c1c);padding:24px;color:white;text-align:center;">
    <h2 style="margin:0;font-size:20px;">⚠️ Resignation Notice Received</h2>
    <p style="margin:4px 0 0;opacity:.85;font-size:13px;">HRzest.com</p>
  </div>
  <div style="padding:24px;">
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      <tr style="background:#f8f9fc;"><td style="padding:10px 14px;color:#555;font-weight:600;width:160px;">Employee</td>
          <td style="padding:10px 14px;">{_safe_name}</td></tr>
      <tr><td style="padding:10px 14px;color:#555;font-weight:600;">Employee ID</td><td style="padding:10px 14px;">{_safe_eid}</td></tr>
      <tr style="background:#f8f9fc;"><td style="padding:10px 14px;color:#555;font-weight:600;">Last Working Day</td>
          <td style="padding:10px 14px;">{_safe_lwd}</td></tr>
      <tr><td style="padding:10px 14px;color:#555;font-weight:600;">Reason</td><td style="padding:10px 14px;">{_safe_reason}</td></tr>
    </table>
    <p style="margin-top:20px;padding:12px 16px;background:#fee2e2;border-radius:8px;color:#991b1b;font-size:13px;">
      Please log in to the <strong>Admin Panel → Resignations</strong> to accept or decline this resignation request.
    </p>
  </div>
</div>"""
        for admin_email in get_admin_emails():
            send_email_async(
                admin_email,
                f"Resignation Notice -- {emp_name} (Last day: {last_working_day})",
                html_body, config
            )

    return redirect(tpath("/employee_portal?resigned=1#resign"))


@leave_bp.route("/resignation_action/<int:rid>", methods=["POST"])
@role_required(*_LEAVE_APPROVER_ROLES)
def resignation_action(rid):
    action = request.form.get("action", "")
    if action not in ("Accepted", "Declined"):
        return redirect(tpath("/leave_holidays?tab=resignations"))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT rr.employee_id, rr.last_working_day, rr.reason,
               e.name, e.email, COALESCE(e.email_alerts_enabled, 1)
        FROM resignation_requests rr
        JOIN employees e ON e.employee_id = rr.employee_id
        WHERE rr.id = %s
    """, (rid,))
    resign_row = cursor.fetchone()
    if resign_row and (hr_scope_denied(resign_row[0]) or manager_scope_denied(resign_row[0])
                       or _approving_own_record(resign_row[0])):
        cursor.close()
        db.close()
        return "Forbidden", 403
    cursor.execute("UPDATE resignation_requests SET status=%s WHERE id=%s", (action, rid))
    db.commit()
    cursor.close()
    db.close()
    if resign_row:
        _audit(f"resignation_{action.lower()}", "resignation_requests", rid,
               f"Employee {resign_row[0]} resignation {action}")

    if resign_row:
        emp_id, lwd, reason, emp_name, emp_email, email_alerts_enabled = resign_row
        icon = "✅" if action == "Accepted" else "❌"
        _create_notification(
            'employee',
            f"{icon} Resignation {action}",
            f"Your resignation request has been {action.lower()}.",
            emp_id
        )
        if emp_email and email_alerts_enabled:
            cfg_row = get_email_config()
            if cfg_row:
                color = "#16a34a" if action == "Accepted" else "#dc2626"
                icon = "✅" if action == "Accepted" else "❌"
                lwd_str = lwd.strftime('%d %b %Y') if hasattr(lwd, 'strftime') else str(lwd)
                _safe_name = _html.escape(str(emp_name))
                _safe_reason = _html.escape(str(reason)) if reason else '--'
                html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fff;border-radius:12px;
     overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">
  <div style="background:linear-gradient(135deg,{color},{color}cc);padding:24px;color:white;text-align:center;">
    <h2 style="margin:0;font-size:22px;">{icon} Resignation {action}</h2>
    <p style="margin:4px 0 0;opacity:.85;font-size:13px;">HRzest.com</p>
  </div>
  <div style="padding:28px 32px;">
    <p style="font-size:15px;color:#1e293b;">Hi <strong>{_safe_name}</strong>,</p>
    <p style="font-size:14px;color:#475569;margin-top:10px;">
      Your resignation request has been <strong style="color:{color};">{action.lower()}</strong>.
    </p>
    <div style="background:#f8fafc;border-left:4px solid {color};border-radius:8px;padding:14px 18px;margin:20px 0;">
      <p style="margin:0;font-size:13px;color:#64748b;">📅 <strong>Last Working Day:</strong> {lwd_str}</p>
      <p style="margin:6px 0 0;font-size:13px;color:#64748b;">📝 <strong>Reason:</strong> {_safe_reason}</p>
      <p style="margin:6px 0 0;font-size:13px;color:#64748b;">📌 <strong>Status:</strong> <span style="color:{color};font-weight:700;">{action}</span></p>
    </div>
    <p style="font-size:13px;color:#94a3b8;margin-top:20px;">For queries, contact your HR administrator.</p>
  </div>
  <div style="background:#f1f5f9;padding:14px;text-align:center;font-size:11px;color:#94a3b8;">
    HRzest.com &bull; Automated Notification
  </div>
</div>"""
                send_email_async(emp_email, f"Resignation {action} -- {emp_name}", html_body, cfg_row)

    return redirect(tpath("/leave_holidays?tab=resignations"))


@leave_bp.route("/bulk_leave_action", methods=["POST"])
@role_required(*_LEAVE_APPROVER_ROLES)
def bulk_leave_action():
    action = request.form.get("action", "")
    raw_ids = request.form.getlist("leave_ids")
    if action not in ("Approved", "Rejected") or not raw_ids:
        return redirect(tpath("/leave_holidays?tab=leaves"))
    try:
        ids = [int(i) for i in raw_ids]
    except ValueError:
        return redirect(tpath("/leave_holidays?tab=leaves"))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    done = 0
    cfg_row = get_email_config()

    for lid in ids:
        cursor.execute("""
            SELECT lr.employee_id, lr.leave_date, lr.reason, e.name, e.email, COALESCE(e.email_alerts_enabled, 1)
            FROM leave_requests lr
            JOIN employees e ON e.employee_id = lr.employee_id
            WHERE lr.id = %s AND lr.status = 'Pending'
        """, (lid,))
        row = cursor.fetchone()
        if not row:
            continue
        emp_id, leave_date, reason, emp_name, emp_email, email_alerts_enabled = row
        if hr_scope_denied(emp_id) or manager_scope_denied(emp_id):
            continue
        cursor.execute("UPDATE leave_requests SET status=%s WHERE id=%s", (action, lid))
        if action == "Approved":
            cursor.execute("""
                INSERT INTO attendance (employee_id, date, attendance_type)
                VALUES (%s, %s, 'Approved Leave')
                ON CONFLICT (employee_id, date) DO UPDATE SET attendance_type='Approved Leave'
            """, (emp_id, leave_date))
        done += 1
        if emp_email and cfg_row and email_alerts_enabled:
            color = "#16a34a" if action == "Approved" else "#dc2626"
            icon = "✅" if action == "Approved" else "❌"
            date_str = leave_date.strftime('%d %b %Y') if hasattr(leave_date, 'strftime') else str(leave_date)
            _safe_name = _html.escape(str(emp_name))
            html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;">
  <div style="background:{color};padding:20px;color:white;text-align:center;">
    <h2 style="margin:0;">{icon} Leave {action}</h2>
  </div>
  <div style="padding:24px;">
    <p>Hi <strong>{_safe_name}</strong>, your leave request for <strong>{date_str}</strong> has been
    <strong style="color:{color};">{action.lower()}</strong>.</p>
    <p style="font-size:12px;color:#94a3b8;margin-top:16px;">HRzest.com &bull; Automated Notification</p>
  </div>
</div>"""
            send_email_async(emp_email, f"Leave {action} -- {date_str}", html_body, cfg_row)

    db.commit()
    cursor.close()
    db.close()
    flash(f"Bulk action: {action} applied to {done} leave request(s).", "success")
    return redirect(tpath("/leave_holidays?tab=leaves"))


@leave_bp.route("/api/holidays", methods=["GET", "POST"])
@api_required
def api_holidays():
    if request.method == "POST":
        data = request.get_json() or {}
        date = data.get("date")
        name = data.get("name")
        if not date or not name:
            return jsonify({"ok": False, "msg": "date and name required"}), 400
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        try:
            cursor.execute("INSERT INTO holidays (date, name) VALUES (%s,%s)", (date, name))
            db.commit()
        except Exception:
            app_log.error("API holiday insert failed", exc_info=True)
            db.rollback()
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Failed to add holiday. Check for duplicate dates."}), 400
        cursor.close()
        db.close()
        return jsonify({"ok": True})

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT id, date, name FROM holidays ORDER BY date")
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "holidays": [{"id": r[0], "date": str(r[1]), "name": r[2]} for r in rows]})


# Bearer-token twins of utils.helpers.manager_scope_subquery/_denied --
# api_role_required's identity lives in g.api_user (an admin_users
# username set by @api_required), not session, so the session-based
# helpers would silently no-op on every mobile request. Scoped to just
# manager (not also HR) since these API routes have no HR-scoping of
# their own either -- adding manager-only scoping here keeps mobile
# manager consistent with the web manager scope just added to
# leave_holidays()/leave_action()/etc, without changing existing HR
# mobile behavior, which is a separate, pre-existing gap.
def _api_manager_scope_subquery(alias=""):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT role FROM admin_users WHERE username=%s", (g.api_user,))
    role_row = cursor.fetchone()
    cursor.close()
    db.close()
    if not role_row or role_row[0] != MANAGER_ROLE:
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN (SELECT employee_id FROM employees WHERE manager_id=%s)", (g.api_user,)  # nosec B608


def _api_manager_scope_denied(emp_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT role FROM admin_users WHERE username=%s", (g.api_user,))
    role_row = cursor.fetchone()
    if not role_row or role_row[0] != MANAGER_ROLE:
        cursor.close()
        db.close()
        return False
    if emp_id == g.api_user:
        cursor.close()
        db.close()
        return False
    cursor.execute("SELECT manager_id FROM employees WHERE employee_id=%s", (emp_id,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    return not row or row[0] != g.api_user


def _approving_own_record(emp_id):
    """Block the self-approval loophole hr_scope_denied()/manager_scope_denied()
    leave open by design: both intentionally return False (not denied) when
    emp_id is the acting session's own employee_id, so an HR/manager session
    can still see its own request in a shared list. That exemption must never
    extend to actually approving/rejecting it -- only the action routes
    (leave/resignation/overtime, web) need this extra check on top of the
    existing scope check. Not applied to 'admin' -- that role isn't
    auto-provisioned from (and 1:1 tied to) an employee record the way hr/
    manager are, so admin_username matching an employee_id isn't the same
    self-approval scenario."""
    return (session.get("admin_role") in (HR_ROLE, MANAGER_ROLE)
            and emp_id == session.get("admin_username"))


def _api_approving_own_record(emp_id):
    """Bearer-token twin of _approving_own_record() -- see its docstring."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT role FROM admin_users WHERE username=%s", (g.api_user,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    return bool(row) and row[0] in (HR_ROLE, MANAGER_ROLE) and emp_id == g.api_user


@leave_bp.route("/api/leave_requests", methods=["GET"])
@api_required
@api_role_required(*_LEAVE_APPROVER_ROLES)
def api_leave_requests():
    _mgr_join, _mgr_args = _api_manager_scope_subquery(alias="lr")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(f"""
        SELECT lr.id, lr.employee_id, e.name, lr.leave_date, lr.reason, lr.status, lr.created_at AS requested_at
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.employee_id
        WHERE 1=1 {_mgr_join}
        ORDER BY lr.created_at DESC
    """, _mgr_args)  # nosec B608
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "leaves": [
        {"id": r[0], "employee_id": r[1], "name": r[2],
         "leave_date": str(r[3]) if r[3] else None,
         "reason": r[4], "status": r[5],
         "requested_at": str(r[6]) if r[6] else None}
        for r in rows
    ]})


@leave_bp.route("/api/leave_requests/<int:lid>/action", methods=["POST"])
@api_required
@api_role_required(*_LEAVE_APPROVER_ROLES)
def api_leave_action(lid):
    data = request.get_json(silent=True) or {}
    raw_action = data.get("action", "").strip().lower()
    if raw_action in ("approved", "approve"):
        action = "Approved"
    elif raw_action in ("declined", "decline", "rejected", "reject"):
        action = "Declined"
    else:
        return jsonify({"ok": False, "msg": "action must be Approved or Declined"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id, leave_date FROM leave_requests WHERE id=%s", (lid,))
    row = cursor.fetchone()
    if row and (_api_manager_scope_denied(row[0]) or _api_approving_own_record(row[0])):
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Leave request not found."}), 404
    # Same decision path as the web approval (QA BUG-32): only from Pending,
    # and approving deducts the balance and marks the day.
    decided = _decide_leave(cursor, db, lid, action) if row else None
    db.commit()
    cursor.close()
    db.close()
    if not row:
        return jsonify({"ok": False, "msg": "Leave request not found."}), 404
    if not decided:
        return jsonify({"ok": False, "msg": "This leave request was already processed."}), 409
    icon = "✅" if action == "Approved" else "❌"
    _create_notification(
        'employee',
        f"{icon} Leave Request {action}",
        f"Your leave request for {row[1]} has been {action.lower()}.",
        row[0]
    )
    return jsonify({"ok": True, "status": action})


@leave_bp.route("/api/resignation_requests", methods=["GET"])
@api_required
@api_role_required(*_LEAVE_APPROVER_ROLES)
def api_resignation_requests():
    _mgr_join, _mgr_args = _api_manager_scope_subquery(alias="rr")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(f"""
        SELECT rr.id, rr.employee_id, e.name, rr.last_working_day, rr.reason, rr.status, rr.created_at AS requested_at
        FROM resignation_requests rr
        JOIN employees e ON rr.employee_id = e.employee_id
        WHERE 1=1 {_mgr_join}
        ORDER BY rr.created_at DESC
    """, _mgr_args)  # nosec B608
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "resignations": [
        {"id": r[0], "employee_id": r[1], "name": r[2],
         "last_working_day": str(r[3]) if r[3] else None,
         "reason": r[4], "status": r[5],
         "requested_at": str(r[6]) if r[6] else None}
        for r in rows
    ]})


@leave_bp.route("/api/resignation_requests/<int:rid>/action", methods=["POST"])
@api_required
@api_role_required(*_LEAVE_APPROVER_ROLES)
def api_resignation_action(rid):
    data = request.get_json(silent=True) or {}
    action = data.get("action", "").strip()
    if action not in ("Accepted", "Declined"):
        return jsonify({"ok": False, "msg": "action must be Accepted or Declined"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT rr.employee_id, rr.last_working_day, rr.reason,
               e.name, e.email, COALESCE(e.email_alerts_enabled, 1)
        FROM resignation_requests rr
        JOIN employees e ON e.employee_id = rr.employee_id
        WHERE rr.id = %s
    """, (rid,))
    resign_row = cursor.fetchone()
    if resign_row and (_api_manager_scope_denied(resign_row[0]) or _api_approving_own_record(resign_row[0])):
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Resignation request not found."}), 404
    cursor.execute("UPDATE resignation_requests SET status=%s WHERE id=%s", (action, rid))
    db.commit()
    cursor.close()
    db.close()
    if not resign_row:
        return jsonify({"ok": True, "status": action})

    emp_id, lwd, reason, emp_name, emp_email, email_alerts_enabled = resign_row
    _audit(f"resignation_{action.lower()}", "resignation_requests", rid,
           f"Employee {emp_id} resignation {action}")
    icon = "✅" if action == "Accepted" else "❌"
    _create_notification(
        'employee',
        f"{icon} Resignation {action}",
        f"Your resignation request has been {action.lower()}.",
        emp_id
    )
    if emp_email and email_alerts_enabled:
        cfg_row = get_email_config()
        if cfg_row:
            color = "#16a34a" if action == "Accepted" else "#dc2626"
            lwd_str = lwd.strftime('%d %b %Y') if hasattr(lwd, 'strftime') else str(lwd)
            _safe_name = _html.escape(str(emp_name))
            _safe_reason = _html.escape(str(reason)) if reason else '--'
            html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fff;border-radius:12px;
     overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.1);">
  <div style="background:linear-gradient(135deg,{color},{color}cc);padding:24px;color:white;text-align:center;">
    <h2 style="margin:0;font-size:22px;">{icon} Resignation {action}</h2>
    <p style="margin:4px 0 0;opacity:.85;font-size:13px;">HRzest.com</p>
  </div>
  <div style="padding:28px 32px;">
    <p style="font-size:15px;color:#1e293b;">Hi <strong>{_safe_name}</strong>,</p>
    <p style="font-size:14px;color:#475569;margin-top:10px;">
      Your resignation request has been <strong style="color:{color};">{action.lower()}</strong>.
    </p>
    <div style="background:#f8fafc;border-left:4px solid {color};border-radius:8px;padding:14px 18px;margin:20px 0;">
      <p style="margin:0;font-size:13px;color:#64748b;">📅 <strong>Last Working Day:</strong> {lwd_str}</p>
      <p style="margin:6px 0 0;font-size:13px;color:#64748b;">📝 <strong>Reason:</strong> {_safe_reason}</p>
      <p style="margin:6px 0 0;font-size:13px;color:#64748b;">📌 <strong>Status:</strong> <span style="color:{color};font-weight:700;">{action}</span></p>
    </div>
    <p style="font-size:13px;color:#94a3b8;margin-top:20px;">For queries, contact your HR administrator.</p>
  </div>
  <div style="background:#f1f5f9;padding:14px;text-align:center;font-size:11px;color:#94a3b8;">
    HRzest.com &bull; Automated Notification
  </div>
</div>"""
            send_email_async(emp_email, f"Resignation {action} -- {emp_name}", html_body, cfg_row)

    return jsonify({"ok": True, "status": action})


@leave_bp.route("/api/employee/leave_request", methods=["POST"])
@employee_api_required
def api_employee_leave_request():
    emp_id = g.api_emp_id
    data = request.get_json() or {}
    leave_date = data.get("leave_date", "").strip()
    reason = data.get("reason", "").strip()
    if not leave_date or not reason:
        return jsonify({"ok": False, "msg": "leave_date and reason required"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s)",
        (emp_id, leave_date, reason)
    )
    db.commit()
    cursor.close()
    db.close()
    _create_notification(
        'admin',
        "📋 New Leave Request",
        f"Employee {emp_id} has submitted a leave request for {leave_date}. Reason: {reason}"
    )
    return jsonify({"ok": True, "msg": "Leave request submitted."})


@leave_bp.route("/api/employee/resign", methods=["POST"])
@employee_api_required
def api_employee_resign():
    emp_id = g.api_emp_id
    data = request.get_json() or {}
    last_working_day = data.get("last_working_day", "").strip()
    reason = data.get("reason", "").strip()
    if not last_working_day or not reason:
        return jsonify({"ok": False, "msg": "last_working_day and reason required"}), 400
    try:
        lwd = datetime.datetime.strptime(last_working_day, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid date format. Use YYYY-MM-DD"}), 400
    min_lwd = company_today() + datetime.timedelta(days=30)
    if lwd < min_lwd:
        return jsonify({"ok": False, "msg": "Last working day must be at least 30 days from today"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (emp_id,))
    emp = cursor.fetchone()
    emp_name = _html.escape(emp[0] if emp else emp_id)
    cursor.execute(
        "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s)",
        (emp_id, last_working_day, reason)
    )
    db.commit()
    _create_notification(
        'admin',
        "📤 New Resignation Request",
        f"Employee {emp_name} ({emp_id}) has submitted a resignation. Last working day: {last_working_day}."
    )
    config = get_email_config()
    if config:
        _safe_eid = _html.escape(str(emp_id))
        _safe_lwd = _html.escape(str(last_working_day))
        _safe_reason = _html.escape(str(reason))
        html_body = (
            f'<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fff;'
            f'border-radius:12px;overflow:hidden;">'
            f'<div style="background:linear-gradient(135deg,#ef4444,#b91c1c);padding:24px;color:white;text-align:center;">'
            f'<h2 style="margin:0;">⚠️ Resignation Notice Received</h2></div>'
            f'<div style="padding:24px;"><table style="width:100%;border-collapse:collapse;font-size:14px;">'
            f'<tr><td style="padding:10px;color:#555;font-weight:600;">Employee</td><td style="padding:10px;">{emp_name}</td></tr>'
            f'<tr><td style="padding:10px;color:#555;font-weight:600;">ID</td><td style="padding:10px;">{_safe_eid}</td></tr>'
            f'<tr><td style="padding:10px;color:#555;font-weight:600;">Last Working Day</td><td style="padding:10px;">{_safe_lwd}</td></tr>'
            f'<tr><td style="padding:10px;color:#555;font-weight:600;">Reason</td><td style="padding:10px;">{_safe_reason}</td></tr>'
            f'</table></div></div>'
        )
        for admin_email in get_admin_emails():
            send_email_async(
                admin_email,
                f"Resignation Notice -- {emp_name} (Last day: {last_working_day})",
                html_body, config
            )
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Resignation submitted successfully."})


@leave_bp.route("/api/employee/leaves", methods=["GET"])
@employee_api_required
def api_employee_leaves():
    emp_id = g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT id, leave_date, reason, status, created_at
        FROM leave_requests WHERE employee_id=%s
        ORDER BY created_at DESC LIMIT 50
    """, (emp_id,))
    leaves = cursor.fetchall()
    approved = sum(1 for r in leaves if r[3] == "Approved")
    pending = sum(1 for r in leaves if r[3] == "Pending")
    rejected = sum(1 for r in leaves if r[3] == "Rejected")
    cursor.close()
    db.close()
    return jsonify({
        "ok": True,
        "summary": {"approved": approved, "pending": pending, "rejected": rejected, "total": len(leaves)},
        "leaves": [
            {"id": r[0], "leave_date": str(r[1]), "reason": r[2], "status": r[3], "created_at": str(r[4])}
            for r in leaves
        ],
    })


@leave_bp.route("/api/employee/cancel_leave/<int:lid>", methods=["POST"])
@employee_api_required
def api_employee_cancel_leave(lid):
    emp_id = g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    # Ownership enforced in SQL itself (id + employee_id), not just in Python --
    # a mismatched id/employee_id pair simply doesn't match any row, so this
    # can't be bypassed by refactoring the check out from under the query.
    cursor.execute("SELECT status, leave_date FROM leave_requests WHERE id=%s AND employee_id=%s", (lid, emp_id))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Leave request not found."}), 404
    if row[0] != "Pending":
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": f"Cannot cancel a leave that is already {row[0]}."}), 400
    if row[1] <= company_today():
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Cannot cancel a leave for today or a past date."}), 400
    cursor.execute(
        "UPDATE leave_requests SET status='Cancelled', cancelled_at=NOW() WHERE id=%s",
        (lid,)
    )
    db.commit()
    cursor.close()
    db.close()
    _audit("cancel_leave", "leave_requests", str(lid), f"Employee {emp_id} cancelled leave for {row[1]}")
    return jsonify({"ok": True, "msg": "Leave request cancelled."})


@leave_bp.route("/cancel_leave/<int:lid>", methods=["POST"])
@employee_required
def cancel_leave_web(lid):
    emp_id = session["employee_id"]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT status, leave_date FROM leave_requests WHERE id=%s AND employee_id=%s", (lid, emp_id))
    row = cursor.fetchone()
    if not row:
        flash("Leave request not found.", "error")
    elif row[0] != "Pending":
        flash(f"Cannot cancel a leave that is already {row[0]}.", "error")
    elif row[1] <= company_today():
        flash("Cannot cancel a leave for today or a past date.", "error")
    else:
        cursor.execute(
            "UPDATE leave_requests SET status='Cancelled', cancelled_at=NOW() WHERE id=%s", (lid,)
        )
        db.commit()
        flash("Leave request cancelled successfully.", "success")
        _audit("cancel_leave", "leave_requests", str(lid), f"Employee {emp_id} cancelled leave for {row[1]}")
    cursor.close()
    db.close()
    return redirect(tpath("/employee_portal?tab=leave#leave-history"))


@leave_bp.route("/api/employee/request_overtime", methods=["POST"])
@employee_api_required
def api_employee_request_overtime():
    emp_id = g.api_emp_id
    data = request.get_json() or {}
    ot_date = data.get("date", str(company_today()))
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"ok": False, "msg": "Reason is required."}), 400
    try:
        ot_date = datetime.date.fromisoformat(ot_date)
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid date."}), 400
    if ot_date < company_today():
        return jsonify({"ok": False, "msg": "Cannot request OT for a past date."}), 400

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT id FROM overtime_records WHERE employee_id=%s AND date=%s", (emp_id, ot_date))
    if cursor.fetchone():
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "An overtime record already exists for that date."}), 400

    cursor.execute("SELECT s.end_time FROM shifts s JOIN employees e ON e.shift_id=s.id WHERE e.employee_id=%s", (emp_id,))
    shift_row = cursor.fetchone()
    shift_end = shift_row[0] if shift_row else cfg.shift_rules().end

    cursor.execute("""
        INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay,
                                      status, requested_by_employee, employee_reason)
        VALUES (%s, %s, %s, %s, 0, 0, 'Pending', 1, %s) RETURNING id
    """, (emp_id, ot_date, shift_end, shift_end, reason))
    oid = cursor.fetchone()[0]
    db.commit()
    cursor.close()
    db.close()
    _audit("request_overtime", "overtime_records", emp_id, f"Employee requested OT for {ot_date}: {reason}")
    _create_notification("admin", "Overtime Request",
                         f"Employee {emp_id} has requested overtime on {ot_date}.")
    return jsonify({"ok": True, "msg": "Overtime request submitted.", "id": oid})


@leave_bp.route("/api/employee/my_overtime", methods=["GET"])
@employee_api_required
def api_employee_my_overtime():
    emp_id = g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT id, date, ot_minutes, ot_pay, status, notes, requested_by_employee, employee_reason
        FROM overtime_records WHERE employee_id=%s ORDER BY date DESC LIMIT 30
    """, (emp_id,))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({
        "ok": True,
        "records": [
            {"id": r[0], "date": str(r[1]), "ot_minutes": r[2], "ot_pay": float(r[3]),
             "status": r[4], "notes": r[5], "self_requested": bool(r[6]), "reason": r[7]}
            for r in rows
        ]
    })


@leave_bp.route("/api/employee/holidays", methods=["GET"])
@employee_api_required
def api_employee_holidays():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT date, name FROM holidays ORDER BY date")
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    today = company_today()
    return jsonify({
        "ok": True,
        "holidays": [
            {"date": str(r[0]), "name": r[1], "passed": r[0] < today}
            for r in rows
        ],
    })


@leave_bp.route("/api/overtime", methods=["GET"])
@api_required
def api_overtime():
    """API endpoint for fetching real overtime records from database.

    Was querying a nonexistent `overtime_requests` table with columns
    (`ot_date`, `hours`, `reason`) that don't exist on the real table
    either -- every call unconditionally raised. The real table is
    `overtime_records` (app.py's init_db, columns: date, shift_end,
    actual_logout, ot_minutes, ot_pay, status, notes), the same one
    overtime_action() below and the session-only /overtime page
    (blueprints/leave.py's overtime()) both use. Fixed to match."""
    _mgr_join, _mgr_args = _api_manager_scope_subquery(alias="o")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(f"""
        SELECT o.id, o.employee_id, e.name, e.department, o.date, o.shift_end,
               o.actual_logout, o.ot_minutes, o.ot_pay, o.status, o.notes, o.created_at
        FROM overtime_records o
        LEFT JOIN employees e ON o.employee_id = e.employee_id
        WHERE 1=1 {_mgr_join}
        ORDER BY o.created_at DESC
        LIMIT 200
    """, _mgr_args)  # nosec B608
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    records = []
    for r in rows:
        records.append({
            "id": r[0],
            "employee_id": r[1],
            "name": r[2] or r[1],
            "department": r[3] or "General",
            "date": str(r[4]) if r[4] else "",
            "shift_end": str(r[5]) if r[5] else "",
            "actual_logout": str(r[6]) if r[6] else "",
            "ot_minutes": r[7] or 0,
            "hours": round((r[7] or 0) / 60, 2),
            "ot_pay": float(r[8]) if r[8] else 0.0,
            "status": r[9] or "Pending",
            "notes": r[10] or "",
            "created_at": str(r[11]) if r[11] else "",
        })
    return jsonify({"ok": True, "overtime": records})


@leave_bp.route("/api/overtime/<int:oid>/action", methods=["POST"])
@api_required
@api_role_required(*_LEAVE_APPROVER_ROLES)
def api_overtime_action(oid):
    """Bearer-token twin of overtime_action() below -- same two effects
    (set the record's status, and credit compoff_balance when approving
    for the first time) so an OT request approved from mobile is
    indistinguishable from one approved on web, not a second, lesser
    approval path."""
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip().lower()
    notes = (data.get("notes") or "").strip()
    if action not in ("approve", "reject"):
        return jsonify({"ok": False, "msg": "action must be 'approve' or 'reject'."}), 400
    status = "Approved" if action == "approve" else "Rejected"

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id, ot_minutes, status FROM overtime_records WHERE id=%s", (oid,))
    ot_row = cursor.fetchone()
    if not ot_row:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Overtime record not found."}), 404
    if _api_manager_scope_denied(ot_row[0]) or _api_approving_own_record(ot_row[0]):
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Overtime record not found."}), 404

    cursor.execute(
        "UPDATE overtime_records SET status=%s, notes=%s WHERE id=%s",
        (status, notes or None, oid)
    )
    db.commit()

    credited_minutes = 0
    if status == "Approved" and ot_row[2] != "Approved":
        emp_id, ot_minutes = ot_row[0], ot_row[1]
        cursor.execute("SELECT COALESCE(compoff_min_ot_minutes,120) FROM company_settings LIMIT 1")
        min_row = cursor.fetchone()
        min_ot = int(min_row[0]) if min_row else 120
        if ot_minutes >= min_ot:
            cursor.execute("""
                INSERT INTO compoff_balance (employee_id, earned_minutes, used_minutes)
                VALUES (%s, %s, 0)
                ON CONFLICT (employee_id) DO UPDATE SET
                    earned_minutes = compoff_balance.earned_minutes + %s
            """, (emp_id, ot_minutes, ot_minutes))
            db.commit()
            credited_minutes = ot_minutes
    cursor.close()
    db.close()
    return jsonify({"ok": True, "status": status, "compoff_minutes_credited": credited_minutes})


@leave_bp.route("/api/compoff", methods=["GET"])
@api_required
def api_compoff():
    """API endpoint for fetching real comp-off balances from the database.

    Was querying a `compoff_balances` (plural) table that is never created
    anywhere in this codebase's schema (only the real, actively-credited
    `compoff_balance` singular table exists -- see overtime_action() above,
    which is what actually writes to it on OT approval) -- every call to
    this route unconditionally raised (relation does not exist). Fixed to
    query the real table and apply the same minutes -> days conversion the
    session-only /overtime page uses (company_settings.compoff_minutes_per_day),
    so mobile and web show identical numbers for the same employee."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT COALESCE(compoff_minutes_per_day,480) FROM company_settings LIMIT 1")
    cfg_row = cursor.fetchone()
    minutes_per_day = int(cfg_row[0]) if cfg_row else 480

    cursor.execute("""
        SELECT e.employee_id, e.name, COALESCE(e.department,''),
               COALESCE(cb.earned_minutes,0), COALESCE(cb.used_minutes,0)
        FROM employees e
        LEFT JOIN compoff_balance cb ON cb.employee_id = e.employee_id
        WHERE e.is_active=1
        ORDER BY e.name ASC
    """)
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    balances = []
    for employee_id, name, department, earned_minutes, used_minutes in rows:
        earned_days = round(earned_minutes / minutes_per_day, 2) if minutes_per_day else 0.0
        used_days = round(used_minutes / minutes_per_day, 2) if minutes_per_day else 0.0
        balances.append({
            "employee_id": employee_id,
            "name": name or employee_id,
            "department": department or "General",
            "earned_days": earned_days,
            "used_days": used_days,
            "balance_days": max(0.0, round(earned_days - used_days, 2)),
        })
    return jsonify({"ok": True, "balances": balances})


@leave_bp.route("/overtime")
@admin_required
def overtime():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    co = get_company_settings()
    pending_leaves, pending_resignations, pending_tickets = get_pending_counts()

    today = company_today()
    month = int(request.args.get('month', today.month))
    year = int(request.args.get('year', today.year))
    active_tab = request.args.get('tab', 'ot')

    # OT records
    _hr, _hr_args = hr_scope_column(alias="e")
    _mgr, _mgr_args = manager_scope_column(alias="e")
    cursor.execute(f"""
        SELECT o.id, o.employee_id, e.name, o.date, o.shift_end, o.actual_logout,
               o.ot_minutes, o.ot_pay, o.status, o.notes
        FROM overtime_records o JOIN employees e ON e.employee_id=o.employee_id
        WHERE EXTRACT(MONTH FROM o.date)=%s AND EXTRACT(YEAR FROM o.date)=%s {_hr} {_mgr}
        ORDER BY o.date DESC
    """, (month, year) + _hr_args + _mgr_args)  # nosec B608
    records = cursor.fetchall()

    total_ot_minutes = sum(r[6] for r in records)
    total_ot_hours = round(total_ot_minutes / 60, 1)
    total_ot_pay = sum(float(r[7]) for r in records)
    pending_count = sum(1 for r in records if r[8] == 'Pending')
    approved_count = sum(1 for r in records if r[8] == 'Approved')

    # Comp-off settings
    cursor.execute(
        "SELECT COALESCE(compoff_min_ot_minutes,120), COALESCE(compoff_minutes_per_day,480) FROM company_settings LIMIT 1")
    cfg_row = cursor.fetchone() or (120, 480)
    min_ot_minutes = int(cfg_row[0])
    minutes_per_day = int(cfg_row[1])

    # Comp-off balances per employee
    cursor.execute(f"""
        SELECT e.employee_id, e.name, COALESCE(e.role,''), COALESCE(e.department,''),
               COALESCE(cb.earned_minutes,0), COALESCE(cb.used_minutes,0)
        FROM employees e
        LEFT JOIN compoff_balance cb ON cb.employee_id=e.employee_id
        WHERE 1=1 {_hr} {_mgr}
        ORDER BY e.name
    """, _hr_args + _mgr_args)  # nosec B608
    compoff_balances = []
    for emp_id, name, role, dept, earned, used in cursor.fetchall():
        earned_days = round(earned / minutes_per_day, 2) if minutes_per_day else 0
        used_days = round(used / minutes_per_day, 2) if minutes_per_day else 0
        avail_days = max(0, round((earned - used) / minutes_per_day, 2)) if minutes_per_day else 0
        compoff_balances.append({
            "emp_id": emp_id, "name": name, "role": role, "dept": dept,
            "earned_min": earned, "used_min": used,
            "earned_days": earned_days, "used_days": used_days, "avail_days": avail_days
        })

    cursor.close()
    db.close()

    return render_template("overtime.html",
                           co=co,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           records=records,
                           month=month, year=year,
                           month_name=datetime.date(year, month, 1).strftime("%B %Y"),
                           active_nav="overtime",
                           total_ot_hours=total_ot_hours,
                           total_ot_pay=total_ot_pay,
                           pending_count=pending_count,
                           approved_count=approved_count,
                           active_tab=active_tab,
                           min_ot_minutes=min_ot_minutes,
                           minutes_per_day=minutes_per_day,
                           compoff_balances=compoff_balances,
                           )


@leave_bp.route("/overtime_action/<int:oid>", methods=["POST"])
@role_required(*_LEAVE_APPROVER_ROLES)
def overtime_action(oid):
    action = request.form.get('action', '').strip()
    notes = request.form.get('notes', '').strip()
    if action not in ('approve', 'reject'):
        flash("Invalid action.", "danger")
        return redirect(tpath("/overtime?tab=ot"))
    status = 'Approved' if action == 'approve' else 'Rejected'
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    # Fetch OT record before updating
    cursor.execute("SELECT employee_id, ot_minutes, status FROM overtime_records WHERE id=%s", (oid,))
    ot_row = cursor.fetchone()
    if ot_row and (hr_scope_denied(ot_row[0]) or manager_scope_denied(ot_row[0])
                   or _approving_own_record(ot_row[0])):
        cursor.close()
        db.close()
        return "Forbidden", 403

    cursor.execute(
        "UPDATE overtime_records SET status=%s, notes=%s WHERE id=%s",
        (status, notes or None, oid)
    )
    db.commit()

    # Credit comp-off balance when approving
    if status == 'Approved' and ot_row and ot_row[2] != 'Approved':
        emp_id = ot_row[0]
        ot_minutes = ot_row[1]
        # Get compoff threshold settings
        cursor.execute("SELECT COALESCE(compoff_min_ot_minutes,120) FROM company_settings LIMIT 1")
        min_row = cursor.fetchone()
        min_ot = int(min_row[0]) if min_row else 120
        if ot_minutes >= min_ot:
            cursor.execute("""
                INSERT INTO compoff_balance (employee_id, earned_minutes, used_minutes)
                VALUES (%s, %s, 0)
                ON CONFLICT (employee_id) DO UPDATE SET
                    earned_minutes = compoff_balance.earned_minutes + %s
            """, (emp_id, ot_minutes, ot_minutes))
            db.commit()
            flash(f"Overtime approved. {ot_minutes} OT minutes credited to comp-off balance.", "success")
        else:
            flash(f"Overtime approved. OT below threshold ({min_ot} min) -- no comp-off credited.", "success")
    elif status == 'Rejected' and ot_row and ot_row[2] == 'Approved':
        # Reverse comp-off if previously approved
        emp_id = ot_row[0]
        ot_minutes = ot_row[1]
        cursor.execute("""
            UPDATE compoff_balance SET earned_minutes = GREATEST(0, earned_minutes - %s)
            WHERE employee_id=%s
        """, (ot_minutes, emp_id))
        db.commit()
        flash("Overtime rejected and comp-off balance reversed.", "success")
    else:
        flash(f"Overtime record {status.lower()}.", "success")

    cursor.close()
    db.close()
    return redirect(tpath("/overtime?tab=ot"))


@leave_bp.route("/compoff")
@admin_required
def compoff():
    return redirect(tpath("/overtime?tab=compoff"))


@leave_bp.route("/compoff_settings", methods=["POST"])
@admin_required
def compoff_settings():
    min_ot = int(request.form.get("min_ot_minutes", 120))
    mpd = int(request.form.get("minutes_per_day", 480))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE company_settings SET compoff_min_ot_minutes=%s, compoff_minutes_per_day=%s
    """, (min_ot, mpd))
    db.commit()
    cursor.close()
    db.close()
    flash("Comp-off settings saved.", "success")
    return redirect(tpath("/overtime?tab=settings"))


@leave_bp.route("/my_compoff")
@employee_required
def my_compoff():
    emp_id = session["employee_id"]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    cursor.execute(
        "SELECT COALESCE(compoff_min_ot_minutes,120), COALESCE(compoff_minutes_per_day,480) FROM company_settings LIMIT 1")
    cfg_row = cursor.fetchone() or (120, 480)
    min_ot_minutes = int(cfg_row[0])
    minutes_per_day = int(cfg_row[1])

    cursor.execute(
        "SELECT COALESCE(earned_minutes,0), COALESCE(used_minutes,0) FROM compoff_balance WHERE employee_id=%s", (emp_id,))
    bal = cursor.fetchone() or (0, 0)
    earned_min, used_min = bal
    avail_min = max(0, earned_min - used_min)
    earned_days = round(earned_min / minutes_per_day, 2) if minutes_per_day else 0
    used_days = round(used_min / minutes_per_day, 2) if minutes_per_day else 0
    avail_days = round(avail_min / minutes_per_day, 2) if minutes_per_day else 0

    # My OT records
    cursor.execute("""
        SELECT date, ot_minutes, ot_pay, status, notes
        FROM overtime_records WHERE employee_id=%s ORDER BY date DESC LIMIT 30
    """, (emp_id,))
    ot_records = cursor.fetchall()

    # My comp-off leave applications
    cursor.execute("""
        SELECT lr.leave_date, lr.status, lr.reason, lr.created_at
        FROM leave_requests lr JOIN leave_types lt ON lt.id=lr.leave_type_id
        WHERE lr.employee_id=%s AND lt.name='Comp-off'
        ORDER BY lr.created_at DESC LIMIT 20
    """, (emp_id,))
    compoff_leaves = cursor.fetchall()

    cursor.execute("SELECT id FROM leave_types WHERE name='Comp-off' LIMIT 1")
    lt_row = cursor.fetchone()
    compoff_lt_id = lt_row[0] if lt_row else None

    emp_info = get_employee_sidebar_info(cursor, emp_id)
    cursor.close()
    db.close()

    return render_template("my_compoff.html",
                           emp_id=emp_id, emp_info=emp_info,
                           earned_days=earned_days, used_days=used_days, avail_days=avail_days,
                           earned_min=earned_min, used_min=used_min, avail_min=avail_min,
                           minutes_per_day=minutes_per_day, min_ot_minutes=min_ot_minutes,
                           ot_records=ot_records, compoff_leaves=compoff_leaves,
                           compoff_lt_id=compoff_lt_id
                           )
