# -*- coding: utf-8 -*-
"""Per-session risk scoring and forced-session-kill enforcement.

Real telemetry only: this accumulates a score from signals the backend can
actually observe (failed logins, injection-shaped input, privilege-escalation
attempts, rate-limit trips) against a specific *already-authenticated*
session. It does not and cannot observe anything about a user's local
network, Wi-Fi, or hardware (MAC addresses aren't exposed to a web server or
a browser page -- there's no legitimate way for a website to see either).

Enforcement is server-side and is the actual kill switch: once a session's
score crosses the threshold, every subsequent authenticated request using
that session is rejected (see auth.py's admin_required/employee_required).
The SSE stream in app.py exists only so an *already-open* browser tab finds
out immediately instead of waiting for its next click -- it is a UX nicety
on top of server-side enforcement, never a substitute for it. A client that
ignores the SSE message entirely still gets locked out on its very next
request.
"""
import os
import secrets
from database import get_db_connection
from extensions import app_log, log_security_event
from utils.async_writer import enqueue_write

_RISK_THRESHOLD = int(os.environ.get("SESSION_RISK_THRESHOLD", "50"))


def ensure_session_id(session) -> str:
    """Get-or-create the per-login correlation ID stored in the session
    cookie. Flask's default session is a signed client-side cookie with no
    server-side session-ID concept of its own, so this is generated once at
    login and used purely as the join key between "this browser's session"
    and its row in session_risk -- it is not a secret and grants no access
    on its own (the signed session cookie itself is still what's checked
    for auth on every request)."""
    if "_sid" not in session:
        session["_sid"] = secrets.token_hex(16)
    return session["_sid"]


def evaluate_session_risk(sid: str, identifier: str, attempt_type: str,
                          weight: int, event_type: str, reason: str) -> None:
    """Called from the request-handling thread. Hands the actual scoring
    off to the background writer thread (utils/async_writer.py) instead of
    touching the DB here -- same reasoning as _record_login_failure in
    utils/auth.py: this must stay fast even when it's being called
    repeatedly during an attack, which is exactly when it matters most.
    No return value on purpose -- no caller makes a same-request decision
    based on the resulting score; enforcement reads session_risk.status
    fresh on the *next* request via is_session_compromised(), independent
    of whether this specific write has landed yet.
    """
    enqueue_write(_evaluate_session_risk_db, sid, identifier, attempt_type, weight, reason)


def _evaluate_session_risk_db(sid: str, identifier: str, attempt_type: str,
                              weight: int, reason: str) -> int:
    """The actual DB write and threshold check -- runs only on the
    background writer thread. Do not call this directly from a route.

    Race-condition note: the increment is a single `UPDATE ... SET score =
    score + %s` statement, not a read-modify-write -- Postgres executes that
    atomically per row. Combined with this only ever running on one
    dedicated writer thread, two events for the same session can never
    race each other at all, let alone lose an increment.
    """
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO session_risk (sid, identifier, attempt_type, score, last_reason)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sid) DO UPDATE SET
                score = session_risk.score + EXCLUDED.score,
                last_reason = EXCLUDED.last_reason,
                updated_at = NOW()
            RETURNING score, status
            """,
            (sid, identifier, attempt_type, weight, reason[:300]),
        )
        new_score, status = cur.fetchone()
        crossed = new_score >= _RISK_THRESHOLD and status != "compromised"
        if crossed:
            cur.execute(
                "UPDATE session_risk SET status='compromised', updated_at=NOW() WHERE sid=%s",
                (sid,),
            )
        db.commit()
        cur.close()
        db.close()
    except Exception as e:
        app_log.error("evaluate_session_risk failed for sid=%s: %s", sid, e)
        return 0

    if crossed:
        # ERROR severity here does double duty via log_security_event:
        # structured log line + the admin webhook alert built earlier this
        # session (utils/alerts.py) -- the "compile an incident report and
        # notify admin" requirement, reusing the existing pipeline rather
        # than a second bespoke alert path.
        log_security_event(
            "session.compromised",
            "Session risk score crossed the kill threshold -- session force-terminated",
            level="ERROR",
            identifier=identifier, attempt_type=attempt_type,
            score=str(new_score), threshold=str(_RISK_THRESHOLD), reason=reason,
        )
    return new_score


def is_session_compromised(sid: str) -> bool:
    """Server-side check -- the actual enforcement point. Called from the
    auth decorators on every request, not just at login."""
    if not sid:
        return False
    # Answered already, in the same statement as the account checks, by
    # utils/auth.py's _prime_guard_state() for this request.
    from utils import request_guard
    cached = request_guard.cached_risk_status(sid)
    if cached is not None:
        return cached == "compromised"
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("SELECT status FROM session_risk WHERE sid=%s", (sid,))
        row = cur.fetchone()
        cur.close()
        db.close()
        return bool(row and row[0] == "compromised")
    except Exception as e:
        app_log.error("is_session_compromised check failed for sid=%s: %s", sid, e)
        # Fail open on a DB error here, not closed -- an outage in this
        # specific check must not lock every legitimate session out of the
        # whole app. The primary auth check (password/session cookie) is
        # unaffected either way.
        return False
