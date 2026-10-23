"""SCALE-01: each nightly job runs once per scheduled time, not once per worker.

wsgi.py starts APScheduler in every gunicorn worker (up to 8, no
preload_app), so every nightly job -- daily report email, trial reminders,
dunning, auto-debit, disbursement prep -- ran up to 8 times. Each run now
first claims (job id, scheduled minute) in att_master.scheduler_job_runs;
only the worker whose claim succeeds runs the job.
"""
import ast
import datetime
import pathlib

import pytest

FIRE = datetime.datetime(2026, 10, 7, 23, 59, 0, 412000)


@pytest.fixture
def job(monkeypatch):
    from utils import job_runs
    clock = {"now": FIRE}
    monkeypatch.setattr(job_runs, "_utc_now", lambda: clock["now"])
    runs = []
    return job_runs.once_per_fire("qa_nightly_job", lambda: runs.append(clock["now"])), runs, clock


def test_job_runs_once_however_many_workers_fire_it(job):
    run, runs, _clock = job
    for _ in range(8):          # every gunicorn worker's scheduler fires at 23:59
        run()
    assert len(runs) == 1


def test_scheduler_jitter_within_the_minute_is_the_same_run(job):
    run, runs, clock = job
    run()
    clock["now"] = FIRE + datetime.timedelta(milliseconds=900)
    run()
    assert len(runs) == 1


def test_next_scheduled_time_runs_again(job):
    run, runs, clock = job
    run()
    clock["now"] = FIRE + datetime.timedelta(days=1)
    run()
    assert len(runs) == 2


def test_a_failed_claim_skips_rather_than_risking_a_duplicate(job, monkeypatch):
    from utils import job_runs
    run, runs, _clock = job

    def _db_down():
        raise RuntimeError("master DB unreachable")

    monkeypatch.setattr(job_runs, "get_master_db", _db_down)
    run()
    assert runs == []


def test_every_scheduled_job_in_wsgi_goes_through_the_claim():
    tree = ast.parse(pathlib.Path(__file__).resolve().parents[1].joinpath("wsgi.py").read_text(encoding="utf-8"))
    add_job_calls = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_job"]
    assert add_job_calls
    for call in add_job_calls:
        kw = {k.arg: k.value for k in call.keywords}
        func = kw["func"]
        assert isinstance(func, ast.Call) and getattr(func.func, "id", "") == "_scheduled_job", ast.unparse(func)
        # The claim is per minute: an "interval" trigger fires N minutes after
        # each worker booted, at a different minute per worker, so the claim
        # couldn't stop the duplicates. Repeating jobs use cron "*/N" instead.
        trigger = kw.get("trigger")
        assert not (isinstance(trigger, ast.Constant) and trigger.value == "interval"), ast.unparse(func)
