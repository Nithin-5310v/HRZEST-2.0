"""Browser-side telemetry: CSP violation reports and the injected JS-error reporter
are recorded as frontend.* security events; unhandled server errors as app.server_error.

Everything a browser sends is attacker-controlled, so it must be size-capped,
stripped of query strings and control characters, stored as fields (never in the
message) and never above WARNING (an ERROR would page the alert webhook).
"""
import json
import uuid

import pytest

from extensions import app as flask_app
from utils.async_writer import _write_queue


def _flush():
    _write_queue.join()


def _events(db_engine, marker):
    cur = db_engine.cursor()
    cur.execute("SELECT event_type, level, path, identifier, extra_json FROM security_events "
                "WHERE extra_json LIKE %s ORDER BY id", (f"%{marker}%",))
    rows = cur.fetchall()
    cur.close()
    return rows


@pytest.fixture
def csrf_on(client):
    flask_app.config["TESTING"] = False
    try:
        yield client
    finally:
        flask_app.config["TESTING"] = True


class TestFrontendIngestion:
    def test_csp_report_from_a_real_browser_is_recorded(self, csrf_on, db_engine):
        """Browsers send no CSRF token; these reports used to be rejected with 403."""
        m = uuid.uuid4().hex
        body = {"csp-report": {"blocked-uri": f"https://evil.example/{m}.js?token=SECRET",
                               "violated-directive": "script-src-elem",
                               "document-uri": "https://hrzest.test/admin?reset=SECRET#frag"}}
        resp = csrf_on.post("/csp-report", data=json.dumps(body), content_type="application/csp-report")
        assert resp.status_code == 204
        _flush()
        (event_type, level, _path, _who, extra), = _events(db_engine, m)
        extra = json.loads(extra)
        assert event_type == "frontend.csp_violation" and level == "WARNING"
        assert extra["directive"] == "script-src-elem"
        assert extra["page"] == "/admin"                                     # no query, no fragment
        assert extra["blocked_uri"] == f"https://evil.example/{m}.js"        # no query
        assert "SECRET" not in json.dumps(extra)

    def test_reporting_api_format_is_recorded(self, csrf_on, db_engine):
        m = uuid.uuid4().hex
        body = [{"type": "csp-violation", "body": {"blockedURL": f"inline-{m}", "effectiveDirective": "script-src",
                                                   "documentURL": "https://hrzest.test/login"}}]
        assert csrf_on.post("/csp-report", data=json.dumps(body),
                            content_type="application/reports+json").status_code == 204
        _flush()
        (event_type, _lv, _p, _w, extra), = _events(db_engine, m)
        assert event_type == "frontend.csp_violation" and json.loads(extra)["page"] == "/login"

    def test_beacon_works_for_a_logged_in_user_without_a_csrf_token(self, csrf_on, db_engine):
        m = uuid.uuid4().hex
        with csrf_on.session_transaction() as s:
            s["employee_id"] = "TST001"
        resp = csrf_on.post("/api/client-log", data=json.dumps(
            {"type": "js_error", "message": f"x is undefined {m}", "source": "/static/app.js?v=9",
             "line": 7, "page": "/employee_portal?token=SECRET"}), content_type="application/json")
        assert resp.status_code == 204
        _flush()
        (event_type, level, path, who, extra), = _events(db_engine, m)
        extra = json.loads(extra)
        assert (event_type, level, who) == ("frontend.js_error", "WARNING", "TST001")
        assert path == "/api/client-log"                       # the request path...
        assert extra["page"] == "/employee_portal"             # ...vs the page the user was on
        assert extra["source_file"] == "/static/app.js" and "SECRET" not in json.dumps(extra)

    def test_untrusted_input_is_sanitised_and_never_reaches_error_level(self, client, db_engine):
        m = uuid.uuid4().hex
        # (markup like <script> is stopped earlier, by the WAF -- this exercises the sanitiser)
        hostile = f"{m} \x1b[31m\x07\x7f tail " + "A" * 5000
        client.post("/api/client-log", json={"type": "js_error", "message": hostile, "page": "//evil.example/x"})
        _flush()
        rows = _events(db_engine, m)
        assert len(rows) == 1
        _t, level, *_rest, extra = rows[0]
        detail = json.loads(extra)["detail"]
        assert level == "WARNING"                                # an ERROR would page the alert webhook
        assert len(detail) <= 300
        assert not any(ord(c) < 32 or ord(c) == 127 for c in detail)

    @pytest.mark.parametrize("payload", [
        {"type": "server_error", "message": "not an allowed type"},
        {"type": ["js_error"], "message": "wrong shape"},
        "just a string",
        ["a", "list"],
    ])
    def test_invalid_reports_are_ignored(self, client, db_engine, payload):
        before = self._count(db_engine)
        assert client.post("/api/client-log", json=payload).status_code == 204
        _flush()
        assert self._count(db_engine) == before

    def test_oversized_body_is_dropped(self, client, db_engine):
        before = self._count(db_engine)
        big = {"type": "js_error", "message": "x", "pad": "P" * 20000}
        assert client.post("/api/client-log", json=big).status_code == 204
        _flush()
        assert self._count(db_engine) == before

    @staticmethod
    def _count(db_engine):
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM security_events WHERE event_type LIKE 'frontend.%'")
        n = cur.fetchone()[0]
        cur.close()
        return n


class TestReporterIsInjected:
    def test_pages_carry_the_reporter_script(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"/api/client-log" in resp.data
        assert b"unhandledrejection" in resp.data

    def test_error_pages_do_not(self, client):
        assert b"/api/client-log" not in client.get("/definitely-not-a-page").data


class TestServerErrorsAreLogged:
    def test_unhandled_exception_is_persisted_without_the_traceback(self, monkeypatch, db_engine):
        import app as app_module
        monkeypatch.setattr(app_module, "_alert_on_error", lambda *a, **k: None)
        marker = f"MarkerBoom{uuid.uuid4().hex[:8]}"
        exc_type = type(marker, (Exception,), {})
        with flask_app.test_request_context("/boom", method="POST"):
            app_module.unhandled_exception(exc_type("secret internal detail"))
        _flush()
        cur = db_engine.cursor()
        cur.execute("SELECT level, message, path, extra_json FROM security_events "
                    "WHERE event_type='app.server_error' AND extra_json LIKE %s", (f"%{marker}%",))
        (level, message, path, extra), = cur.fetchall()
        cur.close()
        assert level == "WARNING" and path == "/boom"
        assert "secret internal detail" not in message + extra and "Traceback" not in extra
