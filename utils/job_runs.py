"""Run each scheduled job once per scheduled time, however many processes fire it.

wsgi.py starts APScheduler in every gunicorn worker (and every app server),
so each nightly job fires once per process. Before running, a job claims
(job id, scheduled minute) in att_master.scheduler_job_runs; the primary
key lets exactly one claim succeed and every other process skips that run
(QA SCALE-01). This is sturdier than electing one scheduler with an
advisory lock: a fast job can finish before another worker even tries the
lock, and a leader that loses its DB connection loses the lock silently.
"""
import datetime
import functools

from database import get_master_db
from extensions import app_log


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def claim_run(job_id, fire_minute):
    """True if this process won the right to run `job_id` for `fire_minute`."""
    db = get_master_db()
    try:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO scheduler_job_runs (job_id, fire_minute) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING RETURNING job_id",
            (job_id, fire_minute),
        )
        won = cur.fetchone() is not None
        db.commit()
        cur.close()
        return won
    finally:
        db.close()


def once_per_fire(job_id, fn):
    """Wrap a scheduled job so only one process runs it per scheduled minute.
    If the claim can't be made (master DB down), the run is skipped: a
    missed run shows up in the job's heartbeat monitoring, a duplicate
    payslip or invoice email doesn't."""
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        fire_minute = _utc_now().replace(second=0, microsecond=0)
        try:
            won = claim_run(job_id, fire_minute)
        except Exception as exc:
            app_log.error("Scheduled job %s skipped: could not claim the %s run: %s", job_id, fire_minute, exc)
            return None
        if not won:
            app_log.info("Scheduled job %s for %s is running in another process", job_id, fire_minute)
            return None
        return fn(*args, **kwargs)
    return _wrapped
