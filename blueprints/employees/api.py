# -*- coding: utf-8 -*-
"""employees: JSON API for the mobile app: list/register/detail/edit/delete, org chart"""
import os
from database import get_db_connection, transaction
from extensions import app_log, limiter
from flask import jsonify, request
from qr_generator import generate_qr
from utils.auth import api_required, api_role_required, generate_password_hash
from utils.billing_notices import notify_employee_added
from utils.face_utils import _face_recognition_available, face_recognition, detection_array
from utils.helpers import (
    _audit,
    _validate_image_file,
    add_employee_seat_cap_check,
    amount,
    decrypt_pii,
    encrypt_contact,
    int_param,
    validate_emp_id,
    validate_employee_email_domain,
)
from utils.storage import decrypted_stream, save_upload
from utils.tenant_files import face_photo_path
from . import employees_bp
from .registration import _send_welcome_credentials_email


@employees_bp.route("/api/employees", methods=["GET"])
@api_required
@api_role_required("admin")
@limiter.limit("10 per minute")
def api_employees():
    page = max(1, int_param(request.args.get("page", 1)))
    # Up to 500 a page: the app loads the whole staff list (it used to get
    # only the first 50) and this route allows 10 calls a minute.
    per_page = min(500, max(1, int_param(request.args.get("per_page", 50))))
    offset = (page - 1) * per_page
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT COUNT(*) FROM employees")
    total = cursor.fetchone()[0]
    # role and date_of_joining too: the app's edit form starts from this
    # list, and api_edit_employee() saves both as sent -- without them here
    # an edit made in the app blanked the employee's role and joining date.
    cursor.execute("""
        SELECT e.employee_id, e.name, e.email, s.salary_per_day,
               e.role, e.department, e.phone, e.date_of_joining, e.face_image IS NOT NULL
        FROM employees e
        LEFT JOIN salary_config s ON e.employee_id = s.employee_id
        ORDER BY e.name
        LIMIT %s OFFSET %s
    """, (per_page, offset))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "total": total, "page": page, "per_page": per_page,
                    "employees": [
                        # email and phone are stored encrypted (encrypt_contact).
                        {"employee_id": r[0], "name": r[1], "email": decrypt_pii(r[2]) or "",
                         "salary_per_day": float(amount(r[3])),
                         "role": r[4] or "", "department": r[5] or "", "phone": decrypt_pii(r[6]) or "",
                         "date_of_joining": r[7].isoformat() if r[7] else "", "has_photo": bool(r[8])}
                        for r in rows
                    ]})


@employees_bp.route("/api/employees", methods=["POST"])
@api_required
def api_register_employee():
    name = request.form.get("name", "").strip()
    emp_id = request.form.get("emp_id", "").strip()
    email = request.form.get("email", "").strip() or None
    file = request.files.get("face")
    if not name or not emp_id or not file:
        return jsonify({"ok": False, "msg": "name, emp_id and face image required"}), 400
    if not validate_emp_id(emp_id):
        return jsonify({"ok": False, "msg": "emp_id may only contain letters, digits, hyphens and underscores"}), 400
    _domain_error = validate_employee_email_domain(email)
    if _domain_error:
        return jsonify({"ok": False, "msg": _domain_error}), 400
    _seat_error = add_employee_seat_cap_check()
    if _seat_error:
        return jsonify({"ok": False, "msg": _seat_error}), 403
    # Cheap preflight, same non-authoritative role as add_employee_seat_cap_check()
    # above -- the INSERT's own UNIQUE constraint (caught below) is still what
    # actually prevents a race, but without this check first, file.save() a few
    # lines down would silently overwrite the EXISTING employee_id's photo on
    # disk with the rejected duplicate's upload before the INSERT ever ran,
    # even though the request still correctly 400s.
    _dup_check_db = get_db_connection()
    _dup_check_cursor = _dup_check_db.cursor(buffered=True)
    _dup_check_cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
    _dup_exists = _dup_check_cursor.fetchone() is not None
    _dup_check_cursor.close()
    _dup_check_db.close()
    if _dup_exists:
        return jsonify({"ok": False, "msg": "Failed to create employee. Check for duplicate ID."}), 400
    # Validate extension, MIME type, magic bytes and size before writing to disk.
    ok, err = _validate_image_file(file)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 400
    filepath = face_photo_path(emp_id)
    save_upload(file, filepath)
    if _face_recognition_available:
        test_img = detection_array(decrypted_stream(filepath))
        if not face_recognition.face_encodings(test_img):
            os.remove(filepath)
            return jsonify({"ok": False, "msg": "No face detected in uploaded photo."}), 400
    qr_path = generate_qr(emp_id)
    init_pass = request.form.get("password", "").strip() or emp_id
    hashed_pwd = generate_password_hash(init_pass)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        # Authoritative, lock-protected recheck immediately before the
        # INSERT, wrapped in an explicit transaction() -- the pooled
        # connection defaults to autocommit=True, so without an explicit
        # transaction the FOR UPDATE lock taken inside
        # add_employee_seat_cap_check() would release itself the instant
        # that one SELECT statement finished, before this INSERT ever
        # ran, and the race would still be wide open. The earlier
        # add_employee_seat_cap_check() call above (no cursor, own
        # connection) is only a cheap UX preflight -- see that function's
        # docstring.
        _seat_error = None
        with transaction(db):
            _seat_error = add_employee_seat_cap_check(cursor)
            if not _seat_error:
                _enc_email, _email_bidx = encrypt_contact("email", email)
                cursor.execute(
                    "INSERT INTO employees (name, employee_id, email, email_bidx, face_image, qr_code, password, "
                    "force_pin_change) VALUES (%s,%s,%s,%s,%s,%s,%s,1)",
                    (name, emp_id, _enc_email, _email_bidx, filepath, qr_path, hashed_pwd)
                )
        if _seat_error:
            cursor.close()
            db.close()
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({"ok": False, "msg": _seat_error}), 403
    except Exception:
        app_log.error("API employee register failed", exc_info=True)
        db.rollback()
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Failed to create employee. Check for duplicate ID."}), 400
    cursor.close()
    db.close()
    notify_employee_added()
    if email:
        try:
            _send_welcome_credentials_email(emp_id, name, email, init_pass)
        except Exception:
            app_log.error("api_register_employee: welcome email failed", exc_info=True)
    return jsonify({"ok": True, "msg": f"Employee {name} registered."})


@employees_bp.route("/api/employees/<emp_id>", methods=["GET"])
@api_required
@api_role_required("admin", "hr")
@limiter.limit("30 per minute")
def api_employee_detail(emp_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT e.employee_id, e.name, e.email, e.role, e.date_of_joining,
               s.salary_per_day
        FROM employees e
        LEFT JOIN salary_config s ON e.employee_id = s.employee_id
        WHERE e.employee_id = %s
    """, (emp_id,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    return jsonify({"ok": True, "employee": {
        "employee_id": row[0], "name": row[1], "email": row[2],
        "role": row[3], "date_of_joining": str(row[4]) if row[4] else None,
        "salary_per_day": float(amount(row[5]))
    }})


@employees_bp.route("/api/employees/<emp_id>", methods=["PUT"])
@api_required
@api_role_required("admin")
def api_edit_employee(emp_id):
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip() or None
    role = data.get("role", "").strip() or None
    date_of_joining = data.get("date_of_joining", "").strip() or None
    if not name:
        return jsonify({"ok": False, "msg": "name required"}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT role FROM employees WHERE employee_id=%s", (emp_id,))
    _prev_row = cursor.fetchone()
    _prev_role = _prev_row[0] if _prev_row else None
    _enc_email, _email_bidx = encrypt_contact("email", email)
    cursor.execute(
        "UPDATE employees SET name=%s, email=%s, email_bidx=%s, role=%s, date_of_joining=%s WHERE employee_id=%s",
        (name, _enc_email, _email_bidx, role, date_of_joining, emp_id)
    )
    db.commit()
    cursor.close()
    db.close()
    if role != _prev_role:
        _audit("update_employee_role", "employees", emp_id, f"role changed from {_prev_role!r} to {role!r} (via API)")
    return jsonify({"ok": True, "msg": "Employee updated."})


@employees_bp.route("/api/employees/<emp_id>/photo", methods=["POST"])
@api_required
@api_role_required("admin")
@limiter.limit("20 per minute")
def api_update_employee_photo(emp_id):
    """The app's Staff screen setting an employee's face photo. It used to
    post to /api/employee/photo, which changes the photo of the token's OWN
    employee record and ignores which employee was meant: for an owner admin
    with a linked profile that replaced the admin's photo. The face is
    checked before anything is written, so a rejected photo never replaces
    the one on file."""
    file = request.files.get("photo") or request.files.get("face")
    if not file or not file.filename:
        return jsonify({"ok": False, "msg": "No photo provided."}), 400
    ok, err = _validate_image_file(file)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
        if not cursor.fetchone():
            return jsonify({"ok": False, "msg": "Employee not found."}), 404
        if _face_recognition_available:
            file.stream.seek(0)
            if not face_recognition.face_locations(detection_array(file.stream)):
                return jsonify({"ok": False, "msg": "No face found in the photo. Use a clear, front-facing photo."}), 400
            file.stream.seek(0)
        filepath = face_photo_path(emp_id)
        save_upload(file, filepath)
        cursor.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s", (filepath, emp_id))
        db.commit()
    finally:
        cursor.close()
        db.close()
    _audit("update_employee_photo", "employees", emp_id, "photo updated from the mobile app")
    return jsonify({"ok": True, "msg": "Photo updated."})


@employees_bp.route("/api/employees/<emp_id>", methods=["DELETE"])
@api_required
@api_role_required("admin")
def api_delete_employee(emp_id):
    """Bearer-token twin of delete_employee() above -- same
    transaction-wrapped multi-table delete (all-or-nothing, and files are
    only removed AFTER the DB transaction actually commits, not before)
    and the same _audit() call, so a deletion made from the mobile app
    leaves an identical trail to one made from the web admin UI instead
    of a silent, unaudited, non-atomic one."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT face_image, qr_code FROM employees WHERE employee_id=%s", (emp_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    try:
        with transaction(db):
            cursor.execute("DELETE FROM attendance WHERE employee_id=%s", (emp_id,))
            cursor.execute("DELETE FROM salary_config WHERE employee_id=%s", (emp_id,))
            cursor.execute("DELETE FROM leave_requests WHERE employee_id=%s", (emp_id,))
            cursor.execute("DELETE FROM resignation_requests WHERE employee_id=%s", (emp_id,))
            cursor.execute("DELETE FROM tickets WHERE employee_id=%s", (emp_id,))
            cursor.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
    except Exception:
        cursor.close()
        db.close()
        app_log.warning("api_delete_employee failed mid-transaction for %s, rolled back", emp_id)
        return jsonify({"ok": False, "msg": f"Failed to delete employee '{emp_id}'; no changes were made."}), 500
    for path in row:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as _e:
                app_log.warning("Could not delete file %s: %s", path, _e)
    cursor.close()
    db.close()
    _audit("delete_employee", "employees", emp_id, f"Employee {emp_id} permanently deleted (via API)")
    return jsonify({"ok": True, "msg": f"Employee '{emp_id}' deleted."})


@employees_bp.route("/api/org_chart", methods=["GET"])
@api_required
def api_org_chart():
    """Bearer-token twin of blueprints/admin_views.py's api_org_chart_data()
    -- same manager_id-based tree, same shape -- so mobile's Organization
    Chart screen can show real reporting lines instead of falling back to a
    flat department grouping (the only option before this existed). Kept
    as its own route/name (not a second decorator on api_org_chart_data
    itself) since that route is session-only (@admin_required) and already
    serves the web org-chart page at that exact path."""
    dept_filter = request.args.get("dept", "")
    company_id_raw = request.args.get("company_id", "").strip()
    db = get_db_connection()
    cursor = db.cursor()
    query = """
        SELECT e.employee_id, e.name, e.role, e.department,
               e.manager_id, e.face_image
        FROM employees e
        WHERE COALESCE(e.is_active, 1) = 1
    """
    params = []
    # Mobile has no company switcher yet, so this mirrors the web route's
    # own "no active company selected" case (session.get("active_company_id")
    # falsy -> unscoped) rather than guessing a company. Once mobile gains a
    # switcher it can pass ?company_id= to scope the tree the same way web does.
    if company_id_raw.isdigit():
        query += " AND e.company_id = %s"
        params.append(int(company_id_raw))
    if dept_filter:
        query += " AND e.department = %s"
        params.append(dept_filter)
    query += " ORDER BY e.name"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    db.close()

    emp_map = {}
    for r in rows:
        emp_map[r[0]] = {
            "id": r[0],
            "name": r[1],
            "role": r[2] or "Employee",
            "department": r[3] or "",
            "manager_id": r[4],
            "has_photo": bool(r[5] and os.path.exists(r[5])),
            "children": [],
        }

    roots = []
    for emp in emp_map.values():
        mid = emp["manager_id"]
        if mid and mid in emp_map and mid != emp["id"]:
            emp_map[mid]["children"].append(emp)
        else:
            roots.append(emp)

    def sort_tree(node):
        node["children"].sort(key=lambda x: x["name"])
        for child in node["children"]:
            sort_tree(child)
        return node

    roots.sort(key=lambda x: x["name"])
    tree = [sort_tree(r) for r in roots]
    return jsonify({"ok": True, "tree": tree, "total": len(emp_map)})
