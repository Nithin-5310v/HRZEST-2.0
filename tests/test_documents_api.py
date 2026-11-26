"""The mobile app's document APIs (blueprints/documents.py): the admin/HR
list, upload and delete (/api/documents*) and the employee's own
(/api/employee/documents*). Files really are written, encrypted, under a
temporary private_uploads/ and removed on delete."""
import hashlib
import io
import os
import secrets

import pytest

FAKE_PDF = b"%PDF-1.4\n" + b"x" * 40


def _bearer(db_engine, identity, token_type):
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s, %s, %s, NOW() + INTERVAL '1 hour')",
        (identity, hashlib.sha256(raw.encode()).hexdigest(), token_type),
    )
    cur.close()
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture
def private_root(tmp_path, monkeypatch):
    """Uploads go to <tmp_path>/private_uploads, which is also where
    open_private()/delete_private() look."""
    import blueprints.documents as documents
    from utils import storage
    monkeypatch.setattr(storage, "_APP_ROOT", str(tmp_path))
    real_save = storage.save_private
    monkeypatch.setattr(documents, "save_private", lambda _root, f, rel: real_save(str(tmp_path), f, rel))
    return tmp_path


@pytest.fixture
def team_scoped_hr(db_engine, seed_hr_admin):
    """The built-in 'hr' role limited to its own people (TST002 via
    seed_assigned_employee; TST001 is outside it)."""
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO roles (role_key, display_name, base_type, is_system, data_scope) "
        "VALUES ('hr', 'HR', 'hr', FALSE, 'team') ON CONFLICT (role_key) DO UPDATE SET data_scope='team'"
    )
    cur.close()
    return seed_hr_admin


def _upload(client, headers, url="/api/documents/upload", data=None, filename="offer.pdf", content=FAKE_PDF):
    form = {"doc_type": "Offer Letter", "document": (io.BytesIO(content), filename)}
    form.update(data or {})
    return client.post(url, data=form, content_type="multipart/form-data", headers=headers)


def _docs(db_engine, employee_id):
    cur = db_engine.cursor()
    cur.execute("SELECT id, doc_type, uploaded_by, stored_name FROM employee_documents WHERE employee_id=%s "
                "ORDER BY id", (employee_id,))
    rows = cur.fetchall()
    cur.close()
    return rows


def _insert_doc(db_engine, employee_id, stored_name="/nonexistent/doc.pdf"):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by) "
                "VALUES (%s, 'ID Proof', 'id.pdf', %s, 'admin') RETURNING id", (employee_id, stored_name))
    did = cur.fetchone()[0]
    cur.close()
    return did


# ── /api/documents* (admin and HR tokens) ───────────────────────────────────

class TestAdminDocumentsApiGuards:
    @pytest.mark.parametrize("method,url", [
        ("get", "/api/documents"), ("post", "/api/documents/upload"), ("delete", "/api/documents/1"),
    ])
    def test_needs_an_admin_token(self, client, db_engine, seed_employee, method, url):
        assert getattr(client, method)(url).status_code == 401
        emp = _bearer(db_engine, seed_employee["employee_id"], "employee")
        assert getattr(client, method)(url, headers=emp).status_code == 401

    @pytest.mark.parametrize("method,url", [
        ("get", "/api/documents"), ("post", "/api/documents/upload"), ("delete", "/api/documents/1"),
    ])
    def test_a_manager_token_is_refused(self, client, db_engine, seed_manager_admin, method, url):
        headers = _bearer(db_engine, seed_manager_admin["username"], "admin")
        assert getattr(client, method)(url, headers=headers).status_code == 403


class TestAdminDocumentsApi:
    def test_upload_list_and_delete(self, client, db_engine, seed_admin, seed_employee, private_root):
        headers = _bearer(db_engine, seed_admin["username"], "admin")
        resp = _upload(client, headers, data={"employee_id": "TST001"})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_json()

        [(did, doc_type, uploaded_by, stored)] = _docs(db_engine, "TST001")
        assert (doc_type, uploaded_by) == ("Offer Letter", "admin")
        stored_path = os.path.realpath(stored)
        assert stored_path.startswith(os.path.realpath(private_root / "private_uploads" / "employee_documents"))
        assert os.path.isfile(stored_path)
        with open(stored_path, "rb") as fh:
            assert FAKE_PDF not in fh.read(), "documents are encrypted at rest"

        listed = client.get("/api/documents", headers=headers).get_json()["documents"]
        assert [(d["id"], d["employee_id"], d["original_name"]) for d in listed] == [(did, "TST001", "offer.pdf")]
        assert client.get("/api/documents?emp_id=TST001", headers=headers).get_json()["documents"][0]["id"] == did
        assert client.get("/api/documents?emp_id=NOPE99", headers=headers).get_json()["documents"] == []

        resp = client.delete(f"/api/documents/{did}", headers=headers)
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        assert _docs(db_engine, "TST001") == []
        assert not os.path.exists(stored_path)
        assert client.delete(f"/api/documents/{did}", headers=headers).status_code == 404

    @pytest.mark.parametrize("data,filename,content,needle", [
        ({}, "offer.pdf", FAKE_PDF, "required"),
        ({"employee_id": "TST001", "doc_type": ""}, "offer.pdf", FAKE_PDF, "required"),
        ({"employee_id": "NOPE99"}, "offer.pdf", FAKE_PDF, "Unknown employee_id"),
        ({"employee_id": "TST001"}, "tool.exe", b"MZ\x90\x00", "not allowed"),
        ({"employee_id": "TST001"}, "fake.pdf", b"not a pdf at all", "Invalid PDF"),
    ])
    def test_bad_uploads_store_nothing(self, client, db_engine, seed_admin, seed_employee, private_root,
                                       data, filename, content, needle):
        headers = _bearer(db_engine, seed_admin["username"], "admin")
        resp = _upload(client, headers, data=data, filename=filename, content=content)
        assert resp.status_code == 400 and needle in resp.get_json()["msg"]
        assert _docs(db_engine, "TST001") == []
        assert not (private_root / "private_uploads").exists()


class TestHrTeamScope:
    def test_upload_only_for_own_people(self, client, db_engine, team_scoped_hr, seed_employee,
                                        seed_assigned_employee, private_root):
        headers = _bearer(db_engine, team_scoped_hr["username"], "admin")
        resp = _upload(client, headers, data={"employee_id": "TST001"})
        assert resp.status_code == 404
        assert _docs(db_engine, "TST001") == []
        assert _upload(client, headers, data={"employee_id": "TST002"}).status_code == 200
        assert len(_docs(db_engine, "TST002")) == 1

    def test_list_shows_only_own_people(self, client, db_engine, team_scoped_hr, seed_employee,
                                        seed_assigned_employee):
        outside = _insert_doc(db_engine, "TST001")
        inside = _insert_doc(db_engine, "TST002")
        headers = _bearer(db_engine, team_scoped_hr["username"], "admin")
        assert [d["id"] for d in client.get("/api/documents", headers=headers).get_json()["documents"]] == [inside]
        assert client.get("/api/documents?emp_id=TST001", headers=headers).get_json()["documents"] == []
        assert outside not in [d["id"] for d in
                               client.get("/api/documents?emp_id=TST002", headers=headers).get_json()["documents"]]

    def test_cannot_delete_someone_elses_document(self, client, db_engine, team_scoped_hr, seed_employee):
        outside = _insert_doc(db_engine, "TST001")
        headers = _bearer(db_engine, team_scoped_hr["username"], "admin")
        assert client.delete(f"/api/documents/{outside}", headers=headers).status_code == 404
        assert [r[0] for r in _docs(db_engine, "TST001")] == [outside]


# ── /api/employee/documents* (the employee's own) ───────────────────────────

class TestEmployeeDocumentsApi:
    @pytest.mark.parametrize("method,url", [
        ("get", "/api/employee/documents"), ("post", "/api/employee/documents/upload"),
        ("delete", "/api/employee/documents/1"),
    ])
    def test_needs_an_employee_token(self, client, method, url):
        assert getattr(client, method)(url).status_code == 401
        assert getattr(client, method)(url, headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_upload_list_and_delete_own(self, client, db_engine, seed_employee, seed_assigned_employee,
                                        private_root):
        headers = _bearer(db_engine, seed_employee["employee_id"], "employee")
        # employee_id in the form is ignored: the token decides whose it is.
        resp = _upload(client, headers, url="/api/employee/documents/upload", data={"employee_id": "TST002"})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True, resp.get_json()
        assert _docs(db_engine, "TST002") == []
        [(did, _type, uploaded_by, stored)] = _docs(db_engine, "TST001")
        assert uploaded_by == "employee" and os.path.isfile(stored)

        someone_else = _insert_doc(db_engine, "TST002")
        listed = client.get("/api/employee/documents", headers=headers).get_json()["documents"]
        assert [d["id"] for d in listed] == [did]

        assert client.delete(f"/api/employee/documents/{someone_else}", headers=headers).status_code == 404
        assert [r[0] for r in _docs(db_engine, "TST002")] == [someone_else]

        assert client.delete(f"/api/employee/documents/{did}", headers=headers).status_code == 200
        assert _docs(db_engine, "TST001") == [] and not os.path.exists(stored)

    @pytest.mark.parametrize("data,filename,content,needle", [
        ({"doc_type": ""}, "offer.pdf", FAKE_PDF, "required"),
        ({}, "tool.exe", b"MZ\x90\x00", "not allowed"),
        ({}, "fake.pdf", b"not a pdf at all", "Invalid PDF"),
    ])
    def test_bad_uploads_store_nothing(self, client, db_engine, seed_employee, private_root,
                                       data, filename, content, needle):
        headers = _bearer(db_engine, seed_employee["employee_id"], "employee")
        resp = _upload(client, headers, url="/api/employee/documents/upload", data=data, filename=filename,
                       content=content)
        assert resp.status_code == 400 and needle in resp.get_json()["msg"]
        assert _docs(db_engine, "TST001") == []
