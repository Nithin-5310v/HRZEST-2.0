"""The mobile app's company signup (/api/create_org*) runs the same wizard as
the website: the account (email, mobile number, password), the emailed code,
then the company profile -- which provisions the company -- then documents.
An older app build that still sends everything in one request keeps working
the old way (its company is provisioned at the code step)."""
from tests.signup_flow import FAKE_PDF, FAKE_PNG, MOBILE, capture_codes, random_gst
from tests.test_org import _drop_schema

_ACCOUNT = {"admin_email": "Owner@MobileSignup.test", "admin_mobile": MOBILE, "admin_password": "password123"}


def _start(client, monkeypatch, **overrides):
    codes = capture_codes(monkeypatch)
    resp = client.post("/api/create_org", json={**_ACCOUNT, **overrides})
    return resp, codes


def _verify(client, body, code):
    return client.post("/api/create_org/verify_otp", json={
        "application_id": body["application_id"], "access_token": body["access_token"], "otp_code": code})


def _profile(client, body, **fields):
    return client.post("/api/create_org/profile", json={
        "application_id": body["application_id"], "access_token": body["access_token"], **fields})


def _row(db_engine, application_id):
    cur = db_engine.cursor()
    cur.execute("SELECT admin_username, admin_mobile, company_name, status, tenant_id, payment_option "
                "FROM att_master.tenant_applications WHERE id=%s", (application_id,))
    row = cur.fetchone()
    cur.close()
    return row


class TestAccountStep:
    def test_account_starts_an_application_and_emails_a_code(self, client, db_engine, monkeypatch):
        resp, codes = _start(client, monkeypatch)
        body = resp.get_json()
        assert resp.status_code == 200 and body["ok"] and body["step"] == "otp"
        assert body["access_token"] and codes
        assert _row(db_engine, body["application_id"]) == (
            "owner@mobilesignup.test", "+919876543210", "", "started", None, "manual")

    def test_bad_account_is_refused(self, client, monkeypatch):
        for overrides in ({"admin_mobile": "123"}, {"admin_password": "short"}, {"admin_email": "nope"}):
            resp, codes = _start(client, monkeypatch, **overrides)
            assert resp.status_code == 400 and resp.get_json()["ok"] is False, overrides
            assert codes == []


class TestCodeAndProfileSteps:
    def test_code_leads_to_the_profile_not_a_company(self, client, db_engine, monkeypatch):
        resp, codes = _start(client, monkeypatch)
        body = resp.get_json()
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

    def test_profile_needs_the_access_token(self, client, monkeypatch):
        resp, codes = _start(client, monkeypatch)
        body = resp.get_json()
        _verify(client, body, codes[-1])
        resp = _profile(client, dict(body, access_token="wrong"), company_name="Stolen Co", subdomain="stolen-co",
                        gst_number=random_gst())
        assert resp.status_code == 400

    def test_bad_profile_is_refused_and_can_be_sent_again(self, client, db_engine, monkeypatch):
        resp, codes = _start(client, monkeypatch)
        body = resp.get_json()
        _verify(client, body, codes[-1])
        resp = _profile(client, body, company_name="Reserved Co", subdomain="login", gst_number=random_gst())
        assert resp.status_code == 400 and "reserved" in resp.get_json()["msg"]
        assert _row(db_engine, body["application_id"])[3] == "otp_verified"

    def test_profile_provisions_the_company_then_documents_follow(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        schema_name = "att_mobile_signup_co"
        _drop_schema(db_engine, schema_name)
        try:
            resp, codes = _start(client, monkeypatch)
            body = resp.get_json()
            _verify(client, body, codes[-1])
            resp = _profile(client, body, company_name="Mobile Signup Co", subdomain="mobile-signup-co",
                            gst_number=random_gst(), signup_plan="trial")
            assert resp.status_code == 200, resp.get_json()
            assert resp.get_json()["step"] == "documents"
            assert resp.get_json()["portal_url"].endswith("/mobile-signup-co/login")

            row = _row(db_engine, body["application_id"])
            assert row[2:4] == ("Mobile Signup Co", "provisioned") and row[4] is not None and row[5] == "trial"
            cur = db_engine.cursor()
            cur.execute(f'SELECT username FROM "{schema_name}".admin_users')
            assert cur.fetchone()[0] == "owner@mobilesignup.test"
            cur.execute(f'SELECT contact_phone FROM "{schema_name}".company_settings WHERE id=1')
            assert cur.fetchone()[0] == "+919876543210"
            cur.close()

            import io
            docs = client.post("/api/create_org/upload_documents", data={
                "application_id": str(body["application_id"]), "access_token": body["access_token"],
                "registration_cert": (io.BytesIO(FAKE_PDF), "cert.pdf"),
                "address_proof": (io.BytesIO(FAKE_PDF), "address.pdf"),
                "visiting_card": (io.BytesIO(FAKE_PNG), "card.png"),
                "name_board_photo": (io.BytesIO(FAKE_PNG), "board.png"),
            }, content_type="multipart/form-data")
            assert docs.get_json()["step"] == "provisioned"

            # The app's admin login takes the email as the username, capitals and all.
            login = client.post("/mobile-signup-co/api/login", json={
                "username": "Owner@MobileSignup.test", "password": "password123"}).get_json()
            assert login["ok"] and login["username"] == "owner@mobilesignup.test"
        finally:
            _drop_schema(db_engine, schema_name)


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
