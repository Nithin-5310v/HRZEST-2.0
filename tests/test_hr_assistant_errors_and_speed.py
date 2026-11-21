"""HR Assistant (employee portal chat): accurate errors and faster replies.

Report "HR Assistant: When and Why It Fails" (20 Nov 2026): the chat showed
"Couldn't reach the server" whenever /api/employee/chat answered with
anything but JSON -- above all @employee_required's redirect to a login page
(signed out, admin in the same browser, first-login password change, ...).
And one question could wait 25 + 20 + 20 = 65 s for the three AI services in
turn, past nginx's 60 s gateway limit.
"""
import json
import pathlib
import time

import pytest

import utils.ai_assistant as ai


def _employee_session(client, emp_id, **extra):
    with client.session_transaction() as s:
        s["employee_id"] = emp_id
        s["_session_created"] = time.time()
        s["_last_activity"] = time.time()
        s.update(extra)


@pytest.fixture
def ai_reply(monkeypatch):
    monkeypatch.setattr(ai, "_n8n_webhook_url", lambda: "")
    monkeypatch.setattr(ai, "_gemini_api_key", lambda: None)
    monkeypatch.setattr(ai, "_api_key", lambda: "k")
    monkeypatch.setattr(ai, "_call_claude", lambda s, m: ("Open Support Tickets and tap Raise Ticket.", None))


def _chat(client):
    return client.post("/api/employee/chat", json={"message": "How do I raise a support ticket?"})


# ── sign-in problems answer the chat with JSON, not a redirect ─────────────────

class TestApiGetsJsonNotRedirect:
    def test_not_signed_in(self, client, ai_reply):
        resp = _chat(client)
        assert resp.status_code == 401 and resp.is_json
        body = resp.get_json()
        assert body["ok"] is False and "sign in" in body["msg"].lower()
        assert body["redirect"].endswith("login")

    def test_admin_signed_in_in_the_same_browser(self, client, seed_employee, ai_reply):
        _employee_session(client, seed_employee["employee_id"], admin_logged_in=True, admin_username="test_admin")
        resp = _chat(client)
        assert resp.status_code == 409 and resp.is_json
        assert "admin" in resp.get_json()["msg"].lower()
        assert resp.get_json()["redirect"] == "/admin"

    def test_first_login_password_change_pending(self, client, seed_employee, ai_reply):
        _employee_session(client, seed_employee["employee_id"], _fpc=True)
        resp = _chat(client)
        assert resp.status_code == 403 and resp.is_json
        assert resp.get_json()["redirect"] == "/force_change_pin"

    def test_deactivated_account(self, client, seed_employee, db_engine, ai_reply, monkeypatch):
        import utils.auth as auth
        monkeypatch.setattr(auth, "_account_still_active", lambda *a: False)
        _employee_session(client, seed_employee["employee_id"])
        resp = _chat(client)
        assert resp.status_code == 401 and resp.is_json

    def test_signed_in_employee_still_gets_the_answer(self, client, seed_employee, ai_reply):
        _employee_session(client, seed_employee["employee_id"])
        resp = _chat(client)
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True, "reply": "Open Support Tickets and tap Raise Ticket."}

    def test_pages_still_redirect(self, client, seed_employee):
        assert client.get("/employee_portal").status_code == 302           # not signed in
        _employee_session(client, seed_employee["employee_id"], _fpc=True)
        resp = client.get("/employee_portal")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/force_change_pin")


# ── speed: one shared time budget, and a service that just failed is skipped ──

class TestAiSpeed:
    def _configure(self, monkeypatch, n8n=True, gemini=True, claude=True):
        monkeypatch.setattr(ai, "_n8n_webhook_url", lambda: "https://n8n.test/hook" if n8n else "")
        monkeypatch.setattr(ai, "_gemini_api_key", lambda: "g" if gemini else None)
        monkeypatch.setattr(ai, "_api_key", lambda: "a" if claude else None)

    def test_a_failed_service_is_skipped_on_the_next_question(self, monkeypatch):
        self._configure(monkeypatch)
        calls = []

        def n8n(*a):
            calls.append("n8n")
            return None, "timeout"

        def gemini(*a):
            calls.append("gemini")
            return "answer", None
        monkeypatch.setattr(ai, "_call_n8n", n8n)
        monkeypatch.setattr(ai, "_call_gemini", gemini)
        monkeypatch.setattr(ai, "_call_claude", lambda *a: (_ for _ in ()).throw(AssertionError("not needed")))
        assert ai.ask_assistant("ctx", "q1") == (True, "answer")
        assert ai.ask_assistant("ctx", "q2") == (True, "answer")
        assert calls == ["n8n", "gemini", "gemini"]          # n8n not waited on again

    def test_it_is_tried_again_after_the_cool_down(self, monkeypatch):
        self._configure(monkeypatch, gemini=False)
        results = iter([(None, "down"), ("n8n is back", None)])
        monkeypatch.setattr(ai, "_call_n8n", lambda *a: next(results))
        monkeypatch.setattr(ai, "_call_claude", lambda *a: ("claude answer", None))
        assert ai.ask_assistant("ctx", "q1") == (True, "claude answer")
        ai._failed_at["n8n"] -= ai._SKIP_AFTER_FAILURE_SECONDS + 1  # cool-down over
        assert ai.ask_assistant("ctx", "q2") == (True, "n8n is back")

    def test_when_every_service_failed_recently_they_are_still_tried(self, monkeypatch):
        self._configure(monkeypatch, n8n=False, gemini=False)
        results = iter([(None, "down"), ("recovered", None)])
        monkeypatch.setattr(ai, "_call_claude", lambda *a: next(results))
        assert ai.ask_assistant("ctx", "q1")[0] is False
        assert ai.ask_assistant("ctx", "q2") == (True, "recovered")

    def test_timeouts_share_one_budget_below_the_gateway_limit(self, monkeypatch):
        """The services' timeouts are cut to what is left of the question's
        budget, so the whole question ends well inside nginx's 60 s."""
        self._configure(monkeypatch)
        seen = []

        def slow_urlopen(req, timeout=None):
            seen.append(timeout)
            ai._deadline.at -= timeout                      # this call used up its whole timeout
            raise ai.urllib.error.URLError("timed out")
        monkeypatch.setattr(ai.urllib.request, "urlopen", slow_urlopen)
        ok, _ = ai.ask_assistant("ctx", "q")
        assert ok is False
        assert sum(seen) <= ai._TOTAL_TIMEOUT_SECONDS + 1
        assert ai._TOTAL_TIMEOUT_SECONDS < 60
        assert seen[0] == ai._N8N_TIMEOUT_SECONDS             # first call keeps its own limit
        assert ai._deadline.at is None                        # budget cleared after the question

    def test_no_budget_outside_a_question(self):
        assert ai._timeout(20) == 20

    def test_claude_asks_for_low_effort_with_room_for_thinking(self, monkeypatch):
        captured = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"content": [{"type": "text", "text": "hi"}]}).encode()

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _Resp()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
        assert ai._call_claude("sys", [{"role": "user", "content": "hi"}]) == ("hi", None)
        assert captured["body"]["model"] == "claude-sonnet-5"
        assert captured["body"]["output_config"] == {"effort": "low"}
        assert captured["body"]["max_tokens"] == 1024


# ── the widget shows the real reason ─────────────────────────────────────────

def test_widget_reads_msg_and_handles_non_json_answers():
    html = (pathlib.Path(__file__).resolve().parent.parent / "templates" / "employee_portal.html").read_text(encoding="utf-8")
    start = html.index("fetch('/api/employee/chat'")
    script = html[start:start + 6000]
    assert "data.reply || data.msg" in script                 # server errors use "msg"
    assert "application/json" in script and "r.redirected" in script
    assert "AbortController" in html and "controller.abort()" in html
    assert "history = [];" in script                          # security-filter block resets the conversation
