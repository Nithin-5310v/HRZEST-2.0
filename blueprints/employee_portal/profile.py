# -*- coding: utf-8 -*-
"""employee_portal: self-service profile, bank details, experience, education, photo (web)"""
from database import get_db_connection
from flask import redirect, request, session
from utils.auth import employee_required
from utils.face_utils import _face_recognition_available, detection_array, face_recognition
from utils.helpers import _validate_image_file, encrypt_contact, encrypt_pii, tpath
from utils.storage import save_image
from utils.tenant_files import face_photo_path
from . import employee_portal_bp


@employee_portal_bp.route("/update_my_profile", methods=["POST"])
@employee_required
def update_my_profile():
    emp_id = session["employee_id"]
    _phone, _phone_bidx = encrypt_contact("phone", request.form.get("phone", "").strip() or None)
    fields = {
        "phone": _phone,
        "phone_bidx": _phone_bidx,
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
            phone=%s, phone_bidx=%s, gender=%s, dob=%s, blood_group=%s,
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
    from PIL import Image, ImageOps
    emp_id = session["employee_id"]
    file = request.files.get("photo")
    ok, err = _validate_image_file(file)
    if not ok:
        return redirect(tpath("/employee_portal?photo_error=bad_format#my-profile"))
    try:
        # Upright by its EXIF orientation: it is saved without EXIF, so a
        # phone photo would otherwise be stored sideways.
        img = ImageOps.exif_transpose(Image.open(file.stream)).convert("RGB")
        if _face_recognition_available:
            locs = face_recognition.face_locations(detection_array(img))
            if not locs:
                return redirect(tpath("/employee_portal?photo_error=no_face#my-profile"))
        save_path = face_photo_path(emp_id)
        save_image(img, save_path, "JPEG", quality=90)
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s", (save_path, emp_id))
        db.commit()
        cursor.close()
        db.close()
        return redirect(tpath("/employee_portal?photo_saved=1#my-profile"))
    except Exception:
        return redirect(tpath("/employee_portal?photo_error=failed#my-profile"))
