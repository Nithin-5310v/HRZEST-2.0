"""The Organization page's editable details (web /profile and the app's
/api/admin/organization) and the app's policy APIs (/api/admin/policies,
/api/employee/company_policies)."""
import datetime
import hashlib
import io
import os
import secrets

import pytest
from PIL import Image

from blueprints.company_profile import clean_org_fields

TENANT_SCHEMA = os.environ["DB_NAME"]
_ORG_COLUMNS = ("company_name", "company_tagline", "contact_phone", "contact_email", "website", "address", "logo_url")


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _token(db_engine, identity, token_type):
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) VALUES (%s,%s,%s,%s)",
        (identity, hashlib.sha256(raw.encode()).hexdigest(), token_type,
         datetime.datetime.now() + datetime.timedelta(hours=1)),
    )
    db_engine.commit()
    cur.close()
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture
def org_settings(db_engine):
    """Restores the company's details after a test changes them."""
    from utils.helpers import invalidate_settings_cache
    cur = db_engine.cursor()
    cur.execute(f"SELECT {', '.join(_ORG_COLUMNS)} FROM company_settings LIMIT 1")
    saved = cur.fetchone()
    yield
    cur.execute("UPDATE company_settings SET " + ", ".join(f"{c}=%s" for c in _ORG_COLUMNS), saved)
    db_engine.commit()
    cur.close()
    invalidate_settings_cache()


def _org_row(db_engine):
    cur = db_engine.cursor()
    cur.execute(f"SELECT {', '.join(_ORG_COLUMNS)} FROM company_settings LIMIT 1")
    row = dict(zip(_ORG_COLUMNS, cur.fetchone()))
    cur.close()
    return row


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "#0B5CAD").save(buf, "PNG")
    buf.seek(0)
    return buf


@pytest.fixture
def admin_headers(db_engine, seed_admin):
    return _token(db_engine, seed_admin["username"], "admin")


@pytest.fixture
def cleanup_policies(db_engine):
    yield
    cur = db_engine.cursor()
    cur.execute("DELETE FROM company_policies WHERE title LIKE 'OrgTest %'")
    db_engine.commit()
    cur.close()


class TestCleanOrgFields:
    def test_only_present_fields_are_returned(self):
        assert clean_org_fields({"contact_phone": " +91 98765 43210 "}) == ({"contact_phone": "+91 98765 43210"}, None)

    def test_blank_clears_but_company_name_is_required(self):
        assert clean_org_fields({"address": "  "}) == ({"address": None}, None)
        assert clean_org_fields({"company_name": " "})[1] == "Company name is required."

    @pytest.mark.parametrize("raw,expected", [
        ("acme.com", "https://acme.com"),
        ("www.acme.co.in/careers", "https://www.acme.co.in/careers"),
        ("http://acme.com", "http://acme.com"),
    ])
    def test_website_gets_a_scheme(self, raw, expected):
        assert clean_org_fields({"website": raw}) == ({"website": expected}, None)

    @pytest.mark.parametrize("raw", ["javascript:alert(1)", "javascript://acme.com/%0aalert(1)",
                                     "ftp://acme.com", "not a site", "https://localhost"])
    def test_website_rejects_non_http_links(self, raw):
        updates, error = clean_org_fields({"website": raw})
        assert updates is None and "valid website" in error

    @pytest.mark.parametrize("field,value", [
        ("contact_email", "not-an-email"), ("contact_phone", "call me"), ("company_name", "x" * 201),
    ])
    def test_rejects_bad_values(self, field, value):
        updates, error = clean_org_fields({field: value})
        assert updates is None and error


class TestWebOrganization:
    def test_admin_edits_name_tagline_and_logo(self, client, db_engine, seed_admin, org_settings):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/profile/organization", data={
            "company_name": "Org Test Pvt Ltd", "company_tagline": "We test things",
            "logo": (_png(), "logo.png", "image/png"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 302
        row = _org_row(db_engine)
        assert row["company_name"] == "Org Test Pvt Ltd"
        assert row["company_tagline"] == "We test things"
        assert row["logo_url"].startswith("/static/company_logos/") and row["logo_url"].endswith(".png")
        logo_file = os.path.join(client.application.root_path, row["logo_url"].lstrip("/"))
        assert os.path.exists(logo_file)
        os.remove(logo_file)

    def test_invalid_input_changes_nothing(self, client, db_engine, seed_admin, org_settings):
        before = _org_row(db_engine)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/profile/organization", data={"company_name": ""}, follow_redirects=True)
        assert b"Company name is required." in resp.data
        assert _org_row(db_engine) == before

    def test_contact_panel_saves_email_and_website(self, client, db_engine, seed_admin, org_settings):
        _admin_session(client, seed_admin["username"])
        client.post("/profile/company_info", data={
            "contact_phone": "+91 98765 43210", "contact_email": "hr@orgtest.in",
            "website": "orgtest.in", "address": "1 Test Road",
        })
        row = _org_row(db_engine)
        assert (row["contact_phone"], row["contact_email"], row["website"], row["address"]) == (
            "+91 98765 43210", "hr@orgtest.in", "https://orgtest.in", "1 Test Road")
        page = client.get("/profile?section=contact").data
        assert b'href="https://orgtest.in"' in page and b"mailto:hr@orgtest.in" in page

    def test_overview_has_the_edit_form(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        page = client.get("/profile").data
        assert b'action="/profile/organization"' in page or b"/profile/organization" in page
        assert b'name="company_tagline"' in page

    def test_hr_cannot_edit(self, client, db_engine, seed_hr_admin, org_settings):
        from utils.auth import HR_ROLE
        before = _org_row(db_engine)
        _admin_session(client, seed_hr_admin["username"], HR_ROLE)
        resp = client.post("/profile/organization", data={"company_name": "HR Was Here"})
        assert resp.status_code in (302, 403)
        assert _org_row(db_engine) == before


class TestOrganizationApi:
    def test_requires_an_admin_token(self, client, db_engine, seed_hr_admin):
        assert client.get("/api/admin/organization").status_code == 401
        hr = _token(db_engine, seed_hr_admin["username"], "admin")
        assert client.get("/api/admin/organization", headers=hr).status_code == 403
        assert client.post("/api/admin/organization", json={"company_name": "x"}, headers=hr).status_code == 403

    def test_get_and_save(self, client, db_engine, admin_headers, org_settings):
        resp = client.post("/api/admin/organization", headers=admin_headers, json={
            "company_name": "Org Api Co", "contact_email": "team@orgapi.in", "website": "orgapi.in",
        })
        assert resp.status_code == 200
        org = resp.get_json()["organization"]
        assert (org["company_name"], org["contact_email"], org["website"]) == (
            "Org Api Co", "team@orgapi.in", "https://orgapi.in")

        data = client.get("/api/admin/organization", headers=admin_headers).get_json()
        assert data["ok"] and data["organization"]["company_name"] == "Org Api Co"
        assert set(data["organization"]) >= {"company_tagline", "contact_phone", "address", "logo_url"}

    def test_validation_error_is_400(self, client, db_engine, admin_headers, org_settings):
        before = _org_row(db_engine)
        resp = client.post("/api/admin/organization", headers=admin_headers,
                           json={"company_tagline": "ok", "website": "ftp://acme.com"})
        assert resp.status_code == 400 and "valid website" in resp.get_json()["msg"]
        assert _org_row(db_engine) == before

    def test_multipart_logo(self, client, db_engine, admin_headers, org_settings):
        resp = client.post("/api/admin/organization", headers=admin_headers, data={
            "company_tagline": "With a logo", "logo": (_png(), "logo.png", "image/png"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 200
        logo_url = resp.get_json()["organization"]["logo_url"]
        assert logo_url.startswith("/static/company_logos/")
        os.remove(os.path.join(client.application.root_path, logo_url.lstrip("/")))


class TestPolicyApi:
    def test_admin_manages_policies(self, client, db_engine, admin_headers, cleanup_policies):
        resp = client.post("/api/admin/policies", headers=admin_headers, json={
            "category": "terms", "title": "OrgTest Terms", "body": "Be kind.",
        })
        assert resp.status_code == 200
        pid = resp.get_json()["id"]

        listing = client.get("/api/admin/policies", headers=admin_headers).get_json()
        mine = next(p for p in listing["policies"] if p["id"] == pid)
        assert mine["is_published"] is True and mine["body"] == "Be kind."
        assert {"key": "terms", "label": "Terms & Conditions"} in listing["categories"]
        assert listing["templates"]

        assert client.post(f"/api/admin/policies/{pid}", headers=admin_headers, json={
            "category": "Travel Policy", "title": "OrgTest Travel", "body": "Book early.",
        }).status_code == 200
        assert client.post(f"/api/admin/policies/{pid}/unpublish", headers=admin_headers).status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT category, title, body, is_published FROM company_policies WHERE id=%s", (pid,))
        assert cur.fetchone() == ("Travel Policy", "OrgTest Travel", "Book early.", 0)
        assert client.post(f"/api/admin/policies/{pid}/publish", headers=admin_headers).status_code == 200
        assert client.delete(f"/api/admin/policies/{pid}", headers=admin_headers).status_code == 200
        cur.execute("SELECT 1 FROM company_policies WHERE id=%s", (pid,))
        assert cur.fetchone() is None
        cur.close()

    def test_draft_and_missing_and_invalid(self, client, db_engine, admin_headers, cleanup_policies):
        resp = client.post("/api/admin/policies", headers=admin_headers, json={
            "category": "rules", "title": "OrgTest Draft", "body": "Not yet.", "is_published": False,
        })
        pid = resp.get_json()["id"]
        cur = db_engine.cursor()
        cur.execute("SELECT is_published FROM company_policies WHERE id=%s", (pid,))
        assert cur.fetchone()[0] == 0
        cur.close()

        assert client.post("/api/admin/policies/999999/publish", headers=admin_headers).status_code == 404
        assert client.delete("/api/admin/policies/999999", headers=admin_headers).status_code == 404
        resp = client.post("/api/admin/policies", headers=admin_headers, json={"category": "terms", "title": "x"})
        assert resp.status_code == 400
        resp = client.post("/api/admin/policies", headers=admin_headers,
                           json={"category": "terms", "title": "t" * 201, "body": "b"})
        assert resp.status_code == 400 and "200" in resp.get_json()["msg"]

    def test_hr_token_may_manage_but_manager_may_not(self, client, db_engine, seed_hr_admin, cleanup_policies):
        hr = _token(db_engine, seed_hr_admin["username"], "admin")
        assert client.post("/api/admin/policies", headers=hr, json={
            "category": "terms", "title": "OrgTest HR", "body": "From HR.",
        }).status_code == 200

        cur = db_engine.cursor()
        cur.execute("INSERT INTO admin_users (username, password, role, is_active) "
                    "VALUES ('orgtest_manager', 'x', 'manager', 1) ON CONFLICT (username) DO NOTHING")
        db_engine.commit()
        try:
            manager = _token(db_engine, "orgtest_manager", "admin")
            assert client.get("/api/admin/policies", headers=manager).status_code == 403
        finally:
            cur.execute("DELETE FROM api_tokens WHERE identity='orgtest_manager'")
            cur.execute("DELETE FROM admin_users WHERE username='orgtest_manager'")
            db_engine.commit()
            cur.close()

    def test_employee_app_sees_only_published(self, client, db_engine, seed_employee, cleanup_policies):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO company_policies (category, title, body, is_published) VALUES "
                    "('terms','OrgTest Live','live',1), ('terms','OrgTest Hidden','hidden',0)")
        db_engine.commit()
        cur.close()
        assert client.get("/api/employee/company_policies").status_code == 401
        emp = _token(db_engine, seed_employee["employee_id"], "employee")
        data = client.get("/api/employee/company_policies", headers=emp).get_json()
        titles = [p["title"] for p in data["policies"]]
        assert "OrgTest Live" in titles and "OrgTest Hidden" not in titles
        assert set(data["contact"]) == {"company_name", "contact_phone", "contact_email", "website"}
