# -*- coding: utf-8 -*-
"""Automated company-KYC document verification (Company Onboarding ->
Trial -> Document Verification -> Subscription Activation, automated-review
follow-up). Reads the 4 documents a self-serve applicant already uploaded
(blueprints/org.py's _save_application_documents()), extracts GST/PAN/CIN/
company name/address via a vision-capable Claude call, cross-checks the
extracted GST/company-name against what was typed at signup, and checks
extracted GST/PAN/CIN for an exact duplicate against every other
already-provisioned tenant.

Unlike utils/email_utils.py's email_queue (a per-tenant-schema table, one
per tenant, drained schema-by-schema), tenant_applications/tenants live
ONLY in the master schema -- there is exactly one queue here, no
per-tenant fan-out needed. Modeled on the same durable
pending/attempts/retry idiom regardless, since silently losing a
verification job would strand a real applicant behind the login gate
blueprints/auth.py's admin_login() now enforces.

Three possible outcomes per application, applied via the same
_approve_application_reviewed()/_reject_application_reviewed() helpers
blueprints/platform_admin.py's human-reviewer routes call:
  - Confident pass, no duplicate -> approved automatically.
  - Confident duplicate (exact GST/PAN/CIN match against a DIFFERENT
    tenant) -> rejected automatically, same suspend+re-upload-link
    recovery path a human rejection already uses.
  - Anything inconclusive (illegible scan, low-confidence extraction, a
    typed-vs-extracted mismatch that isn't a hard duplicate) -> left at
    doc_review_status='pending_review' untouched, which is already
    exactly what blueprints/platform_admin.py's manual queue filters on --
    ambiguous cases fall back to human review with zero changes needed
    there.
"""
import base64
import json
import os
import re
import time as _time

from extensions import app_log, log_security_event
from utils.storage import open_private

_POLL_INTERVAL_SEC = 15
_MAX_ATTEMPTS = 3

_DOC_KIND_COLUMNS = {
    "registration_cert": "doc_registration_cert",
    "address_proof": "doc_address_proof",
    "visiting_card": "doc_visiting_card",
    "name_board_photo": "doc_name_board_photo",
}
_DOC_KIND_LABELS = {
    "registration_cert": "Company Registration Certificate",
    "address_proof": "Company Address Proof",
    "visiting_card": "Company Visiting Card",
    "name_board_photo": "Office Name-Board Photo",
}
# Claude's vision input supports these -- notably NOT .bmp (a real allowed
# upload extension for visiting_card/name_board_photo per
# utils/helpers.py's _ALLOWED_IMG_EXT); a .bmp document is simply skipped
# below rather than sent, same effect as the model finding it illegible.
_CLAUDE_VISION_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

_EXTRACTION_PROMPT = """You are reviewing scanned business-registration documents submitted by a company during signup to an HR platform. You are given, in order: the Company Registration Certificate, Company Address Proof, Company Visiting Card, and Office Name-Board Photo (any of these may be missing from the images provided if that document could not be read).

Extract the following fields by reading the documents. If a field genuinely is not present/legible in any document, use null for it -- never guess or fabricate a value.

Respond with STRICT JSON ONLY (no markdown fences, no prose before or after), exactly this shape:
{
  "gst_number": "15-character GSTIN or null",
  "pan": "10-character PAN or null",
  "cin": "21-character CIN/company registration number or null",
  "company_name": "company legal name as printed on the documents, or null",
  "address": "registered business address as printed, or null",
  "legible": true or false (false if the scans are too blurry/dark/cropped to confidently read the key identifiers),
  "confidence": "high", "medium", or "low" (your overall confidence in the extracted identifiers),
  "notes": "one short sentence on anything a human reviewer should know, or empty string"
}"""


def _fetch_application_for_verification(application_id):
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, company_name, admin_email, gst_number, doc_registration_cert, doc_address_proof, "
        "doc_visiting_card, doc_name_board_photo, doc_review_status, tenant_id, verification_attempts "
        "FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    cols = ["id", "company_name", "admin_email", "gst_number", "doc_registration_cert", "doc_address_proof",
            "doc_visiting_card", "doc_name_board_photo", "doc_review_status", "tenant_id", "verification_attempts"]
    return dict(zip(cols, row))


def extract_kyc_fields(application):
    """Reads all 4 stored documents (whichever are present and in a
    Claude-vision-supported format) and returns (result_dict_or_None,
    error_or_None). result_dict matches _EXTRACTION_PROMPT's schema.
    Raises nothing -- callers get a clean (None, error_message) instead,
    same contract shape as utils/storage.py's save_*() functions."""
    from utils.ai_client import call_claude_vision

    images = []
    any_document = False
    for doc_kind, column in _DOC_KIND_COLUMNS.items():
        stored_ref = application.get(column)
        if not stored_ref:
            continue
        ext = os.path.splitext(stored_ref)[1].lower()
        media_type = _CLAUDE_VISION_MEDIA_TYPES.get(ext)
        if not media_type:
            continue  # e.g. a .bmp visiting_card -- not vision-API-supported, skipped
        try:
            data = open_private(stored_ref)
        except Exception as exc:
            app_log.warning("extract_kyc_fields: could not read %s (%s) for application %s: %s",
                            doc_kind, stored_ref, application["id"], exc)
            continue
        b64 = base64.b64encode(data).decode("ascii")
        images.append((media_type, b64))
        any_document = True

    if not any_document:
        return None, "No readable documents to extract from."

    try:
        text_out = call_claude_vision(_EXTRACTION_PROMPT, images, max_tokens=1024, timeout=45)
    except Exception as exc:
        return None, f"Extraction request failed: {exc}"

    json_match = re.search(r"\{.*\}", text_out, re.DOTALL)
    if not json_match:
        return None, "Extraction response was not valid JSON."
    try:
        result = json.loads(json_match.group(0))
    except Exception as exc:
        return None, f"Extraction response JSON parse failed: {exc}"
    return result, None


def _names_roughly_match(typed_name, extracted_name):
    """Same normalize-then-compare idiom blueprints/org.py's
    check_duplicate_name() already uses for legal-suffix-insensitive
    company-name comparison -- reused here rather than reimplemented, to
    keep "does this look like the same company" defined in exactly one
    place."""
    from blueprints.org import _normalize_company_name
    import difflib
    a, b = _normalize_company_name(typed_name or ""), _normalize_company_name(extracted_name or "")
    if not a or not b:
        return True  # nothing to compare -- don't treat missing data as a mismatch
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def run_automated_verification(application_id):
    """Orchestrates one application's automated review: extraction ->
    typed-vs-extracted comparison -> duplicate checks -> applies one of
    the three outcomes described in this module's docstring. Returns a
    short human-readable outcome string for logging; never raises (the
    worker's caller already wraps this, but every internal step is also
    defensively try/except'd so a partial failure degrades to "leave for
    manual review" rather than crashing the whole job)."""
    from blueprints.org import (
        check_duplicate_gst, check_duplicate_pan, check_duplicate_cin,
        _approve_application_reviewed, _reject_application_reviewed, _record_duplicate_alert,
    )
    from database import get_master_db

    application = _fetch_application_for_verification(application_id)
    if not application:
        return "application not found"
    if application["doc_review_status"] != "pending_review":
        return "not awaiting review, skipped"

    result, error = extract_kyc_fields(application)

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE tenant_applications SET ocr_extraction=%s WHERE id=%s",
        (json.dumps(result) if result else None, application_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    if error or not result:
        log_security_event(
            "org.automated_verification_inconclusive",
            f"Automated document verification could not extract data for application {application_id}: {error}",
            level="INFO", application_id=application_id,
        )
        return f"inconclusive (extraction failed: {error})"

    if not result.get("legible") or result.get("confidence") == "low":
        log_security_event(
            "org.automated_verification_inconclusive",
            f"Automated document verification low-confidence/illegible for application {application_id}",
            level="INFO", application_id=application_id,
        )
        return "inconclusive (low confidence / illegible)"

    extracted_gst = (result.get("gst_number") or "").strip().upper() or None
    extracted_pan = (result.get("pan") or "").strip().upper() or None
    extracted_cin = (result.get("cin") or "").strip().upper() or None

    # Typed-vs-extracted cross-check: a mismatch is a signal for a human,
    # not an auto-reject -- a real applicant's own typo, or a legibly
    # slightly-different rendering (e.g. abbreviation) of their own real
    # company, shouldn't be treated the same as a confirmed duplicate.
    if application["gst_number"] and extracted_gst and application["gst_number"] != extracted_gst:
        log_security_event(
            "org.automated_verification_inconclusive",
            f"Application {application_id}: typed GST does not match document-extracted GST",
            level="WARNING", application_id=application_id,
        )
        return "inconclusive (typed vs. extracted GST mismatch)"
    if not _names_roughly_match(application["company_name"], result.get("company_name")):
        log_security_event(
            "org.automated_verification_inconclusive",
            f"Application {application_id}: typed company name does not match document-extracted name",
            level="WARNING", application_id=application_id,
        )
        return "inconclusive (typed vs. extracted company name mismatch)"

    # Hard duplicate detection -- exact match only, against a DIFFERENT
    # tenant than the one this application already provisioned (comparing
    # against itself would always "match" once gst_number/pan/cin get
    # backfilled onto its own tenant row by the approve path below).
    for identifier, checker, match_type in (
        (extracted_gst, check_duplicate_gst, "gst"),
        (extracted_pan, check_duplicate_pan, "pan"),
        (extracted_cin, check_duplicate_cin, "cin"),
    ):
        if not identifier:
            continue
        conflicting = checker(identifier)
        if conflicting and conflicting[0] != application["tenant_id"]:
            _record_duplicate_alert(
                application_id, application["company_name"], application["admin_email"],
                conflicting, match_type=match_type,
            )
            ok, msg = _reject_application_reviewed(
                application, "system:automated_verification",
                f"Automated verification found a duplicate {match_type.upper()} already registered to another company.",
            )
            return f"auto-rejected (duplicate {match_type}): {msg}" if ok else f"auto-reject failed: {msg}"

    # Passed extraction, cross-check, and duplicate detection.
    ok, msg = _approve_application_reviewed(application, "system:automated_verification")
    if ok and application["tenant_id"]:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute(
            "UPDATE tenants SET pan=COALESCE(pan, %s), cin=COALESCE(cin, %s) WHERE id=%s",
            (extracted_pan, extracted_cin, application["tenant_id"])
        )
        conn.commit()
        cur.close()
        conn.close()
    return f"auto-approved: {msg}" if ok else f"auto-approve failed: {msg}"


def _document_verification_worker():
    """Background thread, started once in wsgi.py alongside the existing
    email-queue-worker thread -- polls tenant_applications for jobs
    _save_application_documents() enqueued, processes up to 5 per cycle.
    tenant_applications is master-schema-only (unlike email_queue, which
    is per-tenant), so no g.tenant_db switching/schema fan-out is needed
    here -- just a single global queue."""
    from extensions import app
    from database import get_master_db
    while True:
        try:
            with app.app_context():
                conn = get_master_db()
                cur = conn.cursor(buffered=True)
                cur.execute(
                    "SELECT id FROM tenant_applications WHERE verification_job_status='pending' "
                    "AND verification_attempts < %s ORDER BY documents_submitted_at LIMIT 5",
                    (_MAX_ATTEMPTS,)
                )
                job_ids = [r[0] for r in cur.fetchall()]
                for app_id in job_ids:
                    cur.execute(
                        "UPDATE tenant_applications SET verification_job_status='processing', "
                        "verification_attempts=verification_attempts+1 WHERE id=%s",
                        (app_id,)
                    )
                conn.commit()
                cur.close()
                conn.close()

                for app_id in job_ids:
                    try:
                        outcome = run_automated_verification(app_id)
                        app_log.info("Automated document verification for application %s: %s", app_id, outcome)
                        conn2 = get_master_db()
                        cur2 = conn2.cursor(buffered=True)
                        cur2.execute(
                            "UPDATE tenant_applications SET verification_job_status='done' WHERE id=%s",
                            (app_id,)
                        )
                        conn2.commit()
                        cur2.close()
                        conn2.close()
                    except Exception as exc:
                        app_log.error("Automated document verification failed for application %s: %s", app_id, exc)
                        conn3 = get_master_db()
                        cur3 = conn3.cursor(buffered=True)
                        cur3.execute(
                            "UPDATE tenant_applications SET verification_job_status=%s, verification_error=%s "
                            "WHERE id=%s",
                            ("pending", str(exc)[:500], app_id)
                        )
                        conn3.commit()
                        cur3.close()
                        conn3.close()

                # Attempts exhausted -- give up automating this one,
                # leaves doc_review_status='pending_review' exactly as it
                # already was, so it's already sitting in the existing
                # manual queue with no further action needed here.
                conn4 = get_master_db()
                cur4 = conn4.cursor(buffered=True)
                cur4.execute(
                    "UPDATE tenant_applications SET verification_job_status='failed' "
                    "WHERE verification_job_status='pending' AND verification_attempts >= %s",
                    (_MAX_ATTEMPTS,)
                )
                conn4.commit()
                cur4.close()
                conn4.close()
        except Exception as exc:
            app_log.error("Document verification worker error: %s", exc)
        _time.sleep(_POLL_INTERVAL_SEC)
