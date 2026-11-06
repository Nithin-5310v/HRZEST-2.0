# -*- coding: utf-8 -*-
import sys
# Runs before app_log is importable -- harmless either way (fails only on a
# stream that doesn't support .reconfigure(), e.g. Python <3.7 or a fully
# redirected/piped stdout that's already fixed-encoding). See wsgi.py's
# identical guard for the same rationale.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# When this file is run directly (`python app.py`, the local-dev entrypoint),
# Python loads it as module "__main__" -- a *different* module identity than
# "app". Every lazy `from app import init_tenant_db` (etc.) done deep inside
# a request handler (blueprints/org.py's provision_tenant() among others)
# then finds no "app" module cached, so Python re-imports this entire file
# from scratch as a second "app" module. extensions.app is the same Flask
# object either way (extensions.py is cached under its own name), so that
# second execution re-runs every @app.route/@app.context_processor/
# @app.errorhandler in this file against the one, already-serving Flask
# instance -- which Flask rejects once it's handled a single request
# ("The setup method ... can no longer be called"). Registering this
# execution under the name "app" up front makes any later `from app import
# X` resolve to this exact already-fully-loaded module instead of
# re-executing it. wsgi.py already imports this file as "app" normally, so
# it never hits this branch -- only a bare `python app.py` needs the alias.
if __name__ == "__main__":
    sys.modules.setdefault("app", sys.modules[__name__])


import os
import threading

from dotenv import load_dotenv

load_dotenv()
_HASHI_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hashi", ".env")
load_dotenv(_HASHI_ENV)

# ── Startup: warn if critical env vars are missing ──
_missing_env = [k for k in ("DB_HOST", "DB_USER", "DB_PASS", "DB_NAME") if not os.environ.get(k)]
if _missing_env:
    import warnings
    warnings.warn(
        f"Missing required environment variables: {', '.join(_missing_env)}. "
        "Copy .env.example to .env and fill in the values.",
        stacklevel=2
    )

from extensions import app, app_log, limiter, log_security_event  # noqa: E402,F401 -- re-exported (tests: app.limiter)

# ── App setup ────────────────────────────────────────────────────────────────
# Template helpers, request hooks and error handlers live in app_setup/.
# Import ORDER matters: Flask runs before_request hooks in registration order
# (and after_request hooks in reverse), and each module registers its hooks
# on the shared app as it is imported.
from app_setup import config, templating, tenant, security, csrf_csp, errors  # noqa: E402,F401

# Re-exported so existing `from app import X` call sites and tests keep working.
from app_setup.csrf_csp import _add_csrf_fields, _csrf_exempt, _CSRF_METHODS  # noqa: E402,F401
from app_setup.errors import _alert_on_error, unhandled_exception  # noqa: E402,F401
from app_setup.config import UPLOAD_FOLDER  # noqa: E402,F401

# DB setup lives in migrations/ (hand-rolled, idempotent startup migrations).
from migrations import init_db, init_master_db, init_tenant_db, _run_migrations_for_all_tenants  # noqa: E402,F401
from utils.email_utils import _email_queue_worker  # noqa: E402
from utils.client_ip import apply_proxy_fix  # noqa: E402


# ── /api/v1/ aliases ──────────────────────────────────────────────────────────
# Register every /api/<path> route also under /api/v1/<path>.  Existing mobile
# clients keep using /api/ with no changes; new integrations can start on v1.
# The view functions (and their decorators: @limiter, @api_required, etc.) are
# shared, so rate-limits and auth are identical on both prefixes.
def _register_api_v1_aliases():
    _seen = set()
    for _rule in list(app.url_map.iter_rules()):
        if not _rule.rule.startswith("/api/") or _rule.rule.startswith("/api/v"):
            continue
        _v1_rule = "/api/v1" + _rule.rule[4:]
        _vf = app.view_functions.get(_rule.endpoint)
        if _vf is None:
            continue
        _ep_v1 = "v1_" + _rule.endpoint
        if _ep_v1 in _seen:
            continue
        _seen.add(_ep_v1)
        app.add_url_rule(
            _v1_rule,
            endpoint=_ep_v1,
            view_func=_vf,
            methods=_rule.methods,
        )


# ── Self-register blueprints when app.py is the entrypoint ────────────────────
# wsgi.py and tests/conftest.py both register every blueprint on the shared
# `app` instance BEFORE importing this module (documented in conftest.py) --
# in that case core.home is already present and this is a no-op. Only a bare
# `python app.py` reaches this branch, so it's the one path where app.py must
# register the blueprints itself before _register_api_v1_aliases() runs,
# otherwise every route (including "/") would 404 and v1 aliases would be
# built from an empty url_map.
if "core.home" not in app.view_functions:
    from blueprints import register_all
    register_all(app)


# ── Billing context (flat per-employee rate) ── registered AFTER the blueprints
# on purpose: later context processors win on key clashes. ─────────────────────────────────
@app.context_processor
def inject_billing_context():
    try:
        from flask import g as _g
        from utils.plan_limits import get_tenant_employee_count, calculate_price, format_price_inr, get_per_employee_paise
        employee_count = get_tenant_employee_count(_g.tenant_db)
        monthly_bill_paise = calculate_price(employee_count)
        return dict(
            employee_count=employee_count,
            per_employee_paise=get_per_employee_paise(),
            monthly_bill_display=format_price_inr(monthly_bill_paise),
        )
    except Exception:
        return dict(employee_count=0, per_employee_paise=9900, monthly_bill_display="₹0")


_register_api_v1_aliases()

# ---------------- RUN ----------------
if __name__ == "__main__":
    init_master_db()
    init_db()
    _run_migrations_for_all_tenants()
    # wsgi.py wraps app.wsgi_app with this at import time -- running app.py
    # directly (`python app.py`) never goes through wsgi.py, so without this
    # every path-based tenant link (/<company-slug>/...) 404s: nothing ever
    # strips the slug into SCRIPT_NAME for Flask's router to see the bare
    # route underneath. Guarded so re-running this block (shouldn't happen,
    # but __main__ only executes once anyway) can't double-wrap.
    from utils.tenant_routing import TenantPrefixMiddleware
    if not isinstance(app.wsgi_app, TenantPrefixMiddleware):
        app.wsgi_app = TenantPrefixMiddleware(app.wsgi_app)
    # Same story as TenantPrefixMiddleware just above: wsgi.py wraps this
    # at import time, so running app.py directly never gets it. Without
    # it, every request tunneled through something like ngrok (testing a
    # Razorpay webhook against a local server, say) looks to Flask like it
    # came from 127.0.0.1 -- ngrok's local agent is the actual TCP peer --
    # so the WAF's rate-limit-breach auto-ban ends up banning localhost
    # itself, locking out the developer along with whatever tripped it.
    # x_for=1 trusts exactly one forwarded-for hop, matching wsgi.py's own
    # setting; harmless for plain direct localhost access, which never
    # sends the header at all.
    from werkzeug.middleware.proxy_fix import ProxyFix
    if not isinstance(app.wsgi_app, ProxyFix):
        app.wsgi_app = apply_proxy_fix(app.wsgi_app)
    # Only started here -- when app.py is run directly (`python app.py`,
    # local dev). wsgi.py (the real production entrypoint) already starts
    # this exact worker itself before importing app.py; starting it again
    # unconditionally at module level (the old behavior) meant production
    # ran two of these per process, racing on the same email_queue table
    # with no row locking -- a live duplicate-delivery bug, not a
    # hypothetical one.
    threading.Thread(target=_email_queue_worker, daemon=True, name="email-queue-worker").start()
    # Same "only started here" reasoning as email-queue-worker just above.
    from utils.document_verification import _document_verification_worker
    threading.Thread(target=_document_verification_worker, daemon=True, name="document-verification-worker").start()
    import os as _os
    from werkzeug.serving import WSGIRequestHandler

    class _QuietRequestHandler(WSGIRequestHandler):
        """Werkzeug's dev server writes its own 'Server: Werkzeug/x.x Python/x.x'
        header straight onto the socket (via BaseHTTPRequestHandler.send_response)
        regardless of what _security_headers already set on the response object --
        so the real fix has to happen here, not by adding another header.
        Overriding version_string() replaces that raw value instead of leaking
        the exact Werkzeug/Python versions (only relevant to `python app.py` dev/
        kiosk runs; the gunicorn path in wsgi.py never uses this handler)."""

        def version_string(self):
            return "AttendanceApp"

    _cert = _os.environ.get("SSL_CERT_PATH") or _os.path.join(_os.path.dirname(__file__), "cert.pem")
    _key = _os.environ.get("SSL_KEY_PATH") or _os.path.join(_os.path.dirname(__file__), "key.pem")
    # Was hardcoded to 5000 in both branches below -- gunicorn.conf.py (the
    # real production path, via wsgi.py) already reads PORT from the
    # environment, but this `python app.py` dev-server path never did,
    # making a local port collision with anything else already bound to
    # 5000 unfixable without editing source. Same env var, same default.
    _port = int(_os.environ.get("PORT", "5000"))
    # threaded=True: Werkzeug's dev server is single-threaded by default,
    # so one slow request (an upload, a report) would block every other
    # request until it finished.
    if _os.path.exists(_cert) and _os.path.exists(_key):
        # app.run(..., ssl_context=(...)) wraps the *listening* socket
        # (Werkzeug's serving.py), so every TLS handshake runs synchronously
        # inside the single accept() loop, before threaded=True's per-
        # connection threading ever kicks in. A client that completes the
        # TCP connect but stalls or never sends its ClientHello (a browser's
        # abandoned speculative/prefetch connection is enough) leaves that
        # accept() call blocked forever -- wedging the loop and freezing the
        # *entire* server for every subsequent request, not just that one
        # connection: process stays alive and CPU-idle, but even /healthz
        # times out. run_dev.py hit and fixed this exact freeze; ported here
        # so `python app.py` doesn't carry the same live bug -- the TLS wrap
        # is deferred into finish_request(), which always runs inside the
        # freshly spawned per-connection worker thread, so a stalled
        # handshake only ever ties up its own disposable thread.
        import ssl as _ssl
        from werkzeug.serving import ThreadedWSGIServer, load_ssl_context

        print(f"🔒  SSL cert found -- starting on https://0.0.0.0:{_port}")
        _tls_ctx = load_ssl_context(_cert, _key)

        class _DeferredHandshakeServer(ThreadedWSGIServer):
            def finish_request(self, request, client_address):
                request.settimeout(30)
                try:
                    request = _tls_ctx.wrap_socket(request, server_side=True)
                except (_ssl.SSLError, OSError):
                    request.close()
                    return
                super().finish_request(request, client_address)

        _srv = _DeferredHandshakeServer("0.0.0.0", _port, app, handler=_QuietRequestHandler,
                                        ssl_context=None)
        # Socket itself stays unwrapped (see finish_request above); this
        # attribute only drives wsgi.url_scheme detection and SSL-error-log
        # suppression elsewhere in werkzeug/serving.py, both of which still
        # need to know this is actually an HTTPS server.
        _srv.ssl_context = _tls_ctx
        _srv.log_startup()
        try:
            _srv.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        print(f"⚠   No cert.pem / key.pem -- starting on http://0.0.0.0:{_port}")
        print("    Fingerprint / WebAuthn requires HTTPS. Run: python scripts/generate_cert.py")
        app.run(host='0.0.0.0', port=_port, debug=False, use_reloader=False, threaded=True,  # nosec B104
                request_handler=_QuietRequestHandler)
