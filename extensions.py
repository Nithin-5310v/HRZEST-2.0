"""Shared Flask extensions — imported by blueprints to avoid circular imports."""
import os
import json
import logging
import sys
import secrets
import warnings
from flask import Flask
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pythonjsonlogger import jsonlogger
import redis as _redis_lib

# ── Logging ──────────────────────────────────────────────────────────────────
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(levelname)s %(module)s %(message)s",
    rename_fields={"asctime": "time", "levelname": "level"},
))
app_log = logging.getLogger("attendance")
if not app_log.handlers:
    app_log.addHandler(_log_handler)
app_log.setLevel(logging.INFO)
app_log.propagate = False

# ── Structured security-event logging ──────────────────────────────────────
# Distinct from routine app_log.info/warning/error calls: every event logged
# here is tagged security_event=true with a short event_type slug (e.g.
# "auth.failure", "access.denied", "validation.failure"), so a log
# pipeline/SIEM can filter and alert on these without regex-parsing
# free-text messages.
#
# Log injection note: JsonFormatter renders each call as one JSON object —
# control characters (newlines, quotes) inside any field value are escaped
# by the JSON encoder, so attacker-controlled data can't split a line into
# a second forged record the way it could against a naive plain-text
# logger. Even so, always pass variable/attacker-controlled data as `extra`
# fields rather than interpolating it into `message` itself — keeping the
# message a fixed, code-controlled string means a hostile value can never
# be mistaken for log-formatting content by anything downstream that
# treats `message` as free text, independent of the JSON-encoding safety
# net.
_SECURITY_LOG_LEVELS = {
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

# Python's LogRecord already owns these attribute names — passing any of
# them through `extra=` raises KeyError("Attempt to overwrite ... in
# LogRecord") and crashes the *caller*, not just the log call. Discovered
# the hard way: utils/helpers.py's upload validators passed filename=...,
# which collided with LogRecord's own `filename` (the source .py file of
# the log call site) and turned every rejected upload into a 500 instead
# of a clean error message. Fixed at those call sites, but also guarded
# here so no future caller can reintroduce the same crash with a
# differently-named collision (module, process, thread, ...).
_LOGRECORD_RESERVED = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
})


def log_security_event(event_type: str, message: str, level: str = "WARNING", **fields):
    """Emit a structured security-relevant log event.

    event_type: short dotted slug identifying the event class — the thing a
    SIEM rule filters on (e.g. "auth.failure", "access.denied").
    fields: structured context (identifier, reason, ...) merged into the
    JSON record. Never put attacker-controlled values into `message` —
    pass them as fields instead. Field names colliding with a reserved
    LogRecord attribute (see _LOGRECORD_RESERVED) are auto-prefixed with
    `field_` rather than raising — pick a different name at the call site
    when you see one show up prefixed like that in the logs.
    """
    try:
        from flask import request as _req
        ip, path, method = _req.remote_addr, _req.path, _req.method
    except Exception:
        ip = path = method = None
    safe_fields = {
        (f"field_{k}" if k in _LOGRECORD_RESERVED else k): v
        for k, v in fields.items()
    }
    app_log.log(
        _SECURITY_LOG_LEVELS.get(level.upper(), logging.WARNING),
        message,
        extra={"security_event": True, "event_type": event_type,
               "ip": ip, "path": path, "method": method, **safe_fields},
    )
    # ERROR/CRITICAL security events get a real-time webhook alert on top
    # of the log line — lockouts, malware detections, injection-shaped
    # input, privilege-escalation attempts, and honeypot trap hits
    # (blueprints/honeypot_routes.py logs those at CRITICAL specifically
    # so they're never mistaken for a routine application error). INFO/
    # WARNING stay log-only; paging a human for every anonymous 401 would
    # make the channel worth ignoring. Lazy import: utils/alerts.py
    # imports app_log from this module, so importing it at module load
    # time here would be circular.
    upper_level = level.upper()
    if upper_level in ("ERROR", "CRITICAL"):
        from utils.alerts import send_security_alert
        send_security_alert(event_type, message, level=upper_level,
                            ip=ip, path=path, method=method, **fields)

    # Every event (not just ERROR) also lands in the security_events table,
    # queryable by the SOC dashboard — the log stream above is for
    # tail/grep/SIEM ingestion, this is for "a SOC analyst opens a page and
    # sees recent events" without needing log-file access. Async via the
    # same background-writer queue as session-risk/lockout writes, so a
    # burst of events (e.g. a brute-force flood) can't add DB-write latency
    # to the request thread that's already under load. Lazy import for the
    # same circularity reason as utils.alerts above: utils/async_writer.py
    # imports app_log from this module.
    from utils.async_writer import enqueue_write
    enqueue_write(_persist_security_event, event_type, level.upper(), message,
                  fields.get("identifier"), ip, path, method,
                  {k: v for k, v in fields.items() if k != "identifier"})


def _persist_security_event(event_type, level, message, identifier, ip, path, method, extra_fields):
    """Runs on the background writer thread only — see utils/async_writer.py.
    Never let a persistence failure here affect the caller; log_security_event
    has already done its real job (the log line + any webhook) by the time
    this runs."""
    from database import get_db_connection
    try:
        db = get_db_connection()
        cur = db.cursor()
        try:
            cur.execute(
                "INSERT INTO security_events (event_type, level, message, identifier, ip, path, method, extra_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (event_type[:80], level[:10], message[:500], identifier, ip, path, method,
                 json.dumps(extra_fields, default=str)[:4000] if extra_fields else None),
            )
            db.commit()
        finally:
            cur.close()
            db.close()
    except Exception as e:
        app_log.error("Failed to persist security event to DB: %s", e)


def check_secret_key_configured(app_env, secret_key_env_value, logger=None):
    """Pure-ish (env values passed in, not read internally) so this can be
    unit-tested directly without reloading this module or its side effects
    -- see tests/test_production_safety_checks.py, and
    check_production_safety() below for the same pattern.

    The persisted-local-file fallback below (env var → persisted local file
    → generated once) is fine for local dev (same box, same disk, across
    restarts) but actively dangerous outside it: a container
    redeploy/replacement normally gets a fresh filesystem, so every deploy
    would silently generate a NEW secret key -- invalidating every session
    and CSRF token in flight with no error, no warning, just a wave of
    logged-out users. Worse, if two app processes ever start with different
    filesystems (e.g. mid-rollout on two hosts) before either writes the
    file, they'd sign with two different keys and reject each other's
    sessions. Fail hard instead -- same fail-secure posture as
    utils/razorpay_utils.py's module-level checks."""
    logger = logger or app_log
    if app_env == "development" or (secret_key_env_value or "").strip():
        return
    logger.critical(
        "FATAL: APP_ENV=%s but SECRET_KEY is not set -- refusing to start rather than "
        "silently falling back to a locally-generated key that won't survive a "
        "redeploy. Set SECRET_KEY (see .env.example).",
        app_env,
    )
    raise RuntimeError(
        "SECRET_KEY is not set outside of APP_ENV=development -- refusing to start (fail-secure)."
    )


check_secret_key_configured(os.environ.get("APP_ENV", "production"), os.environ.get("SECRET_KEY", ""))


def check_redis_requirement(app_env, redis_configured, require_redis=None, logger=None):
    """Pure-ish (env values passed in, not read internally), same pattern as
    check_secret_key_configured() above -- unit-tested directly without
    reloading this module, see tests/test_production_safety_checks.py.

    Without REDIS_HOST, utils/waf.py's auto-ban breach counter and Flask-
    Limiter's rate limiter both fall back to their own per-worker in-memory
    storage -- a complete, working fallback, not a stub, but under N
    gunicorn workers each enforces independently, so the effective ban/rate
    threshold an attacker actually has to clear is N times the configured
    one. That used to be a boot-time warning only, regardless of intent --
    this makes it an explicit choice instead: REQUIRE_REDIS defaults to
    true in production (refuse to start without Redis, same fail-secure
    posture as check_secret_key_configured's SECRET_KEY check) and false
    everywhere else, but can be set either way to override the default --
    e.g. REQUIRE_REDIS=false for a deployment that deliberately runs a
    single gunicorn worker and has no need for a shared backend.

    require_redis: None (env var unset -- use the app_env-based default),
    or a "true"/"false"-shaped string (as read straight from os.environ),
    or an actual bool (for callers/tests that already parsed it).
    """
    logger = logger or app_log
    if isinstance(require_redis, str):
        effective_require = require_redis.strip().lower() not in ("false", "0", "no")
    elif require_redis is None:
        effective_require = (app_env == "production")
    else:
        effective_require = bool(require_redis)

    if redis_configured:
        return

    if effective_require:
        logger.critical(
            "FATAL: REDIS_HOST is not set -- refusing to start. Without a shared "
            "Redis backend, the WAF auto-ban breach counter and Flask-Limiter's "
            "rate limiter both fall back to per-worker in-memory storage, "
            "silently multiplying the effective ban/rate threshold by the "
            "gunicorn worker count. Set REDIS_HOST (and REDIS_PORT/REDIS_PASSWORD "
            "if needed), or set REQUIRE_REDIS=false to intentionally boot "
            "without Redis (e.g. a deliberate single-worker deployment)."
        )
        raise RuntimeError(
            "REDIS_HOST is not set and REQUIRE_REDIS is not false -- refusing to "
            "start (fail-secure). Set REDIS_HOST, or set REQUIRE_REDIS=false to "
            "opt out."
        )
    logger.warning(
        "REDIS_HOST is not set (REQUIRE_REDIS=false) -- booting anyway. The WAF "
        "auto-ban breach counter and the rate limiter will both run in "
        "per-worker, in-memory-only mode: fine for a single gunicorn worker, but "
        "under multiple workers each enforces independently, multiplying the "
        "effective ban/rate threshold by the worker count."
    )


check_redis_requirement(os.environ.get("APP_ENV", "production"),
                        bool(os.environ.get("REDIS_HOST", "").strip()),
                        require_redis=os.environ.get("REQUIRE_REDIS"))


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")

# Without this, Werkzeug's send_file() defaults max_age to None, which makes
# it emit "Cache-Control: no-cache" on every static asset -- forcing the
# browser to round-trip a revalidation request to the server for every CSS/
# JS/font file (tabler-icons.min.css alone is ~200KB) on every single page
# navigation, even though nothing changed. This was the actual cause behind
# "clicking between sidebar modules feels slow": each full-page nav was
# re-fetching ~5-8 static assets from the server instead of the browser's
# own cache. 1 hour balances that against static/*.min.* having no
# cache-busting (hashed filenames or ?v= query params) -- a deploy that
# changes them takes up to this long to reach an already-open browser tab.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

# Secret key: env var → persisted local file (dev-only, see
# check_secret_key_configured() above) → generated once
_env_key = os.environ.get("SECRET_KEY", "").strip()
if _env_key:
    app.secret_key = _env_key
else:
    _key_file = os.path.join(os.path.dirname(__file__), "hashi", ".secret_key")
    if os.path.exists(_key_file):
        with open(_key_file) as _f:
            app.secret_key = _f.read().strip()
    else:
        app.secret_key = secrets.token_hex(32)
        os.makedirs(os.path.dirname(_key_file), exist_ok=True)
        with open(_key_file, "w") as _f:
            _f.write(app.secret_key)
        try:
            os.chmod(_key_file, 0o600)
        except Exception as exc:
            # Leaves the freshly-written secret-key file at whatever the
            # default umask permissions are (potentially group/world
            # readable) -- security-relevant enough to always report, not
            # just a cosmetic failure (Windows filesystems commonly hit
            # this since chmod's bit semantics don't map cleanly there).
            app_log.warning("Could not chmod %s to 0600: %s", _key_file, exc, exc_info=True)

app.config["SESSION_COOKIE_HTTPONLY"] = True   # blocks document.cookie read via injected/XSS'd JS
# Secure flag is always True — browsers exempt 127.0.0.1/localhost from the
# Secure requirement (RFC 6265bis §5.2.7), so local dev still works over
# plain HTTP while the flag is present for every environment. Leaving it
# False in dev caused scanners (including our own security_scan.py) to flag
# it as a gap because the Set-Cookie header never contains Secure locally.
app.config["SESSION_COOKIE_SECURE"] = True
# never attached to a cross-site request — closes CSRF via cookie-riding
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
app.config["SESSION_COOKIE_PATH"] = "/"    # explicit for clarity; Flask's own default, stated rather than implied
# SESSION_COOKIE_DOMAIN deliberately left unset: omitting it makes the
# cookie host-only (RFC 6265) — it's sent ONLY to this exact hostname, not
# to sibling subdomains. Setting an explicit Domain here would WIDEN scope
# (e.g. exposing it to every *.example.com subdomain), the opposite of a
# security "limit" despite Domain sounding like a restriction.
app.config["PERMANENT_SESSION_LIFETIME"] = 28800  # 8 hours absolute ceiling — utils/session_risk.py's kill switch and
# app.py's _enforce_idle_timeout handle inactivity separately

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB — guards direct access without nginx
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ── CORS ──────────────────────────────────────────────────────────────────────
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "").strip()
_app_env = os.environ.get("APP_ENV", "production")
if not _raw_origins:
    if _app_env != "development":
        app_log.critical(
            "ALLOWED_ORIGINS is not configured — CORS set to deny-all for /api/*. "
            "Set ALLOWED_ORIGINS=https://yourdomain.com in .env."
        )
        warnings.warn(
            "ALLOWED_ORIGINS is unset in production — CORS set to deny-all for /api/*.",
            stacklevel=1,
        )
    _allowed_origins = "*" if _app_env == "development" else []
elif _raw_origins == "*":
    if _app_env != "development":
        # CRITICAL (not just WARNING) -- this is a live CORS hole, not a
        # cosmetic misconfiguration, so it belongs at the same severity as
        # the ALLOWED_ORIGINS-unset case above rather than something a log
        # pipeline might filter out at WARNING level.
        app_log.critical(
            "ALLOWED_ORIGINS='*' in production allows all origins for /api/*. "
            "Restrict it to your domain(s)."
        )
        warnings.warn("ALLOWED_ORIGINS='*' in production — all origins allowed.", stacklevel=1)
    _allowed_origins = "*"
else:
    _allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
CORS(app, resources={r"/api/*": {"origins": _allowed_origins}})


# ── Production-safety startup checks ───────────────────────────────────────────
# Deliberately warn-loudly rather than refuse to boot: a hard crash on an
# already-running production deployment (e.g. this check shipping in a
# routine deploy) is a worse outage than a misconfigured-but-running app,
# and app_log.critical() above already writes to stdout unconditionally
# (see the StreamHandler setup at the top of this file), so it can't be
# silently swallowed the way a log line at a filtered level could.
def check_production_safety(app_env, clamav_host, malware_scan_enabled=True,
                            ai_assistant_configured=True,
                            db_sslmode="require", logger=None):
    """Pure-ish (env values passed in, not read internally) so this can be
    unit-tested directly without reloading this module or its side effects
    (CORS registration, rate limiter, etc.) against the shared Flask app
    instance -- see tests/test_production_safety_checks.py. Called once
    below with the real environment at import time."""
    logger = logger or app_log
    if app_env == "development":
        return
    # CLAMAV_HOST defaults to "clamav" (the compose.yaml service name) --
    # in a non-containerized production deploy that was never overridden,
    # every single document/photo upload silently fails closed (rejected)
    # the moment the first admin or employee tries one (utils/helpers.py's
    # _scan_for_malware(), which fails closed whenever APP_ENV != "development"
    # and the scanner is unreachable) -- but nothing says so until then. This
    # surfaces that risk at boot instead of leaving it to be discovered as a
    # confusing support ticket. Skipped entirely if malware scanning was
    # deliberately turned off (MALWARE_SCAN_ENABLED=false) -- that's a clean,
    # intentional choice, not a misconfiguration to warn about.
    if malware_scan_enabled and not clamav_host:
        logger.critical(
            "APP_ENV=production but CLAMAV_HOST is not set (defaulting to 'clamav', the "
            "compose.yaml service name). If no ClamAV instance is actually reachable at "
            "that host, EVERY document/photo upload will be rejected (utils/helpers.py's "
            "_scan_for_malware() fails closed outside development). Set CLAMAV_HOST to a "
            "reachable ClamAV instance, or set MALWARE_SCAN_ENABLED=false if this "
            "deployment intentionally runs without malware scanning."
        )
    # Unlike the ClamAV check above, an unconfigured AI assistant fails OPEN,
    # not closed: utils/ai_assistant.py's ask_assistant() just returns a
    # friendly "isn't configured yet" message forever -- nothing crashes,
    # nothing 500s, no upload gets rejected. That's exactly why it needs a
    # boot-time warning instead of relying on someone noticing: there's no
    # error to trip an alert on, just a chat feature that quietly never
    # works for any employee, indefinitely, until an admin happens to try it
    # themselves. Not a fail-secure case like Razorpay's test-key-in-
    # production check (utils/razorpay_utils.py) -- there's no unsafe
    # fallback behavior to refuse here, just a missing feature -- so this
    # warns rather than raising/refusing to start.
    if not ai_assistant_configured:
        logger.critical(
            "APP_ENV=production but no AI assistant backend is configured (none of "
            "N8N_WEBHOOK_URL, GEMINI_API_KEY, ANTHROPIC_API_KEY is set). The HR "
            "Assistant chat on the employee portal will silently show every employee "
            "the same \"isn't configured yet\" message instead of answering. Set one "
            "of those three env vars, or ignore this if the assistant is "
            "intentionally disabled for this deployment."
        )
    # database.py's DB_SSLMODE defaults to "prefer" -- it encrypts the
    # connection whenever the server supports TLS but silently degrades to
    # plaintext otherwise, with nothing here able to tell which happened
    # from an env var alone. RDS always supports TLS, so a production
    # deployment left on the default gets encryption today but has no
    # actual guarantee of it -- a misconfigured security group, a
    # non-RDS Postgres, or a downgrade attack would all fail open to
    # plaintext instead of failing the connection. "require" (or stricter:
    # verify-ca/verify-full with DB_SSLROOTCERT) makes that a hard
    # connection failure instead of a silent downgrade. Warns rather than
    # refusing to start, same as the Redis check above -- an operator who
    # already made this tradeoff deliberately (e.g. a private-network-only
    # Postgres with no TLS configured at all) shouldn't be blocked from
    # booting over it.
    if db_sslmode not in ("require", "verify-ca", "verify-full"):
        logger.critical(
            "APP_ENV=production but DB_SSLMODE=%s (default is 'prefer', which "
            "silently falls back to a plaintext DB connection if TLS isn't "
            "available, instead of failing). Set DB_SSLMODE=require (or "
            "verify-full with DB_SSLROOTCERT pointing at the RDS CA bundle) "
            "for a production Postgres connection, or ignore this if this "
            "deployment intentionally has no TLS between app and database "
            "(e.g. both on the same private network with no other option).",
            db_sslmode,
        )


_malware_scan_enabled_at_boot = os.environ.get("MALWARE_SCAN_ENABLED", "true").strip().lower() not in ("false", "0", "no")
_ai_assistant_configured_at_boot = bool(
    os.environ.get("N8N_WEBHOOK_URL", "").strip()
    or os.environ.get("GEMINI_API_KEY", "").strip()
    or os.environ.get("ANTHROPIC_API_KEY", "").strip()
)
check_production_safety(_app_env, os.environ.get("CLAMAV_HOST"), _malware_scan_enabled_at_boot,
                        _ai_assistant_configured_at_boot,
                        db_sslmode=os.environ.get("DB_SSLMODE", "prefer").strip())


# ── Redis (optional shared cache — rate limiter + WAF auto-ban counters) ──────
# PostgreSQL remains the only durable datastore this app runs; Redis here is
# a pure cache with no persistence configured (compose.yaml disables RDB/AOF
# for it) — losing its contents on restart just resets counters, not data.
#
# Opt-in via REDIS_HOST. Unset (the default for local dev, tests, and the
# low-memory compose profile) keeps the previous behavior exactly: the rate
# limiter falls back to Flask-Limiter's in-memory storage and utils/waf.py's
# auto-ban counter falls back to its own in-memory dict — both per-worker,
# not shared across gunicorn workers, which is a real limitation under
# multiple workers (each worker enforces independently, so the effective
# ceiling is limit * worker_count) but a complete, working fallback, not a
# stub. Set REDIS_HOST (docker-compose's `redis` service, or any reachable
# Redis) to make both shared and persistent-across-restarts-of-a-single-
# worker instead. A short ping at startup decides which mode wins — a
# misconfigured/unreachable REDIS_HOST degrades to the in-memory fallback
# with a logged warning rather than failing app startup.
def _init_redis_backend():
    """Returns (redis_client_or_None, limiter_storage_uri). Split out from
    module scope so tests can exercise the fallback logic directly instead
    of needing to reload this whole module with different env vars."""
    host = os.environ.get("REDIS_HOST", "").strip()
    if not host:
        return None, "memory://"
    port = int(os.environ.get("REDIS_PORT", "6379"))
    password = os.environ.get("REDIS_PASSWORD", "").strip() or None
    try:
        client = _redis_lib.Redis(
            host=host, port=port, password=password,
            socket_connect_timeout=2, socket_timeout=2,
        )
        client.ping()
    except Exception as e:
        app_log.warning(
            "REDIS_HOST=%s set but unreachable (%s) — rate limiter and WAF "
            "auto-ban counter falling back to per-worker in-memory storage.",
            host, e,
        )
        return None, "memory://"
    auth = f":{password}@" if password else ""
    return client, f"redis://{auth}{host}:{port}/0"


redis_client, _limiter_storage_uri = _init_redis_backend()

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=_limiter_storage_uri,
    default_limits=["300 per minute"],
)
