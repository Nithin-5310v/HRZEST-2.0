"""Serve the real app (wsgi.application) for the QA load test.

    python qa_audit/tools/loadtest/run_server.py            # http://127.0.0.1:5050

Environment comes from _env.py (process-local only: att_test, MFA off,
external services blanked). Served by waitress with 32 threads, roughly
production's 8 gunicorn workers x 4 threads, in one process because gunicorn
doesn't run on Windows. DB_POOL_MAX_CONN is raised to 40 for this process so
the single pool isn't smaller than the thread count (in production each
worker has 4 threads and its own pool of 20). Numbers are therefore
indicative of application cost per request, not a production capacity figure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
import _env  # noqa: E402,F401  -- must precede the app import

from waitress import serve  # noqa: E402

from wsgi import application  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("LT_PORT", "5050"))
    print(f"QA load-test server on http://127.0.0.1:{port} (DB_NAME={os.environ['DB_NAME']})")
    serve(application, host="127.0.0.1", port=port, threads=int(os.environ.get("LT_THREADS", "32")),
          connection_limit=1000, channel_timeout=120)
    # clear_untrusted_proxy_headers=False: waitress otherwise strips the
    # per-user X-Forwarded-For the locustfile sends, every request arrives as
    # 127.0.0.1, the shared IP trips the per-IP rate limits and the WAF bans it.
    # wsgi.py's ProxyFix(x_for=1) then applies the header, as nginx would.
    serve(application, host="127.0.0.1", port=port, threads=int(os.environ.get("LT_THREADS", "32")),
          connection_limit=1000, channel_timeout=120, clear_untrusted_proxy_headers=False)
