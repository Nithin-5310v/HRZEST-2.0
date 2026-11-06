# -*- coding: utf-8 -*-
"""platform_admin: gated company-signup review queue (approve/reject, documents)"""
import secrets
from database import get_master_db
from extensions import app_log, log_security_event
from utils.auth import _hash_token
from flask import flash, redirect, render_template, request, session
from . import _platform_admin_required, platform_admin_bp


# ── Gated company-signup review queue (blueprints/org.py's tenant_applications) ──
# Every applicant now goes through email OTP + KYC document upload before
# reaching this queue; provision_tenant() is only ever called from the
# approve action below (or, for paid plans, from billing.py's
# verify_payment() after the post-approval payment step completes).

_APPLICATION_DOC_KINDS = ("registration_cert", "address_proof", "visiting_card", "name_board_photo")
# One fixed statement per document column: doc_kind only ever selects among
# these, so nothing from the request is formatted into SQL (QA SEC-12).
_APPLICATION_DOC_SQL = {kind: "SELECT doc_" + kind + " FROM tenant_applications WHERE id=%s"  # nosec B608
                        for kind in _APPLICATION_DOC_KINDS}
_APPLICATION_STATUS_COLS = (
    "id, company_name, subdomain, admin_username, admin_email, email_domain, gst_number, employee_count, "
    "payment_option, logo_path, email_verified_at, doc_registration_cert, doc_address_proof, "
    "doc_visiting_card, doc_name_board_photo, documents_submitted_at, status, reviewed_by, "
    "reviewed_at, rejection_reason, tenant_id, created_at, doc_review_status, admin_mobile"
)
_APPLICATION_COLS = [c.strip() for c in _APPLICATION_STATUS_COLS.split(",")]


def _fetch_application(application_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications WHERE id=%s", (application_id,))  # nosec B608 -- _APPLICATION_STATUS_COLS is a fixed module-level constant, never built from request input  # noqa: E501
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return dict(zip(_APPLICATION_COLS, row))


@platform_admin_bp.route("/super_admin/applications", methods=["GET"])
@_platform_admin_required
def platform_admin_applications_queue():
    # "pending_review" here now means "KYC review outstanding", which since
    # every self-serve application provisions instantly right after OTP
    # (blueprints/org.py's _provision_application_instantly()) covers two
    # shapes: the now-rare legacy row (status='pending_review', no tenant
    # yet) and the common case (status='provisioned', doc_review_status=
    # 'pending_review'). Every other filter value still matches doc_review_status
    # directly, which is what actually tracks review state for a provisioned
    # application.
    status_filter = request.args.get("status", "pending_review")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    if status_filter == "all":
        cur.execute(f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications ORDER BY created_at DESC")  # nosec B608 -- fixed constant, see _fetch_application  # noqa: E501
    elif status_filter == "pending_review":
        cur.execute(
            f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications "  # nosec B608
            "WHERE status='pending_review' OR (status='provisioned' AND doc_review_status='pending_review') "
            "ORDER BY created_at ASC"
        )
    else:
        cur.execute(
            f"SELECT {_APPLICATION_STATUS_COLS} FROM tenant_applications WHERE status=%s ORDER BY created_at ASC",  # nosec B608
            (status_filter,)
        )
    rows = cur.fetchall()
    cur.execute(
        "SELECT COUNT(*) FROM tenant_applications "
        "WHERE status='pending_review' OR (status='provisioned' AND doc_review_status='pending_review')"
    )
    pending_count = cur.fetchone()[0]
    cur.close()
    conn.close()
    applications = [dict(zip(_APPLICATION_COLS, r)) for r in rows]
    return render_template(
        "super_admin_applications.html", applications=applications,
        status_filter=status_filter, pending_count=pending_count,
    )


@platform_admin_bp.route("/super_admin/applications/<int:application_id>", methods=["GET"])
@_platform_admin_required
def platform_admin_application_detail(application_id):
    application = _fetch_application(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/super_admin/applications")
    return render_template("super_admin_application_detail.html", application=application,
                           doc_kinds=_APPLICATION_DOC_KINDS)


@platform_admin_bp.route("/super_admin/applications/<int:application_id>/documents/<doc_kind>", methods=["GET"])
@_platform_admin_required
def platform_admin_view_document(application_id, doc_kind):
    """Streams one uploaded KYC document. This route + the
    @_platform_admin_required guard above is the ENTIRE access control for
    these files -- they're saved outside static/ (see utils/helpers.py's
    save_application_document()) specifically so there is no other way to
    reach them. doc_kind is checked against a fixed whitelist and the
    actual filesystem path/S3 ref always comes from the DB row, never from
    anything client-supplied, so this can't be tricked into serving an
    arbitrary path.

    Reads via utils/storage.py's open_private() rather than send_file(path)
    directly, since the stored ref may be a local path OR an "s3://..."
    reference (whichever save_application_document() actually used) --
    open_private() handles both transparently, this route doesn't need to
    know or care which mode is active."""
    if doc_kind not in _APPLICATION_DOC_KINDS:
        flash("Invalid document type.", "error")
        return redirect(f"/super_admin/applications/{application_id}")
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(_APPLICATION_DOC_SQL[doc_kind], (application_id,))  # nosec B608 -- doc_kind is allowlist-checked against _APPLICATION_DOC_KINDS above, never interpolated from an unchecked value  # noqa: E501
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row[0]:
        flash("Document not found.", "error")
        return redirect(f"/super_admin/applications/{application_id}")
    import io
    import mimetypes
    from flask import send_file
    from utils.storage import open_private
    try:
        data = open_private(row[0])
    except Exception as exc:
        app_log.warning("Could not read application document %s (application_id=%s): %s", row[0], application_id, exc, exc_info=True)
        flash("Could not read this document.", "error")
        return redirect(f"/super_admin/applications/{application_id}")
    mimetype = mimetypes.guess_type(row[0])[0] or "application/octet-stream"
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=False)


@platform_admin_bp.route("/super_admin/applications/<int:application_id>/approve", methods=["POST"])
@_platform_admin_required
def platform_admin_approve_application(application_id):
    from blueprints.org import provision_tenant, check_duplicate_name, check_duplicate_gst, \
        check_duplicate_admin_email, _record_duplicate_alert, send_portal_ready_email, _GENERIC_DUPLICATE_MSG, \
        _approve_application_reviewed

    application = _fetch_application(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/super_admin/applications")

    if application["tenant_id"] is not None:
        # Every self-serve application now provisions instantly right
        # after OTP verification (blueprints/org.py's
        # _provision_application_instantly()) -- by the time it reaches
        # this queue, the tenant has been live for a while already, and
        # utils/document_verification.py's automated worker has usually
        # already resolved most applications one way or the other before a
        # human ever sees this route -- this only fires for whatever the
        # automation left at 'pending_review' (or the rare case a platform
        # admin wants to override before the automated check finishes).
        # _approve_application_reviewed() is the exact same logic the
        # automated worker itself calls, just with reviewed_by set to this
        # platform admin's username instead of the worker's system identity.
        ok, msg = _approve_application_reviewed(application, session.get("platform_admin_username"))
        flash(msg, "success" if ok else "error")
        return redirect("/super_admin/applications")

    if application["status"] != "pending_review":
        flash("This application isn't awaiting review.", "error")
        return redirect("/super_admin/applications")

    # Re-check for a name/GST/email collision -- state may have shifted
    # since this application was submitted (e.g. a different applicant
    # with the same name, GST, or email was approved first).
    conflicting, match_type = check_duplicate_name(application["company_name"])
    if not conflicting:
        conflicting, match_type = check_duplicate_gst(application["gst_number"]), "gst"
    if not conflicting:
        conflicting, match_type = check_duplicate_admin_email(application["admin_email"]), "email"
    if conflicting:
        _record_duplicate_alert(application_id, application["company_name"], application["admin_email"],
                                conflicting, match_type=match_type)
        flash(_GENERIC_DUPLICATE_MSG + " (duplicate detected at approval time)", "error")
        return redirect(f"/super_admin/applications/{application_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    reviewer = session.get("platform_admin_username")

    if application["payment_option"] == "online":
        # Payment happens AFTER approval for paid plans -- provisioning is
        # deferred until blueprints/billing.py's verify_payment() confirms
        # the charge (see create_order()'s new application_id requirement).
        # Rotate the application's access token (same one-live-token model
        # as the rejection email below) so the emailed pay link can carry
        # it -- /create_org/pay/<id> now requires it (blueprints/org.py).
        access_token = secrets.token_urlsafe(32)
        cur.execute(
            "UPDATE tenant_applications SET status='approved_pending_payment', reviewed_by=%s, reviewed_at=NOW(), "
            "access_token_hash=%s, updated_at=NOW() WHERE id=%s",
            (reviewer, _hash_token(access_token), application_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        from utils.email_utils import get_email_config, send_email_async
        pay_url = f"{request.url_root.rstrip('/')}/create_org/pay/{application_id}?token={access_token}"
        email_cfg = get_email_config()
        if email_cfg:
            send_email_async(
                application["admin_email"], f"Your application was approved -- complete payment for {application['company_name']}",
                f"<p>Your company registration for <strong>{application['company_name']}</strong> has been approved.</p>"
                f"<p><a href=\"{pay_url}\">Complete payment to activate your portal</a></p>",
                email_cfg,
            )
        log_security_event("platform_admin.application_approved", f"Application {application_id} approved (awaiting payment)",
                           level="INFO", identifier=reviewer, application_id=application_id)
        flash("Application approved. The applicant has been emailed a private payment link.", "success")
        return redirect("/super_admin/applications")

    if application["payment_option"] == "trial":
        # Same deferred-provisioning shape as the online branch above --
        # provisioning only starts once the applicant authorizes a payment
        # mandate via blueprints/org.py's create_org_setup_trial_confirm(),
        # not at approval time. The trial clock itself starts even later,
        # at the tenant's first login (app.py's inject_billing_lock_status()).
        # Reuses the
        # 'approved_pending_payment' status (same meaning here: "approved,
        # one external step still outstanding") rather than adding a
        # parallel status string every status-aware view would also need
        # to learn -- create_org_status.html branches on payment_option to
        # show trial-appropriate copy for it.
        access_token = secrets.token_urlsafe(32)
        cur.execute(
            "UPDATE tenant_applications SET status='approved_pending_payment', reviewed_by=%s, reviewed_at=NOW(), "
            "access_token_hash=%s, updated_at=NOW() WHERE id=%s",
            (reviewer, _hash_token(access_token), application_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        from utils.email_utils import get_email_config, send_email_async
        setup_url = f"{request.url_root.rstrip('/')}/create_org/setup_trial/{application_id}?token={access_token}"
        email_cfg = get_email_config()
        if email_cfg:
            from blueprints.trial_billing import TRIAL_DURATION_DAYS
            send_email_async(
                application["admin_email"],
                f"Your application was approved -- set up your {TRIAL_DURATION_DAYS}-day free trial for {application['company_name']}",
                f"<p>Your company registration for <strong>{application['company_name']}</strong> has been approved.</p>"
                f"<p><a href=\"{setup_url}\">Set up your free trial</a> -- you'll authorize a payment method now, "
                f"and your {TRIAL_DURATION_DAYS}-day trial starts the first time you log in. You can cancel anytime "
                f"before it ends with no charge.</p>",
                email_cfg,
            )
        log_security_event("platform_admin.application_approved", f"Application {application_id} approved (awaiting trial mandate setup)",
                           level="INFO", identifier=reviewer, application_id=application_id)
        flash("Application approved. The applicant has been emailed a private trial setup link.", "success")
        return redirect("/super_admin/applications")

    cur.execute("SELECT admin_password_hash FROM tenant_applications WHERE id=%s", (application_id,))
    admin_password_hash = cur.fetchone()[0]
    cur.close()
    conn.close()

    ok, error, portal_url, checkin_url = provision_tenant(
        application["company_name"], application["subdomain"], application["admin_username"], admin_password_hash,
        application["admin_email"], payment_option=application["payment_option"],
        email_domain=application["email_domain"], employee_count=application["employee_count"],
        logo_path=application["logo_path"], gst_number=application["gst_number"],
    )
    if not ok:
        flash(f"Approval failed: {error}", "error")
        return redirect(f"/super_admin/applications/{application_id}")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT id FROM tenants WHERE subdomain=%s", (application["subdomain"],))
    tenant_id = cur.fetchone()[0]
    cur.execute(
        "UPDATE tenant_applications SET status='provisioned', reviewed_by=%s, reviewed_at=NOW(), tenant_id=%s, "
        "updated_at=NOW() WHERE id=%s",
        (reviewer, tenant_id, application_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    send_portal_ready_email(application["admin_email"], application["company_name"], application["admin_username"],
                            portal_url, checkin_url=checkin_url)
    log_security_event("platform_admin.application_approved", f"Application {application_id} approved and provisioned",
                       level="INFO", identifier=reviewer, application_id=application_id, tenant_id=tenant_id)
    flash(f"Company '{application['company_name']}' approved and provisioned. Portal: {portal_url}", "success")
    return redirect("/super_admin/applications")


@platform_admin_bp.route("/super_admin/applications/<int:application_id>/reject", methods=["POST"])
@_platform_admin_required
def platform_admin_reject_application(application_id):
    from blueprints.org import send_application_rejected_email, _reject_application_reviewed
    from utils.auth import _hash_token
    from utils.helpers import _safe_app_url
    reason = request.form.get("reason", "").strip()[:1000]
    application = _fetch_application(application_id)
    if not application:
        flash("Application not found.", "error")
        return redirect("/super_admin/applications")

    reviewer = session.get("platform_admin_username")

    if application["tenant_id"] is not None:
        # Already provisioned -- reuse the exact same suspend+re-upload-link
        # logic utils/document_verification.py's automated worker itself
        # calls for a confident duplicate match, just with reviewed_by set
        # to this platform admin instead of the worker's system identity.
        ok, msg = _reject_application_reviewed(application, reviewer, reason)
        flash(msg, "success" if ok else "error")
        return redirect("/super_admin/applications")

    # A fresh access_token is issued here specifically so the rejection
    # email can carry a working re-upload link -- the original raw token
    # (from application submission) was never persisted anywhere except
    # that first session, which is very often long gone by the time
    # someone acts on a rejection email days later. Rotating it here is
    # safe: the old token stops working the moment this UPDATE lands,
    # same one-token-live-at-a-time model as every other flow that reuses
    # access_token_hash.
    new_token = secrets.token_urlsafe(32)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE tenant_applications SET status='rejected', doc_review_status='rejected', reviewed_by=%s, "
        "reviewed_at=NOW(), rejection_reason=%s, access_token_hash=%s, updated_at=NOW() WHERE id=%s",
        (reviewer, reason, _hash_token(new_token), application_id)
    )
    conn.commit()
    cur.close()
    conn.close()

    status_url = f"{_safe_app_url()}/create_org/status/{application_id}?token={new_token}"
    send_application_rejected_email(application["admin_email"], application["company_name"], reason, status_url)
    log_security_event("platform_admin.application_rejected", f"Application {application_id} rejected",
                       level="INFO", identifier=reviewer, application_id=application_id, reason=reason)
    flash("Application rejected.", "success")
    return redirect("/super_admin/applications")
