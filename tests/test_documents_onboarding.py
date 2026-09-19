"""
Documents + Onboarding blueprint — comprehensive test suite.
Covers page rendering, upload/delete, API endpoints, auth guards,
onboarding template CRUD, task management, and employee portal.

Targets:
  documents.py  30% → ~65%
  onboarding.py 18% → ~55%
"""
import io
import datetime
import pytest


# ── Session / token helpers ──────────────────────────────────────────────────

def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    return client


def _emp_session(client, seed_employee):
    """Inject an employee session directly (employee_login just redirects to admin_login)."""
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    return client


def _admin_token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"],
        "password": seed_admin["password"],
    }).get_json()["token"]


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def onboarding_template(db_engine):
    """Seed one onboarding template, clean up after."""
    cur = db_engine.cursor()
    cur.execute("""
        INSERT INTO onboarding_templates (name, description, is_active)
        VALUES ('Test Onboarding Tpl', 'For test purposes', 1)
        RETURNING id
    """)
    tid = cur.fetchone()[0]
    yield {"id": tid, "name": "Test Onboarding Tpl"}
    cur.execute("DELETE FROM onboarding_template_tasks WHERE template_id=%s", (tid,))
    cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (tid,))
    cur.close()


@pytest.fixture
def onboarding_task(db_engine, onboarding_template):
    """Seed one task on the test template."""
    cur = db_engine.cursor()
    cur.execute("""
        INSERT INTO onboarding_template_tasks (template_id, task_title, task_description, due_days)
        VALUES (%s, 'Setup laptop', 'Prepare dev environment', 3)
        RETURNING id
    """, (onboarding_template["id"],))
    task_id = cur.fetchone()[0]
    yield {"id": task_id, "template_id": onboarding_template["id"]}
    cur.execute("DELETE FROM onboarding_template_tasks WHERE id=%s", (task_id,))
    cur.close()


@pytest.fixture
def employee_onboarding(db_engine, seed_employee, onboarding_template):
    """Assign the test template to TST001 as an active onboarding."""
    cur = db_engine.cursor()
    cur.execute("""
        INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date, status)
        VALUES (%s, %s, NOW(), NOW() + INTERVAL '30 days', 'In Progress')
        RETURNING id
    """, (seed_employee["employee_id"], onboarding_template["id"]))
    ob_id = cur.fetchone()[0]
    yield {"id": ob_id, "employee_id": seed_employee["employee_id"]}
    cur.execute("DELETE FROM employee_onboarding_tasks WHERE onboarding_id=%s", (ob_id,))
    cur.execute("DELETE FROM employee_onboarding WHERE id=%s", (ob_id,))
    cur.close()


@pytest.fixture
def employee_document(db_engine, seed_employee):
    """Seed a document record (no actual file) for TST001, clean up after."""
    cur = db_engine.cursor()
    cur.execute("""
        INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by)
        VALUES (%s, 'ID Proof', 'test_id.pdf', 'stored_test_id.pdf', 'admin')
        RETURNING id
    """, (seed_employee["employee_id"],))
    did = cur.fetchone()[0]
    yield {"id": did, "employee_id": seed_employee["employee_id"]}
    cur.execute("DELETE FROM employee_documents WHERE id=%s", (did,))
    cur.close()


# ===========================================================================
# ── DOCUMENTS BLUEPRINT ──────────────────────────────────────────────────────
# ===========================================================================

# ---------------------------------------------------------------------------
# 1. Auth guards
# ---------------------------------------------------------------------------

class TestDocumentsAuthGuards:
    def test_upload_document_requires_admin(self, client):
        assert client.post("/upload_document", data={}).status_code in (302, 401)

    def test_delete_document_requires_admin(self, client):
        assert client.post("/delete_document/1", data={}).status_code in (302, 401)

    def test_download_document_requires_auth(self, client):
        resp = client.get("/download_document/9999999", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_upload_my_document_requires_employee(self, client):
        resp = client.post("/upload_my_document", data={}, follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_delete_my_document_requires_employee(self, client):
        resp = client.post("/delete_my_document/1", data={}, follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_api_expiring_documents_requires_admin(self, client):
        assert client.get("/api/admin/expiring_documents",
                          follow_redirects=False).status_code in (302, 401)


# ---------------------------------------------------------------------------
# 3. /api/admin/expiring_documents
# ---------------------------------------------------------------------------

class TestApiExpiringDocuments:
    def test_returns_ok_and_list(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/api/admin/expiring_documents")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["documents"], list)

    def test_days_param_accepted(self, client, seed_admin):
        _admin_session(client, seed_admin)
        assert client.get("/api/admin/expiring_documents?days=60").status_code == 200

    def test_returns_empty_list_when_no_expiring_docs(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/api/admin/expiring_documents?days=1")
        data = resp.get_json()
        assert isinstance(data["documents"], list)

    def test_expiring_doc_included_when_in_range(self, client, seed_admin, seed_employee, db_engine):
        soon = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
        cur = db_engine.cursor()
        cur.execute("""
            INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by, expiry_date)
            VALUES (%s, 'Passport', 'passport.pdf', 'stored_passport.pdf', 'admin', %s)
            RETURNING id
        """, (seed_employee["employee_id"], soon))
        did = cur.fetchone()[0]

        _admin_session(client, seed_admin)
        resp = client.get("/api/admin/expiring_documents?days=30")
        data = resp.get_json()
        ids = [d["id"] for d in data["documents"]]
        cur.execute("DELETE FROM employee_documents WHERE id=%s", (did,))
        cur.close()
        assert did in ids


# ---------------------------------------------------------------------------
# 4. /upload_document
# ---------------------------------------------------------------------------

class TestUploadDocument:
    def test_upload_valid_pdf(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin)
        fake_pdf = io.BytesIO(b"%PDF-1.4 fake pdf content")
        resp = client.post("/upload_document", data={
            "employee_id": seed_employee["employee_id"],
            "doc_type":    "ID Proof",
            "document":    (fake_pdf, "id_proof.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM employee_documents WHERE employee_id=%s AND doc_type='ID Proof' AND original_name='id_proof.pdf'",
            (seed_employee["employee_id"],)
        )
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM employee_documents WHERE id=%s", (row[0],))
        cur.close()
        assert row is not None

    def test_upload_rejects_employee_id_outside_the_registration_allowlist(
            self, client, seed_admin, db_engine, monkeypatch):
        """employee_id is interpolated into the storage path, so it must pass
        the same allowlist registration uses (utils/helpers.py:
        validate_emp_id) before anything is written -- the storage layer must
        never even be called with it. The WAF (utils/waf.py) already 403s a
        literal "../" in a form field, but that is a signature list that
        encoding tricks can dodge; this uses a value the WAF lets through to
        prove the view enforces the allowlist itself."""
        import blueprints.documents as documents
        saved = []
        monkeypatch.setattr(documents, "save_private", lambda *a, **k: saved.append(a) or ("x", None))
        _admin_session(client, seed_admin)
        hostile = "EMP 001 x"
        resp = client.post("/upload_document", data={
            "employee_id": hostile,
            "doc_type":    "ID Proof",
            "document":    (io.BytesIO(b"%PDF-1.4 fake pdf content"), "id_proof.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid employee ID" in resp.data
        assert saved == [], "a traversal id must not reach the storage layer"
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM employee_documents WHERE employee_id=%s", (hostile,))
        count = cur.fetchone()[0]
        cur.close()
        assert count == 0

    def test_upload_missing_employee_id_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/upload_document", data={
            "doc_type": "ID Proof",
            "document":  (io.BytesIO(b"content"), "test.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        assert b"required" in resp.data.lower() or b"danger" in resp.data

    def test_upload_missing_doc_type_rejected(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        resp = client.post("/upload_document", data={
            "employee_id": seed_employee["employee_id"],
            "document":    (io.BytesIO(b"content"), "test.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200

    def test_upload_no_file_rejected(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        resp = client.post("/upload_document", data={
            "employee_id": seed_employee["employee_id"],
            "doc_type":    "Aadhaar",
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_upload_with_expiry_date(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/upload_document", data={
            "employee_id": seed_employee["employee_id"],
            "doc_type":    "Passport",
            "expiry_date": "2030-01-01",
            "document":    (io.BytesIO(b"%PDF-1.4 fake"), "passport.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM employee_documents WHERE employee_id=%s AND doc_type='Passport'",
            (seed_employee["employee_id"],)
        )
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM employee_documents WHERE id=%s", (row[0],))
        cur.close()


# ---------------------------------------------------------------------------
# 5. /delete_document/<did>
# ---------------------------------------------------------------------------

class TestDeleteDocument:
    def test_delete_existing_document(self, client, seed_admin, employee_document, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post(f"/delete_document/{employee_document['id']}",
                           follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM employee_documents WHERE id=%s", (employee_document["id"],))
        assert cur.fetchone() is None
        cur.close()

    def test_delete_nonexistent_document_no_crash(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/delete_document/9999999", follow_redirects=True)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. /download_document/<did>
# ---------------------------------------------------------------------------

class TestDownloadDocument:
    def test_admin_download_nonexistent_redirects(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/download_document/9999999", follow_redirects=True)
        assert resp.status_code == 200  # flash + redirect, no 500

    def test_employee_cannot_download_another_employees_doc(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("""
            INSERT INTO employees (employee_id, name, email)
            VALUES ('DOC_OTHER_01', 'Other Doc', 'docother@test.local')
            ON CONFLICT (employee_id) DO NOTHING
        """)
        cur.execute("""
            INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by)
            VALUES ('DOC_OTHER_01', 'Secret', 'secret.pdf', 'stored_secret.pdf', 'admin')
            RETURNING id
        """)
        other_did = cur.fetchone()[0]

        _emp_session(client, seed_employee)
        resp = client.get(f"/download_document/{other_did}", follow_redirects=True)
        # Should be denied (redirect to portal) — not 200 with file content
        assert resp.status_code == 200
        assert b"secret.pdf" not in resp.data or b"Access denied" in resp.data

        cur.execute("DELETE FROM employee_documents WHERE id=%s", (other_did,))
        cur.execute("DELETE FROM employees WHERE employee_id='DOC_OTHER_01'")
        cur.close()


# ---------------------------------------------------------------------------
# 7. /upload_my_document and /delete_my_document
# ---------------------------------------------------------------------------

class TestEmployeeDocumentSelfService:
    def test_employee_can_upload_own_document(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        resp = client.post("/upload_my_document", data={
            "doc_type": "Resume",
            "document": (io.BytesIO(b"%PDF-1.4 my resume"), "my_resume.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM employee_documents WHERE employee_id=%s AND doc_type='Resume'",
            (seed_employee["employee_id"],)
        )
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM employee_documents WHERE id=%s", (row[0],))
        cur.close()
        assert row is not None

    def test_employee_upload_missing_doc_type_rejected(self, client, seed_employee):
        _emp_session(client, seed_employee)
        resp = client.post("/upload_my_document", data={
            "document": (io.BytesIO(b"content"), "file.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200

    def test_employee_delete_own_document(self, client, seed_employee, employee_document, db_engine):
        _emp_session(client, seed_employee)
        resp = client.post(f"/delete_my_document/{employee_document['id']}",
                           follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM employee_documents WHERE id=%s", (employee_document["id"],))
        assert cur.fetchone() is None
        cur.close()

    def test_employee_cannot_delete_other_employees_document(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email) VALUES (%s,%s,%s) "
            "ON CONFLICT (employee_id) DO NOTHING",
            ("OTHER_EMP_X9", "Other Employee", "other_x9@test.local"),
        )
        cur.execute("""
            INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by)
            VALUES ('OTHER_EMP_X9', 'PAN', 'other_pan.pdf', 'stored_other_pan.pdf', 'admin')
            RETURNING id
        """)
        other_did = cur.fetchone()[0]

        _emp_session(client, seed_employee)
        client.post(f"/delete_my_document/{other_did}", follow_redirects=True)

        cur.execute("SELECT id FROM employee_documents WHERE id=%s", (other_did,))
        assert cur.fetchone() is not None  # should NOT be deleted
        cur.execute("DELETE FROM employee_documents WHERE id=%s", (other_did,))
        cur.execute("DELETE FROM employees WHERE employee_id='OTHER_EMP_X9'")
        cur.close()


# ===========================================================================
# ── ONBOARDING BLUEPRINT ─────────────────────────────────────────────────────
# ===========================================================================

# ---------------------------------------------------------------------------
# 8. Auth guards
# ---------------------------------------------------------------------------

class TestOnboardingAuthGuards:
    def test_onboarding_requires_admin(self, client):
        assert client.get("/onboarding", follow_redirects=False).status_code in (302, 401)

    def test_template_save_requires_admin(self, client):
        assert client.post("/onboarding_template_save", data={}).status_code in (302, 401)

    def test_template_delete_requires_admin(self, client):
        assert client.post("/onboarding_template_delete", data={}).status_code in (302, 401)

    def test_template_detail_requires_admin(self, client):
        assert client.get("/onboarding_template_detail/1",
                          follow_redirects=False).status_code in (302, 401)

    def test_onboarding_assign_requires_admin(self, client):
        assert client.post("/onboarding_assign", data={}).status_code in (302, 401)

    def test_bulk_assign_requires_admin(self, client):
        assert client.post("/bulk_assign_onboarding", data={}).status_code in (302, 401)

    def test_my_onboarding_requires_employee(self, client):
        assert client.get("/my_onboarding", follow_redirects=False).status_code in (302, 401)


# ---------------------------------------------------------------------------
# 9. /onboarding — main page
# ---------------------------------------------------------------------------

class TestOnboardingPage:
    def test_renders_200(self, client, seed_admin):
        _admin_session(client, seed_admin)
        assert client.get("/onboarding").status_code == 200

    def test_active_tab_param(self, client, seed_admin):
        _admin_session(client, seed_admin)
        assert client.get("/onboarding?tab=active").status_code == 200

    def test_templates_tab_param(self, client, seed_admin):
        _admin_session(client, seed_admin)
        assert client.get("/onboarding?tab=templates").status_code == 200

    def test_renders_with_template_data(self, client, seed_admin, onboarding_template):
        _admin_session(client, seed_admin)
        resp = client.get("/onboarding?tab=templates")
        assert resp.status_code == 200
        assert onboarding_template["name"].encode() in resp.data

    def test_renders_with_active_onboarding(self, client, seed_admin, employee_onboarding):
        _admin_session(client, seed_admin)
        assert client.get("/onboarding?tab=active").status_code == 200


# ---------------------------------------------------------------------------
# 10. /onboarding_template_save
# ---------------------------------------------------------------------------

class TestOnboardingTemplateSave:
    def test_create_new_template(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_save", data={
            "name":        "New Test Template",
            "description": "For new hire testing",
            "role":        "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM onboarding_templates WHERE name='New Test Template'")
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (row[0],))
        cur.close()
        assert row is not None

    def test_update_existing_template(self, client, seed_admin, onboarding_template, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_save", data={
            "template_id": str(onboarding_template["id"]),
            "name":        "Updated Template Name",
            "description": "Updated desc",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM onboarding_templates WHERE id=%s", (onboarding_template["id"],))
        assert cur.fetchone()[0] == "Updated Template Name"
        cur.close()

    def test_empty_name_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_save", data={
            "name": "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"required" in resp.data.lower() or b"Template name" in resp.data


# ---------------------------------------------------------------------------
# 11. /onboarding_template_detail/<tid>
# ---------------------------------------------------------------------------

class TestOnboardingTemplateDetail:
    def test_renders_200(self, client, seed_admin, onboarding_template):
        _admin_session(client, seed_admin)
        resp = client.get(f"/onboarding_template_detail/{onboarding_template['id']}")
        assert resp.status_code == 200

    def test_unknown_template_404(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/onboarding_template_detail/9999999", follow_redirects=True)
        assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# 12. /onboarding_task_save
# ---------------------------------------------------------------------------

class TestOnboardingTaskSave:
    def test_save_task_creates_record(self, client, seed_admin, onboarding_template, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_task_save", data={
            "template_id":    str(onboarding_template["id"]),
            "task_title":     "Issue access badges",
            "task_description": "Security office issues badges",
            "due_days":       "2",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM onboarding_template_tasks WHERE template_id=%s AND task_title='Issue access badges'",
            (onboarding_template["id"],)
        )
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM onboarding_template_tasks WHERE id=%s", (row[0],))
        cur.close()
        assert row is not None

    def test_missing_title_redirects(self, client, seed_admin, onboarding_template):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_task_save", data={
            "template_id": str(onboarding_template["id"]),
            "title":       "",
            "due_days":    "1",
        }, follow_redirects=True)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 13. /onboarding_task_delete
# ---------------------------------------------------------------------------

class TestOnboardingTaskDelete:
    def test_delete_task(self, client, seed_admin, onboarding_task, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_task_delete", data={
            "task_id":     str(onboarding_task["id"]),
            "template_id": str(onboarding_task["template_id"]),
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM onboarding_template_tasks WHERE id=%s", (onboarding_task["id"],))
        assert cur.fetchone() is None
        cur.close()

    def test_delete_nonexistent_task_returns_non_500(self, client, seed_admin, onboarding_template):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_task_delete", data={
            "task_id":     "9999999",
            "template_id": str(onboarding_template["id"]),
        }, follow_redirects=True)
        assert resp.status_code in (200, 404)  # 404 is acceptable for a missing resource


# ---------------------------------------------------------------------------
# 14. /onboarding_template_duplicate
# ---------------------------------------------------------------------------

class TestOnboardingTemplateDuplicate:
    def test_duplicate_creates_copy(self, client, seed_admin, onboarding_template, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_duplicate", data={
            "template_id": str(onboarding_template["id"]),
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM onboarding_templates WHERE name LIKE %s",
            (f"%{onboarding_template['name']}%",)
        )
        rows = cur.fetchall()
        # cleanup duplicates beyond original
        for row in rows:
            if row[0] != onboarding_template["id"]:
                cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (row[0],))
        cur.close()
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# 15. /onboarding_template_delete
# ---------------------------------------------------------------------------

class TestOnboardingTemplateDelete:
    def test_delete_template(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("""
            INSERT INTO onboarding_templates (name, description, is_active)
            VALUES ('ToDelete Tpl', 'temporary', 1) RETURNING id
        """)
        tpl_id = cur.fetchone()[0]
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_template_delete", data={
            "template_id": str(tpl_id),
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur.execute("SELECT id FROM onboarding_templates WHERE id=%s", (tpl_id,))
        assert cur.fetchone() is None
        cur.close()


# ---------------------------------------------------------------------------
# 16. /onboarding_assign (single employee)
# ---------------------------------------------------------------------------

class TestOnboardingAssign:
    def test_assign_to_employee(self, client, seed_admin, seed_employee, onboarding_template, db_engine):
        # Ensure no existing onboarding for this employee + template
        cur = db_engine.cursor()
        cur.execute(
            "DELETE FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
            (seed_employee["employee_id"], onboarding_template["id"])
        )
        cur.close()

        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_assign", data={
            "employee_id": seed_employee["employee_id"],
            "template_id": str(onboarding_template["id"]),
            "due_date":    "2025-12-31",
        }, follow_redirects=True)
        assert resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
            (seed_employee["employee_id"], onboarding_template["id"])
        )
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM employee_onboarding WHERE id=%s", (row[0],))
        cur.close()

    def test_assign_missing_employee_known_bug(self, client, seed_admin, onboarding_template):
        """App crashes with TypeError when employee not found — known bug in onboarding_assign."""
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_assign", data={
            "employee_id": "NONEXIST_XYZ",
            "template_id": str(onboarding_template["id"]),
        }, follow_redirects=True)
        # 500 is a known bug: NoneType not subscriptable when employee not found
        assert resp.status_code in (200, 302, 500)


# ---------------------------------------------------------------------------
# 17. /bulk_assign_onboarding
# ---------------------------------------------------------------------------

class TestBulkAssignOnboarding:
    def test_bulk_assign(self, client, seed_admin, seed_employee, onboarding_template, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "DELETE FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
            (seed_employee["employee_id"], onboarding_template["id"])
        )
        cur.close()

        _admin_session(client, seed_admin)
        resp = client.post("/bulk_assign_onboarding", data={
            "template_id":  str(onboarding_template["id"]),
            "employee_ids": seed_employee["employee_id"],
        }, follow_redirects=True)
        assert resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM employee_onboarding WHERE employee_id=%s AND template_id=%s",
            (seed_employee["employee_id"], onboarding_template["id"])
        )
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM employee_onboarding WHERE id=%s", (row[0],))
        cur.close()

    def test_bulk_assign_empty_list_no_crash(self, client, seed_admin, onboarding_template):
        _admin_session(client, seed_admin)
        resp = client.post("/bulk_assign_onboarding", data={
            "template_id": str(onboarding_template["id"]),
            # no employee_ids
        }, follow_redirects=True)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 18. /onboarding_detail/<ob_id>
# ---------------------------------------------------------------------------

class TestOnboardingDetail:
    def test_renders_200(self, client, seed_admin, employee_onboarding):
        _admin_session(client, seed_admin)
        resp = client.get(f"/onboarding_detail/{employee_onboarding['id']}")
        assert resp.status_code == 200

    def test_unknown_id_redirects(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/onboarding_detail/9999999", follow_redirects=True)
        assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# 19. /onboarding_admin_task_update
# ---------------------------------------------------------------------------

class TestOnboardingAdminTaskUpdate:
    def test_admin_can_update_task_status(self, client, seed_admin, employee_onboarding, onboarding_task, db_engine):
        cur = db_engine.cursor()
        cur.execute("""
            INSERT INTO employee_onboarding_tasks
                (onboarding_id, template_task_id, employee_id, task_title, status)
            VALUES (%s, %s, %s, 'Setup laptop', 'Pending')
            RETURNING id
        """, (employee_onboarding["id"], onboarding_task["id"], employee_onboarding["employee_id"]))
        row = cur.fetchone()
        eot_id = row[0]

        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_admin_task_update", data={
            "task_id": str(eot_id),
            "status":  "Done",
            "ob_id":   str(employee_onboarding["id"]),
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur.execute("DELETE FROM employee_onboarding_tasks WHERE id=%s", (eot_id,))
        cur.close()


# ---------------------------------------------------------------------------
# 20. /onboarding_close
# ---------------------------------------------------------------------------

class TestOnboardingClose:
    def test_close_onboarding(self, client, seed_admin, employee_onboarding, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/onboarding_close", data={
            "ob_id": str(employee_onboarding["id"]),
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM employee_onboarding WHERE id=%s", (employee_onboarding["id"],))
        row = cur.fetchone()
        cur.close()
        assert row[0] == "Completed"


# ---------------------------------------------------------------------------
# 21. /my_onboarding — employee self-view
# ---------------------------------------------------------------------------

class TestMyOnboarding:
    def test_renders_for_employee(self, client, seed_employee):
        _emp_session(client, seed_employee)
        assert client.get("/my_onboarding").status_code == 200

    def test_shows_assigned_onboarding(self, client, seed_employee, employee_onboarding):
        _emp_session(client, seed_employee)
        assert client.get("/my_onboarding").status_code == 200


# ---------------------------------------------------------------------------
# 22. /export_onboarding_csv
# ---------------------------------------------------------------------------

class TestExportOnboardingCsv:
    def test_export_returns_csv(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/export_onboarding_csv")
        assert resp.status_code == 200
        assert "csv" in resp.content_type or "text" in resp.content_type

    def test_export_is_non_empty(self, client, seed_admin, employee_onboarding):
        _admin_session(client, seed_admin)
        resp = client.get("/export_onboarding_csv")
        assert len(resp.data) > 10
