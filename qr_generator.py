import qrcode
import os
import hmac
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
    """Tenant the current request runs as: flask.g.tenant_db, with the
    single-tenant default (DB_NAME, or no tenant at all) normalised to
    "public" -- the schema it actually lives in, and what a migration's
    search_path reports for it."""
    try:
        from flask import g, has_app_context
        tenant = getattr(g, "tenant_db", None) if has_app_context() else None
    except Exception:
        tenant = None
    if not tenant or tenant == os.environ.get("DB_NAME", "employee_attendance"):
        return "public"
    return tenant


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
        return None, False
    emp_id, _, sig = qr_value.rpartition(".")
    if not emp_id or not sig:
        return None, False
    sig = sig.lower()
    if hmac.compare_digest(_qr_signature(emp_id), sig):
        return emp_id, True
    if _legacy_qr_accepted() and hmac.compare_digest(_legacy_qr_signature(emp_id), sig):
        # Logged so admins can see who still carries an old badge before
        # the window closes.
        _log.warning("attendance.qr_legacy_accepted: pre-tenant QR code used for %s (tenant %s); "
                     "reissue this badge before QR_LEGACY_ACCEPT_UNTIL", emp_id, _default_tenant_key())
        return emp_id, True
    return None, False
