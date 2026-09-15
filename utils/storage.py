# -*- coding: utf-8 -*-
"""Upload storage abstraction -- local disk today, S3-ready without a
behavior change until you actually opt in.

Why this exists: every upload path in this codebase (company logos,
employee documents, KYC application documents, ID-card photos/QR codes)
writes straight to the local filesystem under static/ or private_uploads/.
That's fine for a single dev/EC2-instance deployment, but breaks the
moment this runs as more than one container/instance (nothing shared
between them) or behind ephemeral container storage (a restart silently
loses every upload since the last deploy).

This module is a drop-in save/read/delete/url layer that:
  - Uses S3 when AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (or an IAM
    role -- boto3's default credential chain either way) AND S3_BUCKET
    are configured.
  - Falls back to the exact same local-disk behavior as before when
    they're not -- this is the ONLY mode actually exercised today, since
    no deployment has S3 configured yet. Local dev and the current
    production EC2 instance are both completely unaffected until someone
    sets S3_BUCKET.

boto3 is imported lazily, only inside the S3 code path (never at module
import time) -- same pattern utils/secrets_loader.py already uses for AWS
Secrets Manager, so a local dev environment that has never run `pip
install boto3` (it's in requirements.txt for production, not necessarily
installed in every dev venv) still works fine as long as S3 isn't
configured.

Callers, not this module, decide public vs. private:
  - save_public(): local path is under static/ (Flask serves it
    directly); S3 path uploads with public-read-equivalent access
    (bucket policy, not ACLs -- ACLs are deprecated/often disabled on
    newer buckets) and returns a direct object URL. For company logos,
    ID card template backgrounds -- pure branding artwork with no
    encoded meaning, genuinely safe to be world-readable.
  - save_private(): local path is OUTSIDE static/ (mirrors
    utils/helpers.py's save_application_document() convention); S3 path
    uploads to the same bucket but the URL returned is None -- callers
    must use open_private()/a presigned URL, never a bare object URL,
    for anything that needs an auth check first (employee documents, KYC
    application documents, attendance QR codes -- see qr_generator.py's
    generate_qr(): the QR image itself IS a bearer credential, an
    unsigned copy of it under static/ would let anyone who can guess an
    employee_id fetch a permanently-valid, replayable check-in code for
    that person with no login at all, which is exactly the attack
    qr_generator.py's own HMAC signature was added to prevent -- so the
    image has to be gated the same way the signature already is).

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
larger and riskier change to make blind without live S3 credentials to
verify against. Follow-up, not done here.
"""
import os


def s3_configured():
    """True if S3 storage is actually configured and should be used.
    The single source of truth every function below checks first."""
    return bool(
        os.environ.get("S3_BUCKET")
        and (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_EXECUTION_ENV") or os.environ.get("AWS_ROLE_ARN"))
    )


def _bucket():
    return os.environ.get("S3_BUCKET")


def _s3_client():
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


def _local_path(app_root_path, rel_path):
    return os.path.join(app_root_path, rel_path.replace("/", os.sep))


def save_public(app_root_path, file_storage, rel_path, content_type=None):
    """Save an upload meant to be world-readable (company logo, QR code).
    rel_path is relative to static/ either way, e.g. "company_logos/acme.png".

    Returns (url_or_static_path, error). On the local-disk fallback,
    returns the same "company_logos/acme.png"-style relative path callers
    already build a /static/ URL from today -- no caller-visible change.
    On S3, returns the full https:// object URL instead."""
    if s3_configured():
        try:
            key = f"static/{rel_path}"
            extra = {"ContentType": content_type} if content_type else {}
            stream = file_storage.stream if hasattr(file_storage, "stream") else file_storage
            _s3_client().upload_fileobj(stream, _bucket(), key, ExtraArgs=extra)
            region = os.environ.get("AWS_REGION", "ap-south-1")
            return f"https://{_bucket()}.s3.{region}.amazonaws.com/{key}", None
        except Exception as exc:
            return None, f"S3 upload failed: {exc}"

    full_path = _local_path(app_root_path, os.path.join("static", rel_path))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    file_storage.save(full_path)
    return rel_path, None


def save_private(app_root_path, file_storage, rel_path):
    """Save an upload that must stay behind an auth check (employee
    document, KYC application document). rel_path is relative to
    private_uploads/ either way, e.g. "tenant_applications/12/cert.pdf".

    Returns (stored_ref, error) -- stored_ref is an opaque string to pass
    back into open_private()/delete_private(), never a directly-usable
    URL (S3 object stays private; local path is outside static/)."""
    if s3_configured():
        try:
            key = f"private/{rel_path}"
            stream = file_storage.stream if hasattr(file_storage, "stream") else file_storage
            _s3_client().upload_fileobj(stream, _bucket(), key)
            return f"s3://{_bucket()}/{key}", None
        except Exception as exc:
            return None, f"S3 upload failed: {exc}"

    full_path = _local_path(app_root_path, os.path.join("private_uploads", rel_path))
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    file_storage.save(full_path)
    return full_path, None


def open_private(stored_ref):
    """Read back a private upload's bytes given the stored_ref save_private()
    returned. Works for both an "s3://bucket/key" ref and a plain local
    filesystem path, so callers don't need to branch on which mode is active."""
    if stored_ref.startswith("s3://"):
        _, _, rest = stored_ref.partition("s3://")
        bucket, _, key = rest.partition("/")
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    with open(stored_ref, "rb") as f:
        return f.read()


def delete_private(stored_ref):
    """Best-effort delete, mirroring the try/except-and-log pattern every
    local os.remove() call site in this codebase already uses -- returns
    True/False rather than raising, callers log the failure themselves
    with their own context (which employee/application/etc. it belonged to)."""
    if stored_ref.startswith("s3://"):
        _, _, rest = stored_ref.partition("s3://")
        bucket, _, key = rest.partition("/")
        _s3_client().delete_object(Bucket=bucket, Key=key)
        return True
    os.remove(stored_ref)
    return True
