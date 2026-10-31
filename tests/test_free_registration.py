"""A platform admin's free company registration -- "New Company (Free)" on the
dashboard opens /create_org?free=1: the same one-step signup as a paid one
(company name, admin account, emailed code), and the company is provisioned
as a normal manually-invoiced one, not a capped trial. The code sends the
platform admin back to Platform Admin; the company's own admin logs in and
finishes the checklist, which has no payment step. Only a live
platform-admin session can start one (blueprints/org.py's
_free_registration_allowed)."""
import time

from tests.signup_flow import (
    finish_org_setup,
    PASSWORD, application_id_from, capture_codes, start_signup, verify_email,
)
from tests.test_org import _drop_schema
from tests.test_platform_admin import _login_platform_admin

_FREE_PLAN_INPUT = 'name="signup_plan" value="free"'
_OAUTH_ENV = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
              "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_SECRET")
_EMAIL = "owner@freereg.test"


def _page(client, url="/create_org?free=1"):
    resp = client.get(url)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _application(db_engine, email=_EMAIL):
    cur = db_engine.cursor()
    cur.execute("SELECT status, payment_option, free_registration, tenant_id "
                "FROM att_master.tenant_applications WHERE admin_email=%s", (email,))
    row = cur.fetchone()
    cur.close()
    return row


class TestFreeModePage:
    def test_platform_admin_gets_the_signup_form_marked_free(self, client, monkeypatch):
        for name in _OAUTH_ENV:   # even with keys set, a free registration offers no OAuth shortcut
            monkeypatch.setenv(name, "test-value")
        _login_platform_admin(client)
        page = _page(client)
        assert _FREE_PLAN_INPUT in page
        assert "/create_org/google" not in page and "/create_org/microsoft" not in page
        assert "Free registration" in page
        for field in ("company_name", "admin_email", "admin_mobile", "admin_password"):
            assert f'name="{field}"' in page, field

    def test_without_a_platform_admin_session_it_is_the_normal_page(self, client):
        page = _page(client)
        assert _FREE_PLAN_INPUT not in page and "Free registration" not in page
        assert 'name="admin_email"' in page

    def test_an_idle_platform_admin_session_does_not_unlock_it(self, client):
        _login_platform_admin(client)
        with client.session_transaction() as sess:
            sess["platform_admin_last_activity"] = time.time() - 24 * 3600
        assert _FREE_PLAN_INPUT not in _page(client)


class TestFreeRegistrationFlow:
    def test_only_a_platform_admin_can_start_one(self, client, db_engine, monkeypatch):
        codes = capture_codes(monkeypatch)
        resp = start_signup(client, _EMAIL, signup_plan="free")
        assert resp.headers["Location"] == "/create_org"
        assert codes == [] and _application(db_engine) is None

    def test_starts_with_the_email_code_like_a_paid_signup(self, client, db_engine, monkeypatch):
        _login_platform_admin(client)
        codes = capture_codes(monkeypatch)
        resp = start_signup(client, _EMAIL, signup_plan="free")
        assert resp.headers["Location"].startswith("/create_org/verify_otp?application_id=")
        assert codes, "the verification code must be emailed to the company admin"
        assert _application(db_engine) == ("started", "manual", True, None)
        code_page = client.get(resp.headers["Location"]).get_data(as_text=True)
        assert 'name="otp_code"' in code_page and "Create the Company" in code_page
        assert "Payment" not in code_page

    def test_validation_errors_return_to_the_free_form(self, client, monkeypatch):
        _login_platform_admin(client)
        capture_codes(monkeypatch)
        resp = start_signup(client, _EMAIL, password="short", signup_plan="free")
        assert resp.headers["Location"] == "/create_org?free=1"

    def test_code_creates_an_active_invoiced_company_and_returns_to_platform_admin(
            self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        _login_platform_admin(client)
        schema_name = "att_free_reg_live"
        _drop_schema(db_engine, schema_name)
        try:
            codes = capture_codes(monkeypatch)
            application_id = application_id_from(
                start_signup(client, "owner@freereglive.test", "Free Reg Live", signup_plan="free"))
            verified = verify_email(client, application_id, codes[-1])
            assert verified.headers["Location"] == "/super_admin"
            with client.session_transaction() as sess:
                assert sess.get("platform_admin_logged_in") is True
                assert not sess.get("admin_logged_in"), "the platform admin must not become the company's admin"

            cur = db_engine.cursor()
            cur.execute("SELECT db_name, status, subscription_status, payment_option FROM att_master.tenants "
                        "WHERE subdomain=%s", ("free-reg-live",))
            row = cur.fetchone()
            cur.close()
            assert row == (schema_name, "active", "active", "manual")

            # The company's admin finishes registering from the dashboard:
            # company details and documents, no payment step.
            with client.session_transaction() as sess:
                sess.clear()
            client.post("/free-reg-live/login", data={"identifier": "owner@freereglive.test", "password": PASSWORD})
            finish_org_setup(db_engine, schema_name)
            page = client.get("/free-reg-live/profile").get_data(as_text=True)
            assert "Complete your registration" in page and "0 of 2 done" in page
            assert "/free-reg-live/settings?tab=billing" not in page
        finally:
            _drop_schema(db_engine, schema_name)
