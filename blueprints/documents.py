# -*- coding: utf-8 -*-
"""Documents blueprint -- admin and employee document management."""
import uuid
from io import BytesIO
from flask import Blueprint, request, session, redirect, flash, send_file, jsonify, g as _g
from extensions import app, app_log
from database import get_db_connection, transaction
from werkzeug.utils import secure_filename
from utils.auth import admin_required, employee_required, enforce_ownership, api_required, api_role_required, employee_api_required
from utils.helpers import (
    tpath, _audit, _validate_upload, _safe_referrer_redirect, hr_scope_denied, _create_notification, validate_emp_id,
)
from utils.storage import save_private, open_private, delete_private

documents_bp = Blueprint("documents", __name__)

_DOC_ALLOWED_EXT = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls', 'xlsx'}


@documents_bp.route("/upload_document", methods=["POST"])
@admin_required
def upload_document():
    emp_id = request.form.get('employee_id', '').strip()
    doc_type = request.form.get('doc_type', '').strip()
    f = request.files.get('document')
    if not emp_id or not doc_type or not f or not f.filename:
        flash("All fields required.", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}" if emp_id else "/employees"))
    # emp_id is interpolated into the storage path below (local disk or S3
    # key), so it must pass the same allowlist registration uses -- otherwise
    # "../../static/x" walks the write out of private_uploads/.
    if not validate_emp_id(emp_id):
        flash("Invalid employee ID.", "danger")
        return redirect(tpath("/employees"))
    ok, err = _validate_upload(f, _DOC_ALLOWED_EXT)
    if not ok:
        flash(err, "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))
    orig_name = f.filename
    rel_path = f"employee_documents/{emp_id}/{uuid.uuid4()}_{secure_filename(orig_name)}"
    stored_ref, err = save_private(app.root_path, f, rel_path)
    if err:
        flash(f"Upload failed: {err}", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    expiry_raw = request.form.get("expiry_date", "").strip()
    expiry_date = expiry_raw if expiry_raw else None
    cursor.execute(
        "INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by, expiry_date) "
        "VALUES (%s,%s,%s,%s,'admin',%s)",
        (emp_id, doc_type, orig_name, stored_ref, expiry_date)
    )
    db.commit()
    cursor.close()
    db.close()
    _audit("upload_document", "employee_documents", emp_id,
           f"doc_type={doc_type} file={orig_name} expiry={expiry_date or 'none'}")
    flash("Document uploaded successfully.", "success")
    raw_redirect = request.form.get('redirect_to') or f'/employee_detail/{emp_id}'
    # Reject any redirect that leaves this origin (open-redirect prevention).
    # Only allow relative URLs (no scheme, no netloc).
    from urllib.parse import urlparse as _urlparse
    _p = _urlparse(raw_redirect)
    safe_redirect = raw_redirect if (not _p.scheme and not _p.netloc) else f'/employee_detail/{emp_id}'
    return redirect(tpath(safe_redirect))


@documents_bp.route("/delete_document/<int:did>", methods=["POST"])
@admin_required
def delete_document(did):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id, stored_name FROM employee_documents WHERE id=%s", (did,))
    row = cursor.fetchone()
    fallback = "/employees"
    if row:
        _emp_id, stored_ref = row
        fallback = f"/employee_detail/{_emp_id}"
        try:
            delete_private(stored_ref)
        except Exception as exc:
            # DB row still gets deleted below -- an orphaned file left on
            # disk is a minor leak, not worth blocking the delete over, but
            # worth knowing about since these silently accumulate.
            app_log.warning("Could not remove document file %s: %s", stored_ref, exc)
        cursor.execute("DELETE FROM employee_documents WHERE id=%s", (did,))
        db.commit()
    cursor.close()
    db.close()
    flash("Document deleted.", "success")
    return redirect(_safe_referrer_redirect(request.referrer or "", fallback))


@documents_bp.route("/download_document/<int:did>")
def download_document(did):
    is_admin = session.get("admin_logged_in")
    emp_session = session.get("employee_id")
    if not is_admin and not emp_session:
        return redirect(tpath("/login"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id, original_name, stored_name FROM employee_documents WHERE id=%s", (did,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        flash("Document not found.", "danger")
        return redirect(tpath("/employees"))
    doc_emp_id, original_name, stored_ref = row
    # This check existed before but never logged a denial -- a real IDOR
    # probe against someone else's payslip/ID-document upload would have
    # been invisible. enforce_ownership() logs it at ERROR, which feeds the
    # same alerting webhook the payslip endpoint already uses.
    if not enforce_ownership(doc_emp_id, "document", did):
        flash("Access denied.", "danger")
        return redirect(tpath("/employee_portal"))
    try:
        data = open_private(stored_ref)
    except Exception:
        flash("Document file is missing or unreadable.", "danger")
        return redirect(tpath(f"/employee_detail/{doc_emp_id}") if is_admin else tpath("/employee_portal"))
    return send_file(BytesIO(data), as_attachment=True, download_name=original_name)


@documents_bp.route("/upload_my_document", methods=["POST"])
def upload_my_document():
    emp_id = session.get("employee_id")
    if not emp_id:
        return redirect(tpath("/login"))
    doc_type = request.form.get('doc_type', '').strip()
    f = request.files.get('document')
    if not doc_type or not f or not f.filename:
        flash("All fields required.", "danger")
        return redirect(tpath("/employee_portal"))
    ok, err = _validate_upload(f, _DOC_ALLOWED_EXT)
    if not ok:
        flash(err, "danger")
        return redirect(tpath("/employee_portal"))
    orig_name = f.filename
    rel_path = f"employee_documents/{emp_id}/{uuid.uuid4()}_{secure_filename(orig_name)}"
    stored_ref, err = save_private(app.root_path, f, rel_path)
    if err:
        flash(f"Upload failed: {err}", "danger")
        return redirect(tpath("/employee_portal"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by) VALUES (%s,%s,%s,%s,'employee')",
        (emp_id, doc_type, orig_name, stored_ref)
    )
    db.commit()
    cursor.close()
    db.close()
    flash("Document uploaded successfully.", "success")
    return redirect(tpath("/employee_portal#documents"))


@documents_bp.route("/delete_my_document/<int:did>", methods=["POST"])
@employee_required
def delete_my_document(did):
    emp_id = session["employee_id"]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id, stored_name FROM employee_documents WHERE id=%s AND employee_id=%s", (did, emp_id))
    row = cursor.fetchone()
    if row:
        stored_ref = row[1]
        try:
            delete_private(stored_ref)
        except Exception as exc:
            app_log.warning("Could not remove document file %s: %s", stored_ref, exc)
        cursor.execute("DELETE FROM employee_documents WHERE id=%s AND employee_id=%s", (did, emp_id))
        db.commit()
    cursor.close()
    db.close()
    flash("Document deleted.", "success")
    return redirect(tpath("/employee_portal#documents"))


def _fmt_doc(row):
    doc_id, employee_id, name, doc_type, original_name, uploaded_by, uploaded_at, expiry_date = row
    return {
        "id": doc_id, "employee_id": employee_id, "employee_name": name,
        "doc_type": doc_type, "original_name": original_name, "uploaded_by": uploaded_by,
        "uploaded_at": str(uploaded_at) if uploaded_at else None,
        "expiry_date": str(expiry_date) if expiry_date else None,
    }


# ── Documents (Bearer-token API) ─────────────────────────────────────────────
# Bearer twins of the session-only routes above. Real download (streaming
# a file back through axios/react-native) isn't wired on mobile yet --
# these cover list/upload/delete, the actions mobile/src/screens/employee/
# DocumentsScreen.js's own comments already flagged as missing entirely.

@documents_bp.route("/api/documents", methods=["GET"])
@api_required
@api_role_required("admin", "hr")
def api_documents_list():
    emp_id = request.args.get("emp_id", "").strip()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    if emp_id:
        cursor.execute("""
            SELECT d.id, d.employee_id, e.name, d.doc_type, d.original_name,
                   d.uploaded_by, d.uploaded_at, d.expiry_date
            FROM employee_documents d JOIN employees e ON e.employee_id=d.employee_id
            WHERE d.employee_id=%s ORDER BY d.uploaded_at DESC
        """, (emp_id,))
    else:
        cursor.execute("""
            SELECT d.id, d.employee_id, e.name, d.doc_type, d.original_name,
                   d.uploaded_by, d.uploaded_at, d.expiry_date
            FROM employee_documents d JOIN employees e ON e.employee_id=d.employee_id
            ORDER BY d.uploaded_at DESC
        """)
    docs = [_fmt_doc(r) for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "documents": docs})


@documents_bp.route("/api/documents/upload", methods=["POST"])
@api_required
@api_role_required("admin", "hr")
def api_documents_upload():
    emp_id = (request.form.get("employee_id") or "").strip()
    doc_type = (request.form.get("doc_type") or "").strip()
    f = request.files.get("document")
    if not emp_id or not doc_type or not f or not f.filename:
        return jsonify({"ok": False, "msg": "employee_id, doc_type, and a file are all required."}), 400
    ok, err = _validate_upload(f, _DOC_ALLOWED_EXT)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 400

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
    if not cursor.fetchone():
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": f"Unknown employee_id '{emp_id}'."}), 400

    orig_name = f.filename
    rel_path = f"employee_documents/{emp_id}/{uuid.uuid4()}_{secure_filename(orig_name)}"
    stored_ref, err = save_private(app.root_path, f, rel_path)
    if err:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": f"Upload failed: {err}"}), 500
    expiry_raw = (request.form.get("expiry_date") or "").strip()
    cursor.execute(
        "INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by, expiry_date) "
        "VALUES (%s,%s,%s,%s,'admin',%s)",
        (emp_id, doc_type, orig_name, stored_ref, expiry_raw or None)
    )
    db.commit()
    cursor.close()
    db.close()
    _audit("upload_document", "employee_documents", emp_id,
           f"doc_type={doc_type} file={orig_name} expiry={expiry_raw or 'none'} (via mobile)")
    return jsonify({"ok": True, "msg": "Document uploaded."})


@documents_bp.route("/api/documents/<int:did>", methods=["DELETE"])
@api_required
@api_role_required("admin", "hr")
def api_documents_delete(did):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT employee_id, stored_name FROM employee_documents WHERE id=%s", (did,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Document not found."}), 404
    _emp_id, stored_ref = row
    try:
        delete_private(stored_ref)
    except Exception as exc:
        app_log.warning("Could not remove document file %s: %s", stored_ref, exc)
    cursor.execute("DELETE FROM employee_documents WHERE id=%s", (did,))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Document deleted."})


@documents_bp.route("/api/employee/documents", methods=["GET"])
@employee_api_required
def api_my_documents_list():
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT d.id, d.employee_id, e.name, d.doc_type, d.original_name,
               d.uploaded_by, d.uploaded_at, d.expiry_date
        FROM employee_documents d JOIN employees e ON e.employee_id=d.employee_id
        WHERE d.employee_id=%s ORDER BY d.uploaded_at DESC
    """, (emp_id,))
    docs = [_fmt_doc(r) for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "documents": docs})


@documents_bp.route("/api/employee/documents/upload", methods=["POST"])
@employee_api_required
def api_my_documents_upload():
    emp_id = _g.api_emp_id
    doc_type = (request.form.get("doc_type") or "").strip()
    f = request.files.get("document")
    if not doc_type or not f or not f.filename:
        return jsonify({"ok": False, "msg": "doc_type and a file are both required."}), 400
    ok, err = _validate_upload(f, _DOC_ALLOWED_EXT)
    if not ok:
        return jsonify({"ok": False, "msg": err}), 400

    orig_name = f.filename
    rel_path = f"employee_documents/{emp_id}/{uuid.uuid4()}_{secure_filename(orig_name)}"
    stored_ref, err = save_private(app.root_path, f, rel_path)
    if err:
        return jsonify({"ok": False, "msg": f"Upload failed: {err}"}), 500
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by) "
        "VALUES (%s,%s,%s,%s,'employee')",
        (emp_id, doc_type, orig_name, stored_ref)
    )
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Document uploaded."})


@documents_bp.route("/api/employee/documents/<int:did>", methods=["DELETE"])
@employee_api_required
def api_my_documents_delete(did):
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT stored_name FROM employee_documents WHERE id=%s AND employee_id=%s", (did, emp_id))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Document not found."}), 404
    stored_ref = row[0]
    try:
        delete_private(stored_ref)
    except Exception as exc:
        app_log.warning("Could not remove document file %s: %s", stored_ref, exc)
    cursor.execute("DELETE FROM employee_documents WHERE id=%s AND employee_id=%s", (did, emp_id))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Document deleted."})


# ── Employee document verification (Company Onboarding -> Trial ->
# Document Verification -> Subscription Activation) ─────────────────────────
# A separate concept from the free-text doc_type uploads above: these rows
# are tagged with a normalized doc_kind and carry a real approve/reject
# workflow, ending in an admin notification once every required kind is
# approved for a given employee (the trigger that nudges the admin toward
# Settings -> Finances -> Billing). Web/admin-only for v1 -- no employee
# self-serve or mobile route for this specific flow yet.
_EMPLOYEE_VERIFICATION_DOC_KINDS = ("id_proof", "address_proof")
_EMPLOYEE_VERIFICATION_DOC_LABELS = {"id_proof": "ID Proof", "address_proof": "Address Proof"}


@documents_bp.route("/upload_verification_document", methods=["POST"])
@admin_required
def upload_verification_document():
    emp_id = request.form.get('employee_id', '').strip()
    doc_kind = request.form.get('doc_kind', '').strip()
    f = request.files.get('document')
    if not emp_id or not f or not f.filename:
        flash("An employee and a file are both required.", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}" if emp_id else "/employees"))
    if doc_kind not in _EMPLOYEE_VERIFICATION_DOC_KINDS:
        flash("Invalid verification document type.", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))
    if hr_scope_denied(emp_id):
        flash("Employee not found.", "danger")
        return redirect(tpath("/employees"))
    ok, err = _validate_upload(f, _DOC_ALLOWED_EXT)
    if not ok:
        flash(err, "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))
    orig_name = f.filename
    rel_path = f"employee_documents/{emp_id}/{uuid.uuid4()}_{secure_filename(orig_name)}"
    stored_ref, err = save_private(app.root_path, f, rel_path)
    if err:
        flash(f"Upload failed: {err}", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by, "
        "doc_kind, verification_status) VALUES (%s,%s,%s,%s,'admin',%s,'pending')",
        (emp_id, _EMPLOYEE_VERIFICATION_DOC_LABELS[doc_kind], orig_name, stored_ref, doc_kind)
    )
    db.commit()
    cursor.close()
    db.close()
    _audit("upload_verification_document", "employee_documents", emp_id,
           f"doc_kind={doc_kind} file={orig_name}")
    flash("Verification document uploaded.", "success")
    return redirect(tpath(f"/employee_detail/{emp_id}"))


@documents_bp.route("/employees/<emp_id>/documents/<int:did>/verify", methods=["POST"])
@admin_required
def verify_employee_document(emp_id, did):
    action = request.form.get("action", "").strip()
    reason = request.form.get("reason", "").strip()[:500]
    if action not in ("approve", "reject"):
        flash("Invalid action.", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))
    if action == "reject" and not reason:
        flash("A reason is required to reject a document.", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))
    if hr_scope_denied(emp_id):
        flash("Employee not found.", "danger")
        return redirect(tpath("/employees"))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT doc_kind, employee_id FROM employee_documents WHERE id=%s", (did,)
    )
    row = cursor.fetchone()
    if not row or row[1] != emp_id or not row[0]:
        cursor.close()
        db.close()
        flash("Verification document not found.", "danger")
        return redirect(tpath(f"/employee_detail/{emp_id}"))

    reviewer = session.get("admin_username")
    new_status = "approved" if action == "approve" else "rejected"
    notified_now = False
    employee_name = emp_id
    with transaction(db):
        cursor.execute(
            "UPDATE employee_documents SET verification_status=%s, verified_by=%s, verified_at=NOW(), "
            "rejection_reason=%s WHERE id=%s",
            (new_status, reviewer, reason or None, did)
        )
        if action == "approve":
            # Race-safe: two concurrent approvals of the two different
            # required doc_kinds for the same employee must not both
            # observe "not yet complete" and both skip notifying, nor
            # both fire it -- the transaction (row lock implicit in the
            # guarded UPDATE below) plus the "only notify if rowcount==1"
            # check makes this exactly-once regardless of ordering.
            cursor.execute(
                "SELECT DISTINCT ON (doc_kind) doc_kind, verification_status FROM employee_documents "
                "WHERE employee_id=%s AND doc_kind IS NOT NULL ORDER BY doc_kind, uploaded_at DESC",
                (emp_id,)
            )
            latest_by_kind = dict(cursor.fetchall())
            all_approved = all(
                latest_by_kind.get(kind) == "approved" for kind in _EMPLOYEE_VERIFICATION_DOC_KINDS
            )
            if all_approved:
                cursor.execute(
                    "UPDATE employees SET doc_verification_notified_at=NOW() "
                    "WHERE employee_id=%s AND doc_verification_notified_at IS NULL",
                    (emp_id,)
                )
                notified_now = cursor.rowcount == 1
                if notified_now:
                    cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (emp_id,))
                    name_row = cursor.fetchone()
                    employee_name = name_row[0] if name_row else emp_id
    cursor.close()
    db.close()

    _audit("verify_employee_document", "employee_documents", emp_id,
           f"doc_kind={row[0]} action={action} reason={reason or 'n/a'}")

    if notified_now:
        _notify_admin_employee_verified(emp_id, employee_name)

    flash(f"Document {new_status}.", "success")
    return redirect(tpath(f"/employee_detail/{emp_id}"))


def _notify_admin_employee_verified(emp_id, employee_name):
    """Fires once per employee, the moment every required verification
    document kind is approved -- paired with an email since there's no web
    notification-bell consumer today and mobile push is inert (no EAS
    project configured), same reasoning blueprints/trial_billing.py's own
    admin notices already rely on. Best-effort: a failure here must not
    undo the verification approval that already committed above."""
    title = "Employee verified — set up billing"
    message = (
        f"{employee_name} ({emp_id})'s documents are verified. Set up your subscription in "
        f"Settings → Finances → Billing to keep them active after your trial."
    )
    try:
        _create_notification("admin", title, message)
    except Exception as exc:
        app_log.warning("_notify_admin_employee_verified: notification row failed for %s: %s", emp_id, exc)
    try:
        from utils.email_utils import get_email_config, get_admin_emails, send_email_async
        config = get_email_config()
        if not config:
            return
        recipients = get_admin_emails()
        if not recipients:
            return
        html = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:520px;margin:auto;background:#f8fafc;border-radius:16px;overflow:hidden;border:1px solid #dbeafe;">
  <div style="background:#1e3a8a;padding:24px 28px;color:white;">
    <div style="font-size:20px;font-weight:700;">HRzest.com</div>
    <div style="font-size:13px;opacity:0.75;margin-top:4px;">Employee verified</div>
  </div>
  <div style="padding:28px;font-size:14px;color:#1e293b;line-height:1.6;">
    <p><strong>{employee_name}</strong> ({emp_id})'s documents have been verified.</p>
    <p>Set up your subscription in <strong>Settings &rarr; Finances &rarr; Billing</strong> to keep them
       active after your trial ends.</p>
  </div>
</div>"""
        for email in recipients:
            send_email_async(email, title, html, config)
    except Exception as exc:
        app_log.warning("_notify_admin_employee_verified: email failed for %s: %s", emp_id, exc)
