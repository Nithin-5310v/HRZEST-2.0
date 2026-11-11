# -*- coding: utf-8 -*-
"""employee_portal: employee portal page, my_* shortcuts, change password, portal API"""
import calendar
import datetime
import os
from database import get_db_connection
from flask import jsonify, redirect, render_template, request, session
from utils.attendance_utils import fetch_holidays_set, get_billable_past_days, infer_type_legacy, is_company_working_day
from utils.auth import employee_api_required, employee_required, session_account_ok, validate_new_password
from utils.helpers import (
    _db,
    amount,
    coerce_datetime,
    company_today,
    decrypt_pii,
    decrypt_pii_date,
    get_auth_config,
    int_param,
    tpath,
)
from utils.leave_utils import assign_leave_balances_for_employee
from utils.tenant_files import resolve_face_photo
from . import _fmt_t, employee_portal_bp


@employee_portal_bp.route("/my_attendance")
@employee_required
def my_attendance():
    """Deep-link redirect → employee portal attendance tab."""
    from flask import redirect
    return redirect(tpath("/employee_portal#tab-attendance"))


@employee_portal_bp.route("/my_leaves")
@employee_required
def my_leaves():
    """Deep-link redirect → employee portal leaves tab."""
    from flask import redirect
    return redirect(tpath("/employee_portal#tab-leaves"))


@employee_portal_bp.route("/my_salary")
@employee_required
def my_salary():
    """Deep-link redirect → employee portal salary tab."""
    from flask import redirect
    return redirect(tpath("/employee_portal#tab-salary"))


@employee_portal_bp.route("/my_profile")
@employee_required
def my_profile():
    """Deep-link redirect → employee portal profile tab."""
    from flask import redirect
    return redirect(tpath("/employee_portal#tab-profile"))


def _build_portal_context(emp_id):
    """Builds the full employee-portal template context for emp_id, or
    returns None if that employee_id has no employees row. Shared by the
    plain-employee /employee_portal route below AND core.py's /my_workspace
    route (a role-holder viewing their own self-service inside the admin
    shell) -- one query path, one source of truth, so the two experiences
    can never drift apart."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    cursor.execute("""
        SELECT e.employee_id, e.name, e.role, e.email, e.face_image,
               e.date_of_joining,
               sc.salary_per_day AS salary_per_day,
               sh.name AS shift_name, sh.start_time AS shift_start, sh.end_time AS shift_end,
               e.phone, e.gender, e.dob, e.blood_group,
               e.address, e.city, e.state, e.pincode,
               e.emergency_contact_name, e.emergency_contact_phone, e.emergency_contact_relation,
               e.aadhar_number, e.pan_number, e.bank_name, e.bank_account, e.bank_ifsc, e.uan_number,
               e.qr_code, e.work_mode, e.about_me, e.manager_name, e.department,
               e.fingerprint_credential_id
        FROM employees e
        LEFT JOIN salary_config sc ON e.employee_id = sc.employee_id
        LEFT JOIN shifts sh ON e.shift_id = sh.id
        WHERE e.employee_id = %s
    """, (emp_id,))
    _emp_raw = cursor.fetchone()
    if not _emp_raw:
        cursor.close()
        db.close()
        return None
    emp = list(_emp_raw)
    emp[6] = amount(emp[6])              # salary_per_day (encrypted; NULL -> 0)
    # emp indices:
    # [0]=id [1]=name [2]=role [3]=email [4]=face_image [5]=date_of_joining
    # [6]=salary_per_day [7]=shift_name [8]=shift_start [9]=shift_end
    # [10]=phone [11]=gender [12]=dob [13]=blood_group
    # [14]=address [15]=city [16]=state [17]=pincode
    # [18]=emergency_contact_name [19]=emergency_contact_phone [20]=emergency_contact_relation
    # [21]=aadhar_number [22]=pan_number [23]=bank_name [24]=bank_account [25]=bank_ifsc [26]=uan_number
    # [27]=qr_code [28]=work_mode [29]=about_me [30]=manager_name [31]=department
    # [32]=fingerprint_credential_id
    fp_enrolled = bool(emp[32]) if len(emp) > 32 else False
    # Decrypt PII fields: [11]=gender [13]=blood_group [14]=address [15]=city
    # [16]=state [17]=pincode [18]=ec_name [19]=ec_phone [20]=ec_relation
    # [21]=aadhar_number [22]=pan_number [23]=bank_name [24]=bank_account
    # [25]=bank_ifsc [26]=uan_number. [12]=dob handled separately since the
    # template calls .strftime() on it (see decrypt_pii_date).
    for _pii_idx in (11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26):
        if _pii_idx < len(emp):
            emp[_pii_idx] = decrypt_pii(emp[_pii_idx])
    emp[12] = decrypt_pii_date(emp[12])

    today = company_today()
    cursor.execute(
        "SELECT login_time, logout_time, status, logout_status, attendance_type "
        "FROM attendance WHERE employee_id=%s AND date=%s",
        (emp_id, today)
    )
    today_att = cursor.fetchone()

    year = int_param(request.args.get("year", today.year))
    month = int_param(request.args.get("month", today.month))
    _, last_day = calendar.monthrange(year, month)
    cursor.execute("""
        SELECT date, login_time, logout_time, status, logout_status, attendance_type
        FROM attendance
        WHERE employee_id=%s AND date BETWEEN %s AND %s
        ORDER BY date DESC
    """, (emp_id, datetime.date(year, month, 1), datetime.date(year, month, last_day)))
    monthly_att = cursor.fetchall()

    holidays_set = fetch_holidays_set(year, month)
    # Fetch holiday names for attendance calendar tooltips
    cursor.execute(
        "SELECT date, name FROM holidays WHERE date BETWEEN %s AND %s",
        (datetime.date(year, month, 1), datetime.date(year, month, calendar.monthrange(year, month)[1]))
    )
    att_hol_name_map = {row[0]: row[1] for row in cursor.fetchall()}
    billable_past = get_billable_past_days(year, month)
    att_by_date = {r[0]: r for r in monthly_att}
    full_days = half_days = late_days = absent_days = 0
    total_seconds = 0
    for d in billable_past:
        row = att_by_date.get(d)
        if row:
            _, login_t, logout_t, status, _ls, att_type = row
            final = att_type if att_type else infer_type_legacy(status, login_t, logout_t)
            if final in ("Full Day", "Approved Leave"):
                full_days += 1
            elif final == "Late - Full Day":
                late_days += 1
            elif final in ("Half Day", "Present"):
                half_days += 1
            else:
                absent_days += 1
            if login_t and logout_t:
                li = login_t.total_seconds() if hasattr(login_t, "total_seconds") else (
                    login_t.hour * 3600 + login_t.minute * 60 + login_t.second)
                lo = logout_t.total_seconds() if hasattr(logout_t, "total_seconds") else (
                    logout_t.hour * 3600 + logout_t.minute * 60 + logout_t.second)
                if lo > li:
                    total_seconds += int(lo - li)
        else:
            absent_days += 1

    total_hours_str = f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m"
    billable_count = len(billable_past)
    present_equiv = full_days + late_days + half_days * 0.5
    att_pct = round(present_equiv / billable_count * 100, 1) if billable_count else 0

    # Calendar data for JS rendering
    cal_data = {}
    _, month_days = calendar.monthrange(year, month)
    for day in range(1, month_days + 1):
        d = datetime.date(year, month, day)
        if d in holidays_set:
            cal_data[day] = "holiday"
        elif not is_company_working_day(d):
            cal_data[day] = "weekend"
        elif d > today:
            cal_data[day] = "future"
        else:
            row = att_by_date.get(d)
            if row:
                _, login_t, logout_t, status, _ls, att_type = row
                final = att_type if att_type else infer_type_legacy(status, login_t, logout_t)
                if final == "Full Day":
                    cal_data[day] = "full"
                elif final == "Late - Full Day":
                    cal_data[day] = "late"
                elif final in ("Half Day", "Present"):
                    cal_data[day] = "half"
                else:
                    cal_data[day] = "absent"
            else:
                cal_data[day] = "absent"
    cal_hol_names = {d.day: n for d, n in att_hol_name_map.items()}
    cal_year = year
    cal_month = month
    cal_first_dow = datetime.date(year, month, 1).weekday()  # 0=Mon

    cursor.execute("""
        SELECT lr.leave_date, lr.reason, lr.status, lr.created_at,
               COALESCE(lt.name, '') AS leave_type_name, lr.id
        FROM leave_requests lr
        LEFT JOIN leave_types lt ON lr.leave_type_id = lt.id
        WHERE lr.employee_id=%s
        ORDER BY lr.created_at DESC LIMIT 20
    """, (emp_id,))
    my_leaves = cursor.fetchall()

    cursor.execute("""
        SELECT last_working_day, reason, status, created_at
        FROM resignation_requests WHERE employee_id=%s
        ORDER BY created_at DESC LIMIT 1
    """, (emp_id,))
    my_resignation = cursor.fetchone()

    cursor.execute("""
        SELECT id, category, subject, priority, status, admin_response, created_at
        FROM tickets WHERE employee_id=%s
        ORDER BY created_at DESC LIMIT 20
    """, (emp_id,))
    my_tickets = cursor.fetchall()

    # Leave types & per-type balances
    try:
        cursor.execute(
            "SELECT id, name, annual_quota, is_paid FROM leave_types WHERE is_active=1 ORDER BY id"
        )
        leave_types_list = cursor.fetchall()
        # Ensure balances exist for this employee
        assign_leave_balances_for_employee(cursor, emp_id, today.year)
        # Fetch from leave_balances table
        cursor.execute("""
            SELECT lt.id, lt.name, lt.annual_quota, lt.is_paid,
                   COALESCE(lb.total_days, lt.annual_quota) as total,
                   COALESCE(lb.used_days, 0) as used
            FROM leave_types lt
            LEFT JOIN leave_balances lb ON lb.employee_id=%s
                AND lb.leave_type_id=lt.id AND lb.year=%s
            WHERE lt.is_active=1 ORDER BY lt.id
        """, (emp_id, today.year))
        leave_type_balances = []
        annual_leave_quota = 0
        leaves_used = 0
        for lt_id, lt_name, lt_quota, lt_paid, total, used in cursor.fetchall():
            used = float(used or 0)
            total = int(total or lt_quota)
            remaining = max(0, total - used)
            leave_type_balances.append({
                "id": lt_id, "name": lt_name, "quota": total,
                "used": used, "balance": remaining, "is_paid": lt_paid
            })
            annual_leave_quota += total
            leaves_used += used
        leave_balance = max(0, annual_leave_quota - leaves_used)
    except Exception:
        leave_type_balances = []
        annual_leave_quota = 12
        cursor.execute("""
            SELECT COUNT(*) FROM leave_requests
            WHERE employee_id=%s AND EXTRACT(YEAR FROM leave_date)=%s AND status IN ('Approved','Pending')
        """, (emp_id, today.year))
        leaves_used = cursor.fetchone()[0] or 0
        leave_balance = max(0, annual_leave_quota - leaves_used)

    # Announcements for dashboard (public + private addressed to this employee)
    cursor.execute("""
        SELECT id, title, content, priority, created_at, attachment_original_name
        FROM announcements
        WHERE COALESCE(visibility,'public') = 'public'
           OR (visibility = 'private' AND target_employee_id = %s)
        ORDER BY created_at DESC LIMIT 10
    """, (emp_id,))
    announcements = [r[:4] + (coerce_datetime(r[4]),) + r[5:] for r in cursor.fetchall()]

    # Pending leave count for nav badge
    cursor.execute("SELECT COUNT(*) FROM leave_requests WHERE employee_id=%s AND status='Pending'", (emp_id,))
    pending_leaves_count = cursor.fetchone()[0] or 0

    # Open ticket count for nav badge
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE employee_id=%s AND status='Open'", (emp_id,))
    open_tickets_count = cursor.fetchone()[0] or 0

    # Unread notification count for bell icon
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM notifications WHERE recipient_type='employee' AND employee_id=%s AND is_read=FALSE",
            (emp_id,)
        )
        unread_notifications_web = cursor.fetchone()[0] or 0
    except Exception:
        unread_notifications_web = 0

    # Upcoming holidays (next 3 from today) for dashboard widget
    cursor.execute("""
        SELECT date, name FROM holidays WHERE date >= %s ORDER BY date LIMIT 3
    """, (today,))
    upcoming_holidays = cursor.fetchall()

    # Upcoming holidays for leave planning panel (rest of year, up to 15)
    cursor.execute("""
        SELECT date, name FROM holidays
        WHERE date >= %s AND EXTRACT(YEAR FROM date) = %s
        ORDER BY date LIMIT 15
    """, (today, today.year))
    leave_holidays = cursor.fetchall()

    # Holiday list data for employee view
    hol_year = int_param(request.args.get("hol_year", today.year))
    cursor.execute("SELECT id, date, name FROM holidays WHERE EXTRACT(YEAR FROM date)=%s ORDER BY date", (hol_year,))
    hol_rows = cursor.fetchall()

    # Employee's own incentive history
    try:
        cursor.execute("""
            SELECT ig.title, ig.description, ei.month, ei.year, ei.amount, ei.notes, ei.awarded_at
            FROM employee_incentives ei
            JOIN incentive_goals ig ON ei.goal_id = ig.id
            WHERE ei.employee_id = %s
            ORDER BY ei.year DESC, ei.month DESC, ei.awarded_at DESC
        """, (emp_id,))
        my_incentives = cursor.fetchall()
        cursor.execute(   # amount is encrypted: summed here, not in SQL
            "SELECT amount FROM employee_incentives WHERE employee_id=%s AND year=%s",
            (emp_id, today.year)
        )
        total_incentive_year = round(sum(float(amount(r[0])) for r in cursor.fetchall()), 2)
    except Exception:
        my_incentives = []
        total_incentive_year = 0.0

    # Employee work experience & education
    try:
        cursor.execute(
            "SELECT id, company, designation, from_year, to_year, is_current, description "
            "FROM employee_experience WHERE employee_id=%s ORDER BY is_current DESC, from_year DESC",
            (emp_id,)
        )
        my_experience = [
            {"id": r[0], "company": r[1], "designation": r[2], "from_year": r[3],
             "to_year": r[4], "is_current": r[5], "description": r[6]}
            for r in cursor.fetchall()
        ]
    except Exception:
        my_experience = []

    try:
        cursor.execute(
            "SELECT id, degree, institution, year_of_passing, percentage "
            "FROM employee_education WHERE employee_id=%s ORDER BY year_of_passing DESC",
            (emp_id,)
        )
        my_education = [
            {"id": r[0], "degree": r[1], "institution": r[2], "year_of_passing": r[3], "percentage": r[4]}
            for r in cursor.fetchall()
        ]
    except Exception:
        my_education = []

    try:
        cursor.execute(
            "SELECT id, doc_type, original_name, uploaded_by, uploaded_at FROM employee_documents WHERE employee_id=%s ORDER BY uploaded_at DESC",
            (emp_id,)
        )
        my_docs = cursor.fetchall()
    except Exception:
        my_docs = []

    try:
        cursor.execute(
            "SELECT date, shift_end, actual_logout, ot_minutes, ot_pay, status FROM overtime_records "
            "WHERE employee_id=%s AND EXTRACT(YEAR FROM date)=%s ORDER BY date DESC LIMIT 20",
            (emp_id, today.year)
        )
        my_overtime = cursor.fetchall()
    except Exception:
        my_overtime = []

    # Salary summary for Earnings tab: the one net-pay calculation, as it
    # will be paid (QA BUG-05). This card used to pay late days in full, fix
    # half days at 50%, ignore holidays, leave and PF/PT/TDS, and add overtime.
    salary_per_day = float(emp[6]) if emp[6] else 0.0
    from blueprints.payroll import compute_salary_data_for_month
    _pay = (compute_salary_data_for_month(today.year, today.month, employee_ids=[emp_id]) or [{}])[0]
    gross_this_month = _pay.get("attendance_earned", 0.0)
    deduction_this_month = _pay.get("deduction", 0.0)
    incentives_this_month = _pay.get("incentive", 0.0)
    statutory_this_month = _pay.get("statutory", 0.0)
    net_this_month = _pay.get("net", 0.0)
    try:
        cursor.execute(   # ot_pay is encrypted: summed here, not in SQL
            "SELECT ot_pay FROM overtime_records WHERE employee_id=%s "
            "AND EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s AND status='Approved'",
            (emp_id, today.month, today.year)
        )
        ot_pay_this_month = round(sum(float(amount(r[0])) for r in cursor.fetchall()), 2)
    except Exception:
        ot_pay_this_month = 0.0

    # Comp-off balance
    try:
        cursor.execute("SELECT COALESCE(compoff_minutes_per_day,480) FROM company_settings LIMIT 1")
        mpd_row = cursor.fetchone()
        compoff_mpd = int(mpd_row[0]) if mpd_row else 480
        cursor.execute(
            "SELECT COALESCE(earned_minutes,0), COALESCE(used_minutes,0) FROM compoff_balance WHERE employee_id=%s", (emp_id,))
        co_row = cursor.fetchone() or (0, 0)
        compoff_earned_days = round(co_row[0] / compoff_mpd, 1) if compoff_mpd else 0
        compoff_avail_days = round(max(0, co_row[0] - co_row[1]) / compoff_mpd, 1) if compoff_mpd else 0
    except Exception:
        compoff_earned_days = 0
        compoff_avail_days = 0

    cursor.close()
    db.close()

    # Build last 12 months list for pay slips section
    payslip_months = []
    py, pm = today.year, today.month
    for _ in range(12):
        payslip_months.append((py, pm, calendar.month_name[pm]))
        pm -= 1
        if pm == 0:
            pm = 12
            py -= 1

    # Cache-busting suffix for the /my_photo <img> src -- that URL is always
    # the same regardless of which photo is behind it, so the browser (and
    # send_from_directory's own Cache-Control/ETag headers) can keep showing
    # a stale image after update_my_photo() saves a new one over the old
    # file. Tying the query string to the file's own mtime forces a fresh
    # fetch exactly when the photo actually changed, without needing a
    # dedicated "photo updated at" column.
    try:
        _photo_path = resolve_face_photo(emp_id) or ""
        photo_v = int(os.path.getmtime(_photo_path))
    except OSError:
        photo_v = 0

    return {
        "emp": emp,
        "photo_v": photo_v,
        "today_date": today,
        "today": today.strftime("%d %b %Y"),
        "today_long": today.strftime("%A, %d %B %Y"),
        "today_att": today_att,
        "monthly_att": monthly_att,
        "full_days": full_days, "late_days": late_days,
        "half_days": half_days, "absent_days": absent_days,
        "billable": billable_count,
        "my_leaves": my_leaves,
        "my_resignation": my_resignation,
        "my_tickets": my_tickets,
        "leave_sent": request.args.get("leave_sent") == "1",
        "resigned": request.args.get("resigned") == "1",
        "ticket_sent": request.args.get("ticket_sent") == "1",
        "month_name": datetime.date(year, month, 1).strftime("%B %Y"),
        "selected_month": f"{year}-{month:02d}",
        "att_pct": att_pct,
        "total_hours": total_hours_str,
        "cal_data": cal_data,
        "cal_hol_names": cal_hol_names,
        "cal_year": cal_year,
        "cal_month": cal_month,
        "cal_first_dow": cal_first_dow,
        "sel_year": year,
        "sel_month": month,
        "years": list(range(today.year - 2, today.year + 1)),
        "months": [(i, datetime.date(year, i, 1).strftime("%B")) for i in range(1, 13)],
        "payslip_months": payslip_months,
        "leave_balance": leave_balance,
        "leaves_used": leaves_used,
        "annual_leave_quota": annual_leave_quota,
        "leave_type_balances": leave_type_balances,
        "leave_types_for_form": [
            {"id": lt[0], "name": lt[1]}
            for lt in (leave_types_list if leave_types_list else [])
        ],
        "announcements": announcements,
        "pending_leaves_count": pending_leaves_count,
        "open_tickets_count": open_tickets_count,
        "unread_notifications_web": unread_notifications_web,
        "upcoming_holidays": upcoming_holidays,
        "leave_holidays": leave_holidays,
        "hol_year": hol_year,
        "all_holidays_list": hol_rows,
        "my_incentives": my_incentives,
        "total_incentive_year": total_incentive_year,
        "my_experience": my_experience,
        "my_education": my_education,
        "my_docs": my_docs,
        "my_overtime": my_overtime,
        "compoff_avail_days": compoff_avail_days,
        "compoff_earned_days": compoff_earned_days,
        "salary_per_day": salary_per_day,
        "gross_this_month": gross_this_month,
        "deduction_this_month": deduction_this_month,
        "incentives_this_month": incentives_this_month,
        "statutory_this_month": statutory_this_month,
        "ot_pay_this_month": ot_pay_this_month,
        "net_this_month": net_this_month,
        "fp_enrolled": fp_enrolled,
        "fp_enabled": get_auth_config().get("fingerprint_enabled", False),
        "attendance_auth_cfg": get_auth_config(),
    }


@employee_portal_bp.route("/employee_portal")
@employee_required
def employee_portal():
    """Plain-employee entry point: builds the shared context for the logged-in
    employee and renders the standalone portal shell (own header/sidebar)."""
    ctx = _build_portal_context(session["employee_id"])
    if ctx is None:
        session.clear()
        return redirect(tpath("/login"))
    return render_template("employee_portal.html", portal_base_path=tpath("/employee_portal"), **ctx)


@employee_portal_bp.route("/api/employee/change-password", methods=["POST"])
@employee_api_required
def api_employee_change_password():
    data = request.get_json() or {}
    current_password = data.get("current_password", "").strip()
    new_password = data.get("new_password", "").strip()
    if not current_password or not new_password:
        return jsonify({"ok": False, "msg": "current_password and new_password required"}), 400
    _pw_ok, _pw_err = validate_new_password(new_password)
    if not _pw_ok:
        return jsonify({"ok": False, "msg": _pw_err}), 400
    from flask import g as _g
    from utils.auth import verify_and_update_password
    emp_id = _g.api_emp_id
    if not verify_and_update_password("employees", "employee_id", emp_id, current_password, new_password):
        return jsonify({"ok": False, "msg": "Current password is incorrect"}), 401
    return jsonify({"ok": True, "msg": "Password changed successfully"})


@employee_portal_bp.route("/api/employee/portal", methods=["GET"])
def api_employee_portal():
    from flask import g as _g
    # A session only counts if it passes the same per-request checks the
    # decorators apply (kill switch, account active, password unchanged).
    emp_id = (session.get("employee_id") if session_account_ok() else None) or getattr(_g, "api_emp_id", None)
    if not emp_id:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            from utils.auth import _hash_token, _TOKEN_NOT_STALE_SQL, _employment_ended_sql, access_cutoff_date
            token_hash = _hash_token(auth[7:])
            with _db() as (cursor, _conn):
                cursor.execute(
                    "SELECT t.identity FROM api_tokens t WHERE t.token=%s AND t.token_type='employee' "
                    f"AND t.expires_at > NOW() AND {_TOKEN_NOT_STALE_SQL} "
                    f"AND NOT {_employment_ended_sql('t.identity')}",  # nosec B608 -- fixed module constants
                    (token_hash, access_cutoff_date())
                )
                row = cursor.fetchone()
                if row:
                    emp_id = row[0]
    if not emp_id:
        return jsonify({"ok": False, "msg": "Unauthorized"}), 401

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()

    cursor.execute("""
        SELECT e.name, e.email, COALESCE(c.name, (SELECT company_name FROM company_settings LIMIT 1), '') AS company_name
        FROM employees e
        LEFT JOIN companies c ON e.company_id = c.id
        WHERE e.employee_id=%s
    """, (emp_id,))
    emp = cursor.fetchone()

    cursor.execute(
        "SELECT login_time, logout_time, status, logout_status, attendance_type "
        "FROM attendance WHERE employee_id=%s AND date=%s", (emp_id, today)
    )
    att = cursor.fetchone()

    cursor.execute("""
        SELECT date, login_time, logout_time, status, logout_status, attendance_type
        FROM attendance WHERE employee_id=%s AND date >= %s
        ORDER BY date DESC LIMIT 10
    """, (emp_id, today - datetime.timedelta(days=30)))
    recent = cursor.fetchall()

    cursor.execute(
        "SELECT leave_date, reason, status, created_at FROM leave_requests "
        "WHERE employee_id=%s ORDER BY created_at DESC LIMIT 5", (emp_id,)
    )
    leaves = cursor.fetchall()

    cursor.execute(
        "SELECT last_working_day, reason, status, created_at FROM resignation_requests "
        "WHERE employee_id=%s ORDER BY created_at DESC LIMIT 1", (emp_id,)
    )
    resign = cursor.fetchone()
    cursor.execute(
        "SELECT COUNT(*) FROM notifications WHERE recipient_type='employee' AND employee_id=%s AND is_read=FALSE",
        (emp_id,)
    )
    unread_notifications = cursor.fetchone()[0]
    cursor.execute("""
        SELECT title, content, priority, created_at FROM announcements
        WHERE COALESCE(visibility,'public') = 'public'
           OR (visibility = 'private' AND target_employee_id = %s)
        ORDER BY created_at DESC LIMIT 5
    """, (emp_id,))
    ann_rows = cursor.fetchall()
    cursor.execute("SELECT role, department FROM employees WHERE employee_id=%s", (emp_id,))
    emp_extra = cursor.fetchone()
    cursor.close()
    db.close()

    return jsonify({
        "ok": True,
        "employee_id": emp_id,
        "name": emp[0] if emp else emp_id,
        "email": emp[1] if emp else None,
        "company_name": emp[2] if emp else "",
        "today": today.strftime("%d %b %Y"),
        "today_attendance": {
            "login_time": _fmt_t(att[0]),
            "logout_time": _fmt_t(att[1]),
            "login_status": att[2],
            "logout_status": att[3],
            "attendance_type": att[4],
        } if att else None,
        "recent_attendance": [
            {"date": str(r[0]), "login_time": _fmt_t(r[1]), "logout_time": _fmt_t(r[2]),
             "login_status": r[3], "logout_status": r[4], "attendance_type": r[5]}
            for r in recent
        ],
        "recent_leaves": [
            {"leave_date": str(r[0]), "reason": r[1], "status": r[2],
             "requested_at": str(r[3])}
            for r in leaves
        ],
        "resignation": {
            "last_working_day": str(resign[0]),
            "reason": resign[1],
            "status": resign[2],
            "created_at": str(resign[3]),
        } if resign else None,
        "unread_notifications": unread_notifications,
        "role": emp_extra[0] if emp_extra else None,
        "department": emp_extra[1] if emp_extra else None,
        "announcements": [
            {"title": r[0], "content": r[1], "priority": r[2], "created_at": str(r[3])}
            for r in ann_rows
        ],
    })
