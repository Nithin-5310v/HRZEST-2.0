"""Enquiries reach one support inbox (utils/email_utils.py's support_inbox():
SUPPORT_EMAIL, else the sending address, else noreply@hrzest.com). The
landing page's Contact, Request-info and Feedback forms email it, and the
page shows that address instead of a hard-coded one."""
import pytest

import blueprints.org as org
import utils.email_utils as email_utils


@pytest.fixture
def sent(monkeypatch):
    """Captures what notify_support() would queue."""
    calls = []
    monkeypatch.setattr(email_utils, "get_email_config", lambda: {"host": "smtp.test", "user": "u"})
    monkeypatch.setattr(email_utils, "send_email_async",
                        lambda to, subject, body, cfg, **_: calls.append((to, subject, body)))
    return calls


@pytest.fixture
def cleanup(db_engine):
    emails = []
    yield emails
    cur = db_engine.cursor()
    for e in emails:
        cur.execute("DELETE FROM att_master.leads WHERE email=%s", (e,))
        cur.execute("DELETE FROM att_master.feedback WHERE email=%s", (e,))
    db_engine.commit()
    cur.close()


class TestSupportInbox:
    def test_defaults_to_noreply(self, monkeypatch):
        for k in ("SUPPORT_EMAIL", "EMAIL_FROM_EMAIL", "SMTP_FROM_EMAIL"):
            monkeypatch.delenv(k, raising=False)
        assert email_utils.support_inbox() == "noreply@hrzest.com"

    def test_follows_the_sending_address(self, monkeypatch):
        monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
        monkeypatch.setenv("EMAIL_FROM_EMAIL", "noreply@example.test")
        assert email_utils.support_inbox() == "noreply@example.test"

    def test_support_email_setting_wins(self, monkeypatch):
        monkeypatch.setenv("SUPPORT_EMAIL", "help@example.test")
        monkeypatch.setenv("EMAIL_FROM_EMAIL", "noreply@example.test")
        assert email_utils.support_inbox() == "help@example.test"


class TestFormsEmailTheInbox:
    def test_contact_form_is_saved_and_emailed(self, client, db_engine, sent, cleanup, monkeypatch):
        monkeypatch.setenv("SUPPORT_EMAIL", "help@example.test")
        cleanup.append("visitor@acme.test")
        resp = client.post("/api/leads", json={
            "name": "Ravi <b>K</b>", "email": "visitor@acme.test",
            "subject": "On-premises pricing", "message": "We have 300 staff."})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("SELECT message FROM att_master.leads WHERE email=%s", ("visitor@acme.test",))
        assert cur.fetchone()[0] == "On-premises pricing\n\nWe have 300 staff."
        cur.close()

        (to, subject, body), = sent
        assert to == "help@example.test"
        assert subject == "New enquiry: On-premises pricing"
        assert 'href="mailto:visitor@acme.test"' in body and "We have 300 staff." in body
        # Visitor input is escaped, never rendered as HTML.
        assert "<b>K</b>" not in body and "&lt;b&gt;K&lt;/b&gt;" in body

    def test_request_info_form_is_emailed(self, client, sent, cleanup):
        cleanup.append("lead@acme.test")
        resp = client.post("/api/leads", json={"name": "Asha", "email": "lead@acme.test",
                                               "phone": "+91 90000 00000", "company_name": "Acme Foods"})
        assert resp.status_code == 200
        (to, subject, body), = sent
        assert to == email_utils.support_inbox()
        assert subject == "New enquiry: Acme Foods" and "+91 90000 00000" in body

    def test_feedback_is_emailed(self, client, sent, cleanup):
        cleanup.append("fb@acme.test")
        resp = client.post("/api/feedback", json={"feedback_type": "Biometrics", "rating": 5,
                                                  "email": "fb@acme.test", "message": "Face check-in is quick."})
        assert resp.status_code == 200
        (to, subject, body), = sent
        assert subject == "New feedback: Biometrics (5/5)" and "Face check-in is quick." in body

    def test_rejected_forms_send_nothing(self, client, sent):
        assert client.post("/api/leads", json={"name": "No Email"}).status_code == 400
        assert client.post("/api/feedback", json={"message": ""}).status_code == 400
        assert sent == []

    def test_a_mail_problem_doesnt_lose_the_enquiry(self, client, db_engine, cleanup, monkeypatch):
        monkeypatch.setattr(email_utils, "get_email_config", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        cleanup.append("still-saved@acme.test")
        resp = client.post("/api/leads", json={"name": "Kept", "email": "still-saved@acme.test"})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM att_master.leads WHERE email=%s", ("still-saved@acme.test",))
        assert cur.fetchone()
        cur.close()

    def test_no_email_configured_still_saves(self, client, cleanup, monkeypatch):
        monkeypatch.setattr(email_utils, "get_email_config", lambda: None)
        cleanup.append("noconfig@acme.test")
        assert client.post("/api/leads", json={"name": "N", "email": "noconfig@acme.test"}).status_code == 200


class TestLandingPage:
    def test_shows_the_contact_email_setting(self, client, monkeypatch):
        monkeypatch.setenv("CONTACT_EMAIL", "help@example.test")
        html = client.get("/").get_data(as_text=True)
        assert "contact@hrzest.com" not in html
        assert html.count("help@example.test") >= 6
        assert 'id="contactFormError"' in html

    def test_never_shows_the_inbox_enquiries_go_to(self, client, monkeypatch):
        # The inbox is often someone's own mailbox; visitors see noreply@
        # unless CONTACT_EMAIL says otherwise.
        monkeypatch.delenv("CONTACT_EMAIL", raising=False)
        monkeypatch.setenv("SUPPORT_EMAIL", "person@example.test")
        monkeypatch.setenv("EMAIL_FROM_EMAIL", "sender@example.test")
        html = client.get("/").get_data(as_text=True)
        assert "person@example.test" not in html and "sender@example.test" not in html
        assert html.count("noreply@hrzest.com") >= 5

    def test_contact_thank_you_reads_as_one_sentence(self, client, monkeypatch):
        # The banner is a flex row: the text must be one item next to the icon,
        # or its pieces land on separate lines. It names no inbox -- without
        # SUPPORT_EMAIL that would be noreply@, which reads as a dead end.
        for name in ("SUPPORT_EMAIL", "EMAIL_FROM_EMAIL", "SMTP_FROM_EMAIL"):
            monkeypatch.delenv(name, raising=False)
        html = client.get("/").get_data(as_text=True)
        assert ('<i class="ti ti-circle-check"></i> <span>Thank you! We\'ve received your message '
                'and will reply to the email address you gave us.</span>') in html
        assert "has been sent to" not in html


def test_enquiry_email_skips_empty_rows():
    body = org._enquiry_email("H", [("Name", "A"), ("Phone", None), ("Email", "a@b.test")], None)
    assert "Phone" not in body and 'mailto:a@b.test' in body
