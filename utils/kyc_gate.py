# -*- coding: utf-8 -*-
"""Restricted mode for companies whose KYC documents aren't approved yet.

Security audit P3 (2026-11-20), option B. A self-serve company gets its
tenant as soon as its email is verified, so signup stays instant. Until its
KYC documents are approved (by utils/document_verification.py's automated
check or by a platform admin), the features a fraudster would want are
closed:

* salary payouts: saving the payout bank account and approving a run;
* bulk email (the email blast).

Companies with no self-serve application (created by a platform admin, older
tenants, the single-tenant default) are not affected.

KYC_LIMITED_MODE=0 turns the restriction off.
"""
import os
from functools import wraps

from flask import flash, g, has_request_context, jsonify, redirect

from extensions import app_log, log_security_event

KYC_PENDING_MSG = ("This is available once your company's documents are verified. "
                   "Upload them from the registration checklist; verification usually takes a few minutes.")


def limited_mode_enabled() -> bool:
    return os.environ.get("KYC_LIMITED_MODE", "1").strip().lower() not in ("0", "false", "off", "no")


def _kyc_status_for(tenant_db):
    """'approved', another doc_review_status value, or None when the
    company has no self-serve application. Raises on a database error."""
    from database import get_master_db
    conn = get_master_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(a.doc_review_status, 'not_submitted') FROM tenant_applications a "
            "JOIN tenants t ON t.id = a.tenant_id WHERE t.db_name = %s ORDER BY a.id DESC LIMIT 1",
            (tenant_db,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return row[0] if row else None


def company_kyc_verified() -> bool:
    """True if the current company may use payout and bulk-email features.

    Fails CLOSED: if the status can't be read, the gated action is refused
    (these are money-movement and mass-mail actions; a retry is cheap).
    """
    if not limited_mode_enabled():
        return True
    if not has_request_context():
        return True
    cached = getattr(g, "_kyc_verified", None)
    if cached is not None:
        return cached
    tenant_db = getattr(g, "tenant_db", None)
    if not tenant_db:
        return True
    try:
        status = _kyc_status_for(tenant_db)
    except Exception as exc:
        app_log.error("KYC status check failed for %s: %s", tenant_db, type(exc).__name__)
        return False
    verified = status is None or status == "approved"
    g._kyc_verified = verified
    return verified


def kyc_verified_required(redirect_path=None):
    """Refuse the route until the company's KYC is approved: a JSON 403, or
    for a form route (``redirect_path`` given) a flash message and a
    redirect to that fixed path.

    Put it under the auth decorators so only signed-in users reach it."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not company_kyc_verified():
                log_security_event("access.denied", "Feature closed until the company's KYC is approved",
                                   level="INFO", tenant_db=getattr(g, "tenant_db", None))
                if redirect_path:
                    from utils.helpers import tpath
                    flash(KYC_PENDING_MSG, "error")
                    return redirect(tpath(redirect_path))
                return jsonify({"ok": False, "kyc_pending": True, "msg": KYC_PENDING_MSG}), 403
            return f(*args, **kwargs)
        wrapper._hrz_kyc_gate = True  # read by tests/test_kyc_gate.py
        return wrapper
    return decorator
