"""SCALE-08: the session kill-switch stream must answer immediately.

Every authenticated page opens an EventSource to /api/session/risk-stream.
The endpoint used to hold the request for ~20 s (10 checks, 2 s apart) and
the browser reconnected straight away, so each open tab permanently held
one of the server's few gthread worker threads -- a few dozen open tabs
stalled the app for everyone. It now reports the current state, tells the
browser to ask again later (SSE `retry:`), and closes. The real kill switch
is still _reject_if_compromised() on every authenticated request.
"""
import time

import blueprints.core as core


def _open_session(client):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["_sid"] = "qa-risk-sid"


def test_stream_answers_without_holding_a_worker_thread(client, monkeypatch):
    monkeypatch.setattr(core, "is_session_compromised", lambda sid: False)
    _open_session(client)
    started = time.monotonic()
    resp = client.get("/api/session/risk-stream")
    body = resp.get_data(as_text=True)
    assert time.monotonic() - started < 2
    assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
    assert "event: compromised" not in body


def test_browser_is_told_when_to_ask_again(client, monkeypatch):
    monkeypatch.setattr(core, "is_session_compromised", lambda sid: False)
    _open_session(client)
    body = client.get("/api/session/risk-stream").get_data(as_text=True)
    retry_lines = [line for line in body.splitlines() if line.startswith("retry:")]
    assert retry_lines and int(retry_lines[0].split(":", 1)[1]) >= 10000


def test_compromised_session_is_reported_on_the_first_answer(client, monkeypatch):
    monkeypatch.setattr(core, "is_session_compromised", lambda sid: sid == "qa-risk-sid")
    _open_session(client)
    body = client.get("/api/session/risk-stream").get_data(as_text=True)
    assert "event: compromised" in body
