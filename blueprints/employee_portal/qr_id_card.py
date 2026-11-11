# -*- coding: utf-8 -*-
"""employee_portal: my QR code and ID card"""
from database import get_db_connection
from flask import jsonify
from io import BytesIO
from qr_generator import generate_qr
from utils.auth import employee_api_required, any_user_required
from utils.helpers import self_service_emp_id
from utils.storage import open_private
from . import employee_portal_bp


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
@any_user_required
def my_qr():
    """The only sanctioned way for an employee to view/download their own
    attendance QR now that generate_qr() saves it to private storage --
    see that function's own docstring for why (the QR image is a bearer
    credential; a world-readable copy under static/ would defeat the
    HMAC signature's whole purpose)."""
    from flask import send_file
    emp_id = self_service_emp_id()
    if not emp_id:
        return "Not logged in", 401
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
@any_user_required
def my_id_card():
    from flask import send_file
    from blueprints.employees import _build_id_card_buf

    emp_id = self_service_emp_id()
    if not emp_id:
        return "Not logged in", 401
    buf = _build_id_card_buf(emp_id)
    if buf is None:
        return "Employee not found", 404
    return send_file(buf, as_attachment=True,
                     download_name=f"IDCard_{emp_id}.png", mimetype="image/png")
