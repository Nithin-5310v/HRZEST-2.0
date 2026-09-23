# -*- coding: utf-8 -*-
"""
Application factory entry point for the refactored blueprint structure.

Usage:
  gunicorn wsgi:application          # production
  python wsgi.py                     # local dev (SSL-aware)

Migration status
----------------
The blueprint split (app.py's routes moved into blueprints/) is complete --
see the "Register blueprints" section below for the full list. app.py now
holds only shared setup: init_db, error handlers, before/after_request
hooks, and template filters.
"""
import os
import sys

# ── Encoding fix for Windows ──────────────────────────────────────────────────
# Runs before `from extensions import app_log` below, so there's no logger
# available yet to report a failure here to -- harmless either way (fails
# only on a stream that doesn't support .reconfigure(), e.g. Python <3.7 or
# a fully redirected/piped stdout that's already fixed-encoding).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import os as _os
from dotenv import load_dotenv
load_dotenv()

# ── Import shared extensions FIRST (no side-effects) ─────────────────────────
from extensions import app, app_log  # noqa: F401
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Path-based multi-tenancy (www.hrzest.com/<company-slug>/...) -- strips a
# recognized tenant slug into SCRIPT_NAME before Flask ever routes the
# request. See utils/tenant_routing.py for why this has to be WSGI-level
# rather than a route parameter on every blueprint.
from utils.tenant_routing import TenantPrefixMiddleware
app.wsgi_app = TenantPrefixMiddleware(app.wsgi_app)

# ── Startup DB init ───────────────────────────────────────────────────────────
with app.app_context():
    try:
        from app import init_master_db, init_db, _run_migrations_for_all_tenants
        from utils.config import load_default_shift, load_salary_rules
        init_master_db()
        init_db()
        _run_migrations_for_all_tenants()
        load_default_shift()
        load_salary_rules()
    except Exception as _e:
        app_log.warning("Startup init failed (non-fatal): %s", _e)

# ── Start the email queue worker ──────────────────────────────────────────────
import threading
from utils.email_utils import _email_queue_worker
threading.Thread(target=_email_queue_worker, daemon=True, name="email-queue-worker").start()

# ── Start the automated document-verification worker ─────────────────────────
# Drains tenant_applications rows _save_application_documents() enqueues
# (blueprints/org.py) -- unlike email_queue, tenant_applications is
# master-schema-only, so this is a single global queue, no per-tenant loop.
from utils.document_verification import _document_verification_worker
threading.Thread(target=_document_verification_worker, daemon=True, name="document-verification-worker").start()

# ── Shared app setup & blueprint registration ─────────────────────────────────
# The blueprint-split migration mentioned below is long complete -- every
# route now lives in blueprints/, registered by the single loop near the
# bottom of app.py (`for _bp in (health_bp, notifications_bp, ...):
# app.register_blueprint(_bp)`, 28 blueprints as of this writing), not
# individually here. app.py itself holds only shared setup: init_db, error
# handlers, before/after_request hooks, template filters. Importing it here
# is what actually runs that registration loop.
import app as _app_module  # noqa: F401

# ── Nightly daily report scheduler ───────────────────────────────────────────
# Wave 2 item #9 (audit): a heartbeat/dead-man's-switch per job, so a job
# that silently stops running (an exception every night, or the scheduler
# itself never starting) is discovered by a monitoring alert instead of by
# a customer noticing stale data weeks later. Set
# HEARTBEAT_URL_<JOB_ID_UPPERCASED> (e.g. HEARTBEAT_URL_DAILY_ATTENDANCE_REPORT)
# to a Healthchecks.io/Cronitor/UptimeRobot heartbeat check-in URL for any
# job you want covered -- pinged only on that job's successful completion,
# same convention as BACKUP_HEARTBEAT_URL in scripts/backup_db.sh. A job
# with no matching env var runs exactly as before, heartbeat-free.


def _with_heartbeat(job_id, fn):
    def _wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        url = _os.environ.get(f"HEARTBEAT_URL_{job_id.upper()}")
        if url:
            try:
                import urllib.request
                urllib.request.urlopen(url, timeout=10)  # nosec B310 -- URL is an operator-configured env var, not request input
            except Exception as _hb_err:
                app_log.warning("Heartbeat ping for job '%s' failed: %s", job_id, _hb_err)
        return result
    _wrapped.__name__ = getattr(fn, "__name__", job_id)
    return _wrapped


try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:
    app_log.warning("APScheduler not installed -- all 7 nightly scheduled jobs disabled. Run: pip install apscheduler")
else:
    try:
        from blueprints.daily_report import generate_and_send_daily_report
        from blueprints.auto_debit import sync_and_bill_auto_debit, reconcile_billing_records
        from blueprints.billing_dunning import check_tenant_billing
        from blueprints.disbursement import prepare_pending_disbursements
        from blueprints.trial_billing import check_trial_expirations, check_trial_ending_soon
    except ImportError as _job_import_err:
        # Deliberately NOT the same except block as "apscheduler missing"
        # above -- that used to be one try/except ImportError covering
        # both apscheduler itself AND all 5 of these job modules, so a
        # missing/broken job module (e.g. blueprints/trial_billing.py not
        # yet present on a given checkout) got silently misreported as
        # "APScheduler not installed" -- true-sounding, wrong cause, and
        # it took down all 7 jobs instead of just the one with the bad
        # import.
        app_log.error(
            "Nightly scheduler jobs disabled -- failed to import a job module (NOT an APScheduler problem): %s",
            _job_import_err, exc_info=True,
        )
    else:
        try:
            _scheduler = BackgroundScheduler(daemon=True)
            _scheduler.add_job(
                func=_with_heartbeat("daily_attendance_report", generate_and_send_daily_report),
                trigger="cron",
                hour=23, minute=59,
                id="daily_attendance_report",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_with_heartbeat("auto_debit_sync_and_bill", sync_and_bill_auto_debit),
                trigger="cron",
                hour=2, minute=0,
                id="auto_debit_sync_and_bill",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_with_heartbeat("reconcile_billing_records", reconcile_billing_records),
                trigger="cron",
                hour=2, minute=30,
                id="reconcile_billing_records",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_with_heartbeat("tenant_billing_dunning_check", check_tenant_billing),
                trigger="cron",
                hour=3, minute=0,
                id="tenant_billing_dunning_check",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_with_heartbeat("salary_disbursement_prep", prepare_pending_disbursements),
                trigger="cron",
                hour=4, minute=0,
                id="salary_disbursement_prep",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_with_heartbeat("trial_expiration_check", check_trial_expirations),
                trigger="cron",
                hour=1, minute=0,
                id="trial_expiration_check",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_with_heartbeat("trial_ending_soon_check", check_trial_ending_soon),
                trigger="cron",
                hour=0, minute=30,
                id="trial_ending_soon_check",
                replace_existing=True,
            )
            _scheduler.start()
            app_log.info("Daily report scheduler started -- fires at 23:59 every night")
            app_log.info("Auto-debit sync/billing scheduler started -- fires at 02:00 every night")
            app_log.info("Billing records reconciliation started -- fires at 02:30 every night")
            app_log.info("Tenant billing dunning check started -- fires at 03:00 every night")
            app_log.info("Salary disbursement prep scheduler started -- fires at 04:00 every night")
            app_log.info("Trial expiration check started -- fires at 01:00 every night")
            app_log.info("Trial ending-soon reminder check started -- fires at 00:30 every night")
        except Exception as _sch_err:
            app_log.warning("Scheduler failed to start: %s", _sch_err)

# ── WSGI export ───────────────────────────────────────────────────────────────
application = app   # gunicorn / uWSGI entry point

if __name__ == "__main__":
    _cert = _os.environ.get("SSL_CERT_PATH") or _os.path.join(_os.path.dirname(__file__), "cert.pem")
    _key = _os.environ.get("SSL_KEY_PATH") or _os.path.join(_os.path.dirname(__file__), "key.pem")
    # threaded=True: /api/session/risk-stream (blueprints/core.py) holds an
    # SSE connection open for ~20s, and Werkzeug's dev server is single-
    # threaded by default -- without this, one open stream blocks every
    # other request until it closes.
    if _os.path.exists(_cert) and _os.path.exists(_key):
        print("SSL cert found -- starting on https://0.0.0.0:5000")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True,  # nosec B104
                ssl_context=(_cert, _key))
    else:
        print("No cert.pem / key.pem -- starting on http://0.0.0.0:5000")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)  # nosec B104
