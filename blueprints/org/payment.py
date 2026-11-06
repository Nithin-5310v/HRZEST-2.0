# -*- coding: utf-8 -*-
"""org: signup payment and trial setup (Razorpay subscription)"""
import secrets
from extensions import app_log, limiter, log_security_event
from flask import abort, flash, jsonify, redirect, render_template, request
from utils.helpers import _safe_app_url
from utils.plan_limits import get_per_employee_paise
from . import org_bp
from .applications import _verify_application_access
from .provisioning import provision_tenant, send_portal_ready_email


@org_bp.route("/create_org/pay/<int:application_id>", methods=["GET"])
def create_org_pay_page(application_id):
    """Landing page linked from the approval email for online-payment
    applications (blueprints/platform_admin.py's approve action, once
    payment_option=='online') -- pre-filled/locked to this one approved
    application rather than an editable signup form, since every other
    field was already collected and reviewed. Requires the application's
    access token (from the emailed link, or the signup session) so a
    sequential application_id alone reveals nothing; 404 otherwise."""
    if not _verify_application_access(application_id):
        abort(404)
    if request.args.get("token"):
        return redirect(f"/create_org/pay/{application_id}")  # drop the token from the address bar
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("SELECT company_name, status FROM tenant_applications WHERE id=%s", (application_id,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        flash("Application not found.", "error")
        return redirect("/create_org")
    company_name, status = row
    if status != "approved_pending_payment":
        return redirect(f"/create_org/status/{application_id}")
    return render_template(
        "create_org_pay.html", application_id=application_id, company_name=company_name,
        per_employee_paise=get_per_employee_paise(),
    )


@org_bp.route("/create_org/setup_trial/<int:application_id>", methods=["GET"])
def create_org_setup_trial_page(application_id):
    """Landing page linked from the approval email for trial applications
    (blueprints/platform_admin.py's approve action, once payment_option==
    'trial') -- same shape as create_org_pay_page above, just collecting a
    payment MANDATE (authorized now, not charged) instead of an immediate
    payment. Provisioning happens once create_org_setup_trial_confirm()
    below verifies the authorization -- the trial clock itself only starts
    later, at first login (see app.py's inject_billing_lock_status()).
    Same access-token requirement as create_org_pay_page()."""
    if not _verify_application_access(application_id):
        abort(404)
    if request.args.get("token"):
        return redirect(f"/create_org/setup_trial/{application_id}")
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "SELECT company_name, status, payment_option FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        flash("Application not found.", "error")
        return redirect("/create_org")
    company_name, status, payment_option = row
    if payment_option != "trial" or status != "approved_pending_payment":
        return redirect(f"/create_org/status/{application_id}")
    return render_template(
        "create_org_setup_trial.html", application_id=application_id, company_name=company_name,
        per_employee_paise=get_per_employee_paise(),
    )


@org_bp.route("/api/create_org/setup_trial/<int:application_id>/create_subscription", methods=["POST"])
@limiter.limit("10 per minute")
def create_org_setup_trial_create_subscription(application_id):
    """Authorizes (but does not charge) a Razorpay Subscription for a
    trial applicant -- same create_id_or_demo/create_customer/
    create_subscription calls blueprints/auto_debit.py's enroll() makes for
    an already-provisioned tenant, reused here (via that module's plan-id
    cache) before the tenant schema even exists yet. Quantity starts at 1
    as a placeholder -- there are no employees yet, and the recurring daily
    sync (blueprints/auto_debit.py's sync_and_bill_auto_debit(), or the
    one-off sync in trial_billing.py's check_trial_expirations()) corrects
    it to the real headcount well before the trial ends and any charge
    happens."""
    if not _verify_application_access(application_id):
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    from database import get_master_db
    from blueprints.auto_debit import _get_or_create_plan_id, _DEMO_SUBSCRIPTION_PREFIX
    from utils.razorpay_utils import create_customer, create_subscription, create_id_or_demo, key_id as razorpay_key_id

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute(
        "SELECT company_name, admin_email, status, payment_option FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    company_name, admin_email, status, payment_option = row
    if payment_option != "trial" or status != "approved_pending_payment":
        return jsonify({"ok": False, "msg": "This application isn't ready for trial setup."}), 400

    def _create_real_subscription():
        plan_id, error = _get_or_create_plan_id()
        if error:
            return None, error
        customer_id, error = create_customer(company_name, admin_email)
        if error:
            return None, error
        return create_subscription(plan_id, customer_id, 1)

    subscription_id, is_demo, error = create_id_or_demo(_DEMO_SUBSCRIPTION_PREFIX, _create_real_subscription)
    if error:
        app_log.error("create_org_setup_trial_create_subscription: subscription setup failed: %s", error)
        return jsonify({"ok": False, "msg": "Could not start trial setup. Please try again."}), 502

    # Bind this exact subscription to this application: the confirm step
    # below only accepts the id recorded here, so a validly-signed
    # authorization for some OTHER subscription can't provision it.
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute(
        "UPDATE tenant_applications SET pending_subscription_id=%s, updated_at=NOW() "
        "WHERE id=%s AND status='approved_pending_payment'",
        (subscription_id, application_id)
    )
    mconn.commit()
    mcur.close()
    mconn.close()

    return jsonify({
        "ok": True, "subscription_id": subscription_id, "key_id": razorpay_key_id(), "demo": is_demo,
    })


@org_bp.route("/api/create_org/setup_trial/<int:application_id>/confirm", methods=["POST"])
@limiter.limit("20 per minute")
def create_org_setup_trial_confirm(application_id):
    """Verifies the subscription-authorization callback (same signature
    check blueprints/auto_debit.py's confirm() uses for an existing
    tenant), then provisions the tenant -- provision_tenant() itself sets
    subscription_status='trialing' for payment_option=='trial' (dates left
    NULL until first login). The authorized mandate is armed and waiting
    (auto_debit_mandates.status='active') but nothing is charged until
    blueprints/trial_billing.py's check_trial_expirations() runs, which
    only ever fires once trial_end_date is actually set and past."""
    from database import get_master_db
    from blueprints.auto_debit import _DEMO_SUBSCRIPTION_PREFIX
    from utils.razorpay_utils import verify_subscription_signature, verify_or_demo

    data = request.get_json(silent=True) or request.form
    subscription_id = (data.get("razorpay_subscription_id") or "").strip()
    payment_id = (data.get("razorpay_payment_id") or "").strip()
    signature = (data.get("razorpay_signature") or "").strip()
    if not subscription_id:
        return jsonify({"ok": False, "msg": "Missing subscription id."}), 400

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute(
        "SELECT company_name, subdomain, admin_username, admin_email, admin_password_hash, email_domain, "
        "logo_path, status, payment_option, gst_number, pending_subscription_id "
        "FROM tenant_applications WHERE id=%s",
        (application_id,)
    )
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    if not row:
        return jsonify({"ok": False, "msg": "Application not found."}), 404
    (company_name, subdomain, admin_username, admin_email, admin_password_hash, email_domain,
     logo_path, status, payment_option, gst_number, pending_subscription_id) = row

    # Idempotent: a retried/duplicate client POST for an already-provisioned
    # application must not attempt to re-provision.
    if status == "provisioned":
        portal_url = f"{_safe_app_url()}/{subdomain}/login"
        return jsonify({"ok": True, "portal_url": portal_url, "already_provisioned": True})
    if payment_option != "trial" or status != "approved_pending_payment":
        return jsonify({"ok": False, "msg": "This application isn't ready for trial setup."}), 400
    if not pending_subscription_id or not secrets.compare_digest(
            str(pending_subscription_id), subscription_id):
        log_security_event(
            "org.trial_subscription_mismatch",
            "Trial confirm presented a subscription that was not created for this application",
            level="ERROR", application_id=application_id,
        )
        return jsonify({"ok": False, "msg": "Payment verification failed."}), 400

    ok, is_demo, error = verify_or_demo(
        subscription_id, _DEMO_SUBSCRIPTION_PREFIX, payment_id, signature, verify_subscription_signature
    )
    if not ok:
        if not is_demo:
            log_security_event(
                "org.trial_signature_invalid", "Razorpay subscription-authorization signature verification failed",
                level="ERROR", subscription_id=subscription_id, application_id=application_id,
            )
        return jsonify({"ok": False, "msg": error}), 400

    ok, error, portal_url, checkin_url = provision_tenant(
        company_name, subdomain, admin_username, admin_password_hash, admin_email,
        payment_option="trial", email_domain=email_domain, logo_path=logo_path, gst_number=gst_number,
    )
    if not ok:
        app_log.error("create_org_setup_trial_confirm: provisioning failed for application %s: %s", application_id, error)
        return jsonify({"ok": False, "msg": f"Trial setup failed: {error}. Contact support."}), 500

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT id, db_name FROM tenants WHERE subdomain=%s", (subdomain,))
    tenant_row = mcur.fetchone()
    tenant_id, tenant_db_name = tenant_row if tenant_row else (None, None)
    mcur.execute(
        "UPDATE tenant_applications SET status='provisioned', tenant_id=%s, updated_at=NOW() WHERE id=%s",
        (tenant_id, application_id)
    )
    # Mandate is authorized and ready, but the tenant has no employees yet
    # (schema was just created) -- quantity_synced starts at 0 so the very
    # first daily sync (or trial_billing.py's own one-off sync at trial end)
    # PATCHes it to whatever headcount actually exists by then.
    if tenant_db_name:
        mcur.execute(
            "INSERT INTO auto_debit_mandates (tenant_schema, company_name, razorpay_customer_id, "
            "razorpay_subscription_id, quantity_synced, status, requested_by, created_at, activated_at) "
            "VALUES (%s, %s, NULL, %s, 0, 'active', %s, NOW(), NOW()) "
            "ON CONFLICT (tenant_schema) DO UPDATE SET razorpay_subscription_id=%s, status='active', "
            "activated_at=NOW(), cancelled_at=NULL",
            (tenant_db_name, company_name, subscription_id, admin_username, subscription_id)
        )
    mconn.commit()
    mcur.close()
    mconn.close()

    if is_demo:
        log_security_event(
            "org.trial_demo_enrolled", "Demo/test-mode trial mandate authorized (no real mandate)",
            level="INFO", subscription_id=subscription_id, application_id=application_id,
        )
    log_security_event(
        "org.trial_tenant_provisioned", f"Trial tenant '{company_name}' provisioned, trial clock starts at first login",
        level="INFO", subdomain=subdomain, application_id=application_id, tenant_id=tenant_id,
    )
    send_portal_ready_email(admin_email, company_name, admin_username, portal_url, checkin_url=checkin_url)

    return jsonify({"ok": True, "portal_url": portal_url})
