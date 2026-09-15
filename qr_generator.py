import qrcode
import os
import hmac
import hashlib
from io import BytesIO


def _qr_signature(emp_id: str) -> str:
    """HMAC-SHA256(SECRET_KEY, "qr:"+emp_id), truncated to 16 hex chars (64
    bits) -- signs the employee ID embedded in a QR code so a scanner can't
    just guess or type another employee's ID and have it accepted. Employee
    IDs are often sequential (EMP001, EMP002, ...), so encoding the raw ID
    alone let anyone who knew or guessed a coworker's ID spoof their
    attendance via the QR-only check-in path with zero further
    verification. Reads SECRET_KEY fresh on every call (not cached at
    import time) so key rotation naturally invalidates old QR codes too,
    same as it already invalidates sessions.

    "qr:" prefix domain-separates this HMAC from any other use of
    SECRET_KEY (e.g. Flask's own session signing) using the same key."""
    secret = os.environ.get("SECRET_KEY", "")
    return hmac.new(secret.encode(), f"qr:{emp_id}".encode(), hashlib.sha256).hexdigest()[:16]


def generate_qr(emp_id):
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
    utils.storage.open_private(), never assume it's a bare local path."""
    from utils.storage import save_private
    from werkzeug.datastructures import FileStorage

    app_root_path = os.path.dirname(os.path.abspath(__file__))
    qr_value = f"{emp_id}.{_qr_signature(emp_id)}"
    img = qrcode.make(qr_value)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file_storage = FileStorage(stream=buf, filename=f"{emp_id}.png", content_type="image/png")
    stored_ref, err = save_private(app_root_path, file_storage, f"qrcodes/{emp_id}.png")
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
    expected = _qr_signature(emp_id)
    if not hmac.compare_digest(expected, sig.lower()):
        return None, False
    return emp_id, True
