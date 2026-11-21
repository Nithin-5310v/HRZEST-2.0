# -*- coding: utf-8 -*-
"""Server-side data cache for each module's read-only JSON routes.

Every blueprint's GET data routes (the lists, dashboards and reports the
mobile app and the pages' fetch() calls read) carry ``@data_cache("<module>")``.
A repeat of the same request by the same signed-in person, within the TTL and
with no write in between, is answered from Redis instead of the database.

Security properties -- these are the point, not extras:

* **Under the auth guards.** The decorator sits directly on the view, below
  admin_required / api_required / session_or_bearer_required etc., so every
  request still passes the login, kill-switch, deactivation, role and module
  checks before the cache is even consulted.
* **Keyed per company and per person.** The key covers the tenant schema,
  the exact path and query string, and who is asking: session account, role,
  base type, active company, employee id, and the API token's identity. Two
  people never share an entry, so team scope and ownership checks done inside
  the view are preserved.
* **Encrypted at rest.** Entries are AES-256-GCM encrypted with the same key
  as PII columns (utils/pii_crypto.py); a value that fails to decrypt is a miss.
* **Never stale after a write.** Any successful POST/PUT/PATCH/DELETE in a
  company bumps that company's cache generation, which retires every entry at
  once; the TTL (DATA_CACHE_TTL_SECONDS, default 60) bounds anything changed
  outside a request (scheduled jobs).
* **Only clean 200 JSON is stored** -- never errors, redirects, files or
  responses that set a cookie.
* **Off without Redis.** With no shared Redis, a per-worker cache would serve
  stale data after a write handled by another worker, so caching is disabled
  instead. DATA_CACHE_ENABLED=0 turns it off explicitly.
* **Browsers still get no-store** (app_setup/cache_control.py): this cache
  lives only on the server.

Routes that write, record an audit read (e.g. salary views) or stream are
deliberately not decorated: a cached hit would skip the write or the audit
entry. tests/test_data_cache.py checks that list.
"""
import hashlib
import os
from functools import wraps

from flask import Response, g, has_request_context, make_response, request, session

from extensions import app, app_log

_KEY_PREFIX = "dcache:v1"
_GEN_PREFIX = "dcache:gen"
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _ttl():
    try:
        return max(1, min(int(os.environ.get("DATA_CACHE_TTL_SECONDS", "60")), 3600))
    except ValueError:
        return 60


def _redis():
    import extensions
    return getattr(extensions, "redis_client", None)


def enabled():
    if os.environ.get("DATA_CACHE_ENABLED", "1").strip().lower() in ("0", "false", "off", "no"):
        return False
    return _redis() is not None


def _tenant():
    return getattr(g, "tenant_db", None) or "__no_tenant__"


def _generation(r, tenant):
    raw = r.get(f"{_GEN_PREFIX}:{tenant}")
    if isinstance(raw, bytes):
        raw = raw.decode()
    return raw or "0"


def invalidate_tenant(tenant=None):
    """Retire every cached entry for one company (the current one by default)."""
    r = _redis()
    if r is None:
        return
    try:
        r.incr(f"{_GEN_PREFIX}:{tenant or _tenant()}")
    except Exception as exc:
        app_log.warning("data cache: invalidation failed: %s", type(exc).__name__)


def _actor_parts():
    """Everything that decides what this caller may see."""
    return (
        session.get("admin_logged_in"), session.get("admin_username"), session.get("admin_role"),
        session.get("admin_base_type"), session.get("active_company_id"), session.get("employee_id"),
        session.get("platform_admin_username"),
        getattr(g, "auth_identity", None), getattr(g, "auth_kind", None),
        getattr(g, "api_user", None), getattr(g, "api_emp_id", None),
    )


def _key(namespace, generation):
    material = "|".join(str(p) for p in (
        _tenant(), generation, namespace, request.method, request.full_path, *_actor_parts()))
    return f"{_KEY_PREFIX}:{_tenant()}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _encrypt(text):
    from utils.helpers.crypto import _pii_cipher
    return _pii_cipher.encrypt(text)


def _decrypt(value):
    from utils import pii_crypto
    from utils.helpers.crypto import _pii_cipher
    if isinstance(value, bytes):
        value = value.decode()
    return pii_crypto.decode_plaintext(_pii_cipher.decrypt(value))


def data_cache(namespace, ttl=None):
    """Cache this GET JSON view's 200 responses (see the module docstring).
    Place it directly above ``def``, under every auth decorator."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if request.method != "GET" or not enabled():
                return view(*args, **kwargs)
            r = _redis()
            key = None
            try:
                key = _key(namespace, _generation(r, _tenant()))
                raw = r.get(key)
                if raw:
                    return Response(_decrypt(raw), status=200, mimetype="application/json")
            except Exception as exc:
                # Any cache problem (Redis down, bad entry) just means "miss".
                app_log.debug("data cache read skipped: %s", type(exc).__name__)
            resp = make_response(view(*args, **kwargs))
            if (key and resp.status_code == 200 and resp.mimetype == "application/json"
                    and not resp.direct_passthrough and "Set-Cookie" not in resp.headers):
                try:
                    r.setex(key, ttl or _ttl(), _encrypt(resp.get_data(as_text=True)))
                except Exception as exc:
                    app_log.debug("data cache write skipped: %s", type(exc).__name__)
            return resp
        wrapper._hrz_data_cache = namespace
        return wrapper
    return decorator


@app.after_request
def _invalidate_after_write(response):
    """A successful write anywhere in a company retires that company's cache."""
    if (has_request_context() and request.method in _WRITE_METHODS and response.status_code < 400
            and enabled()):
        invalidate_tenant()
    return response
