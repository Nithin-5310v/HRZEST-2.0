#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Pre-set environment variables for smooth local execution
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("SECRET_KEY", "dev-secret-key-12345")
# Local-only: no ClamAV instance is reachable from a bare local dev setup --
# APP_ENV=development makes utils/helpers.py's malware scan fail OPEN
# (upload allowed, warning logged) instead of fail CLOSED like it does in
# production when the scanner is unreachable.
os.environ.setdefault("APP_ENV", "development")

from wsgi import app

# Both MFA gates (admin TOTP enrollment, platform admin's emailed OTP) now
# default on via app.py/.env (MANDATORY_ADMIN_MFA / MANDATORY_PLATFORM_ADMIN_MFA)
# -- previously force-disabled here because local dev had no SMTP to deliver
# the platform-admin OTP and no authenticator app set up. Set either to
# False in .env if you need to bypass them locally again.

if __name__ == "__main__":
    # Same cert-detection pattern as wsgi.py's own __main__ block -- kept
    # in sync here since run_dev.py is a separate entrypoint that imports
    # wsgi's `app` directly rather than executing wsgi.py itself, so that
    # block never runs when launching via `python run_dev.py`.
    port = int(os.environ.get("PORT", 5000))
    _cert = os.environ.get("SSL_CERT_PATH") or os.path.join(os.path.dirname(__file__), "cert.pem")
    _key = os.environ.get("SSL_KEY_PATH") or os.path.join(os.path.dirname(__file__), "key.pem")
    _https = os.path.exists(_cert) and os.path.exists(_key)
    _scheme = "https" if _https else "http"

    print("\n" + "="*60)
    print("🚀 HRzest.com -- Development Server")
    print("="*60)
    print(f"📍 Local URLs to open in your web browser (Port {port}):")
    print(f"   • Login Page:      {_scheme}://127.0.0.1:{port}/login")
    print(f"   • Admin Dashboard: {_scheme}://127.0.0.1:{port}/admin")
    print(f"   • Employee Portal: {_scheme}://127.0.0.1:{port}/employee_portal")
    print(f"   • Health Check:    {_scheme}://127.0.0.1:{port}/healthz")
    if not _https:
        print("\n⚠   No cert.pem / key.pem found -- run `python scripts/generate_cert.py` for HTTPS.")
    print("="*60 + "\n")

    # threaded=True matters here: /api/session/risk-stream (blueprints/
    # core.py) holds an SSE connection open for ~20s, and Werkzeug's dev
    # server is single-threaded by default -- without this, one open
    # stream blocks every other request on the server until it closes,
    # which is exactly what produced the ERR_TIMED_OUT/"took too long to
    # respond" browser error.
    if _https:
        # app.run(..., ssl_context=(...)) wraps the *listening* socket with
        # ssl.SSLContext.wrap_socket(..., do_handshake_on_connect=True) --
        # Werkzeug's own serving.py, not this app. That means every TLS
        # handshake runs synchronously inside SSLSocket.accept(), which
        # socketserver's ThreadingMixIn always calls from the single main
        # accept loop -- BEFORE it spawns the per-connection worker thread.
        # threaded=True only threads request *handling*; it can't thread
        # the handshake, because the handshake happens a step earlier, in
        # the loop that hands connections out to be threaded.
        #
        # A client that completes the TCP connect but stalls or never
        # sends its ClientHello (a browser's speculative/prefetch
        # connection that gets abandoned is enough) leaves that accept()
        # call blocked forever waiting for handshake bytes that never
        # arrive. With the loop itself wedged, the server stops accepting
        # *any* new connection -- reproduced locally as: server process
        # alive and CPU-idle, but every new request (even `curl`) times
        # out during the TLS ClientHello, confirmed via a `sample` stack
        # dump showing the main thread parked in
        # _ssl__SSLSocket_do_handshake_impl -> read().
        #
        # An earlier version of this fix bounded that call with a socket
        # timeout, which self-heals the freeze after N seconds -- but a
        # visitor's browser still sees a stall (or ERR_TIMED_OUT, if its
        # own timeout is shorter) until that expires. This version removes
        # the freeze entirely: the listening socket is left unwrapped (a
        # plain accept() never blocks on a handshake), and the TLS wrap +
        # handshake is deferred into finish_request(), which
        # ThreadingMixIn always runs inside the freshly spawned
        # per-connection worker thread -- so a stalled handshake only ever
        # ties up its own disposable thread, never the shared accept loop.
        import ssl as _ssl
        from werkzeug.serving import ThreadedWSGIServer, load_ssl_context

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

        srv = _DeferredHandshakeServer("0.0.0.0", port, app, ssl_context=None)
        # Socket itself stays unwrapped (see finish_request above); this
        # attribute only drives wsgi.url_scheme detection and SSL-error-log
        # suppression elsewhere in werkzeug/serving.py, both of which still
        # need to know this is actually an HTTPS server.
        srv.ssl_context = _tls_ctx
        srv.log_startup()
        print("Press CTRL+C to quit")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
