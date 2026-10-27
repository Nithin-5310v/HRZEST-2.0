"""A platform admin's free company registration -- "New Company (Free)" on the
dashboard opens /create_org?free=1: the same wizard as a paid signup (company
details, emailed code, documents) without the Pay Now / Trial block, and the
company is provisioned as a normal manually-invoiced one, not a capped trial.
Only a live platform-admin session can start one (blueprints/org.py's
_free_registration_allowed)."""
import time

from tests.test_org import _random_gst, _drop_schema
from tests.test_platform_admin import _login_platform_admin

_FREE_PLAN_INPUT = 'id="signup_plan_input" value="free"'
_OAUTH_ENV = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
              "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_SECRET")


def _page(client, url="/create_org?free=1"):
    resp = client.get(url)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _start(client, monkeypatch, gst, **overrides):
    """POSTs the free-mode form; returns (response, emailed_code_or_None)."""
    import blueprints.org as org_module
    sent = {}
    monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                        lambda to, company, otp: sent.update(to=to, otp=otp) or True)
    data = {"company_name": "Free Reg Co", "subdomain": "free-reg-co", "admin_username": "fr_admin",
            "admin_password": "FreeReg@123", "admin_email": "owner@freereg.test", "email_domain": "freereg.test",
            "gst_number": gst, "signup_plan": "free"}
    data.update(overrides)
    return client.post("/create_org", data=data, follow_redirects=False), sent.get("otp")


def _application(db_engine, gst):
    cur = db_engine.cursor()
    cur.execute("SELECT status, payment_option, free_registration, tenant_id "
                "FROM att_master.tenant_applications WHERE gst_number=%s", (gst,))
    row = cur.fetchone()
    cur.close()
    return row


class TestFreeModePage:
    def test_platform_admin_gets_the_paid_form_without_the_payment_block(self, client, monkeypatch):
        for name in _OAUTH_ENV:   # even with keys set, a free registration offers no OAuth shortcut
            monkeypatch.setenv(name, "test-value")
        _login_platform_admin(client)
        page = _page(client)
        assert _FREE_PLAN_INPUT in page
        assert 'id="planOptionPayNow"' not in page and "2-Day Free Trial" not in page
        assert "/create_org/google" not in page and "/create_org/microsoft" not in page
        assert "Free registration" in page
        for field in ("company_name", "gst_number", "subdomain", "admin_email", "admin_username",
                      "admin_password", "logo", "email_domain"):
            assert f'name="{field}"' in page, field

    def test_without_a_platform_admin_session_it_is_the_normal_page(self, client):
        page = _page(client)
        assert _FREE_PLAN_INPUT not in page
        assert 'id="signup_plan_input" value="pay_now"' in page and 'id="planOptionPayNow"' in page

    def test_an_idle_platform_admin_session_does_not_unlock_it(self, client):
        _login_platform_admin(client)
        with client.session_transaction() as sess:
            sess["platform_admin_last_activity"] = time.time() - 24 * 3600
        assert _FREE_PLAN_INPUT not in _page(client)


class TestFreeRegistrationFlow:
    def test_only_a_platform_admin_can_start_one(self, client, db_engine, monkeypatch):
        gst = _random_gst()
        resp, otp = _start(client, monkeypatch, gst)
        assert resp.headers["Location"] == "/create_org"
        assert otp is None and _application(db_engine, gst) is None

    def test_starts_with_the_email_code_like_a_paid_signup(self, client, db_engine, monkeypatch):
        _login_platform_admin(client)
        gst = _random_gst()
        resp, otp = _start(client, monkeypatch, gst)
        assert resp.headers["Location"].startswith("/create_org/verify_otp?application_id=")
        assert otp, "the verification code must be emailed to the company admin"
        assert _application(db_engine, gst) == ("started", "manual", True, None)
        verify_page = client.get(resp.headers["Location"]).get_data(as_text=True)
        assert "Verify Email" in verify_page and "Payment" not in verify_page

    def test_validation_errors_return_to_the_free_form(self, client, monkeypatch):
        _login_platform_admin(client)
        resp, _otp = _start(client, monkeypatch, _random_gst(), subdomain="super_admin")   # reserved
        assert resp.headers["Location"] == "/create_org?free=1"

    def test_verified_code_creates_an_active_invoiced_company_not_a_trial(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        _login_platform_admin(client)
        gst = _random_gst()
        schema_name = "att_free_reg_live"
        _drop_schema(db_engine, schema_name)
        try:
            resp, otp = _start(client, monkeypatch, gst, company_name="Free Reg Live", subdomain="free-reg-live",
                               admin_email="owner@freereglive.test", email_domain="freereglive.test")
            application_id = resp.headers["Location"].rsplit("=", 1)[-1]
            verified = client.post("/create_org/verify_otp",
                                   data={"application_id": application_id, "otp_code": otp})
            assert verified.headers["Location"] == f"/create_org/upload_documents?application_id={application_id}"
            cur = db_engine.cursor()
            cur.execute("SELECT db_name, status, subscription_status, payment_option FROM att_master.tenants "
                        "WHERE subdomain=%s", ("free-reg-live",))
            row = cur.fetchone()
            cur.close()
            assert row == (schema_name, "active", "active", "manual")
            docs_page = client.get(verified.headers["Location"]).get_data(as_text=True)
            assert "Documents" in docs_page and "Payment" not in docs_page
        finally:
            _drop_schema(db_engine, schema_name)
