# -*- coding: utf-8 -*-
"""helpers: tenant-aware paths, static asset cache-busting, safe redirects"""
import hashlib
import os
import threading
from extensions import app_log
from flask import request, session

# utils/helpers/urls.py -> repo root is three levels up.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def tpath(path: str) -> str:
    """Prefix an absolute-path link/redirect target with the current
    tenant's URL prefix (request.script_root -- "" on marketing/platform-
    admin routes, "/<company-slug>" once the WSGI tenant-prefix wrapper in
    wsgi.py has stripped that slug into SCRIPT_NAME for a resolved tenant
    request). Used in place of bare redirect("/x")/href="/x" everywhere a
    link should stay within the current tenant's path, since this codebase
    builds links as literal absolute-path strings rather than via
    url_for(), which would pick up SCRIPT_NAME automatically.

    Idempotent by design: some inputs (e.g. a path pulled out of the raw
    Referer header in _safe_referrer_redirect, which reflects the real
    browser URL the visitor was on) already carry the tenant prefix, while
    most call sites pass a bare, unprefixed literal like "/admin" -- rather
    than track which is which at every call site, tpath() just checks
    whether the prefix is already there before adding it."""
    if not path.startswith("/"):
        return path
    try:
        prefix = request.script_root
    except RuntimeError:
        return path  # no request context (e.g. called from a script/test)
    if not prefix or path == prefix or path.startswith(prefix + "/"):
        return path
    return prefix + path


def self_service_emp_id():
    """The employee_id a self-service write action (apply leave, raise a
    ticket, update my profile, ...) should act on -- for a plain employee
    session that's session['employee_id'] as always; for a role-holder's
    admin session (e.g. an HR/manager viewing their OWN leave/attendance
    inside my_workspace.html, no session switch involved) it's
    session['admin_username'], which IS their employee_id. The owner admin's
    username isn't an employee: theirs is the profile linked to the login
    (utils/own_profile.py), or None until they create one. Returns None if
    there's no such employee (caller should redirect to login)."""
    if session.get("employee_id"):
        return session.get("employee_id")
    if session.get("admin_logged_in"):
        from utils.own_profile import OWNER_ROLE, session_linked_employee_id
        if session.get("admin_role") == OWNER_ROLE:
            return session_linked_employee_id()
        return session.get("admin_username")
    return None


def self_service_return_path(suffix: str = "") -> str:
    """Where a self-service write action should redirect back to once it's
    done: /my_workspace for a role-holder's admin session (so they land back
    in their own dashboard, not a page their session can't open), /
    employee_portal for a plain employee session. suffix is appended as-is
    (e.g. "#tickets") -- pass it already formatted."""
    base = "/my_workspace" if session.get("admin_logged_in") else "/employee_portal"
    return tpath(base + suffix)


# ── Static asset cache-busting ────────────────────────────────────────────────
# filename -> (mtime, hash) -- lets static_url() below skip re-hashing a file
# on every request and only pay that cost the first time a filename is seen
# or after the file has actually changed on disk.
_STATIC_ASSET_CACHE = {}
_STATIC_ASSET_CACHE_LOCK = threading.Lock()


def static_url(filename: str) -> str:
    """Return "/static/<filename>?v=<hash>" for a CSS/JS (or any other)
    file under static/, so a deploy that changes a file's bytes is visible
    to browsers/CDNs immediately instead of being served stale for up to
    SEND_FILE_MAX_AGE_DEFAULT (extensions.py, currently 1 hour) -- or
    indefinitely by any intermediary that ignores that header.

    Templates call this in place of a bare "/static/x.js" href/src (this
    codebase writes static links as literal strings, same as tpath() above
    -- see its docstring) or `{{ url_for('static', filename='x.js') }}`.

    The hash is an md5 of the file's actual bytes, memoized in
    _STATIC_ASSET_CACHE keyed by filename and invalidated with a cheap
    os.path.getmtime() check -- so a request only re-reads+re-hashes the
    file when it's new to the cache or has actually changed, not on every
    page render of a busy HR app.

    This mtime-keyed, in-process cache is correct today because there is
    exactly one app instance reading static/ off its own local disk, so
    "the file changed" and "mtime changed" are the same fact, and every
    request sees the same cache. It would need to change (e.g. to a
    build-time manifest file checked into the deploy, or a shared
    Redis-backed cache) if this ever runs as multiple instances behind a
    load balancer -- each process's mtime cache would only reflect files
    it happens to have re-read locally, so a client could get inconsistent
    hashes for the same file across requests -- or if static/ moves to
    S3/a CDN origin instead of local disk, where there is no local mtime
    to check at all.
    """
    from extensions import app as _app
    static_root = _app.static_folder or os.path.join(_REPO_ROOT, "static")
    path = os.path.join(static_root, filename)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return f"/static/{filename}"  # missing file -- let the resulting 404 happen with no query string

    cached = _STATIC_ASSET_CACHE.get(filename)
    if cached is None or cached[0] != mtime:
        with _STATIC_ASSET_CACHE_LOCK:
            cached = _STATIC_ASSET_CACHE.get(filename)
            if cached is None or cached[0] != mtime:
                try:
                    with open(path, "rb") as f:
                        digest = hashlib.md5(f.read(), usedforsecurity=False).hexdigest()[:10]  # cache-busting fingerprint, not security
                except OSError:
                    return f"/static/{filename}"
                cached = (mtime, digest)
                _STATIC_ASSET_CACHE[filename] = cached
    return f"/static/{filename}?v={cached[1]}"


def employee_login_url() -> str:
    """Absolute URL to the current tenant's own login page (e.g.
    "https://www.hrzest.com/acme/login") -- used in employee-welcome-email
    templates so a new hire has a clickable link straight to their
    company's portal, not just credentials with nowhere to use them.

    Built from session["tenant_slug"] rather than tpath()/request.script_root:
    the admin registering this employee may have reached the page via a
    bare, slug-less URL (an already-resolved tenant session cached from an
    earlier request -- the same edge case blueprints/core.py's home()
    documents), in which case tpath() would silently drop the slug and
    hand a brand-new employee, who has no session of their own yet, a
    link the WSGI tenant-prefix middleware can't resolve to any company."""
    try:
        slug = session.get("tenant_slug")
        base = request.host_url.rstrip("/")
    except RuntimeError:
        return "/login"
    return f"{base}/{slug}/login" if slug else f"{base}/login"


_APP_URL = os.environ.get("APP_URL", "").rstrip("/")


def _safe_app_url() -> str:
    """Return a trusted base URL. In production APP_URL must be set -- falling
    back to request.host_url is unsafe because the Host header is attacker-controlled."""
    if _APP_URL:
        return _APP_URL
    if os.environ.get("APP_ENV", "production") != "development":
        app_log.warning(
            "APP_URL is not set in hashi/.env -- password-reset links will use the "
            "request Host header, which is unsafe in production. "
            "Set APP_URL=https://yourdomain.com in hashi/.env to fix this."
        )
    return request.host_url.rstrip("/")


def company_url(path: str) -> str:
    """Absolute link to `path` in this request's company, for emails:
    <APP_URL>/<company-slug><path>. The company is always named explicitly:
    a bare /login whose company step is done remembers the company in the
    session only, so a link built from that request's own URL would work
    only in the same browser -- opened from a mail app or another device it
    would land in no company and report its token as invalid."""
    slug = request.environ.get("hrz.tenant_slug") or session.get("tenant_slug") or ""
    return f"{_safe_app_url()}{'/' + slug if slug else ''}{path}"


def _safe_redirect(dest: str, fallback: str = "/admin") -> str:
    """Validate that a redirect target is a relative path (prevents open
    redirect), then stamp it with the current tenant's URL prefix via
    tpath() so every caller gets a correctly-scoped link for free."""
    # Browsers treat "/\evil.com" like "//evil.com" and drop tabs/newlines
    # inside URLs ("/	/evil.com"), so a backslash or control character
    # anywhere is refused, not just a leading "//".
    if (dest and dest.startswith("/") and not dest.startswith("//")
            and "\\" not in dest and not any(ord(c) < 32 or ord(c) == 127 for c in dest)):
        return tpath(dest)
    return tpath(fallback)


def _safe_referrer_redirect(referrer: str, fallback: str) -> str:
    """Like _safe_redirect, but also accepts an absolute Referer header as long
    as it points back at this same app (scheme+host), reducing it to a
    relative path first. Referer is client-supplied and can be forged by
    non-browser HTTP clients, so it's never trusted as-is."""
    if not referrer:
        return tpath(fallback)
    from urllib.parse import urlparse as _urlparse
    p = _urlparse(referrer)
    if not p.scheme and not p.netloc:
        return _safe_redirect(referrer, fallback)
    if p.netloc == request.host:
        path = p.path or "/"
        return _safe_redirect(path + (("?" + p.query) if p.query else ""), fallback)
    return tpath(fallback)
