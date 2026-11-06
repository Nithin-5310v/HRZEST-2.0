# -*- coding: utf-8 -*-
"""org: duplicate company / GST / PAN / CIN / admin-email detection and alerts"""
import difflib
import re
from extensions import app_log, log_security_event
from flask import request


_COMPANY_SUFFIX_RE = re.compile(
    r'\b(private\s+limited|pvt\.?\s*ltd\.?|public\s+limited|limited|ltd\.?|llp|inc\.?|incorporated|'
    r'corp\.?|corporation|co\.?|company|llc)\b'
)
# A pure-normalization-based match still catches most real re-registration
# attempts (same legal name, different casing/punctuation/legal suffix)
# with zero false-positive risk. A second, SequenceMatcher-based pass below
# catches near-identical names a registrant deliberately tweaks (a typo, a
# dropped/added plural) -- no Postgres extension (pg_trgm) required, so
# this works identically in every deployment without needing CREATE
# EXTENSION privileges.
#
# Deliberately high (0.95, not the more obvious ~0.85): this blocks a real
# SIGNUP outright, not just a soft admin-review flag, so a false positive
# means refusing a legitimate, genuinely-different business. Two real
# companies that happen to share most of their name (regional arms, near-
# namesakes -- "Acme Logistics" vs "Acme Logistics West") are far more
# common than this threshold's headroom suggests: at a looser threshold,
# a single differing word/character in an otherwise-templated name (e.g.
# "Northgate Retail A" vs "Northgate Retail B") already scores ~0.92 and
# would be wrongly blocked. 0.95 still reliably catches the actual abuse
# case this exists for -- a registrant re-submitting the near-identical
# name with a cosmetic tweak (a typo, a dropped/added "s") -- which scores
# ~0.97+ in practice.
_FUZZY_MATCH_THRESHOLD = 0.95


def _normalize_company_name(name):
    """Strips legal-entity suffixes and punctuation so "Acme Pvt Ltd" and
    "Acme Private Limited" collapse to the same core string before either
    the exact or the fuzzy comparison in check_duplicate_name() runs."""
    s = re.sub(r'\s+', ' ', (name or '').strip()).lower()
    s = _COMPANY_SUFFIX_RE.sub('', s)
    s = re.sub(r'[^a-z0-9 ]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def check_duplicate_name(company_name):
    """Looks for an existing, already-provisioned tenant whose name is the
    same business as `company_name` -- either an exact (post-normalization)
    match, or a fuzzy one close enough that it's almost certainly the same
    registrant trying again under a slightly different name (e.g. "Acme
    Pvt Ltd" vs "Acme India"). Returns ((tenant_id, company_name,
    admin_email), match_type) if found, else (None, None).

    Deliberately NOT folded into _validate_new_tenant_fields(), which the
    mobile signup API shares -- only the gated step-1 signup route calls
    this (including a platform admin's free registration)."""
    normalized = _normalize_company_name(company_name)
    if not normalized:
        return None, None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants")
    rows = mcur.fetchall()
    mcur.close()
    mconn.close()

    best_row, best_ratio = None, 0.0
    for row in rows:
        existing_normalized = _normalize_company_name(row[1])
        if not existing_normalized:
            continue
        if existing_normalized == normalized:
            return row, "exact"
        ratio = difflib.SequenceMatcher(None, normalized, existing_normalized).ratio()
        if ratio > best_ratio:
            best_row, best_ratio = row, ratio
    if best_row is not None and best_ratio >= _FUZZY_MATCH_THRESHOLD:
        return best_row, "fuzzy"
    return None, None


def check_duplicate_gst(gst_number):
    """Exact match against every already-provisioned tenant's GSTIN.
    Catches the case check_duplicate_name() can't: the same real business
    registering again under a genuinely different company name after a
    prior trial locked/expired. Returns (tenant_id, company_name,
    admin_email) if found, else None."""
    if not gst_number:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE gst_number=%s", (gst_number,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def check_duplicate_pan(pan):
    """Exact match against every already-provisioned tenant's PAN --
    same purpose as check_duplicate_gst() above, but PAN is never typed at
    signup (only GST is); this is populated purely from OCR-extracted
    document content (utils/document_verification.py) and only ever
    compared against other tenants' OCR-extracted PANs, never against
    anything a registrant typed. Returns (tenant_id, company_name,
    admin_email) if found, else None."""
    if not pan:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE pan=%s", (pan,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def check_duplicate_cin(cin):
    """Exact match against every already-provisioned tenant's CIN --
    same shape/purpose as check_duplicate_pan() above."""
    if not cin:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE cin=%s", (cin,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def check_duplicate_admin_email(admin_email):
    """Exact (case-insensitive) match against every already-provisioned
    tenant's admin_email. A trial-abuse registrant reusing their own email
    under a new company name/GST is still caught here even if the other
    two checks somehow miss it. Returns (tenant_id, company_name,
    admin_email) if found, else None."""
    normalized = (admin_email or '').strip().lower()
    if not normalized:
        return None
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT id, company_name, admin_email FROM tenants WHERE LOWER(admin_email)=%s", (normalized,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def _record_duplicate_alert(application_id, attempted_company_name, attempted_admin_email, conflicting,
                            match_type="exact"):
    """Internal-only record of a signup blocked by check_duplicate_name()/
    check_duplicate_gst()/check_duplicate_admin_email() (match_type
    identifies which one fired: 'exact'/'fuzzy' for a company-name match,
    'gst' or 'email' for the other two). The registrant never sees any of
    `conflicting`'s contents -- only the platform-admin duplicate-alerts
    screen (blueprints/platform_admin.py) ever reads this table.
    Best-effort: a logging failure must not be able to block (or un-block)
    the signup rejection itself."""
    conflicting_id, conflicting_name, conflicting_email = conflicting
    try:
        from database import get_master_db
        mconn = get_master_db()
        mcur = mconn.cursor()
        mcur.execute(
            "INSERT INTO tenant_duplicate_alerts "
            "(application_id, attempted_company_name, attempted_admin_email, conflicting_tenant_id, "
            "conflicting_company_name, conflicting_admin_email, match_type, source_ip) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (application_id, attempted_company_name, attempted_admin_email, conflicting_id, conflicting_name,
             conflicting_email, match_type, request.remote_addr)
        )
        mconn.commit()
        mcur.close()
        mconn.close()
    except Exception as exc:
        app_log.error("_record_duplicate_alert failed: %s", exc)
    log_security_event(
        "org.duplicate_company_blocked",
        f"Signup blocked: {match_type} match against an existing tenant",
        level="WARNING", attempted_company_name=attempted_company_name,
        conflicting_tenant_id=conflicting_id,
    )
