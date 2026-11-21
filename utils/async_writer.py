# -*- coding: utf-8 -*-
"""In-process async write queue for high-frequency security bookkeeping.

Measured problem this solves: under a brute-force flood against one
identifier, every failed attempt did a synchronous `UPDATE ... failed_count
= failed_count + 1` on the request-handling thread. Concurrent requests
serialize on that row's lock plus the connection pool (maxconn=20), and
each gunicorn worker sits blocked for the full wait. Measured with 60
concurrent failed attempts against one identifier: median per-request
latency 3.0s, max 5.8s -- long enough that every legitimate, unrelated
request would also be starved of a worker for the same window. That's the
exact failure mode asked about: security-event handling degrading
availability for real users during an attack.

Why not Celery/Redis (asked three times this session now, same answer):
this app's actual scale doesn't need a distributed broker, and critically,
adding one here would mean the enqueue step -- the thing that MUST stay
fast during a flood -- now depends on a network hop to Redis also staying
healthy under the same load. An in-process queue.Queue has zero moving
parts to fail: no socket, no broker to overload, nothing to reconnect to.
It works because this is single-process-per-worker; if this app ever runs
multiple worker PROCESSES needing state shared across them (not just
threads within one), that's the point a real broker earns its keep.

Durability tradeoff, stated plainly: this queue is in-memory and does not
survive a process crash or restart. Losing a few failed-login-counter
increments or risk-score points in that scenario is acceptable -- lockout
and session-risk scoring are defense-in-depth, not the primary auth
boundary (password verification and CSRF stay fully synchronous, never
queued). This is NOT an acceptable tradeoff for something like email
delivery, which is why email_queue stays Postgres-backed and untouched by
this module -- different durability class, deliberately different design.
"""
import queue
import threading
from extensions import app_log

# Bounded on purpose: an unbounded queue under a sustained flood just
# relocates the DoS risk from "blocked request threads" to "unbounded
# memory growth" -- still a resource-exhaustion vector, just a different
# one. At 10,000 pending writes, something is already badly wrong; drop
# and log rather than let memory grow without limit.
_MAX_QUEUE_DEPTH = 10_000
_write_queue: "queue.Queue" = queue.Queue(maxsize=_MAX_QUEUE_DEPTH)

_last_drop_log = 0.0
_DROP_LOG_INTERVAL_SECONDS = 5.0

# Test-only switch -- see set_synchronous_mode() below.
_SYNCHRONOUS_MODE = False


def set_synchronous_mode(enabled: bool = True):
    """Test-only: make enqueue_write() run its callable immediately on the
    caller's thread instead of handing it to the background writer thread.
    Called once from tests/conftest.py, never from production code.

    Two independent reasons this matters for tests specifically:
    (1) the background thread started at import time below calls
    get_db_connection() itself (via _record_login_failure_db/
    _clear_login_failures_db/_evaluate_session_risk_db) -- fully safe in
    production, where it just borrows another connection from the pool
    like any request thread, but a real hazard for any test-side mechanism
    that assumes DB access during one test happens on one thread.
    (2) even without that, a test asserting on the write's effect
    immediately after the triggering request would otherwise race the
    background thread's own queue/processing delay -- non-deterministic
    by construction, independent of anything else in the test suite.
    """
    global _SYNCHRONOUS_MODE
    _SYNCHRONOUS_MODE = enabled


def is_synchronous_mode() -> bool:
    """True under the test suite (see set_synchronous_mode). Callers that
    batch writes themselves (utils/firewall_events.py) flush at once then,
    so a test sees the row as soon as the request returns."""
    return _SYNCHRONOUS_MODE


def enqueue_write(fn, *args, **kwargs):
    """Hand a DB-writing callable off to the background writer thread.
    Never blocks the caller -- this is the entire point. On a full queue
    (sustained overload), the write is dropped rather than blocking the
    request thread, which would defeat the purpose of calling this at all.
    Rate-limits its own drop-warning so a flood can't also flood the logs.
    """
    if _SYNCHRONOUS_MODE:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            app_log.error("Synchronous test-mode write failed (%s): %s",
                          getattr(fn, "__name__", fn), e)
        return
    try:
        _write_queue.put_nowait((fn, args, kwargs))
    except queue.Full:
        global _last_drop_log
        import time as _t
        now = _t.time()
        if now - _last_drop_log > _DROP_LOG_INTERVAL_SECONDS:
            app_log.warning(
                "Security event write queue full (depth=%d) -- dropping writes. "
                "Sustained overload; investigate rather than raising the cap.",
                _MAX_QUEUE_DEPTH,
            )
            _last_drop_log = now


def _worker():
    """Single dedicated writer thread. Deliberately singular, not a pool --
    one writer processing sequentially is what eliminates the row-lock
    contention in the first place (N writers fighting over the same row is
    the original problem; 1 writer means there's never anything to fight
    over)."""
    while True:
        fn, args, kwargs = _write_queue.get()
        try:
            fn(*args, **kwargs)
        except Exception as e:
            app_log.error("Async security write failed (%s): %s",
                          getattr(fn, "__name__", fn), e)
        finally:
            _write_queue.task_done()


threading.Thread(target=_worker, daemon=True, name="security-write-worker").start()
