# gunicorn.conf.py — Production WSGI server configuration
# Loaded automatically: gunicorn -c gunicorn.conf.py wsgi:application

import multiprocessing
import os

# ── Binding ──────────────────────────────────────────────────────────────────
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# ── TLS (internal nginx<->app hop only — nginx terminates the real cert) ──────
# nginx's proxy_pass talks to this backend over https:// (see nginx.conf /
# nginx.conf.template), so gunicorn must actually terminate TLS here or every
# proxied request hangs until nginx's read timeout, surfacing as 504s. Same
# cert.pem/key.pem path wsgi.py's own dev-server fallback uses.
_cert = os.environ.get("SSL_CERT_PATH", os.path.join(os.path.dirname(__file__), "cert.pem"))
_key = os.environ.get("SSL_KEY_PATH", os.path.join(os.path.dirname(__file__), "key.pem"))
if os.path.exists(_cert) and os.path.exists(_key):
    certfile = _cert
    keyfile = _key

# ── Workers ──────────────────────────────────────────────────────────────────
# gthread: multi-threaded workers — ideal for I/O-bound face recognition
# Rule of thumb: (2 × CPU cores) + 1, capped at 8 for a 4 GB VPS.
# GUNICORN_WORKERS / GUNICORN_THREADS override both -- compose.lowmem.yaml
# sets them (each worker loads its own face_recognition/dlib/OpenCV), and
# they used to be ignored (QA CONFIG-01).
workers = int(os.environ.get("GUNICORN_WORKERS") or min((multiprocessing.cpu_count() * 2) + 1, 8))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS") or 4)   # threads per worker
worker_connections = 1000

# ── Timeouts ─────────────────────────────────────────────────────────────────
# Face-recognition encoding can take 3-6 s on CPU
timeout = 120
graceful_timeout = 30
keepalive = 5

# ── Memory leak prevention ───────────────────────────────────────────────────
# Restart each worker after this many requests to prevent slow leaks from
# face_recognition / numpy / cv2 object accumulation
max_requests = 1000
max_requests_jitter = 100   # random jitter so workers don't all restart at once

# ── Logging ──────────────────────────────────────────────────────────────────
accesslog = "-"             # stdout → Docker / systemd captures it
errorlog = "-"             # stderr
loglevel = os.environ.get("LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'

# ── Process naming ───────────────────────────────────────────────────────────
proc_name = "hrms-attendance"

# ── Security ─────────────────────────────────────────────────────────────────
limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190

# ── Server hooks ─────────────────────────────────────────────────────────────


def on_starting(server):
    server.log.info("🚀 HRzest.com — Gunicorn starting")


def worker_exit(server, worker):
    server.log.info(f"Worker {worker.pid} exited cleanly")
