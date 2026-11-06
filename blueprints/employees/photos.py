# -*- coding: utf-8 -*-
"""employees: reference photo upload, QR codes, photo serving"""
import os
from database import get_db_connection
from flask import abort, flash, redirect, request, send_file, session
from io import BytesIO
from qr_generator import generate_qr
from utils.auth import admin_required, any_user_required
from utils.face_utils import _face_recognition_available, face_recognition
from utils.helpers import _validate_image_file, hr_scope_denied as _hr_scope_denied, tpath
from utils.storage import decrypted_stream, open_private, save_upload, send_image
from utils.tenant_files import face_photo_path, resolve_face_photo
from . import employees_bp


@employees_bp.route("/update_employee_photo/<emp_id>", methods=["POST"])
@admin_required
def update_employee_photo(emp_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (emp_id,))
    if not cursor.fetchone():
        flash("Employee not found.", "error")
        cursor.close()
        db.close()
        return redirect(tpath("/employees"))

    file = request.files.get("face")
    if not file or not file.filename:
        flash("No photo file provided.", "error")
        cursor.close()
        db.close()
        return redirect(tpath("/employees"))

    _img_ok, _img_err = _validate_image_file(file)
    if not _img_ok:
        flash(_img_err, "error")
        cursor.close()
        db.close()
        return redirect(tpath("/employees"))

    filepath = face_photo_path(emp_id)
    save_upload(file, filepath)

    if _face_recognition_available:
        test_img = face_recognition.load_image_file(decrypted_stream(filepath))
        if not face_recognition.face_encodings(test_img):
            os.remove(filepath)
            flash("No face detected in the uploaded photo. Please upload a clear front-facing photo.", "error")
            cursor.close()
            db.close()
            return redirect(tpath("/employees"))

    cursor.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s", (filepath, emp_id))
    db.commit()
    flash(f"Photo updated for employee '{emp_id}'.", "success")
    cursor.close()
    db.close()
    return redirect(tpath("/employees"))


@employees_bp.route("/regenerate_qr/<emp_id>", methods=["POST"])
@admin_required
def regenerate_qr(emp_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (emp_id,))
    if not cursor.fetchone():
        flash("Employee not found.", "error")
        cursor.close()
        db.close()
        return redirect(tpath("/employees"))
    qr_path = generate_qr(emp_id)
    cursor.execute("UPDATE employees SET qr_code=%s WHERE employee_id=%s", (qr_path, emp_id))
    db.commit()
    flash(f"QR code regenerated for '{emp_id}'.", "success")
    cursor.close()
    db.close()
    return redirect(tpath("/employees"))


@employees_bp.route("/employees/<emp_id>/qr")
@admin_required
def admin_view_qr(emp_id):
    """Admin/HR-side twin of blueprints/employee_portal.py's my_qr() -- the
    only two sanctioned ways to ever read an employee's attendance QR back
    out, now that generate_qr() saves it to private storage instead of
    static/ (see that function's own docstring for why: the QR image is a
    bearer credential, not just a picture). HR sessions are scoped to
    their assigned employees exactly like every other single-record
    employee route in this file; admin is unrestricted."""
    if _hr_scope_denied(emp_id):
        return "Employee not found", 404
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
    found = cursor.fetchone()
    cursor.close()
    db.close()
    if not found:
        return "Employee not found", 404
    stored_ref = generate_qr(emp_id)
    return send_file(BytesIO(open_private(stored_ref)), mimetype="image/png",
                     as_attachment=True, download_name=f"QR_{emp_id}.png")


@employees_bp.route("/dataset/<path:filename>")
@admin_required
def serve_dataset(filename):
    """An employee's photo in the CURRENT company only. Used to serve any
    path under the shared dataset/ folder, so any company's admin could
    read every company's face photos (and face_logs) by name."""
    stem, ext = os.path.splitext(filename)
    if ext.lower() != ".jpg" or not stem or "/" in stem or "\\" in stem:
        abort(404)
    path = resolve_face_photo(stem)
    if not path:
        abort(404)
    return send_image(path, max_age=0)


@employees_bp.route("/my_photo")
@any_user_required
def my_photo():
    emp_id = session.get("employee_id")
    if not emp_id:
        return "", 403
    photo_path = resolve_face_photo(emp_id)
    if not photo_path:
        return "", 404
    return send_image(photo_path, max_age=0)
