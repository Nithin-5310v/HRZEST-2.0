"""
Onboarding blueprint tests — templates, task assignment, offer letters
(PDF generation via ReportLab, email delivery, candidate-facing token
routes), and the employee-facing my_onboarding self-service page.

Run with:
    python -m pytest tests/test_onboarding.py -v
"""
import datetime
import hashlib
import io
import pytest

import blueprints.onboarding as onboarding_module


def _admin_session(client, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"],
        "password": seed_admin["password"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    return resp


def _employee_session(client, seed_employee):
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]


def _make_template(db_engine, name="Test Onboarding Template", tasks=None):
    """Insert a template + its tasks; returns (template_id, [task_ids])."""
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO onboarding_templates (name, description, is_active) VALUES (%s,%s,1) RETURNING id",
        (name, "A template created for tests"),
    )
    tid = cur.fetchone()[0]
    task_ids = []
    for i, t in enumerate(tasks or ["Sign NDA", "Submit ID proof"]):
        cur.execute(
            "INSERT INTO onboarding_template_tasks (template_id, task_title, task_description, "
            "requires_document, due_days, sort_order) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (tid, t, "", 0, 7, i),
        )
        task_ids.append(cur.fetchone()[0])
    cur.close()
    return tid, task_ids


def _assign_onboarding(db_engine, emp_id, template_id, task_ids, task_titles=None):
    """Directly create an employee_onboarding row + its task copies, bypassing
    the route — used when a test only needs the assignment to already exist."""
    cur = db_engine.cursor()
    today = datetime.date.today()
    cur.execute(
        "INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date, status) "
        "VALUES (%s,%s,%s,%s,'In Progress') RETURNING id",
        (emp_id, template_id, today, today + datetime.timedelta(days=30)),
    )
    ob_id = cur.fetchone()[0]
    titles = task_titles or ["Sign NDA", "Submit ID proof"]
    task_row_ids = []
    for tt_id, title in zip(task_ids, titles):
        cur.execute(
            "INSERT INTO employee_onboarding_tasks (onboarding_id, template_task_id, employee_id, "
            "task_title, task_description, requires_document, due_days, status) "
            "VALUES (%s,%s,%s,%s,'',0,7,'Pending') RETURNING id",
            (ob_id, tt_id, emp_id, title),
        )
        task_row_ids.append(cur.fetchone()[0])
    cur.close()
    return ob_id, task_row_ids


def _cleanup_onboarding(db_engine, template_id=None, ob_id=None):
    cur = db_engine.cursor()
    if ob_id:
        cur.execute("DELETE FROM employee_onboarding_tasks WHERE onboarding_id=%s", (ob_id,))
        cur.execute("DELETE FROM offer_letters WHERE onboarding_id=%s", (ob_id,))
        cur.execute("DELETE FROM employee_onboarding WHERE id=%s", (ob_id,))
    if template_id:
        cur.execute("DELETE FROM employee_onboarding_tasks WHERE template_task_id IN "
                    "(SELECT id FROM onboarding_template_tasks WHERE template_id=%s)", (template_id,))
        cur.execute("DELETE FROM employee_onboarding WHERE template_id=%s", (template_id,))
        cur.execute("DELETE FROM onboarding_template_tasks WHERE template_id=%s", (template_id,))
        cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (template_id,))
    cur.close()


@pytest.fixture
def template(db_engine):
    tid, task_ids = _make_template(db_engine)
    yield tid, task_ids
    _cleanup_onboarding(db_engine, template_id=tid)


@pytest.fixture
def assigned_onboarding(db_engine, seed_employee, template):
    tid, task_ids = template
    ob_id, task_row_ids = _assign_onboarding(db_engine, seed_employee["employee_id"], tid, task_ids)
    yield ob_id, task_row_ids
    _cleanup_onboarding(db_engine, ob_id=ob_id)


# ===========================================================================
# Dashboard
# ===========================================================================

class TestOnboardingDashboard:
    def test_requires_admin(self, client):
        resp = client.get("/onboarding", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_renders_for_admin(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/onboarding")
        assert resp.status_code == 200

    def test_renders_with_active_assignment(self, client, seed_admin, assigned_onboarding):
        _admin_session(client, seed_admin)
        resp = client.get("/onboarding?tab=active")
        assert resp.status_code == 200


# ===========================================================================
# Template CRUD
# ===========================================================================

class TestTemplateCRUD:
    def test_save_requires_admin(self, client):
        resp = client.post("/onboarding_template_save", data={"name": "X"}, follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_empty_name_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_save", data={"name": "  "}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"required" in resp.data.lower()

    def test_create_then_update(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_save",
                           data={"name": "CRUD Template", "description": "v1"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT id, description FROM onboarding_templates WHERE name='CRUD Template'")
        row = cur.fetchone()
        assert row is not None
        tid, desc = row
        assert desc == "v1"

        resp = client.post("/onboarding_template_save",
                           data={"template_id": tid, "name": "CRUD Template", "description": "v2"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur.execute("SELECT description FROM onboarding_templates WHERE id=%s", (tid,))
        assert cur.fetchone()[0] == "v2"
        cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (tid,))
        cur.close()

    def test_duplicate_copies_template_and_tasks(self, client, seed_admin, db_engine, template):
        tid, task_ids = template
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_duplicate", data={"template_id": tid}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM onboarding_templates WHERE name=%s", ("Copy of Test Onboarding Template",))
        new_row = cur.fetchone()
        assert new_row is not None
        new_id = new_row[0]
        cur.execute("SELECT COUNT(*) FROM onboarding_template_tasks WHERE template_id=%s", (new_id,))
        assert cur.fetchone()[0] == len(task_ids)
        _cleanup_onboarding(db_engine, template_id=new_id)
        cur.close()

    def test_duplicate_unknown_template_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_duplicate", data={"template_id": 999999}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"not found" in resp.data.lower()

    def test_delete_removes_template_and_tasks(self, client, seed_admin, db_engine):
        tid, task_ids = _make_template(db_engine, name="To Delete")
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_delete", data={"template_id": tid}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM onboarding_templates WHERE id=%s", (tid,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM onboarding_template_tasks WHERE template_id=%s", (tid,))
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_template_detail_renders(self, client, seed_admin, template):
        tid, _ = template
        _admin_session(client, seed_admin)
        resp = client.get(f"/onboarding_template_detail/{tid}")
        assert resp.status_code == 200


# ===========================================================================
# Template tasks
# ===========================================================================

class TestTemplateTasks:
    def test_empty_title_rejected(self, client, seed_admin, template):
        tid, _ = template
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_task_save",
                           data={"template_id": tid, "task_title": "  "},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"required" in resp.data.lower()

    def test_create_and_update_task(self, client, seed_admin, db_engine, template):
        tid, _ = template
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_task_save", data={
            "template_id": tid, "task_title": "New Task", "due_days": "5",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT id, due_days FROM onboarding_template_tasks WHERE task_title='New Task'")
        row = cur.fetchone()
        assert row is not None
        task_id, due_days = row
        assert due_days == 5

        resp = client.post("/onboarding_task_save", data={
            "task_id": task_id, "template_id": tid, "task_title": "New Task", "due_days": "10",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur.execute("SELECT due_days FROM onboarding_template_tasks WHERE id=%s", (task_id,))
        assert cur.fetchone()[0] == 10
        cur.close()

    def test_delete_task(self, client, seed_admin, db_engine, template):
        tid, task_ids = template
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_task_delete", data={"task_id": task_ids[0]}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM onboarding_template_tasks WHERE id=%s", (task_ids[0],))
        assert cur.fetchone()[0] == 0
        cur.close()


# ===========================================================================
# Assignment — single + bulk, duplicate prevention, task copying
# ===========================================================================

class TestAssignOnboarding:
    def test_assign_copies_template_tasks(self, client, seed_admin, seed_employee, db_engine, template):
        tid, task_ids = template
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_assign", data={
            "employee_id": seed_employee["employee_id"], "template_id": tid,
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)

        cur = db_engine.cursor()
        cur.execute("SELECT id FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
                    (seed_employee["employee_id"], tid))
        row = cur.fetchone()
        assert row is not None
        ob_id = row[0]
        cur.execute("SELECT COUNT(*) FROM employee_onboarding_tasks WHERE onboarding_id=%s", (ob_id,))
        assert cur.fetchone()[0] == len(task_ids)
        cur.close()
        _cleanup_onboarding(db_engine, ob_id=ob_id)

    def test_assign_same_template_twice_rejected(self, client, seed_admin, assigned_onboarding, seed_employee, template):
        tid, _ = template
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_assign", data={
            "employee_id": seed_employee["employee_id"], "template_id": tid,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"already has this onboarding" in resp.data.lower()

    def test_bulk_assign_skips_already_assigned(self, client, seed_admin, seed_employee, db_engine, template):
        tid, task_ids = template
        _admin_session(client, seed_admin)
        # First bulk assign creates it
        resp = client.post("/bulk_assign_onboarding", data={
            "template_id": tid, "employee_ids": [seed_employee["employee_id"]],
        }, follow_redirects=True)
        assert resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
                    (seed_employee["employee_id"], tid))
        assert cur.fetchone()[0] == 1

        # Second bulk assign to the same employee+template must be a no-op, not a duplicate row
        resp2 = client.post("/bulk_assign_onboarding", data={
            "template_id": tid, "employee_ids": [seed_employee["employee_id"]],
        }, follow_redirects=True)
        assert resp2.status_code == 200
        cur.execute("SELECT COUNT(*) FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
                    (seed_employee["employee_id"], tid))
        assert cur.fetchone()[0] == 1

        cur.execute("SELECT id FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
                    (seed_employee["employee_id"], tid))
        ob_id = cur.fetchone()[0]
        cur.close()
        _cleanup_onboarding(db_engine, ob_id=ob_id)


# ===========================================================================
# Onboarding detail, admin task status updates, auto-complete
# ===========================================================================

class TestOnboardingDetailAndTaskUpdate:
    def test_detail_page_renders(self, client, seed_admin, assigned_onboarding):
        ob_id, _ = assigned_onboarding
        _admin_session(client, seed_admin)
        resp = client.get(f"/onboarding_detail/{ob_id}")
        assert resp.status_code == 200

    def test_marking_all_tasks_done_auto_completes_onboarding(self, client, seed_admin, db_engine, assigned_onboarding):
        ob_id, task_row_ids = assigned_onboarding
        _admin_session(client, seed_admin)
        # Mark all but the last task done — onboarding must still be In Progress
        for task_id in task_row_ids[:-1]:
            resp = client.post("/onboarding_admin_task_update", data={
                "task_id": task_id, "status": "Done", "ob_id": ob_id,
            }, follow_redirects=False)
            assert resp.status_code in (301, 302)

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM employee_onboarding WHERE id=%s", (ob_id,))
        assert cur.fetchone()[0] == "In Progress"

        # Mark the last one done -> onboarding auto-completes
        resp = client.post("/onboarding_admin_task_update", data={
            "task_id": task_row_ids[-1], "status": "Done", "ob_id": ob_id,
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur.execute("SELECT status FROM employee_onboarding WHERE id=%s", (ob_id,))
        assert cur.fetchone()[0] == "Completed"
        cur.close()

    def test_close_marks_completed(self, client, seed_admin, db_engine, assigned_onboarding):
        ob_id, _ = assigned_onboarding
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_close", data={"ob_id": ob_id}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM employee_onboarding WHERE id=%s", (ob_id,))
        assert cur.fetchone()[0] == "Completed"
        cur.close()


class TestExportCsv:
    def test_export_returns_csv(self, client, seed_admin, assigned_onboarding):
        _admin_session(client, seed_admin)
        resp = client.get("/export_onboarding_csv")
        assert resp.status_code == 200
        assert resp.mimetype == "text/csv"
        assert b"Employee ID" in resp.data


# ===========================================================================
# Offer letters — form, CTC fallback, PDF generation, email send
# ===========================================================================

@pytest.fixture
def offer_letter_row(db_engine, assigned_onboarding, seed_employee):
    ob_id, _ = assigned_onboarding
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO offer_letters (onboarding_id, employee_id, designation, department, "
        "work_location, monthly_ctc, joining_date, reporting_to) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (ob_id, seed_employee["employee_id"], "Software Engineer", "Engineering",
         "Remote", 60000, datetime.date.today(), "Engineering Manager"),
    )
    letter_id = cur.fetchone()[0]
    cur.close()
    yield letter_id, ob_id


class TestOfferLetterForm:
    def test_page_renders_ctc_fallback_from_salary_per_day(self, client, seed_admin, db_engine, assigned_onboarding, seed_employee):
        ob_id, _ = assigned_onboarding
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s,%s) "
            "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=EXCLUDED.salary_per_day",
            (seed_employee["employee_id"], 2000),
        )
        cur.close()
        _admin_session(client, seed_admin)
        resp = client.get(f"/offer_letter/{ob_id}")
        assert resp.status_code == 200
        # monthly_ctc falls back to salary_per_day * 26 = 52,000 when monthly_ctc is 0
        assert b"52000" in resp.data or b"52,000" in resp.data

    def test_save_then_view(self, client, seed_admin, db_engine, assigned_onboarding, seed_employee):
        ob_id, _ = assigned_onboarding
        _admin_session(client, seed_admin)
        resp = client.post("/offer_letter_save", data={
            "ob_id": ob_id, "employee_id": seed_employee["employee_id"],
            "designation": "QA Engineer", "department": "Quality",
            "work_location": "Bengaluru", "monthly_ctc": "75000",
            "joining_date": str(datetime.date.today()),
            "probation_months": "3", "reporting_to": "QA Lead",
            "notice_period_days": "15",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/offer_letter_view/" in resp.headers["Location"]

        cur = db_engine.cursor()
        cur.execute("SELECT designation, probation_months, notice_period_days FROM offer_letters WHERE onboarding_id=%s", (ob_id,))
        row = cur.fetchone()
        assert row == ("QA Engineer", 3, 15)
        cur.close()

        letter_id = resp.headers["Location"].rstrip("/").split("/")[-1]
        resp2 = client.get(f"/offer_letter_view/{letter_id}")
        assert resp2.status_code == 200
        assert b"QA Engineer" in resp2.data

    def test_save_is_idempotent_update_not_duplicate(self, client, seed_admin, db_engine, assigned_onboarding, seed_employee):
        ob_id, _ = assigned_onboarding
        _admin_session(client, seed_admin)
        for ctc in ("50000", "55000"):
            client.post("/offer_letter_save", data={
                "ob_id": ob_id, "employee_id": seed_employee["employee_id"],
                "designation": "Dev", "monthly_ctc": ctc,
            }, follow_redirects=False)
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*), MAX(monthly_ctc) FROM offer_letters WHERE onboarding_id=%s", (ob_id,))
        count, ctc = cur.fetchone()
        assert count == 1
        assert float(ctc) == 55000.0
        cur.close()

    def test_view_unknown_letter_id_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/offer_letter_view/999999", follow_redirects=True)
        assert resp.status_code == 200
        assert b"not found" in resp.data.lower()


class TestOfferLetterPdfGeneration:
    """_generate_offer_letter_pdf is the core artifact every candidate
    receives — exercised directly with a realistic letter tuple (matching
    offer_letter_send's SELECT column order) rather than through the full
    email-sending route, so PDF correctness is isolated from SMTP."""

    def _letter_tuple(self, **overrides):
        base = dict(
            id=1, onboarding_id=1, employee_id="TST001", designation="Software Engineer",
            department="Engineering", work_location="Remote", monthly_ctc=60000.0,
            joining_date=datetime.date.today(), offer_valid_until=datetime.date.today() + datetime.timedelta(days=7),
            probation_months=6, reporting_to="Engineering Manager", additional_notes="Welcome aboard!",
            generated_at=datetime.datetime.now(), sent_at=None, status="draft",
            notice_period_days=30, candidate_address="123 Main St", name="Jane Doe", email="jane@test.local",
        )
        base.update(overrides)
        return tuple(base.values())

    def test_generates_valid_pdf_bytes(self):
        co = {"company_name": "Acme Inc", "address": "1 Acme Way", "email": "hr@acme.test"}
        letter = self._letter_tuple()
        pdf_bytes = onboarding_module._generate_offer_letter_pdf(letter, co)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 1000

    def test_zero_ctc_omits_compensation_table_without_crashing(self):
        co = {"company_name": "Acme Inc"}
        letter = self._letter_tuple(monthly_ctc=0)
        pdf_bytes = onboarding_module._generate_offer_letter_pdf(letter, co)
        assert pdf_bytes[:4] == b"%PDF"

    def test_missing_dates_render_em_dash_without_crashing(self):
        co = {"company_name": "Acme Inc"}
        letter = self._letter_tuple(joining_date=None, offer_valid_until=None, generated_at=None)
        pdf_bytes = onboarding_module._generate_offer_letter_pdf(letter, co)
        assert pdf_bytes[:4] == b"%PDF"


class TestOfferLetterSend:
    def test_not_configured_flashes_error(self, client, seed_admin, monkeypatch, offer_letter_row):
        letter_id, _ = offer_letter_row
        monkeypatch.setattr(onboarding_module, "get_email_config", lambda: None)
        _admin_session(client, seed_admin)
        resp = client.post(f"/offer_letter_send/{letter_id}", follow_redirects=True)
        assert resp.status_code == 200
        assert b"not configured" in resp.data.lower()

    def test_send_success_generates_token_and_updates_status(self, client, seed_admin, db_engine, monkeypatch, offer_letter_row):
        letter_id, _ = offer_letter_row
        sent = {}

        def _fake_send(to_email, subject, html_body, config, attachment_bytes=None, attachment_filename=None):
            sent["to"] = to_email
            sent["subject"] = subject
            sent["attachment_bytes"] = attachment_bytes
            sent["attachment_filename"] = attachment_filename

        monkeypatch.setattr(onboarding_module, "get_email_config", lambda: {
            "host": "smtp.test.local", "port": 587, "user": "noreply@test.local",
            "password": "x", "from_name": "Test Co", "from_email": "noreply@test.local",
        })
        monkeypatch.setattr(onboarding_module, "send_email_smtp", _fake_send)

        _admin_session(client, seed_admin)
        resp = client.post(f"/offer_letter_send/{letter_id}", follow_redirects=True)
        assert resp.status_code == 200
        assert b"emailed" in resp.data.lower()

        assert sent["to"] == "emp@test.local"
        assert sent["attachment_bytes"][:4] == b"%PDF"
        assert sent["attachment_filename"].endswith(".pdf")

        cur = db_engine.cursor()
        cur.execute("SELECT status, response_token FROM offer_letters WHERE id=%s", (letter_id,))
        status, token_hash = cur.fetchone()
        assert status == "sent"
        assert token_hash is not None
        cur.close()


class TestOfferLetterCandidateFacingRoutes:
    @pytest.fixture
    def sent_letter(self, db_engine, offer_letter_row):
        letter_id, ob_id = offer_letter_row
        token = "test-plaintext-token-12345"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE offer_letters SET status='sent', response_token=%s, "
            "response_token_expiry=%s WHERE id=%s",
            (token_hash, datetime.datetime.utcnow() + datetime.timedelta(days=30), letter_id),
        )
        cur.close()
        return token, letter_id

    def test_pdf_view_with_valid_token(self, client, sent_letter):
        token, _ = sent_letter
        resp = client.get(f"/offer_letter_pdf/{token}")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert "inline" in resp.headers["Content-Disposition"]

    def test_pdf_download_disposition(self, client, sent_letter):
        token, _ = sent_letter
        resp = client.get(f"/offer_letter_pdf/{token}?dl=1")
        assert resp.status_code == 200
        assert "attachment" in resp.headers["Content-Disposition"]

    def test_pdf_invalid_token_404s(self, client):
        resp = client.get("/offer_letter_pdf/not-a-real-token")
        assert resp.status_code == 404

    def test_pdf_expired_token_404s(self, client, db_engine, offer_letter_row):
        letter_id, _ = offer_letter_row
        token = "expired-token-abc"
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE offer_letters SET response_token=%s, response_token_expiry=%s WHERE id=%s",
            (hashlib.sha256(token.encode()).hexdigest(),
             datetime.datetime.utcnow() - datetime.timedelta(days=1), letter_id),
        )
        cur.close()
        resp = client.get(f"/offer_letter_pdf/{token}")
        assert resp.status_code == 404

    def test_accept_response_recorded(self, client, db_engine, sent_letter):
        token, letter_id = sent_letter
        resp = client.get(f"/offer_letter_respond/{token}/accept")
        assert resp.status_code == 200
        assert b"Accepted" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT candidate_response, status FROM offer_letters WHERE id=%s", (letter_id,))
        assert cur.fetchone() == ("accept", "accepted")
        cur.close()

    def test_reject_response_recorded(self, client, db_engine, sent_letter):
        token, letter_id = sent_letter
        resp = client.get(f"/offer_letter_respond/{token}/reject")
        assert resp.status_code == 200
        assert b"Declined" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT candidate_response, status FROM offer_letters WHERE id=%s", (letter_id,))
        assert cur.fetchone() == ("reject", "rejected")
        cur.close()

    def test_responding_twice_shows_already_responded(self, client, sent_letter):
        token, _ = sent_letter
        client.get(f"/offer_letter_respond/{token}/accept")
        resp = client.get(f"/offer_letter_respond/{token}/accept")
        assert resp.status_code == 200
        assert b"already" in resp.data.lower()

    def test_invalid_action_rejected(self, client, sent_letter):
        token, _ = sent_letter
        resp = client.get(f"/offer_letter_respond/{token}/delete-everything")
        assert resp.status_code == 400

    def test_invalid_token_404s(self, client):
        resp = client.get("/offer_letter_respond/not-a-real-token/accept")
        assert resp.status_code == 404


# ===========================================================================
# Employee-facing self-service
# ===========================================================================

class TestMyOnboardingEmployeeFacing:
    def test_requires_employee_login(self, client):
        resp = client.get("/my_onboarding", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_renders_with_assignment(self, client, seed_employee, assigned_onboarding):
        _employee_session(client, seed_employee)
        resp = client.get("/my_onboarding")
        assert resp.status_code == 200

    def test_task_done_marks_status_and_saves_note(self, client, db_engine, seed_employee, assigned_onboarding):
        ob_id, task_row_ids = assigned_onboarding
        _employee_session(client, seed_employee)
        resp = client.post("/my_onboarding_task_done", data={
            "task_id": task_row_ids[0], "ob_id": ob_id, "employee_note": "Done via test",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT status, employee_note FROM employee_onboarding_tasks WHERE id=%s", (task_row_ids[0],))
        status, note = cur.fetchone()
        assert status == "Done"
        assert note == "Done via test"
        cur.close()

    def test_completing_all_tasks_auto_completes_onboarding(self, client, db_engine, seed_employee, assigned_onboarding):
        ob_id, task_row_ids = assigned_onboarding
        _employee_session(client, seed_employee)
        for task_id in task_row_ids:
            resp = client.post("/my_onboarding_task_done", data={
                "task_id": task_id, "ob_id": ob_id,
            }, follow_redirects=False)
            assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM employee_onboarding WHERE id=%s", (ob_id,))
        assert cur.fetchone()[0] == "Completed"
        cur.close()

    def test_cannot_complete_another_employees_task(self, client, db_engine, seed_admin, seed_employee, assigned_onboarding):
        """Ownership check: employee_onboarding_tasks.employee_id must match
        the logged-in employee — otherwise any employee could mark any
        other employee's onboarding task as done by guessing task IDs.

        A session for a nonexistent employee_id is indistinguishable from a
        deleted employee's stale session, so employee_required's
        _reject_if_account_deactivated() (see utils/auth.py) now kills it
        before my_onboarding_task_done()'s own body ever runs, redirecting
        to the unified login page with locked=1 rather than reaching the
        route's own (now-unreachable-for-this-case) ownership check. The
        DB-state assertion below is the real proof the task was never
        touched."""
        ob_id, task_row_ids = assigned_onboarding
        with client.session_transaction() as sess:
            sess["employee_id"] = "SOMEONE_ELSE"
        resp = client.post("/my_onboarding_task_done", data={
            "task_id": task_row_ids[0], "ob_id": ob_id,
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/admin-login" in resp.headers["Location"] and "locked=1" in resp.headers["Location"]

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM employee_onboarding_tasks WHERE id=%s", (task_row_ids[0],))
        assert cur.fetchone()[0] == "Pending"
        cur.close()


class TestMyOnboardingTaskDocumentUpload:
    """Regression coverage for the security fix to my_onboarding_task_done()'s
    document attachment: it used to save straight to static/onboarding_docs/
    (world-readable, no validation, no malware scan) with just the bare
    filename in document_path. Now goes through the same _validate_upload()
    + save_private() every other document upload in this app uses -- see
    blueprints/onboarding.py's _save_onboarding_task_document()."""

    def test_valid_document_is_stored_privately_not_under_static(self, client, db_engine, seed_employee, assigned_onboarding):
        ob_id, task_row_ids = assigned_onboarding
        _employee_session(client, seed_employee)
        resp = client.post("/my_onboarding_task_done", data={
            "task_id": task_row_ids[0], "ob_id": ob_id,
            "document": (io.BytesIO(b"%PDF-1.4 fake pdf content"), "certificate.pdf"),
        }, content_type="multipart/form-data", follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT status, document_path FROM employee_onboarding_tasks WHERE id=%s", (task_row_ids[0],))
        status, document_path = cur.fetchone()
        cur.close()
        assert status == "Done"
        # The pre-fix format was a bare "EMP_TASKID_certificate.pdf" with no
        # path separator at all, living directly under static/ -- a
        # save_private() stored_ref always has one (a private_uploads/...
        # local path, or an s3://... URL), and never contains "static".
        assert document_path is not None
        assert "/" in document_path or "\\" in document_path
        assert "static" not in document_path.replace("\\", "/").split("/")

    def test_disallowed_file_type_rejected_and_task_not_completed(self, client, db_engine, seed_employee, assigned_onboarding):
        ob_id, task_row_ids = assigned_onboarding
        _employee_session(client, seed_employee)
        resp = client.post("/my_onboarding_task_done", data={
            "task_id": task_row_ids[0], "ob_id": ob_id,
            "document": (io.BytesIO(b"MZ\x90\x00fake exe"), "payload.exe"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        assert b"not allowed" in resp.data.lower()
        cur = db_engine.cursor()
        cur.execute("SELECT status, document_path FROM employee_onboarding_tasks WHERE id=%s", (task_row_ids[0],))
        status, document_path = cur.fetchone()
        cur.close()
        assert status == "Pending"
        assert document_path is None

    def test_spoofed_extension_with_wrong_magic_bytes_rejected(self, client, db_engine, seed_employee, assigned_onboarding):
        """A file named *.pdf whose content isn't actually a PDF -- the
        extension/content-type allowlist alone wouldn't catch this; the
        magic-byte check inside _validate_upload() is what does."""
        ob_id, task_row_ids = assigned_onboarding
        _employee_session(client, seed_employee)
        resp = client.post("/my_onboarding_task_done", data={
            "task_id": task_row_ids[0], "ob_id": ob_id,
            "document": (io.BytesIO(b"not actually a pdf"), "certificate.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM employee_onboarding_tasks WHERE id=%s", (task_row_ids[0],))
        assert cur.fetchone()[0] == "Pending"
        cur.close()


class TestOnboardingDocumentDownload:
    """/onboarding_document/<task_id> -- the only sanctioned way to read a
    completed task's attachment back now that it's no longer served
    directly from static/."""

    @pytest.fixture
    def completed_task_with_doc(self, client, db_engine, seed_employee, assigned_onboarding):
        ob_id, task_row_ids = assigned_onboarding
        _employee_session(client, seed_employee)
        client.post("/my_onboarding_task_done", data={
            "task_id": task_row_ids[0], "ob_id": ob_id,
            "document": (io.BytesIO(b"%PDF-1.4 fake pdf content"), "certificate.pdf"),
        }, content_type="multipart/form-data")
        client.get("/logout")
        return task_row_ids[0]

    def test_requires_login(self, client, completed_task_with_doc):
        resp = client.get(f"/onboarding_document/{completed_task_with_doc}", follow_redirects=False)
        assert resp.status_code in (302, 401)
        assert "/login" in resp.headers.get("Location", "")

    def test_owning_employee_can_download(self, client, seed_employee, completed_task_with_doc):
        _employee_session(client, seed_employee)
        resp = client.get(f"/onboarding_document/{completed_task_with_doc}")
        assert resp.status_code == 200
        assert resp.data.startswith(b"%PDF")

    def test_other_employee_denied(self, client, db_engine, completed_task_with_doc):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
            "VALUES ('ONBOARD_OTHER','Other','other2@test.local','x',0) ON CONFLICT DO NOTHING"
        )
        db_engine.commit()
        cur.close()
        with client.session_transaction() as sess:
            sess["employee_id"] = "ONBOARD_OTHER"
        resp = client.get(f"/onboarding_document/{completed_task_with_doc}", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/my_onboarding" in resp.headers["Location"]
        cur = db_engine.cursor()
        cur.execute("DELETE FROM employees WHERE employee_id='ONBOARD_OTHER'")
        db_engine.commit()
        cur.close()

    def test_admin_can_download(self, client, seed_admin, completed_task_with_doc):
        _admin_session(client, seed_admin)
        resp = client.get(f"/onboarding_document/{completed_task_with_doc}")
        assert resp.status_code == 200
        assert resp.data.startswith(b"%PDF")

    def test_missing_document_redirects(self, client, seed_admin, assigned_onboarding):
        _, task_row_ids = assigned_onboarding
        _admin_session(client, seed_admin)
        resp = client.get(f"/onboarding_document/{task_row_ids[0]}", follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_stale_session_for_deleted_employee_redirects_to_login(self, client):
        """my_onboarding() used to crash with a 500 (emp[1] on None) if the
        session's employee_id didn't match any row in employees — e.g. an
        admin deletes an employee who is still logged in elsewhere.

        That case is now caught earlier, at the auth layer:
        employee_required's _reject_if_account_deactivated() (utils/auth.py)
        treats a missing employees row the same as a deactivated one and
        kills the session before the route body runs, redirecting to the
        unified login page with locked=1."""
        with client.session_transaction() as sess:
            sess["employee_id"] = "DOES_NOT_EXIST"
        resp = client.get("/my_onboarding", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert "/admin-login" in resp.headers["Location"] and "locked=1" in resp.headers["Location"]
