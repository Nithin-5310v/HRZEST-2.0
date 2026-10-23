"""The /create_org page offers Google, Microsoft AND manual entry.

The page had been reduced to the two OAuth buttons while POST /create_org
(email OTP -> documents -> approval) stayed in the backend, which left manual
registration unreachable from the browser. These tests pin that the form is
there, that it posts every field the handler reads, and that it works under
the page's own CSP (the Turnstile widget on it was previously blocked).
"""
import html
import re

import pytest

# Every request.form key create_org() reads in blueprints/org.py.
HANDLER_FIELDS = {
    "company_name", "subdomain", "admin_username", "admin_password",
    "admin_email", "email_domain", "gst_number", "signup_plan",
}


def _page(client):
    resp = client.get("/create_org")
    assert resp.status_code == 200
    return resp, resp.get_data(as_text=True)


def _manual_form(page):
    """The <form> that posts to /create_org (not the OAuth links)."""
    m = re.search(r'<form[^>]*action="/create_org"[^>]*>(.*?)</form>', page, re.S)
    assert m, "no manual-signup <form> posting to /create_org on the page"
    return m.group(0), m.group(1)


def _field_names(form_body):
    return set(re.findall(r'<input[^>]*\bname="([^"]+)"', form_body))


class TestManualSignupFormIsOffered:
    def test_google_microsoft_and_manual_entry_all_present(self, client):
        _, page = _page(client)
        assert 'href="/create_org/google"' in page
        assert 'href="/create_org/microsoft"' in page
        form_tag, _ = _manual_form(page)
        assert 'method="POST"' in form_tag
        # the logo upload needs multipart
        assert 'enctype="multipart/form-data"' in form_tag

    def test_form_carries_a_csrf_token_that_matches_the_session(self, client):
        _, page = _page(client)
        _, body = _manual_form(page)
        m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', body)
        assert m and m.group(1), "manual form is missing its CSRF token"
        with client.session_transaction() as sess:
            assert sess.get("_csrf") == html.unescape(m.group(1))

    def test_form_has_every_field_the_handler_reads(self, client):
        _, page = _page(client)
        _, body = _manual_form(page)
        missing = HANDLER_FIELDS - _field_names(body)
        assert not missing, f"form no longer sends: {sorted(missing)}"

    def test_logo_upload_is_optional_and_password_is_masked(self, client):
        _, page = _page(client)
        _, body = _manual_form(page)
        logo = re.search(r'<input[^>]*name="logo"[^>]*>', body).group(0)
        assert "required" not in logo
        pw = re.search(r'<input[^>]*name="admin_password"[^>]*>', body).group(0)
        assert 'type="password"' in pw and 'autocomplete="new-password"' in pw

    def test_copy_does_not_promise_oauth_perks_to_manual_signups(self, client):
        """The 'no documents needed' line is true for Google/Microsoft only;
        the manual path asks for an email code and four documents."""
        _, page = _page(client)
        form_tag, body = _manual_form(page)
        before_form = page.split(form_tag)[0]
        assert "No documents or business registration number needed" in before_form
        assert "business documents" in before_form.split("or register manually")[1]

    def test_page_script_defines_every_handler_the_form_calls(self, client):
        _, page = _page(client)
        _, body = _manual_form(page)
        called = set(re.findall(r'on(?:click|input|blur)="\s*([A-Za-z_]\w*)\(', body))
        assert called, "expected the form to use its inline helpers"
        for fn in called:
            assert re.search(rf"function\s+{fn}\s*\(", page), f"{fn}() is called but not defined"


class TestRenderedFormReachesTheBackend:
    def test_submitting_the_fields_the_page_renders_starts_an_application(self, client, db_engine, monkeypatch):
        import blueprints.org as org_module
        # conftest forces APP_ENV=development, which skips the OTP screen;
        # this test is about the real flow the browser form feeds.
        monkeypatch.setenv("APP_ENV", "production")
        sent = {}
        monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                            lambda email, company, otp: sent.setdefault("otp", otp) or True)

        _, page = _page(client)
        _, body = _manual_form(page)
        values = {
            "company_name": "Manual Form Org", "subdomain": "manual-form-org",
            "admin_username": "mf_admin", "admin_password": "password123",
            "admin_email": "mf@manualform.test", "email_domain": "manualform.test",
            "gst_number": "27ABCDE1234F1Z5", "signup_plan": "trial",
            "_csrf_token": html.unescape(re.search(r'name="_csrf_token"\s+value="([^"]+)"', body).group(1)),
        }
        # Only send what the page actually renders -- a field the form dropped
        # must not be papered over by the test.
        data = {k: v for k, v in values.items() if k in _field_names(body)}
        assert set(data) >= HANDLER_FIELDS | {"_csrf_token"}

        cur = db_engine.cursor()
        try:
            resp = client.post("/create_org", data=data, follow_redirects=False)
            assert resp.status_code == 302
            assert resp.headers["Location"].startswith("/create_org/verify_otp?application_id=")
            assert sent.get("otp"), "an OTP email should have been issued"
            cur.execute(
                "SELECT payment_option, admin_email, email_domain FROM att_master.tenant_applications "
                "WHERE subdomain=%s", ("manual-form-org",))
            assert cur.fetchone() == ("trial", "mf@manualform.test", "manualform.test")
        finally:
            cur.execute("DELETE FROM att_master.tenant_applications WHERE subdomain=%s", ("manual-form-org",))
            cur.close()


class TestSignupPageCsp:
    @staticmethod
    def _directive(csp, name):
        m = re.search(rf"(?:^|;\s*){name} ([^;]+)", csp)
        return m.group(1) if m else ""

    def test_turnstile_is_allowed_on_the_signup_page(self, client):
        """The manual form renders the Turnstile widget when it is configured.
        Without these sources the script, frame and verification request are
        blocked, the token is empty, and every signup fails the captcha."""
        resp, _ = _page(client)
        csp = resp.headers["Content-Security-Policy"]
        for name in ("script-src", "frame-src", "connect-src"):
            assert "https://challenges.cloudflare.com" in self._directive(csp, name), name

    @pytest.mark.parametrize("path", ["/", "/get-started", "/checkin"])
    def test_turnstile_is_not_opened_up_elsewhere(self, client, path):
        resp = client.get(path, follow_redirects=False)
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "challenges.cloudflare.com" not in csp
