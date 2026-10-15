# -*- coding: utf-8 -*-
"""Shared utility helpers used across multiple blueprints."""
import os
import re
import json
import base64
import datetime
import hashlib
import threading
from contextlib import contextmanager

import pytz
from cryptography.fernet import Fernet, MultiFernet, InvalidToken as _FernetInvalid
from flask import session, request
from database import get_db_connection
from extensions import app_log, log_security_event, redis_client

_SAFE_IDENT_RE = re.compile(r'^[a-z][a-z0-9_]*$')

# Employee IDs are used to build filesystem paths (dataset/<emp_id>.jpg,
# private_uploads/qrcodes/<emp_id>.png) before the DB row necessarily exists yet
# (registration), so a DB existence check can't be relied on to reject
# path-traversal characters the way it does for update-in-place routes.
_EMP_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')


def validate_emp_id(emp_id: str) -> bool:
    return bool(emp_id) and bool(_EMP_ID_RE.match(emp_id))


def coerce_datetime(value):
    """A TIMESTAMP column comes back as a real datetime from Postgres, but
    as a plain str from the local-fallback SQLite path (database.py's
    _SqliteConnWrapper opens sqlite3.connect() with no detect_types, so
    declared column types aren't used to auto-convert query results).
    Callers doing datetime arithmetic or comparison/sorting on a value that
    might have come from either backend (e.g. blueprints/auto_debit.py's
    monthly-charge-due check, blueprints/platform_admin.py's payment-feed
    sort) should route it through this first rather than assume one type.
    Returns a datetime unchanged, parses an ISO-ish string, or None for
    anything else/unparseable -- never raises."""
    if value is None or isinstance(value, datetime.datetime):
        return value
    # A DATE column (e.g. monthly_invoices.billing_period) comes back from
    # psycopg2 as a plain datetime.date, not datetime.datetime -- must be
    # checked before the str branch since date isn't a datetime subclass.
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def tpath(path: str) -> str:
    """Prefix an absolute-path link/redirect target with the current
    tenant's URL prefix (request.script_root -- "" on marketing/platform-
    admin routes, "/<company-slug>" once the WSGI tenant-prefix wrapper in
    wsgi.py has stripped that slug into SCRIPT_NAME for a resolved tenant
    request). Used in place of bare redirect("/x")/href="/x" everywhere a
    link should stay within the current tenant's path, since this codebase
    builds links as literal absolute-path strings rather than via
    url_for(), which would pick up SCRIPT_NAME automatically.

    Idempotent by design: some inputs (e.g. a path pulled out of the raw
    Referer header in _safe_referrer_redirect, which reflects the real
    browser URL the visitor was on) already carry the tenant prefix, while
    most call sites pass a bare, unprefixed literal like "/admin" -- rather
    than track which is which at every call site, tpath() just checks
    whether the prefix is already there before adding it."""
    if not path.startswith("/"):
        return path
    try:
        prefix = request.script_root
    except RuntimeError:
        return path  # no request context (e.g. called from a script/test)
    if not prefix or path == prefix or path.startswith(prefix + "/"):
        return path
    return prefix + path


# ── Static asset cache-busting ────────────────────────────────────────────────
# filename -> (mtime, hash) -- lets static_url() below skip re-hashing a file
# on every request and only pay that cost the first time a filename is seen
# or after the file has actually changed on disk.
_STATIC_ASSET_CACHE = {}
_STATIC_ASSET_CACHE_LOCK = threading.Lock()


def static_url(filename: str) -> str:
    """Return "/static/<filename>?v=<hash>" for a CSS/JS (or any other)
    file under static/, so a deploy that changes a file's bytes is visible
    to browsers/CDNs immediately instead of being served stale for up to
    SEND_FILE_MAX_AGE_DEFAULT (extensions.py, currently 1 hour) -- or
    indefinitely by any intermediary that ignores that header.

    Templates call this in place of a bare "/static/x.js" href/src (this
    codebase writes static links as literal strings, same as tpath() above
    -- see its docstring) or `{{ url_for('static', filename='x.js') }}`.

    The hash is an md5 of the file's actual bytes, memoized in
    _STATIC_ASSET_CACHE keyed by filename and invalidated with a cheap
    os.path.getmtime() check -- so a request only re-reads+re-hashes the
    file when it's new to the cache or has actually changed, not on every
    page render of a busy HR app.

    This mtime-keyed, in-process cache is correct today because there is
    exactly one app instance reading static/ off its own local disk, so
    "the file changed" and "mtime changed" are the same fact, and every
    request sees the same cache. It would need to change (e.g. to a
    build-time manifest file checked into the deploy, or a shared
    Redis-backed cache) if this ever runs as multiple instances behind a
    load balancer -- each process's mtime cache would only reflect files
    it happens to have re-read locally, so a client could get inconsistent
    hashes for the same file across requests -- or if static/ moves to
    S3/a CDN origin instead of local disk, where there is no local mtime
    to check at all.
    """
    from extensions import app as _app
    static_root = _app.static_folder or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    path = os.path.join(static_root, filename)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return f"/static/{filename}"  # missing file -- let the resulting 404 happen with no query string

    cached = _STATIC_ASSET_CACHE.get(filename)
    if cached is None or cached[0] != mtime:
        with _STATIC_ASSET_CACHE_LOCK:
            cached = _STATIC_ASSET_CACHE.get(filename)
            if cached is None or cached[0] != mtime:
                try:
                    with open(path, "rb") as f:
                        digest = hashlib.md5(f.read(), usedforsecurity=False).hexdigest()[:10]  # cache-busting fingerprint, not security
                except OSError:
                    return f"/static/{filename}"
                cached = (mtime, digest)
                _STATIC_ASSET_CACHE[filename] = cached
    return f"/static/{filename}?v={cached[1]}"


def employee_login_url() -> str:
    """Absolute URL to the current tenant's own login page (e.g.
    "https://www.hrzest.com/acme/login") -- used in employee-welcome-email
    templates so a new hire has a clickable link straight to their
    company's portal, not just credentials with nowhere to use them.

    Built from session["tenant_slug"] rather than tpath()/request.script_root:
    the admin registering this employee may have reached the page via a
    bare, slug-less URL (an already-resolved tenant session cached from an
    earlier request -- the same edge case blueprints/core.py's home()
    documents), in which case tpath() would silently drop the slug and
    hand a brand-new employee, who has no session of their own yet, a
    link the WSGI tenant-prefix middleware can't resolve to any company."""
    try:
        slug = session.get("tenant_slug")
        base = request.host_url.rstrip("/")
    except RuntimeError:
        return "/login"
    return f"{base}/{slug}/login" if slug else f"{base}/login"


_APP_URL = os.environ.get("APP_URL", "").rstrip("/")


def _safe_app_url() -> str:
    """Return a trusted base URL. In production APP_URL must be set -- falling
    back to request.host_url is unsafe because the Host header is attacker-controlled."""
    if _APP_URL:
        return _APP_URL
    if os.environ.get("APP_ENV", "production") != "development":
        app_log.warning(
            "APP_URL is not set in hashi/.env -- password-reset links will use the "
            "request Host header, which is unsafe in production. "
            "Set APP_URL=https://yourdomain.com in hashi/.env to fix this."
        )
    return request.host_url.rstrip("/")


def _safe_redirect(dest: str, fallback: str = "/admin") -> str:
    """Validate that a redirect target is a relative path (prevents open
    redirect), then stamp it with the current tenant's URL prefix via
    tpath() so every caller gets a correctly-scoped link for free."""
    if dest and dest.startswith("/") and not dest.startswith("//"):
        return tpath(dest)
    return tpath(fallback)


def _safe_referrer_redirect(referrer: str, fallback: str) -> str:
    """Like _safe_redirect, but also accepts an absolute Referer header as long
    as it points back at this same app (scheme+host), reducing it to a
    relative path first. Referer is client-supplied and can be forged by
    non-browser HTTP clients, so it's never trusted as-is."""
    if not referrer:
        return tpath(fallback)
    from urllib.parse import urlparse as _urlparse
    p = _urlparse(referrer)
    if not p.scheme and not p.netloc:
        return _safe_redirect(referrer, fallback)
    if p.netloc == request.host:
        path = p.path or "/"
        return _safe_redirect(path + (("?" + p.query) if p.query else ""), fallback)
    return tpath(fallback)


# ── PII encryption (Fernet) -- fail-secure bootstrap ────────────────────────────
# Canonical location for this check. app.py used to carry a second,
# stricter copy (hard-fail in production, silent plaintext fallback in
# development) while this file's copy silently no-op'd in every
# environment -- a real gap: any future caller of THIS copy would have
# stored PAN/UAN/bank-account numbers in plaintext with no warning
# louder than a log line nobody was necessarily watching.
#
# Policy is now unconditional: missing or invalid ENCRYPTION_KEY is a hard
# abort in every environment, including local dev and CI, no exception.
# The previous "allow it in development" carve-out was itself the
# mechanism that let this exact class of bug hide -- a working-in-dev,
# broken-in-prod bootstrap teaches nobody to notice until it's live.
# Every environment that runs this code now needs a real key; generate
# one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
_ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "").strip()
if not _ENCRYPTION_KEY:
    app_log.critical(
        "FATAL: ENCRYPTION_KEY is not set. PAN, UAN, and bank account numbers "
        "require encryption at rest in every environment this application runs "
        "in -- refusing to start rather than silently storing PII as plaintext. "
        "Generate a key: python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\""
    )
    raise RuntimeError("ENCRYPTION_KEY is not set -- refusing to start (fail-secure).")
try:
    # Key rotation: ENCRYPTION_KEY always encrypts. ENCRYPTION_KEY_PREVIOUS
    # (comma-separated, newest first) is accepted for DECRYPTION only, so a
    # rotation is: set the new ENCRYPTION_KEY, move the old one into
    # ENCRYPTION_KEY_PREVIOUS, deploy, run scripts/rotate_encryption_key.py
    # to re-encrypt stored values, then drop the old key. With no previous
    # keys this behaves exactly like a single Fernet.
    _previous_keys = [k.strip() for k in os.environ.get("ENCRYPTION_KEY_PREVIOUS", "").split(",") if k.strip()]
    _fernet = MultiFernet([Fernet(k.encode()) for k in [_ENCRYPTION_KEY] + _previous_keys])
except Exception as _key_err:
    app_log.critical(
        "FATAL: ENCRYPTION_KEY is set but malformed (%s) -- refusing to start "
        "rather than silently storing PII as plaintext. Regenerate with: "
        "python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"",
        type(_key_err).__name__,
    )
    raise RuntimeError("ENCRYPTION_KEY is malformed -- refusing to start (fail-secure).") from _key_err


def encrypt_pii(value: str) -> str:
    if not value:
        return value
    return _fernet.encrypt(value.encode()).decode()


def _looks_like_fernet_token(value: str) -> bool:
    """True if `value` is at least shaped like a real Fernet token (right
    base64 alphabet, right minimum length, right version byte) -- as
    opposed to legacy plaintext written before PII encryption existed
    (e.g. a bare "Female" or "1988-03-10" seeded by an old migration or a
    test fixture), which decrypt_pii() must keep passing through
    unchanged rather than flagging as a decryption failure."""
    try:
        raw = base64.urlsafe_b64decode(value.encode())
    except Exception:
        return False
    # Fernet token = 1 version byte + 8 timestamp + 16 IV + >=16 ciphertext
    # (AES-CBC pads to a full block even for empty input) + 32 HMAC = 73
    # bytes minimum; version byte is always 0x80.
    return len(raw) >= 73 and raw[0:1] == b"\x80"


def decrypt_pii(value: str) -> str:
    if not value:
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except (_FernetInvalid, Exception):
        if not _looks_like_fernet_token(value):
            # Legacy plaintext that was never encrypted in the first
            # place -- not a failure, just pass it through as-is (existing
            # behavior, relied on by pre-encryption data / test fixtures).
            return value
        # This IS shaped like real ciphertext but failed to decrypt --
        # almost always ENCRYPTION_KEY having been rotated after this row
        # was written, or corrupted data. Previously this fell through to
        # returning the raw gAAAAAB... blob, rendered directly in the UI
        # and indistinguishable from a template bug to whoever's looking
        # at it. Log it (so a real key-rotation incident is actually
        # visible) and return an unambiguous placeholder instead; the
        # underlying ciphertext is unrecoverable without the original key
        # either way, so there's nothing useful to show the caller.
        app_log.warning("decrypt_pii: failed to decrypt a PII value (wrong/rotated ENCRYPTION_KEY or corrupted data) -- returning placeholder")
        return "[unable to decrypt]"


def decrypt_pii_date(value):
    """decrypt_pii() for employees.dob: that column used to be a native DATE
    and callers throughout the app call .strftime() on what it returns --
    widening it to TEXT so Fernet ciphertext fits (see app.py's
    employee_pii_columns_to_text_v1 migration) would otherwise silently
    turn every one of those call sites into an AttributeError. Returns a
    datetime.date (or None), never a bare string, so existing .strftime()
    call sites keep working unchanged."""
    if not value:
        return None
    decrypted = decrypt_pii(value)
    if isinstance(decrypted, datetime.date):
        return decrypted
    try:
        return datetime.datetime.strptime(str(decrypted), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ── DB context manager ────────────────────────────────────────────────────────
@contextmanager
def _db():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        yield cursor, conn
    finally:
        try:
            cursor.close()
        except Exception as _e:
            app_log.debug("cursor.close() failed: %s", _e)
        try:
            conn.close()
        except Exception as _e:
            app_log.debug("conn.close() failed: %s", _e)


# ── Audit logging ──────────────────────────────────────────────────────────────
def _audit(action, table=None, record_id=None, detail=None):
    try:
        actor = session.get("admin_username") or session.get("employee_id") or "system"
        actor_type = "admin" if session.get("admin_logged_in") else "employee"
        ip = request.remote_addr or ""
        db = get_db_connection()
        cursor = db.cursor()
        try:
            cursor.execute(
                "INSERT INTO audit_logs (actor, actor_type, action, target_table, target_id, detail, ip_address) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (actor, actor_type, action, table, str(record_id) if record_id is not None else None, detail, ip)
            )
            db.commit()
        finally:
            cursor.close()
            db.close()
    except Exception as exc:
        # A silently-lost audit_logs row undermines the whole point of an
        # audit trail -- worth a trace even though _audit() itself must
        # never raise and block the action it's recording.
        app_log.warning("_audit failed (action=%s, table=%s, record_id=%s): %s", action, table, record_id, exc, exc_info=True)


# ── Notification helper ───────────────────────────────────────────────────────
def _create_notification(recipient_type, title, message, employee_id=None, tenant_schema=None):
    """tenant_schema is only needed by a caller that has no flask.g.tenant_db
    of its own to rely on -- get_db_connection() falls back to the "public"
    schema when g.tenant_db is unset, which is exactly platform_admin.py's
    /super_admin/* routes (deliberately no resolved tenant there). Passing
    tenant_schema explicitly routes the INSERT at the right tenant's
    notifications table instead of silently landing in "public". Every
    existing call site leaves this None and is unaffected."""
    try:
        if tenant_schema is not None:
            from database import get_tenant_db
            db = get_tenant_db(tenant_schema)
        else:
            db = get_db_connection()
        cursor = db.cursor()
        try:
            cursor.execute(
                "INSERT INTO notifications (recipient_type, employee_id, title, message) VALUES (%s,%s,%s,%s)",
                (recipient_type, employee_id, title, message)
            )
            db.commit()
        finally:
            cursor.close()
            db.close()
    except Exception as exc:
        app_log.warning("_create_notification failed (recipient_type=%s, employee_id=%s): %s", recipient_type, employee_id, exc, exc_info=True)
        return
    # Mobile push -- best-effort, and only attempted once the in-app
    # notification row above actually landed. Handed to the same in-
    # process background writer utils/async_writer.py already uses for
    # high-frequency security bookkeeping (never Postgres-backed like
    # utils/email_utils.py's email_queue -- losing a push on a crash is an
    # acceptable tradeoff for a best-effort channel, the same call
    # async_writer.py's own docstring already makes for login-failure
    # counters) -- _send_expo_push()'s blocking urllib call (up to
    # _TIMEOUT_SECONDS) would otherwise run on the request thread handling
    # whatever leave/ticket/announcement action triggered this. Imported
    # lazily to avoid a circular import (utils/push_utils.py imports _db
    # from this module).
    from utils.async_writer import enqueue_write
    from utils.push_utils import send_push_notification, send_push_notification_broadcast
    if recipient_type == "employee" and employee_id:
        enqueue_write(send_push_notification, "employee", employee_id, title, message)
    elif recipient_type == "admin":
        enqueue_write(send_push_notification_broadcast, "admin", title, message)


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


# ── Company settings cache (60-second TTL) ────────────────────────────────────
# Every cache below is keyed by _tenant_cache_key(), not a bare {"data":...}
# singleton -- a single shared dict would let one tenant's company_settings/
# auth_config/companies-list/pending-counts leak into another tenant's
# response for up to its TTL window under ordinary concurrent multi-tenant
# traffic (two different companies' requests interleaved on the same
# process), not just a test artifact. get_db_connection() already scopes the
# underlying query correctly per g.tenant_db; only the cache layer on top of
# it was unscoped.
#
# Backed by Redis (shared across gunicorn workers) when extensions.redis_client
# is configured, falling back to this in-memory per-worker dict otherwise --
# same pattern as utils/waf.py's breach counter. Without Redis, an admin
# changing a setting on the worker that handles their request left every
# OTHER worker serving the stale value for up to _CO_CACHE_TTL seconds,
# since invalidate_settings_cache() only ever cleared its own process's dict.
_co_cache = {}
_auth_cache = {}
_settings_lock = threading.Lock()
# Configurable via env so an operator can trade off "settings changes are
# visible sooner" against "fewer DB round-trips under load" without a code
# change -- same pattern as utils/waf.py's threshold env vars. Default (60s)
# is what this cache has always used.
_CO_CACHE_TTL = int(os.environ.get("SETTINGS_CACHE_TTL_SECONDS", "60"))

# Sentinel distinct from None -- get_overdue_onboarding_count() legitimately
# caches 0, and get_auth_config()/get_company_settings() can cache a dict
# with falsy values, so "no entry" can't just be represented as a falsy return.
_CACHE_MISS = object()


def _tenant_cache_key():
    try:
        from flask import g as _flask_g
        return getattr(_flask_g, "tenant_db", None) or "__no_tenant__"
    except RuntimeError:
        return "__no_tenant__"  # no active Flask request/app context


def _co_expired(cache):
    entry = cache.get(_tenant_cache_key())
    return entry is None or entry["data"] is None or datetime.datetime.now() >= entry["expires"]


def _cache_get(mem_cache, redis_prefix):
    """Read a cached value for the current tenant. Tries Redis first when
    configured -- including falling back to the in-memory dict if a
    configured Redis errors or is unreachable mid-request, matching
    utils/waf.py's _record_breach_redis fallback. Returns _CACHE_MISS on
    a genuine miss (expired/absent), never None/falsy, since a cached
    value can itself be None-ish."""
    key = _tenant_cache_key()
    if redis_client is not None:
        try:
            raw = redis_client.get(f"{redis_prefix}:{key}")
            return json.loads(raw) if raw is not None else _CACHE_MISS
        except Exception as exc:
            app_log.warning("%s: redis read failed, using in-memory fallback: %s", redis_prefix, exc)
    with _settings_lock:
        if not _co_expired(mem_cache):
            return mem_cache[key]["data"]
    return _CACHE_MISS


def _cache_set(mem_cache, redis_prefix, data, ttl):
    """Write a cached value for the current tenant -- Redis when configured
    (falling back to the in-memory dict on error), the in-memory dict
    otherwise. The in-memory dict is kept as the fallback path even when
    Redis is configured, not written in parallel with it, since it's only
    ever read when a Redis attempt fails or Redis isn't configured at all."""
    key = _tenant_cache_key()
    if redis_client is not None:
        try:
            redis_client.setex(f"{redis_prefix}:{key}", ttl, json.dumps(data))
            return
        except Exception as exc:
            app_log.warning("%s: redis write failed, using in-memory fallback: %s", redis_prefix, exc)
    with _settings_lock:
        mem_cache[key] = {"data": data, "expires": datetime.datetime.now() + datetime.timedelta(seconds=ttl)}


def _redis_clear_prefix(prefix):
    """Delete every tenant's Redis key under this cache's namespace -- the
    Redis-backed equivalent of the in-memory dict's own .clear(). Uses
    SCAN rather than KEYS so this never blocks the shared Redis instance,
    even though the actual key count here (one per tenant) is small."""
    if redis_client is None:
        return
    try:
        keys = list(redis_client.scan_iter(match=f"{prefix}:*"))
        if keys:
            redis_client.delete(*keys)
    except Exception as exc:
        app_log.warning("Redis cache clear failed for prefix %s: %s", prefix, exc)


def invalidate_settings_cache():
    """Clears every tenant's cached entry, not just the caller's current
    one -- deliberately, matching this cache's original (pre-tenant-keying)
    "always clear everything" behavior. A caller that updated
    company_settings without an active Flask request context (a test
    fixture writing directly via its own DB connection, a script) would
    otherwise invalidate the "__no_tenant__" key while the real per-tenant
    entry that live requests actually read from stays stale -- invalidation
    is rare enough (only after a genuine settings write) that clearing
    everyone's cache is a fine trade for never getting this wrong."""
    with _settings_lock:
        _co_cache.clear()
        _auth_cache.clear()
    _redis_clear_prefix("settings_co")
    _redis_clear_prefix("settings_auth")


def post_announcement(cursor, db, title, content, priority, visibility, target_emp=None,
                      attachment_original_name=None, attachment_stored_ref=None):
    """Insert an `announcements` row and fan out the matching `notifications`
    row(s) -- shared by the web admin form (blueprints/admin_views.py's
    announcements_admin) and the Bearer-token API twin (blueprints/
    notifications.py's api_broadcast_notification), which previously each
    hand-rolled this identical insert-then-fan-out sequence. Uses the
    caller's own open cursor/connection so the public-audience fan-out stays
    one batched executemany() round-trip rather than _create_notification's
    one-connection-per-call pattern (deliberate -- see the perf note this
    replaced in announcements_admin).

    attachment_original_name/attachment_stored_ref (set by the caller after
    saving the upload via utils.storage.save_private) also drive an email
    to every recipient -- the in-app notification alone doesn't reach an
    employee who isn't currently logged in to check it."""
    cursor.execute(
        "INSERT INTO announcements (title, content, priority, visibility, target_employee_id, "
        "attachment_original_name, attachment_stored_ref) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (title, content, priority, visibility, target_emp, attachment_original_name, attachment_stored_ref)
    )
    db.commit()
    snippet = (content[:117] + "...") if len(content) > 120 else content
    if visibility == "private":
        _create_notification('employee', f"📢 {title}", snippet, target_emp)
        cursor.execute("SELECT email FROM employees WHERE employee_id=%s AND email IS NOT NULL AND email != ''",
                       (target_emp,))
        recipient_emails = [row[0] for row in cursor.fetchall()]
    else:
        cursor.execute("SELECT employee_id FROM employees WHERE is_active=1")
        emp_ids = [eid for (eid,) in cursor.fetchall()]
        if emp_ids:
            cursor.executemany(
                "INSERT INTO notifications (recipient_type, employee_id, title, message) "
                "VALUES ('employee', %s, %s, %s)",
                [(eid, f"📢 {title}", snippet) for eid in emp_ids]
            )
            db.commit()
            # This branch bypasses _create_notification() entirely (the
            # executemany() above is the whole point -- one round-trip for
            # the whole company instead of one per employee), so it needs
            # its own push dispatch too, via the bulk twin of
            # _create_notification's own enqueue_write() call below.
            from utils.async_writer import enqueue_write
            from utils.push_utils import send_push_notification_bulk
            enqueue_write(send_push_notification_bulk, "employee", emp_ids, f"📢 {title}", snippet)
        cursor.execute("SELECT email FROM employees WHERE is_active=1 AND email IS NOT NULL AND email != ''")
        recipient_emails = [row[0] for row in cursor.fetchall()]
    _email_announcement(title, content, priority, recipient_emails,
                        attachment_original_name, attachment_stored_ref)


def _email_announcement(title, content, priority, recipient_emails, attachment_name, attachment_ref):
    """Best-effort email fan-out for a newly posted announcement, queued
    through the same DB-backed email worker every other transactional email
    in this app uses (see utils/email_utils.py). Local imports avoid a
    circular import -- email_utils imports from this module already."""
    if not recipient_emails:
        return
    from utils.email_utils import get_email_config, send_email_async
    cfg = get_email_config()
    if not cfg:
        app_log.info("Announcement '%s' posted but SMTP isn't configured -- skipping email fan-out.", title)
        return
    attachment_bytes = None
    if attachment_ref:
        from utils.storage import open_private
        try:
            attachment_bytes = open_private(attachment_ref)
        except Exception as exc:
            app_log.warning("Could not read announcement attachment %s for email: %s", attachment_ref, exc)
    import html as _html_mod
    safe_title = _html_mod.escape(title)
    safe_content = _html_mod.escape(content).replace("\n", "<br>")
    attachment_note = (
        f'<div style="padding:0 20px 16px;color:#64748b;font-size:12px;">'
        f'📎 Attached: {_html_mod.escape(attachment_name)}</div>'
        if attachment_name else ""
    )
    html_body = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:600px;margin:auto;padding:20px;
         border:1px solid #e2e8f0;border-radius:12px;background:#ffffff;">
      <div style="background:#1e3a8a;padding:16px 20px;border-radius:8px 8px 0 0;color:#ffffff;">
        <h2 style="margin:0;font-size:18px;">📢 {safe_title}</h2>
      </div>
      <div style="padding:20px;color:#334155;font-size:14px;line-height:1.6;">
        {safe_content}
      </div>
      {attachment_note}
      <div style="border-top:1px solid #e2e8f0;padding:12px 20px;font-size:11px;color:#94a3b8;">
        Priority: {priority}. Sent via HRzest.com. Please do not reply directly to this automated email.
      </div>
    </div>
    """
    for email in recipient_emails:
        send_email_async(email, f"📢 {title}", html_body, cfg,
                         attachment_bytes=attachment_bytes, attachment_filename=attachment_name)


def get_employee_sidebar_info(cursor, emp_id):
    """(name, role, department, face_image) for the employee-portal sidebar
    -- byte-identical query previously duplicated in blueprints/performance.py
    (my_performance) and blueprints/leave.py (my_compoff)."""
    cursor.execute(
        "SELECT name, COALESCE(role,''), COALESCE(department,''), face_image FROM employees WHERE employee_id=%s",
        (emp_id,)
    )
    return cursor.fetchone()


_pending_counts_cache = {}
_PENDING_COUNTS_CACHE_TTL = 30  # shorter than company_settings' 60s -- these
# counts (leave/resignation/ticket approvals) change far more often, so a
# tighter window keeps sidebar badges from looking stale for too long.


def invalidate_pending_counts_cache():
    # Clears every tenant's entry -- see invalidate_settings_cache()'s
    # docstring for why (a caller without an active request context must
    # not silently invalidate the wrong key).
    with _settings_lock:
        _pending_counts_cache.clear()


def get_pending_counts():
    """(pending_leaves, pending_resignations, pending_tickets) for sidebar
    badges -- the single-tenant/unscoped count, cached for
    _PENDING_COUNTS_CACHE_TTL seconds. This used to be copy-pasted as three
    separate sequential cursor.execute() calls across a dozen route
    handlers (admin dashboard, employees, documents, attendance, tickets,
    leave, payroll, performance...), each paying that cost freshly on every
    single page load -- collapsing it to one shared cache is what actually
    fixes "clicking between sidebar modules feels slow," since almost every
    module page was re-running the identical query trio on every click.

    NOT for routes that scope these counts by active_company_id (multi-
    tenant) -- those need a live per-request query via co_scope_subquery,
    not this shared unscoped cache."""
    with _settings_lock:
        if not _co_expired(_pending_counts_cache):
            return tuple(_pending_counts_cache[_tenant_cache_key()]["data"])
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        # get_pending_action_counts() (below) is the actual query logic --
        # this just adds a cache on top of its default (unscoped,
        # open+in-progress) case, which is what every uncached call site
        # this replaced was already computing.
        result = tuple(get_pending_action_counts(cursor))
        cursor.close()
        db.close()
        with _settings_lock:
            _pending_counts_cache[_tenant_cache_key()] = {
                "data": result,
                "expires": datetime.datetime.now() + datetime.timedelta(seconds=_PENDING_COUNTS_CACHE_TTL),
            }
        return result
    except Exception:
        return (0, 0, 0)


def get_company_settings():
    cached = _cache_get(_co_cache, "settings_co")
    if cached is not _CACHE_MISS:
        return dict(cached)
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("SELECT * FROM company_settings LIMIT 1")
        row = cursor.fetchone()
        row_dict = {}
        if row:
            if hasattr(row, "keys"):
                row_dict = {k.lower(): row[k] for k in row.keys()}
            elif cursor.description:
                cols = [desc[0].lower() for desc in cursor.description]
                row_dict = dict(zip(cols, row))
        cursor.close()
        db.close()

        if row_dict:
            result = {
                "company_name": row_dict.get("company_name") or "My Company",
                "company_tagline": row_dict.get("company_tagline") or "HRzest.com",
                "company_logo": row_dict.get("company_logo"),
                "currency_symbol": row_dict.get("currency_symbol") or "₹",
                "company_code": row_dict.get("company_code") or "COMP",
                "timezone": row_dict.get("timezone") or "Asia/Kolkata",
                "setup_done": bool(row_dict.get("setup_done")),
                "session_timeout": row_dict.get("session_timeout") or 30,
                "logo_url": row_dict.get("logo_url") or "",
                "plan": row_dict.get("plan") or "basic",
                "email_domain": row_dict.get("email_domain") or "",
                # None = unlimited (unmetered tenant, or predates this
                # column); a real integer is the seat count actually paid
                # for at signup -- see add_employee_seat_cap_check() below.
                "paid_employee_slots": row_dict.get("paid_employee_slots"),
                "contact_phone": row_dict.get("contact_phone") or "",
                "address": row_dict.get("address") or "",
            }
            _cache_set(_co_cache, "settings_co", result, _CO_CACHE_TTL)
            return dict(result)
    except Exception as exc:
        # Falls through to generic hardcoded defaults below -- every page
        # in the app would silently render wrong branding/settings, worth
        # a trace.
        app_log.warning("get_company_settings failed, using generic defaults: %s", exc, exc_info=True)
    return {"company_name": "My Company", "company_tagline": "HRzest.com",
            "company_logo": None, "currency_symbol": "₹", "timezone": "Asia/Kolkata",
            "setup_done": False, "company_code": "", "session_timeout": 30, "logo_url": "", "plan": "basic",
            "email_domain": "", "paid_employee_slots": None, "contact_phone": "", "address": ""}


def _company_tzinfo():
    """Resolve the current tenant's configured timezone (company_settings.timezone,
    via get_company_settings() -- already tenant-scoped through get_db_connection()'s
    flask.g.tenant_db resolution) to a pytz tzinfo, falling back to Asia/Kolkata for an
    unset/unrecognized value -- same fallback get_company_settings() itself uses."""
    tz_name = (get_company_settings().get("timezone") or "Asia/Kolkata").strip()
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("Asia/Kolkata")


def company_now():
    """Current wall-clock datetime in the current tenant's configured timezone
    (company_settings.timezone), timezone-aware. Anchored to a real UTC instant
    first (datetime.datetime.now(pytz.utc)) rather than the naive local
    datetime.datetime.now(), since the server host's own system clock is not
    guaranteed to be UTC either. Use this (or company_today()) instead of
    datetime.datetime.now()/datetime.date.today() anywhere "today"/"now" is used
    to decide which calendar day a check-in, leave date, or payroll period
    belongs to."""
    return datetime.datetime.now(pytz.utc).astimezone(_company_tzinfo())


def company_today():
    """Current calendar date in the current tenant's configured timezone. See
    company_now() -- this is just company_now().date()."""
    return company_now().date()


# ── Company email domain (employee-registration gate) ────────────────────────
_DOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$')


def clean_email_domain(raw: str) -> str:
    """Normalize a user-entered company domain -- strips a leading
    scheme/"@"/"www.", any path, and lowercases it, so "https://Acme.com/"
    and "acme.com" both land on "acme.com"."""
    s = (raw or "").strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = s.lstrip('@')
    s = s.split('/')[0].split(':')[0]
    if s.startswith('www.'):
        s = s[4:]
    return s


def validate_email_domain_format(domain: str) -> str:
    """Returns an error message, or None if the domain string is a
    plausible one (e.g. "acme.com") -- format only, no DNS/MX lookup."""
    if not domain:
        return "Company email domain is required."
    if not _DOMAIN_RE.match(domain):
        return "Enter a valid domain, e.g. acme.com."
    return None


def validate_employee_email_domain(email) -> str:
    """Enforces "employee email must match the company's configured
    domain" -- but only once a company has actually set one
    (get_company_settings()["email_domain"]); companies that haven't
    configured a domain yet keep today's behavior (email optional, no
    domain check), so this never breaks an existing tenant that predates
    the feature. Returns an error message, or None if OK."""
    domain = (get_company_settings().get("email_domain") or "").strip().lower()
    if not domain:
        return None
    email = (email or "").strip().lower()
    if not email:
        return f"Employee email is required (must be a @{domain} address)."
    if not email.endswith("@" + domain):
        return f"Employee email must be a @{domain} address."
    return None


# ── Trial employee cap (hard, server-side, independent of the frontend) ──────
TRIAL_EMPLOYEE_CAP = 2


def _tenant_is_trialing() -> bool:
    """Cheap, lock-free check against the master control-plane DB (a
    different DB/connection than the tenant DB entirely, so it can't
    share the tenant-side row lock below anyway)."""
    try:
        from flask import g as _g
        from database import get_master_db
        db = get_master_db()
        cur = db.cursor(buffered=True)
        cur.execute("SELECT subscription_status FROM tenants WHERE db_name=%s", (_g.tenant_db,))
        row = cur.fetchone()
        cur.close()
        db.close()
        return bool(row and row[0] == "trialing")
    except Exception:
        return False


# ── Paid employee-seat cap (employee-registration gate) ──────────────────────
def add_employee_seat_cap_check(cursor=None) -> str:
    """Enforces whichever employee-count cap applies to this tenant --
    the hardcoded TRIAL_EMPLOYEE_CAP for a tenant mid-trial, or otherwise
    company_settings.paid_employee_slots (the seat count actually paid for
    at signup via blueprints/billing.py's payment-verified flow). Both
    checks live in this one function (rather than a separate
    _get_trial_employee_cap_error()) so there's exactly one COUNT(*) and
    one lock acquisition per call, not one per cap type. A slots value of
    None means unlimited: the free/unmetered provisioning paths
    (local-dev fallback, mobile app registration, Platform Admin's own
    tenant creation) never set this column, and existing tenants
    provisioned before this check existed also have it unset, so this
    never blocks them -- unless they're also mid-trial, which still
    applies TRIAL_EMPLOYEE_CAP regardless. Returns an error message ready
    to flash/return as-is, or None if there's room.

    Pass the caller's own open cursor -- the same one it will use for the
    employee INSERT right after this returns None -- to make the cap
    race-safe, AND wrap both this call and that INSERT in
    `with database.transaction(db):`. The transaction() part is not
    optional: database.py's connection pool hands out connections with
    autocommit=True (_borrow_connection()), so on a bare cursor (no
    explicit transaction) the row lock this takes below would release
    itself the instant its own SELECT statement finished -- a full
    statement earlier than the INSERT it's supposed to still be
    protecting -- and the race would be exactly as open as if no cursor
    had been passed at all. With a real transaction, and only when
    there's an actual cap to enforce (trialing, or paid_employee_slots is
    not None), this takes a row lock on company_settings FIRST (the one
    tenant-wide settings row), before counting employees, so a second
    concurrent caller blocks here until the first either commits its
    INSERT (and this recount then correctly sees it, and rejects) or
    rolls back (and this recount doesn't see it, so a slot is still
    free). That's what actually closes the classic check-then-insert
    TOCTOU race: two requests both reading COUNT()==cap-1 before either
    has inserted, and both proceeding. Unlimited, non-trial tenants skip
    the lock entirely -- there's no cap to protect, so there's no reason
    to serialize their employee creation.

    Called with no cursor (own short-lived, read-only connection,
    released immediately, no lock taken) is NOT race-safe by itself --
    it's kept only for a cheap early/UX preflight (e.g. rejecting before
    an expensive face-photo upload), not as the actual enforcement point.
    Every call site must also perform the cursor-and-transaction-carrying,
    lock-protected call immediately before its INSERT for the cap to
    actually hold under concurrency -- see blueprints/employees.py's
    add_employee_page()/api_register_employee() and blueprints/core.py's
    api_employee_signup() for the pattern.
    """
    is_trialing = _tenant_is_trialing()
    cap = get_company_settings().get("paid_employee_slots")
    if not is_trialing and cap is None:
        return None

    owns_conn = cursor is None
    db = None
    try:
        if owns_conn:
            db = get_db_connection()
            cursor = db.cursor()
        else:
            # Postgres: blocks concurrent callers until the lock holder's
            # transaction ends. SQLite fallback: FOR UPDATE isn't
            # supported and this execute() silently no-ops (see
            # database.py's _SqliteCursor.execute), which is fine there --
            # that fallback already serializes all queries behind one
            # process-wide lock, so no additional locking is needed.
            cursor.execute("SELECT id FROM company_settings ORDER BY id LIMIT 1 FOR UPDATE")

        cursor.execute("SELECT COUNT(*) FROM employees")
        current = cursor.fetchone()[0]
    except Exception:
        return None  # fail open -- a transient DB hiccup shouldn't block registration
    finally:
        if owns_conn:
            try:
                cursor.close()
                db.close()
            except Exception:  # nosec B110 -- best-effort close; must not mask the result
                pass

    if is_trialing and current >= TRIAL_EMPLOYEE_CAP:
        return (
            f"Your trial allows up to {TRIAL_EMPLOYEE_CAP} employees. "
            f"Upgrade under Settings → Finances → Billing to add more."
        )
    if cap is not None and current >= cap:
        return (
            f"You've reached your plan's limit of {cap} employee"
            f"{'s' if cap != 1 else ''}. Buy more seats under Seats & Billing to add more."
        )
    return None


# ── Companies list + overdue-onboarding count caches (short TTL) ─────────────
# Both back per-request context processors (app.py's inject_companies_context
# / inject_overdue_onboardings) that previously ran on every single
# admin-rendered page with no cache at all, stacking on top of the
# always-fresh security checks (_enforce_ip_ban, _enforce_admin_mfa_enrollment)
# that must stay uncached. These two are pure reference/reporting data -- a
# few seconds of staleness (a brand-new company not yet in the switcher, an
# onboarding-overdue badge lagging slightly) is an acceptable trade for
# cutting 2 of the ~4 DB round trips every admin page load previously paid.
_companies_cache = {}
_onboarding_cache = {}
_COMPANIES_CACHE_TTL = int(os.environ.get("COMPANIES_CACHE_TTL_SECONDS", "30"))
_ONBOARDING_CACHE_TTL = int(os.environ.get("ONBOARDING_CACHE_TTL_SECONDS", "20"))


def invalidate_companies_cache():
    # Clears every tenant's entry -- see invalidate_settings_cache()'s
    # docstring for why (a caller without an active request context must
    # not silently invalidate the wrong key).
    with _settings_lock:
        _companies_cache.clear()
    _redis_clear_prefix("settings_companies")


def get_companies_list():
    """Cached list of (id, name, code, has_pin) rows from the companies
    table -- tuples on the in-memory-fallback path, lists when served from
    Redis (JSON has no tuple type); every caller only ever index/iterates
    them, never checks the exact type. Call invalidate_companies_cache()
    after any write to companies (add/edit/delete/set-pin/rename-code)."""
    cached = _cache_get(_companies_cache, "settings_companies")
    if cached is not _CACHE_MISS:
        return list(cached)
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute("""
            SELECT id, name, COALESCE(code,''), COALESCE(pin,'')
            FROM companies ORDER BY name
        """)
        rows = cur.fetchall()
        cur.close()
        db.close()
        _cache_set(_companies_cache, "settings_companies", rows, _COMPANIES_CACHE_TTL)
        return list(rows)
    except Exception:
        return []


def get_overdue_onboarding_count():
    """Cached count of non-completed onboarding tasks past their due date."""
    cached = _cache_get(_onboarding_cache, "settings_onboarding")
    if cached is not _CACHE_MISS:
        return cached
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM employee_onboarding
            WHERE status != 'Completed' AND due_date < %s
        """, (datetime.date.today(),))
        count = cur.fetchone()[0]
        cur.close()
        db.close()
        _cache_set(_onboarding_cache, "settings_onboarding", count, _ONBOARDING_CACHE_TTL)
        return count
    except Exception:
        return 0


_AUTH_CONFIG_DEFAULTS = {
    "fingerprint_enabled": False, "qr_enabled": True, "face_enabled": True,
    "location_enabled": True, "employee_password_auth": True,
    "geo_radius": 100, "office_lat": None, "office_lon": None,
}


def get_auth_config():
    cached = _cache_get(_auth_cache, "settings_auth")
    if cached is not _CACHE_MISS:
        return dict(cached)
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("""
            SELECT COALESCE(fingerprint_enabled,0), COALESCE(qr_enabled,1),
                   COALESCE(face_enabled,1), COALESCE(location_enabled,1),
                   COALESCE(employee_password_auth,1), COALESCE(geo_radius,100),
                   office_lat, office_lon
            FROM company_settings LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()
        db.close()
        if row:
            result = {
                "fingerprint_enabled": bool(row[0]), "qr_enabled": bool(row[1]),
                "face_enabled": bool(row[2]), "location_enabled": bool(row[3]),
                "employee_password_auth": bool(row[4]), "geo_radius": row[5],
                "office_lat": row[6], "office_lon": row[7],
            }
            _cache_set(_auth_cache, "settings_auth", result, _CO_CACHE_TTL)
            return dict(result)
    except Exception as exc:
        app_log.warning("get_auth_config failed, using defaults: %s", exc, exc_info=True)
    return dict(_AUTH_CONFIG_DEFAULTS)


def allowed_checkin_combos(auth_cfg):
    """The attendance check-in methods this organisation's settings permit,
    decided on the server -- a client-supplied auth_combo outside this set
    must be rejected. Whenever face recognition is enabled, every allowed
    method includes the face check (so QR-only / fingerprint-only can't be
    used to skip it); otherwise QR and fingerprint methods are offered as
    enabled."""
    qr = bool(auth_cfg.get("qr_enabled"))
    face = bool(auth_cfg.get("face_enabled"))
    fingerprint = bool(auth_cfg.get("fingerprint_enabled"))
    allowed = set()
    if face:
        if qr:
            allowed.add("qr_face")
        if fingerprint:
            allowed.add("face_fingerprint")
    else:
        if qr:
            allowed.add("qr_only")
        if qr and fingerprint:
            allowed.add("qr_fingerprint")
        if fingerprint:
            allowed.add("fingerprint_only")
    return allowed


def _read_global_features():
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute("""
            SELECT face_auth_enabled, geo_enabled, COALESCE(geo_radius,300), qr_enabled,
                   pin_enabled, COALESCE(fingerprint_enabled,0), COALESCE(biometric_enabled,0),
                   COALESCE(notify_leave,1), COALESCE(notify_payslip,1),
                   COALESCE(notify_resignation,1), COALESCE(notify_doc_expiry,1),
                   COALESCE(session_timeout,30),
                   COALESCE(late_deduction_pct,10), COALESCE(half_day_deduction_pct,50),
                   COALESCE(grace_minutes,15), COALESCE(holiday_pay,'paid'),
                   COALESCE(leave_pay,'exclude'),
                   COALESCE(shift_start,'09:00:00'), COALESCE(shift_half,'13:00:00'),
                   COALESCE(shift_end,'18:00:00')
            FROM company_settings LIMIT 1
        """)
        r = cur.fetchone()
        cur.close()
        db.close()
        if r:
            return {
                "face_auth_enabled": bool(r[0]), "geo_enabled": bool(r[1]),
                "geo_radius": r[2], "qr_enabled": bool(r[3]), "pin_enabled": bool(r[4]),
                "fingerprint_enabled": bool(r[5]), "biometric_enabled": bool(r[6]),
                "notify_leave": bool(r[7]), "notify_payslip": bool(r[8]),
                "notify_resignation": bool(r[9]), "notify_doc_expiry": bool(r[10]),
                "session_timeout": r[11], "late_deduction_pct": float(r[12]),
                "half_day_deduction_pct": float(r[13]), "grace_minutes": int(r[14]),
                "holiday_pay": r[15], "leave_pay": r[16],
                "shift_start": r[17], "shift_half": r[18], "shift_end": r[19],
            }
    except Exception as exc:
        app_log.warning("_read_global_features failed, using defaults: %s", exc, exc_info=True)
    return {
        "face_auth_enabled": True, "geo_enabled": False, "geo_radius": 300,
        "qr_enabled": True, "pin_enabled": True, "fingerprint_enabled": False,
        "biometric_enabled": False, "notify_leave": True, "notify_payslip": True,
        "notify_resignation": True, "notify_doc_expiry": True, "session_timeout": 30,
        "late_deduction_pct": 10.0, "half_day_deduction_pct": 50.0, "grace_minutes": 15,
        "holiday_pay": "paid", "leave_pay": "exclude",
        "shift_start": "09:00:00", "shift_half": "13:00:00", "shift_end": "18:00:00",
    }


def get_co_features(company_id=None):
    if not company_id:
        return _read_global_features()
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute("""
            SELECT face_auth_enabled, geo_enabled, geo_radius, qr_enabled,
                   pin_enabled, fingerprint_enabled, biometric_enabled,
                   notify_leave, notify_payslip, notify_resignation, notify_doc_expiry,
                   session_timeout, late_deduction_pct, half_day_deduction_pct,
                   grace_minutes, holiday_pay, leave_pay, shift_start, shift_half, shift_end
            FROM company_feature_settings WHERE company_id=%s
        """, (company_id,))
        r = cur.fetchone()
        cur.close()
        db.close()
        if r:
            return {
                "face_auth_enabled": bool(r[0]), "geo_enabled": bool(r[1]),
                "geo_radius": r[2], "qr_enabled": bool(r[3]), "pin_enabled": bool(r[4]),
                "fingerprint_enabled": bool(r[5]), "biometric_enabled": bool(r[6]),
                "notify_leave": bool(r[7]), "notify_payslip": bool(r[8]),
                "notify_resignation": bool(r[9]), "notify_doc_expiry": bool(r[10]),
                "session_timeout": r[11], "late_deduction_pct": float(r[12]),
                "half_day_deduction_pct": float(r[13]), "grace_minutes": int(r[14]),
                "holiday_pay": r[15], "leave_pay": r[16],
                "shift_start": r[17], "shift_half": r[18], "shift_end": r[19],
            }
    except Exception as exc:
        app_log.warning("Per-company feature settings lookup failed, falling back to global: %s", exc, exc_info=True)
    return _read_global_features()


# shift_start/shift_half/shift_end/holiday_pay/leave_pay were missing from
# this allowlist versus app.py's copy -- not a security gap on their own
# (both copies fail closed on anything not listed), but a functional one:
# any caller trying to persist a per-company shift override through this
# copy would have been silently rejected while app.py's identical-looking
# function accepted it. Added to match.
_VALID_CFS_COLS = frozenset({
    "face_auth_enabled", "geo_enabled", "geo_radius", "qr_enabled", "pin_enabled",
    "fingerprint_enabled", "biometric_enabled", "notify_leave", "notify_payslip",
    "notify_resignation", "notify_doc_expiry", "session_timeout",
    "late_deduction_pct", "half_day_deduction_pct", "grace_minutes",
    "shift_start", "shift_half", "shift_end", "holiday_pay", "leave_pay",
})


def _upsert_co_feature(company_id, field, value):
    if not company_id:
        return
    # Double-gate: frozenset membership + regex ensures only safe identifier chars
    if field not in _VALID_CFS_COLS or not _SAFE_IDENT_RE.match(field):
        app_log.error("_upsert_co_feature: rejected column %r", field)
        return
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute(f"""
            INSERT INTO company_feature_settings (company_id, {field})
            VALUES (%s, %s)
            ON CONFLICT (company_id) DO UPDATE SET {field}=EXCLUDED.{field}
        """, (company_id, value))  # nosec B608
        db.commit()
        cur.close()
        db.close()
    except Exception as exc:
        # The caller (a settings-save route) has no idea this silently
        # failed -- it flashes "saved" while nothing actually persisted.
        app_log.warning("_upsert_co_feature failed (company_id=%s, field=%s): %s", company_id, field, exc, exc_info=True)


def _upsert_co_features(company_id, fields_dict):
    if not company_id or not fields_dict:
        return
    # Validate every key against frozenset AND regex before any interpolation
    bad = [k for k in fields_dict if k not in _VALID_CFS_COLS or not _SAFE_IDENT_RE.match(k)]
    if bad:
        app_log.error("_upsert_co_features: rejected columns %s", bad)
        return
    try:
        safe_fields = {k: v for k, v in fields_dict.items() if k in _VALID_CFS_COLS}
        cols = ", ".join(safe_fields.keys())
        vals = list(safe_fields.values())
        placeholders = ", ".join(["%s"] * len(vals))
        updates = ", ".join(f"{k}=EXCLUDED.{k}" for k in safe_fields.keys())
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute(f"""
            INSERT INTO company_feature_settings (company_id, {cols})
            VALUES (%s, {placeholders})
            ON CONFLICT (company_id) DO UPDATE SET {updates}
        """, [company_id] + vals)  # nosec B608
        db.commit()
        cur.close()
        db.close()
    except Exception as exc:
        app_log.warning("_upsert_co_features failed (company_id=%s, fields=%s): %s", company_id, list(fields_dict.keys()), exc, exc_info=True)


# ── Company-scoping WHERE fragments ─────────────────────────────────────────
# Was hand-repeated (6 near-identical copies) across admin_views.py/leave.py --
# the fragment is always a hardcoded literal chosen by whether an active
# company is selected, never user input; the actual value is always the
# single %s-bound param returned alongside it.
def co_scope_subquery(active_cid, alias=""):
    """WHERE fragment + params scoping by company via a subquery, for tables
    that don't have their own company_id column (attendance, leave_requests,
    tickets, ...). Returns ("", ()) when no active company is selected."""
    if not active_cid:
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN (SELECT employee_id FROM employees WHERE company_id=%s)", (active_cid,)  # nosec B608


def co_scope_column(active_cid, alias=""):
    """WHERE fragment + params scoping by company via a direct company_id
    column (e.g. the employees table itself)."""
    if not active_cid:
        return "", ()
    col = f"{alias}.company_id" if alias else "company_id"
    return f"AND {col}=%s", (active_cid,)


# ── HR-scoping WHERE fragments + ownership check ────────────────────────────
# Same shape as the company-scoping pair above, but for an HR-role admin
# session's assigned_hr_username instead of a selected company -- session-
# driven (no active_cid-style param needed) since "which HR is this" is
# always exactly session["admin_username"] when session["admin_role"] is
# HR_ROLE, never a value a caller picks. blueprints/employees.py's
# view_employees() had this exact pattern hand-inlined 3x before these
# existed; new call sites should use these instead of re-inlining again.
def hr_scope_subquery(alias=""):
    """WHERE fragment + params scoping to an HR session's assigned
    employees via a subquery, for tables that don't have their own
    assigned_hr_username column (attendance, leave_requests, tickets, ...).
    Returns ("", ()) for a non-HR (or unauthenticated) session -- 'admin'
    and every other admin-side role stay fully unscoped, exactly as
    before this existed."""
    from utils.auth import HR_ROLE
    if session.get("admin_role") != HR_ROLE:
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN (SELECT employee_id FROM employees WHERE assigned_hr_username=%s)", (session.get("admin_username"),)  # nosec B608


def hr_scope_column(alias=""):
    """WHERE fragment + params scoping by a direct assigned_hr_username
    column (e.g. the employees table itself)."""
    from utils.auth import HR_ROLE
    if session.get("admin_role") != HR_ROLE:
        return "", ()
    col = f"{alias}.assigned_hr_username" if alias else "assigned_hr_username"
    return f"AND {col}=%s", (session.get("admin_username"),)


def hr_scope_denied(emp_id):
    """True if the current session is HR-role and emp_id is NOT one of its
    assigned employees -- i.e. this request should be rejected. Always
    False for 'admin' (and any other non-HR admin-side role). Generalized
    from blueprints/employees.py's original _hr_scope_denied (that file now
    imports this instead of keeping its own copy) so every blueprint with a
    single-record action route (approve a leave, resolve a ticket, correct
    an attendance row, ...) can guard it the same way, rather than each
    silently allowing an HR session to act on any employee's record by URL/
    ID edit -- which is what every one of those routes did before this.

    Re-queries the DB on every call rather than trusting anything cached in
    the session -- an HR session reaching a DIFFERENT HR's employee by
    editing an id in the request must be caught against the current, real
    row every time."""
    from utils.auth import HR_ROLE
    if session.get("admin_role") != HR_ROLE:
        return False
    if emp_id == session.get("admin_username"):
        return False
    with _db() as (cursor, _conn):
        cursor.execute("SELECT assigned_hr_username FROM employees WHERE employee_id=%s", (emp_id,))
        row = cursor.fetchone()
    return not row or row[0] != session.get("admin_username")


# ── Manager-scoping WHERE fragments + ownership check ───────────────────────
# Same shape as the HR-scoping trio above, but for a manager-role session's
# direct reports instead of an HR's assigned employees. Reuses the existing
# employees.manager_id column (already populated for the org chart --
# blueprints/employees.py's add/edit forms, blueprints/admin_views.py's
# api_org_chart_data()) rather than adding a new link table: a manager's
# admin_users.username is always that same person's own employee_id (see
# blueprints/auth.py's _ensure_manager_admin_account -- it's auto-
# provisioned FROM an employee row, sharing that row's ID), so
# "employees reporting to this manager" is just employees.manager_id
# matching session["admin_username"], no new schema needed.
def manager_scope_subquery(alias=""):
    """WHERE fragment + params scoping to a manager session's direct
    reports via a subquery, for tables without their own manager_id
    column (leave_requests, resignation_requests, overtime). Returns
    ("", ()) for a non-manager (or unauthenticated) session."""
    from utils.auth import MANAGER_ROLE
    if session.get("admin_role") != MANAGER_ROLE:
        return "", ()
    col = f"{alias}.employee_id" if alias else "employee_id"
    return f"AND {col} IN (SELECT employee_id FROM employees WHERE manager_id=%s)", (session.get("admin_username"),)  # nosec B608


def manager_scope_column(alias=""):
    """WHERE fragment + params scoping by a direct manager_id column
    (the employees table itself)."""
    from utils.auth import MANAGER_ROLE
    if session.get("admin_role") != MANAGER_ROLE:
        return "", ()
    col = f"{alias}.manager_id" if alias else "manager_id"
    return f"AND {col}=%s", (session.get("admin_username"),)


def manager_scope_denied(emp_id):
    """True if the current session is manager-role and emp_id is NOT one
    of its direct reports -- mirrors hr_scope_denied's shape exactly,
    including the same self-access exception (a manager acting on their
    own employee_id, e.g. their own leave request appearing in a shared
    list, is never denied by this check alone)."""
    from utils.auth import MANAGER_ROLE
    if session.get("admin_role") != MANAGER_ROLE:
        return False
    if emp_id == session.get("admin_username"):
        return False
    with _db() as (cursor, _conn):
        cursor.execute("SELECT manager_id FROM employees WHERE employee_id=%s", (emp_id,))
        row = cursor.fetchone()
    return not row or row[0] != session.get("admin_username")


# ── Pending-action header counters ──────────────────────────────────────────
# Was hand-repeated (15+ near-identical copies, some drifted to a narrower
# tickets filter) across admin_views.py/attendance.py/documents.py/
# employees.py/leave.py/payroll.py/performance.py/tickets.py/core.py — the
# small badge counts most admin pages show in their header/sidebar.
def get_pending_action_counts(cursor, active_cid=None, tickets_open_only=False):
    """Returns (pending_leaves, pending_resignations, pending_tickets).

    `active_cid` scopes all three counts to one company (via
    co_scope_subquery) when given, else counts across all companies.
    `tickets_open_only` narrows the ticket count to status='Open' instead of
    the default 'Open'+'In Progress' -- a handful of pages intentionally
    show the narrower count."""
    co_sub, co_args = co_scope_subquery(active_cid)
    cursor.execute(f"SELECT COUNT(*) FROM leave_requests WHERE status='Pending' {co_sub}", co_args)  # nosec B608
    pending_leaves = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM resignation_requests WHERE status='Pending' {co_sub}", co_args)  # nosec B608
    pending_resignations = cursor.fetchone()[0]
    ticket_filter = "status='Open'" if tickets_open_only else "status IN ('Open','In Progress')"
    cursor.execute(f"SELECT COUNT(*) FROM tickets WHERE {ticket_filter} {co_sub}", co_args)  # nosec B608
    pending_tickets = cursor.fetchone()[0]
    return pending_leaves, pending_resignations, pending_tickets


# ── Error page renderer ───────────────────────────────────────────────────────
# This used to render_template("error.html", ...) -- that template doesn't
# exist anywhere in templates/. Every call would have raised
# jinja2.exceptions.TemplateNotFound, turning a 404/403/500 handler into a
# second, unhandled 500. Never triggered because nothing called this copy
# (app.py's own separate, working implementation handled every real error
# page) -- found by checking whether the "weaker" duplicate was even
# functional, not just less-featured, before deciding which one to keep.
# Replaced with app.py's version (session-aware back-navigation, inline
# styling, no template dependency) rather than fixing the missing
# template, since that's what every real error page has actually looked
# like in production.
def _error_page(code, icon, title, subtitle, hint):
    back_admin = session.get("admin_logged_in")
    back_emp = session.get("employee_id")
    back_link = "/admin" if back_admin else ("/employee_portal" if back_emp else "/")
    back_label = "Go to Admin Dashboard" if back_admin else ("Go to My Portal" if back_emp else "Go to Home")
    # Same landing_v2.css design system as admin_login.html/create_org.html
    # (see admin_login.html's header comment) -- this page used to be the
    # last one still on the old plain blue-and-white style.
    return f"""<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{code} – {title}</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
<link rel="stylesheet" href="{static_url('shared.min.css')}" />
<link rel="stylesheet" href="{static_url('landing_v2.css')}" />
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: var(--font-body); }}
  html, body {{ min-height: 100vh; color: var(--text-main); }}
  body {{
    background: linear-gradient(160deg, #FDF3E3 0%, #F7ECF7 28%, #EAF0FC 56%, var(--bg-secondary) 100%);
    background-attachment: fixed;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }}
  .box {{
    width: 100%; max-width: 460px; text-align: center;
    background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-lg);
    padding: 48px 40px 40px; box-shadow: var(--shadow-lg); position: relative; overflow: hidden;
  }}
  .box::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; background: var(--gradient-brand); }}
  .icon {{
    width: 68px; height: 68px; margin: 0 auto 18px; border-radius: var(--radius-md);
    background: var(--gradient-brand); display: flex; align-items: center; justify-content: center;
    font-size: 32px; box-shadow: 0 8px 24px rgba(79, 70, 229, 0.35);
  }}
  .code {{ font-family: var(--font-heading); font-size: 15px; font-weight: 800; letter-spacing: 2px;
           color: var(--accent-cyan); margin-bottom: 8px; text-transform: uppercase; }}
  .title {{ font-family: var(--font-heading); font-size: 22px; font-weight: 800; color: var(--text-main); margin-bottom: 10px; }}
  .sub {{ font-size: 14px; color: var(--text-muted); margin-bottom: 6px; line-height: 1.6; }}
  .hint {{ font-size: 12.5px; color: var(--text-subtle); margin-bottom: 28px; }}
  .actions {{ display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }}
  a.btn {{
    display: inline-flex; align-items: center; justify-content: center;
    padding: 12px 26px; border-radius: var(--radius-sm); background: var(--gradient-brand);
    color: #fff; font-size: 14px; font-weight: 700; text-decoration: none;
    transition: var(--transition-fast); box-shadow: var(--shadow-md);
  }}
  a.btn:hover {{ box-shadow: var(--shadow-lg); transform: translateY(-1px); }}
  a.sec {{
    display: inline-flex; align-items: center; justify-content: center;
    padding: 12px 22px; border-radius: var(--radius-sm); background: var(--bg-secondary);
    color: var(--text-main); font-size: 14px; font-weight: 600; text-decoration: none;
    transition: var(--transition-fast); border: 1px solid var(--border-color);
  }}
  a.sec:hover {{ background: var(--bg-tertiary); }}
  @media (max-width: 480px) {{ .box {{ padding: 36px 26px 30px; }} }}
</style></head><body>
<div class="box">
  <div class="icon">{icon}</div>
  <div class="code">Error {code}</div>
  <div class="title">{title}</div>
  <div class="sub">{subtitle}</div>
  <div class="hint">{hint}</div>
  <div class="actions">
    <a href="{back_link}" class="btn">{back_label}</a>
    <a href="javascript:history.back()" class="sec">← Go Back</a>
  </div>
</div>
</body></html>""", code
