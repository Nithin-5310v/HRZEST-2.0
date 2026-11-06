# -*- coding: utf-8 -*-
"""CSRF enforcement, CSP nonces and security headers, and the
response rewriting that injects CSRF fields/meta.

Split out of app.py; registered on the shared Flask app at import time.
app.py imports the app_setup modules in a fixed order because Flask runs
before_request hooks in registration order (after_request in reverse).
"""
import base64
import hashlib
import html as _html
import os
import re
import secrets
from extensions import app, app_log
from flask import current_app, flash, jsonify, redirect, request, session, url_for
from urllib.parse import urlsplit
from utils.helpers import invalidate_settings_cache, static_url as _static_url
from app_setup.templating import _csrf_token


_CSRF_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Browser telemetry sinks (blueprints/core.py). The browser's own report-uri
# mechanism fires /csp-report automatically on every CSP violation -- there is
# no page JS in the loop to attach an X-CSRF-Token and no <form> to carry one,
# so it can never pass the check (every report used to be silently 403'd).
# /api/client-log is the error beacon and must also work for logged-in users.
# Both are write-only log sinks anyone can already POST to, so a forged request
# gains an attacker nothing.
_CSRF_BEACON_PATHS = frozenset({"/csp-report", "/api/client-log"})


def _session_is_authenticated():
    return bool(session.get("admin_logged_in") or session.get("employee_id")
                or session.get("platform_admin_logged_in"))


def _csrf_exempt(path, has_bearer, session_authenticated):
    """Whether a state-changing request skips the CSRF token check.

    CSRF only matters when the browser attaches an ambient credential (the
    session cookie) that a forged cross-site request can ride. Under /api/
    that is NOT true of every route -- dozens of them (hr_accounts, settings,
    email-blast, ...) are @admin_required/@employee_required and authenticate
    by session, so a blanket /api/ exemption left them relying on
    SameSite=Strict alone. An /api/ request is therefore exempt only if it
    carries a Bearer token (can't be forged cross-site without a CORS
    preflight) or has no logged-in session to ride (mobile/pre-login calls:
    /api/login, /api/employee/signup, /api/leads, ...).

    The website's login form (/login, /admin_login, /admin-login) is NOT
    exempt: without a token check, a third-party page could post its own
    credentials there and silently sign the visitor into the attacker's
    account (login CSRF). The login page renders with a token like any
    other form, so a real sign-in always carries one."""
    if path in _CSRF_BEACON_PATHS:
        return True
    if path.startswith("/api/"):
        return has_bearer or not session_authenticated
    return False


@app.before_request
def _enforce_csrf():
    if request.method not in _CSRF_METHODS:
        return
    if current_app.testing:
        return  # CSRF disabled in test mode; Bearer-token tests handle auth separately
    if request.path.startswith("/webhooks/"):
        # Server-to-server callbacks (Razorpay etc.) -- no session cookie
        # exists to carry a CSRF token in the first place; authenticated by
        # request signature instead (blueprints/webhooks.py's generic
        # /webhooks/<provider> route).
        return
    if _csrf_exempt(request.path,
                    request.headers.get("Authorization", "").startswith("Bearer "),
                    _session_is_authenticated()):
        return
    # NOTE: We intentionally do NOT skip JSON requests here. The auto-inject
    # script (_inject_csrf_meta) adds X-CSRF-Token to every fetch() call, so
    # legitimate JSON POSTs from the web UI already carry the token.
    # Skipping CSRF for is_json would allow XSS payloads to forge state-changing
    # JSON requests without a token.
    token = session.get("_csrf")
    submitted = (request.form.get("_csrf_token")
                 or request.headers.get("X-CSRF-Token")
                 or request.headers.get("X-CSRFToken"))
    if not token or not submitted or not secrets.compare_digest(str(token), str(submitted)):
        # Browser form submissions: redirect to login so the user gets a fresh
        # session+token. Gated on the request's actual Content-Type, not
        # Accept/X-Requested-With -- those are unreliable signals for
        # "this is a real full-page form submission, not a background
        # fetch() call": a bare fetch() with no explicit headers sends
        # Accept: */* (which accept_mimetypes.accept_html treats as
        # accepting HTML too) and never sets X-Requested-With on its own,
        # so a JSON-posting fetch() call whose CSRF token expired was
        # taking this branch by mistake. Its JS never follows the redirect
        # or renders the flash, but flash() still queued the message into
        # the session -- silently, repeatedly, once per failed background
        # call -- until the user's next *real* page load (one that calls
        # get_flashed_messages()) dumped every accumulated copy at once.
        # A genuine <form method="post"> is the only thing that can ever
        # carry these two Content-Types; every legitimate fetch() POST in
        # this app sends JSON instead (see the comment above on why JSON
        # isn't exempted from the CSRF check itself).
        if (request.mimetype in ("application/x-www-form-urlencoded", "multipart/form-data")
                and request.accept_mimetypes.accept_html):
            flash("Your session expired. Please log in again.", "warning")
            # There is no standalone "employee_login" endpoint -- employee and
            # admin credentials are both checked by the one unified /login
            # page (auth.admin_login).
            if request.path.startswith("/super_admin"):
                login_url = "/super_admin/login"
            elif request.path == "/soc" or request.path.startswith("/soc/"):
                login_url = "/soc/login"
            else:
                login_url = url_for("auth.admin_login")
            return redirect(login_url)
        return jsonify({"ok": False, "msg": "Session expired. Please refresh and try again."}), 403


_CSRF_HEAD_RE = re.compile(rb'</head>', re.IGNORECASE)
_CSRF_BODY_RE = re.compile(rb'</body>', re.IGNORECASE)
# Every <form method="post"> gets the token as a hidden field in the HTML
# itself. _CSRF_SCRIPT below also adds one on DOMContentLoaded, but security
# scanners (and browsers with JS off) only see the markup -- which is how a
# ZAP scan reported "Absence of Anti-CSRF Tokens" on /login and
# /admin_forgot_password. <script> bodies are left alone so a form built as
# a JS string (templates/hr_dashboard.html) isn't edited mid-string.
_POST_FORM_RE = re.compile(rb'<form\b[^>]*\bmethod\s*=\s*["\']?post\b[^>]*>', re.IGNORECASE)
_FORM_END_RE = re.compile(rb'</form\s*>', re.IGNORECASE)
_SCRIPT_BLOCK_RE = re.compile(rb'<script\b.*?</script\s*>', re.IGNORECASE | re.DOTALL)
_HAS_CSRF_FIELD_RE = re.compile(rb'name\s*=\s*["\']?_csrf_token\b', re.IGNORECASE)


def _add_csrf_fields(html, token):
    """Insert a hidden _csrf_token field at the top of every POST form in
    `html` (bytes) that doesn't already have one, outside <script> blocks."""
    field = b'<input type="hidden" name="_csrf_token" value="' + token.encode() + b'">'

    def in_markup(chunk):
        out, pos = [], 0
        for form in _POST_FORM_RE.finditer(chunk):
            end = _FORM_END_RE.search(chunk, form.end())
            body = chunk[form.end():end.start() if end else len(chunk)]
            out.append(chunk[pos:form.end()])
            if not _HAS_CSRF_FIELD_RE.search(body):
                out.append(field)
            pos = form.end()
        out.append(chunk[pos:])
        return b"".join(out)

    parts, pos = [], 0
    for script in _SCRIPT_BLOCK_RE.finditer(html):
        parts.append(in_markup(html[pos:script.start()]))
        parts.append(script.group(0))
        pos = script.end()
    parts.append(in_markup(html[pos:]))
    return b"".join(parts)


# Matches <script>/<style> tags without a nonce -- used to inject CSP nonces
_SCRIPT_TAG_RE = re.compile(rb'<script(?!\s[^>]*\bnonce\b)(?=[\s>])', re.IGNORECASE)
_STYLE_TAG_RE = re.compile(rb'<style(?!\s[^>]*\bnonce\b)(?=[\s>])', re.IGNORECASE)
# Inline style="" attribute values, hashed so style-src-attr can drop
# 'unsafe-inline' in favor of per-response sha256 hashes.
_CSP_STYLE_DQ = re.compile(rb'\bstyle\s*=\s*"([^"]*)"', re.IGNORECASE)
_CSP_STYLE_SQ = re.compile(rb"\bstyle\s*=\s*'([^']*)'", re.IGNORECASE)
_CSRF_SCRIPT = (
    b'<script>(function(){'
    b'var m=document.querySelector(\'meta[name="csrf-token"]\');'
    b'if(!m)return;'
    b'window._csrfToken=function(){return m.content;};'
    b'var _of=window.fetch;'
    b'window.fetch=function(u,o){'
    b'o=o||{};'
    b'var mt=(o.method||"GET").toUpperCase();'
    b'if(mt==="POST"||mt==="PUT"||mt==="PATCH"||mt==="DELETE"){'
    b'if(o.headers instanceof Headers){'
    b'if(!o.headers.has("X-CSRF-Token"))o.headers.set("X-CSRF-Token",m.content);'
    b'}else{o.headers=Object.assign({},o.headers||{});'
    b'if(!o.headers["X-CSRF-Token"])o.headers["X-CSRF-Token"]=m.content;}}'
    b'return _of.call(this,u,o);};'
    b'document.addEventListener("DOMContentLoaded",function(){'
    b'document.querySelectorAll("form").forEach(function(f){'
    b'if(f.method.toLowerCase()==="post"&&!f.querySelector(\'[name="_csrf_token"]\')){'
    b'var i=document.createElement("input");'
    b'i.type="hidden";i.name="_csrf_token";i.value=m.content;'
    b'f.prepend(i);}});});})();</script>'
)
# Session kill-switch listener -- only injected on pages rendered for an
# authenticated session (see _inject_csrf_meta below), since the SSE
# endpoint itself requires auth and there's nothing to listen for on public
# pages. EventSource auto-reconnects on its own when a bounded-duration
# stream closes normally (see /api/session/risk-stream), so onerror is
# deliberately a no-op rather than manual reconnect logic.
#
# IMPORTANT, and worth being explicit about: this client-side wipe/redirect
# is a UX nicety, not the security boundary. The session cookie is
# HttpOnly by design (extensions.py), so this script cannot read or clear
# it -- that's what HttpOnly means, and weakening it to let JS touch the
# session cookie would be a strictly worse trade for a cosmetic gain. The
# real kill switch is server-side: utils/auth.py's _reject_if_compromised()
# rejects every request on a compromised session regardless of whether
# this script ever runs, and the server's own redirect response is what
# actually clears the session cookie via Set-Cookie. The cookie-wipe loop
# below only ever affects non-HttpOnly cookies (e.g. anything analytics-
# related some future page might add) -- harmless to include, not load-
# bearing for security.
# Frontend (browser) error reporter -> POST /api/client-log (blueprints/core.py).
# Capped at 8 reports per page load, de-duplicated, and sends only the page
# *path* (never the query string, which can carry reset tokens).
_CLIENT_LOG_SCRIPT = (
    b'<script>(function(){'
    b'var n=0,seen={};'
    b'function send(t,m,s,l){'
    b'if(n>=8)return;var k=t+"|"+m+"|"+s+"|"+l;if(seen[k])return;seen[k]=1;n++;'
    b'try{var b=JSON.stringify({type:t,message:String(m||"").slice(0,300),'
    b'source:String(s||"").slice(0,200),line:l||"",page:location.pathname});'
    b'if(navigator.sendBeacon){navigator.sendBeacon("/api/client-log",new Blob([b],{type:"application/json"}));}'
    b'else{fetch("/api/client-log",{method:"POST",headers:{"Content-Type":"application/json"},body:b,keepalive:true});}'
    b'}catch(x){}}'
    b'window.addEventListener("error",function(e){'
    b'if(e.target&&e.target!==window){var u=e.target.src||e.target.href;'
    b'if(u)send("resource_error","Failed to load "+String(e.target.tagName||"resource").toLowerCase(),String(u).split("?")[0],"");}'
    b'else send("js_error",e.message,String(e.filename||"").split("?")[0],e.lineno);},true);'
    b'window.addEventListener("unhandledrejection",function(e){'
    b'var r=e.reason;send("unhandled_rejection",r&&r.message?r.message:r,"","");});'
    b'})();</script>'
)
_KILLSWITCH_SCRIPT = (
    b'<script>(function(){'
    b'if(typeof EventSource==="undefined")return;'
    b'var es=new EventSource("/api/session/risk-stream");'
    b'function kill(){'
    b'es.close();'
    b'try{alert("Security alert: unusual activity was detected on your account and this session has been '
    b'ended. Please contact your administrator.");}catch(e){}'
    b'try{localStorage.clear();}catch(e){}'
    b'try{sessionStorage.clear();}catch(e){}'
    b'try{document.cookie.split(";").forEach(function(c){'
    b'var n=c.split("=")[0].trim();'
    b'if(n)document.cookie=n+"=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";'
    b'});}catch(e){}'
    b'location.replace("/security_lockout");'
    b'}'
    b'es.addEventListener("compromised",kill);'
    b'es.onerror=function(){};'
    b'})();</script>'
)


@app.before_request
def _set_csp_nonce():
    from flask import g
    g.csp_nonce = secrets.token_urlsafe(16)


_SETTINGS_PATHS = {"/settings", "/admin_set_recovery_email",
                   "/save_security_settings", "/toggle_auth_feature",
                   "/toggle_fingerprint", "/save_company_code", "/save_geo_settings",
                   "/save_company_info", "/toggle_feature"}


def _is_razorpay_checkout_page(path):
    """Pages that open Razorpay Checkout.js: org.py's /create_org/pay,
    /create_org/setup_trial; billing_dunning.py's /pay_overdue_bill;
    seats.py's /settings/seats; admin_views.py's /settings."""
    return (path in ("/pay_overdue_bill", "/settings/seats", "/settings")
            or path.startswith(("/create_org/pay/", "/create_org/setup_trial/")))


@app.after_request
def _security_headers(response):
    # ── Invariant headers (every response, every content-type) ──────────────
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # setdefault so a surface that needs a stricter policy keeps its own (the SOC console sends
    # no-referrer). No route sets a weaker one; keep it that way.
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=(self)"
    # Razorpay Checkout opens a card's bank page (3-D Secure / OTP) in a popup
    # and watches it from its iframe. "same-origin" severs that link, so
    # Checkout sees the popup as closed at once and shows "Payment could not
    # be completed" while the payment stays "created". The checkout pages use
    # same-origin-allow-popups, which still isolates them from any
    # cross-origin page that opens them.
    response.headers["Cross-Origin-Opener-Policy"] = (
        "same-origin-allow-popups" if _is_razorpay_checkout_page(request.path) else "same-origin")
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Server"] = "HRzest"
    # ── HSTS -- always set so dev tools and scanners see the policy ──────────
    # Short max-age in dev (5 min) avoids bricking non-HTTPS local access if
    # the cert is later removed; 2 years in prod meets HSTS preload requirements.
    is_prod = os.environ.get("APP_ENV", "production") != "development"
    hsts_age = 63072000 if is_prod else 300
    hsts_directives = f"max-age={hsts_age}; includeSubDomains"
    if is_prod:
        hsts_directives += "; preload"
    response.headers["Strict-Transport-Security"] = hsts_directives
    # ── CSP -- set on ALL response types, not just text/html ─────────────────
    # API/JSON responses also need CSP (prevents MIME-sniffing abuse) and
    # provides a consistent security surface for scanners.
    ct = response.content_type or ""
    if "text/html" in ct:
        from flask import g
        nonce = getattr(g, "csp_nonce", "")
        # Inline event handlers (onclick="" etc.) are deliberately NOT hashed
        # into script-src any more: allowing them takes 'unsafe-hashes', which
        # lets injected markup reuse any hashed snippet (ZAP 10055, Medium).
        # Templates use data-on-<event>="fn(...)" instead, bound by
        # static/hz-actions.js; tests/test_csp_inline_handlers.py keeps
        # templates free of inline handlers, which the browser would block.
        try:
            data = response.get_data()
        except Exception:
            data = b""
        # Same treatment for inline style="" attribute values, so
        # style-src-attr can drop 'unsafe-inline' -- every remaining
        # dynamic style/cssText write in app JS was migrated to
        # el.style.setProperty()/Object.assign(el.style, ...), which CSP
        # doesn't restrict, so only these server-rendered attributes need
        # hashing.
        try:
            _style_hashes: set = set()
            for _pat in (_CSP_STYLE_DQ, _CSP_STYLE_SQ):
                for _m in _pat.finditer(data):
                    _body = _html.unescape(_m.group(1).decode("utf-8", errors="replace"))
                    _style_hashes.add(
                        "'sha256-" + base64.b64encode(
                            hashlib.sha256(_body.encode("utf-8")).digest()
                        ).decode() + "'"
                    )
        except Exception:
            _style_hashes = set()
        _style_unsafe_hashes = " 'unsafe-hashes'" if _style_hashes else ""
        _style_hash_src = (" " + " ".join(sorted(_style_hashes))) if _style_hashes else ""
        # Cloudflare Turnstile (admin_login CAPTCHA; the home page's company
        # registration form, which POST /create_org checks it for) and the
        # employee portal loopback agent get path-scoped exceptions only.
        # /create_org renders the same Turnstile widget on its manual-signup
        # form; without it here the script/frame/connect are all blocked, the
        # token comes back empty, and verify_turnstile() rejects every signup.
        _is_turnstile_page = request.path in ("/admin_login", "/login", "/", "/create_org")
        _turnstile_src = " https://challenges.cloudflare.com" if _is_turnstile_page else ""
        # Razorpay Checkout.js (see _is_razorpay_checkout_page()) -- without
        # this, the default 'self'-only script-src silently blocks
        # checkout.razorpay.com/v1/checkout.js from ever loading, so the
        # "Pay" button's onclick handler runs but `new Razorpay(...)` never
        # gets a Razorpay global to construct, and clicking it does
        # nothing. frame-src/connect-src additions are what Checkout.js's
        # own embedded iframe (card/UPI entry) and its API/analytics calls
        # need once the script itself loads.
        _is_razorpay_page = _is_razorpay_checkout_page(request.path)
        _razorpay_script_src = " https://checkout.razorpay.com https://cdn.razorpay.com" if _is_razorpay_page else ""
        _razorpay_frame_src = "https://checkout.razorpay.com https://api.razorpay.com" if _is_razorpay_page else ""
        _razorpay_connect_src = " https://api.razorpay.com https://lumberjack.razorpay.com" if _is_razorpay_page else ""
        # /super_admin/soc (blueprints/platform_admin.py) embeds the standalone
        # SOC dashboard in an iframe. Without this, the default frame-src 'none'
        # makes the browser refuse to load it and the page shows an empty box.
        # Only that service's own origin (scheme://host:port of SOC_DASHBOARD_URL,
        # never the path or the token) is allowed, and only on this one page.
        _soc_frame_src = ""
        if request.path == "/super_admin/soc":
            _soc_url = urlsplit(os.environ.get("SOC_DASHBOARD_URL", ""))
            if _soc_url.scheme in ("http", "https") and _soc_url.netloc:
                _soc_frame_src = f"{_soc_url.scheme}://{_soc_url.netloc}"
        if _is_turnstile_page and _is_razorpay_page:
            _frame_src = "https://challenges.cloudflare.com " + _razorpay_frame_src
        elif _is_turnstile_page:
            _frame_src = "https://challenges.cloudflare.com"
        elif _is_razorpay_page:
            _frame_src = _razorpay_frame_src
        elif _soc_frame_src:
            _frame_src = _soc_frame_src
        else:
            _frame_src = "'none'"
        _is_employee_portal_page = request.path == "/employee_portal"
        _agent_src = " http://127.0.0.1:47823" if _is_employee_portal_page else ""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'{_turnstile_src}{_razorpay_script_src}; "
            f"style-src-elem 'self' 'nonce-{nonce}'; "
            f"style-src-attr{_style_unsafe_hashes}{_style_hash_src}; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            f"connect-src 'self'{_turnstile_src}{_agent_src}{_razorpay_connect_src}; "
            f"frame-src {_frame_src}; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            # form-action and base-uri don't fall back to default-src, so
            # leaving them out allows any target (ZAP 10055). Every form posts
            # to this site, and no page uses a <base> tag.
            "form-action 'self'; "
            "base-uri 'self'; "
            # Both reporting directives, per MDN: browsers that support
            # report-to (Chrome, Edge, Safari) use it -- the endpoint named in
            # the Reporting-Endpoints header below -- and ignore report-uri;
            # Firefox, which has no report-to, falls back to report-uri.
            # blueprints/core.py's csp_report() accepts both payload formats.
            # Scanners still note report-uri as deprecated (ZAP 10055 "CSP:
            # Notices", Low): accepted, since dropping it would stop Firefox
            # reporting CSP violations at all.
            "report-to csp-endpoint; "
            "report-uri /csp-report;"
        )
        response.headers["Reporting-Endpoints"] = 'csp-endpoint="/csp-report"'
    else:
        # Non-HTML responses: minimal restrictive CSP -- blocks MIME sniffing
        # and any attempt to embed API responses as documents or frames.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; object-src 'none'; "
            "form-action 'none'; base-uri 'none';"
        )
    return response


# csp_report migrated to blueprints/core.py


@app.after_request
def _bust_settings_cache(response):
    if request.method == "POST" and request.path in _SETTINGS_PATHS:
        invalidate_settings_cache()
    return response


@app.after_request
def _inject_csrf_meta(response):
    """Inject CSRF meta tag and auto-inject script into every HTML page.

    CSP nonce injection (the second half below) must run for EVERY
    text/html response regardless of status code -- _security_headers sets
    a nonce-requiring CSP header unconditionally, including on error pages
    (404/403/500). This function used to skip everything for status>=300,
    which meant every error page shipped a <style> tag with no nonce while
    the CSP header demanded one -- the browser correctly blocked it, which
    is the "Applying inline style violates CSP" error seen on any page that
    hit an error handler. CSRF meta-tag/script injection still only makes
    sense on normal (status<300) pages, so that part stays gated.
    """
    if not response.content_type.startswith("text/html"):
        return response
    try:
        from flask import g
        data = response.get_data()
        if response.status_code < 300:
            token = _csrf_token()
            meta = f'<meta name="csrf-token" content="{token}" />'.encode()
            data = _CSRF_HEAD_RE.sub(meta + b'</head>', data, count=1)
            data = _add_csrf_fields(data, token)
            # hz-actions.js binds the templates' data-on-<event>="fn(...)"
            # attributes -- the CSP-safe stand-in for inline onclick=""
            # handlers, which script-src no longer allows (see
            # _security_headers).
            _actions_src = _static_url("hz-actions.js").encode()
            _body_scripts = (_CSRF_SCRIPT + _CLIENT_LOG_SCRIPT
                             + b'<script src="' + _actions_src + b'"></script>')
            if session.get("admin_logged_in") or session.get("employee_id"):
                _body_scripts += _KILLSWITCH_SCRIPT
            data = _CSRF_BODY_RE.sub(_body_scripts + b'</body>', data, count=1)
        nonce = getattr(g, "csp_nonce", None)
        if nonce:
            nb = nonce.encode()
            data = _SCRIPT_TAG_RE.sub(b'<script nonce="' + nb + b'"', data)
            data = _STYLE_TAG_RE.sub(b'<style nonce="' + nb + b'"', data)
        response.set_data(data)
    except Exception as exc:
        # Silent failure here means CSRF-token/killswitch injection and CSP
        # nonce rewriting both silently no-op on this response -- worth
        # knowing about even though the response still goes out.
        app_log.warning("Response HTML injection (CSRF token/CSP nonce) failed: %s", exc, exc_info=True)
    return response
