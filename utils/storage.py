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
import io
import os
import tempfile

# Encryption at rest for private uploads (AES-256-GCM, utils/pii_crypto.py,
# same ENCRYPTION_KEY as PII columns). Applied here so every save_private()/
# open_private() caller gets it with no change: files on disk -- and in
# backups or a copied volume -- are ciphertext; open_private() returns the
# original bytes. Files written before this existed have no FILE_MAGIC
# header and are returned as-is, so nothing breaks before
# scripts/encrypt_private_uploads.py has upgraded them.
_cipher = None


def _get_cipher():
    global _cipher
    if _cipher is None:
        from utils.pii_crypto import cipher_from_env
        _cipher = cipher_from_env()
    return _cipher


def write_encrypted(full_path, data):
    """Atomically write `data` encrypted to full_path (temp file + rename,
    so a crash can never leave a half-written or plaintext file behind)."""
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    blob = _get_cipher().encrypt_bytes(data)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(full_path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.replace(tmp, full_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


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
    buf = io.BytesIO()
    file_storage.save(buf)
    write_encrypted(full_path, buf.getvalue())
    return full_path, None


def open_private(stored_ref):
    """Read back a private upload's bytes given the stored_ref save_private()
    returned. Decrypts transparently (legacy plaintext files pass through)."""
    with open(stored_ref, "rb") as f:
        return _get_cipher().decrypt_bytes(f.read())


def delete_private(stored_ref):
    """Best-effort delete, mirroring the try/except-and-log pattern every
    local os.remove() call site in this codebase already uses -- returns
    True/False rather than raising, callers log the failure themselves
    with their own context (which employee/application/etc. it belonged to)."""
    os.remove(stored_ref)
    return True


# ── Biometric images (face photos, kiosk face logs, admin photos) ────────────
# Same AES-256-GCM file encryption as private uploads, for images that live
# under UPLOAD_FOLDER (dataset/) and are addressed by plain filesystem paths.
# Every reader must go through these helpers: they decrypt in memory and
# also accept legacy plaintext files, so files can be encrypted in place
# (scripts/encrypt_private_uploads.py --root dataset) only once no raw
# open()/Image.open()/load_image_file()/send_file() on these paths remains.

def read_file(path):
    """Decrypted bytes of an encrypted (or legacy plaintext) file."""
    with open(path, "rb") as f:
        return _get_cipher().decrypt_bytes(f.read())


def save_image(img, path, fmt="JPEG", **save_kwargs):
    """Encode a PIL image and write it encrypted to `path`."""
    buf = io.BytesIO()
    img.save(buf, fmt, **save_kwargs)
    write_encrypted(path, buf.getvalue())
    return path


def save_upload(file_storage, path):
    """Write an uploaded file (werkzeug FileStorage) encrypted to `path`."""
    buf = io.BytesIO()
    file_storage.save(buf)
    write_encrypted(path, buf.getvalue())
    return path


def open_image(path):
    """PIL image from an encrypted (or legacy plaintext) file, fully loaded
    so no file handle or buffer is left dangling."""
    from PIL import Image
    img = Image.open(io.BytesIO(read_file(path)))
    img.load()
    return img


def load_face_image(path, mode="RGB"):
    """Drop-in for face_recognition.load_image_file(path) on an encrypted
    file: a numpy array in `mode`."""
    import numpy as np
    return np.array(open_image(path).convert(mode))


def send_image(path, mimetype="image/jpeg", **kwargs):
    """Flask response for an encrypted image -- replaces send_file(path) /
    send_from_directory(dir, name). Callers still do their own auth and
    path checks first."""
    from flask import send_file
    return send_file(io.BytesIO(read_file(path)), mimetype=mimetype, **kwargs)


def decrypted_stream(path):
    """In-memory file object of a decrypted image, for libraries that take a
    path or file (face_recognition.load_image_file, PIL)."""
    return io.BytesIO(read_file(path))
