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
        return (True, None) if _dev else (False, "Upload blocked: virus scanning is unavailable, so the file "
                                                 "couldn't be checked. Please try again later or contact support.")
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
            return False, "Upload blocked: the virus scanner flagged this file, so it can't be uploaded."
        return True, None
    except Exception as _e:
        app_log.error("ClamAV scan failed (%s): %s", type(_e).__name__, _e)
        return (True, None) if _dev else (False, "Upload blocked: the file couldn't be checked for viruses "
                                                 "right now. Please try again in a few minutes.")


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


def _size_text(n_bytes):
    """A file size the way people read it: "7.2 MB", "850 KB"."""
    if n_bytes >= 1024 * 1024:
        return f"{n_bytes / (1024 * 1024):.1f} MB"
    return f"{max(1, round(n_bytes / 1024))} KB"


def _not_real_file(kind):
    """The file's bytes aren't what its name says (renamed, damaged, or a
    disguised payload)."""
    return (f"Invalid {kind} file: its content isn't a real {kind} file. It may be damaged or "
            "renamed. Please choose a different file.")


def _validate_upload(file_storage, allowed_exts=None):
    if not file_storage or not file_storage.filename:
        return False, "No file selected: please choose a file to upload."
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if allowed_exts and ext not in allowed_exts:
        log_security_event("validation.failure", "Upload rejected: disallowed extension",
                           level="INFO", upload_filename=file_storage.filename, ext=ext)
        shown = f".{ext} files" if ext else "Files without an extension"
        return False, (f"File type not allowed: {shown} can't be uploaded here. "
                       f"Allowed types: {', '.join(e.upper() for e in sorted(allowed_exts))}.")
    ct = (file_storage.content_type or "").split(";")[0].strip().lower()
    if ct and ext in _ALLOWED_MIME_MAP and ct not in _ALLOWED_MIME_MAP[ext]:
        log_security_event("validation.failure", "Upload rejected: content-type/extension mismatch",
                           level="WARNING", upload_filename=file_storage.filename, ext=ext, content_type=ct)
        return False, (f"File type does not match its content: this isn't really a .{ext} file. "
                       "It may be damaged or renamed. Please choose a different file.")
    header = file_storage.stream.read(8)
    file_storage.stream.seek(0)
    if ext == "pdf" and not header.startswith(b"%PDF"):
        log_security_event("validation.failure", "Upload rejected: magic bytes don't match .pdf",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, _not_real_file("PDF")
    if ext == "png" and not header.startswith(b"\x89PNG"):
        log_security_event("validation.failure", "Upload rejected: magic bytes don't match .png",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, _not_real_file("PNG")
    if ext in ("jpg", "jpeg") and not header.startswith(b"\xff\xd8"):
        log_security_event("validation.failure", "Upload rejected: magic bytes don't match .jpg",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, _not_real_file("JPEG")
    if ext in ("docx", "xlsx") and not header.startswith(b"PK\x03\x04"):
        # Modern Office formats are just ZIP archives (OOXML) -- same
        # magic bytes as every other ZIP, but neither extension nor
        # content-type alone is attacker-controlled the way this check
        # is, so this is what actually stops a payload uploaded with a
        # spoofed .docx/.xlsx extension + matching Content-Type header.
        log_security_event("validation.failure", f"Upload rejected: magic bytes don't match .{ext}",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, _not_real_file(ext.upper())
    if ext in ("doc", "xls") and not header.startswith(b"\xd0\xcf\x11\xe0"):
        # Legacy Office formats are OLE2 compound files, always starting
        # with this exact 4-byte signature (the same on-disk format .msi/
        # .msg files also use, so this only proves "a real OLE2 container",
        # not specifically Word/Excel -- ClamAV below is the real backstop
        # for what's actually inside one).
        log_security_event("validation.failure", f"Upload rejected: magic bytes don't match .{ext}",
                           level="WARNING", upload_filename=file_storage.filename)
        return False, _not_real_file(ext.upper())
    file_storage.stream.seek(0, 2)
    n_bytes = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if n_bytes > _MAX_DOC_SIZE_MB * 1024 * 1024:
        log_security_event("validation.failure", "Upload rejected: exceeds size limit",
                           level="INFO", upload_filename=file_storage.filename,
                           size_mb=round(n_bytes / (1024 * 1024), 1))
        return False, (f"File too large: this file is {_size_text(n_bytes)}; the maximum is "
                       f"{_MAX_DOC_SIZE_MB} MB. Please choose a smaller file.")
    clean, scan_err = _scan_for_malware(file_storage)
    if not clean:
        return False, scan_err
    return True, None


_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_ALLOWED_IMG_MIME = {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/gif"}
_MAX_PHOTO_SIZE_MB = 5
# Photos of people (profile and face photos) must be at least this many
# pixels on each side, so the face is clear enough to recognise. The same
# rules are checked in the browser (static/js/photo-check.js) and the app
# (mobile/src/utils/photoRules.js); tests/test_clear_error_messages.py keeps
# the three in step.
PHOTO_MIN_PX = 200
_IMG_TYPES_TEXT = "JPG, PNG, WEBP or BMP"
# Shown under every photo picker, so people see the rules before choosing.
PHOTO_RULES_HINT = f"{_IMG_TYPES_TEXT} · up to {_MAX_PHOTO_SIZE_MB} MB · at least {PHOTO_MIN_PX} × {PHOTO_MIN_PX} px"
_IMG_MAGIC = {
    ".jpg": (b"\xff\xd8",),
    ".jpeg": (b"\xff\xd8",),
    ".png": (b"\x89PNG",),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
}


def _check_photo_pixels(file, n_bytes):
    """A person's photo must be a readable image of at least PHOTO_MIN_PX on
    each side. Reads only the image header (no full decode). Returns an
    error message, or None when the photo is fine."""
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(file.stream) as img:
            width, height = img.size
    except Image.DecompressionBombError:
        return ("Photo too large: this photo has more pixels than can be processed. "
                "Please choose a smaller photo or reduce its resolution.")
    except (UnidentifiedImageError, OSError, ValueError):
        return ("Unreadable photo: the file is damaged or isn't a complete image. "
                "Please choose a different photo.")
    finally:
        file.stream.seek(0)
    if width < PHOTO_MIN_PX or height < PHOTO_MIN_PX:
        return (f"Photo too small: this photo is {width} × {height} pixels ({_size_text(n_bytes)}); it must be "
                f"at least {PHOTO_MIN_PX} × {PHOTO_MIN_PX} pixels so the face is clear. "
                "Please choose a larger photo.")
    return None


def _validate_image_file(file, person_photo=False):
    """Checks an uploaded image. Returns (ok, message): message says what is
    wrong and why ("File too large: this photo is 7.2 MB; the maximum is
    5 MB ...").

    person_photo=True is for photos of a person (profile and face photos):
    they must also be readable and at least PHOTO_MIN_PX × PHOTO_MIN_PX
    pixels. Logos and ID-card artwork leave it False -- they can
    legitimately be small."""
    if not file or not file.filename:
        return False, "No photo selected: please choose a photo to upload."
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _ALLOWED_IMG_EXT:
        log_security_event("validation.failure", "Photo upload rejected: disallowed extension",
                           level="INFO", upload_filename=file.filename, ext=ext)
        shown = f"{ext} files" if ext else "Files without an extension"
        return False, f"File type not allowed: {shown} can't be used. Please choose a {_IMG_TYPES_TEXT} image."
    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct and ct not in _ALLOWED_IMG_MIME:
        log_security_event("validation.failure", "Photo upload rejected: disallowed content-type",
                           level="WARNING", upload_filename=file.filename, content_type=ct)
        return False, f"File type not allowed: this file isn't an image. Please choose a {_IMG_TYPES_TEXT} image."
    header = file.stream.read(8)
    file.stream.seek(0)
    for magic in _IMG_MAGIC.get(ext, ()):
        if not header.startswith(magic):
            log_security_event("validation.failure", "Photo upload rejected: magic bytes mismatch",
                               level="WARNING", upload_filename=file.filename, ext=ext)
            return False, (f"File type does not match its content: this isn't really a {ext} image. "
                           "It may be damaged or renamed. Please choose a different photo.")
    file.stream.seek(0, 2)
    n_bytes = file.stream.tell()
    file.stream.seek(0)
    if n_bytes > _MAX_PHOTO_SIZE_MB * 1024 * 1024:
        return False, (f"File too large: this photo is {_size_text(n_bytes)}; the maximum is "
                       f"{_MAX_PHOTO_SIZE_MB} MB. Please choose a smaller photo or reduce its size.")
    if n_bytes == 0:
        return False, "Empty file: the photo has no content. Please choose a different photo."
    if person_photo:
        problem = _check_photo_pixels(file, n_bytes)
        if problem:
            log_security_event("validation.failure", "Photo upload rejected: unreadable or too small",
                               level="INFO", upload_filename=file.filename)
            return False, problem
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

    The filename gets a random suffix (security review 2026-11-20). It used
    to be just the slug, and the public signup saved the logo before
    checking the slug was free, so anyone could overwrite a live company's
    logo by signing up with that company's address. Each upload is now a
    new, unguessable file; the caller stores the returned path."""
    from flask import current_app
    from utils.storage import save_public
    ok, err = _validate_image_file(file_storage)
    if not ok:
        return None, err
    ext = os.path.splitext(file_storage.filename)[1].lower()
    import secrets as _secrets
    safe_name = _LOGO_NAME_RE.sub("", name_hint.lower()) or "logo"
    unique = f"{safe_name}-{_secrets.token_hex(8)}"
    return save_public(current_app.root_path, file_storage, f"company_logos/{unique}{ext}",
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
