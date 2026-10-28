"""The manual company-registration form on the home page ("/").

It is the signup itself -- the company name and the admin's email, mobile
number and password, posted to POST /create_org (the emailed code follows on
/create_org/verify_otp; company details, documents and payment are finished
from the dashboard). These tests cover what is specific
to the home page: the form is there with every field create_org() reads, a
failed submission comes back to that form, and the Turnstile captcha can
load there when it is configured.
"""
import re

import blueprints.core as core_module

_FORM_RE = re.compile(r'<form[^>]*id="homeRegisterForm".*?</form>', re.S)
_SHORT_PASSWORD = {"company_name": "Home Form Co", "admin_email": "admin@homeform.test",
                   "admin_mobile": "+91 98765 43210", "admin_password": "short"}


def _form_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    match = _FORM_RE.search(resp.get_data(as_text=True))
    assert match, "registration form missing from the home page"
    return match.group(0), resp


class TestHomeRegistrationForm:
    def test_form_posts_every_field_create_org_reads(self, client):
        form, resp = _form_html(client)
        assert 'action="/create_org"' in form
        assert 'method="POST"' in form
        for name in ("company_name", "admin_email", "admin_mobile", "admin_password", "return_to", "_csrf_token"):
            assert f'name="{name}"' in form, name
        for name in ("gst_number", "subdomain", "admin_username", "logo"):
            assert f'name="{name}"' not in form, f"{name} is finished from the dashboard, or made from another field"
        assert 'name="return_to" value="home"' in form
        with client.session_transaction() as s:
            assert s["_csrf"] in form
        assert 'id="register"' in resp.get_data(as_text=True)

    def test_no_captcha_when_turnstile_off(self, client, monkeypatch):
        monkeypatch.setattr(core_module, "turnstile_enabled", lambda: False)
        form, resp = _form_html(client)
        assert "cf-turnstile" not in form
        assert "challenges.cloudflare.com" not in resp.get_data(as_text=True)

    def test_captcha_widget_and_csp_when_turnstile_on(self, client, monkeypatch):
        monkeypatch.setattr(core_module, "turnstile_enabled", lambda: True)
        monkeypatch.setattr(core_module, "_TURNSTILE_SITE_KEY", "test-site-key")
        form, resp = _form_html(client)
        assert 'class="cf-turnstile" data-sitekey="test-site-key"' in form
        csp = resp.headers["Content-Security-Policy"]
        assert "script-src" in csp and "https://challenges.cloudflare.com" in csp.split("script-src", 1)[1].split(";")[0]
        assert "https://challenges.cloudflare.com" in csp.split("frame-src", 1)[1].split(";")[0]


class TestHomeRegistrationErrors:
    def test_failed_submission_returns_to_home_form_with_message(self, client):
        resp = client.post("/create_org", data=dict(_SHORT_PASSWORD, return_to="home"))
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/?registration=failed#register"
        page = client.get("/?registration=failed").get_data(as_text=True)
        assert "at least 8 characters" in page

    def test_without_return_to_still_goes_to_create_org(self, client):
        resp = client.post("/create_org", data=dict(_SHORT_PASSWORD))
        assert resp.headers["Location"] == "/create_org"

    def test_return_to_accepts_only_home(self, client):
        for value in ("https://evil.example/", "//evil.example", "/admin", "HOME"):
            resp = client.post("/create_org", data=dict(_SHORT_PASSWORD, return_to=value))
            assert resp.headers["Location"] == "/create_org", value

    def test_home_leaves_unrelated_flashes_alone(self, client):
        with client.session_transaction() as s:
            s["_flashes"] = [("warning", "Your admin session expired.")]
        page = client.get("/").get_data(as_text=True)
        assert "Your admin session expired." not in page
        with client.session_transaction() as s:
            assert s.get("_flashes") == [("warning", "Your admin session expired.")]


class TestHomeRegistrationSuccess:
    def test_valid_submission_starts_an_application(self, client, db_engine, monkeypatch):
        import blueprints.org as org_module
        monkeypatch.setattr(org_module, "send_org_signup_otp_email", lambda email, company, otp: True)
        resp = client.post("/create_org", data={
            "company_name": "Home Form Valid Co", "admin_email": "hf@homeformvalid.test",
            "admin_mobile": "9876543210", "admin_password": "password123", "return_to": "home",
        })
        assert resp.status_code == 302
        # Next step is always the emailed-code screen (conftest's
        # APP_ENV=development used to skip it).
        assert resp.headers["Location"].startswith("/create_org/verify_otp?application_id=")
        cur = db_engine.cursor()
        cur.execute("SELECT company_name, admin_username, admin_mobile, status FROM att_master.tenant_applications "
                    "WHERE admin_email=%s", ("hf@homeformvalid.test",))
        row = cur.fetchone()
        cur.close()
        assert row == ("Home Form Valid Co", "hf@homeformvalid.test", "9876543210", "started")
