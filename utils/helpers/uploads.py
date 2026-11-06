# -*- coding: utf-8 -*-
"""helpers: upload validation, malware scanning (ClamAV), logo / application-document storage"""
import os
import re
from extensions import app_log, log_security_event


# ── Malware scanning (ClamAV) ─────────────────────────────────────────────────
try:
    import clamd as _clamd_lib
    _clamav_available = True
except ImportError:
    _clamd_lib = None
    _clamav_available = False

_CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "clamav")
_CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))
_MALWARE_SCAN_ENABLED = os.environ.get("MALWARE_SCAN_ENABLED", "true").strip().lower() not in ("false", "0", "no")


def _scan_for_malware(file_storage):
    """Scan an uploaded file with ClamAV before it's saved. Returns (is_clean, error_msg).
    Fails closed (rejects the upload) in production if the scanner is unavailable
    or unreachable; fails open with a logged warning in development, so a missing
    local ClamAV instance doesn't block day-to-day dev work.

    Set MALWARE_SCAN_ENABLED=false to turn this off deliberately (e.g. a
    memory-constrained deployment that can't run ClamAV) -- that's a clean
    skip, not a failure, so it doesn't trigger the fail-closed behavior
    below and permanently block uploads."""
    if not _MALWARE_SCAN_ENABLED:
        return True, None
    _dev = os.environ.get("APP_ENV", "production") == "development"
    if not _clamav_available:
        app_log.error("clamd package not installed -- malware scanning skipped")
        return (True, None) if _dev else (False, "Malware scanning is unavailable -- upload rejected.")
    try:
        cd = _clamd_lib.ClamdNetworkSocket(host=_CLAMAV_HOST, port=_CLAMAV_PORT, timeout=15)
        pos = file_storage.stream.tell()
        file_storage.stream.seek(0)
        result = cd.instream(file_storage.stream)
        file_storage.stream.seek(pos)
        status, signature = result.get("stream", (None, None))
        if status == "FOUND":
            log_security_event("validation.failure", "Malware detected in upload", level="ERROR",
                               upload_filename=file_storage.filename, signature=signature)
            return False, "This file was flagged by malware scanning and cannot be uploaded."
        return True, None
    except Exception as _e:
        app_log.error("ClamAV scan failed (%s): %s", type(_e).__name__, _e)
        return (True, None) if _dev else (False, "File could not be scanned for malware -- please try again shortly.")


# ── File upload validation ─────────────────────────────────────────────────────
_ALLOWED_MIME_MAP = {
    "pdf": {"application/pdf"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "doc": {"application/msword"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xls": {"application/vnd.ms-excel"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
}
_MAX_DOC_SIZE_MB = 10


def _validate_upload(file_storage, allowed_exts=None):
    if not file_storage or not file_storage.filename:
        return False, "No file selected."
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if allowed_exts and ext not in allowed_exts:
        log_security_event("validation.failure", "Upload rejected: disallowed extension",
                           level="INFO", upload_filename=file_storage.filename, ext=ext)
        return False, f"File type .{ext} not allowed. Allowed: {', '.join(sorted(allowed_exts))}"
    ct = (file_storage.content_type or "").split(";")[0].strip().lower()
    if ct and ext in _ALLOWED_MIME_MAP and ct not in _ALLOWED_MIME_MAP[ext]:
        log_security_event("validation.failure", "Upload rejected: content-type/extension mismatch",
                           level="WARNING", upload_filename=file_storage.filename, ext=ext, content_type=ct)
        return False, "File content does not match its extension."
    header = file_storage.stream.read(8)
    file_storage.stream.seek(0)
    if ext == "pdf" and not header.startswith(b"%PDF"):
        log_security_event("validation.failure", "Upload rejected: magic bytes don't match .pdf",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, "Invalid PDF file."
    if ext == "png" and not header.startswith(b"\x89PNG"):
        log_security_event("validation.failure", "Upload rejected: magic bytes don't match .png",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, "Invalid PNG file."
    if ext in ("jpg", "jpeg") and not header.startswith(b"\xff\xd8"):
        log_security_event("validation.failure", "Upload rejected: magic bytes don't match .jpg",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, "Invalid JPEG file."
    if ext in ("docx", "xlsx") and not header.startswith(b"PK\x03\x04"):
        # Modern Office formats are just ZIP archives (OOXML) -- same
        # magic bytes as every other ZIP, but neither extension nor
        # content-type alone is attacker-controlled the way this check
        # is, so this is what actually stops a payload uploaded with a
        # spoofed .docx/.xlsx extension + matching Content-Type header.
        log_security_event("validation.failure", f"Upload rejected: magic bytes don't match .{ext}",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, f"Invalid {ext.upper()} file."
    if ext in ("doc", "xls") and not header.startswith(b"\xd0\xcf\x11\xe0"):
        # Legacy Office formats are OLE2 compound files, always starting
        # with this exact 4-byte signature (the same on-disk format .msi/
        # .msg files also use, so this only proves "a real OLE2 container",
        # not specifically Word/Excel -- ClamAV below is the real backstop
        # for what's actually inside one).
        log_security_event("validation.failure", f"Upload rejected: magic bytes don't match .{ext}",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, f"Invalid {ext.upper()} file."
    file_storage.stream.seek(0, 2)
    size_mb = file_storage.stream.tell() / (1024 * 1024)
    file_storage.stream.seek(0)
    if size_mb > _MAX_DOC_SIZE_MB:
        log_security_event("validation.failure", "Upload rejected: exceeds size limit",
                           level="INFO", upload_filename=file_storage.filename, size_mb=round(size_mb, 1))
        return False, f"File too large ({size_mb:.1f} MB). Maximum: {_MAX_DOC_SIZE_MB} MB."
    clean, scan_err = _scan_for_malware(file_storage)
    if not clean:
        return False, scan_err
    return True, None


_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_ALLOWED_IMG_MIME = {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/gif"}
_MAX_PHOTO_SIZE_MB = 5
_IMG_MAGIC = {
    ".jpg": (b"\xff\xd8",),
    ".jpeg": (b"\xff\xd8",),
    ".png": (b"\x89PNG",),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
}


def _validate_image_file(file):
    if not file or not file.filename:
        return False, "No file selected."
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _ALLOWED_IMG_EXT:
        log_security_event("validation.failure", "Photo upload rejected: disallowed extension",
                           level="INFO", upload_filename=file.filename, ext=ext)
        return False, f"Invalid file type '{ext}'. Only JPG, PNG, WEBP or BMP allowed."
    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct and ct not in _ALLOWED_IMG_MIME:
        log_security_event("validation.failure", "Photo upload rejected: disallowed content-type",
                           level="WARNING", upload_filename=file.filename, content_type=ct)
        return False, f"Invalid content type '{ct}'. Only image files accepted."
    header = file.stream.read(8)
    file.stream.seek(0)
    for magic in _IMG_MAGIC.get(ext, ()):
        if not header.startswith(magic):
            log_security_event("validation.failure", "Photo upload rejected: magic bytes mismatch",
                               level="WARNING", upload_filename=file.filename, ext=ext)
            return False, "File content does not match its extension."
    file.stream.seek(0, 2)
    size_mb = file.stream.tell() / (1024 * 1024)
    file.stream.seek(0)
    if size_mb > _MAX_PHOTO_SIZE_MB:
        return False, f"Photo too large ({size_mb:.1f} MB). Maximum: {_MAX_PHOTO_SIZE_MB} MB."
    clean, scan_err = _scan_for_malware(file)
    if not clean:
        return False, scan_err
    return True, ""


_LOGO_NAME_RE = re.compile(r'[^a-z0-9\-]')


def save_uploaded_logo(file_storage, name_hint):
    """Validate and save an uploaded company-logo image under
    static/company_logos/, named after name_hint (the tenant's subdomain
    slug, already restricted to [a-z0-9-] by org.py's _SUBDOMAIN_RE --
    scrubbed again here defensively since other callers may not enforce
    that). Returns (stored_ref, None) on success -- stored_ref is the
    "company_logos/x.png"-style relative path that
    company_settings.logo_url is built from, or (None, error_message) if
    the file is present but invalid. Callers should treat "no file
    provided" as optional and skip calling this entirely rather than
    treating it as an error.

    Deterministic filename (no re-upload dedup needed): a second signup
    attempt for the same subdomain just overwrites the previous file,
    which is fine since a subdomain can only ever back one live tenant."""
    from flask import current_app
    from utils.storage import save_public
    ok, err = _validate_image_file(file_storage)
    if not ok:
        return None, err
    ext = os.path.splitext(file_storage.filename)[1].lower()
    safe_name = _LOGO_NAME_RE.sub("", name_hint.lower()) or "logo"
    return save_public(current_app.root_path, file_storage, f"company_logos/{safe_name}{ext}",
                       content_type=file_storage.content_type)


_APPLICATION_DOC_KINDS = {
    "registration_cert": ("upload", {"pdf", "jpg", "jpeg", "png"}),
    "address_proof": ("upload", {"pdf", "jpg", "jpeg", "png"}),
    "visiting_card": ("image", None),
    "name_board_photo": ("image", None),
}


def save_application_document(file_storage, application_id, doc_kind):
    """Validate and save one KYC document for a pending company-signup
    application (blueprints/org.py's gated /create_org flow). Unlike
    save_uploaded_logo() above, this deliberately does NOT save under
    static/ -- these are business-verification documents (registration
    certificate, address proof, visiting card, name-board photo), not an
    asset meant to be publicly rendered on every dashboard. They're only
    ever read back server-side, by the platform-admin-only document-view
    route (blueprints/platform_admin.py), which reads via
    utils/storage.py's open_private() after checking
    @_platform_admin_required -- that route plus never constructing a
    client-facing URL to this path is the entire access control.

    Returns (stored_ref, None) on success -- stored_ref is an absolute
    local filesystem path, pass it straight into
    open_private()/delete_private() -- or (None, error_message).
    """
    from flask import current_app
    from utils.storage import save_private
    kind_info = _APPLICATION_DOC_KINDS.get(doc_kind)
    if not kind_info:
        return None, "Unknown document type."
    validator_kind, allowed_exts = kind_info
    if validator_kind == "image":
        ok, err = _validate_image_file(file_storage)
    else:
        ok, err = _validate_upload(file_storage, allowed_exts=allowed_exts)
    if not ok:
        return None, err
    ext = os.path.splitext(file_storage.filename)[1].lower()
    return save_private(current_app.root_path, file_storage,
                        f"tenant_applications/{int(application_id)}/{doc_kind}{ext}")
