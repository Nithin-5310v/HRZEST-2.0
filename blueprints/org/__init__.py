# -*- coding: utf-8 -*-
"""Org blueprint -- multi-tenant org self-registration.

Package layout (split from one module; routes register in this order):
  validation     signup field validation: slugs, company/admin fields, mobile, GST
  provisioning   tenant provisioning and portal-ready / payment-confirmation emails
  duplicates     duplicate company / GST / PAN / CIN / admin-email detection and alerts
  applications   signup application lifecycle: OTP, access tokens, approve/reject, documents
  signup_web     web signup routes: get-started, create_org, OTP, profile, documents, status
  company        company verification, registration checklist, company details
  payment        signup payment and trial setup (Razorpay subscription)
  signup_api     mobile/JSON signup API
  leads          public leads and product feedback
"""
from flask import Blueprint

org_bp = Blueprint("org", __name__)


# Route modules -- importing them registers their routes on the blueprint above.
from . import validation, provisioning, duplicates, applications, signup_web, company, payment, signup_api, leads  # noqa: E402,F401

# Re-exported so `blueprints.org.<name>` keeps resolving for existing callers.
from .validation import (  # noqa: E402,F401
    _EMAIL_RE,
    _GST_RE,
    _MAX_SIGNUP_EMAIL_LEN,
    _MAX_SLUG_LEN,
    _MOBILE_RE,
    _RESERVED_SUBDOMAINS,
    _SUBDOMAIN_RE,
    _clean_gst,
    _clean_mobile,
    _clean_subdomain_slug,
    _slug_for_company,
    _validate_company_profile,
    _validate_gst_number,
    _validate_new_tenant_fields,
    _validate_signup_account,
)
from .provisioning import (  # noqa: E402,F401
    _PAYMENT_OPTIONS,
    provision_tenant,
    send_payment_confirmation_email,
    send_portal_ready_email,
)
from .duplicates import (  # noqa: E402,F401
    SIGNUP_BLOCKED_MSG,
    signup_block_for,
    _COMPANY_SUFFIX_RE,
    _FUZZY_MATCH_THRESHOLD,
    _normalize_company_name,
    _record_duplicate_alert,
    check_duplicate_admin_email,
    check_duplicate_cin,
    check_duplicate_gst,
    check_duplicate_name,
    check_duplicate_pan,
)
from .applications import (  # noqa: E402,F401
    _APPLICATION_DOC_KINDS,
    _GENERIC_DUPLICATE_MSG,
    _OTP_MAX_ATTEMPTS,
    _OTP_TTL_MINUTES,
    _approve_application_reviewed,
    _back_to_signup_form,
    _create_signup_company,
    _fetch_application_row,
    _free_registration_allowed,
    _generate_access_token,
    _generate_otp,
    _load_application,
    _mark_application_otp_verified,
    _provision_application_instantly,
    _reject_application_reviewed,
    _save_application_documents,
    _verify_application_access,
    _verify_application_otp,
    send_application_rejected_email,
    send_org_signup_otp_email,
)
from .signup_web import (  # noqa: E402,F401
    _check_new_signup,
    _load_profile_application,
    _log_in_new_company_admin,
    _render_profile_page,
    _save_company_profile,
    _start_signup_application,
    create_org,
    create_org_page,
    create_org_pending_page,
    create_org_profile,
    create_org_profile_page,
    create_org_resend_otp,
    create_org_status_page,
    create_org_upload_documents,
    create_org_upload_documents_page,
    create_org_verify_otp,
    create_org_verify_otp_page,
    get_started_page,
)
from .company import (  # noqa: E402,F401
    _DOC_STEP_STATUS,
    _load_own_tenant_application,
    _own_tenant,
    api_admin_registration,
    api_admin_registration_company,
    api_admin_registration_documents,
    company_details_page,
    company_details_save,
    company_verification_page,
    company_verification_upload,
    registration_checklist,
    save_company_details,
)
from .payment import (  # noqa: E402,F401
    create_org_pay_page,
    create_org_setup_trial_confirm,
    create_org_setup_trial_create_subscription,
    create_org_setup_trial_page,
)
from .signup_api import (  # noqa: E402,F401
    _api_create_company_and_sign_in,
    _api_start_account,
    api_create_org,
    api_create_org_profile,
    api_create_org_resend_otp,
    api_create_org_status,
    api_create_org_upload_documents,
    api_create_org_verify_otp,
)
from .leads import (  # noqa: E402,F401
    _FEEDBACK_TYPES,
    _enquiry_email,
    _first_name,
    submit_feedback,
    submit_lead,
    superadmin_redirect,
)
