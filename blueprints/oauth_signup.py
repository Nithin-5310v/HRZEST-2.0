# -*- coding: utf-8 -*-
"""OAuth blueprint -- "Sign up/in with Google" / "...with Microsoft".

Replaces the public entry point of the old manual /create_org form
(company details + GST + document upload + human KYC review) with an
instant path: sign in with a Google or Microsoft account, we provision the
company in the same request, and you land straight on a 2-day free trial
-- no payment, no waiting on a reviewer. The old manual routes
(POST /create_org, OTP verify, document upload, Platform Admin's KYC
review queue) still exist underneath and are untouched -- they're no
longer linked from the public signup page, but removing them outright
would also break Platform Admin's existing review tooling and any
already-in-flight legacy applications, which is out of scope here.

Trade-off this deliberately makes: no company documents, GST, or human
review at signup means there is no fraud/duplicate-business check beyond
(a) one trial per company email domain (personal providers like Gmail
exempted -- see utils/oauth_client.PERSONAL_EMAIL_DOMAINS) and (b) the
existing check_duplicate_admin_email() exact-email check. A company that
wants to fake a business can still get a 2-day trial; the KYC pipeline
this bypasses was the thing that would have caught that. Left as an
accepted trade-off per this feature's explicit spec, not an oversight.

Two independent flows:
  - SIGNUP (new company): GET /create_org/<provider> starts it,
    GET /create_org/<provider>/callback finishes it. Both paths already
    fall under app.py's _resolve_tenant() "/create_org" skip prefix (no
    tenant exists yet).
  - LOGIN (returning admin of an OAuth-provisioned company, needed
    because that admin has no usable password -- see provision_tenant()
    call below): GET /oauth_login/<provider> must be reached tenant-
    prefixed (e.g. /acme/oauth_login/google) so _resolve_tenant() sets
    g.tenant_db from the URL before it captures which tenant this login
    is for. GET /oauth/callback/<provider> is the fixed global URL the
    provider actually redirects back to (a per-tenant redirect_uri isn't
    possible -- Google/Microsoft only accept exact-match pre-registered
    URLs) -- which tenant it's for travels via the server-side
    oauth_signup_states row instead (utils/oauth_client.py), keyed by the
    `state` param, not the URL.
"""
import time
import secrets

from flask import Blueprint, request, redirect, session, flash, g

from extensions import limiter, app_log, log_security_event
from utils.auth import generate_password_hash, HR_ROLE, MANAGER_ROLE, stamp_password_version
from utils.helpers import tpath, _safe_app_url
from utils.session_risk import ensure_session_id
from utils.oauth_client import (
    provider_configured, build_authorize_url, complete_oauth, OAuthError,
    PERSONAL_EMAIL_DOMAINS,
)

oauth_signup_bp = Blueprint("oauth_signup", __name__)

_PROVIDERS = ("google", "microsoft")


def _bad_provider(provider):
    return provider not in _PROVIDERS


def _tenant_for_domain(domain):
    """(id, company_name) of the tenant already registered under this
    email domain, or None. Only meaningful for non-personal domains --
    see PERSONAL_EMAIL_DOMAINS."""
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute("SELECT id, company_name FROM tenants WHERE email_domain=%s", (domain,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def _unique_subdomain(base_slug):
    """base_slug, or base_slug-xxxx if taken/reserved. provision_tenant()
    itself is still the authoritative uniqueness check (this just
    minimizes retries under the normal, non-racing case)."""
    from database import get_master_db
    from blueprints.org import _RESERVED_SUBDOMAINS
    base_slug = base_slug or "company"
    conn = get_master_db()
    cur = conn.cursor()
    candidate = base_slug
    for _attempt in range(20):
        cur.execute("SELECT 1 FROM tenants WHERE subdomain=%s", (candidate,))
        taken = cur.fetchone() is not None
        if not taken and candidate not in _RESERVED_SUBDOMAINS:
            break
        candidate = f"{base_slug}-{secrets.token_hex(2)}"
    cur.close()
    conn.close()
    return candidate


def _start_admin_session(username, role):
    session.clear()
    session["admin_logged_in"] = True
    session["admin_username"] = username
    session["admin_role"] = role
    session["_session_created"] = time.time()
    session.permanent = True
    ensure_session_id(session)
    stamp_password_version("admin_users", username)


def _dest_path_for_role(role):
    if role == HR_ROLE:
        return "/hr_dashboard"
    if role == MANAGER_ROLE:
        return "/leave_holidays"
    return "/admin"


# ─────────────────────────── Signup (new company) ───────────────────────────

@oauth_signup_bp.route("/create_org/<provider>")
@limiter.limit("20 per hour")
def oauth_signup_start(provider):
    if _bad_provider(provider):
        return "Unknown provider", 404
    if not provider_configured(provider):
        flash(f"Sign up with {provider.title()} isn't available right now.", "error")
        return redirect("/create_org")
    redirect_uri = f"{_safe_app_url()}/create_org/{provider}/callback"
    try:
        url = build_authorize_url(provider, redirect_uri, mode="signup")
    except RuntimeError:
        flash(f"Sign up with {provider.title()} isn't available right now.", "error")
        return redirect("/create_org")
    return redirect(url)


@oauth_signup_bp.route("/create_org/<provider>/callback")
@limiter.limit("20 per hour")
def oauth_signup_callback(provider):
    if _bad_provider(provider):
        return "Unknown provider", 404
    if request.args.get("error"):
        flash("Sign-up was cancelled. Please try again.", "error")
        return redirect("/create_org")
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        flash("Sign-up failed. Please try again.", "error")
        return redirect("/create_org")

    redirect_uri = f"{_safe_app_url()}/create_org/{provider}/callback"
    try:
        identity = complete_oauth(provider, code, state, redirect_uri)
    except OAuthError as exc:
        flash(str(exc), "error")
        return redirect("/create_org")

    if identity["mode"] != "signup":
        flash("Sign-up failed. Please try again.", "error")
        return redirect("/create_org")

    email = identity["email"]
    domain = email.split("@", 1)[1]
    is_personal_domain = domain in PERSONAL_EMAIL_DOMAINS

    from blueprints.org import check_duplicate_admin_email, provision_tenant, _clean_subdomain_slug
    dup = check_duplicate_admin_email(email)
    if dup:
        flash("An account already exists for this email. Try signing in instead.", "error")
        return redirect(tpath("/login"))

    if not is_personal_domain:
        existing = _tenant_for_domain(domain)
        if existing:
            flash(
                "A free trial has already been created for this organisation. "
                "Ask your admin for access, or sign in if that's you.", "error",
            )
            return redirect(tpath("/login"))

    company_name = identity["name"] if is_personal_domain else domain.split(".")[0].replace("-", " ").title()
    if not company_name:
        company_name = email.split("@")[0]
    base_slug = _clean_subdomain_slug(company_name if not is_personal_domain else email.split("@")[0])

    admin_username = email
    random_password_hash = generate_password_hash(secrets.token_urlsafe(32))

    # Small retry loop: _unique_subdomain() picks a likely-free slug, but
    # provision_tenant() is the authoritative check -- a genuine race
    # (two signups landing on the same slug in the same instant) is
    # retried with a fresh random suffix rather than failing the signup.
    ok = err = portal_subdomain = None
    for _attempt in range(3):
        subdomain = _unique_subdomain(base_slug)
        ok, err, _portal_url, _checkin_url = provision_tenant(
            company_name=company_name,
            subdomain=subdomain,
            admin_username=admin_username,
            admin_password_hash=random_password_hash,
            admin_email=email,
            payment_option="trial",
            email_domain=domain,
            subscription_status="trialing",
        )
        if ok:
            portal_subdomain = subdomain
            break
        if not err or "already taken" not in err:
            break

    if not ok:
        app_log.error("oauth_signup provisioning failed: provider=%s domain=%s error=%s", provider, domain, err)
        flash("Could not create your organisation. Please try again or contact support.", "error")
        return redirect("/create_org")

    db_name = "att_" + portal_subdomain.replace("-", "_")

    # provision_tenant() deliberately does NOT write email_domain onto the
    # master tenants row (see its own comment) -- every other caller
    # shares that function and two unrelated tenants provisioned through
    # them can share a domain just fine. Only this OAuth signup path
    # relies on that column (and its partial unique index,
    # migrations/master.py) for "one free trial per company domain", so
    # it sets it here, directly, scoped to only the tenant just created.
    # The partial unique index still protects against the race the
    # earlier _tenant_for_domain() pre-check can't (two signups for the
    # same never-before-seen domain within the same instant) -- this
    # UPDATE would raise on a genuine collision here. Left unhandled
    # deliberately: the tenant is already fully provisioned by this
    # point, and there's no clean partial-rollback to fall back to for
    # something this rare; a 500 for the unlucky second request is an
    # acceptable outcome next to silently allowing the duplicate through.
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    mcur.execute("UPDATE tenants SET email_domain=%s WHERE db_name=%s", (domain, db_name))
    mconn.commit()
    mcur.close()
    mconn.close()

    from database import get_tenant_db
    tconn = get_tenant_db(db_name)
    tcur = tconn.cursor()
    tcur.execute(
        "UPDATE admin_users SET oauth_provider=%s, oauth_subject=%s WHERE username=%s",
        (provider, identity["sub"], admin_username)
    )
    tconn.commit()
    tcur.close()
    tconn.close()

    _start_admin_session(admin_username, "admin")
    log_security_event(
        "auth.oauth_signup_success",
        f"OAuth signup+login for '{company_name}' ({portal_subdomain}) via {provider}",
        level="INFO", identifier=admin_username,
    )
    # Relative, not _safe_app_url()-prefixed: this is a same-request
    # browser redirect (the next request resolves g.tenant_db/session
    # tenant_db fresh from this URL's slug, same as every other
    # tenant-scoped redirect in this codebase -- see auth.py's tpath()
    # uses), not a link handed to something outside this request.
    return redirect(f"/{portal_subdomain}/admin")


# ────────────────────── Login (returning OAuth admin) ───────────────────────

@oauth_signup_bp.route("/oauth_login/<provider>")
@limiter.limit("30 per hour")
def oauth_login_start(provider):
    if _bad_provider(provider):
        return "Unknown provider", 404
    if not provider_configured(provider):
        flash(f"Sign in with {provider.title()} isn't available right now.", "error")
        return redirect(tpath("/login"))
    tenant_db = getattr(g, "tenant_db", None)
    redirect_uri = f"{_safe_app_url()}/oauth/callback/{provider}"
    try:
        url = build_authorize_url(provider, redirect_uri, mode="login", tenant_db=tenant_db)
    except RuntimeError:
        flash(f"Sign in with {provider.title()} isn't available right now.", "error")
        return redirect(tpath("/login"))
    return redirect(url)


@oauth_signup_bp.route("/oauth/callback/<provider>")
@limiter.limit("30 per hour")
def oauth_login_callback(provider):
    if _bad_provider(provider):
        return "Unknown provider", 404
    if request.args.get("error"):
        flash("Sign-in was cancelled. Please try again.", "error")
        return redirect("/login")
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        flash("Sign-in failed. Please try again.", "error")
        return redirect("/login")

    redirect_uri = f"{_safe_app_url()}/oauth/callback/{provider}"
    try:
        identity = complete_oauth(provider, code, state, redirect_uri)
    except OAuthError as exc:
        flash(str(exc), "error")
        return redirect("/login")

    db_name = identity.get("tenant_db")
    if identity["mode"] != "login" or not db_name or not db_name.startswith("att_"):
        flash("Sign-in failed. Please try again.", "error")
        return redirect("/login")
    subdomain = db_name[len("att_"):].replace("_", "-")

    from database import get_tenant_db
    tconn = get_tenant_db(db_name)
    tcur = tconn.cursor()
    tcur.execute(
        "SELECT username, COALESCE(role,'admin'), COALESCE(is_active,1) FROM admin_users "
        "WHERE oauth_provider=%s AND oauth_subject=%s",
        (provider, identity["sub"])
    )
    row = tcur.fetchone()
    tcur.close()
    tconn.close()

    if not row or not row[2]:
        flash("No account here is linked to that sign-in. Contact your company admin.", "error")
        return redirect(f"/{subdomain}/login")

    username, role, _active = row
    g.tenant_db = db_name  # request-scoped; makes the log line below (and anything else this request touches) resolve against the right schema
    _start_admin_session(username, role)
    log_security_event(
        "auth.oauth_login_success", f"OAuth login for '{username}' via {provider}",
        level="INFO", identifier=username,
    )
    # Relative, not _safe_app_url()-prefixed -- see the matching comment
    # in oauth_signup_callback() above.
    return redirect(f"/{subdomain}{_dest_path_for_role(role)}")
