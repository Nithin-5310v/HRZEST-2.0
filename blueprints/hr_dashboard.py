# -*- coding: utf-8 -*-
"""HR Dashboard blueprint -- single consolidated overview page for HR-role
sessions, replacing the old templates/hr_dashboard.html stub that
blueprints/employees.py's view_employees() used to render on ?view=dashboard.

Every route here is HR-only (@role_required(HR_ROLE) -- 'admin' keeps using
/admin instead, unchanged) and every query is scoped to the session's
assigned employees via utils/helpers.py's hr_scope_column()/
hr_scope_subquery() -- the same helpers this session's earlier hardening
pass added to attendance.py/leave.py/tickets.py/performance.py/
onboarding.py/payroll.py.

Design: this page is a glance-and-deep-link hub, not a reimplementation of
every admin page's full interactive UI. Overview is server-rendered on the
initial GET; every other tab lazy-loads its data as JSON on first open and
renders a compact read-only summary, with a link through to the existing,
already-built, already-hardened full page for anything requiring an actual
action (approve a leave, correct attendance, edit an employee) -- reusing
that page's own routes/templates rather than duplicating a second copy of
the same form/workflow inline here.
"""
import datetime
from flask import Blueprint, jsonify, render_template, request
from database import get_db_connection
from utils.auth import role_required, HR_ROLE
from utils.helpers import tpath, hr_scope_column, hr_scope_subquery, hr_scope_denied, get_company_settings, company_today
from utils.attendance_utils import attendance_pct_series

hr_dashboard_bp = Blueprint("hr_dashboard", __name__)


@hr_dashboard_bp.route("/hr_dashboard")
@role_required(HR_ROLE)
def hr_dashboard():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()
    _hr, _hr_args = hr_scope_column(alias="e")
    _hr_sub, _ = hr_scope_subquery()  # no-alias form, for tables with a plain employee_id column

    cursor.execute(f"SELECT COUNT(*) FROM employees e WHERE 1=1 {_hr}", _hr_args)  # nosec B608
    total = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT COUNT(DISTINCT e.employee_id) FROM employees e
        JOIN attendance a ON a.employee_id=e.employee_id AND a.date=%s
        WHERE a.login_time IS NOT NULL {_hr}
    """, (today,) + _hr_args)  # nosec B608
    active_today = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT COUNT(DISTINCT e.employee_id) FROM employees e
        JOIN attendance a ON a.employee_id=e.employee_id AND a.date=%s
        WHERE a.status IN ('Late Login','Half Day Login') {_hr}
    """, (today,) + _hr_args)  # nosec B608
    late_today = cursor.fetchone()[0]

    cursor.execute(f"""
        SELECT COUNT(DISTINCT lr.employee_id) FROM leave_requests lr
        JOIN employees e ON e.employee_id=lr.employee_id
        WHERE lr.status='Approved' AND lr.leave_date=%s {_hr}
    """, (today,) + _hr_args)  # nosec B608
    on_leave_today = cursor.fetchone()[0]

    # Approximate, glance-only figure -- not adjusted for holidays/weekends
    # the way monthly_report's per-day billable-days logic is, since this
    # is a single "today" snapshot number, not a payroll-relevant total.
    absent_today = max(0, total - active_today - on_leave_today)

    cursor.execute(f"SELECT COUNT(*) FROM leave_requests WHERE status='Pending' {_hr_sub}", _hr_args)  # nosec B608
    pending_leaves = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM resignation_requests WHERE status='Pending' {_hr_sub}", _hr_args)  # nosec B608
    pending_resignations = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM overtime_records WHERE status='Pending' {_hr_sub}", _hr_args)  # nosec B608
    pending_overtime = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM tickets WHERE status IN ('Open','In Progress') {_hr_sub}", _hr_args)  # nosec B608
    open_tickets = cursor.fetchone()[0]

    week_ago = today - datetime.timedelta(days=7)
    cursor.execute(f"SELECT COUNT(*) FROM employees e WHERE e.date_of_joining >= %s {_hr}", (week_ago,) + _hr_args)  # nosec B608
    new_joiners = cursor.fetchone()[0]

    co = get_company_settings()
    cursor.close()
    db.close()

    return render_template(
        "hr_dashboard.html",
        co=co,
        total=total, active_today=active_today, late_today=late_today,
        absent_today=absent_today, on_leave_today=on_leave_today,
        pending_leaves=pending_leaves, pending_resignations=pending_resignations,
        pending_overtime=pending_overtime,
        # "Pending approvals" (distinct from "pending leave requests", which
        # already gets its own card) = resignations + overtime awaiting a
        # decision -- the other two approval queues an HR session can act on.
        pending_approvals=pending_resignations + pending_overtime,
        open_tickets=open_tickets, new_joiners=new_joiners,
        active_nav="dashboard",
    )


@hr_dashboard_bp.route("/api/hr_dashboard/employees")
@role_required(HR_ROLE)
def api_hr_dashboard_employees():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT e.employee_id, e.name, COALESCE(e.department,''), COALESCE(e.role,''),
               COALESCE(e.phone,''), COALESCE(e.email,'')
        FROM employees e WHERE 1=1 {_hr} ORDER BY e.name
    """, _hr_args)  # nosec B608
    rows = cursor.fetchall()
    cursor.execute("SELECT DISTINCT employee_id FROM resignation_requests WHERE status='Accepted'")
    resigned_set = {r[0] for r in cursor.fetchall()}
    cursor.execute("SELECT DISTINCT employee_id FROM leave_requests WHERE status='Approved' AND leave_date=CURRENT_DATE")
    on_leave_set = {r[0] for r in cursor.fetchall()}
    cursor.close()
    db.close()
    employees = []
    for eid, name, dept, role, phone, email in rows:
        status = "Resigned" if eid in resigned_set else ("On Leave" if eid in on_leave_set else "Active")
        employees.append({
            "employee_id": eid, "name": name, "department": dept, "role": role,
            "phone": phone, "email": email, "status": status,
            "detail_url": tpath(f"/employee_detail/{eid}"),
        })
    return jsonify({"ok": True, "employees": employees})


@hr_dashboard_bp.route("/api/hr_dashboard/attendance/today")
@role_required(HR_ROLE)
def api_hr_dashboard_attendance_today():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT e.employee_id, e.name, a.login_time, a.logout_time, a.status
        FROM employees e
        LEFT JOIN attendance a ON a.employee_id=e.employee_id AND a.date=%s
        WHERE 1=1 {_hr}
        ORDER BY e.name
    """, (today,) + _hr_args)  # nosec B608
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    year, month = today.year, today.month
    out = []
    for eid, name, login_t, logout_t, status in rows:
        if status in ("Late Login", "Half Day Login"):
            disp = "Late"
        elif login_t:
            disp = "Present"
        else:
            disp = "Absent"
        out.append({
            "employee_id": eid, "name": name, "status": disp,
            "login_time": str(login_t) if login_t else None,
            "logout_time": str(logout_t) if logout_t else None,
            "detail_url": tpath(f"/employee_attendance_detail/{eid}/{year}/{month}"),
        })
    return jsonify({"ok": True, "date": today.isoformat(), "rows": out})


@hr_dashboard_bp.route("/api/hr_dashboard/leave/pending")
@role_required(HR_ROLE)
def api_hr_dashboard_leave_pending():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT lr.id, e.employee_id, e.name, lr.leave_date, lr.reason, lr.created_at
        FROM leave_requests lr JOIN employees e ON e.employee_id=lr.employee_id
        WHERE lr.status='Pending' {_hr}
        ORDER BY lr.created_at DESC
    """, _hr_args)  # nosec B608
    leaves = [
        {"id": r[0], "employee_id": r[1], "name": r[2],
         "leave_date": str(r[3]) if r[3] else None, "reason": r[4],
         "created_at": str(r[5]) if r[5] else None}
        for r in cursor.fetchall()
    ]
    cursor.execute(f"""
        SELECT rr.id, e.employee_id, e.name, rr.last_working_day, rr.reason, rr.created_at
        FROM resignation_requests rr JOIN employees e ON e.employee_id=rr.employee_id
        WHERE rr.status='Pending' {_hr}
        ORDER BY rr.created_at DESC
    """, _hr_args)  # nosec B608
    resignations = [
        {"id": r[0], "employee_id": r[1], "name": r[2],
         "last_working_day": str(r[3]) if r[3] else None, "reason": r[4],
         "created_at": str(r[5]) if r[5] else None}
        for r in cursor.fetchall()
    ]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "leaves": leaves, "resignations": resignations})


@hr_dashboard_bp.route("/api/hr_dashboard/payroll/report")
@role_required(HR_ROLE)
def api_hr_dashboard_payroll_report():
    """Full unmasked salary structure/report for this HR session's assigned
    employees -- per this session's payroll-access decision, HR gets the
    same financial detail 'admin' sees on /salary_report, but only for
    employees assigned to them. Reuses payroll.py's compute_salary_data_for_month()
    (its employee_ids= param was added specifically for this) rather than a
    second, potentially-drifting reimplementation of the same computation."""
    from blueprints.payroll import compute_salary_data_for_month
    today = datetime.date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column()
    cursor.execute(f"SELECT employee_id FROM employees WHERE 1=1 {_hr}", _hr_args)  # nosec B608
    emp_ids = [r[0] for r in cursor.fetchall()]
    cursor.close()
    db.close()

    salary_data = compute_salary_data_for_month(year, month, employee_ids=emp_ids)
    rows = [{
        "employee_id": e["emp_id"], "name": e["name"], "role": e.get("role", ""),
        "salary_per_day": e.get("spd", 0), "full_days": e.get("full_days", 0),
        "half_days": e.get("half_days", 0), "late_days": e.get("late_days", 0),
        "absent": e.get("absent", 0), "gross": e.get("gross", 0),
        "deduction": e.get("deduction", 0), "incentive": e.get("incentive", 0),
        "net": e.get("net", 0),
        "payslip_url": tpath(f"/api/hr_dashboard/payroll/payslip/{e['emp_id']}/{year}/{month}"),
    } for e in salary_data]
    return jsonify({"ok": True, "year": year, "month": month, "rows": rows})


@hr_dashboard_bp.route("/api/hr_dashboard/payroll/payslip/<emp_id>/<int:year>/<int:month>")
@role_required(HR_ROLE)
def api_hr_dashboard_payslip(emp_id, year, month):
    """HR-only payslip view, full unmasked detail for an assigned employee
    -- payroll.py's own /view_payslip explicitly blocks any non-'admin'
    role and stays that way (see its own docstring); this is a separate,
    deliberately HR-scoped route reusing the same rendering logic via
    payroll.py's _render_payslip_html()."""
    if hr_scope_denied(emp_id):
        return "Employee not found", 404
    from blueprints.payroll import _render_payslip_html
    return _render_payslip_html(emp_id, year, month)


@hr_dashboard_bp.route("/api/hr_dashboard/payroll/overtime")
@role_required(HR_ROLE)
def api_hr_dashboard_overtime():
    today = datetime.date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT o.id, o.employee_id, e.name, o.date, o.ot_minutes, o.ot_pay, o.status
        FROM overtime_records o JOIN employees e ON e.employee_id=o.employee_id
        WHERE EXTRACT(MONTH FROM o.date)=%s AND EXTRACT(YEAR FROM o.date)=%s {_hr}
        ORDER BY o.date DESC
    """, (month, year) + _hr_args)  # nosec B608
    rows = [{
        "id": r[0], "employee_id": r[1], "name": r[2], "date": str(r[3]) if r[3] else None,
        "ot_minutes": r[4], "ot_pay": float(r[5]) if r[5] is not None else 0,
        "status": r[6],
    } for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "year": year, "month": month, "rows": rows})


@hr_dashboard_bp.route("/api/hr_dashboard/reports")
@role_required(HR_ROLE)
def api_hr_dashboard_reports():
    """Scoped analytics for an HR session's assigned employees only --
    same metric shapes as blueprints/admin_views.py's admin-only /analytics
    (attendance % trend, headcount trend, department breakdown), copied and
    scoped rather than modifying that route/template, which stays fully
    unscoped/company-wide for 'admin' exactly as before."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = datetime.date.today()
    _hr, _hr_args = hr_scope_column(alias="e")
    _hr_sub, _ = hr_scope_subquery()

    cursor.execute(f"SELECT COUNT(*) FROM employees e WHERE 1=1 {_hr}", _hr_args)  # nosec B608
    total_employees = cursor.fetchone()[0]

    cursor.execute("SELECT date FROM holidays")
    all_holidays = {r[0] for r in cursor.fetchall()}

    _month_bounds = []
    for i in range(5, -1, -1):
        ref = (today.replace(day=1) - datetime.timedelta(days=1) * (i * 28)).replace(day=1)
        y, m = ref.year, ref.month
        month_start = datetime.date(y, m, 1)
        month_end = datetime.date(y + 1, 1, 1) if m == 12 else datetime.date(y, m + 1, 1)
        _month_bounds.append((y, m, month_start, month_end))

    cursor.execute(f"""
        SELECT a.date, COUNT(DISTINCT a.employee_id)
        FROM attendance a JOIN employees e ON e.employee_id=a.employee_id
        WHERE a.date >= %s AND a.date < %s AND a.login_time IS NOT NULL {_hr}
        GROUP BY a.date
    """, (_month_bounds[0][2], _month_bounds[-1][3]) + _hr_args)  # nosec B608
    present_by_day = {r[0]: r[1] for r in cursor.fetchall()}
    attendance_trend = attendance_pct_series(_month_bounds, present_by_day, all_holidays, today, total_employees)

    headcount_trend = []
    for _y, _m, month_start, month_end in _month_bounds:
        cursor.execute(f"SELECT COUNT(*) FROM employees e WHERE e.date_of_joining < %s {_hr}", (month_end,) + _hr_args)  # nosec B608
        headcount_trend.append({"month_label": month_start.strftime("%b %Y"), "count": cursor.fetchone()[0]})

    cursor.execute(f"""
        SELECT COALESCE(e.department, 'Unassigned'), COUNT(*) FROM employees e
        WHERE 1=1 {_hr}
        GROUP BY COALESCE(e.department, 'Unassigned') ORDER BY 2 DESC
    """, _hr_args)  # nosec B608
    dept_data = [{"department": r[0], "count": r[1]} for r in cursor.fetchall()]

    cursor.close()
    db.close()
    return jsonify({
        "ok": True,
        "total_employees": total_employees,
        "attendance_trend": attendance_trend,
        "headcount_trend": headcount_trend,
        "dept_data": dept_data,
    })


@hr_dashboard_bp.route("/api/hr_dashboard/onboarding")
@role_required(HR_ROLE)
def api_hr_dashboard_onboarding():
    """New-joiner checklist/progress for this HR session's assigned
    employees -- same scoped query shape as blueprints/onboarding.py's own
    onboarding() (hardened in this session's earlier pass), reused here as
    a read-only glance; the Manage link goes to that existing page for
    actually updating a task."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT eo.id, e.employee_id, e.name, ot.name AS template_name,
               eo.assigned_date, eo.due_date, eo.status,
               COUNT(eot.id) AS total_tasks,
               SUM(CASE WHEN eot.status='Done' THEN 1 ELSE 0 END) AS done_tasks
        FROM employee_onboarding eo
        JOIN employees e ON e.employee_id = eo.employee_id {_hr}
        JOIN onboarding_templates ot ON ot.id = eo.template_id
        LEFT JOIN employee_onboarding_tasks eot ON eot.onboarding_id = eo.id
        GROUP BY eo.id, e.employee_id, e.name, ot.name, eo.assigned_date, eo.due_date, eo.status
        ORDER BY eo.assigned_date DESC
    """, _hr_args)  # nosec B608
    today = datetime.date.today()
    rows = []
    for ob_id, eid, name, tname, assigned, due, status, total_tasks, done_tasks in cursor.fetchall():
        total_tasks = total_tasks or 0
        done_tasks = done_tasks or 0
        rows.append({
            "id": ob_id, "employee_id": eid, "name": name, "template_name": tname,
            "assigned_date": str(assigned) if assigned else None,
            "due_date": str(due) if due else None,
            "status": status,
            "is_overdue": bool(due and due < today and status != "Completed"),
            "progress_pct": round(done_tasks / total_tasks * 100) if total_tasks else 0,
            "detail_url": tpath(f"/onboarding_detail/{ob_id}"),
        })
    cursor.close()
    db.close()
    return jsonify({"ok": True, "rows": rows})


@hr_dashboard_bp.route("/api/hr_dashboard/performance")
@role_required(HR_ROLE)
def api_hr_dashboard_performance():
    """Current-quarter review status for this HR session's assigned
    employees -- same scoped query shape as blueprints/performance.py's
    own performance() (hardened in this session's earlier pass)."""
    today = datetime.date.today()
    q = int(request.args.get("quarter", (today.month - 1) // 3 + 1))
    yr = int(request.args.get("year", today.year))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT e.employee_id, e.name, COALESCE(e.department,''),
               COALESCE(pr.overall_rating,0), COALESCE(pr.status,'Not Started')
        FROM employees e
        LEFT JOIN performance_reviews pr
            ON pr.employee_id=e.employee_id AND pr.year=%s AND pr.quarter=%s
        WHERE e.is_active=1 {_hr}
        ORDER BY e.name
    """, (yr, q) + _hr_args)  # nosec B608
    rows = [{
        "employee_id": r[0], "name": r[1], "department": r[2],
        "rating": float(r[3]), "status": r[4],
        "review_url": tpath(f"/performance_review/{r[0]}?quarter={q}&year={yr}"),
    } for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "year": yr, "quarter": q, "rows": rows})


@hr_dashboard_bp.route("/api/hr_dashboard/tickets")
@role_required(HR_ROLE)
def api_hr_dashboard_tickets():
    """Open/in-progress support tickets for this HR session's assigned
    employees -- same scoped query shape as blueprints/leave.py's
    leave_holidays(tab=tickets) (hardened in this session's earlier pass).
    Resolve/close actions stay on that existing page (ticket_action there
    is already hr_scope_denied-guarded from Phase 1)."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT t.id, e.employee_id, e.name, t.category, t.subject, t.priority, t.status, t.created_at
        FROM tickets t JOIN employees e ON t.employee_id = e.employee_id {_hr}
        WHERE t.status IN ('Open','In Progress')
        ORDER BY CASE WHEN t.priority='High' THEN 0 WHEN t.priority='Medium' THEN 1 ELSE 2 END, t.created_at DESC
    """, _hr_args)  # nosec B608
    rows = [{
        "id": r[0], "employee_id": r[1], "name": r[2], "category": r[3],
        "subject": r[4], "priority": r[5], "status": r[6],
        "created_at": str(r[7]) if r[7] else None,
    } for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "rows": rows})


@hr_dashboard_bp.route("/api/hr_dashboard/policies")
@role_required(HR_ROLE)
def api_hr_dashboard_policies():
    """Company-wide, not per-assigned-employee (see blueprints/policies.py's
    own docstring) -- every policy, published or not, since this is the
    HR/admin management view; add/edit/publish/delete post to that
    blueprint's routes and reload this tab."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, category, title, body, is_published, updated_at FROM company_policies "
        "ORDER BY sort_order, id"
    )
    rows = [{
        "id": r[0], "category": r[1], "title": r[2], "body": r[3],
        "is_published": bool(r[4]), "updated_at": str(r[5]) if r[5] else None,
    } for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "rows": rows})


@hr_dashboard_bp.route("/api/hr_dashboard/holidays")
@role_required(HR_ROLE)
def api_hr_dashboard_holidays():
    """Company-wide by nature -- add_holiday()/delete_holiday()/
    import_indian_holidays() (blueprints/leave.py) are already HR-reachable
    and already correct with no scoping gap (confirmed in this session's
    Phase 1 audit), so this tab only needs a read endpoint; its forms post
    straight to those existing routes."""
    today = datetime.date.today()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT id, date, name FROM holidays WHERE date >= %s ORDER BY date", (today.replace(month=1, day=1),))
    rows = [{"id": r[0], "date": str(r[1]), "name": r[2]} for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "rows": rows})
