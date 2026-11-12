# -*- coding: utf-8 -*-
"""admin_views: admin dashboard, omnisearch, live tiles, attendance chart, MFA-required page, activity log"""
import datetime
import utils.config as cfg
from database import get_db_connection
from flask import g, jsonify, render_template, request, session
from utils.auth import admin_required, require_access, role_required
from utils.helpers import (
    co_scope_column,
    co_scope_subquery,
    company_today,
    contact_lookup_keys,
    get_pending_action_counts,
)
from utils.payment_history import get_payment_history, get_plan_details
from . import admin_views_bp


@admin_views_bp.route("/admin")
@admin_required
def admin():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()
    active_cid = session.get("active_company_id")
    _co_filter, _co_args = co_scope_column(active_cid, alias="e")
    _co_sub, _ = co_scope_subquery(active_cid)

    # total/present/late/pending_* used to be six separate round-trips (a
    # WHERE COUNT(*) query each) -- same scoping fragments as before,
    # combined into one query via scalar subqueries. pending_ot stays
    # separate below since it's deliberately try/except-guarded for
    # deployments predating the overtime_records table; folding it in here
    # would make the whole dashboard 500 on that case instead of just
    # showing 0 OT pending.
    cursor.execute(
        f"""
        SELECT
            (SELECT COUNT(*) FROM employees e WHERE 1=1 {_co_filter}),
            (SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND login_time IS NOT NULL {_co_sub}),
            (SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND status='Late Login' {_co_sub}),
            (SELECT COUNT(*) FROM leave_requests WHERE status='Pending' {_co_sub}),
            (SELECT COUNT(*) FROM resignation_requests WHERE status='Pending' {_co_sub}),
            (SELECT COUNT(*) FROM tickets WHERE status IN ('Open','In Progress') {_co_sub})
        """,  # nosec B608 -- _co_filter/_co_sub are fixed scope fragments, values bound
        _co_args + (today,) + _co_args + (today,) + _co_args + _co_args + _co_args + _co_args
    )
    (total, present, late,
     pending_leaves, pending_resignations, pending_tickets) = cursor.fetchone()

    cursor.execute(
        f"SELECT e.employee_id, e.name, a.login_time, a.logout_time, a.status, "  # nosec B608
        f"       a.logout_status, a.attendance_type, e.role "
        f"FROM employees e "
        f"LEFT JOIN attendance a ON e.employee_id=a.employee_id AND a.date=%s "
        f"WHERE 1=1 {_co_filter} ORDER BY e.name",
        (today,) + _co_args
    )
    today_rows = cursor.fetchall()

    if active_cid:
        cursor.execute("SELECT employee_id, name FROM employees WHERE company_id=%s ORDER BY name", _co_args)
    else:
        cursor.execute("SELECT employee_id, name FROM employees ORDER BY name")
    all_employees = cursor.fetchall()

    try:
        cursor.execute("SELECT COUNT(*) FROM overtime_records WHERE status='Pending'")
        pending_ot = cursor.fetchone()[0]
    except Exception:
        pending_ot = 0

    cursor.execute("SELECT id, name, COALESCE(code,'') FROM companies ORDER BY name")
    companies_list = cursor.fetchall()

    # Onboarding summary for dashboard widget
    try:
        cursor.execute("""
            SELECT
              SUM(CASE WHEN status != 'Completed' THEN 1 ELSE 0 END),
              SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END),
              SUM(CASE WHEN status != 'Completed' AND due_date < %s THEN 1 ELSE 0 END)
            FROM employee_onboarding
        """, (today,))
        _ob = cursor.fetchone()
        ob_active = int(_ob[0] or 0)
        ob_completed = int(_ob[1] or 0)
        ob_overdue = int(_ob[2] or 0)
        cursor.execute("""
            SELECT eo.id, e.name, ot.name, eo.due_date
            FROM employee_onboarding eo
            JOIN employees e ON eo.employee_id = e.employee_id
            JOIN onboarding_templates ot ON eo.template_id = ot.id
            WHERE eo.status != 'Completed' AND eo.due_date < %s
            ORDER BY eo.due_date LIMIT 5
        """, (today,))
        ob_overdue_list = cursor.fetchall()
    except Exception:
        ob_active = ob_completed = ob_overdue = 0
        ob_overdue_list = []

    cursor.execute("SELECT id, break_name, break_time, duration_minutes, is_active FROM break_config ORDER BY break_time")
    break_rows = cursor.fetchall()
    breaks_display = []
    for b in break_rows:
        bt = b[2]
        if hasattr(bt, 'seconds'):
            h, m = divmod(bt.seconds // 60, 60)
        elif hasattr(bt, 'hour'):
            h, m = bt.hour, bt.minute
        else:
            # SQLite fallback has no native TIME type -- break_time comes
            # back as a plain "HH:MM:SS" string instead of a time/timedelta.
            h, m = (int(x) for x in str(bt).split(':')[:2])
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        breaks_display.append({
            "id": b[0], "name": b[1],
            "time_str": "%02d:%02d %s" % (h12, m, ampm),
            "duration": b[3], "is_active": b[4]
        })

    cursor.close()
    db.close()
    shift = cfg.shift_rules()

    # Plan dates and the latest payments with their invoices -- company
    # admins only, the same people the billing emails go to.
    plan, recent_payments = None, []
    if session.get("admin_role", "admin") == "admin" and getattr(g, "tenant_db", None):
        plan = get_plan_details(g.tenant_db)
        if plan:
            recent_payments = get_payment_history(tenant_id=plan["tenant_id"], tenant_schema=g.tenant_db,
                                                  limit=5)["payments"]

    return render_template("admin.html",
                           plan=plan,
                           recent_payments=recent_payments,
                           total=total,
                           present=present,
                           absent=total - present,
                           late=late,
                           today=today.strftime("%d %b %Y"),
                           active_nav="dashboard",
                           today_rows=today_rows,
                           all_employees=all_employees,
                           shift_start=shift.start.strftime("%I:%M %p"),
                           shift_end=shift.end.strftime("%I:%M %p"),
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_ot=pending_ot,
                           pending_tickets=pending_tickets,
                           now_month=today.month,
                           now_year=today.year,
                           breaks_display=breaks_display,
                           companies_list=companies_list,
                           ob_active=ob_active,
                           ob_completed=ob_completed,
                           ob_overdue=ob_overdue,
                           ob_overdue_list=ob_overdue_list,
                           )


@admin_views_bp.route("/api/admin/search")
@admin_required
def api_admin_search():
    """Omnisearch across employees, tickets and leave requests for the
    admin dashboard's search bar. Static admin-page matches (Settings,
    Analytics, ...) are matched client-side -- no DB query needed for those."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": True, "results": []})
    like = f"%{q}%"
    active_cid = session.get("active_company_id")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    results = []

    _co_filter, _co_args = co_scope_column(active_cid, alias="e")
    cursor.execute(
        f"SELECT e.employee_id, e.name, e.email, e.role FROM employees e "  # nosec B608
        # email/phone are encrypted: matched exactly via their blind index
        # (ILIKE on them only still finds rows not yet migrated).
        f"WHERE (e.name ILIKE %s OR e.employee_id ILIKE %s OR e.email ILIKE %s OR e.phone ILIKE %s "
        f"OR e.email_bidx = ANY(%s) OR e.phone_bidx = ANY(%s)) {_co_filter} "
        f"ORDER BY e.name LIMIT 8",
        (like, like, like, like, contact_lookup_keys("email", q), contact_lookup_keys("phone", q)) + _co_args
    )
    for eid, name, email, role in cursor.fetchall():
        results.append({
            "type": "employee", "icon": "user",
            "label": name, "sub": eid + (f" · {role}" if role else ""),
            "url": f"/employees?hl={eid}",
        })

    _tk_sub, _tk_args = co_scope_subquery(active_cid, alias="t")
    cursor.execute(
        f"SELECT t.id, t.subject, t.status, e.name FROM tickets t "  # nosec B608
        f"JOIN employees e ON t.employee_id=e.employee_id "
        f"WHERE (t.subject ILIKE %s OR t.category ILIKE %s OR e.name ILIKE %s) {_tk_sub} "
        f"ORDER BY t.created_at DESC LIMIT 6",
        (like, like, like) + _tk_args
    )
    for tid, subject, status, emp_name in cursor.fetchall():
        results.append({
            "type": "ticket", "icon": "ticket",
            "label": subject, "sub": f"{emp_name} · {status}",
            "url": "/tickets",
        })

    _lv_sub, _lv_args = co_scope_subquery(active_cid, alias="lr")
    cursor.execute(
        f"SELECT lr.id, lr.leave_date, lr.status, e.name FROM leave_requests lr "  # nosec B608
        f"JOIN employees e ON lr.employee_id=e.employee_id "
        f"WHERE (e.name ILIKE %s OR lr.reason ILIKE %s) {_lv_sub} "
        f"ORDER BY lr.created_at DESC LIMIT 6",
        (like, like) + _lv_args
    )
    for lid, leave_date, status, emp_name in cursor.fetchall():
        results.append({
            "type": "leave", "icon": "calendar-event",
            "label": f"{emp_name} -- {leave_date}", "sub": status,
            "url": "/leave_holidays",
        })

    cursor.close()
    db.close()
    return jsonify({"ok": True, "results": results})


@admin_views_bp.route("/api/dashboard_live")
@admin_required
@require_access("attendance")
def dashboard_live():
    def fmt(t):
        if t is None:
            return None
        if hasattr(t, "strftime"):
            return t.strftime("%H:%M:%S")
        total = int(t.total_seconds())
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()
    active_cid = session.get("active_company_id")
    _co_filter, _co_args = co_scope_column(active_cid, alias="e")
    _co_sub, _ = co_scope_subquery(active_cid)

    if active_cid:
        cursor.execute("SELECT COUNT(*) FROM employees WHERE company_id=%s", _co_args)
    else:
        cursor.execute("SELECT COUNT(*) FROM employees")
    total = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND login_time IS NOT NULL {_co_sub}",  # nosec B608
        (today,) + _co_args
    )
    present = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND status='Late Login' {_co_sub}",  # nosec B608
        (today,) + _co_args
    )
    late = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT e.employee_id, e.name, a.login_time, a.logout_time, "  # nosec B608
        f"       a.status, a.logout_status, a.attendance_type, e.role "
        f"FROM employees e "
        f"LEFT JOIN attendance a ON e.employee_id=a.employee_id AND a.date=%s "
        f"WHERE 1=1 {_co_filter} ORDER BY e.name",
        (today,) + _co_args
    )
    rows = []
    for emp_id, name, login_t, logout_t, status, logout_s, att_type, role in cursor.fetchall():
        rows.append({
            "emp_id": emp_id,
            "name": name,
            "role": role or "",
            "login_t": fmt(login_t),
            "logout_t": fmt(logout_t),
            "status": status or "",
            "logout_s": logout_s or "",
            "att_type": att_type or "",
        })

    pending_leaves, pending_resignations, pending_tickets = get_pending_action_counts(cursor, active_cid)

    cursor.close()
    db.close()

    return jsonify({
        "total": total,
        "present": present,
        "absent": total - present,
        "late": late,
        "rows": rows,
        "pending_leaves": pending_leaves,
        "pending_resignations": pending_resignations,
        "pending_tickets": pending_tickets,
    })


@admin_views_bp.route("/api/attendance_chart_data")
@admin_required
@require_access("attendance")
def attendance_chart_data():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()

    active_cid = session.get("active_company_id")
    _co_filter, _co_args = co_scope_column(active_cid, alias="e")
    _co_sub, _ = co_scope_subquery(active_cid, alias="a")

    # Last 30 days: present count per day
    cursor.execute(f"""
        SELECT a.date, COUNT(DISTINCT a.employee_id)
        FROM attendance a
        WHERE a.date >= %s AND a.date <= %s AND a.login_time IS NOT NULL {_co_sub}
        GROUP BY a.date ORDER BY a.date
    """, (today - datetime.timedelta(days=29), today) + _co_args)  # nosec B608
    present_by_day = {str(r[0]): r[1] for r in cursor.fetchall()}

    if active_cid:
        cursor.execute("SELECT COUNT(*) FROM employees WHERE company_id=%s", _co_args)
    else:
        cursor.execute("SELECT COUNT(*) FROM employees")
    total = cursor.fetchone()[0]

    trend_labels, trend_present, trend_absent = [], [], []
    for i in range(29, -1, -1):
        d = today - datetime.timedelta(days=i)
        key = str(d)
        p = present_by_day.get(key, 0)
        trend_labels.append(d.strftime("%d %b"))
        trend_present.append(p)
        trend_absent.append(max(total - p, 0))

    # Today by department
    cursor.execute(f"""
        SELECT COALESCE(e.department, 'Unassigned'),
               COUNT(DISTINCT CASE WHEN a.login_time IS NOT NULL THEN e.employee_id END),
               COUNT(DISTINCT e.employee_id)
        FROM employees e
        LEFT JOIN attendance a ON e.employee_id=a.employee_id AND a.date=%s
        WHERE 1=1 {_co_filter}
        GROUP BY COALESCE(e.department, 'Unassigned')
        ORDER BY COALESCE(e.department, 'Unassigned')
    """, (today,) + _co_args)  # nosec B608
    dept_labels, dept_present, dept_absent = [], [], []
    for dept, p, tot in cursor.fetchall():
        dept_labels.append(dept)
        dept_present.append(p or 0)
        dept_absent.append(max((tot or 0) - (p or 0), 0))

    cursor.close()
    db.close()
    return jsonify({
        "trend": {"labels": trend_labels, "present": trend_present, "absent": trend_absent},
        "dept": {"labels": dept_labels, "present": dept_present, "absent": dept_absent},
    })


@admin_views_bp.route("/admin/mfa-required")
@admin_required
def admin_mfa_required_page():
    """Forced-enrollment landing page: app.py's _enforce_admin_mfa_enrollment
    before_request hook redirects every admin/manager session without TOTP
    enrolled here, and here only, until they complete it (soc_analyst
    sessions can't reach this unenrolled in the first place -- /mfa_login_verify
    already marks TOTP enrolled before that session exists at all). Not a
    TOTP step-up gate -- an unenrolled admin can't pass a step-up gate they
    haven't set up yet, so this page (and the /api/settings/2fa/setup +
    /api/settings/2fa/enable it calls) is deliberately reachable on
    @admin_required alone."""
    return render_template("admin_mfa_required.html")


_ACTIVITY_LOG_PER_PAGE = 50


@admin_views_bp.route("/activity_log")
@role_required("admin")
def activity_log_page():
    """Browsable view over this tenant's own audit_logs table (utils/
    helpers.py's _audit(), written from ~20 call sites across leave.py,
    payroll.py, disbursement.py, documents.py, employees.py, and more --
    salary changes, payroll lock/unlock, leave/resignation approvals,
    disbursement bank-config edits, document uploads, employee deletion).
    Those writes previously had no reviewer anywhere in the product --
    the only existing "Audit Log" page (super_admin_audit_log.html) is a
    completely separate table (security_events: login/access-denial
    events only), not this one. Mirrors that page's search/filter/
    pagination shape, tenant-scoped and admin-only instead of platform-
    operator-only."""
    return render_template("activity_log.html", active_nav="admin_tools", **_load_activity_log())


def _load_activity_log():
    """Query + filter + paginate audit_logs from the current request's
    q / action / page args. Shared by /activity_log and the Activity Log tab
    of /admin_tools, so both views always show the same data."""
    q = request.args.get("q", "").strip()
    action_filter = request.args.get("action", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    conditions = []
    params = []
    if action_filter:
        conditions.append("action=%s")
        params.append(action_filter)
    if q:
        conditions.append("(detail ILIKE %s OR actor ILIKE %s OR target_id ILIKE %s OR action ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like, like])
    where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""  # nosec B608 -- conditions are fixed literals built above, never from unescaped request input; all variable values are bound params  # noqa: E501

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(f"SELECT COUNT(*) FROM audit_logs{where_sql}", params)  # nosec B608 -- see where_sql note above
    total = cursor.fetchone()[0]
    total_pages = max(1, -(-total // _ACTIVITY_LOG_PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * _ACTIVITY_LOG_PER_PAGE
    cursor.execute(
        f"SELECT actor, actor_type, action, target_table, target_id, detail, ip_address, created_at "  # nosec B608 -- see where_sql note above
        f"FROM audit_logs{where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        params + [_ACTIVITY_LOG_PER_PAGE, offset],
    )
    rows = cursor.fetchall()
    cursor.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action")
    all_actions = [r[0] for r in cursor.fetchall()]
    cursor.close()
    db.close()
    entries = [
        {"actor": r[0], "actor_type": r[1], "action": r[2], "target_table": r[3],
         "target_id": r[4], "detail": r[5], "ip_address": r[6], "created_at": r[7]}
        for r in rows
    ]
    return dict(
        entries=entries, q=q, action_filter=action_filter,
        all_actions=all_actions, page=page, total_pages=total_pages, total=total,
    )
