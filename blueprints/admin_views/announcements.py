# -*- coding: utf-8 -*-
"""admin_views: announcements, attachments, test email, expiring documents"""
import uuid
from database import get_db_connection
from extensions import app, app_log, log_security_event
from flask import abort, flash, jsonify, redirect, request, send_file, session
from io import BytesIO
from utils.auth import admin_required, any_user_required, role_required
from utils.email_utils import get_email_config, send_email_smtp
from utils.helpers import _validate_upload, int_param, post_announcement, tpath
from utils.storage import delete_private, open_private, save_private
from werkzeug.utils import secure_filename
from . import _ANN_ALLOWED_EXT, admin_views_bp


@admin_views_bp.route("/announcements", methods=["GET", "POST"])
@role_required("admin", "hr")
def announcements_admin():
    """Admins and HRs post and delete announcements; the sidebar shows the
    page to both (templates/admin_base.html)."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            visibility = request.form.get("visibility", "public")
            target_emps = [e.strip() for e in request.form.getlist("target_employee_ids") if e.strip()]
            if visibility == "private" and not target_emps:
                flash("Please select at least one employee for a private announcement.", "error")
                cursor.close()
                db.close()
                return redirect(tpath("/announcements"))
            title = request.form["title"]
            content = request.form.get("content", "")
            priority = request.form.get("priority", "Normal")
            attachment_name = None
            f = request.files.get("attachment")
            if f and f.filename:
                ok, err = _validate_upload(f, _ANN_ALLOWED_EXT)
                if not ok:
                    flash(err, "error")
                    cursor.close()
                    db.close()
                    return redirect(tpath("/announcements"))
                attachment_name = f.filename

            def _save_attachment_copy():
                """Each announcement row owns an independent copy of the file
                (re-saved from the still-open upload stream) so deleting one
                row's attachment can never remove a file another row still
                references -- cheaper than reference-counting a shared file
                for the handful-of-employees case this targets."""
                if not f or not f.filename:
                    return None, None
                f.stream.seek(0)
                rel_path = f"announcements/{uuid.uuid4()}_{secure_filename(attachment_name)}"
                ref, err = save_private(app.root_path, f, rel_path)
                if err:
                    app_log.warning("Announcement attachment save failed: %s", err)
                return ref, err

            if visibility == "public":
                attachment_ref, err = _save_attachment_copy()
                if attachment_name and err:
                    flash(f"Attachment upload failed: {err}", "error")
                    cursor.close()
                    db.close()
                    return redirect(tpath("/announcements"))
                post_announcement(cursor, db, title, content, priority, "public", None,
                                  attachment_original_name=attachment_name, attachment_stored_ref=attachment_ref)
                flash("Announcement posted.", "success")
            else:
                for target_emp in target_emps:
                    attachment_ref, err = _save_attachment_copy()
                    if attachment_name and err:
                        flash(f"Attachment upload failed: {err}", "error")
                        cursor.close()
                        db.close()
                        return redirect(tpath("/announcements"))
                    post_announcement(cursor, db, title, content, priority, "private", target_emp,
                                      attachment_original_name=attachment_name, attachment_stored_ref=attachment_ref)
                flash(f"Announcement posted to {len(target_emps)} employee(s).", "success")
        elif action == "delete":
            cursor.execute("SELECT attachment_stored_ref FROM announcements WHERE id=%s", (request.form["ann_id"],))
            row = cursor.fetchone()
            cursor.execute("DELETE FROM announcements WHERE id=%s", (request.form["ann_id"],))
            db.commit()
            if row and row[0]:
                try:
                    delete_private(row[0])
                except Exception as exc:
                    app_log.warning("Could not remove announcement attachment %s: %s", row[0], exc)
            flash("Announcement deleted.", "success")
        cursor.close()
        db.close()
        return redirect(tpath("/announcements"))
    cursor.close()
    db.close()
    from blueprints.leave import render_hr_hub
    return render_hr_hub("announcements", "announcements")


@admin_views_bp.route("/download_announcement/<int:aid>")
@any_user_required
def download_announcement(aid):
    is_admin = session.get("admin_logged_in")
    emp_id = session.get("employee_id")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT COALESCE(visibility,'public'), target_employee_id, attachment_original_name, attachment_stored_ref "
        "FROM announcements WHERE id=%s", (aid,)
    )
    row = cursor.fetchone()
    cursor.close()
    db.close()
    fallback = tpath("/employee_portal") if emp_id else tpath("/announcements")
    if not row or not row[3]:
        flash("Attachment not found.", "danger")
        return redirect(fallback)
    visibility, target_emp, orig_name, stored_ref = row
    if not is_admin and visibility == "private" and target_emp != emp_id:
        log_security_event(
            "access.denied", "Attempt to download an announcement attachment not addressed to this employee",
            level="WARNING", identifier=emp_id,
        )
        abort(403)
    try:
        data = open_private(stored_ref)
    except Exception:
        flash("Attachment file is missing or unreadable.", "danger")
        return redirect(fallback)
    return send_file(BytesIO(data), as_attachment=True, download_name=orig_name)


@admin_views_bp.route("/test_email", methods=["POST"])
@role_required("admin")
def test_email():
    to_email = request.form.get("test_to", "").strip()
    config = get_email_config()
    if not config:
        return jsonify({"ok": False, "msg": "Email not configured yet."})
    if not to_email:
        return jsonify({"ok": False, "msg": "Enter a test recipient email."})
    try:
        send_email_smtp(
            to_email,
            "Test Email - HRzest.com",
            "<h2>Test email from HRzest.com</h2><p>Email configuration is working correctly.</p>",
            config,
        )
        return jsonify({"ok": True, "msg": f"Test email sent to {to_email}"})
    except Exception:
        app_log.error("Test email send failed", exc_info=True)
        return jsonify({"ok": False, "msg": "Failed to send test email. Check email settings."})


@admin_views_bp.route("/api/admin/expiring_documents", methods=["GET"])
@admin_required
def api_expiring_documents():
    days = int_param(request.args.get("days", 30))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT d.id, d.employee_id, e.name, d.doc_type, d.original_name, d.expiry_date,
               (d.expiry_date - CURRENT_DATE) AS days_left
        FROM employee_documents d
        JOIN employees e ON e.employee_id = d.employee_id
        WHERE d.expiry_date IS NOT NULL
          AND d.expiry_date >= CURRENT_DATE
          AND d.expiry_date <= CURRENT_DATE + (%s * INTERVAL '1 day')
        ORDER BY d.expiry_date ASC
    """, (days,))
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify({
        "ok": True,
        "documents": [
            {"id": r[0], "employee_id": r[1], "employee_name": r[2],
             "doc_type": r[3], "filename": r[4],
             "expiry_date": str(r[5]), "days_left": r[6]}
            for r in rows
        ]
    })
