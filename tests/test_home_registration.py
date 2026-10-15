"""The manual company-registration form on the home page ("/").

It posts to the existing POST /create_org (OTP -> KYC documents ->
platform-admin review), so these tests cover only what is new: the form
is on the page with every field create_org() reads, a failed submission
comes back to that form instead of the OAuth-only /create_org page, and
the Turnstile captcha can load there when it is configured.
"""
import re

import blueprints.core as core_module
from tests.test_org import _random_gst

_FORM_RE = re.compile(r'<form[^>]*id="homeRegisterForm".*?</form>', re.S)
_SHORT_PASSWORD = {
    "company_name": "Home Form Co", "subdomain": "home-form-co",
    "admin_username": "home_admin", "admin_password": "short",
    "admin_email": "admin@homeform.test", "email_domain": "homeform.test",
}


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
        assert 'enctype="multipart/form-data"' in form
        for name in ("company_name", "gst_number", "subdomain", "admin_email", "admin_username",
                     "admin_password", "logo", "email_domain", "signup_plan", "return_to", "_csrf_token"):
            assert f'name="{name}"' in form, name
        assert 'name="return_to" value="home"' in form
        with client.session_transaction() as s:
            assert s["_csrf"] in form
        assert 'id="register"' in resp.get_data(as_text=True)

    def test_form_script_is_external_and_served(self, client):
        _form, resp = _form_html(client)
        assert "landing_register.js" in resp.get_data(as_text=True)
        assert client.get("/static/landing_register.js").status_code == 200

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
        resp = client.post("/create_org", data=dict(_SHORT_PASSWORD, gst_number=_random_gst(), return_to="home"))
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/?registration=failed#register"
        page = client.get("/?registration=failed").get_data(as_text=True)
        assert "at least 8 characters" in page

    def test_without_return_to_still_goes_to_create_org(self, client):
        resp = client.post("/create_org", data=dict(_SHORT_PASSWORD, gst_number=_random_gst()))
        assert resp.headers["Location"] == "/create_org"

    def test_return_to_accepts_only_home(self, client):
        for value in ("https://evil.example/", "//evil.example", "/admin", "HOME"):
            resp = client.post("/create_org", data=dict(_SHORT_PASSWORD, gst_number=_random_gst(), return_to=value))
            assert resp.headers["Location"] == "/create_org", value

    def test_home_leaves_unrelated_flashes_alone(self, client):
        with client.session_transaction() as s:
            s["_flashes"] = [("warning", "Your admin session expired.")]
        page = client.get("/").get_data(as_text=True)
        assert "Your admin session expired." not in page
        with client.session_transaction() as s:
            assert s.get("_flashes") == [("warning", "Your admin session expired.")]


class TestHomeRegistrationSuccess:
    def test_valid_submission_starts_an_application(self, client, db_engine):
        gst = _random_gst()
        resp = client.post("/create_org", data={
            "company_name": "Home Form Valid Co", "subdomain": "home-form-valid-co",
            "admin_username": "hf_admin", "admin_password": "password123",
            "admin_email": "hf@homeformvalid.test", "email_domain": "homeformvalid.test",
            "gst_number": gst, "signup_plan": "pay_now", "return_to": "home",
        })
        assert resp.status_code == 302
        # conftest forces APP_ENV=development, where create_org() skips the
        # OTP screen and goes straight to the document upload step.
        assert resp.headers["Location"].startswith("/create_org/upload_documents?application_id=")
        cur = db_engine.cursor()
        cur.execute("SELECT company_name, status FROM att_master.tenant_applications WHERE gst_number=%s", (gst,))
        row = cur.fetchone()
        cur.close()
        assert row is not None and row[0] == "Home Form Valid Co"
