"""Email through Microsoft 365 (utils/graph_mail.py): chosen by .env's
EMAIL_PROVIDER=microsoft_graph, used by every existing send through
utils/email_utils.py's send_email_smtp(). No network: Microsoft's two
endpoints are replaced by a fake that records each request."""
import base64
import json
import urllib.parse

import pytest

import utils.email_utils as email_utils
from utils import graph_mail

GRAPH_ENV = {
    "EMAIL_PROVIDER": "microsoft_graph",
    "MICROSOFT_TENANT_ID": "tenant-123",
    "MICROSOFT_CLIENT_ID": "client-456",
    "MICROSOFT_CLIENT_SECRET": "very-secret-value",
    "EMAIL_FROM_EMAIL": "noreply@hrzest.test",
    "EMAIL_FROM_NAME": "HRzest",
}


@pytest.fixture
def graph_env(monkeypatch):
    for k, v in GRAPH_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MICROSOFT_GRAPH_ENDPOINT", raising=False)
    graph_mail._token_cache.clear()
    yield
    graph_mail._token_cache.clear()


class FakeMicrosoft:
    """Stands in for graph_mail._post: answers the token and sendMail calls."""

    def __init__(self, token_status=200, send_statuses=(202,)):
        self.calls = []
        self.token_status = token_status
        self.send_statuses = list(send_statuses)
        self.tokens_issued = 0

    def __call__(self, url, data, headers):
        self.calls.append((url, data, headers))
        if "login.microsoftonline.com" in url:
            if self.token_status != 200:
                return self.token_status, json.dumps({
                    "error": "invalid_client",
                    "error_description": "AADSTS7000215: Invalid client secret provided.\nTrace ID: x"}).encode()
            self.tokens_issued += 1
            return 200, json.dumps({"access_token": f"tok-{self.tokens_issued}", "expires_in": 3599}).encode()
        status = self.send_statuses.pop(0) if self.send_statuses else 202
        if status == 202:
            return 202, b""
        return status, json.dumps({"error": {"code": "ErrorAccessDenied",
                                             "message": "Access is denied."}}).encode()

    def sends(self):
        return [c for c in self.calls if "/sendMail" in c[0]]


def _no_db(*_a, **_k):
    raise RuntimeError("db down")


class TestConfig:
    def test_env_selects_microsoft_365(self, graph_env, monkeypatch):
        monkeypatch.setattr(email_utils, "get_db_connection", _no_db)
        cfg = email_utils.get_email_config()
        assert cfg["provider"] == "microsoft_graph"
        assert cfg["from_email"] == "noreply@hrzest.test" and cfg["from_name"] == "HRzest"
        # Callers that only check an SMTP-shaped config is present still work.
        assert cfg["host"] == "graph.microsoft.com" and cfg["user"] == "noreply@hrzest.test"

    def test_beats_env_smtp(self, graph_env, monkeypatch):
        monkeypatch.setattr(email_utils, "get_db_connection", _no_db)
        monkeypatch.setenv("SMTP_HOST", "smtp.env.test")
        monkeypatch.setenv("SMTP_USER", "u")
        monkeypatch.setenv("SMTP_PASS", "p")
        assert email_utils.get_email_config()["provider"] == "microsoft_graph"

    def test_a_company_saved_smtp_still_wins(self, graph_env, db_engine):
        from utils.helpers import encrypt_pii
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO email_config (smtp_host, smtp_port, smtp_user, smtp_pass, from_name, from_email) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            ("smtp.company.test", 587, "graphtest@company.test", encrypt_pii("pw"), "Co", "graphtest@company.test"))
        try:
            cfg = email_utils.get_email_config()
            assert cfg["host"] == "smtp.company.test" and "provider" not in cfg
        finally:
            cur.execute("DELETE FROM email_config WHERE smtp_user='graphtest@company.test'")
            cur.close()

    @pytest.mark.parametrize("missing", ["MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID",
                                         "MICROSOFT_CLIENT_SECRET", "EMAIL_FROM_EMAIL"])
    def test_incomplete_settings_are_ignored(self, graph_env, monkeypatch, missing):
        monkeypatch.delenv(missing)
        monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
        assert graph_mail.config_from_env() is None

    def test_other_provider_value_is_ignored(self, graph_env, monkeypatch):
        monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
        assert graph_mail.config_from_env() is None


class TestSending:
    def test_sends_through_send_email_smtp(self, graph_env, monkeypatch):
        fake = FakeMicrosoft()
        monkeypatch.setattr(graph_mail, "_post", fake)
        cfg = graph_mail.config_from_env()
        email_utils.send_email_smtp("asha@acme.test", "Your code", "<p>123456</p>", cfg)

        token_url, token_data, _ = fake.calls[0]
        assert token_url == "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/token"
        form = urllib.parse.parse_qs(token_data.decode())
        assert form["grant_type"] == ["client_credentials"]
        assert form["scope"] == ["https://graph.microsoft.com/.default"]
        assert form["client_id"] == ["client-456"]

        url, data, headers = fake.sends()[0]
        assert url == "https://graph.microsoft.com/v1.0/users/noreply@hrzest.test/sendMail"
        assert headers["Authorization"] == "Bearer tok-1"
        msg = json.loads(data)["message"]
        assert msg["subject"] == "Your code"
        assert msg["body"] == {"contentType": "HTML", "content": "<p>123456</p>"}
        assert msg["toRecipients"] == [{"emailAddress": {"address": "asha@acme.test"}}]
        assert msg["from"]["emailAddress"] == {"name": "HRzest", "address": "noreply@hrzest.test"}
        assert json.loads(data)["saveToSentItems"] is False

    def test_attachment_goes_as_a_file_attachment(self, graph_env, monkeypatch):
        fake = FakeMicrosoft()
        monkeypatch.setattr(graph_mail, "_post", fake)
        email_utils.send_email_smtp("a@x.test", "Payslip", "<p>hi</p>", graph_mail.config_from_env(),
                                    attachment_bytes=b"%PDF-1.4 data", attachment_filename="payslip.pdf")
        att = json.loads(fake.sends()[0][1])["message"]["attachments"][0]
        assert att["@odata.type"] == "#microsoft.graph.fileAttachment"
        assert att["name"] == "payslip.pdf"
        assert base64.b64decode(att["contentBytes"]) == b"%PDF-1.4 data"

    def test_several_recipients(self, graph_env, monkeypatch):
        fake = FakeMicrosoft()
        monkeypatch.setattr(graph_mail, "_post", fake)
        graph_mail.send_mail(graph_mail.config_from_env(), "a@x.test, b@x.test", "s", "<p>b</p>")
        to = json.loads(fake.sends()[0][1])["message"]["toRecipients"]
        assert [r["emailAddress"]["address"] for r in to] == ["a@x.test", "b@x.test"]

    def test_token_is_reused_between_emails(self, graph_env, monkeypatch):
        fake = FakeMicrosoft()
        monkeypatch.setattr(graph_mail, "_post", fake)
        cfg = graph_mail.config_from_env()
        for n in range(3):
            graph_mail.send_mail(cfg, "a@x.test", f"s{n}", "<p>b</p>")
        assert fake.tokens_issued == 1 and len(fake.sends()) == 3

    def test_a_rejected_token_is_renewed_once(self, graph_env, monkeypatch):
        fake = FakeMicrosoft(send_statuses=(401, 202))
        monkeypatch.setattr(graph_mail, "_post", fake)
        graph_mail.send_mail(graph_mail.config_from_env(), "a@x.test", "s", "<p>b</p>")
        assert fake.tokens_issued == 2
        assert [c[2]["Authorization"] for c in fake.sends()] == ["Bearer tok-1", "Bearer tok-2"]

    def test_a_wrong_secret_raises_without_leaking_it(self, graph_env, monkeypatch):
        monkeypatch.setattr(graph_mail, "_post", FakeMicrosoft(token_status=401))
        with pytest.raises(graph_mail.GraphMailError) as exc:
            graph_mail.send_mail(graph_mail.config_from_env(), "a@x.test", "s", "<p>b</p>")
        text = str(exc.value)
        assert "invalid_client" in text and "AADSTS7000215" in text
        assert "very-secret-value" not in text and "Trace ID" not in text

    def test_missing_permission_raises_microsofts_reason(self, graph_env, monkeypatch):
        monkeypatch.setattr(graph_mail, "_post", FakeMicrosoft(send_statuses=(403,)))
        with pytest.raises(graph_mail.GraphMailError) as exc:
            graph_mail.send_mail(graph_mail.config_from_env(), "a@x.test", "s", "<p>b</p>")
        assert "ErrorAccessDenied" in str(exc.value) and "HTTP 403" in str(exc.value)


class TestQueueAndTestButton:
    def test_queued_email_is_sent_through_microsoft_365(self, graph_env, db_engine, monkeypatch):
        fake = FakeMicrosoft()
        monkeypatch.setattr(graph_mail, "_post", fake)
        cur = db_engine.cursor()
        cur.execute("INSERT INTO email_queue (to_email, subject, html_body) VALUES (%s,%s,%s) RETURNING id",
                    ("queued@acme.test", "Your sign-in code", "<p>654321</p>"))
        qid = cur.fetchone()[0]
        try:
            from extensions import app as flask_app
            with flask_app.app_context():
                email_utils._drain_queue_for_schema("public")
            cur.execute("SELECT status FROM email_queue WHERE id=%s", (qid,))
            assert cur.fetchone()[0] == "done"
            sent = [json.loads(c[1])["message"]["toRecipients"][0]["emailAddress"]["address"] for c in fake.sends()]
            assert "queued@acme.test" in sent
        finally:
            cur.execute("DELETE FROM email_queue WHERE id=%s", (qid,))
            cur.close()

    def test_test_email_button_shows_microsofts_reason(self, client, seed_admin, graph_env, monkeypatch):
        monkeypatch.setattr(graph_mail, "_post", FakeMicrosoft(send_statuses=(403,)))
        monkeypatch.setattr("blueprints.admin_views.get_email_config", graph_mail.config_from_env)
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        resp = client.post("/api/admin/test_email", json={"to_email": "admin@acme.test"})
        body = resp.get_json()
        assert resp.status_code == 500
        assert "Microsoft 365 email test failed" in body["msg"] and "ErrorAccessDenied" in body["msg"]
        assert "very-secret-value" not in body["msg"]
