# -*- coding: utf-8 -*-
"""Minimal OAuth2/OIDC authorization-code+PKCE client for "Sign up/in with
Google" and "...with Microsoft" -- no third-party OAuth library, stdlib
urllib.request only (matches this codebase's existing minimal-dependency
pattern, e.g. utils/ai_assistant.py's stdlib call to the Anthropic API).
Both providers are reached via their standard OIDC authorization-code flow
plus a userinfo-endpoint call -- no local JWT/JWKS signature verification
needed, since the tokens are obtained over a direct, client_secret-
authenticated server-to-server HTTPS call, not accepted from the browser.

State/PKCE storage is server-side (att_master.oauth_signup_states,
migrations/master.py), keyed by the opaque `state` token itself, NOT the
Flask session. Why: this app's session cookie is
SESSION_COOKIE_SAMESITE="Strict" (extensions.py) -- which is not sent on
the top-level cross-site GET that brings the browser back from
accounts.google.com/login.microsoftonline.com to our own callback URL, so
a session-held state would fail verification on effectively every real
attempt. A DB-backed store also works correctly regardless of worker count
or Redis availability, unlike an in-memory dict.
"""
import os
import json
import base64
import hashlib
import secrets
import urllib.request
import urllib.parse
import urllib.error

from extensions import app_log

# One-trial-per-domain (see migrations/master.py's tenants.email_domain
# partial unique index, which excludes exactly this list) doesn't apply to
# personal mail providers -- every Gmail user shares "gmail.com", so a
# domain-level uniqueness rule would let only the first Gmail user on
# Earth ever get a trial. Signups on these domains are deduped per full
# email address instead (blueprints/oauth_signup.py's
# check_duplicate_admin_email() reuse), same as any other admin_email.
PERSONAL_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "icloud.com", "me.com", "aol.com",
    "protonmail.com", "proton.me", "zoho.com", "yandex.com", "gmx.com",
    "mail.com",
})

_STATE_TTL_SECONDS = 600  # 10 minutes -- plenty to complete a provider's consent screen

PROVIDERS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "client_id_env": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret_env": "GOOGLE_OAUTH_CLIENT_SECRET",
    },
    "microsoft": {
        # MS_OAUTH_TENANT (.env) selects which Microsoft accounts can sign
        # in: "common" (default) = personal + work/school accounts,
        # "organizations" = work/school only, "consumers" = personal only,
        # or a specific Azure AD tenant ID/domain to restrict to one org.
        "authorize_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "scope": "openid email profile",
        "client_id_env": "MICROSOFT_OAUTH_CLIENT_ID",
        "client_secret_env": "MICROSOFT_OAUTH_CLIENT_SECRET",
    },
}


class OAuthError(Exception):
    """User-facing OAuth failure -- str(exc) is safe to flash() as-is."""


def provider_configured(provider):
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return False
    return bool(os.environ.get(cfg["client_id_env"]) and os.environ.get(cfg["client_secret_env"]))


def _ms_tenant():
    return os.environ.get("MS_OAUTH_TENANT", "common")


def _authorize_url(provider):
    url = PROVIDERS[provider]["authorize_url"]
    return url.format(tenant=_ms_tenant()) if provider == "microsoft" else url


def _token_url(provider):
    url = PROVIDERS[provider]["token_url"]
    return url.format(tenant=_ms_tenant()) if provider == "microsoft" else url


def _b64url(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")


def _save_state(state, provider, mode, code_verifier, tenant_db):
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor()
    # Opportunistic cleanup on every insert -- no separate scheduled job
    # needed for a table this small and self-limiting.
    cur.execute("DELETE FROM oauth_signup_states WHERE created_at < NOW() - INTERVAL '15 minutes'")
    cur.execute(
        "INSERT INTO oauth_signup_states (state, provider, mode, code_verifier, tenant_db) "
        "VALUES (%s, %s, %s, %s, %s)",
        (state, provider, mode, code_verifier, tenant_db)
    )
    conn.commit()
    cur.close()
    conn.close()


def _consume_state(state, provider):
    """Deletes and returns the stored (code_verifier, mode, tenant_db) row
    (single-use), or None if state is unknown/expired/already used --
    callers must treat None as a hard failure."""
    from database import get_master_db
    conn = get_master_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM oauth_signup_states WHERE state=%s AND provider=%s "
        "AND created_at >= NOW() - INTERVAL %s RETURNING code_verifier, mode, tenant_db",
        (state, provider, f"{_STATE_TTL_SECONDS} seconds")
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return row


def build_authorize_url(provider, redirect_uri, mode, tenant_db=None):
    """Starts an authorization-code+PKCE flow. `mode` is 'signup' or
    'login'; `tenant_db` is required for 'login' (which tenant's
    admin_users to check on the way back -- the provider's redirect
    target is a single fixed global URL with no tenant slug in it).
    Returns the URL to redirect the browser to. Raises RuntimeError if
    the provider isn't configured."""
    if not provider_configured(provider):
        raise RuntimeError(f"{provider} OAuth is not configured")
    cfg = PROVIDERS[provider]
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    _save_state(state, provider, mode, code_verifier, tenant_db)
    params = {
        "client_id": os.environ[cfg["client_id_env"]],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": cfg["scope"],
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{_authorize_url(provider)}?{urllib.parse.urlencode(params)}"


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 -- fixed https provider URLs, never request input
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url, bearer_token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 -- fixed https provider URLs
        return json.loads(resp.read().decode("utf-8"))


def complete_oauth(provider, code, state, redirect_uri):
    """Exchanges an authorization `code` for tokens and fetches the
    provider's userinfo. Returns {sub, email, name, provider, mode,
    tenant_db}. Raises OAuthError on any failure (unknown/expired state,
    provider error, missing/unverified email) -- callers should flash()
    str(exc) directly rather than the raw exception."""
    consumed = _consume_state(state, provider)
    if not consumed:
        raise OAuthError("Your sign-in attempt expired or was already used. Please try again.")
    code_verifier, mode, tenant_db = consumed

    cfg = PROVIDERS[provider]
    try:
        token_payload = _post_form(_token_url(provider), {
            "client_id": os.environ[cfg["client_id_env"]],
            "client_secret": os.environ[cfg["client_secret_env"]],
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        })
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        app_log.warning("oauth.token_exchange_failed: provider=%s status=%s body=%s", provider, exc.code, body)
        raise OAuthError("Sign-in with the provider failed. Please try again.")
    except Exception as exc:
        app_log.warning("oauth.token_exchange_error: provider=%s error=%s", provider, exc)
        raise OAuthError("Sign-in with the provider failed. Please try again.")

    access_token = token_payload.get("access_token")
    if not access_token:
        raise OAuthError("Sign-in with the provider failed. Please try again.")

    try:
        userinfo = _get_json(cfg["userinfo_url"], access_token)
    except Exception as exc:
        app_log.warning("oauth.userinfo_failed: provider=%s error=%s", provider, exc)
        raise OAuthError("Could not read your profile from the provider. Please try again.")

    email = (userinfo.get("email") or "").strip().lower()
    sub = userinfo.get("sub") or userinfo.get("id") or userinfo.get("oid")
    name = userinfo.get("name") or (email.split("@")[0] if email else "")
    if not email or not sub:
        raise OAuthError("Your account did not return an email address. Please try a different account.")

    # Google explicitly reports verification status; trust it when
    # present. Microsoft's OIDC userinfo returns no email_verified claim
    # at all -- an email surfaced there is already Microsoft's own
    # verified account email (personal or Azure AD work/school), so its
    # absence is treated as verified rather than rejected.
    email_verified = userinfo.get("email_verified", True)
    if isinstance(email_verified, str):
        email_verified = email_verified.lower() == "true"
    if not email_verified:
        raise OAuthError("Your email address is not verified with the provider yet.")

    return {"sub": str(sub), "email": email, "name": name, "provider": provider, "mode": mode, "tenant_db": tenant_db}
