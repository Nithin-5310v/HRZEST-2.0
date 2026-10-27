"""CI guard: every registered route must either carry one of the auth
decorators from utils/auth.py (or the platform-admin guard) -- each tags its
wrapper with `_hrz_auth_guard`, which functools.wraps carries outward through
limiter/other wrappers -- or be listed below with the reason it is public or
authenticates itself inline.

Adding a route? Put a real decorator on it. Only add it here if it is
genuinely public (login pages, webhooks verified by signature, health
checks, ...) or does its own session-OR-Bearer check, and say which.
"""
from extensions import app as flask_app


# endpoint -> why it has no auth decorator
ALLOWED_UNGUARDED = {
    # ── static / infra / SEO ──
    "static": "static files",
    "health.favicon": "public asset",
    "health.healthz": "load-balancer health check",
    "seo.robots_txt": "public",
    "seo.sitemap_xml": "public",
    "honeypot_routes.honeypot_trap": "decoy paths that log probes",
    "core.csp_report": "browser CSP reports (rate-limited, no auth by design)",
    "core.client_log": "browser error reports (rate-limited)",

    # ── public pages / pre-login flows ──
    "core.home": "public landing page",
    "core.checkin_page": "shared kiosk page; identity proven per check-in",
    "core.security_lockout": "lockout notice page",
    "org.get_started_page": "public signup",
    "org.create_org_page": "public signup",
    "org.create_org": "public signup (rate-limited)",
    "org.create_org_verify_otp_page": "signup OTP step; application token checked",
    "org.create_org_verify_otp": "signup OTP step; application token checked",
    "org.create_org_resend_otp": "signup OTP step; application token checked",
    "org.create_org_upload_documents_page": "renders form only; POST checks token",
    "org.create_org_upload_documents": "application access token checked (_load_application)",
    "org.create_org_pending_page": "static pending notice",
    "org.create_org_status_page": "application access token checked (_load_application)",
    "org.create_org_pay_page": "application access token checked (_verify_application_access)",
    "org.create_org_setup_trial_page": "application access token checked (_verify_application_access)",
    "org.create_org_setup_trial_create_subscription": "application access token checked",
    "org.create_org_setup_trial_confirm": "bound to the application's pending subscription + signature",
    "org.api_create_org": "public signup API (rate-limited)",
    "org.api_create_org_verify_otp": "application token checked",
    "org.api_create_org_resend_otp": "application token checked",
    "org.api_create_org_upload_documents": "application token checked",
    "org.api_create_org_status": "application token checked",
    "org.superadmin_redirect": "redirect only",
    "org.submit_lead": "public contact form (rate-limited)",
    "org.submit_feedback": "public feedback form (rate-limited)",
    "billing.create_order": "signup checkout; re-checks application status",
    "billing.verify_payment": "Razorpay signature verified",
    "webhooks.receive_webhook": "provider signature verified",
    "oauth_signup.oauth_signup_start": "OAuth start",
    "oauth_signup.oauth_signup_callback": "OAuth callback; server-side state + PKCE",
    "oauth_signup.oauth_signup_verify_page": "signup OTP step; pending-signup token checked",
    "oauth_signup.oauth_signup_verify": "signup OTP step; pending-signup token checked",
    "oauth_signup.oauth_signup_resend": "signup OTP step; pending-signup token checked",
    "oauth_signup.oauth_login_start": "OAuth start",
    "oauth_signup.oauth_login_callback": "OAuth callback; server-side state + PKCE",
    "onboarding.offer_letter_pdf": "candidate magic link (hashed, expiring token)",
    "onboarding.offer_letter_respond": "candidate magic link (hashed, expiring token)",

    # ── authentication endpoints themselves ──
    "auth.admin_login": "login",
    "auth.mfa_verify": "login step 2; server-side challenge",
    "auth.logout": "logout (POST + CSRF)",
    "auth.employee_logout": "logout (POST + CSRF)",
    "auth.admin_forgot_password": "password reset request",
    "auth.admin_reset_password": "hashed, expiring reset token",
    "auth.employee_forgot_password": "password reset request",
    "auth.employee_reset_password": "hashed, expiring reset token",
    "auth.step_up_mfa": "checks admin session inline",
    "core.api_login": "mobile login step 1",
    "core.api_login_verify": "mobile login step 2; server-side challenge",
    "core.api_logout": "revokes the presented Bearer token",
    "core.api_employee_login": "mobile employee login",
    "core.api_employee_logout": "revokes the presented Bearer token",
    "core.api_employee_signup": "employee self-signup (rate-limited, domain-checked)",
    "core.mobile_bridge_login": "single-use, 5-minute, hashed bridge token minted only for MFA'd tokens",
    "platform_admin.platform_admin_login": "super-admin login",
    "platform_admin.platform_admin_logout": "logout (POST + CSRF)",
    "soc.login": "SOC console login: password -> setup code/enrol -> TOTP (rate-limited, lockout)",
    "soc.logout": "SOC console logout (POST + CSRF)",
    "soc.asset": "SOC console assets; the login stylesheet is public, everything else needs a SOC session (checked inline)",

    # ── kiosk / WebAuthn (identity proven by QR signature, face or passkey) ──
    "attendance.attendance": "kiosk check-in; signed QR + server-decided factors",
    "attendance.api_attendance_challenge": (
        "kiosk liveness challenge; only redeemable together with that employee's face + "
        "signed QR; same-shape response, rate-limited"),
    "employee_portal.api_employee_qr_face_checkin": "kiosk check-in; signed QR + server-decided factors",
    "employee_portal.api_employee_auth_config": "public: which check-in factors are enabled",
    "auth.webauthn_status": "public capability probe",
    "auth.webauthn_registration_options": "gated by _wa_authorize_enrollment",
    "auth.webauthn_authentication_options": "challenge issuance",
    "auth.webauthn_verify_challenge": "verifies a WebAuthn assertion",
    "auth.api_kiosk_enroll_face_verify": "kiosk face match gates enrollment",
    "auth.webauthn_register": "requires enrollment authorization in session",
    "auth.webauthn_unenroll": "checks session inline",
    "auth.webauthn_register_kiosk": "requires prior kiosk face match + challenge",

    # ── session OR Bearer, checked inline (session_account_ok / token lookup) ──
    "auth.get_employee_webauthn_credential": "session (session_account_ok) or Bearer, ownership-checked",
    "employee_portal.api_employee_portal": "session (session_account_ok) or employee Bearer",
    "employees.generate_emp_id": "admin session + session_account_ok, JSON 401",
    "attendance.api_breaks": "session or Bearer (resolve_bearer_identity_any)",
    "ai_hrms.api_hr_helpdesk": "session (session_account_ok) or Bearer",
    "ai_hrms.api_evaluate_interview": "resolve_admin_identity",
    "ai_hrms.api_attrition_analytics": "resolve_admin_identity",
    "email_blast.api_email_blast": "resolve_admin_identity",
    "core.session_risk_stream": "per-session kill-switch SSE stream",

    # ── redirects only ──
    "leave.leave_requests_redirect": "redirect only",
    "leave.resignation_requests_redirect": "redirect only",
}


def _is_guarded(view):
    seen = set()
    fn = view
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        if getattr(fn, "_hrz_auth_guard", None):
            return True
        fn = getattr(fn, "__wrapped__", None)
    return False


def _unguarded_endpoints():
    out = {}
    for rule in flask_app.url_map.iter_rules():
        view = flask_app.view_functions.get(rule.endpoint)
        if view is None or _is_guarded(view):
            continue
        # /api/v1/* aliases reuse the same view function as their /api/* original.
        out.setdefault(rule.endpoint, rule.rule)
    return out


def test_every_route_is_guarded_or_explicitly_allowed():
    unguarded = _unguarded_endpoints()
    alias_free = {}
    for endpoint, rule in unguarded.items():
        base = endpoint
        if base not in ALLOWED_UNGUARDED:
            view = flask_app.view_functions[endpoint]
            original = next((ep for ep, v in flask_app.view_functions.items()
                             if v is view and ep in ALLOWED_UNGUARDED), None)
            if original:
                continue
            alias_free[endpoint] = rule
    assert not alias_free, (
        "Routes with no auth decorator and no ALLOWED_UNGUARDED entry "
        "(add a decorator, or an allow-list entry with a reason):\n"
        + "\n".join(f"  {ep}  ->  {rule}" for ep, rule in sorted(alias_free.items()))
    )


def test_allow_list_has_no_stale_entries():
    """Keeps the allow-list honest: an entry for a route that no longer
    exists, or that is now properly decorated, must be removed."""
    unguarded = set(_unguarded_endpoints())
    stale = sorted(ep for ep in ALLOWED_UNGUARDED if ep not in unguarded)
    assert not stale, f"Remove stale ALLOWED_UNGUARDED entries: {stale}"


def test_marker_survives_limiter_wrapping():
    """Sanity check on the mechanism itself: a decorated + rate-limited
    route is detected as guarded."""
    assert _is_guarded(flask_app.view_functions["core.api_mobile_web_session_link"])
    assert _is_guarded(flask_app.view_functions["documents.download_document"])
