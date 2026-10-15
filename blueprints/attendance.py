# -*- coding: utf-8 -*-
"""Attendance blueprint -- check-in/out, shifts, breaks, reports.

Bandit B608 audit note: the nosec-marked queries below interpolate `_co`/
`_args`, a company-scoping fragment that's always a hardcoded literal chosen
by a bool (`"AND e.company_id=%s" if active_cid else ""`) -- never user
input. Actual values are always %s-bound params.
"""
import os
import datetime
import calendar
import io as _io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from flask import (
    Blueprint, request, session, redirect, jsonify, render_template, flash, g,
)
from extensions import limiter, app_log, log_security_event
from database import get_db_connection
from utils.auth import admin_required, api_required, resolve_bearer_identity_any, HR_ROLE
from utils.helpers import (
    tpath, get_auth_config, allowed_checkin_combos, get_company_settings, _safe_redirect, _safe_referrer_redirect,
    co_scope_column, hr_scope_column, hr_scope_denied, decrypt_pii, get_pending_action_counts,
    company_today, company_now,
)
from utils.email_utils import get_email_config, send_email_smtp
from utils.attendance_utils import (
    classify_by_worked_minutes, detect_overtime, get_working_days,
    fetch_holidays_set, get_employee_shift, _td_to_time, infer_type_legacy,
    is_within_range, is_within_office_range,
    check_attendance_lockout, record_attendance_failure, clear_attendance_lockout,
    geofence_check_error, compute_session_worked_minutes, fetch_employee_work_location,
)
from utils.face_utils import face_recognition, _face_recognition_available, _get_known_face_encoding
from utils.webauthn_utils import _wa_fingerprint_recently_verified
import utils.config as cfg
from utils import punch_integrity as pi

attendance_bp = Blueprint("attendance", __name__)


def _today_pending_counts(cursor):
    """Sidebar badge counts, shared by today_present/today_absent/today_late."""
    return get_pending_action_counts(cursor)


@attendance_bp.route("/today_present")
@admin_required
def today_present():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()
    active_cid = session.get("active_company_id")
    _co, _co_args = co_scope_column(active_cid, alias="e")
    _hr, _hr_args = hr_scope_column(alias="e")
    _args = (today,) + _co_args + _hr_args
    cursor.execute(f"""
        SELECT e.employee_id, e.name, e.role, a.login_time, a.logout_time,
               a.status, a.logout_status, a.attendance_type
        FROM employees e
        JOIN attendance a ON e.employee_id = a.employee_id AND a.date = %s
        WHERE a.login_time IS NOT NULL {_co} {_hr}
        ORDER BY a.login_time
    """, _args)  # nosec B608
    rows = cursor.fetchall()
    pl, pr, pt = _today_pending_counts(cursor)
    cursor.close()
    db.close()
    return render_template("today_attendance.html",
                           filter_type="present", title="Present Today",
                           rows=rows, today=today.strftime("%d %b %Y"),
                           pending_leaves=pl, pending_resignations=pr, pending_tickets=pt)


@attendance_bp.route("/today_absent")
@admin_required
def today_absent():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()
    active_cid = session.get("active_company_id")
    _co, _co_args = co_scope_column(active_cid, alias="e")
    _hr, _hr_args = hr_scope_column(alias="e")
    _args = (today,) + _co_args + _hr_args
    cursor.execute(f"""
        SELECT e.employee_id, e.name, e.role
        FROM employees e
        LEFT JOIN attendance a ON e.employee_id = a.employee_id AND a.date = %s
        WHERE a.employee_id IS NULL {_co} {_hr}
        ORDER BY e.name
    """, _args)  # nosec B608
    rows = cursor.fetchall()
    pl, pr, pt = _today_pending_counts(cursor)
    cursor.close()
    db.close()
    return render_template("today_attendance.html",
                           filter_type="absent", title="Absent Today",
                           rows=rows, today=today.strftime("%d %b %Y"),
                           pending_leaves=pl, pending_resignations=pr, pending_tickets=pt)


@attendance_bp.route("/today_late")
@admin_required
def today_late():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    today = company_today()
    active_cid = session.get("active_company_id")
    _co, _co_args = co_scope_column(active_cid, alias="e")
    _hr, _hr_args = hr_scope_column(alias="e")
    _args = (today,) + _co_args + _hr_args
    cursor.execute(f"""
        SELECT e.employee_id, e.name, e.role, a.login_time, a.status
        FROM employees e
        JOIN attendance a ON e.employee_id = a.employee_id AND a.date = %s
        WHERE a.status IN ('Late Login', 'Half Day Login') {_co} {_hr}
        ORDER BY a.login_time
    """, _args)  # nosec B608
    rows = cursor.fetchall()
    pl, pr, pt = _today_pending_counts(cursor)
    cursor.close()
    db.close()
    return render_template("today_attendance.html",
                           filter_type="late", title="Late Logins Today",
                           rows=rows, today=today.strftime("%d %b %Y"),
                           pending_leaves=pl, pending_resignations=pr, pending_tickets=pt)


@attendance_bp.route("/shifts", methods=["GET"])
@admin_required
def shifts():
    return redirect(tpath("/settings?tab=shifts"))


@attendance_bp.route("/add_shift", methods=["POST"])
@admin_required
def add_shift():
    name = (request.form.get("shift_name") or request.form.get("name", "")).strip()
    start = request.form.get("start_time", "").strip()
    half = request.form.get("half_time", "").strip()
    end = request.form.get("end_time", "").strip()
    dest = request.form.get("redirect") or (
        "/settings?tab=shifts" if request.form.get("redirect_to") == "settings" else "/settings?tab=shifts")
    cid_raw = request.form.get("company_id", "").strip()
    company_id = int(cid_raw) if cid_raw.isdigit() else None
    if not all([name, start, half, end]):
        return redirect(dest)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        if company_id:
            cursor.execute(
                "INSERT INTO shifts (name, start_time, half_time, end_time, company_id) VALUES (%s,%s,%s,%s,%s)",
                (name, start, half, end, company_id)
            )
        else:
            cursor.execute(
                "INSERT INTO shifts (name, start_time, half_time, end_time) VALUES (%s,%s,%s,%s)",
                (name, start, half, end)
            )
        db.commit()
    except Exception as exc:
        # Previously silent -- the admin got redirected back with no
        # indication the shift was never actually created.
        app_log.warning("create_shift failed for '%s': %s", name, exc, exc_info=True)
        flash("Could not create shift. Please try again.", "error")
    cursor.close()
    db.close()
    return redirect(dest)


@attendance_bp.route("/delete_shift/<int:sid>", methods=["POST"])
@admin_required
def delete_shift(sid):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE employees SET shift_id=NULL WHERE shift_id=%s", (sid,))
    cursor.execute("UPDATE break_config SET shift_id=NULL WHERE shift_id=%s", (sid,))
    cursor.execute("DELETE FROM shifts WHERE id=%s", (sid,))
    db.commit()
    cursor.close()
    db.close()
    dest = request.form.get("redirect") or "/settings?tab=shifts"
    return redirect(dest)


@attendance_bp.route("/edit_shift/<int:sid>", methods=["POST"])
@admin_required
def edit_shift(sid):
    name = (request.form.get("shift_name") or request.form.get("name", "")).strip()
    start = request.form.get("start_time", "").strip()
    half = request.form.get("half_time", "").strip()
    end = request.form.get("end_time", "").strip()
    dest = request.form.get("redirect") or "/settings?tab=shifts"
    if not all([name, start, half, end]):
        return redirect(dest)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE shifts SET name=%s, start_time=%s, half_time=%s, end_time=%s WHERE id=%s",
        (name, start, half, end, sid)
    )
    db.commit()
    cursor.close()
    db.close()
    return redirect(dest)


@attendance_bp.route("/bulk_assign_shift", methods=["POST"])
@admin_required
def bulk_assign_shift():
    shift_id = request.form.get("shift_id", "").strip()
    emp_ids = request.form.getlist("emp_ids")
    dept_filter = request.form.get("dept_filter", "").strip()
    dest = request.form.get("redirect") or "/employees?tab=schedule"
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    if emp_ids:
        cursor.execute(
            "UPDATE employees SET shift_id=%s WHERE employee_id = ANY(%s)",
            (shift_id if shift_id else None, emp_ids)
        )
    elif dept_filter:
        cursor.execute(
            "UPDATE employees SET shift_id=%s WHERE department=%s",
            (shift_id if shift_id else None, dept_filter)
        )
    else:
        cursor.execute("UPDATE employees SET shift_id=%s", (shift_id if shift_id else None,))
    db.commit()
    cursor.close()
    db.close()
    return redirect(dest)


@attendance_bp.route("/update_default_shift", methods=["POST"])
@admin_required
def update_default_shift():
    # No `global` needed -- cfg.SHIFT_START/HALF/END live in utils.config now;
    # cfg.load_default_shift() below mutates that module's own globals via
    # its own correctly-scoped `global` statement, not this function's.
    start = request.form.get("shift_start", "").strip()
    half = request.form.get("shift_half", "").strip()
    end = request.form.get("shift_end", "").strip()
    if not all([start, half, end]):
        return redirect(tpath("/shifts?error=All+fields+required"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE company_settings SET shift_start=%s, shift_half=%s, shift_end=%s",
        (start, half, end)
    )
    db.commit()
    cursor.close()
    db.close()
    cfg.load_default_shift()
    return redirect(tpath("/shifts?default_saved=1"))


@attendance_bp.route("/assign_shift", methods=["POST"])
@admin_required
def assign_shift():
    emp_id = request.form.get("emp_id", "").strip()
    shift_id = request.form.get("shift_id", "").strip()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE employees SET shift_id=%s WHERE employee_id=%s",
        (shift_id if shift_id else None, emp_id)
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True})


@attendance_bp.route("/api/breaks")
@limiter.limit("30 per minute")
def api_breaks():
    # Was checking only that an Authorization header starts with "Bearer "
    # -- never looked the token up against api_tokens at all, so literally
    # any string ("Bearer x") passed for an unauthenticated caller. Now
    # shares the same real token lookup blueprints/ai_hrms.py's helpdesk
    # route uses (identity for either an employee or admin token, expiry
    # checked) instead of a session cookie OR a merely-shaped header.
    if not (session.get("admin_logged_in") or session.get("employee_id")
            or resolve_bearer_identity_any()):
        return jsonify({"ok": False, "msg": "Unauthorized"}), 401
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT id, break_name, break_time, duration_minutes FROM break_config WHERE is_active=1 ORDER BY break_time")
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    result = []
    for row in rows:
        bt = row[2]
        if hasattr(bt, 'seconds'):
            total = bt.seconds
            h, m = divmod(total // 60, 60)
        elif isinstance(bt, str):
            # SQLite fallback stores TIME columns as plain "HH:MM[:SS]"
            # text -- Postgres never takes this branch (psycopg2 returns
            # a timedelta or datetime.time for a real TIME column).
            h, m = (int(x) for x in bt.split(':')[:2])
        else:
            h, m = bt.hour, bt.minute
        result.append({"id": row[0], "name": row[1],
                       "hour": h, "minute": m,
                       "duration": row[3]})
    return jsonify(result)


@attendance_bp.route("/break_config")
@admin_required
def view_break_config():
    return redirect(tpath("/settings?tab=shifts"))


@attendance_bp.route("/add_break", methods=["POST"])
@admin_required
def add_break():
    name = request.form.get("break_name", "").strip()
    btime = request.form.get("break_time", "").strip()
    duration = int(request.form.get("duration_minutes", 10) or 10)
    dest = _safe_redirect(request.form.get("redirect", ""), _safe_referrer_redirect(
        request.referrer or "", "/employees?tab=schedule"))
    cid_raw = request.form.get("company_id", "").strip()
    company_id = int(cid_raw) if cid_raw.isdigit() else None
    sid_raw = request.form.get("shift_id", "").strip()
    shift_id = int(sid_raw) if sid_raw.isdigit() else None
    if not name or not btime:
        flash("Break name and time are required.", "error")
        return redirect(dest)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    if company_id:
        cursor.execute(
            "INSERT INTO break_config (break_name, break_time, duration_minutes, company_id, shift_id) VALUES (%s,%s,%s,%s,%s)",
            (name, btime, duration, company_id, shift_id)
        )
    else:
        cursor.execute("INSERT INTO break_config (break_name, break_time, duration_minutes, shift_id) VALUES (%s,%s,%s,%s)",
                       (name, btime, duration, shift_id))
    db.commit()
    cursor.close()
    db.close()
    flash("Break added successfully.", "success")
    return redirect(dest)


@attendance_bp.route("/update_break/<int:bid>", methods=["POST"])
@admin_required
def update_break(bid):
    name = request.form.get("break_name", "").strip()
    btime = request.form.get("break_time", "").strip()
    duration = int(request.form.get("duration_minutes", 10) or 10)
    active = 1 if request.form.get("is_active") else 0
    dest = _safe_redirect(request.form.get("redirect", ""), _safe_referrer_redirect(
        request.referrer or "", "/employees?tab=schedule"))
    sid_raw = request.form.get("shift_id", "").strip()
    if not name or not btime:
        flash("Break name and time are required.", "error")
        return redirect(dest)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    if sid_raw.isdigit():
        cursor.execute(
            "UPDATE break_config SET break_name=%s, break_time=%s, duration_minutes=%s, is_active=%s, shift_id=%s WHERE id=%s",
            (name, btime, duration, active, int(sid_raw), bid)
        )
    else:
        cursor.execute(
            "UPDATE break_config SET break_name=%s, break_time=%s, duration_minutes=%s, is_active=%s WHERE id=%s",
            (name, btime, duration, active, bid)
        )
    db.commit()
    cursor.close()
    db.close()
    flash("Break updated.", "success")
    return redirect(dest)


@attendance_bp.route("/delete_break/<int:bid>", methods=["POST"])
@admin_required
def delete_break(bid):
    dest = request.form.get("redirect") or "/employees?tab=schedule"
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM break_config WHERE id=%s", (bid,))
    db.commit()
    cursor.close()
    db.close()
    flash("Break deleted.", "success")
    return redirect(dest)


@attendance_bp.route("/monthly_report")
@admin_required
def monthly_report():
    year = int(request.args.get("year", datetime.date.today().year))
    month = int(request.args.get("month", datetime.date.today().month))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    active_cid = session.get("active_company_id")
    _co, _co_args = co_scope_column(active_cid)
    _hr, _hr_args = hr_scope_column()
    cursor.execute(
        f"SELECT employee_id, name, COALESCE(role,''), COALESCE(phone,''), COALESCE(email,'') "  # nosec B608
        f"FROM employees WHERE 1=1 {_co} {_hr} ORDER BY name",
        _co_args + _hr_args)
    employees = cursor.fetchall()

    _, last_day = calendar.monthrange(year, month)
    cursor.execute("""
        SELECT employee_id, date, login_time, logout_time, status, logout_status, attendance_type
        FROM attendance
        WHERE date BETWEEN %s AND %s
    """, (datetime.date(year, month, 1), datetime.date(year, month, last_day)))

    att_map = {}
    for row in cursor.fetchall():
        att_map.setdefault(row[0], {})[row[1]] = row

    holidays = fetch_holidays_set(year, month)
    working_days = get_working_days(year, month)
    today = company_today()

    report = []
    for emp_id, name, role, phone, email in employees:
        emp_att = att_map.get(emp_id, {})
        full_days = half_days = late_days = absent = 0

        for d in working_days:
            if d > today or d in holidays:
                continue
            row = emp_att.get(d)
            if row:
                _, _, login_t, logout_t, status, _logout_status, att_type = row
                final = att_type if att_type else infer_type_legacy(status, login_t, logout_t)
                if final == "Full Day":
                    full_days += 1
                elif final == "Late - Full Day":
                    late_days += 1
                elif final in ("Half Day", "Present"):
                    half_days += 1
                else:
                    absent += 1
            else:
                absent += 1

        billable = len([d for d in working_days if d <= today and d not in holidays])
        present_equiv = full_days + late_days + half_days * 0.5
        pct = round(present_equiv / billable * 100, 1) if billable > 0 else 0

        report.append({
            "emp_id": emp_id,
            "name": name,
            "role": role,
            "phone": phone,
            "email": email,
            "full_days": full_days,
            "half_days": half_days,
            "late_days": late_days,
            "absent": absent,
            "billable": billable,
            "pct": pct,
        })

    cursor.close()
    db.close()

    months = [(i, datetime.date(year, i, 1).strftime("%B")) for i in range(1, 13)]
    years = list(range(datetime.date.today().year - 2, datetime.date.today().year + 1))

    return render_template("monthly_report.html",
                           report=report,
                           month_name=datetime.date(year, month, 1).strftime("%B %Y"),
                           active_nav="attendance",
                           year=year, month=month,
                           months=months, years=years,
                           holiday_count=len(holidays),
                           total_working=len([d for d in working_days if d <= today and d not in holidays]),
                           )


@attendance_bp.route("/employee_attendance_detail/<emp_id>/<int:year>/<int:month>")
@admin_required
def employee_attendance_detail(emp_id, year, month):
    if hr_scope_denied(emp_id):
        return "Employee not found", 404
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    cursor.execute(
        "SELECT employee_id, name, COALESCE(role,''), COALESCE(phone,''), COALESCE(email,'') "
        "FROM employees WHERE employee_id = %s", (emp_id,)
    )
    emp = cursor.fetchone()
    if not emp:
        cursor.close()
        db.close()
        return "Employee not found", 404

    _, last_day = calendar.monthrange(year, month)
    cursor.execute("""
        SELECT date, login_time, logout_time, status, logout_status, attendance_type
        FROM attendance
        WHERE employee_id = %s AND date BETWEEN %s AND %s
        ORDER BY date
    """, (emp_id, datetime.date(year, month, 1), datetime.date(year, month, last_day)))
    att_map = {row[0]: row for row in cursor.fetchall()}

    holidays_set = fetch_holidays_set(year, month)
    today = company_today()

    # Informational only -- which dates this employee is currently locked
    # out of online check-in for (utils/attendance_utils.py). Table may not
    # exist yet on an older, not-yet-migrated tenant schema.
    locked_dates = set()
    try:
        cursor.execute(
            "SELECT date FROM attendance_lockouts WHERE employee_id=%s AND locked=1 "
            "AND date BETWEEN %s AND %s",
            (emp_id, datetime.date(year, month, 1), datetime.date(year, month, last_day))
        )
        locked_dates = {r[0] for r in cursor.fetchall()}
    except Exception as exc:
        # debug, not warning: expected and harmless on an older tenant
        # schema that predates the attendance_lockouts table.
        app_log.debug("attendance_lockouts lookup failed for %s (%s/%s): %s", emp_id, year, month, exc)

    days = []
    full_days = half_days = late_days = absent = 0
    for d in range(1, last_day + 1):
        date = datetime.date(year, month, d)
        is_sunday = date.weekday() == 6
        is_holiday = date in holidays_set
        is_future = date > today
        row = att_map.get(date)

        if row:
            _, login_t, logout_t, status, logout_status, att_type = row
            final = att_type if att_type else infer_type_legacy(status, login_t, logout_t)
            login_str = _td_to_time(login_t).strftime("%I:%M %p") if login_t else "--"
            logout_str = _td_to_time(logout_t).strftime("%I:%M %p") if logout_t else "--"
            if not is_future:
                if final == "Full Day":
                    full_days += 1
                elif final == "Late - Full Day":
                    late_days += 1
                elif final in ("Half Day", "Present"):
                    half_days += 1
                else:
                    absent += 1
        else:
            final = "--"
            login_str = "--"
            logout_str = "--"
            if not is_sunday and not is_holiday and not is_future:
                absent += 1

        days.append({
            "date": date,
            "day_name": date.strftime("%a"),
            "login": login_str,
            "logout": logout_str,
            "status": final,
            "is_sunday": is_sunday,
            "is_holiday": is_holiday,
            "is_future": is_future,
            "is_locked": date in locked_dates,
        })

    cursor.close()
    db.close()

    months = [(i, datetime.date(year, i, 1).strftime("%B")) for i in range(1, 13)]
    years = list(range(datetime.date.today().year - 2, datetime.date.today().year + 1))

    return render_template("employee_attendance_detail.html",
                           emp=emp,
                           days=days,
                           month_name=datetime.date(year, month, 1).strftime("%B %Y"),
                           active_nav="attendance",
                           year=year, month=month,
                           months=months, years=years,
                           full_days=full_days,
                           late_days=late_days,
                           half_days=half_days,
                           absent=absent,
                           )


@attendance_bp.route("/correct_attendance", methods=["POST"])
@admin_required
def correct_attendance():
    emp_id = request.form.get("emp_id", "").strip()
    date_str = request.form.get("date", "").strip()
    login_str = request.form.get("login_time", "").strip()
    logout_str = request.form.get("logout_time", "").strip()
    att_type = request.form.get("attendance_type", "").strip()
    year = request.form.get("year", "")
    month = request.form.get("month", "")

    if not emp_id or not date_str or not att_type:
        flash("Missing required fields.", "error")
        return redirect(_safe_referrer_redirect(request.referrer or "", "/monthly_report"))

    if hr_scope_denied(emp_id):
        flash("Employee not found.", "error")
        return redirect(_safe_referrer_redirect(request.referrer or "", "/monthly_report"))

    try:
        date_obj = datetime.date.fromisoformat(date_str)
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(_safe_referrer_redirect(request.referrer or "", "/monthly_report"))

    login_time = login_str if login_str else None
    logout_time = logout_str if logout_str else None

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id FROM attendance WHERE employee_id=%s AND date=%s",
        (emp_id, date_obj)
    )
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            "UPDATE attendance SET login_time=%s, logout_time=%s, attendance_type=%s, "
            "status='Manual', logout_status='Manual' WHERE employee_id=%s AND date=%s",
            (login_time, logout_time, att_type, emp_id, date_obj)
        )
    else:
        cursor.execute(
            "INSERT INTO attendance (employee_id, date, login_time, logout_time, "
            "attendance_type, status, logout_status) VALUES (%s,%s,%s,%s,%s,'Manual','Manual')",
            (emp_id, date_obj, login_time, logout_time, att_type)
        )
    db.commit()
    cursor.close()
    db.close()

    # An admin manually marking attendance is itself the unlock action for
    # utils/attendance_utils.py's failed-check-in lockout -- no separate
    # unlock UI needed.
    clear_attendance_lockout(emp_id, date_obj, session.get("admin_username"))

    flash(f"Attendance updated for {date_obj.strftime('%d %b %Y')}.", "success")
    return redirect(tpath(f"/employee_attendance_detail/{emp_id}/{year}/{month}"))


@attendance_bp.route("/bulk_mark_attendance", methods=["GET", "POST"])
@admin_required
def bulk_mark_attendance():
    today = datetime.date.today()

    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        try:
            date_obj = datetime.date.fromisoformat(date_str)
        except ValueError:
            flash("Invalid date.", "error")
            return redirect(tpath("/bulk_mark_attendance"))

        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        _hr, _hr_args = hr_scope_column()
        cursor.execute(f"SELECT employee_id FROM employees WHERE is_active=1 {_hr}", _hr_args)  # nosec B608
        emp_ids = [r[0] for r in cursor.fetchall()]

        rows = []
        for eid in emp_ids:
            att_type = request.form.get(f"att_{eid}", "").strip()
            if not att_type:
                continue
            login_t = request.form.get(f"login_{eid}", "").strip() or None
            logout_t = request.form.get(f"logout_{eid}", "").strip() or None
            rows.append((eid, date_obj, login_t, logout_t, att_type))

        # Single multi-row upsert instead of one INSERT round trip per
        # employee -- placeholders are a fixed repeated pattern sized off
        # len(rows), never user input, so this stays parameterized (%s).
        saved = len(rows)
        if rows:
            placeholders = ",".join(["(%s,%s,%s,%s,%s,'Manual','Manual')"] * len(rows))
            params = [v for row in rows for v in row]
            cursor.execute(
                "INSERT INTO attendance (employee_id, date, login_time, logout_time, "  # nosec B608
                f"attendance_type, status, logout_status) VALUES {placeholders} "
                "ON CONFLICT (employee_id, date) DO UPDATE SET "
                "login_time=EXCLUDED.login_time, logout_time=EXCLUDED.logout_time, "
                "attendance_type=EXCLUDED.attendance_type, status='Manual', logout_status='Manual'",
                params
            )
        db.commit()
        cursor.close()
        db.close()

        # Same unlock-on-manual-mark rule as correct_attendance() above.
        admin_username = session.get("admin_username")
        for eid, _date, _login, _logout, _att_type in rows:
            clear_attendance_lockout(eid, date_obj, admin_username)

        flash(f"Attendance saved for {saved} employee(s) on {date_obj.strftime('%d %b %Y')}.", "success")
        return redirect(tpath(f"/bulk_mark_attendance?date={date_str}"))

    date_str = request.args.get("date", today.isoformat())
    try:
        date_obj = datetime.date.fromisoformat(date_str)
    except ValueError:
        date_obj = today
        date_str = today.isoformat()

    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    base_select = (
        "SELECT e.employee_id, e.name, COALESCE(e.department,''), COALESCE(e.designation,''), "
        "COALESCE(s.name,''), COALESCE(TO_CHAR(s.start_time,'HH24:MI'),''), "
        "COALESCE(TO_CHAR(s.end_time,'HH24:MI'),''), "
        "COALESCE(e.phone,''), COALESCE(e.email,''), "
        "COALESCE(e.work_mode,'office'), COALESCE(TO_CHAR(e.date_of_joining,'YYYY-MM-DD'),''), "
        "COALESCE(e.gender,''), COALESCE(e.role,'') "
        "FROM employees e LEFT JOIN shifts s ON s.id=e.shift_id "
    )
    active_cid = session.get("active_company_id")
    _co, _co_args = co_scope_column(active_cid, alias="e")
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(  # nosec B608
        base_select + f"WHERE e.is_active=1 {_co} {_hr} ORDER BY e.name",
        _co_args + _hr_args)
    # [11]=gender is Fernet-encrypted at rest -- decrypt before display.
    employees = [row[:11] + (decrypt_pii(row[11]),) + row[12:] for row in cursor.fetchall()]

    cursor.execute(
        "SELECT employee_id, login_time, logout_time, attendance_type "
        "FROM attendance WHERE date=%s", (date_obj,)
    )
    att_map = {r[0]: r for r in cursor.fetchall()}

    # Informational only -- which employees are currently locked out of
    # online check-in for this date (utils/attendance_utils.py's failed
    # check-in lockout, Medium/Prime plans). Table may not exist yet on an
    # older tenant schema that hasn't been migrated, hence the try/except.
    locked_employee_ids = set()
    try:
        cursor.execute(
            "SELECT employee_id FROM attendance_lockouts WHERE date=%s AND locked=1", (date_obj,)
        )
        locked_employee_ids = {r[0] for r in cursor.fetchall()}
    except Exception as exc:
        app_log.debug("attendance_lockouts lookup failed for date=%s: %s", date_obj, exc)

    # Monthly summary for the selected date's month
    cursor.execute(
        """SELECT employee_id,
             SUM(CASE WHEN attendance_type IN ('Full Day','Late - Full Day') THEN 1 ELSE 0 END) AS present_days,
             SUM(CASE WHEN attendance_type = 'Half Day' THEN 1 ELSE 0 END) AS half_days,
             SUM(CASE WHEN attendance_type = 'Absent' THEN 1 ELSE 0 END) AS absent_days,
             SUM(CASE WHEN attendance_type = 'Leave' THEN 1 ELSE 0 END) AS leave_days,
             COUNT(*) AS total_marked
           FROM attendance
           WHERE EXTRACT(YEAR FROM date)=%s AND EXTRACT(MONTH FROM date)=%s
           GROUP BY employee_id""",
        (date_obj.year, date_obj.month)
    )
    month_summary = {r[0]: r for r in cursor.fetchall()}

    co = get_company_settings()
    pending_leaves = 0
    pending_resignations = 0
    pending_tickets = 0
    try:
        pending_leaves, pending_resignations, pending_tickets = get_pending_action_counts(cursor, tickets_open_only=True)
    except Exception as exc:
        # Counts stay at 0 -- misleadingly implies nothing pending, worth
        # knowing since it directly affects what admins see on this page.
        app_log.warning("get_pending_action_counts failed: %s", exc, exc_info=True)
    cursor.close()
    db.close()

    return render_template("bulk_attendance.html",
                           co=co, employees=employees, att_map=att_map,
                           month_summary=month_summary,
                           date_str=date_str, date_obj=date_obj,
                           today=today, pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           active_nav="attendance",
                           locked_employee_ids=locked_employee_ids,
                           )


def _api_hr_username():
    """Bearer-token counterpart of hr_scope_column()'s session check: the
    caller's username if its admin_users.role is HR, else None. The session-
    based hr_scope_*() helpers no-op for a Bearer caller (no session), so the
    API twins below need their own way to apply the same HR scoping."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT role FROM admin_users WHERE username=%s", (g.api_user,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    return g.api_user if row and row[0] == HR_ROLE else None


@attendance_bp.route("/api/bulk_mark_attendance", methods=["GET", "POST"])
@api_required
def api_bulk_mark_attendance():
    today = datetime.date.today()
    if request.method == "POST":
        data = request.get_json() or {}
        date_str = data.get("date", "").strip() or today.isoformat()
        records = data.get("records", [])
        try:
            date_obj = datetime.date.fromisoformat(date_str)
        except ValueError:
            return jsonify({"ok": False, "msg": "Invalid date format."}), 400

        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        # Same scope the web bulk_mark_attendance() gets from hr_scope_column():
        # active employees only, and for an HR caller only its assigned ones.
        # Without this, any admin-side token could write attendance rows for
        # arbitrary (even nonexistent) employee IDs.
        _hr_user = _api_hr_username()
        if _hr_user:
            cursor.execute("SELECT employee_id FROM employees WHERE is_active=1 AND assigned_hr_username=%s", (_hr_user,))
        else:
            cursor.execute("SELECT employee_id FROM employees WHERE is_active=1")
        allowed_ids = {row[0] for row in cursor.fetchall()}
        rows = []
        for r in records:
            eid = r.get("emp_id") or r.get("employee_id")
            att_type = r.get("attendance_type", "").strip()
            if not eid or not att_type or eid not in allowed_ids:
                continue
            login_t = r.get("login_time") or None
            logout_t = r.get("logout_time") or None
            rows.append((eid, date_obj, login_t, logout_t, att_type))

        saved = len(rows)
        if rows:
            placeholders = ",".join(["(%s,%s,%s,%s,%s,'Manual','Manual')"] * len(rows))
            params = [v for row in rows for v in row]
            cursor.execute(
                "INSERT INTO attendance (employee_id, date, login_time, logout_time, "  # nosec B608
                f"attendance_type, status, logout_status) VALUES {placeholders} "
                "ON CONFLICT (employee_id, date) DO UPDATE SET "
                "login_time=EXCLUDED.login_time, logout_time=EXCLUDED.logout_time, "
                "attendance_type=EXCLUDED.attendance_type, status='Manual', logout_status='Manual'",
                params
            )
            db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "saved": saved, "msg": f"Attendance saved for {saved} employee(s)."})

    # GET request
    date_str = request.args.get("date", today.isoformat())
    try:
        date_obj = datetime.date.fromisoformat(date_str)
    except ValueError:
        date_obj = today

    _hr_user = _api_hr_username()
    _hr_filter = "AND e.assigned_hr_username=%s" if _hr_user else ""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(f"""
        SELECT e.employee_id, e.name, COALESCE(e.department,''), COALESCE(e.role,''),
               COALESCE(TO_CHAR(s.start_time,'HH24:MI'),'09:00'),
               COALESCE(TO_CHAR(s.end_time,'HH24:MI'),'18:00'),
               COALESCE(e.work_mode,'office')
        FROM employees e
        LEFT JOIN shifts s ON s.id=e.shift_id
        WHERE COALESCE(e.is_active, 1)=1 {_hr_filter}
        ORDER BY e.name
    """, (_hr_user,) if _hr_user else ())  # nosec B608 -- _hr_filter is a fixed literal, value is %s-bound
    emp_rows = cursor.fetchall()

    cursor.execute("SELECT employee_id, login_time, logout_time, attendance_type FROM attendance WHERE date=%s", (date_obj,))
    att_map = {r[0]: r for r in cursor.fetchall()}
    cursor.close()
    db.close()

    employees = []
    for r in emp_rows:
        eid = r[0]
        att = att_map.get(eid)
        employees.append({
            "employee_id": eid,
            "name": r[1],
            "department": r[2],
            "role": r[3],
            "shift_start": r[4],
            "shift_end": r[5],
            "work_mode": r[6],
            "attendance_type": att[3] if att else "",
            "login_time": str(att[1]) if att and att[1] else None,
            "logout_time": str(att[2]) if att and att[2] else None,
        })

    return jsonify({"ok": True, "date": date_obj.isoformat(), "employees": employees})


@attendance_bp.route("/monthly_report_export")
@admin_required
@limiter.limit("10 per minute")
def monthly_report_export():
    from flask import send_file
    year = int(request.args.get("year", datetime.date.today().year))
    month = int(request.args.get("month", datetime.date.today().month))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    active_cid = session.get("active_company_id")
    _co, _co_args = co_scope_column(active_cid)
    _hr, _hr_args = hr_scope_column()
    cursor.execute(
        f"SELECT employee_id, name FROM employees WHERE 1=1 {_co} {_hr} ORDER BY name",  # nosec B608 -- fixed scope fragments
        _co_args + _hr_args)
    employees = cursor.fetchall()

    _, last_day = calendar.monthrange(year, month)
    cursor.execute("""
        SELECT employee_id, date, login_time, logout_time, status, logout_status, attendance_type
        FROM attendance WHERE date BETWEEN %s AND %s
    """, (datetime.date(year, month, 1), datetime.date(year, month, last_day)))

    att_map = {}
    for row in cursor.fetchall():
        att_map.setdefault(row[0], {})[row[1]] = row

    holidays = fetch_holidays_set(year, month)
    working_days = get_working_days(year, month)
    today = company_today()
    cursor.close()
    db.close()

    report = []
    for emp_id, name in employees:
        emp_att = att_map.get(emp_id, {})
        full_days = half_days = late_days = absent = 0
        for d in working_days:
            if d > today or d in holidays:
                continue
            row = emp_att.get(d)
            if row:
                _, _, login_t, logout_t, status, _ls, att_type = row
                final = att_type if att_type else infer_type_legacy(status, login_t, logout_t)
                if final == "Full Day":
                    full_days += 1
                elif final == "Late - Full Day":
                    late_days += 1
                elif final in ("Half Day", "Present"):
                    half_days += 1
                else:
                    absent += 1
            else:
                absent += 1
        billable = len([d for d in working_days if d <= today and d not in holidays])
        present_equiv = full_days + late_days + half_days * 0.5
        pct = round(present_equiv / billable * 100, 1) if billable > 0 else 0
        report.append({"emp_id": emp_id, "name": name, "full_days": full_days,
                       "late_days": late_days, "half_days": half_days,
                       "absent": absent, "billable": billable, "pct": pct})

    month_name = datetime.date(year, month, 1).strftime("%B %Y")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance Report"

    # ── styles ──
    hdr_fill = PatternFill("solid", fgColor="1E3A8A")
    hdr_font = Font(color="FFFFFF", bold=True, size=11)
    title_font = Font(bold=True, size=13, color="1E3A8A")
    center = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin", color="DBEAFE")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    alt_fill = PatternFill("solid", fgColor="EFF6FF")

    # ── title row ──
    ws.merge_cells("A1:H1")
    ws["A1"] = f"Monthly Attendance Report -- {month_name}"
    ws["A1"].font = title_font
    ws["A1"].alignment = center
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:H2")
    working_day_count = len([d for d in working_days if d <= today and d not in holidays])
    ws["A2"] = f"Working Days: {working_day_count}   |   Holidays: {len(holidays)}   |   Employees: {len(report)}"
    ws["A2"].alignment = center
    ws["A2"].font = Font(size=10, color="64748B")
    ws.row_dimensions[2].height = 18

    # ── header row ──
    headers = ["Emp ID", "Name", "Full Days", "Late Days", "Half Days", "Absent", "Working Days", "Attendance %"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=h)
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = center
        cell.border = border
    ws.row_dimensions[3].height = 22

    # ── data rows ──
    for i, r in enumerate(report, 4):
        row_fill = alt_fill if i % 2 == 0 else PatternFill("solid", fgColor="FFFFFF")
        values = [r["emp_id"], r["name"], r["full_days"], r["late_days"],
                  r["half_days"], r["absent"], r["billable"], r["pct"]]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=i, column=col, value=val)
            cell.fill = row_fill
            cell.alignment = center if col != 2 else Alignment(horizontal="left", vertical="center")
            cell.border = border
            if col == 8:  # Attendance %
                pct_val = val
                if pct_val >= 90:
                    cell.font = Font(color="15803D", bold=True)
                elif pct_val >= 70:
                    cell.font = Font(color="D97706", bold=True)
                else:
                    cell.font = Font(color="DC2626", bold=True)

    # ── column widths ──
    col_widths = [12, 24, 12, 12, 12, 10, 14, 14]
    for col, w in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = w

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"attendance_{year}_{month:02d}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@attendance_bp.route("/send_absentee_report", methods=["POST"])
@admin_required
def send_absentee_report():
    cfg = get_email_config()
    if not cfg:
        return jsonify({"ok": False, "msg": "Email not configured. Go to Email Settings first."})

    today = company_today()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    cursor.execute("SELECT employee_id, name FROM employees ORDER BY name")
    all_emp = cursor.fetchall()

    cursor.execute("SELECT DISTINCT employee_id FROM attendance WHERE date=%s", (today,))
    present_ids = {r[0] for r in cursor.fetchall()}
    cursor.close()
    db.close()

    absentees = [(eid, nm) for eid, nm in all_emp if eid not in present_ids]
    total = len(all_emp)
    absent = len(absentees)
    present = total - absent

    rows_html = "".join(
        f"<tr><td style='padding:8px 14px;border-bottom:1px solid #e2e8f0;'>{eid}</td>"
        f"<td style='padding:8px 14px;border-bottom:1px solid #e2e8f0;'>{nm}</td></tr>"
        for eid, nm in absentees
    ) or "<tr><td colspan='2' style='padding:14px;text-align:center;color:#16a34a;'>All employees present!</td></tr>"

    html = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:600px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">&#127970; Daily Absentee Report</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">{today.strftime('%A, %d %B %Y')}</div>
  </div>
  <div style="padding:24px;">
    <div style="display:flex;gap:16px;margin-bottom:24px;">
      <div style="flex:1;background:#dcfce7;border-radius:10px;padding:16px;text-align:center;">
        <div style="font-size:28px;font-weight:700;color:#15803d;">{present}</div>
        <div style="font-size:12px;color:#166534;">Present</div>
      </div>
      <div style="flex:1;background:#fee2e2;border-radius:10px;padding:16px;text-align:center;">
        <div style="font-size:28px;font-weight:700;color:#dc2626;">{absent}</div>
        <div style="font-size:12px;color:#991b1b;">Absent</div>
      </div>
      <div style="flex:1;background:#dbeafe;border-radius:10px;padding:16px;text-align:center;">
        <div style="font-size:28px;font-weight:700;color:#1d4ed8;">{total}</div>
        <div style="font-size:12px;color:#1e40af;">Total</div>
      </div>
    </div>
    <table style="width:100%;border-collapse:collapse;background:white;border-radius:10px;overflow:hidden;border:1px solid #dbeafe;">
      <thead>
        <tr style="background:#dbeafe;">
          <th style="padding:10px 14px;text-align:left;color:#1e3a8a;font-size:13px;">Employee ID</th>
          <th style="padding:10px 14px;text-align:left;color:#1e3a8a;font-size:13px;">Name</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>"""

    try:
        send_email_smtp(cfg.get("from_email", cfg["user"]),
                        f"Daily Absentee Report -- {today.strftime('%d %b %Y')}", html, cfg)
        return jsonify({"ok": True, "msg": f"Report sent! {absent} absent out of {total} employees."})
    except Exception:
        app_log.error("Failed to send absentee report email", exc_info=True)
        return jsonify({"ok": False, "msg": "Failed to send email. Check email settings."})


@attendance_bp.route("/attendance", methods=["POST"])
def attendance():
    import base64
    import io
    import numpy as np
    from PIL import Image

    data = request.get_json() or {}
    emp_id = data.get("employee_id", "").strip()
    face_b64 = data.get("face_image", "")
    user_lat = data.get("lat")
    user_lon = data.get("lon")
    auth_combo = data.get("auth_combo", "qr_face")
    # Optional integrity inputs (utils/punch_integrity.py). Old clients send
    # none of these; what that means is decided by LIVENESS_MODE.
    liveness_in = data.get("liveness") if isinstance(data.get("liveness"), dict) else {}
    channel = data.get("channel") if data.get("channel") in ("kiosk", "portal") else "kiosk"

    if auth_combo not in ("qr_face", "qr_only", "qr_fingerprint", "fingerprint_only"):
        return jsonify({"ok": False, "msg": "Invalid auth combination."})

    if not emp_id:
        err_msg = "Employee ID is required." if auth_combo == "fingerprint_only" else "No QR code data received."
        return jsonify({"ok": False, "msg": err_msg})

    # auth_combo comes from the client, so which checks run must be decided
    # here from the organisation's settings -- otherwise a client could
    # simply ask for "qr_only" and skip face verification. Runs before any
    # QR verification, lockout lookup or employee lookup.
    auth_cfg = get_auth_config()
    if auth_combo not in allowed_checkin_combos(auth_cfg):
        log_security_event("attendance.combo_rejected", "Check-in method not permitted by settings",
                           level="WARNING", auth_combo=auth_combo)
        return jsonify({"ok": False, "msg": "This check-in method is not enabled for your organisation."}), 403

    # For every combo that involves scanning a QR code, the posted
    # "employee_id" is really whatever text the QR encodes -- which must be
    # this employee's signed "<id>.<hmac>" value (see qr_generator.py), not
    # a bare ID. Without this, qr_only in particular performed ZERO
    # identity verification: the QR previously just encoded the raw,
    # often-guessable employee_id, so anyone who knew or guessed a
    # coworker's ID could mark them present/absent with no further check.
    # fingerprint_only has no QR component (the ID is typed, then a real
    # fingerprint match against THAT ID's stored credential gates it), so
    # it's intentionally excluded here.
    if auth_combo in ("qr_face", "qr_only", "qr_fingerprint"):
        from qr_generator import verify_qr_value
        _verified_emp_id, _qr_valid = verify_qr_value(emp_id)
        if not _qr_valid:
            return jsonify({"ok": False, "msg": "Invalid or unrecognized QR code. Please rescan."}), 400
        emp_id = _verified_emp_id

    # Attendance auto-lockout -- 4 failed identity-mismatch attempts (face
    # mismatch / fingerprint verify failure) locks online check-in for this
    # employee/day; only an admin manually marking attendance
    # (correct_attendance/bulk_mark_attendance) clears it. Checked before any
    # biometric work so a locked-out employee doesn't burn a face-recognition
    # pass for nothing. Available to every tenant.
    _today = company_today()
    _locked, _lock_msg = check_attendance_lockout(emp_id, _today)
    if _locked:
        return jsonify({"ok": False, "msg": _lock_msg}), 403

    if auth_combo in ("qr_fingerprint", "fingerprint_only"):
        if not auth_cfg["fingerprint_enabled"]:
            return jsonify({"ok": False, "msg": "Fingerprint not enabled. Ask your admin to enable it in Settings."}), 403
        # Real, server-verified, one-time, employee-bound proof from
        # /api/employee/webauthn-verify-challenge -- not a client-supplied flag.
        if not _wa_fingerprint_recently_verified(emp_id):
            record_attendance_failure(emp_id, _today, "Fingerprint verification failed")
            return jsonify({"ok": False, "msg": "Fingerprint verification failed. Please try again."}), 401

    needs_face = (auth_combo == "qr_face")
    if needs_face and not face_b64:
        return jsonify({"ok": False, "msg": "Face photo not captured."})

    frame = None
    if needs_face:
        try:
            img_bytes = base64.b64decode(face_b64)
            pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            frame = np.array(pil_img)
        except Exception:
            return jsonify({"ok": False, "msg": "Invalid face image data."})

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT face_image, name, work_mode, work_lat, work_lon "
        "FROM employees WHERE employee_id=%s", (emp_id,))
    result = cursor.fetchone()

    if not result:
        cursor.close()
        db.close()
        not_found_msg = ("Employee ID not found. Please check your ID and try again."
                         if auth_combo == "fingerprint_only"
                         else "Employee not found. Please check your QR code.")
        return jsonify({"ok": False, "msg": not_found_msg})

    face_path, employee_name, emp_work_mode, emp_work_lat, emp_work_lon = result

    ctx = pi.build_context(emp_id, channel, auth_combo, request.remote_addr, data)

    def _reject(decision):
        """Single exit for every integrity refusal: evidence row, optional
        lockout credit, then the response."""
        pi.record_evidence(ctx, cursor, outcome="rejected")
        cursor.close()
        db.close()
        if decision.count_as_failure:
            record_attendance_failure(emp_id, _today, decision.msg)
        return jsonify({"ok": False, "msg": decision.msg, "code": decision.code}), decision.status

    _d = pi.check_mock_location(ctx, log_security_event)
    if not _d.allow:
        return _reject(_d)
    _d = pi.check_office_network(ctx, cursor, emp_work_mode, log_security_event)
    if not _d.allow:
        return _reject(_d)
    if auth_combo == "qr_face":
        _d = pi.check_biometric_consent(ctx, cursor, log_security_event)
        if not _d.allow:
            return _reject(_d)

    # Location check
    if auth_cfg["location_enabled"] and (not user_lat or not user_lon):
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Location not captured. Please allow location access."})
    if auth_cfg["location_enabled"] and user_lat and user_lon:
        if emp_work_mode == 'wfh':
            if emp_work_lat and emp_work_lon:
                if not is_within_range(float(user_lat), float(user_lon), float(emp_work_lat), float(emp_work_lon)):
                    cursor.close()
                    db.close()
                    return jsonify({"ok": False, "msg": "You are outside your registered home location."})
        else:
            if not is_within_office_range(float(user_lat), float(user_lon)):
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "You are outside the office premises."})

    # Face recognition (only for qr_face combo)
    known_encoding = None
    if needs_face:
        if not _face_recognition_available:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face recognition is currently unavailable on this server. Contact your admin."})
        if not os.path.exists(face_path):
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face image missing. Please re-register."})
        known_encoding = _get_known_face_encoding(emp_id, face_path)
        if known_encoding is None:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Stored face image is invalid. Please re-register."})

    if needs_face:
        locs = face_recognition.face_locations(frame)
        encs = face_recognition.face_encodings(frame, locs)
        if not encs:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "No face detected in photo. Look directly at the camera."})
        # tolerance=0.5, matching every other compare_faces() call site in
        # this codebase (utils/face_utils.py, blueprints/employee_portal.py)
        # -- omitting it silently falls back to face_recognition's default
        # of 0.6, a looser threshold that let unrelated-but-similar faces
        # pass as a match on this specific, unattended kiosk check-in path.
        matched = any(
            True in face_recognition.compare_faces([known_encoding], enc, tolerance=0.5)
            for enc in encs
        )
        if not matched:
            cursor.close()
            db.close()
            record_attendance_failure(emp_id, _today, "Face does not match")
            return jsonify({"ok": False, "msg": "Face does not match. Please try again."})

        # Identity matched; now ask whether a live person is behind it and
        # whether this exact photo has been used before.
        live_frames, live_frame_bytes = _decode_liveness_frames(liveness_in.get("frames"))
        _photo_bytes = img_bytes if face_b64 else (live_frame_bytes[0] if live_frame_bytes else None)
        if _photo_bytes:
            try:
                ctx.photo_sha256, ctx.photo_dhash = pi.fingerprint_photo(_photo_bytes)
            except Exception:
                app_log.warning("photo fingerprint failed for %s", emp_id, exc_info=True)
        _d = pi.check_photo_replay(ctx, cursor, log_security_event)
        if not _d.allow:
            return _reject(_d)
        _d = pi.check_liveness(
            ctx, cursor, frames=live_frames,
            challenge_id=liveness_in.get("challenge_id"), nonce=liveness_in.get("nonce"),
            known_encoding=known_encoding, face_recognition_mod=face_recognition,
            log_event=log_security_event)
        if not _d.allow:
            return _reject(_d)

    pi.record_evidence(ctx, cursor, outcome="accepted")

    now = company_now()
    today = now.date()
    current_time = now.time()

    cursor.execute(
        "SELECT login_time, logout_time, status, worked_minutes, last_relogin "
        "FROM attendance WHERE employee_id=%s AND date=%s",
        (emp_id, today)
    )
    record = cursor.fetchone()
    login_time = record[0] if record else None
    logout_time = record[1] if record else None
    login_status_stored = record[2] if record else None
    worked_mins_stored = (record[3] or 0) if record else 0
    last_relogin_stored = record[4] if record else None

    # Use employee's assigned shift, or global defaults
    s_start, s_half, s_end, shift_name = get_employee_shift(emp_id, cursor)

    if not login_time:
        grace_time = (datetime.datetime.combine(today, s_start) + datetime.timedelta(minutes=cfg.GRACE_MINUTES)).time()
        if current_time <= grace_time:
            login_status = "Full Day Login"
        elif current_time <= s_half:
            login_status = "Late Login"
        else:
            login_status = "Half Day Login"
        cursor.execute(
            "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
            (emp_id, today, current_time, login_status)
        )
        db.commit()
        cursor.close()
        db.close()
        time_str = current_time.strftime("%H:%M:%S")
        return jsonify({"ok": True, "type": "login", "name": employee_name,
                        "status": login_status, "time": time_str, "shift": shift_name,
                        "work_mode": emp_work_mode})

    elif not logout_time:
        # Determine session start (re-login time if present, else first login)
        session_start = last_relogin_stored if last_relogin_stored else login_time
        if not isinstance(session_start, datetime.time):
            session_start = _td_to_time(session_start)
        cur_dt = datetime.datetime.combine(today, current_time)
        start_dt = datetime.datetime.combine(today, session_start)
        session_m = max(0, int((cur_dt - start_dt).total_seconds() / 60))
        total_m = worked_mins_stored + session_m

        if current_time < s_half:
            logout_status = "Half Day Logout"
        elif current_time < s_end:
            logout_status = "Early Logout"
        else:
            logout_status = "Completed"
        # Overtime: minutes beyond shift end
        now_mins = current_time.hour * 60 + current_time.minute
        end_mins = s_end.hour * 60 + s_end.minute
        overtime_m = max(0, now_mins - end_mins)
        att_type = classify_by_worked_minutes(login_status_stored, total_m, s_start, s_end)
        cursor.execute(
            "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, logout_status, att_type, total_m, emp_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        detect_overtime(emp_id, today, current_time)
        time_str = current_time.strftime("%H:%M:%S")
        resp = {"ok": True, "type": "logout", "name": employee_name,
                "status": logout_status, "att_type": att_type,
                "time": time_str, "shift": shift_name, "work_mode": emp_work_mode}
        if overtime_m > 0:
            resp["overtime"] = f"{overtime_m // 60}h {overtime_m % 60}m" if overtime_m >= 60 else f"{overtime_m}m"
        return jsonify(resp)

    else:
        # Re-login after a break -- re-open the session
        # worked_minutes was already saved on the previous logout, so just set last_relogin
        cursor.execute(
            "UPDATE attendance SET logout_time=NULL, last_relogin=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, emp_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        time_str = current_time.strftime("%H:%M:%S")
        return jsonify({"ok": True, "type": "relogin", "name": employee_name,
                        "status": "Re-Login", "time": time_str, "shift": shift_name,
                        "work_mode": emp_work_mode})


def _decode_liveness_frames(raw_frames):
    return pi.decode_b64_frames(raw_frames)


@attendance_bp.route("/api/attendance/challenge", methods=["POST"])
@limiter.limit("60 per minute")
def api_attendance_challenge():
    """Issues the single-use liveness challenge a face check-in must answer
    (see utils/punch_integrity.py). Deliberately unauthenticated like
    /attendance itself: a challenge on its own grants nothing -- it is only
    ever redeemable together with that employee's face. The response has
    the same shape whether or not the id exists, so this is not an
    employee-id oracle."""
    import secrets
    data = request.get_json(silent=True) or {}
    raw_id = str(data.get("employee_id", "")).strip()
    if not raw_id or len(raw_id) > 200:
        return jsonify({"ok": False, "msg": "employee_id required"}), 400
    from qr_generator import verify_qr_value
    verified_id, valid = verify_qr_value(raw_id)
    emp_id = verified_id if valid else raw_id.upper()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
        known = cursor.fetchone() is not None
        owner = emp_id if known else f"unknown:{secrets.token_hex(4)}"
        ch = pi.issue_challenge(cursor, owner, request.remote_addr)
    finally:
        cursor.close()
        db.close()
    return jsonify({"ok": True, **ch})


@attendance_bp.route("/api/admin/attendance/flagged", methods=["GET"])
@admin_required
def api_admin_flagged_punches():
    """Punch attempts that tripped an integrity rule (flags set) or were
    rejected by one, newest first -- the HR review queue for spoofing."""
    try:
        days = max(1, min(int(request.args.get("days", 7)), 90))
    except ValueError:
        days = 7
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        cursor.execute(
            "SELECT id, employee_id, channel, auth_combo, ip, device_id, liveness, liveness_action, "
            "flags, outcome, created_at FROM punch_evidence "
            "WHERE ((flags IS NOT NULL AND flags <> '') OR outcome = 'rejected') "
            "AND created_at > NOW() - (%s * INTERVAL '1 day') ORDER BY id DESC LIMIT 500", (days,))
        rows = cursor.fetchall()
    finally:
        cursor.close()
        db.close()
    keys = ("id", "employee_id", "channel", "auth_combo", "ip", "device_id", "liveness",
            "liveness_action", "flags", "outcome", "created_at")
    punches = [{k: (str(v) if k == "created_at" else v) for k, v in zip(keys, r)} for r in rows]
    return jsonify({"ok": True, "days": days, "punches": punches})


@attendance_bp.route("/api/admin/attendance/office_networks", methods=["GET", "POST"])
@admin_required
def api_admin_office_networks():
    """Public IP ranges on-site punches must come from (WFH exempt). An empty
    list or require=false turns the rule off. Only flagged, never enforced,
    when the app cannot see real client IPs (utils/client_ip.py)."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        if request.method == "GET":
            cursor.execute("SELECT office_ip_cidrs, require_office_network FROM company_settings LIMIT 1")
            row = cursor.fetchone() or ("", 0)
            return jsonify({"ok": True, "cidrs": [c for c in (row[0] or "").split(",") if c],
                            "require": bool(row[1])})
        body = request.get_json(silent=True) or {}
        cidrs = body.get("cidrs", [])
        if not isinstance(cidrs, list) or len(cidrs) > 50:
            return jsonify({"ok": False, "msg": "cidrs must be a list of at most 50 entries."}), 400
        try:
            nets = pi.parse_cidrs(",".join(str(c) for c in cidrs))
        except ValueError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 400
        # A /0 would silently turn the control into "allow everything".
        if any(n.prefixlen == 0 for n in nets):
            return jsonify({"ok": False, "msg": "A /0 network allows everything; refusing."}), 400
        require = 1 if body.get("require") and nets else 0
        cursor.execute("UPDATE company_settings SET office_ip_cidrs=%s, require_office_network=%s",
                       (",".join(str(n) for n in nets), require))
        db.commit()
    finally:
        cursor.close()
        db.close()
    log_security_event("attendance.office_networks_changed", "Office network allow-list changed",
                       level="WARNING", identifier=session.get("admin_username", ""),
                       reason=f"count={len(nets)} require={bool(require)}")
    return jsonify({"ok": True, "cidrs": [str(n) for n in nets], "require": bool(require)})


def process_punch(cursor, db, emp_id, employee_name, punch_dt=None):
    """Core login/logout/relogin state machine for the mobile Bearer
    check-in flow (api_checkin below). Caller owns the cursor/db
    connection -- already pointed at the right tenant schema via the
    normal g.tenant_db path. Returns a plain dict rather than a Response
    so the caller can add its own fields before jsonifying.

    punch_dt defaults to the tenant's current company-local time for a live
    human tap; a device push passes the device's own punch timestamp
    instead, since it may be delivered slightly after the fact."""
    now = punch_dt or company_now()
    today = now.date()
    current_time = now.time()
    cursor.execute(
        "SELECT login_time, logout_time, status, worked_minutes, last_relogin "
        "FROM attendance WHERE employee_id=%s AND date=%s",
        (emp_id, today)
    )
    record = cursor.fetchone()
    login_time = record[0] if record else None
    logout_time = record[1] if record else None
    login_status_stored = record[2] if record else None
    worked_mins_stored = (record[3] or 0) if record else 0
    last_relogin_stored = record[4] if record else None
    if not login_time:
        grace_time = (datetime.datetime.combine(today, cfg.SHIFT_START) +
                      datetime.timedelta(minutes=cfg.GRACE_MINUTES)).time()
        if current_time <= grace_time:
            login_status = "Full Day Login"
        elif current_time <= cfg.SHIFT_HALF:
            login_status = "Late Login"
        else:
            login_status = "Half Day Login"
        cursor.execute(
            "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
            (emp_id, today, current_time, login_status)
        )
        db.commit()
        return {"ok": True, "type": "login", "name": employee_name,
                "status": login_status, "time": current_time.strftime("%H:%M:%S")}
    elif not logout_time:
        total_m = compute_session_worked_minutes(current_time, today, login_time, last_relogin_stored, worked_mins_stored)
        if current_time < cfg.SHIFT_HALF:
            logout_status = "Half Day Logout"
        elif current_time < cfg.SHIFT_END:
            logout_status = "Early Logout"
        else:
            logout_status = "Completed"
        att_type = classify_by_worked_minutes(login_status_stored, total_m, cfg.SHIFT_START, cfg.SHIFT_END)
        cursor.execute(
            "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, logout_status, att_type, total_m, emp_id, today)
        )
        db.commit()
        detect_overtime(emp_id, today, current_time)
        return {"ok": True, "type": "logout", "name": employee_name,
                "status": logout_status, "att_type": att_type,
                "time": current_time.strftime("%H:%M:%S")}
    else:
        cursor.execute(
            "UPDATE attendance SET logout_time=NULL, last_relogin=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, emp_id, today)
        )
        db.commit()
        return {"ok": True, "type": "relogin", "name": employee_name,
                "status": "Re-Login", "time": current_time.strftime("%H:%M:%S")}


@attendance_bp.route("/api/attendance/checkin", methods=["POST"])
@api_required
def api_checkin():
    data = request.get_json() or {}
    emp_id = data.get("employee_id")
    lat = data.get("lat")
    lon = data.get("lon")
    if not emp_id:
        return jsonify({"ok": False, "msg": "employee_id required"}), 400
    _locked, _lock_msg = check_attendance_lockout(emp_id, company_today())
    if _locked:
        return jsonify({"ok": False, "msg": _lock_msg}), 403
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    result = fetch_employee_work_location(cursor, emp_id)
    if not result:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Employee not found."})
    employee_name, emp_work_mode, emp_work_lat, emp_work_lon = result

    # This path has no face/QR/fingerprint factor at all -- location is the
    # only claim -- so the environment checks matter most here.
    ctx = pi.build_context(emp_id, "mobile", "api_checkin", request.remote_addr, data)
    for _d in (pi.check_mock_location(ctx, log_security_event),
               pi.check_office_network(ctx, cursor, emp_work_mode, log_security_event)):
        if not _d.allow:
            pi.record_evidence(ctx, cursor, outcome="rejected")
            cursor.close()
            db.close()
            if _d.count_as_failure:
                record_attendance_failure(emp_id, company_today(), _d.msg)
            return jsonify({"ok": False, "msg": _d.msg, "code": _d.code}), _d.status

    # Same hole as /api/employee/checkin had: the geofence only ran `if lat
    # and lon`, so omitting the coordinates skipped it whatever the
    # company's location setting said. MOBILE_REQUIRE_LOCATION=false
    # restores the old behaviour.
    if (os.environ.get("MOBILE_REQUIRE_LOCATION", "true").strip().lower() not in ("false", "0", "no")
            and get_auth_config().get("location_enabled") and not (lat and lon)):
        ctx.flag("location_missing")
        pi.record_evidence(ctx, cursor, outcome="rejected")
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Location not captured. Please allow location access and try again.",
                        "code": "location_required"}), 400

    if lat and lon:
        geofence_err = geofence_check_error(emp_work_mode, emp_work_lat, emp_work_lon, lat, lon)
        if geofence_err:
            pi.record_evidence(ctx, cursor, outcome="rejected")
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": geofence_err})
    pi.record_evidence(ctx, cursor, outcome="accepted")
    result = process_punch(cursor, db, emp_id, employee_name)
    cursor.close()
    db.close()
    return jsonify(result)


@attendance_bp.route("/api/shifts", methods=["GET"])
@api_required
def api_shifts_get():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT id, name, start_time, half_time, end_time FROM shifts ORDER BY start_time")
    shifts = [
        {"id": r[0], "name": r[1],
         "start": _td_to_time(r[2]).strftime("%H:%M") if r[2] else "--",
         "half": _td_to_time(r[3]).strftime("%H:%M") if r[3] else "--",
         "end": _td_to_time(r[4]).strftime("%H:%M") if r[4] else "--"}
        for r in cursor.fetchall()
    ]
    cursor.execute(
        "SELECT e.employee_id, e.name, e.role, s.id, s.name "
        "FROM employees e LEFT JOIN shifts s ON e.shift_id = s.id ORDER BY e.name"
    )
    employees = [
        {"emp_id": r[0], "name": r[1], "role": r[2] or "",
         "shift_id": r[3], "shift_name": r[4] or "Default"}
        for r in cursor.fetchall()
    ]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "shifts": shifts, "employees": employees})


@attendance_bp.route("/api/shifts", methods=["POST"])
@api_required
def api_shifts_create():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    start = data.get("start_time", "").strip()
    half = data.get("half_time", "").strip()
    end = data.get("end_time", "").strip()
    if not all([name, start, half, end]):
        return jsonify({"ok": False, "msg": "All fields required"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        cursor.execute(
            "INSERT INTO shifts (name, start_time, half_time, end_time) VALUES (%s,%s,%s,%s) RETURNING id",
            (name, start, half, end)
        )
        sid = cursor.fetchone()[0]
        db.commit()
    except Exception:
        app_log.error("Failed to create shift", exc_info=True)
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Failed to create shift. Check for duplicate names."}), 400
    cursor.close()
    db.close()
    return jsonify({"ok": True, "id": sid})


@attendance_bp.route("/api/shifts/<int:sid>", methods=["DELETE"])
@api_required
def api_shifts_delete(sid):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE employees SET shift_id=NULL WHERE shift_id=%s", (sid,))
    cursor.execute("DELETE FROM shifts WHERE id=%s", (sid,))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True})


@attendance_bp.route("/api/shifts/assign", methods=["POST"])
@api_required
def api_shifts_assign():
    data = request.get_json(silent=True) or {}
    emp_id = data.get("emp_id", "").strip()
    shift_id = data.get("shift_id")
    if not emp_id:
        return jsonify({"ok": False, "msg": "emp_id required"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE employees SET shift_id=%s WHERE employee_id=%s",
        (shift_id if shift_id else None, emp_id)
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True})
