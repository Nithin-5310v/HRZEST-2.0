# -*- coding: utf-8 -*-
"""OAuth blueprint -- "Sign up/in with Google" / "...with Microsoft".

Replaces the public entry point of the old manual /create_org form
(company details + GST + document upload + human KYC review) with a
quick path: sign in with a Google or Microsoft account, enter the 6-digit
code we email to that address, and the company is provisioned on a 2-day
free trial -- no payment, no waiting on a reviewer. The emailed code is
mandatory, as on every other signup path: the provider's own
email_verified claim isn't enough (Microsoft doesn't send one at all --
see utils/oauth_client.complete_oauth). The old manual routes
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
    GET /create_org/<provider>/callback emails the code and shows
    /create_org/oauth/verify, and POST /create_org/oauth/verify provisions
    the company once the code matches. All of these fall under app.py's
    _resolve_tenant() "/create_org" skip prefix (no tenant exists yet).
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
import re
import time
import secrets

from flask import Blueprint, request, redirect, session, flash, g, render_template

from extensions import limiter, app_log, log_security_event
from utils.auth import generate_password_hash, HR_ROLE, MANAGER_ROLE, stamp_password_version, _hash_token
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


@oauth_signup_bp.app_context_processor
def _inject_oauth_providers():
    """The providers whose client id and secret are set -- create_org.html
    and admin_login.html show a "Sign up/in with ..." button only for
    these, so a deployment without Google/Microsoft keys offers the manual
    form and password login alone instead of buttons that can only fail."""
    return {"oauth_providers": [p for p in _PROVIDERS if provider_configured(p)]}


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
        app_log.warning("oauth_signup.signup_callback_failed: provider=%s error=%s", provider, exc)
        flash("Sign-up failed. Please try again.", "error")
        return redirect("/create_org")

    if identity["mode"] != "signup":
        flash("Sign-up failed. Please try again.", "error")
        return redirect("/create_org")

    blocked = _signup_blocked(identity["email"])
    if blocked:
        return blocked
    pending_id, token = _start_pending_signup(provider, identity)
    return redirect(_verify_link(pending_id, token))


_PENDING_TTL_HOURS = 24


def _signup_blocked(email):
    """A redirect if this email can't start a new company (it already has
    an account, or its company domain already has a trial), else None.
    Checked when the provider sends the user back and again once the code
    is verified, since another signup can land in between."""
    from blueprints.org import check_duplicate_admin_email
    if check_duplicate_admin_email(email):
        flash("An account already exists for this email. Try signing in instead.", "error")
        return redirect(tpath("/login"))
    domain = email.split("@", 1)[1]
    if domain not in PERSONAL_EMAIL_DOMAINS and _tenant_for_domain(domain):
        flash(
            "A free trial has already been created for this organisation. "
            "Ask your admin for access, or sign in if that's you.", "error",
        )
        return redirect(tpath("/login"))
    return None


def _company_name_for(email, name):
    local, domain = email.split("@", 1)
    if domain in PERSONAL_EMAIL_DOMAINS:
        return name or local
    return domain.split(".")[0].replace("-", " ").title() or local


def _verify_link(pending_id, token):
    return f"/create_org/oauth/verify?id={pending_id}&token={token}"


_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,64}")


def _token_param(value):
    """A pending-signup token taken from the request, or "" unless it has
    the shape _start_pending_signup() issues (secrets.token_urlsafe) -- so
    only a well-formed token is ever put back into a redirect URL (QA SEC-14)."""
    return value if isinstance(value, str) and _TOKEN_RE.fullmatch(value) else ""


def _start_pending_signup(provider, identity):
    """Stores the provider-confirmed identity as an oauth_signup_pending row
    and emails it a code. Returns (pending_id, token): the token is the only
    way back to the row, so it travels in the verify link and form."""
    from database import get_master_db
    from blueprints.org import _generate_otp, _OTP_TTL_MINUTES, send_org_signup_otp_email
    token = secrets.token_urlsafe(32)
    otp_code = _generate_otp()
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM oauth_signup_pending WHERE created_at < NOW() - (%s * INTERVAL '1 hour')",
                (_PENDING_TTL_HOURS,))
    cur.execute(
        "INSERT INTO oauth_signup_pending (provider, subject, email, name, access_token_hash, otp_code_hash, "
        "otp_expires_at) VALUES (%s,%s,%s,%s,%s,%s, NOW() + (%s * INTERVAL '1 minute')) RETURNING id",
        (provider, identity["sub"], identity["email"], identity.get("name") or None,
         _hash_token(token), _hash_token(otp_code), _OTP_TTL_MINUTES)
    )
    pending_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    send_org_signup_otp_email(identity["email"], _company_name_for(identity["email"], identity.get("name")),
                              otp_code)
    return pending_id, token


def _load_pending(pending_id, token):
    """The oauth_signup_pending row as a dict if `token` is its token, else None."""
    if not str(pending_id).isdigit() or not token:
        return None
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, provider, subject, email, name, access_token_hash, otp_code_hash, otp_attempts, "
        "otp_expires_at < NOW() FROM oauth_signup_pending WHERE id=%s",
        (int(pending_id),)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not secrets.compare_digest(row[5], _hash_token(token)):
        return None
    keys = ("id", "provider", "subject", "email", "name", "access_token_hash", "otp_code_hash",
            "otp_attempts", "expired")
    return dict(zip(keys, row))


def _run_on_pending(sql, params):
    """Executes one statement against oauth_signup_pending; returns the row count."""
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(sql, params)
    count = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return count


def _expired_signup():
    flash("This sign-up has expired. Please start again.", "error")
    return redirect("/create_org")


@oauth_signup_bp.route("/create_org/oauth/verify", methods=["GET"])
def oauth_signup_verify_page():
    token = _token_param(request.args.get("token", ""))
    pending = _load_pending(request.args.get("id", ""), token)
    if not pending:
        return _expired_signup()
    return render_template(
        "create_org_verify_otp.html", application_id=pending["id"], token=token, sent_to=pending["email"],
        oauth=True, verify_action="/create_org/oauth/verify", resend_action="/create_org/oauth/resend",
    )


@oauth_signup_bp.route("/create_org/oauth/verify", methods=["POST"])
@limiter.limit("20 per hour")
def oauth_signup_verify():
    from blueprints.org import _OTP_MAX_ATTEMPTS
    token = _token_param(request.form.get("token", ""))
    pending = _load_pending(request.form.get("application_id", ""), token)
    if not pending:
        return _expired_signup()
    back_to_form = redirect(_verify_link(pending["id"], token))

    if pending["otp_attempts"] >= _OTP_MAX_ATTEMPTS:
        flash("Too many incorrect attempts. Please request a new code.", "error")
        return back_to_form
    otp_code = request.form.get("otp_code", "").strip()
    if not otp_code or not secrets.compare_digest(_hash_token(otp_code), pending["otp_code_hash"]):
        _run_on_pending("UPDATE oauth_signup_pending SET otp_attempts=otp_attempts+1 WHERE id=%s", (pending["id"],))
        flash("Incorrect code. Please try again.", "error")
        return back_to_form
    if pending["expired"]:
        flash("This code has expired. Please request a new one.", "error")
        return back_to_form

    # Single use: of two simultaneous submissions only one deletes the row.
    if not _run_on_pending("DELETE FROM oauth_signup_pending WHERE id=%s", (pending["id"],)):
        return _expired_signup()
    blocked = _signup_blocked(pending["email"])
    if blocked:
        return blocked
    return _provision_oauth_signup(pending["provider"], pending["subject"], pending["email"], pending["name"])


@oauth_signup_bp.route("/create_org/oauth/resend", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def oauth_signup_resend():
    from blueprints.org import _generate_otp, _OTP_TTL_MINUTES, send_org_signup_otp_email
    token = _token_param(request.form.get("token", ""))
    pending = _load_pending(request.form.get("application_id", ""), token)
    if not pending:
        return _expired_signup()
    otp_code = _generate_otp()
    _run_on_pending(
        "UPDATE oauth_signup_pending SET otp_code_hash=%s, otp_expires_at=NOW() + (%s * INTERVAL '1 minute'), "
        "otp_attempts=0 WHERE id=%s",
        (_hash_token(otp_code), _OTP_TTL_MINUTES, pending["id"])
    )
    send_org_signup_otp_email(pending["email"], _company_name_for(pending["email"], pending["name"]), otp_code)
    flash("A new code has been sent to your email.", "info")
    return redirect(_verify_link(pending["id"], token))


def _provision_oauth_signup(provider, subject, email, name):
    """Creates the company for a verified OAuth signup, links the admin
    account to the provider identity and signs the admin in."""
    from blueprints.org import provision_tenant, _clean_subdomain_slug
    domain = email.split("@", 1)[1]
    is_personal_domain = domain in PERSONAL_EMAIL_DOMAINS
    company_name = _company_name_for(email, name)
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
        (provider, subject, admin_username)
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
        app_log.warning("oauth_signup.login_callback_failed: provider=%s error=%s", provider, exc)
        flash("Sign-in failed. Please try again.", "error")
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
