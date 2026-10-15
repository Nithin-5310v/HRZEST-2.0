# -*- coding: utf-8 -*-
"""Personal-data lifecycle: retention, subject access (export), erasure and
consent -- the controls India's DPDP Act 2023 and GDPR (Art. 9: biometric
data used to identify a person is special-category) expect and that the
platform previously had none of.

What each piece does
--------------------
retention   purge_expired(): deletes check-in face photos
            (dataset/face_logs) and punch evidence/challenge rows older than
            their window. Windows are env-configurable; defaults favour
            minimisation. Registered face templates are NOT time-limited
            here -- they live as long as the employment relationship and are
            removed by erase_employee().
export      export_employee_data(): everything held about one employee as
            JSON (decrypting the encrypted columns), for a subject-access
            request. Credentials/secrets and other people's data are never
            included.
erasure     erase_employee(): irreversibly removes or blanks the person's
            personal data and biometric artefacts, disables the account, and
            keeps only what a legal retention duty requires (attendance and
            payroll rows -- which no longer resolve to a person because the
            employees row is anonymised). Returns a manifest of what changed
            for the audit trail.
consent     record_consent()/consent_state(): append-only ledger of
            grant/withdraw per purpose and policy version.

Legal retention is a policy decision, not a code decision: review the
RETENTION settings and the "kept" list returned by erase_employee() with
counsel for your jurisdictions before relying on them.
"""
import datetime
import json
import os
import re

CONSENT_PURPOSES = ("biometric_attendance", "location_tracking")
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


# ── retention windows ───────────────────────────────────────────────────────
def _days(name, default):
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def face_log_retention_days():
    return _days("FACE_LOG_RETENTION_DAYS", 30)


def punch_evidence_retention_days():
    return _days("PUNCH_EVIDENCE_RETENTION_DAYS", 180)


def challenge_retention_days():
    return _days("CHECKIN_CHALLENGE_RETENTION_DAYS", 2)


def purge_face_logs(upload_folder, now=None, days=None):
    """Delete check-in photos older than the window. Only *.jpg directly
    under <upload_folder>/face_logs -- never the registered face images."""
    days = days or face_log_retention_days()
    now = now or datetime.datetime.now().timestamp()
    cutoff = now - days * 86400
    root = os.path.join(upload_folder, "face_logs")
    removed = 0
    if not os.path.isdir(root):
        return 0
    with os.scandir(root) as it:
        for entry in it:
            if not entry.is_file(follow_symlinks=False) or not entry.name.lower().endswith(".jpg"):
                continue
            try:
                if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                    os.remove(entry.path)
                    removed += 1
            except OSError:
                continue
    return removed


def purge_expired(cursor, upload_folder):
    """-> {'face_logs': n, 'punch_evidence': n, 'checkin_challenges': n}"""
    out = {"face_logs": purge_face_logs(upload_folder)}
    cursor.execute("DELETE FROM punch_evidence WHERE created_at < NOW() - (%s * INTERVAL '1 day')",
                   (punch_evidence_retention_days(),))
    out["punch_evidence"] = cursor.rowcount
    cursor.execute("DELETE FROM checkin_challenges WHERE expires_at < NOW() - (%s * INTERVAL '1 day')",
                   (challenge_retention_days(),))
    out["checkin_challenges"] = cursor.rowcount
    return out


# ── consent ledger ──────────────────────────────────────────────────────────
def record_consent(cursor, employee_id, purpose, policy_version, granted, ip=None):
    if purpose not in CONSENT_PURPOSES:
        raise ValueError(f"unknown consent purpose {purpose!r}")
    version = str(policy_version or "").strip()[:30]
    if not version:
        raise ValueError("policy_version required")
    cursor.execute(
        "INSERT INTO consent_records (employee_id, purpose, policy_version, granted, ip) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (employee_id, purpose, version, bool(granted), (ip or "")[:64]))
    return cursor.fetchone()[0]


def consent_state(cursor, employee_id, purpose):
    """-> {'granted': bool, 'policy_version': str|None, 'at': iso|None}; the
    most recent row wins, so a withdrawal after a grant reads as not granted."""
    cursor.execute(
        "SELECT granted, policy_version, created_at FROM consent_records "
        "WHERE employee_id=%s AND purpose=%s ORDER BY id DESC LIMIT 1",
        (employee_id, purpose))
    row = cursor.fetchone()
    if not row:
        return {"granted": False, "policy_version": None, "at": None}
    return {"granted": bool(row[0]), "policy_version": row[1], "at": str(row[2])}


# ── introspection helpers (schemas drift; never assume a column exists) ─────
def _table_exists(cursor, table):
    cursor.execute("SELECT to_regclass(%s)", (table,))
    return cursor.fetchone()[0] is not None


def _columns(cursor, table):
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s", (table,))
    return {r[0] for r in cursor.fetchall()}


def _rows(cursor, table, where_col, value, columns=None, limit=5000):
    if not _IDENT.match(table) or not _IDENT.match(where_col) or not _table_exists(cursor, table):
        return []
    have = _columns(cursor, table)
    if where_col not in have:
        return []
    cols = [c for c in (columns or sorted(have)) if c in have and _IDENT.match(c)]
    if not cols:
        return []
    col_list = ", ".join(chr(34) + c + chr(34) for c in cols)
    # nosec B608 -- identifiers are regex-validated and come from information_schema
    cursor.execute(f'SELECT {col_list} FROM "{table}" WHERE "{where_col}"=%s LIMIT {int(limit)}', (value,))  # nosec B608
    return [dict(zip(cols, [_jsonable(v) for v in r])) for r in cursor.fetchall()]


def _jsonable(v):
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return "[binary omitted]"
    return v


# ── export ──────────────────────────────────────────────────────────────────
# Encrypted-at-rest columns on employees, decrypted for the data subject.
_ENCRYPTED_EMPLOYEE_COLS = (
    "gender", "dob", "blood_group", "address", "city", "state", "pincode",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relation",
    "aadhar_number", "pan_number", "bank_name", "bank_account", "bank_ifsc", "uan_number",
)
# Never exported: credentials, reset tokens, credential public keys.
_EMPLOYEE_SECRET_COLS = {"password", "reset_token", "reset_token_expiry",
                         "fingerprint_credential_id", "fingerprint_public_key", "fingerprint_sign_count"}


def export_employee_data(cursor, employee_id, decrypt=None):
    if decrypt is None:
        from utils.helpers import decrypt_pii as decrypt
    emp_cols = [c for c in sorted(_columns(cursor, "employees")) if c not in _EMPLOYEE_SECRET_COLS]
    rows = _rows(cursor, "employees", "employee_id", employee_id, emp_cols, limit=1)
    profile = rows[0] if rows else {}
    for col in _ENCRYPTED_EMPLOYEE_COLS:
        if profile.get(col):
            profile[col] = decrypt(profile[col])
    # file references are paths, not content -- say a photo exists, not where
    for col in ("face_image", "qr_code"):
        if profile.get(col):
            profile[col] = "[on file]"

    data = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "employee_id": employee_id,
        "profile": profile,
        "attendance": _rows(cursor, "attendance", "employee_id", employee_id),
        "leave_requests": _rows(cursor, "leave_requests", "employee_id", employee_id),
        "tickets": _rows(cursor, "tickets", "employee_id", employee_id),
        "documents": _rows(cursor, "employee_documents", "employee_id", employee_id,
                           ["id", "doc_type", "document_type", "file_name", "filename", "uploaded_at",
                            "created_at", "status"]),
        "education": _rows(cursor, "employee_education", "employee_id", employee_id),
        "experience": _rows(cursor, "employee_experience", "employee_id", employee_id),
        "punch_evidence": _rows(cursor, "punch_evidence", "employee_id", employee_id,
                                ["channel", "auth_combo", "ip", "device_id", "lat", "lon", "mocked",
                                 "liveness", "flags", "outcome", "created_at"]),
        "consent": _rows(cursor, "consent_records", "employee_id", employee_id),
        "privacy_requests": _rows(cursor, "privacy_requests", "employee_id", employee_id,
                                  ["id", "kind", "status", "requested_at", "handled_at"]),
    }
    return data


# ── erasure ─────────────────────────────────────────────────────────────────
_EMPLOYEE_ERASE_NULL = (
    "email", "phone", "gender", "dob", "blood_group", "address", "city", "state", "pincode",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relation",
    "aadhar_number", "pan_number", "bank_name", "bank_account", "bank_ifsc", "uan_number",
    "about_me", "face_image", "qr_code", "work_lat", "work_lon",
    "fingerprint_credential_id", "fingerprint_public_key", "reset_token", "reset_token_expiry",
    "manager_name",
)
# (table, employee column) holding rows that are personal data with no
# retention duty -- deleted outright.
_DELETE_ROWS = (
    ("employee_education", "employee_id"),
    ("employee_experience", "employee_id"),
    ("notifications", "employee_id"),
    ("punch_evidence", "employee_id"),
    ("checkin_challenges", "employee_id"),
    ("known_login_ips", "identifier"),
    ("login_attempts", "identifier"),
    ("session_risk", "identifier"),
    ("api_tokens", "identity"),
    ("push_tokens", "owner_id"),
    ("mobile_biometric_proofs", "employee_id"),
)
# Free-text a person may have typed about themselves -- blanked, row kept.
_BLANK_TEXT = (
    ("leave_requests", "employee_id", ("reason",)),
    ("tickets", "employee_id", ("subject", "description", "message")),
    ("resignation_requests", "employee_id", ("reason",)),
)
_KEPT = (
    "attendance (statutory working-time record; no longer resolves to a person)",
    "payroll / salary records (statutory financial retention)",
    "leave counts and approvals (statutory)",
    "consent_records (proof of the consent history itself)",
    "audit_logs / security_events (append-only integrity records)",
)


def erase_employee(cursor, employee_id, upload_folder=None, remove_file=None):
    """Irreversible. `remove_file(path_or_ref)` deletes a stored file (default
    handles plain paths; the app passes a storage-aware remover). Returns a
    manifest. Raises LookupError if the employee does not exist."""
    cols = _columns(cursor, "employees")
    cursor.execute("SELECT face_image, qr_code FROM employees WHERE employee_id=%s", (employee_id,))
    row = cursor.fetchone()
    if row is None:
        raise LookupError(employee_id)
    face_ref, qr_ref = row

    def _rm(ref):
        if not ref:
            return False
        try:
            if remove_file:
                return bool(remove_file(ref))
            if os.path.isfile(ref):
                os.remove(ref)
                return True
        except OSError:
            pass
        return False

    manifest = {"employee_id": employee_id, "files_removed": 0, "rows_deleted": {},
                "blanked": [], "kept": list(_KEPT),
                "erased_at": datetime.datetime.utcnow().isoformat() + "Z"}

    # biometric + file artefacts
    for ref in (face_ref, qr_ref):
        manifest["files_removed"] += 1 if _rm(ref) else 0
    if upload_folder:
        reg = os.path.join(upload_folder, f"{employee_id}.jpg")
        manifest["files_removed"] += 1 if _rm(reg) else 0
        logs = os.path.join(upload_folder, "face_logs")
        if os.path.isdir(logs):
            prefix = f"{employee_id}_"
            with os.scandir(logs) as it:
                for e in it:
                    if e.is_file(follow_symlinks=False) and e.name.startswith(prefix):
                        manifest["files_removed"] += 1 if _rm(e.path) else 0

    # uploaded documents: files then rows
    if _table_exists(cursor, "employee_documents"):
        dcols = _columns(cursor, "employee_documents")
        ref_col = next((c for c in ("file_path", "path", "stored_path", "filename", "file_name") if c in dcols), None)
        if ref_col:
            cursor.execute(f'SELECT "{ref_col}" FROM employee_documents WHERE employee_id=%s', (employee_id,))  # nosec B608
            for (ref,) in cursor.fetchall():
                manifest["files_removed"] += 1 if _rm(ref) else 0
        cursor.execute("DELETE FROM employee_documents WHERE employee_id=%s", (employee_id,))
        manifest["rows_deleted"]["employee_documents"] = cursor.rowcount

    for table, col in _DELETE_ROWS:
        if _table_exists(cursor, table) and col in _columns(cursor, table):
            cursor.execute(f'DELETE FROM "{table}" WHERE "{col}"=%s', (employee_id,))  # nosec B608 -- constants above
            manifest["rows_deleted"][table] = cursor.rowcount

    for table, key, text_cols in _BLANK_TEXT:
        if not _table_exists(cursor, table):
            continue
        have = _columns(cursor, table)
        todo = [c for c in text_cols if c in have]
        if key in have and todo:
            cursor.execute(
                f'UPDATE "{table}" SET {", ".join(chr(34) + c + chr(34) + "=NULL" for c in todo)} '  # nosec B608
                f'WHERE "{key}"=%s', (employee_id,))
            manifest["blanked"].append(f"{table}.{'/'.join(todo)}")

    # the employee row itself: anonymise, disable, keep the key
    sets, params = [], []
    for c in _EMPLOYEE_ERASE_NULL:
        if c in cols:
            sets.append(f'"{c}"=NULL')
    if "name" in cols:
        sets.append('"name"=%s')
        params.append("Former employee")
    if "password" in cols:
        sets.append('"password"=%s')
        params.append("!erased")           # not a valid hash: can never authenticate
    if "is_active" in cols:
        sets.append('"is_active"=0')
    if "status" in cols:
        sets.append('"status"=%s')
        params.append("erased")
    if "email_alerts_enabled" in cols:
        sets.append('"email_alerts_enabled"=0')
    cursor.execute(f'UPDATE employees SET {", ".join(sets)} WHERE employee_id=%s',  # nosec B608 -- constants above
                   params + [employee_id])
    manifest["rows_deleted"]["employees(anonymised)"] = cursor.rowcount
    return manifest


def dumps(data):
    return json.dumps(data, indent=2, default=str)
