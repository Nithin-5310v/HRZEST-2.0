# -*- coding: utf-8 -*-
"""Upload storage helpers. Every uploaded or generated file goes through one
of three groups; callers, not this module, choose which.

  - save_public(): under static/, served as is with no sign-in. Only company
    logos use it (utils/helpers/uploads.py's save_uploaded_logo()): branding
    with nothing sensitive in it.
  - save_private() / open_private() / delete_private(): under
    private_uploads/, encrypted at rest, and read back only by a route that
    checks who is asking. Used for employee documents (blueprints/documents.py),
    onboarding task documents (blueprints/onboarding.py), announcement and
    support-ticket attachments, company-signup KYC documents
    (save_application_document()) and attendance QR codes (qr_generator.py:
    the image is itself a check-in credential, so it is gated like the HMAC
    signature in it). save_private() returns an opaque stored_ref: pass it
    back to open_private()/delete_private(), never build a URL from it.
    Those two only touch files inside private_uploads/ (PrivatePathError
    otherwise; see _private_path()).
  - read_file() / save_image() / save_upload() / open_image() / send_image() /
    decrypted_stream(): photos under UPLOAD_FOLDER (dataset/) -- face
    photos, kiosk check-in faces, admin photos -- addressed by plain paths
    and encrypted the same way (see the section further down).
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


# The app's root directory. Every save_private() caller passes this same
# directory (app.root_path, or qr_generator.py's own folder), so every
# stored_ref lies under <_APP_ROOT>/private_uploads/
# (tests/test_private_storage_paths.py checks the two agree).
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PrivatePathError(FileNotFoundError):
    """A stored_ref that does not resolve to a file inside private_uploads/.
    A FileNotFoundError, so each caller's existing missing-file handling
    applies (they all catch it and show "missing or unreadable")."""


def _private_path(stored_ref):
    """stored_ref resolved (symlinks and ".." included) if it lies inside
    private_uploads/, else PrivatePathError. A stored_ref only ever comes from
    save_private(), but it is read back from a database column: a row that
    names ../../app.py, /etc/passwd or a link pointing out of the folder must
    never be read or deleted."""
    root = os.path.realpath(os.path.join(_APP_ROOT, "private_uploads"))
    try:
        path = os.path.realpath(stored_ref) if isinstance(stored_ref, str) and stored_ref else ""
        inside = bool(path) and path != root and os.path.commonpath([root, path]) == root
    except ValueError:      # an embedded NUL, or another drive (Windows)
        inside = False
    if not inside:
        # Only save_private() writes these, so one pointing elsewhere means a
        # database row was changed by something else: worth the SOC's eyes.
        from extensions import log_security_event
        log_security_event("access.private_file_refused", "Stored file path outside private_uploads refused",
                           level="WARNING", stored_ref=repr(stored_ref)[:200])
        raise PrivatePathError("stored file reference is outside private_uploads")
    return path


def open_private(stored_ref):
    """Read back a private upload's bytes given the stored_ref save_private()
    returned. Decrypts transparently (legacy plaintext files pass through).
    Only files inside private_uploads/ are read (PrivatePathError otherwise)."""
    with open(_private_path(stored_ref), "rb") as f:
        return _get_cipher().decrypt_bytes(f.read())


def delete_private(stored_ref):
    """Best-effort delete, mirroring the try/except-and-log pattern every
    local os.remove() call site in this codebase already uses -- returns
    True/False rather than raising, callers log the failure themselves
    with their own context (which employee/application/etc. it belonged to).
    Only files inside private_uploads/ are deleted (PrivatePathError otherwise)."""
    os.remove(_private_path(stored_ref))
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
