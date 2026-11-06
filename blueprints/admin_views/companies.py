# -*- coding: utf-8 -*-
"""admin_views: company switching, company PIN, company CRUD and images"""
import os
import secrets
import shutil
from database import get_db_connection, transaction
from extensions import app, app_log
from flask import flash, redirect, request, session
from qr_generator import generate_qr
from utils.auth import role_required
from utils.helpers import _safe_redirect, _validate_image_file, invalidate_companies_cache, tpath
from utils.tenant_files import face_photo_path, legacy_face_photo_path
from . import admin_views_bp


@admin_views_bp.route("/switch_company", methods=["POST"])
@role_required("admin")
def switch_company():
    cid = request.form.get("company_id", "").strip()
    pin = request.form.get("pin", "").strip()
    dest = _safe_redirect(request.form.get("next", ""), "/admin")
    if not cid:
        session.pop("active_company_id", None)
        flash("Switched to: All Companies", "success")
        return redirect(dest)
    try:
        cid = int(cid)
    except ValueError:
        return redirect(dest)
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    cur.execute("SELECT name, COALESCE(pin,'') FROM companies WHERE id=%s", (cid,))
    row = cur.fetchone()
    cur.close()
    db.close()
    if not row:
        flash("Company not found.", "error")
        return redirect(dest)
    cname, stored_pin = row
    if stored_pin and not secrets.compare_digest(stored_pin, pin):
        flash(f"Incorrect PIN for {cname}.", "error")
        return redirect(dest + ("&" if "?" in dest else "?") + "pin_error=1&pin_cid=" + str(cid))
    session["active_company_id"] = cid
    flash(f"Switched to: {cname}", "success")
    return redirect(dest)


@admin_views_bp.route("/clear_company", methods=["POST"])
@role_required("admin")
def clear_company():
    session.pop("active_company_id", None)
    flash("Viewing all companies.", "success")
    target = _safe_redirect(request.form.get("next", ""), "/admin")  # same-site paths only
    return redirect(target)


@admin_views_bp.route("/set_company_pin", methods=["POST"])
@role_required("admin")
def set_company_pin():
    cid = request.form.get("company_id", "").strip()
    pin = request.form.get("pin", "").strip()
    if not cid:
        flash("Invalid request.", "error")
        return redirect(tpath("/settings?tab=company"))
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    cur.execute("UPDATE companies SET pin=%s WHERE id=%s", (pin or None, int(cid)))
    db.commit()
    cur.close()
    db.close()
    invalidate_companies_cache()
    flash("PIN " + ("set." if pin else "removed."), "success")
    return redirect(tpath("/settings?tab=company"))


@admin_views_bp.route("/companies")
@role_required("admin")
def view_companies():
    return redirect(tpath("/settings?tab=company"))


def _save_company_image(file_storage, cid, kind):
    """Save an uploaded company logo / ID-card-template image under static/,
    deterministically named by company id so re-uploads just overwrite the
    previous file. `kind` is 'logo', 'front' or 'back'. Returns the relative
    path (under static/) to store in the DB."""
    ext = os.path.splitext(file_storage.filename)[1].lower()
    folder_name = "company_logos" if kind == "logo" else "id_card_templates"
    folder = os.path.join(app.root_path, "static", folder_name)
    os.makedirs(folder, exist_ok=True)
    filename = f"co_{cid}_{kind}{ext}"
    file_storage.save(os.path.join(folder, filename))
    return f"{folder_name}/{filename}"


def _delete_company_image(rel_path):
    """Best-effort cleanup of a previously-stored company logo/template file."""
    if not rel_path:
        return
    try:
        os.remove(os.path.join(app.root_path, "static", rel_path))
    except OSError as exc:
        app_log.warning("Could not remove company image file %s: %s", rel_path, exc)


@admin_views_bp.route("/companies/add", methods=["POST"])
@role_required("admin")
def add_company():
    name = request.form.get("name", "").strip()
    code = request.form.get("code", "").strip().upper()[:20] or None
    address = request.form.get("address", "").strip() or None
    website = request.form.get("website", "").strip() or None
    email = request.form.get("email", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    redirect_to = request.form.get("redirect_to", "companies")
    dest = "/settings?tab=company" if redirect_to == "settings" else "/companies"
    if not name:
        flash("Company name is required.", "error")
        return redirect(dest)

    logo_file = request.files.get("logo")
    if logo_file and logo_file.filename:
        logo_ok, logo_err = _validate_image_file(logo_file)
        if not logo_ok:
            flash(f"Company logo: {logo_err}", "error")
            return redirect(dest)

    w_days = ",".join(request.form.getlist("working_days")) or "Mon,Tue,Wed,Thu,Fri"
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    shift_names = request.form.getlist("shift_name[]")
    shift_starts = request.form.getlist("shift_start[]")
    shift_halfs = request.form.getlist("shift_half[]")
    shift_ends = request.form.getlist("shift_end[]")
    break_names = request.form.getlist("break_name[]")
    break_times = request.form.getlist("break_time[]")
    break_durs = request.form.getlist("break_duration[]")

    try:
        with transaction(db):
            cursor.execute(
                "INSERT INTO companies (name, code, working_days, address, website, email, phone) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (name, code, w_days, address, website, email, phone)
            )
            new_cid = cursor.fetchone()[0]

            for sname, sstart, shalf, send in zip(shift_names, shift_starts, shift_halfs, shift_ends):
                sname = sname.strip()
                sstart = sstart.strip()
                shalf = shalf.strip()
                send = send.strip()
                if sname and sstart and shalf and send:
                    cursor.execute(
                        "INSERT INTO shifts (name, start_time, half_time, end_time, company_id) VALUES (%s,%s,%s,%s,%s)",
                        (sname,
                         sstart + ":00" if len(sstart) == 5 else sstart,
                         shalf + ":00" if len(shalf) == 5 else shalf,
                         send + ":00" if len(send) == 5 else send,
                         new_cid)
                    )

            for bname, btime, bdur in zip(break_names, break_times, break_durs):
                bname = bname.strip()
                btime = btime.strip()
                bdur = bdur.strip()
                if bname and btime and bdur.isdigit():
                    cursor.execute(
                        "INSERT INTO break_config (break_name, break_time, duration_minutes, company_id) VALUES (%s,%s,%s,%s)",
                        (bname, btime + ":00" if len(btime) == 5 else btime, int(bdur), new_cid)
                    )
    except Exception:
        cursor.close()
        db.close()
        app_log.warning("add_company failed mid-transaction for %r, rolled back", name)
        flash("Failed to add company; no changes were made.", "error")
        return redirect(dest)

    if logo_file and logo_file.filename:
        logo_path = _save_company_image(logo_file, new_cid, "logo")
        cursor.execute("UPDATE companies SET logo_path=%s WHERE id=%s", (logo_path, new_cid))
        db.commit()

    cursor.close()
    db.close()
    invalidate_companies_cache()
    flash(f"Company '{name}' added.", "success")
    return redirect(dest)


@admin_views_bp.route("/companies/<int:cid>/edit", methods=["POST"])
@role_required("admin")
def edit_company(cid):
    name = request.form.get("name", "").strip()
    new_code = (request.form.get("code", "").strip().upper()[:20]) or None
    address = request.form.get("address", "").strip() or None
    website = request.form.get("website", "").strip() or None
    email = request.form.get("email", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    redirect_to = request.form.get("redirect_to", "companies")
    dest = "/settings?tab=company" if redirect_to == "settings" else "/companies"

    if not name:
        flash("Company name is required.", "error")
        return redirect(dest)

    logo_file = request.files.get("logo")
    if logo_file and logo_file.filename:
        logo_ok, logo_err = _validate_image_file(logo_file)
        if not logo_ok:
            flash(f"Company logo: {logo_err}", "error")
            return redirect(dest)

    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    w_days = ",".join(request.form.getlist("working_days")) or "Mon,Tue,Wed,Thu,Fri"

    cursor.execute("SELECT COALESCE(code,''), COALESCE(logo_path,'') FROM companies WHERE id=%s", (cid,))
    row = cursor.fetchone()
    old_code = (row[0] or "").strip().upper() if row else ""
    old_logo_path = row[1] if row and row[1] else None

    renamed_count = 0
    to_rename = []
    try:
        with transaction(db):
            cursor.execute(
                "UPDATE companies SET name=%s, code=%s, working_days=%s, address=%s, "
                "website=%s, email=%s, phone=%s WHERE id=%s",
                (name, new_code, w_days, address, website, email, phone, cid)
            )

            if old_code and new_code and old_code != new_code:
                cursor.execute(
                    "SELECT employee_id FROM employees WHERE company_id=%s AND employee_id LIKE %s",
                    (cid, old_code + "%")
                )
                to_rename = [
                    (r[0], new_code + r[0][len(old_code):])
                    for r in cursor.fetchall() if r[0].startswith(old_code)
                ]

                related_tables = [
                    "attendance", "salary_config", "leave_requests", "notifications",
                    "resignation_requests", "tickets", "employee_incentives",
                    "employee_experience", "employee_education", "leave_balances",
                    "employee_documents", "performance_reviews", "overtime_records",
                    "compoff_balance", "employee_onboarding",
                ]

                # One UPDATE per related table for the whole renamed batch (via a
                # Postgres UNNEST mapping table), instead of one UPDATE per table
                # PER renamed employee -- a rename of N employees previously issued
                # N*16 round trips here; this issues a flat 16 regardless of N.
                old_ids = [p[0] for p in to_rename]
                new_ids = [p[1] for p in to_rename]
                for tbl in related_tables:
                    # No inner try/except here on purpose: a failure partway
                    # through this per-table loop must propagate to the
                    # outer except below (which rolls the whole rename back
                    # and flashes "no changes were made") -- swallowing it
                    # locally previously let some related tables keep the
                    # old employee_id while others got the new one, leaving
                    # the rename applied inconsistently across tables.
                    cursor.execute(
                        f"UPDATE {tbl} AS t SET employee_id = m.new_eid "  # nosec B608 -- tbl is one of the fixed related_tables literals above, never user input  # noqa: E501
                        f"FROM (SELECT * FROM UNNEST(%s::text[], %s::text[]) AS m(old_eid, new_eid)) AS m "
                        f"WHERE t.employee_id = m.old_eid",
                        (old_ids, new_ids)
                    )

                for old_eid, new_eid in to_rename:
                    new_img = face_photo_path(new_eid)
                    # The QR signs the employee ID, so a renamed employee
                    # needs a freshly signed badge, not the old file renamed.
                    new_qr = generate_qr(new_eid)
                    cursor.execute(
                        "UPDATE employees SET employee_id=%s, face_image=%s, qr_code=%s "
                        "WHERE employee_id=%s AND company_id=%s",
                        (new_eid, new_img, new_qr, old_eid, cid)
                    )
                    renamed_count += 1
    except Exception:
        cursor.close()
        db.close()
        app_log.warning("edit_company failed mid-transaction for company %s, rolled back", cid)
        flash("Failed to update company; no changes were made.", "error")
        return redirect(dest)

    # File renames happen only after the DB transaction has committed, so a
    # rollback above never leaves files renamed out from under DB rows that
    # still point at the old employee_id.
    for old_eid, new_eid in to_rename:
        new_img = face_photo_path(new_eid)
        own_img = face_photo_path(old_eid)
        shared_img = legacy_face_photo_path(old_eid)
        try:
            if os.path.exists(own_img):
                os.replace(own_img, new_img)
            elif os.path.exists(shared_img):
                # Pre-migration flat file: copy, never move -- another
                # company's employee with the same ID may still use it.
                shutil.copy2(shared_img, new_img)
        except Exception as exc:
            app_log.warning("Could not move employee photo for %s -> %s during company edit: %s", old_eid, new_eid, exc)

    if logo_file and logo_file.filename:
        new_logo_path = _save_company_image(logo_file, cid, "logo")
        cursor.execute("UPDATE companies SET logo_path=%s WHERE id=%s", (new_logo_path, cid))
        db.commit()
        if old_logo_path and old_logo_path != new_logo_path:
            _delete_company_image(old_logo_path)

    if to_rename:
        flash(
            f"Company updated. {renamed_count} employee ID(s) renamed: "
            f"{old_code}xxx → {new_code}xxx.",
            "success"
        )
    else:
        flash("Company updated.", "success")

    cursor.close()
    db.close()
    invalidate_companies_cache()
    return redirect(dest)


@admin_views_bp.route("/companies/<int:cid>/delete", methods=["POST"])
@role_required("admin")
def delete_company(cid):
    redirect_to = request.form.get("redirect_to", "companies")
    dest = "/settings?tab=company" if redirect_to == "settings" else "/companies"
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT COUNT(*) FROM employees WHERE company_id=%s", (cid,))
    count = cursor.fetchone()[0]
    if count > 0:
        cursor.close()
        db.close()
        flash(f"Cannot delete: {count} employee(s) are assigned to this company.", "error")
        return redirect(dest)
    cursor.execute("SELECT COALESCE(logo_path,'') FROM companies WHERE id=%s", (cid,))
    logo_row = cursor.fetchone()
    cursor.execute("SELECT COALESCE(front_image,''), COALESCE(back_image,'') FROM id_card_templates WHERE company_id=%s", (cid,))
    tpl_row = cursor.fetchone()
    cursor.execute("DELETE FROM companies WHERE id=%s", (cid,))
    db.commit()
    cursor.close()
    db.close()
    invalidate_companies_cache()
    _delete_company_image(logo_row[0] if logo_row else None)
    if tpl_row:
        _delete_company_image(tpl_row[0])
        _delete_company_image(tpl_row[1])
    flash("Company deleted.", "success")
    return redirect(dest)
