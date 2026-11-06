# -*- coding: utf-8 -*-
"""org: company verification, registration checklist, company details"""
from extensions import log_security_event
from flask import flash, g, jsonify, redirect, render_template, request
from utils.auth import admin_required, api_required, api_role_required
from utils.helpers import _safe_app_url, tpath
from . import org_bp
from .applications import _APPLICATION_DOC_KINDS, _GENERIC_DUPLICATE_MSG, _save_application_documents
from .duplicates import _record_duplicate_alert, check_duplicate_gst
from .validation import _clean_gst, _validate_gst_number


def _load_own_tenant_application():
    """Looks up the logged-in admin's own tenant's tenant_applications row
    by session identity (g.tenant_db) rather than a token -- unlike
    _load_application() above, which is for the pre-login/token-based
    magic-link flow. Used by /settings/company_verification below, the
    in-portal counterpart that exists because provisioning no longer waits
    on KYC review: an admin can be fully logged in with their documents
    still unreviewed and needs a way back to that step that doesn't depend
    on finding the original signup email. Returns None if none found
    (shouldn't happen for a real tenant, but fails soft rather than 500ing
    a settings page over it)."""
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute(
        "SELECT ta.id, ta.company_name, ta.subdomain, ta.admin_username, ta.admin_email, ta.admin_password_hash, "
        "ta.email_domain, ta.employee_count, ta.payment_option, ta.logo_path, ta.access_token_hash, ta.otp_code_hash, "
        "ta.otp_expires_at, ta.otp_attempts, ta.email_verified_at, ta.doc_registration_cert, ta.doc_address_proof, "
        "ta.doc_visiting_card, ta.doc_name_board_photo, ta.documents_submitted_at, ta.status, ta.reviewed_by, "
        "ta.reviewed_at, ta.rejection_reason, ta.tenant_id, ta.doc_review_status, "
        "t.created_at, t.gst_number, t.pan, t.cin "
        "FROM tenant_applications ta JOIN tenants t ON ta.tenant_id=t.id "
        "WHERE t.db_name=%s ORDER BY ta.id DESC LIMIT 1",
        (g.tenant_db,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return None
    cols = ["id", "company_name", "subdomain", "admin_username", "admin_email", "admin_password_hash",
            "email_domain", "employee_count", "payment_option", "logo_path", "access_token_hash", "otp_code_hash",
            "otp_expires_at", "otp_attempts", "email_verified_at", "doc_registration_cert", "doc_address_proof",
            "doc_visiting_card", "doc_name_board_photo", "documents_submitted_at", "status", "reviewed_by",
            "reviewed_at", "rejection_reason", "tenant_id", "doc_review_status",
            # Added for the Profile page's Company Info tab (account creation
            # date + KYC dedup identifiers) -- from `tenants`, not
            # `tenant_applications`, since that's where they're backfilled to.
            "tenant_created_at", "gst_number", "pan", "cin"]
    return dict(zip(cols, row))


@org_bp.route("/settings/company_verification", methods=["GET"])
@admin_required
def company_verification_page():
    application = _load_own_tenant_application()
    if not application:
        flash("No company verification record found.", "error")
        return redirect(tpath("/admin"))
    return render_template(
        "company_verification.html", application=application, doc_kinds=_APPLICATION_DOC_KINDS,
    )


@org_bp.route("/settings/company_verification", methods=["POST"])
@admin_required
def company_verification_upload():
    application = _load_own_tenant_application()
    if not application:
        flash("No company verification record found.", "error")
        return redirect(tpath("/admin"))

    ok, error = _save_application_documents(application, request.files)
    if not ok:
        flash(error, "error")
    else:
        flash("Documents submitted -- your account is fully active again.", "success")
    return redirect(tpath("/settings/company_verification"))


_DOC_STEP_STATUS = {"approved": "done", "pending_review": "review", "rejected": "rejected"}


def registration_checklist(tenant_db):
    """What a company still has to do after registering -- signup itself
    only asks for its name and the admin's account, so these come from the
    dashboard: a list of {"key", "status"} for "company" (GSTIN, via
    save_company_details), "documents" (_save_application_documents) and
    "payment" (auto-debit, Settings > Billing). status is "done" or "todo";
    documents can also be "review" (submitted, awaiting review -- nothing
    for the company to do) or "rejected" (with "reason"). Payment is left
    out for a platform admin's free registration, billed by manual invoice,
    and while online payments are switched off (there's nothing to set up).

    None for a company with no application at all (Platform Admin's direct
    create tool, or registered before company verification existed):
    nothing to finish. Shared by admin_base.html's checklist (app.py's
    inject_registration_checklist) and the app's /api/admin/registration."""
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    try:
        mcur.execute(
            "SELECT ta.doc_review_status, ta.rejection_reason, ta.free_registration, t.gst_number, "
            "t.subscription_status FROM tenant_applications ta JOIN tenants t ON ta.tenant_id=t.id "
            "WHERE t.db_name=%s ORDER BY ta.id DESC LIMIT 1",
            (tenant_db,)
        )
        row = mcur.fetchone()
        if not row:
            return None
        doc_status, rejection_reason, free_registration, gst_number, subscription_status = row
        mandate_status = None
        if not free_registration:
            mcur.execute("SELECT status FROM auto_debit_mandates WHERE tenant_schema=%s", (tenant_db,))
            mandate = mcur.fetchone()
            mandate_status = mandate[0] if mandate else None
    finally:
        mcur.close()
        mconn.close()

    doc_step_status = _DOC_STEP_STATUS.get(doc_status, "todo")
    steps = [
        {"key": "company", "status": "done" if gst_number else "todo"},
        {"key": "documents", "status": doc_step_status,
         "reason": rejection_reason if doc_step_status == "rejected" else None},
    ]
    from utils.razorpay_utils import payments_enabled
    if not free_registration and payments_enabled():
        paid = mandate_status in ("active", "pending_cancellation") or subscription_status == "active"
        steps.append({"key": "payment", "status": "done" if paid else "todo"})
    return steps


def _own_tenant():
    """The logged-in company's own tenants row: (id, company_name,
    subdomain, gst_number), or None."""
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT id, company_name, subdomain, gst_number FROM tenants WHERE db_name=%s", (g.tenant_db,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    return row


def save_company_details(tenant, fields, logo_file):
    """The registration checklist's "Company details" step (web
    /settings/company_details, the app's /api/admin/registration/company):
    the GSTIN -- required, and must not already belong to another company
    (check_duplicate_gst) -- and an optional logo. The GSTIN goes on the
    tenants row, where duplicate checks read it, and on the company's
    application; the logo becomes company_settings.logo_url. `tenant` is
    _own_tenant()'s row. Returns an error message, or None."""
    from database import get_master_db, get_db_connection
    from utils.helpers import save_uploaded_logo, invalidate_settings_cache

    tenant_id, company_name, slug, _current_gst = tenant
    gst_number = _clean_gst(fields.get("gst_number") or "")
    error = _validate_gst_number(gst_number)
    if error:
        return error
    conflicting = check_duplicate_gst(gst_number)
    if conflicting and conflicting[0] != tenant_id:
        application = _load_own_tenant_application()
        _record_duplicate_alert(application["id"] if application else None, company_name,
                                application["admin_email"] if application else "", conflicting, match_type="gst")
        return _GENERIC_DUPLICATE_MSG

    logo_url = None
    if logo_file and logo_file.filename:
        logo_path, logo_err = save_uploaded_logo(logo_file, slug)
        if logo_err:
            return f"Company logo: {logo_err}"
        logo_url = f"/static/{logo_path}"

    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("UPDATE tenants SET gst_number=%s WHERE id=%s", (gst_number, tenant_id))
    mcur.execute("UPDATE tenant_applications SET gst_number=%s, updated_at=NOW() WHERE tenant_id=%s",
                 (gst_number, tenant_id))
    mconn.commit()
    mcur.close()
    mconn.close()
    if logo_url:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE company_settings SET logo_url=%s WHERE id=1", (logo_url,))
        conn.commit()
        cur.close()
        conn.close()
        invalidate_settings_cache()
    log_security_event("org.company_details_saved", f"Company details saved for '{company_name}'",
                       level="INFO", subdomain=slug, tenant_id=tenant_id, logo_changed=bool(logo_url))
    return None


@org_bp.route("/api/admin/registration", methods=["GET"])
@api_required
@api_role_required("admin")
def api_admin_registration():
    """The app's registration checklist (registration_checklist()), for the
    company its /<company_slug>/ prefix resolved to. steps is empty once
    there is nothing left to finish."""
    tenant = _own_tenant()
    if not tenant:
        return jsonify({"ok": False, "msg": "No company record found."}), 404
    steps = registration_checklist(g.tenant_db) or []
    return jsonify({
        "ok": True, "company_name": tenant[1], "company_slug": tenant[2], "gst_number": tenant[3] or "",
        "portal_url": f"{_safe_app_url()}/{tenant[2]}/login",
        "steps": [] if all(step["status"] == "done" for step in steps) else steps,
    })


@org_bp.route("/api/admin/registration/company", methods=["POST"])
@api_required
@api_role_required("admin")
def api_admin_registration_company():
    """The checklist's company details from the app: gst_number, as JSON
    or as multipart fields when the optional logo comes along."""
    tenant = _own_tenant()
    if not tenant:
        return jsonify({"ok": False, "msg": "No company record found."}), 404
    data = request.form if request.form else (request.get_json(silent=True) or {})
    error = save_company_details(tenant, data, request.files.get("logo"))
    if error:
        return jsonify({"ok": False, "msg": error}), 400
    return jsonify({"ok": True, "msg": "Company details saved."})


@org_bp.route("/api/admin/registration/documents", methods=["POST"])
@api_required
@api_role_required("admin")
def api_admin_registration_documents():
    """The checklist's business documents from the app (multipart, the same
    four files as /settings/company_verification)."""
    application = _load_own_tenant_application()
    if not application:
        return jsonify({"ok": False, "msg": "No company verification record found."}), 404
    ok, error = _save_application_documents(application, request.files)
    if not ok:
        return jsonify({"ok": False, "msg": error}), 400
    return jsonify({"ok": True, "msg": "Documents submitted -- we'll review them shortly."})


@org_bp.route("/settings/company_details", methods=["GET"])
@admin_required
def company_details_page():
    tenant = _own_tenant()
    if not tenant:
        flash("No company record found.", "error")
        return redirect(tpath("/admin"))
    return render_template("company_details.html", gst_number=tenant[3] or "",
                           portal_url=f"{_safe_app_url()}/{tenant[2]}/login")


@org_bp.route("/settings/company_details", methods=["POST"])
@admin_required
def company_details_save():
    tenant = _own_tenant()
    if not tenant:
        flash("No company record found.", "error")
        return redirect(tpath("/admin"))
    error = save_company_details(tenant, request.form, request.files.get("logo"))
    if error:
        flash(error, "error")
        return redirect(tpath("/settings/company_details"))
    flash("Company details saved.", "success")
    return redirect(tpath("/profile"))
