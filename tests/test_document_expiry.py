"""Document expiry dates (utils/document_expiry.py, blueprints/documents.py).

Before this, nothing in the web or mobile app could set a document's expiry
date, so the analytics "documents expiring" alert always counted zero; and
when a request did carry one, a malformed value reached the DATE column
after the file was already stored -- a 500 that left the file behind.
"""
import datetime
import io

import pytest

import blueprints.documents as documents
from utils import document_expiry
from utils.document_expiry import FORMAT_ERROR, expiry_status, parse_expiry

TODAY = datetime.date.today()


def _days(n):
    return TODAY + datetime.timedelta(days=n)


# ── parse_expiry / expiry_status (no database) ──────────────────────────────

@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_means_no_expiry(raw):
    assert parse_expiry(raw) == (None, None)


def test_iso_date_is_accepted():
    assert parse_expiry(" 2027-03-12 ") == (datetime.date(2027, 3, 12), None)


@pytest.mark.parametrize("raw", ["31/12/2027", "2027-02-30", "20270312", "2027-W10-1", "2027-3-1", "soon",
                                 "2027-03-12T00:00:00", "2027-03-12; DROP TABLE x"])
def test_anything_else_is_refused(raw):
    assert parse_expiry(raw) == (None, FORMAT_ERROR)


def test_expiry_status_by_company_date(monkeypatch):
    monkeypatch.setattr(document_expiry, "company_today", lambda: datetime.date(2027, 1, 10))
    assert expiry_status(None) == (None, None)
    assert expiry_status(datetime.date(2027, 1, 9)) == ("expired", -1)
    assert expiry_status(datetime.date(2027, 1, 10)) == ("soon", 0)
    assert expiry_status(datetime.date(2027, 2, 9)) == ("soon", 30)
    assert expiry_status(datetime.date(2027, 2, 10)) == ("ok", 31)
    assert expiry_status("2027-01-11") == ("soon", 1)      # TEXT column in the SQLite dev fallback


# ── helpers ─────────────────────────────────────────────────────────────────

def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _employee_session(client, employee):
    with client.session_transaction() as sess:
        sess["employee_id"] = employee["employee_id"]
        sess["employee_name"] = employee["name"]


def _admin_token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    }).get_json()["token"]


def _employee_token(client, employee):
    return client.post("/api/employee/login", json={
        "employee_id": employee["employee_id"], "password": employee["password"],
    }).get_json()["token"]


def _pdf(name="doc.pdf"):
    return (io.BytesIO(b"%PDF-1.4 test document"), name)


@pytest.fixture
def stored(monkeypatch):
    """Record save_private calls instead of writing files."""
    calls = []

    def fake_save(root, f, rel_path):
        calls.append(rel_path)
        return f"local:{rel_path}", None
    monkeypatch.setattr(documents, "save_private", fake_save)
    return calls


@pytest.fixture
def clean_docs(db_engine):
    """Remove every document row the test created for the test employees."""
    yield
    cur = db_engine.cursor()
    cur.execute("DELETE FROM employee_documents WHERE employee_id IN ('TST001', 'TST002')")
    cur.close()


def _expiry_of(db_engine, employee_id, original_name):
    cur = db_engine.cursor()
    cur.execute("SELECT expiry_date FROM employee_documents WHERE employee_id=%s AND original_name=%s",
                (employee_id, original_name))
    rows = cur.fetchall()
    cur.close()
    return [r[0] for r in rows]


def _seed_doc(db_engine, employee_id, doc_type, expiry, name=None):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by, expiry_date) "
        "VALUES (%s,%s,%s,'local:x','admin',%s) RETURNING id",
        (employee_id, doc_type, name or f"{doc_type.lower()}.pdf", expiry))
    did = cur.fetchone()[0]
    cur.close()
    return did


# ── the four upload routes ──────────────────────────────────────────────────

class TestUploadsCarryTheExpiryDate:
    def test_admin_web_upload_stores_the_date(self, client, seed_admin, seed_employee, db_engine, stored, clean_docs):
        _admin_session(client, seed_admin["username"])
        client.post("/upload_document", data={
            "employee_id": "TST001", "doc_type": "Passport", "expiry_date": "2030-06-15",
            "document": _pdf("passport.pdf"),
        }, content_type="multipart/form-data")
        assert _expiry_of(db_engine, "TST001", "passport.pdf") == [datetime.date(2030, 6, 15)]

    def test_admin_web_upload_refuses_a_malformed_date_before_storing(
            self, client, seed_admin, seed_employee, db_engine, stored, clean_docs):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/upload_document", data={
            "employee_id": "TST001", "doc_type": "Passport", "expiry_date": "15/06/2030",
            "document": _pdf("passport.pdf"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        assert FORMAT_ERROR.encode() in resp.data
        assert stored == [], "the file must not be stored when the form is refused"
        assert _expiry_of(db_engine, "TST001", "passport.pdf") == []

    def test_employee_web_upload_stores_the_date(self, client, seed_employee, db_engine, stored, clean_docs):
        _employee_session(client, seed_employee)
        client.post("/upload_my_document", data={
            "doc_type": "ID Proof", "expiry_date": "2031-01-31", "document": _pdf("visa.pdf"),
        }, content_type="multipart/form-data")
        assert _expiry_of(db_engine, "TST001", "visa.pdf") == [datetime.date(2031, 1, 31)]

    def test_employee_web_upload_refuses_a_malformed_date(self, client, seed_employee, db_engine, stored, clean_docs):
        _employee_session(client, seed_employee)
        client.post("/upload_my_document", data={
            "doc_type": "ID Proof", "expiry_date": "next year", "document": _pdf("visa.pdf"),
        }, content_type="multipart/form-data")
        assert stored == []
        assert _expiry_of(db_engine, "TST001", "visa.pdf") == []

    def test_admin_api_upload(self, client, seed_admin, seed_employee, db_engine, stored, clean_docs):
        headers = {"Authorization": f"Bearer {_admin_token(client, seed_admin)}"}
        bad = client.post("/api/documents/upload", headers=headers, data={
            "employee_id": "TST001", "doc_type": "Passport", "expiry_date": "2030-13-01",
            "document": _pdf("p1.pdf"),
        }, content_type="multipart/form-data")
        assert bad.status_code == 400
        assert bad.get_json() == {"ok": False, "msg": FORMAT_ERROR}
        assert stored == []

        good = client.post("/api/documents/upload", headers=headers, data={
            "employee_id": "TST001", "doc_type": "Passport", "expiry_date": "2030-12-01",
            "document": _pdf("p2.pdf"),
        }, content_type="multipart/form-data")
        assert good.status_code == 200, good.get_json()
        assert _expiry_of(db_engine, "TST001", "p2.pdf") == [datetime.date(2030, 12, 1)]

    def test_employee_api_upload(self, client, seed_employee, db_engine, stored, clean_docs):
        headers = {"Authorization": f"Bearer {_employee_token(client, seed_employee)}"}
        bad = client.post("/api/employee/documents/upload", headers=headers, data={
            "doc_type": "Passport", "expiry_date": "2030-02-29", "document": _pdf("e1.pdf"),
        }, content_type="multipart/form-data")
        assert bad.status_code == 400
        assert stored == []

        good = client.post("/api/employee/documents/upload", headers=headers, data={
            "doc_type": "Passport", "expiry_date": "2032-02-29", "document": _pdf("e2.pdf"),
        }, content_type="multipart/form-data")
        assert good.status_code == 200, good.get_json()
        assert _expiry_of(db_engine, "TST001", "e2.pdf") == [datetime.date(2032, 2, 29)]

    def test_no_date_still_uploads(self, client, seed_employee, db_engine, stored, clean_docs):
        headers = {"Authorization": f"Bearer {_employee_token(client, seed_employee)}"}
        resp = client.post("/api/employee/documents/upload", headers=headers, data={
            "doc_type": "Other", "document": _pdf("plain.pdf"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert _expiry_of(db_engine, "TST001", "plain.pdf") == [None]


# ── where the dates are shown ───────────────────────────────────────────────

@pytest.fixture
def dated_docs(db_engine, seed_employee, clean_docs):
    return {
        "expired": _seed_doc(db_engine, "TST001", "Visa", _days(-3)),
        "soon": _seed_doc(db_engine, "TST001", "Passport", _days(10)),
        "later": _seed_doc(db_engine, "TST001", "Licence", _days(90)),
    }


class TestExpiringDocumentsPage:
    def test_requires_an_admin(self, client):
        assert client.get("/documents/expiring", follow_redirects=False).status_code in (302, 401)

    def test_lists_expired_and_soon_but_not_later(self, client, seed_admin, dated_docs):
        _admin_session(client, seed_admin["username"])
        page = client.get("/documents/expiring").get_data(as_text=True)
        assert "Visa" in page and "Passport" in page
        assert "Licence" not in page
        assert "3 days ago" in page
        assert "in 10 days" in page

    def test_days_widens_the_window(self, client, seed_admin, dated_docs):
        _admin_session(client, seed_admin["username"])
        assert "Licence" in client.get("/documents/expiring?days=90").get_data(as_text=True)

    def test_malformed_days_is_a_400(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        assert client.get("/documents/expiring?days=abc").status_code == 400


class TestExpiringDocumentsApi:
    def test_splits_expired_from_expiring(self, client, seed_admin, dated_docs):
        _admin_session(client, seed_admin["username"])
        data = client.get("/api/admin/expiring_documents?days=30").get_json()
        assert [d["id"] for d in data["documents"]] == [dated_docs["soon"]]
        assert [d["id"] for d in data["expired"]] == [dated_docs["expired"]]
        assert data["documents"][0]["expiry_date"] == _days(10).isoformat()
        assert data["documents"][0]["days_left"] == 10

    def test_team_scoped_role_sees_only_its_own_people(
            self, client, seed_hr_admin, seed_employee, seed_assigned_employee, db_engine, clean_docs):
        mine = _seed_doc(db_engine, "TST002", "Passport", _days(5))
        _seed_doc(db_engine, "TST001", "Passport", _days(5))          # not assigned to this HR
        _admin_session(client, seed_hr_admin["username"], role="hr")
        data = client.get("/api/admin/expiring_documents?days=30").get_json()
        assert [d["id"] for d in data["documents"]] == [mine]


class TestDocumentListsShowTheDate:
    def test_employee_detail_badges(self, client, seed_admin, dated_docs):
        _admin_session(client, seed_admin["username"])
        page = client.get("/employee_detail/TST001").get_data(as_text=True)
        assert f"Expired {_days(-3).strftime('%d %b %Y')}" in page
        assert "(in 10 days)" in page
        assert 'name="expiry_date"' in page

    def test_employee_profile_expires_column(self, client, seed_employee, dated_docs):
        _employee_session(client, seed_employee)
        page = client.get("/employee_portal").get_data(as_text=True)
        assert f"Expired {_days(-3).strftime('%d %b %Y')}" in page
        assert 'name="expiry_date"' in page


class TestAnalyticsAlerts:
    def test_alerts_count_and_link_to_the_page(self, client, seed_admin, dated_docs):
        _admin_session(client, seed_admin["username"])
        page = client.get("/analytics").get_data(as_text=True)
        assert "1 employee document has expired" in page
        assert "1 employee document expiring within 30 days" in page
        assert 'href="/documents/expiring"' in page
