# -*- coding: utf-8 -*-
"""employees: admin actions, edit/delete, employee profile, employee info API"""
import os
import secrets
from database import get_db_connection, transaction
from extensions import app_log, limiter, log_security_event
from flask import flash, jsonify, redirect, render_template, request, session
from utils.auth import (
    HR_ROLE, admin_required, generate_password_hash, record_password_change, require_access, role_required,
)
from utils.dlp import has_pii_clearance, mask_tail
from utils.face_utils import _face_recognition_available, face_recognition, detection_array
from utils.helpers import (
    _audit,
    _db,
    _validate_image_file,
    company_today,
    decrypt_pii,
    decrypt_pii_date,
    encrypt_amount,
    encrypt_contact,
    encrypt_pii,
    hr_scope_denied as _hr_scope_denied,
    notify_new_holiday,
    tpath,
)
from utils.storage import decrypted_stream, save_upload
from utils.tenant_files import face_photo_path
from . import employees_bp


# Which module's edit access each admin_action needs (Settings -> Roles &
# Access) -- the owner Administrator always has it.
_ADMIN_ACTION_MODULES = {"update_face": "employees", "reset_password": "employees",
                         "holiday": "leaves", "salary": "payroll"}


@employees_bp.route("/admin_action", methods=["POST"])
@admin_required
@limiter.limit("20 per minute")
def admin_action():
    action = request.form.get("action")
    from utils.helpers.rbac import can_access
    module = _ADMIN_ACTION_MODULES.get(action)
    if not module or not can_access(module, "edit"):
        flash("You don't have access to that section.", "error")
        return redirect(tpath("/admin"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    if action == "update_face":
        emp_id = request.form["emp_id"]
        file = request.files["face"]
        cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (emp_id,))
        row = cursor.fetchone()
        if not row:
            flash(f"Employee ID '{emp_id}' not found.", "error")
            cursor.close()
            db.close()
            return redirect(tpath("/admin"))
        name = row[0]
        _img_ok, _img_err = _validate_image_file(file)
        if not _img_ok:
            flash(_img_err, "error")
            cursor.close()
            db.close()
            return redirect(tpath("/admin"))
        filepath = face_photo_path(emp_id)
        save_upload(file, filepath)
        if _face_recognition_available:
            test_img = detection_array(decrypted_stream(filepath))
            if not face_recognition.face_encodings(test_img):
                os.remove(filepath)
                flash("No face detected in the uploaded photo. Please upload a clear, well-lit front-facing photo.", "error")
                cursor.close()
                db.close()
                return redirect(tpath("/admin"))
        cursor.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s", (filepath, emp_id))
        db.commit()
        flash(f"Face photo updated successfully for '{name}' (ID: {emp_id}).", "success")

    elif action == "reset_password":
        emp_id = request.form.get("emp_id", "").strip()
        cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (emp_id,))
        row = cursor.fetchone()
        if not row:
            flash(f"Employee ID '{emp_id}' not found.", "error")
        else:
            # Random one-time password, shown to this admin once (the flash
            # below) and never stored or emailed in plaintext.
            # must_change_password makes the employee replace it at their
            # next login: /force_change_pin on the web, and
            # /api/employee/login refuses to issue a token until then.
            temp_password = secrets.token_urlsafe(9)
            cursor.execute(
                "UPDATE employees SET password=%s, must_change_password=1 WHERE employee_id=%s",
                (generate_password_hash(temp_password), emp_id)
            )
            record_password_change(cursor, "employees", emp_id)
            db.commit()
            flash(
                f"Password reset for '{row[0]}' ({emp_id}). Temporary password: {temp_password} -- shown only "
                "once, so share it with the employee privately now. They must set a new password at their next "
                "sign-in.", "success")

    elif action == "holiday":
        cursor.execute(
            "INSERT INTO holidays (date, name) VALUES (%s,%s)",
            (request.form["date"], request.form["holiday_name"])
        )
        db.commit()
        notify_new_holiday(cursor, db, request.form["holiday_name"], request.form["date"])

    elif action == "salary":
        emp_id = request.form["emp_id"]
        salary = encrypt_amount(request.form["salary"])
        cursor.execute("SELECT 1 FROM salary_config WHERE employee_id=%s", (emp_id,))
        if cursor.fetchone():
            cursor.execute(
                "UPDATE salary_config SET salary_per_day=%s WHERE employee_id=%s",
                (salary, emp_id)
            )
        else:
            cursor.execute(
                "INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s,%s)",
                (emp_id, salary)
            )
        db.commit()

    cursor.close()
    db.close()
    if request.form.get("next") == "settings":
        return redirect(tpath("/settings?tab=email"))
    return redirect(tpath("/admin"))


# _hr_scope_denied is utils/helpers.py's hr_scope_denied (imported above under
# this name so every existing call site below -- add_employee/edit_employee/
# employee_detail/etc. -- needed no change). One difference from the shared
# version's docstring worth noting here: an HR session viewing its OWN record
# (emp_id == admin_username) is always allowed even if that row isn't self-
# assigned -- this is what backs templates/admin_base.html's "My Profile"
# sidebar link, since an HR admin's own assigned_hr_username is usually NULL
# or set to a different HR, not themselves.


@employees_bp.route("/delete_employee/<emp_id>", methods=["POST"])
@admin_required
@require_access("employees", "edit")
@limiter.limit("10 per minute")
def delete_employee(emp_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT face_image, qr_code FROM employees WHERE employee_id=%s", (emp_id,))
    row = cursor.fetchone()
    if row:
        try:
            with transaction(db):
                cursor.execute("DELETE FROM attendance WHERE employee_id=%s", (emp_id,))
                cursor.execute("DELETE FROM salary_config WHERE employee_id=%s", (emp_id,))
                cursor.execute("DELETE FROM leave_requests WHERE employee_id=%s", (emp_id,))
                cursor.execute("DELETE FROM resignation_requests WHERE employee_id=%s", (emp_id,))
                cursor.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
        except Exception:
            cursor.close()
            db.close()
            app_log.warning("delete_employee failed mid-transaction for %s, rolled back", emp_id)
            flash(f"Failed to delete employee '{emp_id}'; no changes were made.", "error")
            return redirect(tpath("/employees"))
        for path in row:
            if path and os.path.exists(path):
                os.remove(path)
        _audit("delete_employee", "employees", emp_id, f"Employee {emp_id} permanently deleted")
        flash(f"Employee '{emp_id}' deleted successfully.", "success")
    else:
        flash(f"Employee '{emp_id}' not found.", "error")
    cursor.close()
    db.close()
    return redirect(tpath("/employees"))


@employees_bp.route("/edit_employee/<emp_id>", methods=["GET"])
@admin_required
def edit_employee_page(emp_id):
    if _hr_scope_denied(emp_id):
        return "Employee not found", 404
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT employee_id, name, email, role FROM employees WHERE employee_id=%s", (emp_id,)
    )
    emp = cursor.fetchone()
    cursor.close()
    db.close()
    if not emp:
        return "Employee not found", 404
    return render_template("edit_employee.html", emp=emp, active_nav="employees")


@employees_bp.route("/employee_profile/<emp_id>")
@admin_required
def employee_profile(emp_id):
    if _hr_scope_denied(emp_id):
        return "Employee not found", 404
    today = company_today()
    with _db() as (cursor, db):
        cursor.execute("""
            SELECT employee_id, name, email, role, phone, gender, dob, blood_group,
                   date_of_joining, department, manager_name, work_mode,
                   address, city, state, pincode,
                   emergency_contact_name, emergency_contact_phone, emergency_contact_relation,
                   aadhar_number, pan_number, bank_name, bank_account, bank_ifsc, uan_number,
                   about_me, face_image, shift_id
            FROM employees WHERE employee_id=%s
        """, (emp_id,))
        emp = cursor.fetchone()
        if not emp:
            return "Employee not found", 404
        # Decrypt PII: [5]=gender [7]=blood_group [12]=address [13]=city
        # [14]=state [15]=pincode [16]=ec_name [17]=ec_phone [18]=ec_relation
        # [19]=aadhar_number [20]=pan_number [21]=bank_name [22]=bank_account
        # [23]=bank_ifsc [24]=uan_number. [6]=dob is handled separately since
        # the template calls .strftime() on it (see decrypt_pii_date).
        emp = list(emp)
        for _pii_idx in (5, 7, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24):
            if _pii_idx < len(emp):
                emp[_pii_idx] = decrypt_pii(emp[_pii_idx])
        emp[6] = decrypt_pii_date(emp[6])

        # DLP: aadhar/pan/bank_account/uan are only shown unmasked to the
        # finance/HR-clearance tier (admin_role=="admin") -- mirrors the
        # restriction payroll.py's view_payslip already enforces for
        # payslips. manager/soc_analyst still get the rest of the profile.
        if not has_pii_clearance():
            emp[19] = mask_tail(emp[19])  # aadhar_number
            emp[20] = mask_tail(emp[20])  # pan_number
            emp[22] = mask_tail(emp[22])  # bank_account
            emp[24] = mask_tail(emp[24])  # uan_number
        else:
            log_security_event("data.reveal", "Admin viewed unmasked PII on employee profile",
                               level="WARNING", identifier=session.get("admin_username"),
                               resource_type="employee_profile", resource_id=emp_id)

        # Attendance this month
        cursor.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status IN ('Present','Late Login') THEN 1 ELSE 0 END) AS present,
                SUM(CASE WHEN status='Absent' THEN 1 ELSE 0 END) AS absent,
                SUM(CASE WHEN status='Late Login' THEN 1 ELSE 0 END) AS late,
                SUM(CASE WHEN attendance_type='Half Day' THEN 1 ELSE 0 END) AS halfday
            FROM attendance
            WHERE employee_id=%s AND EXTRACT(MONTH FROM date)=%s AND EXTRACT(YEAR FROM date)=%s
        """, (emp_id, today.month, today.year))
        att = cursor.fetchone()

        # Last 5 attendance records
        cursor.execute("""
            SELECT date, login_time, logout_time, status, attendance_type
            FROM attendance WHERE employee_id=%s
            ORDER BY date DESC LIMIT 5
        """, (emp_id,))
        recent_att = cursor.fetchall()

        # Leave summary this year
        cursor.execute("""
            SELECT lt.name, COUNT(*) as cnt
            FROM leave_requests lr
            LEFT JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.employee_id=%s AND lr.status='Approved'
              AND EXTRACT(YEAR FROM lr.leave_date)=%s
            GROUP BY lt.name
        """, (emp_id, today.year))
        leave_used = cursor.fetchall()

        # Pending leaves
        cursor.execute("""
            SELECT COUNT(*) FROM leave_requests
            WHERE employee_id=%s AND status='Pending'
        """, (emp_id,))
        pending_leaves = cursor.fetchone()[0]

        # Salary config
        cursor.execute("SELECT salary_per_day FROM salary_config WHERE employee_id=%s", (emp_id,))
        sal_row = cursor.fetchone()
        salary_per_day = sal_row[0] if sal_row else None
        salary_hidden = bool(salary_per_day) and not has_pii_clearance()
        if salary_hidden:
            salary_per_day = None

        # Open tickets
        cursor.execute("""
            SELECT COUNT(*) FROM tickets WHERE employee_id=%s AND status IN ('Open','In Progress')
        """, (emp_id,))
        open_tickets = cursor.fetchone()[0]

        # Shift info
        shift_name = None
        if emp[27]:
            cursor.execute("SELECT name, start_time, end_time FROM shifts WHERE id=%s", (emp[27],))
            sh = cursor.fetchone()
            if sh:
                shift_name = f"{sh[0]} ({sh[1]} – {sh[2]})"

        # Which HR manages this employee; the company admin can change it here.
        from blueprints.hr_team import hr_choices, hr_label
        cursor.execute("SELECT assigned_hr_username FROM employees WHERE employee_id=%s", (emp_id,))
        assigned_hr = cursor.fetchone()[0]
        assigned_hr_label = hr_label(cursor, assigned_hr)
        can_reassign_hr = session.get("admin_role") == "admin"
        hr_options = hr_choices(cursor) if can_reassign_hr else []

    return render_template("employee_profile.html",
                           emp=emp,
                           att=att,
                           recent_att=recent_att,
                           leave_used=leave_used,
                           pending_leaves=pending_leaves,
                           salary_per_day=salary_per_day,
                           salary_hidden=salary_hidden,
                           open_tickets=open_tickets,
                           shift_name=shift_name,
                           today=today,
                           active_nav="employees",
                           assigned_hr=assigned_hr, assigned_hr_label=assigned_hr_label,
                           can_reassign_hr=can_reassign_hr, hr_options=hr_options,
                           )


@employees_bp.route("/employee_profile/<emp_id>/assigned_hr", methods=["POST"])
@role_required("admin")
def employee_profile_assign_hr(emp_id):
    """The company admin changes which HR manages this employee (or leaves
    them unassigned). Only an HR with dashboard access can be chosen."""
    from blueprints.hr_team import hr_label, is_assignable_hr
    new_hr = request.form.get("assigned_hr_username", "").strip() or None
    with _db() as (cursor, db):
        cursor.execute("SELECT assigned_hr_username, name FROM employees WHERE employee_id=%s", (emp_id,))
        row = cursor.fetchone()
        if not row:
            flash("Employee not found.", "error")
            return redirect(tpath("/employees"))
        prev_hr, name = row
        if new_hr and not is_assignable_hr(cursor, new_hr):
            flash("Choose an HR who has HR dashboard access.", "error")
            return redirect(tpath(f"/employee_profile/{emp_id}"))
        cursor.execute("UPDATE employees SET assigned_hr_username=%s WHERE employee_id=%s", (new_hr, emp_id))
        db.commit()
        label = hr_label(cursor, new_hr)
    if new_hr != prev_hr:
        _audit("reassign_employee_hr", "employees", emp_id,
               f"assigned HR changed from {prev_hr or 'Unassigned'!r} to {new_hr or 'Unassigned'!r}")
    flash(f"{name} is now managed by {label}." if new_hr else f"{name} no longer has an assigned HR.", "success")
    return redirect(tpath(f"/employee_profile/{emp_id}"))


@employees_bp.route("/edit_employee", methods=["POST"])
@admin_required
def edit_employee():
    emp_id = request.form["emp_id"].strip()
    if _hr_scope_denied(emp_id):
        flash("Employee not found.", "error")
        return redirect(tpath("/employees"))
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip() or None
    role = request.form.get("role", "").strip() or None
    # Access Role is set ONLY from Settings -> Roles & Access now
    # (assign_employee_role(), which also keeps the admin_users shadow
    # account that login routing actually reads in sync -- see
    # utils/helpers/rbac.py). This route never touches it -- not even for
    # the owner Administrator -- so editing someone's name/phone/department
    # here can never silently clear or change their access role, and the two
    # screens can't drift apart on who the real assignment mechanism is.
    with _db() as (_arc, _ard):
        _arc.execute("SELECT access_role FROM employees WHERE employee_id=%s", (emp_id,))
        _arr = _arc.fetchone()
    access_role = _arr[0] if _arr else None
    date_of_joining = request.form.get("date_of_joining", "").strip() or None
    department = request.form.get("department", "").strip() or None
    manager_name = request.form.get("manager_name", "").strip() or None
    manager_id = request.form.get("manager_id", "").strip() or None
    # phone deliberately plaintext -- see the matching comment in add_employee().
    phone = request.form.get("phone", "").strip() or None
    gender = encrypt_pii(request.form.get("gender", "").strip() or None)
    dob = encrypt_pii(request.form.get("dob", "").strip() or None)
    blood_group = encrypt_pii(request.form.get("blood_group", "").strip() or None)
    shift_id_raw = request.form.get("shift_id", "").strip()
    shift_id = int(shift_id_raw) if shift_id_raw else None
    address = encrypt_pii(request.form.get("address", "").strip() or None)
    city = encrypt_pii(request.form.get("city", "").strip() or None)
    state = encrypt_pii(request.form.get("state", "").strip() or None)
    pincode = encrypt_pii(request.form.get("pincode", "").strip() or None)
    ec_name = encrypt_pii(request.form.get("ec_name", "").strip() or None)
    ec_phone = encrypt_pii(request.form.get("ec_phone", "").strip() or None)
    ec_rel = encrypt_pii(request.form.get("ec_rel", "").strip() or None)
    work_mode = request.form.get("work_mode", "office").strip() or "office"
    work_lat_raw = request.form.get("work_lat", "").strip()
    work_lon_raw = request.form.get("work_lon", "").strip()
    work_lat = float(work_lat_raw) if work_lat_raw else None
    work_lon = float(work_lon_raw) if work_lon_raw else None

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    # Fetched before the UPDATE specifically to audit a role (job-title)
    # change -- this is HR display data, not admin_users.role (the real
    # privilege field, which has no write path at all outside account
    # creation), but it had no audit trail on any write path before.
    cursor.execute("SELECT role, assigned_hr_username FROM employees WHERE employee_id=%s", (emp_id,))
    _prev_row = cursor.fetchone()
    _prev_role = _prev_row[0] if _prev_row else None
    _prev_hr = _prev_row[1] if _prev_row else None

    # Reassigning which HR manages this employee is an admin-only action --
    # an HR-scoped editor (already confirmed above to own this employee)
    # never touches assigned_hr_username here, so they can't hand their
    # own employee off to a different HR account. An admin editor may
    # reassign to any real, active HR account, or clear it back to
    # unassigned; anything else in the form is silently ignored (not
    # trusted as free text) rather than erroring the whole save.
    _reassign_sql = ""
    _reassign_params = ()
    _new_hr = _prev_hr
    if session.get("admin_role") != HR_ROLE:
        _new_hr_raw = request.form.get("assigned_hr_username", "").strip()
        if _new_hr_raw:
            from blueprints.hr_team import is_assignable_hr
            if is_assignable_hr(cursor, _new_hr_raw):
                _reassign_sql = ", assigned_hr_username=%s"
                _reassign_params = (_new_hr_raw,)
                _new_hr = _new_hr_raw
        else:
            _reassign_sql = ", assigned_hr_username=NULL"
            _new_hr = None

    _enc_email, _email_bidx = encrypt_contact("email", email)
    _enc_phone, _phone_bidx = encrypt_contact("phone", phone)
    cursor.execute(
        "UPDATE employees SET name=%s, email=%s, email_bidx=%s, role=%s, access_role=%s, date_of_joining=%s, "
        "department=%s, manager_name=%s, manager_id=%s, phone=%s, phone_bidx=%s, gender=%s, dob=%s, blood_group=%s, "
        "shift_id=%s, address=%s, city=%s, state=%s, pincode=%s, "
        "emergency_contact_name=%s, emergency_contact_phone=%s, emergency_contact_relation=%s, "
        f"work_mode=%s, work_lat=%s, work_lon=%s{_reassign_sql} "  # nosec B608 -- _reassign_sql is a hardcoded literal, never user input
        "WHERE employee_id=%s",
        (name, _enc_email, _email_bidx, role, access_role, date_of_joining, department, manager_name, manager_id,
         _enc_phone, _phone_bidx, gender, dob, blood_group, shift_id,
         address, city, state, pincode,
         ec_name, ec_phone, ec_rel,
         work_mode, work_lat, work_lon) + _reassign_params + (emp_id,)
    )
    db.commit()
    cursor.close()
    db.close()
    if role != _prev_role:
        _audit("update_employee_role", "employees", emp_id, f"role changed from {_prev_role!r} to {role!r}")
    if _reassign_sql and _new_hr != _prev_hr:
        _audit("reassign_employee_hr", "employees", emp_id,
               f"assigned HR changed from {_prev_hr or 'Unassigned'!r} to {_new_hr or 'Unassigned'!r}")
    flash(f"Employee '{emp_id}' updated successfully.", "success")
    return redirect(tpath("/employees"))


@employees_bp.route("/api/employee_info/<emp_id>")
@admin_required
def api_employee_info(emp_id):
    if _hr_scope_denied(emp_id):
        return jsonify({"error": "not found"}), 404
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT employee_id, name, role, email, date_of_joining, "
        "work_mode, work_lat, work_lon, department, manager_name, face_image, qr_code, "
        "phone, gender, dob, blood_group, shift_id, "
        "address, city, state, pincode, "
        "emergency_contact_name, emergency_contact_phone, emergency_contact_relation, "
        "COALESCE(manager_id,''), assigned_hr_username, COALESCE(access_role,'') "
        "FROM employees WHERE employee_id=%s", (emp_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    (eid, name, role, email, doj, wm, wlat, wlon, dept, mgr, face_image, qr_code,
     phone, gender, dob, blood_group, shift_id,
     address, city, state, pincode,
     ec_name, ec_phone, ec_rel, mgr_id, assigned_hr_username, access_role) = row
    dob_date = decrypt_pii_date(dob)
    return jsonify({
        "emp_id": eid,
        "name": name or "",
        "role": role or "",
        "access_role": access_role or "",
        "email": email or "",
        "doj": doj.strftime("%Y-%m-%d") if doj else "",
        "work_mode": wm or "office",
        "work_lat": str(wlat) if wlat else "",
        "work_lon": str(wlon) if wlon else "",
        "department": dept or "",
        "manager_name": mgr or "",
        "manager_id": mgr_id or "",
        "assigned_hr_username": assigned_hr_username or "",
        "has_photo": bool(face_image and os.path.exists(face_image)),
        "has_qr": bool(qr_code and os.path.exists(qr_code)),
        "phone": phone or "",
        "gender": decrypt_pii(gender) or "",
        "dob": dob_date.strftime("%Y-%m-%d") if dob_date else "",
        "blood_group": decrypt_pii(blood_group) or "",
        "shift_id": shift_id or "",
        "address": decrypt_pii(address) or "",
        "city": decrypt_pii(city) or "",
        "state": decrypt_pii(state) or "",
        "pincode": decrypt_pii(pincode) or "",
        "ec_name": decrypt_pii(ec_name) or "",
        "ec_phone": decrypt_pii(ec_phone) or "",
        "ec_rel": decrypt_pii(ec_rel) or "",
    })
