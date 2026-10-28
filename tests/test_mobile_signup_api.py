"""The mobile app's company signup (/api/create_org*) is the website's one
step: the company name and the admin's account (email, mobile number,
password), then the emailed code, which creates the company and returns a
Bearer token for it plus its URL slug. The app sends every later request
under /<company_slug>/ and finishes the registration checklist -- company
details, documents -- through /api/admin/registration*. An older app build
that still sends everything in one request keeps working the old way (its
company is provisioned at the code step)."""
import io

from tests.signup_flow import FAKE_PDF, FAKE_PNG, MOBILE, capture_codes, random_gst
from tests.test_org import _drop_schema

_SIGNUP = {"company_name": "Mobile Signup Co", "admin_email": "Owner@MobileSignup.test", "admin_mobile": MOBILE,
           "admin_password": "password123"}
_SLUG = "mobile-signup-co"
_SCHEMA = "att_mobile_signup_co"


def _start(client, monkeypatch, **overrides):
    codes = capture_codes(monkeypatch)
    resp = client.post("/api/create_org", json={**_SIGNUP, **overrides})
    return resp, codes


def _verify(client, body, code):
    return client.post("/api/create_org/verify_otp", json={
        "application_id": body["application_id"], "access_token": body["access_token"], "otp_code": code})


def _profile(client, body, **fields):
    return client.post("/api/create_org/profile", json={
        "application_id": body["application_id"], "access_token": body["access_token"], **fields})


def _row(db_engine, application_id):
    cur = db_engine.cursor()
    cur.execute("SELECT admin_username, admin_mobile, company_name, status, tenant_id, subdomain "
                "FROM att_master.tenant_applications WHERE id=%s", (application_id,))
    row = cur.fetchone()
    cur.close()
    return row


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _signed_in(client, monkeypatch):
    """Signup and code; returns the code step's JSON (token, company_slug ...)."""
    resp, codes = _start(client, monkeypatch)
    verified = _verify(client, resp.get_json(), codes[-1])
    assert verified.status_code == 200, verified.get_json()
    return verified.get_json()


class TestSignupStep:
    def test_signup_starts_an_application_and_emails_a_code(self, client, db_engine, monkeypatch):
        resp, codes = _start(client, monkeypatch)
        body = resp.get_json()
        assert resp.status_code == 200 and body["ok"] and body["step"] == "otp"
        assert body["access_token"] and codes
        assert _row(db_engine, body["application_id"]) == (
            "owner@mobilesignup.test", "+919876543210", "Mobile Signup Co", "started", None, "")

    def test_bad_signup_is_refused(self, client, monkeypatch):
        for overrides in ({"company_name": ""}, {"admin_mobile": "123"}, {"admin_password": "short"},
                          {"admin_email": "nope"}):
            resp, codes = _start(client, monkeypatch, **overrides)
            assert resp.status_code == 400 and resp.get_json()["ok"] is False, overrides
            assert codes == []


class TestCodeCreatesTheCompanyAndSignsIn:
    def test_code_returns_a_token_for_the_new_company(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        _drop_schema(db_engine, _SCHEMA)
        try:
            resp, codes = _start(client, monkeypatch)
            body = resp.get_json()
            verified = _verify(client, body, codes[-1]).get_json()
            assert verified["ok"] and verified["step"] == "done"
            assert verified["token"] and verified["role"] == "admin"
            assert verified["username"] == "owner@mobilesignup.test"
            assert verified["company_slug"] == _SLUG and verified["company_name"] == "Mobile Signup Co"
            assert verified["portal_url"].endswith(f"/{_SLUG}/login")

            row = _row(db_engine, body["application_id"])
            assert row[3] == "provisioned" and row[4] is not None and row[5] == _SLUG
            cur = db_engine.cursor()
            cur.execute(f'SELECT username FROM "{_SCHEMA}".admin_users')
            assert cur.fetchone()[0] == "owner@mobilesignup.test"
            cur.execute(f'SELECT contact_phone FROM "{_SCHEMA}".company_settings WHERE id=1')
            assert cur.fetchone()[0] == "+919876543210"
            cur.close()

            # The app's own login, under the company's prefix, takes the
            # email as the username, capitals and all.
            login = client.post(f"/{_SLUG}/api/login", json={
                "username": "Owner@MobileSignup.test", "password": "password123"}).get_json()
            assert login["ok"] and login["username"] == "owner@mobilesignup.test"
        finally:
            _drop_schema(db_engine, _SCHEMA)

    def test_the_token_only_works_under_its_own_company(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        _drop_schema(db_engine, _SCHEMA)
        try:
            token = _signed_in(client, monkeypatch)["token"]
            assert client.get(f"/{_SLUG}/api/admin/registration", headers=_bearer(token)).status_code == 200
            # No prefix (and no session cookie remembering the company) =
            # the default company, where this token doesn't exist.
            with client.session_transaction() as sess:
                sess.clear()
            assert client.get("/api/admin/registration", headers=_bearer(token)).status_code == 401
            assert client.get(f"/{_SLUG}/api/admin/registration").status_code == 401
        finally:
            _drop_schema(db_engine, _SCHEMA)

    def test_registration_checklist_from_the_app(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        _drop_schema(db_engine, _SCHEMA)
        try:
            token = _signed_in(client, monkeypatch)["token"]
            url = f"/{_SLUG}/api/admin/registration"
            checklist = client.get(url, headers=_bearer(token)).get_json()
            assert checklist["company_name"] == "Mobile Signup Co" and checklist["company_slug"] == _SLUG
            assert [(s["key"], s["status"]) for s in checklist["steps"]] == [
                ("company", "todo"), ("documents", "todo"), ("payment", "todo")]

            bad = client.post(f"{url}/company", json={"gst_number": "nope"}, headers=_bearer(token))
            assert bad.status_code == 400 and "GSTIN" in bad.get_json()["msg"]
            gst = random_gst()
            saved = client.post(f"{url}/company", data={
                "gst_number": gst, "logo": (io.BytesIO(FAKE_PNG), "logo.png"),
            }, content_type="multipart/form-data", headers=_bearer(token))
            assert saved.status_code == 200, saved.get_json()

            docs = client.post(f"{url}/documents", data={
                "registration_cert": (io.BytesIO(FAKE_PDF), "cert.pdf"),
                "address_proof": (io.BytesIO(FAKE_PDF), "address.pdf"),
                "visiting_card": (io.BytesIO(FAKE_PNG), "card.png"),
                "name_board_photo": (io.BytesIO(FAKE_PNG), "board.png"),
            }, content_type="multipart/form-data", headers=_bearer(token))
            assert docs.status_code == 200, docs.get_json()

            checklist = client.get(url, headers=_bearer(token)).get_json()
            assert checklist["gst_number"] == gst
            assert [(s["key"], s["status"]) for s in checklist["steps"]] == [
                ("company", "done"), ("documents", "done"), ("payment", "todo")]
        finally:
            _drop_schema(db_engine, _SCHEMA)


class TestLegacyProfileStep:
    """An application verified before step 1 asked for the company name
    still finishes at the old profile step."""

    def test_code_leads_to_the_profile_for_an_application_without_a_company(
            self, client, db_engine, monkeypatch):
        resp, codes = _start(client, monkeypatch)
        body = resp.get_json()
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.tenant_applications SET company_name='' WHERE id=%s",
                    (body["application_id"],))
        cur.close()
        db_engine.commit()
        verified = _verify(client, body, codes[-1]).get_json()
        assert verified == {"ok": True, "step": "profile", "msg": "Email verified -- now set up your company."}
        assert _row(db_engine, body["application_id"])[3:5] == ("otp_verified", None)
        # Sent again (say, after the app was reopened), it still points at the profile.
        assert _verify(client, body, "000000").get_json()["step"] == "profile"

    def test_profile_needs_the_verified_code(self, client, monkeypatch):
        resp, _codes = _start(client, monkeypatch)
        resp = _profile(client, resp.get_json(), company_name="Too Early Co", subdomain="too-early-co",
                        gst_number=random_gst())
        assert resp.status_code == 400 and resp.get_json()["ok"] is False


class TestOlderAppBuilds:
    def test_everything_in_one_request_still_provisions_at_the_code(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        schema_name = "att_old_app_co"
        _drop_schema(db_engine, schema_name)
        try:
            codes = capture_codes(monkeypatch)
            resp = client.post("/api/create_org", json={
                "company_name": "Old App Co", "subdomain": "old-app-co", "admin_username": "oldapp",
                "admin_email": "owner@oldapp.test", "admin_password": "password123",
                "email_domain": "oldapp.test", "gst_number": random_gst(),
            })
            body = resp.get_json()
            assert body["ok"] and body["step"] == "otp"
            verified = _verify(client, body, codes[-1]).get_json()
            assert verified["ok"] and verified["step"] == "documents"
            assert _row(db_engine, body["application_id"])[3] == "provisioned"
        finally:
            _drop_schema(db_engine, schema_name)
