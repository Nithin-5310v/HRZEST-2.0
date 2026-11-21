"""Background writes (utils/async_writer.py) must land in the schema of the
request that queued them.

The writer thread runs outside any request, so database.get_db_connection()
there used to fall back to "public": a failed sign-in on a company's URL was
counted in public.login_attempts while the lockout check read the company's
own table (so company logins never locked), a company session's risk score
never reached the company's session_risk (so it was never ended), and push
tokens were looked up in the wrong place. The rest of the suite runs these
writes inline, inside the request, which is why none of that showed: these
tests use the real background thread, as production does.

Security events are the exception: they belong in public.security_events
(the SOC console and the detections read them there), whichever company a
request belonged to, and whether written inline or in the background.
"""
import secrets

import pytest
from flask import g

from extensions import app as flask_app, log_security_event
from tests.test_auth_hardening import _make_tenant
from utils import async_writer
from utils.session_risk import evaluate_session_risk, is_session_compromised


@pytest.fixture
def background(monkeypatch):
    """Queue writes for the real writer thread; call the result to wait for them."""
    monkeypatch.setattr(async_writer, "_SYNCHRONOUS_MODE", False)
    return async_writer._write_queue.join


@pytest.fixture
def company(db_engine, client):
    return _make_tenant(db_engine, "bgw" + secrets.token_hex(3))


def _one(db_engine, sql, params=()):
    cur = db_engine.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def test_failed_company_logins_are_counted_where_the_lockout_reads_them(client, company, background, db_engine):
    from utils.auth import _LOGIN_MAX_ATTEMPTS
    slug, schema = company
    for _ in range(_LOGIN_MAX_ATTEMPTS):
        client.post(f"/{slug}/login", data={"identifier": "iso_admin", "password": "not-the-password"})
    background()
    count, locked_until = _one(
        db_engine, f'SELECT failed_count, locked_until FROM "{schema}".login_attempts WHERE identifier=%s',  # nosec B608
        ("iso_admin",))
    assert count == _LOGIN_MAX_ATTEMPTS and locked_until is not None      # the company's own lockout engaged
    assert _one(db_engine, "SELECT COUNT(*) FROM public.login_attempts WHERE identifier=%s", ("iso_admin",))[0] == 0
    # and it is enforced: the right password is refused while locked
    client.post(f"/{slug}/login", data={"identifier": "iso_admin", "password": "IsoAdmin@123"})
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")


def test_a_company_sessions_risk_score_can_end_it(company, background):
    _, schema = company
    sid = secrets.token_hex(16)
    with flask_app.test_request_context("/"):
        g.tenant_db = schema
        evaluate_session_risk(sid, "iso_admin", "admin", 1000, "test.risk", "test: over the threshold")
    background()
    with flask_app.test_request_context("/"):
        g.tenant_db = schema
        assert is_session_compromised(sid) is True


@pytest.mark.parametrize("inline", [False, True])
def test_security_events_always_go_to_public(company, background, db_engine, monkeypatch, inline):
    _, schema = company
    if inline:
        monkeypatch.setattr(async_writer, "_SYNCHRONOUS_MODE", True)
    marker = "BGW_" + secrets.token_hex(4)
    with flask_app.test_request_context("/"):
        g.tenant_db = schema
        log_security_event("test.background_schema", "where does this land", level="INFO", identifier=marker)
    background()
    assert _one(db_engine, "SELECT COUNT(*) FROM public.security_events WHERE identifier=%s", (marker,))[0] == 1
    assert _one(db_engine, f'SELECT COUNT(*) FROM "{schema}".security_events WHERE identifier=%s',  # nosec B608
                (marker,))[0] == 0


def test_writes_outside_any_request_still_use_public(background, db_engine):
    marker = "bgw_" + secrets.token_hex(4)

    def write():
        from database import get_db_connection
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("SELECT (current_schemas(false))[1]")
        seen.append(cur.fetchone()[0])
        cur.close()
        db.close()
    seen = []
    async_writer.enqueue_write(write)
    background()
    assert seen == ["public"], marker
