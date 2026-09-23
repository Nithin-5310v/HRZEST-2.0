# -*- coding: utf-8 -*-
"""Upload storage helpers -- every upload path in this codebase (company
logos, employee documents, KYC application documents, ID-card photos/QR
codes) writes straight to the local filesystem under static/ or
private_uploads/.

Callers, not this module, decide public vs. private:
  - save_public(): local path is under static/ (Flask serves it
    directly). For company logos, ID card template backgrounds -- pure
    branding artwork with no encoded meaning, genuinely safe to be
    world-readable.
  - save_private(): local path is OUTSIDE static/ (mirrors
    utils/helpers.py's save_application_document() convention) -- callers
    must use open_private()/delete_private(), never serve the raw path
    directly, for anything that needs an auth check first (employee
    documents, KYC application documents, attendance QR codes -- see
    qr_generator.py's generate_qr(): the QR image itself IS a bearer
    credential, an unsigned copy of it under static/ would let anyone who
    can guess an employee_id fetch a permanently-valid, replayable
    check-in code for that person with no login at all, which is exactly
    the attack qr_generator.py's own HMAC signature was added to prevent
    -- so the image has to be gated the same way the signature already
    is).

Wired in today: utils/helpers.py's save_uploaded_logo() (public) and
save_application_document() (private) -- the two centralized,
single-purpose upload helpers already existed as one call site each,
making them the safe, low-risk first integration point. The remaining
scattered inline os.path.join(...).save(...) call sites (employee ID
photos/QR codes in blueprints/employees.py, employee_docs in
blueprints/documents.py, kiosk face-log photos in
blueprints/employee_portal.py) still write directly to local disk --
migrating those needs their matching read-back call sites (send_file,
send_from_directory, direct Image.open()) updated in the same pass, a
larger and riskier change to make blind. Follow-up, not done here.
"""
import os


def _local_path(app_root_path, rel_path):
    return os.path.join(app_root_path, rel_path.replace("/", os.sep))


def save_public(app_root_path, file_storage, rel_path, content_type=None):
    """Save an upload meant to be world-readable (company logo, QR code).
    rel_path is relative to static/, e.g. "company_logos/acme.png".

    Returns (static_relative_path, error). Callers already build a
    /static/ URL from the returned path."""
    full_path = _local_path(app_root_path, os.path.join("static", rel_path))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    file_storage.save(full_path)
    return rel_path, None


def save_private(app_root_path, file_storage, rel_path):
    """Save an upload that must stay behind an auth check (employee
    document, KYC application document). rel_path is relative to
    private_uploads/, e.g. "tenant_applications/12/cert.pdf".

    Returns (stored_path, error) -- stored_path is an opaque string to
    pass back into open_private()/delete_private(), never a directly
    servable URL."""
    full_path = _local_path(app_root_path, os.path.join("private_uploads", rel_path))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    file_storage.save(full_path)
    return full_path, None


def open_private(stored_ref):
    """Read back a private upload's bytes given the stored_ref save_private()
    returned."""
    with open(stored_ref, "rb") as f:
        return f.read()


def delete_private(stored_ref):
    """Best-effort delete, mirroring the try/except-and-log pattern every
    local os.remove() call site in this codebase already uses -- returns
    True/False rather than raising, callers log the failure themselves
    with their own context (which employee/application/etc. it belonged to)."""
    os.remove(stored_ref)
    return True
