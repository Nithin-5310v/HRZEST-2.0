# -*- coding: utf-8 -*-
"""employee_portal: mobile self-service API: salary, attendance, profile, preferences, history, photo, ID card"""
import datetime
from database import get_db_connection
from flask import jsonify, request
from utils.auth import employee_api_required
from utils.helpers import (
    _audit,
    _validate_image_file,
    amount,
    company_today,
    decrypt_pii,
    encrypt_contact,
    encrypt_pii,
)
from utils.storage import save_image
from utils.tenant_files import face_photo_path
from . import _fmt_t, employee_portal_bp


@employee_portal_bp.route("/api/employee/salary", methods=["GET"])
@employee_api_required
def api_employee_salary():
    from flask import g as _g
    emp_id = _g.api_emp_id
    try:
        year = int(request.args.get("year", company_today().year))
        month = int(request.args.get("month", company_today().month))
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
    cursor.close()
    db.close()
    # The one net-pay calculation, as paid (QA BUG-05). This endpoint used
    # to count future days as billable, pay late days in full and ignore
    # incentives and PF/PT/TDS. full_days no longer includes late days.
    from blueprints.payroll import compute_salary_data_for_month
    e = compute_salary_data_for_month(year, month, employee_ids=[emp_id])[0]
    return jsonify({
        "ok": True,
        "month_name": datetime.date(year, month, 1).strftime("%B %Y"),
        "year": year, "month": month,
        "salary": {
            "emp_id": emp_id, "name": emp_row[0], "email": emp_row[1],
            "spd": e["spd"], "billable": e["billable"],
            "full_days": e["full_days"], "half_days": e["half_days"],
            "late_days": e["late_days"], "absent": e["absent"], "leave_days": e["leave_days"],
            "gross": e["gross"], "deduction": e["deduction"], "net": e["net"],
            "incentive": e["incentive"], "pf": e["pf"], "pt": e["pt"], "tds": e["tds"],
            "statutory": e["statutory"],
        }
    })


@employee_portal_bp.route("/api/employee/attendance", methods=["GET"])
@employee_api_required
def api_employee_attendance():
    from flask import g as _g
    emp_id = _g.api_emp_id
    try:
        year = int(request.args.get("year", company_today().year))
        month = int(request.args.get("month", company_today().month))
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
               s.salary_per_day, COALESCE(e.joining_date, e.date_of_joining),
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
            "salary_per_day": float(amount(row[21])),
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
    _phone, _phone_bidx = encrypt_contact("phone", (data.get("phone") or "").strip() or None)
    fields = {
        "phone": _phone,
        "phone_bidx": _phone_bidx,
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
            phone=%s, phone_bidx=%s, gender=%s, dob=%s, blood_group=%s,
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
        save_path = face_photo_path(emp_id)
        save_image(img, save_path, "JPEG", quality=85)
        # Same file the old flat path was, so record it as this company's
        # reference photo (resolve_face_photo reads face_image first).
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s", (save_path, emp_id))
        db.commit()
        cursor.close()
        db.close()
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
