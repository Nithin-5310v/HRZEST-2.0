# -*- coding: utf-8 -*-
"""Employee portal blueprint -- self-service profile, photos, QR/ID card, check-in APIs."""
import os
import calendar
import datetime
from io import BytesIO
from flask import (
    Blueprint, request, session, redirect, jsonify, render_template,
)
from extensions import app, app_log, limiter, log_security_event
from database import get_db_connection
from utils.auth import employee_required, employee_api_required, validate_new_password
from utils.helpers import (
    tpath,
    _audit, _db, encrypt_pii, decrypt_pii, decrypt_pii_date, _validate_image_file, get_auth_config,
    company_today, company_now, coerce_datetime,
)
from utils.ai_assistant import build_employee_context, ask_assistant
from utils.session_risk import ensure_session_id, evaluate_session_risk
from utils.attendance_utils import (
    classify_by_worked_minutes, detect_overtime, infer_type_legacy,
    fetch_holidays_set, get_billable_past_days, is_within_range,
    is_within_office_range, geofence_check_error, compute_session_worked_minutes,
    fetch_employee_work_location,
)
from utils.leave_utils import assign_leave_balances_for_employee
from utils.face_utils import face_recognition, _face_recognition_available, _get_known_face_encoding
from utils.webauthn_utils import _wa_fingerprint_recently_verified, _mobile_biometric_recently_verified
from qr_generator import generate_qr
from utils.storage import open_private
import utils.config as cfg

employee_portal_bp = Blueprint("employee_portal", __name__)

UPLOAD_FOLDER = app.config["UPLOAD_FOLDER"]


def _fmt_t(t):
    if t is None:
        return None
    if hasattr(t, 'strftime'):
        return t.strftime("%H:%M:%S")
    total = int(t.total_seconds())
    return "{:02d}:{:02d}:{:02d}".format(total // 3600, (total % 3600) // 60, total % 60)


@employee_portal_bp.route("/update_my_profile", methods=["POST"])
@employee_required
def update_my_profile():
    emp_id = session["employee_id"]
    fields = {
        "phone": request.form.get("phone", "").strip() or None,
        "gender": encrypt_pii(request.form.get("gender", "").strip() or None),
        "dob": encrypt_pii(request.form.get("dob", "").strip() or None),
        "blood_group": encrypt_pii(request.form.get("blood_group", "").strip() or None),
        "address": encrypt_pii(request.form.get("address", "").strip() or None),
        "city": encrypt_pii(request.form.get("city", "").strip() or None),
        "state": encrypt_pii(request.form.get("state", "").strip() or None),
        "pincode": encrypt_pii(request.form.get("pincode", "").strip() or None),
        "emergency_contact_name": encrypt_pii(request.form.get("emergency_contact_name", "").strip() or None),
        "emergency_contact_phone": encrypt_pii(request.form.get("emergency_contact_phone", "").strip() or None),
        "emergency_contact_relation": encrypt_pii(request.form.get("emergency_contact_relation", "").strip() or None),
        "about_me": request.form.get("about_me", "").strip() or None,
    }
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE employees SET
            phone=%s, gender=%s, dob=%s, blood_group=%s,
            address=%s, city=%s, state=%s, pincode=%s,
            emergency_contact_name=%s, emergency_contact_phone=%s, emergency_contact_relation=%s,
            about_me=%s
        WHERE employee_id=%s
    """, (*fields.values(), emp_id))
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath("/employee_portal?profile_saved=1#my-profile"))


@employee_portal_bp.route("/update_my_bank_details", methods=["POST"])
@employee_required
def update_my_bank_details():
    emp_id = session["employee_id"]
    fields = {
        "aadhar_number": encrypt_pii(request.form.get("aadhar_number", "").strip() or None),
        "pan_number": encrypt_pii(request.form.get("pan_number", "").upper().strip() or None),
        "bank_name": encrypt_pii(request.form.get("bank_name", "").strip() or None),
        "bank_account": encrypt_pii(request.form.get("bank_account", "").strip() or None),
        "bank_ifsc": encrypt_pii(request.form.get("bank_ifsc", "").upper().strip() or None),
        "uan_number": encrypt_pii(request.form.get("uan_number", "").strip() or None),
    }
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE employees SET
            aadhar_number=%s, pan_number=%s, bank_name=%s,
            bank_account=%s, bank_ifsc=%s, uan_number=%s
        WHERE employee_id=%s
    """, (*fields.values(), emp_id))
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath("/employee_portal?bank_saved=1#my-profile"))


@employee_portal_bp.route("/add_experience", methods=["POST"])
@employee_required
def add_experience():
    emp_id = session["employee_id"]
    company = request.form.get("company", "").strip()
    designation = request.form.get("designation", "").strip()
    from_year = request.form.get("from_year", "").strip()
    to_year = request.form.get("to_year", "").strip() or None
    is_current = 1 if request.form.get("is_current") else 0
    description = request.form.get("description", "").strip() or None
    if not company or not designation or not from_year:
        return redirect(tpath("/employee_portal?exp_error=1#my-profile"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO employee_experience (employee_id, company, designation, from_year, to_year, is_current, description) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (emp_id, company, designation, from_year, to_year, is_current, description)
    )
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath("/employee_portal?exp_saved=1#my-profile"))


@employee_portal_bp.route("/delete_experience/<int:entry_id>", methods=["POST"])
@employee_required
def delete_experience(entry_id):
    emp_id = session["employee_id"]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM employee_experience WHERE id=%s AND employee_id=%s", (entry_id, emp_id))
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath("/employee_portal#my-profile"))


@employee_portal_bp.route("/add_education_entry", methods=["POST"])
@employee_required
def add_education_entry():
    emp_id = session["employee_id"]
    degree = request.form.get("degree", "").strip()
    institution = request.form.get("institution", "").strip()
    year_of_passing = request.form.get("year_of_passing", "").strip() or None
    percentage = request.form.get("percentage", "").strip() or None
    if not degree or not institution:
        return redirect(tpath("/employee_portal?edu_error=1#my-profile"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO employee_education (employee_id, degree, institution, year_of_passing, percentage) "
        "VALUES (%s,%s,%s,%s,%s)",
        (emp_id, degree, institution, year_of_passing, percentage)
    )
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath("/employee_portal?edu_saved=1#my-profile"))


@employee_portal_bp.route("/delete_education_entry/<int:entry_id>", methods=["POST"])
@employee_required
def delete_education_entry(entry_id):
    emp_id = session["employee_id"]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM employee_education WHERE id=%s AND employee_id=%s", (entry_id, emp_id))
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath("/employee_portal#my-profile"))


@employee_portal_bp.route("/update_my_photo", methods=["POST"])
@employee_required
def update_my_photo():
    import numpy as np
    from PIL import Image
    emp_id = session["employee_id"]
    file = request.files.get("photo")
    ok, err = _validate_image_file(file)
    if not ok:
        return redirect(tpath("/employee_portal?photo_error=bad_format#my-profile"))
    try:
        img = Image.open(file.stream).convert("RGB")
        img_array = np.array(img)
        if _face_recognition_available:
            locs = face_recognition.face_locations(img_array)
            if not locs:
                return redirect(tpath("/employee_portal?photo_error=no_face#my-profile"))
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], emp_id + ".jpg")
        img.save(save_path, "JPEG", quality=90)
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s", (save_path, emp_id))
        db.commit()
        cursor.close()
        db.close()
        return redirect(tpath("/employee_portal?photo_saved=1#my-profile"))
    except Exception:
        return redirect(tpath("/employee_portal?photo_error=failed#my-profile"))


def _generate_and_persist_qr(emp_id):
    """Shared by my_qr() and api_my_qr() below -- regenerates fresh on
    every call (the HMAC signature is deterministic, so this is cheap and
    idempotent) rather than trusting a possibly-stale existence check,
    and keeps employees.qr_code pointed at the current stored_ref."""
    stored_ref = generate_qr(emp_id)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE employees SET qr_code=%s WHERE employee_id=%s", (stored_ref, emp_id))
    db.commit()
    cursor.close()
    db.close()
    return stored_ref


@employee_portal_bp.route("/my_qr")
@employee_required
def my_qr():
    """The only sanctioned way for an employee to view/download their own
    attendance QR now that generate_qr() saves it to private storage --
    see that function's own docstring for why (the QR image is a bearer
    credential; a world-readable copy under static/ would defeat the
    HMAC signature's whole purpose)."""
    from flask import send_file
    emp_id = session["employee_id"]
    stored_ref = _generate_and_persist_qr(emp_id)
    return send_file(BytesIO(open_private(stored_ref)), mimetype="image/png",
                     as_attachment=True, download_name=f"QR_{emp_id}.png")


@employee_portal_bp.route("/api/employee/my_qr")
@employee_api_required
def api_my_qr():
    """Bearer-token twin of my_qr() above -- mobile has no session cookie
    (see mobile/src/api/client.js's own comment on this exact class of
    problem), so the session-only route above is unreachable from it.
    Base64 JSON rather than a raw image stream, same reason
    fetchSalaryReportExport/my_id_card's own API twin do the same: axios
    in React Native has no reliable blob/arraybuffer download path."""
    import base64
    from flask import g as _g
    emp_id = _g.api_emp_id
    stored_ref = _generate_and_persist_qr(emp_id)
    return jsonify({"ok": True, "image_base64": base64.b64encode(open_private(stored_ref)).decode("ascii")})


@employee_portal_bp.route("/my_id_card")
@employee_required
def my_id_card():
    from flask import send_file
    from blueprints.employees import _build_id_card_buf

    emp_id = session["employee_id"]
    buf = _build_id_card_buf(emp_id)
    if buf is None:
        return "Employee not found", 404
    return send_file(buf, as_attachment=True,
                     download_name=f"IDCard_{emp_id}.png", mimetype="image/png")


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


@employee_portal_bp.route("/employee_portal")
@employee_required
def employee_portal():
    emp_id = session["employee_id"]

    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    cursor.execute("""
        SELECT e.employee_id, e.name, e.role, e.email, e.face_image,
               e.date_of_joining,
               COALESCE(sc.salary_per_day, 0) AS salary_per_day,
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
        session.clear()
        return redirect(tpath("/login"))
    emp = list(_emp_raw)
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

    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
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
        elif d.weekday() == 6:
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
    hol_year = int(request.args.get("hol_year", today.year))
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
        cursor.execute(
            "SELECT COALESCE(SUM(amount),0) FROM employee_incentives WHERE employee_id=%s AND year=%s",
            (emp_id, today.year)
        )
        total_incentive_year = float(cursor.fetchone()[0])
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

    # Salary summary for Earnings tab
    salary_per_day = float(emp[6]) if emp[6] else 0.0
    gross_this_month = (full_days + late_days) * salary_per_day + half_days * salary_per_day * 0.5
    deduction_this_month = absent_days * salary_per_day + half_days * salary_per_day * 0.5
    try:
        cursor.execute(
            "SELECT COALESCE(SUM(amount),0) FROM employee_incentives WHERE employee_id=%s AND month=%s AND year=%s",
            (emp_id, today.month, today.year)
        )
        incentives_this_month = float(cursor.fetchone()[0])
    except Exception:
        incentives_this_month = 0.0
    try:
        cursor.execute(
            "SELECT COALESCE(SUM(ot_pay),0) FROM overtime_records WHERE employee_id=%s "
            "AND EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s AND status='Approved'",
            (emp_id, today.month, today.year)
        )
        ot_pay_this_month = float(cursor.fetchone()[0] or 0)
    except Exception:
        ot_pay_this_month = 0.0
    net_this_month = gross_this_month + incentives_this_month + ot_pay_this_month

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

    # Last 3 months payslip summaries
    recent_payslips = []
    py2, pm2 = today.year, today.month
    for _ in range(3):
        pm2 -= 1
        if pm2 == 0:
            pm2 = 12
            py2 -= 1
        _, ld = calendar.monthrange(py2, pm2)
        cursor.execute("""
            SELECT date, login_time, logout_time, status, logout_status, attendance_type
            FROM attendance WHERE employee_id=%s AND date BETWEEN %s AND %s
        """, (emp_id, datetime.date(py2, pm2, 1), datetime.date(py2, pm2, ld)))
        p_att = cursor.fetchall()
        p_billable = get_billable_past_days(py2, pm2)
        p_att_map = {r[0]: r for r in p_att}
        p_full = p_late = p_half = p_absent = 0
        for d in p_billable:
            row = p_att_map.get(d)
            if row:
                _, lt, lot, st, _ls, at = row
                final = at if at else infer_type_legacy(st, lt, lot)
                if final in ("Full Day", "Approved Leave"):
                    p_full += 1
                elif final == "Late - Full Day":
                    p_late += 1
                elif final in ("Half Day", "Present"):
                    p_half += 1
                else:
                    p_absent += 1
            else:
                p_absent += 1
        p_gross = (p_full + p_late) * salary_per_day + p_half * salary_per_day * 0.5
        try:
            cursor.execute(
                "SELECT COALESCE(SUM(amount),0) FROM employee_incentives WHERE employee_id=%s AND month=%s AND year=%s", (emp_id, pm2, py2))
            p_inc = float(cursor.fetchone()[0])
        except Exception:
            p_inc = 0.0
        try:
            cursor.execute(
                "SELECT COALESCE(SUM(ot_pay),0) FROM overtime_records WHERE employee_id=%s "
                "AND EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s AND status='Approved'",
                (emp_id, pm2, py2))
            p_ot = float(cursor.fetchone()[0] or 0)
        except Exception:
            p_ot = 0.0
        recent_payslips.append({
            'month': calendar.month_name[pm2], 'year': py2,
            'gross': p_gross, 'incentives': p_inc, 'ot_pay': p_ot,
            'net': p_gross + p_inc + p_ot,
            'present': p_full + p_late + p_half, 'absent': p_absent,
        })

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
        _photo_path = os.path.join(app.config["UPLOAD_FOLDER"], emp_id + ".jpg")
        photo_v = int(os.path.getmtime(_photo_path))
    except OSError:
        photo_v = 0

    return render_template("employee_portal.html",
                           emp=emp,
                           photo_v=photo_v,
                           today_date=today,
                           today=today.strftime("%d %b %Y"),
                           today_long=today.strftime("%A, %d %B %Y"),
                           today_att=today_att,
                           monthly_att=monthly_att,
                           full_days=full_days, late_days=late_days,
                           half_days=half_days, absent_days=absent_days,
                           billable=billable_count,
                           my_leaves=my_leaves,
                           my_resignation=my_resignation,
                           my_tickets=my_tickets,
                           leave_sent=request.args.get("leave_sent") == "1",
                           resigned=request.args.get("resigned") == "1",
                           ticket_sent=request.args.get("ticket_sent") == "1",
                           month_name=datetime.date(year, month, 1).strftime("%B %Y"),
                           selected_month=f"{year}-{month:02d}",
                           att_pct=att_pct,
                           total_hours=total_hours_str,
                           cal_data=cal_data,
                           cal_hol_names=cal_hol_names,
                           cal_year=cal_year,
                           cal_month=cal_month,
                           cal_first_dow=cal_first_dow,
                           sel_year=year,
                           sel_month=month,
                           years=list(range(today.year - 2, today.year + 1)),
                           months=[(i, datetime.date(year, i, 1).strftime("%B")) for i in range(1, 13)],
                           payslip_months=payslip_months,
                           leave_balance=leave_balance,
                           leaves_used=leaves_used,
                           annual_leave_quota=annual_leave_quota,
                           leave_type_balances=leave_type_balances,
                           leave_types_for_form=[{"id": lt[0], "name": lt[1]}
                                                 for lt in (leave_types_list if leave_types_list else [])],
                           announcements=announcements,
                           pending_leaves_count=pending_leaves_count,
                           open_tickets_count=open_tickets_count,
                           unread_notifications_web=unread_notifications_web,
                           upcoming_holidays=upcoming_holidays,
                           leave_holidays=leave_holidays,
                           hol_year=hol_year,
                           all_holidays_list=hol_rows,
                           my_incentives=my_incentives,
                           total_incentive_year=total_incentive_year,
                           my_experience=my_experience,
                           my_education=my_education,
                           my_docs=my_docs,
                           my_overtime=my_overtime,
                           compoff_avail_days=compoff_avail_days,
                           compoff_earned_days=compoff_earned_days,
                           salary_per_day=salary_per_day,
                           gross_this_month=gross_this_month,
                           deduction_this_month=deduction_this_month,
                           incentives_this_month=incentives_this_month,
                           ot_pay_this_month=ot_pay_this_month,
                           net_this_month=net_this_month,
                           recent_payslips=recent_payslips,
                           fp_enrolled=fp_enrolled,
                           fp_enabled=get_auth_config().get("fingerprint_enabled", False),
                           attendance_auth_cfg=get_auth_config(),
                           )


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
    emp_id = session.get("employee_id") or getattr(_g, "api_emp_id", None)
    if not emp_id:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            from utils.auth import _hash_token
            token_hash = _hash_token(auth[7:])
            with _db() as (cursor, _conn):
                cursor.execute(
                    "SELECT identity FROM api_tokens WHERE token=%s AND token_type='employee' AND expires_at > NOW()",
                    (token_hash,)
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


@employee_portal_bp.route("/api/employee/checkin", methods=["POST"])
@employee_api_required
def api_employee_checkin():
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json() or {}
    lat = data.get("lat")
    lon = data.get("lon")

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    result = fetch_employee_work_location(cursor, emp_id)
    if not result:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    employee_name, work_mode, work_lat, work_lon = result

    if lat and lon:
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (ValueError, TypeError):
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Invalid lat/lon values."}), 400
        if work_mode == 'wfh':
            if work_lat and work_lon:
                if not is_within_range(lat_f, lon_f, float(work_lat), float(work_lon)):
                    cursor.close()
                    db.close()
                    return jsonify({"ok": False, "msg": "You are outside your registered home location."})
        else:
            if not is_within_office_range(lat_f, lon_f):
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "You are outside the office premises."})

    punched_at_str = data.get("punched_at")
    now = company_now()
    if punched_at_str:
        try:
            _pt = datetime.datetime.fromisoformat(punched_at_str.replace("Z", "+00:00"))
            if _pt.tzinfo is not None:
                # Mobile sends Date.toISOString() (always UTC, "...Z") --
                # convert to the tenant's configured timezone before using
                # it as a wall-clock "now". Previously this just discarded
                # the offset (.replace(tzinfo=None)) and treated the raw
                # UTC digits as if they were already local, which shifted
                # every offline check-in's date/time by the UTC offset
                # (e.g. a 9am IST check-in landing as 3:30am).
                _pt = _pt.astimezone(now.tzinfo)
            else:
                # No offset info on the string (e.g. this app's own test
                # suite posts a naive isoformat()) -- assume it already
                # represents company-local wall-clock time, same as before.
                _pt = now.tzinfo.localize(_pt)
            if (now - _pt).total_seconds() <= 86400:
                now = _pt
            else:
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "Offline punch too old (>24 h). Rejected."}), 400
        except (ValueError, TypeError) as exc:
            # Deliberate fallback, not a bug being hidden: an unparseable
            # client-supplied offline-punch timestamp just means `now`
            # (already set above) is used instead -- still worth a trace
            # for diagnosing a client that's persistently sending malformed
            # timestamps.
            app_log.debug("Unparseable offline-punch punched_at=%r: %s", punched_at_str, exc)

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
    login_status = record[2] if record else None
    worked_mins_stored = (record[3] or 0) if record else 0
    last_relogin_stored = record[4] if record else None

    if not login_time:
        grace_time = (datetime.datetime.combine(today, cfg.SHIFT_START) +
                      datetime.timedelta(minutes=cfg.GRACE_MINUTES)).time()
        if current_time <= grace_time:
            status = "Full Day Login"
        elif current_time <= cfg.SHIFT_HALF:
            status = "Late Login"
        else:
            status = "Half Day Login"
        cursor.execute(
            "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
            (emp_id, today, current_time, status)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "login", "name": employee_name,
                        "status": status, "time": current_time.strftime("%H:%M:%S")})
    elif not logout_time:
        total_m = compute_session_worked_minutes(current_time, today, login_time, last_relogin_stored, worked_mins_stored)
        if current_time < cfg.SHIFT_HALF:
            out_status = "Half Day Logout"
        elif current_time < cfg.SHIFT_END:
            out_status = "Early Logout"
        else:
            out_status = "Completed"
        att_type = classify_by_worked_minutes(login_status, total_m, cfg.SHIFT_START, cfg.SHIFT_END)
        cursor.execute(
            "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, out_status, att_type, total_m, emp_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        detect_overtime(emp_id, today, current_time)
        return jsonify({"ok": True, "action": "logout", "name": employee_name,
                        "status": out_status, "att_type": att_type,
                        "time": current_time.strftime("%H:%M:%S")})
    else:
        cursor.execute(
            "UPDATE attendance SET logout_time=NULL, last_relogin=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, emp_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "relogin", "name": employee_name,
                        "status": "Re-Login", "time": current_time.strftime("%H:%M:%S")})


@employee_portal_bp.route("/api/employee/sync_punches", methods=["POST"])
@employee_api_required
def api_employee_sync_punches():
    """Batch-submit offline punches queued on the device when there was no connectivity."""
    from flask import g as _g
    emp_id = _g.api_emp_id
    payload = request.get_json() or {}
    punches = payload.get("punches", [])
    if not punches:
        return jsonify({"ok": True, "results": []})

    db2 = get_db_connection()
    cur2 = db2.cursor(buffered=True)
    _emp_row = fetch_employee_work_location(cur2, emp_id)
    if not _emp_row:
        cur2.close()
        db2.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    _work_mode, _work_lat, _work_lon = _emp_row[1], _emp_row[2], _emp_row[3]

    results = []
    for punch in punches:
        punched_at_str = punch.get("punched_at", "")
        # Same geo-fence check as the live check-in route (is_within_range
        # against work_lat/work_lon for wfh, else is_within_office_range) --
        # previously captured but never enforced here, unlike the live
        # route. Only checked when the client actually sent coordinates,
        # matching the live route's "if lat and lon:" gate.
        lat = punch.get("lat")
        lon = punch.get("lon")
        if lat and lon:
            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (ValueError, TypeError):
                results.append({"id": punch.get("id"), "ok": False, "msg": "Invalid lat/lon values."})
                continue
            if _work_mode == 'wfh':
                if _work_lat and _work_lon and not is_within_range(lat_f, lon_f, float(_work_lat), float(_work_lon)):
                    results.append({"id": punch.get("id"), "ok": False, "msg": "Outside registered home location."})
                    continue
            else:
                if not is_within_office_range(lat_f, lon_f):
                    results.append({"id": punch.get("id"), "ok": False, "msg": "Outside office premises."})
                    continue
        try:
            _pt = datetime.datetime.fromisoformat(punched_at_str.replace("Z", "+00:00"))
            _now = company_now()
            if _pt.tzinfo is not None:
                # See api_employee_checkin's punched_at handling above --
                # mobile always sends UTC ("...Z"); convert to the tenant's
                # configured timezone instead of discarding the offset and
                # treating the raw UTC digits as local.
                _pt = _pt.astimezone(_now.tzinfo)
            else:
                _pt = _now.tzinfo.localize(_pt)
            age = (_now - _pt).total_seconds()
            if age > 86400:
                results.append({"id": punch.get("id"), "ok": False, "msg": "Too old (>24 h)"})
                continue
            if _pt > _now + datetime.timedelta(minutes=5):
                results.append({"id": punch.get("id"), "ok": False, "msg": "Future timestamp rejected"})
                continue
        except (ValueError, TypeError):
            results.append({"id": punch.get("id"), "ok": False, "msg": "Invalid timestamp"})
            continue

        punch_date = _pt.date()
        punch_time = _pt.time()
        cur2.execute(
            "SELECT login_time, logout_time, status, worked_minutes, last_relogin "
            "FROM attendance WHERE employee_id=%s AND date=%s",
            (emp_id, punch_date)
        )
        rec = cur2.fetchone()
        login_time = rec[0] if rec else None
        logout_time = rec[1] if rec else None
        login_status = rec[2] if rec else None
        worked_mins = (rec[3] or 0) if rec else 0
        last_relogin = rec[4] if rec else None

        if not login_time:
            grace_time = (datetime.datetime.combine(punch_date, cfg.SHIFT_START) +
                          datetime.timedelta(minutes=cfg.GRACE_MINUTES)).time()
            if punch_time <= grace_time:
                status = "Full Day Login"
            elif punch_time <= cfg.SHIFT_HALF:
                status = "Late Login"
            else:
                status = "Half Day Login"
            cur2.execute(
                "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
                (emp_id, punch_date, punch_time, status)
            )
            db2.commit()
            results.append({"id": punch.get("id"), "ok": True, "action": "login", "status": status})
        elif not logout_time:
            total_m = compute_session_worked_minutes(punch_time, punch_date, login_time, last_relogin, worked_mins)
            if punch_time < cfg.SHIFT_HALF:
                out_status = "Half Day Logout"
            elif punch_time < cfg.SHIFT_END:
                out_status = "Early Logout"
            else:
                out_status = "Completed"
            att_type = classify_by_worked_minutes(login_status, total_m, cfg.SHIFT_START, cfg.SHIFT_END)
            cur2.execute(
                "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
                "WHERE employee_id=%s AND date=%s",
                (punch_time, out_status, att_type, total_m, emp_id, punch_date)
            )
            db2.commit()
            results.append({"id": punch.get("id"), "ok": True, "action": "logout", "status": out_status})
        else:
            results.append({"id": punch.get("id"), "ok": False, "msg": "Duplicate -- day already complete"})

    cur2.close()
    db2.close()
    _audit("sync_punches", "attendance", emp_id, f"Synced {len([r for r in results if r['ok']])} offline punches")
    return jsonify({"ok": True, "results": results})


@employee_portal_bp.route("/api/employee/auth-config", methods=["GET"])
def api_employee_auth_config():
    """Return all authentication method flags (public, no token required)."""
    return jsonify({"ok": True, **get_auth_config()})


@employee_portal_bp.route("/api/employee/qr-face-checkin", methods=["POST"])
@limiter.limit("20 per minute")
def api_employee_qr_face_checkin():
    """Kiosk endpoint -- supports auth_combo: qr_face | qr_fingerprint | face_fingerprint.

    Also serves the logged-in employee's own dashboard "Mark Attendance"
    widget: when an employee session exists, its employee_id always wins
    over whatever was posted, so a logged-in employee can never mark
    attendance under a different employee_id. With no session (the shared
    kiosk device, which is never logged in), falls back to the posted
    employee_id exactly as before -- unauthenticated by design there, since
    the QR/face/fingerprint combo itself is the proof of identity."""
    lat = request.form.get("lat")
    lon = request.form.get("lon")
    face_photo = request.files.get("face_photo")
    auth_combo = request.form.get("auth_combo", "qr_face")

    if auth_combo not in ("qr_face", "qr_fingerprint", "face_fingerprint"):
        return jsonify({"ok": False, "msg": "Invalid auth_combo"}), 400

    _session_emp_id = session.get("employee_id")
    if _session_emp_id:
        employee_id = _session_emp_id.strip().upper()
    elif auth_combo in ("qr_face", "qr_fingerprint"):
        # Shared, unauthenticated kiosk device: the posted "employee_id" is
        # really whatever text was scanned off the QR code, which must be
        # this employee's signed "<id>.<hmac>" value (qr_generator.py), not
        # a bare ID -- otherwise the QR/fingerprint combo below would be
        # checking the RIGHT biometric against a WRONG (attacker-typed)
        # employee_id if the two auth factors weren't cryptographically
        # bound to the same source.
        from qr_generator import verify_qr_value
        _verified_id, _qr_valid = verify_qr_value(request.form.get("employee_id", "").strip())
        if not _qr_valid:
            return jsonify({"ok": False, "msg": "Invalid or unrecognized QR code. Please rescan."}), 400
        employee_id = _verified_id.upper()
    else:
        # face_fingerprint has no QR component -- the ID is typed/pre-known
        # at the kiosk, and a real face/fingerprint match against THAT ID's
        # stored credential is what actually gates identity.
        employee_id = request.form.get("employee_id", "").strip().upper()

    if not employee_id:
        return jsonify({"ok": False, "msg": "employee_id required"}), 400

    auth_cfg = get_auth_config()

    if auth_combo in ("qr_face", "qr_fingerprint") and not auth_cfg["qr_enabled"]:
        return jsonify({"ok": False, "msg": "QR code authentication is not enabled"}), 403
    if auth_combo in ("qr_face", "face_fingerprint") and not auth_cfg["face_enabled"]:
        return jsonify({"ok": False, "msg": "Face recognition authentication is not enabled"}), 403
    if auth_combo in ("qr_fingerprint", "face_fingerprint"):
        if not auth_cfg["fingerprint_enabled"]:
            return jsonify({"ok": False, "msg": "Fingerprint authentication is not enabled"}), 403
        # Real, server-verified, one-time, employee-bound proof from either
        # /api/employee/webauthn-verify-challenge (web kiosk, session-based)
        # or /api/employee/mobile-biometric-attest (mobile app, Bearer-token-
        # bound) -- never a raw client-supplied flag.
        if not (_wa_fingerprint_recently_verified(employee_id)
                or _mobile_biometric_recently_verified(employee_id)):
            return jsonify({"ok": False, "msg": "Fingerprint verification failed. Please try again."}), 401

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    result = fetch_employee_work_location(cursor, employee_id, include_face=True)
    if not result:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    employee_name, work_mode, work_lat, work_lon, registered_face = result

    if lat and lon:
        try:
            geofence_err = geofence_check_error(work_mode, work_lat, work_lon, lat, lon)
        except (ValueError, TypeError):
            geofence_err = None
        if geofence_err:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": geofence_err})

    needs_face = auth_combo in ("qr_face", "face_fingerprint")
    if needs_face:
        if not face_photo:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face photo required for this authentication method."}), 400
        if not _face_recognition_available:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face recognition is currently unavailable on this server. Contact your admin."}), 503
        if not registered_face or not os.path.exists(registered_face):
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "No registered face found. Please contact your admin."}), 400
        try:
            from PIL import Image as _PILImage
            face_dir = os.path.join(UPLOAD_FOLDER, "face_logs")
            os.makedirs(face_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            face_path = os.path.join(face_dir, f"{employee_id}_{ts}.jpg")
            img = _PILImage.open(face_photo.stream).convert("RGB")
            img.save(face_path, "JPEG", quality=80)

            known_enc = _get_known_face_encoding(employee_id, registered_face)
            test_img_data = face_recognition.load_image_file(face_path)
            test_encs = face_recognition.face_encodings(test_img_data)
            if known_enc is None or not test_encs:
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "Face not detected clearly. Please retake the photo."}), 400
            if not face_recognition.compare_faces([known_enc], test_encs[0], tolerance=0.5)[0]:
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "Face did not match. Please try again."}), 401
        except Exception:
            app_log.error("Face verification error", exc_info=True)
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face verification failed. Please retake the photo."}), 500
    elif face_photo:
        try:
            from PIL import Image as _PILImage
            face_dir = os.path.join(UPLOAD_FOLDER, "face_logs")
            os.makedirs(face_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            face_path = os.path.join(face_dir, f"{employee_id}_{ts}.jpg")
            img = _PILImage.open(face_photo.stream).convert("RGB")
            img.save(face_path, "JPEG", quality=80)
        except Exception as exc:
            # Best-effort audit-log attachment -- must not block attendance
            # marking, but silently missing the face-log photo is worth
            # knowing about.
            app_log.warning("Face-log photo save failed for %s: %s", employee_id, exc, exc_info=True)

    now = company_now()
    today = now.date()
    current_time = now.time()

    cursor.execute(
        "SELECT login_time, logout_time, status, worked_minutes, last_relogin "
        "FROM attendance WHERE employee_id=%s AND date=%s",
        (employee_id, today)
    )
    record = cursor.fetchone()
    login_time = record[0] if record else None
    logout_time = record[1] if record else None
    login_status = record[2] if record else None
    worked_mins_stored = (record[3] or 0) if record else 0
    last_relogin_stored = record[4] if record else None

    if not login_time:
        grace_time = (datetime.datetime.combine(today, cfg.SHIFT_START) +
                      datetime.timedelta(minutes=cfg.GRACE_MINUTES)).time()
        if current_time <= grace_time:
            status = "Full Day Login"
        elif current_time <= cfg.SHIFT_HALF:
            status = "Late Login"
        else:
            status = "Half Day Login"
        cursor.execute(
            "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
            (employee_id, today, current_time, status)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "login", "name": employee_name,
                        "status": status, "time": current_time.strftime("%H:%M:%S")})
    elif not logout_time:
        total_m = compute_session_worked_minutes(current_time, today, login_time, last_relogin_stored, worked_mins_stored)
        if current_time < cfg.SHIFT_HALF:
            out_status = "Half Day Logout"
        elif current_time < cfg.SHIFT_END:
            out_status = "Early Logout"
        else:
            out_status = "Completed"
        att_type = classify_by_worked_minutes(login_status, total_m, cfg.SHIFT_START, cfg.SHIFT_END)
        cursor.execute(
            "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, out_status, att_type, total_m, employee_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        detect_overtime(employee_id, today, current_time)
        return jsonify({"ok": True, "action": "logout", "name": employee_name,
                        "status": out_status, "att_type": att_type,
                        "time": current_time.strftime("%H:%M:%S")})
    else:
        cursor.execute(
            "UPDATE attendance SET logout_time=NULL, last_relogin=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, employee_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "relogin", "name": employee_name,
                        "status": "Re-Login", "time": current_time.strftime("%H:%M:%S")})


@employee_portal_bp.route("/api/employee/salary", methods=["GET"])
@employee_api_required
def api_employee_salary():
    import calendar as cal
    from flask import g as _g
    emp_id = _g.api_emp_id
    try:
        year = int(request.args.get("year", datetime.date.today().year))
        month = int(request.args.get("month", datetime.date.today().month))
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid year/month"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT name, email FROM employees WHERE employee_id=%s", (emp_id,))
    emp_row = cursor.fetchone()
    if not emp_row:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    cursor.execute("SELECT salary_per_day FROM salary_config WHERE employee_id=%s", (emp_id,))
    spd_row = cursor.fetchone()
    spd = float(spd_row[0]) if spd_row else 0.0
    cursor.execute("SELECT date FROM holidays WHERE EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s", (month, year))
    holiday_set = {r[0] for r in cursor.fetchall()}
    _, days_in_month = cal.monthrange(year, month)
    billable = sum(
        1 for d in range(1, days_in_month + 1)
        # weekday() != 6 excludes only Sunday, matching get_working_days() --
        # the real payroll engine treats Saturday as a billable working day.
        if datetime.date(year, month, d).weekday() != 6
        and datetime.date(year, month, d) not in holiday_set
    )
    cursor.execute("""
        SELECT attendance_type FROM attendance
        WHERE employee_id=%s AND EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s
    """, (emp_id, month, year))
    att_rows = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*) FROM leave_requests
        WHERE employee_id=%s AND EXTRACT(MONTH FROM leave_date)=%s AND EXTRACT(YEAR FROM leave_date)=%s AND status='Approved'
    """, (emp_id, month, year))
    leave_days = cursor.fetchone()[0]
    cursor.close()
    db.close()
    full_days = half_days = late_days = 0
    for (att_type,) in att_rows:
        if att_type == 'Full Day':
            full_days += 1
        elif att_type == 'Late - Full Day':
            full_days += 1
            late_days += 1
        elif att_type in ('Half Day', 'Late - Half Day'):
            half_days += 1
    absent = max(0, billable - full_days - half_days - leave_days)
    gross = spd * billable
    deduction = spd * (absent + half_days * 0.5)
    net = gross - deduction
    return jsonify({
        "ok": True,
        "month_name": datetime.date(year, month, 1).strftime("%B %Y"),
        "year": year, "month": month,
        "salary": {
            "emp_id": emp_id, "name": emp_row[0], "email": emp_row[1],
            "spd": spd, "billable": billable,
            "full_days": full_days, "half_days": half_days,
            "late_days": late_days, "absent": absent, "leave_days": leave_days,
            "gross": gross, "deduction": deduction, "net": net,
        }
    })


@employee_portal_bp.route("/api/employee/attendance", methods=["GET"])
@employee_api_required
def api_employee_attendance():
    from flask import g as _g
    emp_id = _g.api_emp_id
    try:
        year = int(request.args.get("year", datetime.date.today().year))
        month = int(request.args.get("month", datetime.date.today().month))
    except ValueError:
        return jsonify({"ok": False, "msg": "Invalid year/month"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT date, login_time, logout_time, status, logout_status, attendance_type, worked_minutes
        FROM attendance
        WHERE employee_id=%s AND EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s
        ORDER BY date DESC
    """, (emp_id, month, year))
    rows = cursor.fetchall()
    cursor.execute("""
        SELECT COUNT(*), attendance_type FROM attendance
        WHERE employee_id=%s AND EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s
        GROUP BY attendance_type
    """, (emp_id, month, year))
    type_counts = {r[1]: r[0] for r in cursor.fetchall()}
    cursor.close()
    db.close()
    full = type_counts.get("Full Day", 0) + type_counts.get("Late - Full Day", 0)
    half = type_counts.get("Half Day", 0) + type_counts.get("Late - Half Day", 0)
    late = type_counts.get("Late - Full Day", 0) + type_counts.get("Late - Half Day", 0)
    return jsonify({
        "ok": True,
        "year": year, "month": month,
        "month_name": datetime.date(year, month, 1).strftime("%B %Y"),
        "summary": {"present": full + half, "full_days": full, "half_days": half, "late": late},
        "records": [
            {
                "date": str(r[0]),
                "login_time": _fmt_t(r[1]),
                "logout_time": _fmt_t(r[2]),
                "login_status": r[3],
                "logout_status": r[4],
                "attendance_type": r[5],
                "worked_minutes": r[6],
            }
            for r in rows
        ],
    })


@employee_portal_bp.route("/api/employee/profile", methods=["GET"])
@employee_api_required
def api_employee_profile():
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT e.employee_id, e.name, e.email, e.role, e.department,
               e.phone, e.dob, e.gender, e.blood_group, e.address, e.city, e.state,
               e.pincode, e.about_me, e.emergency_contact_name, e.emergency_contact_phone,
               e.bank_name, e.bank_account, e.bank_ifsc, e.pan_number, e.aadhar_number,
               COALESCE(s.salary_per_day, 0), COALESCE(e.joining_date, e.date_of_joining),
               COALESCE(c.name, (SELECT company_name FROM company_settings LIMIT 1), ''),
               e.emergency_contact_relation, e.uan_number, COALESCE(e.email_alerts_enabled, 1)
        FROM employees e
        LEFT JOIN salary_config s ON e.employee_id = s.employee_id
        LEFT JOIN companies c ON e.company_id = c.id
        WHERE e.employee_id = %s
    """, (emp_id,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    return jsonify({
        "ok": True,
        "profile": {
            "employee_id": row[0], "name": row[1], "email": row[2],
            "role": row[3], "department": row[4],
            "phone": row[5],
            "dob": decrypt_pii(row[6]) if row[6] else None,
            "gender": decrypt_pii(row[7]), "blood_group": decrypt_pii(row[8]),
            "address": decrypt_pii(row[9]), "city": decrypt_pii(row[10]),
            "state": decrypt_pii(row[11]), "pincode": decrypt_pii(row[12]),
            "about_me": row[13],
            "emergency_contact_name": decrypt_pii(row[14]), "emergency_contact_phone": decrypt_pii(row[15]),
            "emergency_contact_relation": decrypt_pii(row[24]),
            "bank_name": decrypt_pii(row[16]), "bank_account": decrypt_pii(row[17]), "bank_ifsc": decrypt_pii(row[18]),
            "pan_number": decrypt_pii(row[19]), "aadhar_number": decrypt_pii(row[20]),
            "uan_number": decrypt_pii(row[25]),
            "salary_per_day": float(row[21]),
            "join_date": str(row[22]) if row[22] else None,
            "company_name": row[23],
            "photo_url": f"/dataset/{row[0]}.jpg",
            "email_alerts_enabled": bool(row[26]),
        },
    })


@employee_portal_bp.route("/api/employee/notification_preferences", methods=["POST"])
@employee_api_required
def api_update_notification_preferences():
    """Toggles whether this employee receives the status-change alert emails
    gated in blueprints/leave.py (leave approved/declined, resignation
    accepted/declined) -- a dedicated route rather than folding this into
    api_update_my_profile, since that route replaces the whole employees
    row and every existing caller of it would need updating to keep
    round-tripping this field."""
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json(silent=True) or {}
    enabled = 1 if data.get("email_alerts_enabled") else 0
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE employees SET email_alerts_enabled=%s WHERE employee_id=%s", (enabled, emp_id))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "email_alerts_enabled": bool(enabled)})


@employee_portal_bp.route("/api/employee/profile", methods=["POST"])
@employee_api_required
def api_update_my_profile():
    """Bearer-token twin of update_my_profile() -- same fields, same
    encrypt-before-store behavior, JSON body instead of a form POST."""
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json(silent=True) or {}
    fields = {
        "phone": (data.get("phone") or "").strip() or None,
        "gender": encrypt_pii((data.get("gender") or "").strip() or None),
        "dob": encrypt_pii((data.get("dob") or "").strip() or None),
        "blood_group": encrypt_pii((data.get("blood_group") or "").strip() or None),
        "address": encrypt_pii((data.get("address") or "").strip() or None),
        "city": encrypt_pii((data.get("city") or "").strip() or None),
        "state": encrypt_pii((data.get("state") or "").strip() or None),
        "pincode": encrypt_pii((data.get("pincode") or "").strip() or None),
        "emergency_contact_name": encrypt_pii((data.get("emergency_contact_name") or "").strip() or None),
        "emergency_contact_phone": encrypt_pii((data.get("emergency_contact_phone") or "").strip() or None),
        "emergency_contact_relation": encrypt_pii((data.get("emergency_contact_relation") or "").strip() or None),
        "about_me": (data.get("about_me") or "").strip() or None,
    }
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE employees SET
            phone=%s, gender=%s, dob=%s, blood_group=%s,
            address=%s, city=%s, state=%s, pincode=%s,
            emergency_contact_name=%s, emergency_contact_phone=%s, emergency_contact_relation=%s,
            about_me=%s
        WHERE employee_id=%s
    """, (*fields.values(), emp_id))
    db.commit()
    cursor.close()
    db.close()
    _audit("update_profile", "employees", emp_id, "Updated via mobile")
    return jsonify({"ok": True, "msg": "Profile updated."})


@employee_portal_bp.route("/api/employee/bank_details", methods=["POST"])
@employee_api_required
def api_update_my_bank_details():
    """Bearer-token twin of update_my_bank_details()."""
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json(silent=True) or {}
    fields = {
        "aadhar_number": encrypt_pii((data.get("aadhar_number") or "").strip() or None),
        "pan_number": encrypt_pii((data.get("pan_number") or "").upper().strip() or None),
        "bank_name": encrypt_pii((data.get("bank_name") or "").strip() or None),
        "bank_account": encrypt_pii((data.get("bank_account") or "").strip() or None),
        "bank_ifsc": encrypt_pii((data.get("bank_ifsc") or "").upper().strip() or None),
        "uan_number": encrypt_pii((data.get("uan_number") or "").strip() or None),
    }
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE employees SET
            aadhar_number=%s, pan_number=%s, bank_name=%s,
            bank_account=%s, bank_ifsc=%s, uan_number=%s
        WHERE employee_id=%s
    """, (*fields.values(), emp_id))
    db.commit()
    cursor.close()
    db.close()
    _audit("update_bank_details", "employees", emp_id, "Updated via mobile")
    return jsonify({"ok": True, "msg": "Bank details updated."})


@employee_portal_bp.route("/api/employee/experience", methods=["GET"])
@employee_api_required
def api_list_my_experience():
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, company, designation, from_year, to_year, is_current, description "
        "FROM employee_experience WHERE employee_id=%s ORDER BY is_current DESC, from_year DESC",
        (emp_id,)
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "experience": [
        {"id": r[0], "company": r[1], "designation": r[2], "from_year": r[3],
         "to_year": r[4], "is_current": bool(r[5]), "description": r[6]}
        for r in rows
    ]})


@employee_portal_bp.route("/api/employee/experience", methods=["POST"])
@employee_api_required
def api_add_my_experience():
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    designation = (data.get("designation") or "").strip()
    from_year = (data.get("from_year") or "").strip()
    to_year = (data.get("to_year") or "").strip() or None
    is_current = 1 if data.get("is_current") else 0
    description = (data.get("description") or "").strip() or None
    if not company or not designation or not from_year:
        return jsonify({"ok": False, "msg": "company, designation, and from_year are required."}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO employee_experience (employee_id, company, designation, from_year, to_year, is_current, description) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (emp_id, company, designation, from_year, to_year, is_current, description)
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Experience added."})


@employee_portal_bp.route("/api/employee/experience/<int:entry_id>", methods=["DELETE"])
@employee_api_required
def api_delete_my_experience(entry_id):
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM employee_experience WHERE id=%s AND employee_id=%s", (entry_id, emp_id))
    deleted = cursor.rowcount
    db.commit()
    cursor.close()
    db.close()
    if not deleted:
        return jsonify({"ok": False, "msg": "Entry not found."}), 404
    return jsonify({"ok": True, "msg": "Experience removed."})


@employee_portal_bp.route("/api/employee/education", methods=["GET"])
@employee_api_required
def api_list_my_education():
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, degree, institution, year_of_passing, percentage "
        "FROM employee_education WHERE employee_id=%s ORDER BY year_of_passing DESC",
        (emp_id,)
    )
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "education": [
        {"id": r[0], "degree": r[1], "institution": r[2], "year_of_passing": r[3], "percentage": r[4]}
        for r in rows
    ]})


@employee_portal_bp.route("/api/employee/education", methods=["POST"])
@employee_api_required
def api_add_my_education():
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json(silent=True) or {}
    degree = (data.get("degree") or "").strip()
    institution = (data.get("institution") or "").strip()
    year_of_passing = (data.get("year_of_passing") or "").strip() or None
    percentage = (data.get("percentage") or "").strip() or None
    if not degree or not institution:
        return jsonify({"ok": False, "msg": "degree and institution are required."}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO employee_education (employee_id, degree, institution, year_of_passing, percentage) "
        "VALUES (%s,%s,%s,%s,%s)",
        (emp_id, degree, institution, year_of_passing, percentage)
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Education added."})


@employee_portal_bp.route("/api/employee/education/<int:entry_id>", methods=["DELETE"])
@employee_api_required
def api_delete_my_education(entry_id):
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM employee_education WHERE id=%s AND employee_id=%s", (entry_id, emp_id))
    deleted = cursor.rowcount
    db.commit()
    cursor.close()
    db.close()
    if not deleted:
        return jsonify({"ok": False, "msg": "Entry not found."}), 404
    return jsonify({"ok": True, "msg": "Education removed."})


@employee_portal_bp.route("/api/employee/photo", methods=["POST"])
@employee_api_required
def api_employee_upload_photo():
    from flask import g as _g
    from PIL import Image
    emp_id = _g.api_emp_id
    file = request.files.get("photo")
    ok, err = _validate_image_file(file)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 400
    try:
        img = Image.open(file.stream).convert("RGB")
        save_path = os.path.join(UPLOAD_FOLDER, emp_id + ".jpg")
        img.save(save_path, "JPEG", quality=85)
        return jsonify({"ok": True, "msg": "Photo uploaded successfully", "photo_url": f"/dataset/{emp_id}.jpg"})
    except Exception:
        return jsonify({"ok": False, "msg": "Failed to process image"}), 500


@employee_portal_bp.route("/api/employee/my_id_card")
@employee_api_required
def api_employee_my_id_card():
    """Bearer-token twin of my_id_card() above, for the mobile app --
    employee_required is session-cookie-only, which a token-only mobile
    client never has, so that route 401s from mobile as-is. Same
    _build_id_card_buf() rendering, scoped to the token's own employee_id
    exactly like the session version is scoped to session['employee_id'].

    Returns base64 JSON, not a raw image stream -- axios in React Native
    has no reliable blob/arraybuffer download path (see
    fetchSalaryReportExport's comment in mobile/src/api/client.js, the
    established precedent for exactly this problem); the mobile client
    writes this to a local file via expo-file-system and either displays
    or shares it from there."""
    import base64
    from flask import g as _g
    from blueprints.employees import _build_id_card_buf

    emp_id = _g.api_emp_id
    buf = _build_id_card_buf(emp_id)
    if buf is None:
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    return jsonify({"ok": True, "image_base64": base64.b64encode(buf.read()).decode("ascii")})


@employee_portal_bp.route("/api/employee/chat", methods=["POST"])
@employee_required
@limiter.limit("6 per minute")
@limiter.limit("40 per hour")
def api_employee_chat():
    """AI chat assistant for employee self-service queries -- see
    utils/ai_assistant.py for the scoping/security model. The client only
    ever sends free text; the employee's own data is always re-fetched
    server-side from the authenticated session's employee_id, never
    accepted from the request body."""
    emp_id = session["employee_id"]
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")
    history = payload.get("history", [])
    if not isinstance(message, str):
        return jsonify({"ok": False, "msg": "Invalid message."}), 400

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    context = build_employee_context(cursor, emp_id)
    cursor.close()
    db.close()

    ok, reply = ask_assistant(context, message, history if isinstance(history, list) else [], emp_id=emp_id)
    return jsonify({"ok": ok, "reply": reply})


# A locally-running device-posture agent is the only thing that can actually
# see Wi-Fi encryption type / ARP state / DNS config -- no web page or web
# server can (see utils/session_risk.py's module docstring). This endpoint
# receives whatever score that agent already computed, relayed by the
# browser over the user's own authenticated session -- it never computes a
# risk score itself, and there is no code path in this app for one.
_DEVICE_RISK_KILL_WEIGHT = 100  # comfortably exceeds SESSION_RISK_THRESHOLD (default 50)
# regardless of how that env var is configured, so one
# qualifying report always crosses the kill threshold.


@employee_portal_bp.route("/api/employee/device_risk", methods=["POST"])
@employee_required
@limiter.limit("20 per minute")
def api_employee_device_risk():
    """Relay endpoint for a native device-posture agent's Wi-Fi/network risk
    score (see the docstring above `_DEVICE_RISK_KILL_WEIGHT`). A score over
    60 feeds the existing session-risk kill switch (utils/session_risk.py)
    instead of a new mechanism: evaluate_session_risk() marks this session
    compromised, employee_required's _reject_if_compromised() then rejects
    this session's very next request on ANY route and clears it -- that's
    the "block the UI / terminate the session" requirement.
    log_security_event() below is "ship to Admin Security Logs" -- the same
    structured-log + webhook-alert pipeline every other security event in
    this app already uses, not a separate logging path."""
    payload = request.get_json(silent=True) or {}
    try:
        risk_score = int(payload.get("risk_score", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "risk_score must be an integer"}), 400
    risk_score = max(0, min(risk_score, 100))

    threat_vectors = payload.get("threat_vectors")
    if not isinstance(threat_vectors, list):
        threat_vectors = []
    threat_vectors = [str(v)[:60] for v in threat_vectors][:10]

    emp_id = session["employee_id"]
    sid = ensure_session_id(session)
    over_threshold = risk_score > 60

    log_security_event(
        "device.wifi_risk_reported",
        "Device posture agent reported a Wi-Fi/network risk score",
        level="ERROR" if over_threshold else "INFO",
        identifier=emp_id, risk_score=str(risk_score),
        threat_vectors=",".join(threat_vectors) or "none",
    )

    if over_threshold:
        evaluate_session_risk(
            sid, emp_id, "employee",
            _DEVICE_RISK_KILL_WEIGHT,
            "device.wifi_risk_block",
            f"Wi-Fi risk score {risk_score} exceeded 60 ({', '.join(threat_vectors) or 'unspecified threat'})",
        )
        return jsonify({"ok": True, "blocked": True,
                        "msg": "Device risk too high -- this session is being terminated."})

    return jsonify({"ok": True, "blocked": False})
