# -*- coding: utf-8 -*-
"""Data-subject rights and consent (utils/privacy.py): subject access
export, erasure, and the biometric/location consent ledger.

Employee side is Bearer-token (mobile/API) -- an employee can read their own
data, record/withdraw consent, and *request* erasure. They cannot erase
themselves: erasure is destructive and has legal-retention consequences, so
an owner-role administrator performs it, with the exact employee id typed as
confirmation, and every step lands in the audit trail.
"""
import os

from flask import Blueprint, request, jsonify, session, g

from extensions import limiter, log_security_event, app_log
from database import get_db_connection
from utils.auth import employee_api_required, role_required
from utils.helpers import _audit
from utils import privacy

privacy_bp = Blueprint("privacy", __name__)

_REQUEST_KINDS = ("erasure", "access", "correction")


def _upload_folder():
    from extensions import app
    return app.config["UPLOAD_FOLDER"]


def _remove_file(ref):
    """Storage-aware removal for stored refs (private uploads or plain paths)."""
    try:
        if os.path.isfile(ref):
            os.remove(ref)
            return True
    except OSError:
        pass
    return False


# ── employee ────────────────────────────────────────────────────────────────
@privacy_bp.route("/api/employee/privacy/export", methods=["GET"])
@employee_api_required
@limiter.limit("5 per hour")
def api_employee_privacy_export():
    emp_id = g.api_emp_id
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    try:
        data = privacy.export_employee_data(cur, emp_id)
    finally:
        cur.close()
        db.close()
    log_security_event("privacy.export", "Employee exported their own data",
                       level="INFO", identifier=emp_id)
    return jsonify({"ok": True, "data": data})


@privacy_bp.route("/api/employee/privacy/erasure_request", methods=["POST"])
@employee_api_required
@limiter.limit("3 per day")
def api_employee_erasure_request():
    emp_id = g.api_emp_id
    body = request.get_json(silent=True) or {}
    kind = body.get("kind", "erasure")
    if kind not in _REQUEST_KINDS:
        return jsonify({"ok": False, "msg": "Unknown request kind."}), 400
    note = str(body.get("note", ""))[:1000]
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    try:
        cur.execute("SELECT 1 FROM privacy_requests WHERE employee_id=%s AND kind=%s AND status='open'",
                    (emp_id, kind))
        if cur.fetchone():
            return jsonify({"ok": True, "msg": "You already have an open request of this kind."})
        cur.execute("INSERT INTO privacy_requests (employee_id, kind, note) VALUES (%s,%s,%s) RETURNING id",
                    (emp_id, kind, note))
        rid = cur.fetchone()[0]
    finally:
        cur.close()
        db.close()
    log_security_event("privacy.request_opened", f"Employee opened a {kind} request",
                       level="INFO", identifier=emp_id)
    return jsonify({"ok": True, "request_id": rid})


@privacy_bp.route("/api/employee/consent", methods=["GET", "POST"])
@employee_api_required
def api_employee_consent():
    emp_id = g.api_emp_id
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    try:
        if request.method == "GET":
            return jsonify({"ok": True, "consent": {
                p: privacy.consent_state(cur, emp_id, p) for p in privacy.CONSENT_PURPOSES}})
        body = request.get_json(silent=True) or {}
        try:
            privacy.record_consent(cur, emp_id, body.get("purpose"), body.get("policy_version"),
                                   bool(body.get("granted")), request.remote_addr)
        except ValueError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 400
        state = privacy.consent_state(cur, emp_id, body.get("purpose"))
    finally:
        cur.close()
        db.close()
    log_security_event("privacy.consent", "Consent recorded", level="INFO", identifier=emp_id,
                       reason=f"{body.get('purpose')}={'granted' if body.get('granted') else 'withdrawn'}")
    return jsonify({"ok": True, "state": state})


# ── administrator ───────────────────────────────────────────────────────────
@privacy_bp.route("/api/admin/privacy/requests", methods=["GET"])
@role_required("admin")
def api_admin_privacy_requests():
    status = request.args.get("status", "open")
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    try:
        cur.execute(
            "SELECT id, employee_id, kind, status, note, requested_at, handled_by, handled_at "
            "FROM privacy_requests WHERE status=%s ORDER BY id DESC LIMIT 200", (status,))
        keys = ("id", "employee_id", "kind", "status", "note", "requested_at", "handled_by", "handled_at")
        rows = [{k: (str(v) if v is not None and k.endswith("_at") else v) for k, v in zip(keys, r)}
                for r in cur.fetchall()]
    finally:
        cur.close()
        db.close()
    return jsonify({"ok": True, "requests": rows})


@privacy_bp.route("/api/admin/privacy/employee/<emp_id>/export", methods=["GET"])
@role_required("admin")
@limiter.limit("20 per hour")
def api_admin_privacy_export(emp_id):
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    try:
        cur.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
        if not cur.fetchone():
            return jsonify({"ok": False, "msg": "Employee not found."}), 404
        data = privacy.export_employee_data(cur, emp_id)
    finally:
        cur.close()
        db.close()
    _audit("privacy.export", "employees", emp_id, "Administrator exported employee personal data")
    log_security_event("privacy.export", "Administrator exported an employee's personal data",
                       level="WARNING", identifier=session.get("admin_username", ""),
                       reason=f"employee={emp_id}")
    return jsonify({"ok": True, "data": data})


@privacy_bp.route("/api/admin/privacy/employee/<emp_id>/erase", methods=["POST"])
@role_required("admin")
@limiter.limit("10 per hour")
def api_admin_privacy_erase(emp_id):
    body = request.get_json(silent=True) or {}
    if str(body.get("confirm", "")) != emp_id:
        return jsonify({"ok": False, "msg": "Type the employee id in `confirm` to proceed."}), 400
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    try:
        cur.execute("SELECT 1 FROM admin_users WHERE username=%s", (emp_id,))
        if cur.fetchone():
            return jsonify({"ok": False, "msg": "This id belongs to an administrator account; "
                                                "remove the account through user management first."}), 409
        try:
            manifest = privacy.erase_employee(cur, emp_id, upload_folder=_upload_folder(),
                                              remove_file=_remove_file)
        except LookupError:
            return jsonify({"ok": False, "msg": "Employee not found."}), 404
        cur.execute(
            "UPDATE privacy_requests SET status='completed', handled_by=%s, handled_at=NOW(), result_json=%s "
            "WHERE employee_id=%s AND kind='erasure' AND status='open'",
            (session.get("admin_username", ""), privacy.dumps({k: manifest[k] for k in
                                                               ("files_removed", "rows_deleted", "blanked")}), emp_id))
    except Exception:
        app_log.error("privacy erase failed for %s", emp_id, exc_info=True)
        return jsonify({"ok": False, "msg": "Erasure failed; no confirmation was recorded."}), 500
    finally:
        cur.close()
        db.close()
    _audit("privacy.erase", "employees", emp_id,
           f"files={manifest['files_removed']} rows={sum(manifest['rows_deleted'].values())}")
    log_security_event("privacy.erased", "Employee personal data erased", level="WARNING",
                       identifier=session.get("admin_username", ""), reason=f"employee={emp_id}")
    return jsonify({"ok": True, "manifest": manifest})


@privacy_bp.route("/api/admin/privacy/requests/<int:rid>/resolve", methods=["POST"])
@role_required("admin")
def api_admin_privacy_resolve(rid):
    body = request.get_json(silent=True) or {}
    status = body.get("status")
    if status not in ("completed", "rejected"):
        return jsonify({"ok": False, "msg": "status must be completed or rejected."}), 400
    db = get_db_connection()
    cur = db.cursor(buffered=True)
    try:
        cur.execute(
            "UPDATE privacy_requests SET status=%s, handled_by=%s, handled_at=NOW(), note=COALESCE(%s, note) "
            "WHERE id=%s AND status='open' RETURNING employee_id",
            (status, session.get("admin_username", ""), (body.get("note") or None), rid))
        row = cur.fetchone()
    finally:
        cur.close()
        db.close()
    if not row:
        return jsonify({"ok": False, "msg": "Request not found or already handled."}), 404
    _audit("privacy.request_resolved", "privacy_requests", rid, f"{status} for {row[0]}")
    return jsonify({"ok": True})
