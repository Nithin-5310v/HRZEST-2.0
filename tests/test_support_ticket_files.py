"""Support tickets: screenshots/documents attached by either side, and the
email to whoever raised a ticket when HRZEST replies or changes its status
(utils/support_tickets.py, blueprints/company_profile.py,
blueprints/platform_admin/tickets.py)."""
import io
import os

import pytest

from tests.test_company_profile_routes import _admin_session, other_tenant, own_tenant  # noqa: F401
from tests.test_platform_admin import _login_platform_admin

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%test\n"


def _file(data, name):
    return (io.BytesIO(data), name)


@pytest.fixture
def cleanup_files(db_engine):
    yield
    cur = db_engine.cursor()
    cur.execute("SELECT stored_ref FROM att_master.tenant_ticket_attachments")
    for (ref,) in cur.fetchall():
        try:
            os.remove(ref)
        except OSError:
            pass
    cur.execute("DELETE FROM att_master.tenant_ticket_attachments")
    db_engine.commit()
    cur.close()


@pytest.fixture
def sent(monkeypatch):
    """Captures queued emails instead of sending them."""
    import utils.email_utils as eu
    mails = []
    monkeypatch.setattr(eu, "get_email_config", lambda: {"host": "smtp.test"})
    monkeypatch.setattr(eu, "send_email_async",
                        lambda to, subject, html, config, **kw: mails.append((to, subject, html)))
    return mails


def _attachments(db_engine, ticket_id):
    cur = db_engine.cursor()
    cur.execute("SELECT id, comment_id, original_name, uploader_kind, stored_ref FROM att_master.tenant_ticket_attachments "
                "WHERE ticket_id=%s ORDER BY id", (ticket_id,))
    rows = cur.fetchall()
    cur.close()
    return rows


def _raise_ticket(client, db_engine, tenant_id, files):
    client.post("/profile/tickets", data={
        "subject": "Payslip PDF is blank", "description": "See the screenshot.", "priority": "normal",
        "attachments": files,
    }, content_type="multipart/form-data")
    cur = db_engine.cursor()
    cur.execute("SELECT id FROM att_master.tenant_support_tickets WHERE tenant_id=%s ORDER BY id DESC LIMIT 1",
                (tenant_id,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


class TestCompanyAttaches:
    def test_raise_a_ticket_with_files(self, client, db_engine, seed_admin, own_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        ticket_id = _raise_ticket(client, db_engine, own_tenant,
                                  [_file(PNG, "screen.png"), _file(PDF, "payslip.pdf")])
        rows = _attachments(db_engine, ticket_id)
        assert [(r[1], r[2], r[3]) for r in rows] == [(None, "screen.png", "company_admin"),
                                                      (None, "payslip.pdf", "company_admin")]
        # Encrypted at rest, and given back as it was sent.
        with open(rows[0][4], "rb") as f:
            assert f.read() != PNG
        resp = client.get(f"/profile/tickets/{ticket_id}/files/{rows[0][0]}?view=1")
        assert resp.status_code == 200 and resp.data == PNG and resp.mimetype == "image/png"
        pdf = client.get(f"/profile/tickets/{ticket_id}/files/{rows[1][0]}")
        assert pdf.data == PDF and pdf.mimetype == "application/octet-stream"
        assert "attachment" in pdf.headers["Content-Disposition"]
        page = client.get(f"/profile/tickets/{ticket_id}").get_data(as_text=True)
        assert "screen.png" in page and "payslip.pdf" in page

    def test_wrong_kind_of_file_saves_nothing(self, client, db_engine, seed_admin, own_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        resp = client.post("/profile/tickets", data={
            "subject": "Has an exe", "description": "x", "attachments": [_file(b"MZ\x90\x00", "tool.exe")],
        }, content_type="multipart/form-data", follow_redirects=True)
        assert b"not allowed" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM att_master.tenant_support_tickets WHERE subject='Has an exe'")
        assert cur.fetchone() is None, "the ticket isn't half-saved"
        cur.close()

    def test_a_png_that_is_not_a_png_is_refused(self, client, db_engine, seed_admin, own_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        resp = client.post("/profile/tickets", data={
            "subject": "Fake png", "description": "x", "attachments": [_file(b"<script>", "x.png")],
        }, content_type="multipart/form-data", follow_redirects=True)
        assert b"Invalid PNG file." in resp.data

    def test_too_many_files(self, client, seed_admin, own_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        resp = client.post("/profile/tickets", data={
            "subject": "Many", "description": "x", "attachments": [_file(PNG, f"{i}.png") for i in range(6)],
        }, content_type="multipart/form-data", follow_redirects=True)
        assert b"Attach up to 5 files" in resp.data

    def test_reply_with_only_a_file(self, client, db_engine, seed_admin, own_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        ticket_id = _raise_ticket(client, db_engine, own_tenant, [])
        resp = client.post(f"/profile/tickets/{ticket_id}/reply", data={"attachments": [_file(PNG, "more.png")]},
                           content_type="multipart/form-data", follow_redirects=True)
        assert b"Reply sent." in resp.data
        rows = _attachments(db_engine, ticket_id)
        assert len(rows) == 1 and rows[0][1] is not None, "linked to the reply"

    def test_empty_reply_still_refused(self, client, db_engine, seed_admin, own_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        ticket_id = _raise_ticket(client, db_engine, own_tenant, [])
        resp = client.post(f"/profile/tickets/{ticket_id}/reply", data={"comment": "  "}, follow_redirects=True)
        assert b"Write a reply or attach a file." in resp.data

    def test_another_companys_file_is_not_reachable(self, client, db_engine, seed_admin, own_tenant,  # noqa: F811
                                                    other_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description) "
                    "VALUES (%s,'Theirs','x') RETURNING id", (other_tenant,))
        their_ticket = cur.fetchone()[0]
        cur.execute("INSERT INTO att_master.tenant_ticket_attachments (ticket_id, original_name, stored_ref, "
                    "uploader_kind) VALUES (%s,'secret.pdf','/nonexistent','company_admin') RETURNING id",
                    (their_ticket,))
        att_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()
        assert client.get(f"/profile/tickets/{their_ticket}/files/{att_id}").status_code == 404
        # Nor through one of its own tickets.
        mine = _raise_ticket(client, db_engine, own_tenant, [])
        assert client.get(f"/profile/tickets/{mine}/files/{att_id}").status_code == 404


class TestPlatformAdminSide:
    def test_sees_and_downloads_the_companys_files(self, client, db_engine, seed_admin, own_tenant, cleanup_files):  # noqa: F811
        _admin_session(client, "test_admin")
        ticket_id = _raise_ticket(client, db_engine, own_tenant, [_file(PDF, "log.pdf")])
        att_id = _attachments(db_engine, ticket_id)[0][0]
        _login_platform_admin(client)
        page = client.get(f"/super_admin/tickets/{ticket_id}").get_data(as_text=True)
        assert "log.pdf" in page and f"/super_admin/tickets/{ticket_id}/files/{att_id}" in page
        assert client.get(f"/super_admin/tickets/{ticket_id}/files/{att_id}").data == PDF
        assert client.get(f"/super_admin/tickets/{ticket_id}/files/999999").status_code == 404
        queue = client.get("/super_admin/tickets").get_data(as_text=True)
        assert "ti-paperclip" in queue

    def test_reply_with_file_reaches_the_company_by_email(self, client, db_engine, seed_admin, own_tenant,  # noqa: F811
                                                          cleanup_files, sent):
        _admin_session(client, "test_admin")
        ticket_id = _raise_ticket(client, db_engine, own_tenant, [])
        _login_platform_admin(client, username="support_agent")
        resp = client.post(f"/super_admin/tickets/{ticket_id}/comment",
                           data={"comment": "Fixed -- see the steps.", "attachments": [_file(PDF, "steps.pdf")]},
                           content_type="multipart/form-data", follow_redirects=True)
        assert b"Reply sent to the company." in resp.data
        assert [r[3] for r in _attachments(db_engine, ticket_id)] == ["platform_admin"]
        assert len(sent) == 1
        to, subject, html = sent[0]
        assert to == "admin@test.local"  # the raiser's own admin account
        assert f"#{ticket_id}" in subject
        assert "Fixed -- see the steps." in html and f"/profile-test-co/profile/tickets/{ticket_id}" in html
        # The company sees HRZEST's reply and its file.
        _admin_session(client, "test_admin")
        page = client.get(f"/profile/tickets/{ticket_id}").get_data(as_text=True)
        assert "HRZEST support" in page and "steps.pdf" in page


class TestStatusEmails:
    def _ticket(self, client, db_engine, tenant_id):
        _admin_session(client, "test_admin")
        return _raise_ticket(client, db_engine, tenant_id, [])

    def test_resolving_emails_the_raiser(self, client, db_engine, seed_admin, own_tenant, sent):  # noqa: F811
        ticket_id = self._ticket(client, db_engine, own_tenant)
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tickets/{ticket_id}/update",
                           data={"status": "resolved", "priority": "normal", "note": "Re-run payroll now."},
                           follow_redirects=True)
        assert b"has been emailed" in resp.data
        assert len(sent) == 1
        to, subject, html = sent[0]
        assert to == "admin@test.local"
        assert "has been resolved" in subject and "Payslip PDF is blank" in subject
        assert "Re-run payroll now." in html
        # The note is also left in the conversation.
        cur = db_engine.cursor()
        cur.execute("SELECT comment, author_kind FROM att_master.tenant_ticket_comments WHERE ticket_id=%s",
                    (ticket_id,))
        assert cur.fetchall() == [("Re-run payroll now.", "platform_admin")]
        cur.close()

    def test_any_status_change_emails(self, client, db_engine, seed_admin, own_tenant, sent):  # noqa: F811
        ticket_id = self._ticket(client, db_engine, own_tenant)
        _login_platform_admin(client)
        client.post(f"/super_admin/tickets/{ticket_id}/update", data={"status": "in_progress", "priority": "normal"})
        assert len(sent) == 1 and "is being worked on" in sent[0][1]

    def test_priority_only_change_sends_nothing(self, client, db_engine, seed_admin, own_tenant, sent):  # noqa: F811
        ticket_id = self._ticket(client, db_engine, own_tenant)
        _login_platform_admin(client)
        client.post(f"/super_admin/tickets/{ticket_id}/update", data={"status": "open", "priority": "urgent"})
        assert sent == []

    def test_falls_back_to_the_company_admin_email(self, client, db_engine, own_tenant, sent):  # noqa: F811
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.tenants SET admin_email='owner@profile-test.test' WHERE id=%s", (own_tenant,))
        cur.execute("INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description, created_by) "
                    "VALUES (%s,'Raised by HRZEST','x','some_platform_admin') RETURNING id", (own_tenant,))
        ticket_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()
        _login_platform_admin(client)
        client.post(f"/super_admin/tickets/{ticket_id}/update", data={"status": "closed", "priority": "normal"})
        assert [m[0] for m in sent] == ["owner@profile-test.test"]

    def test_mail_failure_does_not_block_the_update(self, client, db_engine, seed_admin, own_tenant, monkeypatch):  # noqa: F811
        import utils.email_utils as eu

        def boom():
            raise RuntimeError("smtp down")
        monkeypatch.setattr(eu, "get_email_config", boom)
        ticket_id = self._ticket(client, db_engine, own_tenant)
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tickets/{ticket_id}/update",
                           data={"status": "resolved", "priority": "normal"}, follow_redirects=True)
        assert b"No email could be sent" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.tenant_support_tickets WHERE id=%s", (ticket_id,))
        assert cur.fetchone()[0] == "resolved"
        cur.close()
