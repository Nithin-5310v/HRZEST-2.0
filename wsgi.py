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
from utils.client_ip import apply_proxy_fix
# TRUSTED_PROXY_HOPS (default 1 = nginx) -- see utils/client_ip.py
app.wsgi_app = apply_proxy_fix(app.wsgi_app)

# Path-based multi-tenancy (www.hrzest.com/<company-slug>/...) -- strips a
# recognized tenant slug into SCRIPT_NAME before Flask ever routes the
# request. See utils/tenant_routing.py for why this has to be WSGI-level
# rather than a route parameter on every blueprint.
from utils.tenant_routing import TenantPrefixMiddleware
app.wsgi_app = TenantPrefixMiddleware(app.wsgi_app)

# ── Startup DB init ───────────────────────────────────────────────────────────
# Once per deploy, one worker at a time -- see migrations/runner.py (QA SCALE-02).
with app.app_context():
    try:
        from app import init_master_db, init_db, _run_migrations_for_all_tenants
        from migrations.runner import run_startup_migrations
        run_startup_migrations(init_master_db, init_db, _run_migrations_for_all_tenants)
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
# app.register_blueprint(_bp)`, 29 blueprints as of this writing), not
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


def _scheduled_job(job_id, fn):
    # Every gunicorn worker runs this scheduler; once_per_fire makes only
    # one of them actually run each nightly job (QA SCALE-01). It wraps the
    # heartbeat, so only the worker that ran the job pings.
    from utils.job_runs import once_per_fire
    return once_per_fire(job_id, _with_heartbeat(job_id, fn))


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
        from utils.detections import run_detections
        from utils.privacy_jobs import run_retention_purge
        from utils.audit_jobs import emit_anchors, run_full_verification
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
                func=_scheduled_job("daily_attendance_report", generate_and_send_daily_report),
                trigger="cron",
                hour=23, minute=59,
                # End of the company's day, not the server's: the container
                # runs UTC, which fired this at 05:29 IST (QA BUG-26).
                # Asia/Kolkata is the company-timezone default.
                timezone="Asia/Kolkata",
                id="daily_attendance_report",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_scheduled_job("auto_debit_sync_and_bill", sync_and_bill_auto_debit),
                trigger="cron",
                hour=2, minute=0,
                id="auto_debit_sync_and_bill",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_scheduled_job("reconcile_billing_records", reconcile_billing_records),
                trigger="cron",
                hour=2, minute=30,
                id="reconcile_billing_records",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_scheduled_job("tenant_billing_dunning_check", check_tenant_billing),
                trigger="cron",
                hour=3, minute=0,
                id="tenant_billing_dunning_check",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_scheduled_job("salary_disbursement_prep", prepare_pending_disbursements),
                trigger="cron",
                hour=4, minute=0,
                id="salary_disbursement_prep",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_scheduled_job("trial_expiration_check", check_trial_expirations),
                trigger="cron",
                hour=1, minute=0,
                id="trial_expiration_check",
                replace_existing=True,
            )
            _scheduler.add_job(
                func=_scheduled_job("trial_ending_soon_check", check_trial_ending_soon),
                trigger="cron",
                hour=0, minute=30,
                id="trial_ending_soon_check",
                replace_existing=True,
            )
            # Every worker runs this scheduler, so every job goes through
            # _scheduled_job's once-per-minute claim (QA SCALE-01). The
            # repeating jobs fire on the clock ("*/N" minutes) rather than N
            # minutes after each worker booted, so all workers fire in the
            # same minute and the claim leaves exactly one to run.
            # Correlation rules over security_events/audit_logs/punch_evidence
            # (utils/detections.py); a per-(rule,key) cooldown claim also keeps
            # each finding to one alert.
            _scheduler.add_job(
                func=_scheduled_job("security_detections", run_detections),
                trigger="cron",
                minute=f"*/{int(_os.environ.get('DETECTIONS_INTERVAL_MINUTES', '5'))}",
                id="security_detections",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            # Audit-chain head anchors -> stdout -> Promtail -> Loki (off-box).
            _scheduler.add_job(
                func=_scheduled_job("audit_anchor", emit_anchors),
                trigger="cron",
                minute=f"*/{int(_os.environ.get('AUDIT_ANCHOR_INTERVAL_MINUTES', '10'))}",
                id="audit_anchor",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            _scheduler.add_job(
                func=_scheduled_job("audit_chain_verify", run_full_verification),
                trigger="cron",
                hour=3, minute=45,
                id="audit_chain_verify",
                replace_existing=True,
            )
            # Personal-data retention (utils/privacy.py): check-in face photos,
            # punch evidence, spent challenges.
            _scheduler.add_job(
                func=_scheduled_job("privacy_retention_purge", run_retention_purge),
                trigger="cron",
                hour=3, minute=30,
                id="privacy_retention_purge",
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
    # threaded=True: Werkzeug's dev server is single-threaded by default,
    # so one slow request (an upload, a report) would block every other
    # request until it finished.
    if _os.path.exists(_cert) and _os.path.exists(_key):
        print("SSL cert found -- starting on https://0.0.0.0:5000")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True,  # nosec B104
                ssl_context=(_cert, _key))
    else:
        print("No cert.pem / key.pem -- starting on http://0.0.0.0:5000")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)  # nosec B104
