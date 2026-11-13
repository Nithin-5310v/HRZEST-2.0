import qrcode
import os
import hmac
import re
import hashlib
from io import BytesIO


import datetime
import logging

_log = logging.getLogger("attendance")

# QR codes signed before signatures named the company (see _qr_signature)
# keep working until the end of this date, so printed ID cards can be
# reissued; override with QR_LEGACY_ACCEPT_UNTIL=YYYY-MM-DD, or set it to
# a past date to stop accepting them immediately.
_LEGACY_QR_DEFAULT_UNTIL = "2026-12-04"


def _default_tenant_key():
    """The current request's company -- see utils/tenant_files.tenant_key()."""
    from utils.tenant_files import tenant_key
    return tenant_key()


def _qr_signature(emp_id: str, tenant: str = None) -> str:
    """HMAC-SHA256(SECRET_KEY, "qr:<tenant>:<emp_id>"), truncated to 16 hex
    chars (64 bits) -- signs the employee ID embedded in a QR code so a
    scanner can't just guess or type another employee's ID and have it
    accepted. Employee IDs are often sequential (EMP001, EMP002, ...), so
    encoding the raw ID alone let anyone who knew or guessed a coworker's
    ID spoof their attendance via the QR-only check-in path. Reads
    SECRET_KEY fresh on every call so key rotation invalidates old codes.

    The tenant is part of the signed message because every company shares
    one SECRET_KEY and employee IDs repeat across companies: signing only
    the ID made company A's EMP001 badge -- which any company's admin can
    mint for themselves by creating an EMP001 -- valid at company B's kiosk.

    "qr:" prefix domain-separates this HMAC from any other use of
    SECRET_KEY (e.g. Flask's own session signing) using the same key."""
    tenant = tenant or _default_tenant_key()
    secret = os.environ.get("SECRET_KEY", "")
    return hmac.new(secret.encode(), f"qr:{tenant}:{emp_id}".encode(), hashlib.sha256).hexdigest()[:16]


def _legacy_qr_signature(emp_id: str) -> str:
    """The pre-tenant signature, HMAC(SECRET_KEY, "qr:"+emp_id). Accepted
    only during the reissue window -- see _legacy_qr_accepted()."""
    secret = os.environ.get("SECRET_KEY", "")
    return hmac.new(secret.encode(), f"qr:{emp_id}".encode(), hashlib.sha256).hexdigest()[:16]


def _legacy_qr_accepted(today=None) -> bool:
    raw = (os.environ.get("QR_LEGACY_ACCEPT_UNTIL") or _LEGACY_QR_DEFAULT_UNTIL).strip()
    try:
        until = datetime.date.fromisoformat(raw)
    except ValueError:
        _log.error("QR_LEGACY_ACCEPT_UNTIL=%r is not YYYY-MM-DD -- legacy QR codes refused", raw)
        return False
    return (today or datetime.date.today()) <= until


def generate_qr(emp_id, tenant=None):
    """Generates (or regenerates -- the signature is deterministic, so this
    is idempotent and cheap to call on every view) the employee's
    attendance QR and saves it to PRIVATE storage, never static/. The
    scanned payload ("<employee_id>.<hmac signature>") is a bearer
    credential: whoever holds a copy of this image can check that
    employee in/out indefinitely with no further proof of identity. A
    world-readable copy under static/ would hand that out to anyone who
    can guess an employee_id (routinely sequential -- EMP001, EMP002, ...)
    with zero authentication, completely bypassing the HMAC signature's
    own purpose (see _qr_signature()'s docstring above). Retrieval must
    always go through an authenticated, ownership-checked route -- see
    blueprints/employee_portal.py's my_qr() and blueprints/employees.py's
    admin-side twin.

    Returns save_private()'s opaque stored_ref -- read it back with
    utils.storage.open_private(), never assume it's a bare local path.

    tenant: the company to sign for; defaults to the current request's
    (see _default_tenant_key). Callers with no request -- migrations --
    must pass it. The image is stored per company too, so two companies'
    EMP001 never overwrite each other's badge."""
    from utils.storage import save_private
    from werkzeug.datastructures import FileStorage

    app_root_path = os.path.dirname(os.path.abspath(__file__))
    tenant = tenant or _default_tenant_key()
    qr_value = f"{emp_id}.{_qr_signature(emp_id, tenant)}"
    img = qrcode.make(qr_value)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file_storage = FileStorage(stream=buf, filename=f"{emp_id}.png", content_type="image/png")
    stored_ref, err = save_private(app_root_path, file_storage, f"qrcodes/{tenant}/{emp_id}.png")
    if err:
        raise RuntimeError(f"Failed to save QR code for {emp_id}: {err}")
    return stored_ref


def verify_qr_value(qr_value: str):
    """Split a scanned QR payload of the form "<employee_id>.<signature>"
    into (employee_id, is_valid). Returns (None, False) for anything
    malformed or whose signature doesn't match -- callers must not trust
    the returned employee_id unless is_valid is True. Case-insensitive on
    the signature so callers that upper() the whole scanned string (as
    several check-in routes already did before this existed) don't break
    a legitimate scan."""
    if not qr_value or "." not in qr_value:
        _log_rejection("malformed", qr_value)
        return None, False
    emp_id, _, sig = qr_value.rpartition(".")
    if not emp_id or not sig:
        _log_rejection("malformed", qr_value)
        return None, False
    sig = sig.lower()
    spellings = [emp_id]
    for candidate in _id_spellings(emp_id):
        if candidate not in spellings:
            spellings.append(candidate)
        if hmac.compare_digest(_qr_signature(candidate), sig):
            return candidate, True
        if _legacy_qr_accepted() and hmac.compare_digest(_legacy_qr_signature(candidate), sig):
            # Logged so admins can see who still carries an old badge before
            # the window closes.
            _log.warning("attendance.qr_legacy_accepted: pre-tenant QR code used for %s (tenant %s); "
                         "reissue this badge before QR_LEGACY_ACCEPT_UNTIL", candidate, _default_tenant_key())
            return candidate, True
    expired_legacy = any(hmac.compare_digest(_legacy_qr_signature(c), sig) for c in spellings)
    _log_rejection("legacy_expired" if expired_legacy else "bad_signature", qr_value, emp_id)
    return None, False


_EMP_ID_SHAPE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _id_spellings(emp_id):
    """The scanned ID, then -- only if it differs -- the same employee's ID
    exactly as stored. The signature covers the stored spelling, and some
    scanners (an older kiosk page, hardware readers in caps-lock mode)
    upper-case the whole payload, which turned a valid "Sales-007" badge
    into an unverifiable "SALES-007". The stored spelling comes from the
    current company's employees table, matched case-insensitively and only
    when exactly one employee matches (a lookup runs only after the scanned
    spelling failed, so valid scans cost nothing extra)."""
    yield emp_id
    if not _EMP_ID_SHAPE.match(emp_id):
        return
    try:
        from database import get_db_connection
        db = get_db_connection()
        try:
            cur = db.cursor()
            cur.execute("SELECT employee_id FROM employees WHERE UPPER(employee_id)=UPPER(%s) LIMIT 2", (emp_id,))
            rows = cur.fetchall()
            cur.close()
        finally:
            db.close()
    except Exception as exc:
        _log.warning("attendance.qr_id_lookup_failed: %s", type(exc).__name__)
        return
    if len(rows) == 1 and rows[0][0] != emp_id:
        yield rows[0][0]


def _log_rejection(reason, qr_value, emp_id=None):
    """One line per rejected scan, so "Invalid or unrecognized QR code" can be
    traced to its cause: malformed (not a signed badge at all),
    legacy_expired (a pre-company badge after QR_LEGACY_ACCEPT_UNTIL), or
    bad_signature (signed for another company, with another SECRET_KEY, or
    for an employee this company doesn't have)."""
    _log.warning("attendance.qr_rejected: reason=%s tenant=%s employee=%s length=%d",
                 reason, _default_tenant_key(), emp_id or "-", len(qr_value or ""))
