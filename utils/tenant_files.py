# -*- coding: utf-8 -*-
"""Per-company locations for employee face photos and check-in face logs.

These used to live flat in UPLOAD_FOLDER as <employee_id>.jpg. Every
company shares that folder and employee IDs repeat across companies
(EMP001, ...), so two companies' EMP001 overwrote one reference photo: a
company could replace another company's reference face with its own, and
any company admin could read any company's photos via /dataset/<file>.

New files go to UPLOAD_FOLDER/tenants/<tenant>/. The authoritative photo for
an employee is always the path stored in THIS company's employees.face_image
row (resolve_face_photo), so rows written before the move -- still pointing
at the old flat file -- keep working until scripts/migrate_face_photos_per_tenant.py
moves them.
"""
import os

_DEFAULT_UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset")


def tenant_key():
    """The company the current request runs as: flask.g.tenant_db, with the
    single-tenant default (DB_NAME, or no tenant at all) normalised to
    "public" -- the schema it lives in, and what a migration cursor's
    search_path reports for it."""
    try:
        from flask import g, has_app_context
        tenant = getattr(g, "tenant_db", None) if has_app_context() else None
    except Exception:
        tenant = None
    if not tenant or tenant == os.environ.get("DB_NAME", "employee_attendance"):
        return "public"
    return tenant


def cursor_tenant(cursor):
    """The tenant schema a DB cursor runs against: the first entry of its
    search_path (database.py's _set_search_path sets '"<tenant>", public'),
    normalised like tenant_key(). For code with no reliable request --
    migrations, jobs, a tenant provisioned from inside another request."""
    cursor.execute("SHOW search_path")
    first = (cursor.fetchone()[0] or "public").split(",")[0].strip().strip('"')
    if not first or first.startswith("$") or first == os.environ.get("DB_NAME", "employee_attendance"):
        return "public"
    return first


def upload_root():
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            return current_app.config.get("UPLOAD_FOLDER") or _DEFAULT_UPLOAD_FOLDER
    except Exception:
        pass
    return _DEFAULT_UPLOAD_FOLDER


def _safe_name(name):
    name = str(name or "")
    if not name or os.path.basename(name) != name or name in (".", "..") or "\\" in name:
        raise ValueError(f"Unsafe file name component: {name!r}")
    return name


def tenant_upload_dir(tenant=None, create=True):
    path = os.path.join(upload_root(), "tenants", _safe_name(tenant or tenant_key()))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def face_photo_path(emp_id, tenant=None):
    """Where this company's reference photo for emp_id is written."""
    return os.path.join(tenant_upload_dir(tenant), _safe_name(emp_id) + ".jpg")


def legacy_face_photo_path(emp_id):
    """The old, shared flat location -- read-only, for migration/erasure."""
    return os.path.join(upload_root(), _safe_name(emp_id) + ".jpg")


def face_logs_dir(tenant=None):
    path = os.path.join(tenant_upload_dir(tenant), "face_logs")
    os.makedirs(path, exist_ok=True)
    return path


def _inside_upload_root(path):
    root = os.path.realpath(upload_root())
    try:
        return os.path.commonpath([root, os.path.realpath(path)]) == root
    except ValueError:
        return False


def resolve_face_photo(emp_id):
    """Path of emp_id's photo in the CURRENT company, or None. Reads this
    company's own employees.face_image row first (so pre-migration rows
    still resolve), then the per-company location. Never looks at another
    company's files."""
    try:
        name = _safe_name(emp_id)
    except ValueError:
        return None
    stored = None
    try:
        from database import get_db_connection
        db = get_db_connection()
        try:
            cur = db.cursor()
            cur.execute("SELECT face_image FROM employees WHERE employee_id=%s", (name,))
            row = cur.fetchone()
            cur.close()
        finally:
            db.close()
        stored = row[0] if row else None
    except Exception:
        stored = None
    if stored and os.path.isfile(stored) and _inside_upload_root(stored):
        return stored
    path = face_photo_path(name)
    return path if os.path.isfile(path) else None
