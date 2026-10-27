"""The /create_org page offers Google, Microsoft AND manual entry.

The page had been reduced to the two OAuth buttons while POST /create_org
(email OTP -> documents -> approval) stayed in the backend, which left manual
registration unreachable from the browser. These tests pin that the form is
there -- the wizard's first step, the admin's email, mobile number and
password -- that it posts every field the handler reads, and that it works
under the page's own CSP (the Turnstile widget on it was previously blocked).
"""
import html
import re

import pytest

# Every request.form key create_org() reads in blueprints/org.py (plus
# signup_plan, sent only by a platform admin's free registration).
HANDLER_FIELDS = {"admin_email", "admin_mobile", "admin_password"}


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


_OAUTH_ENV = {
    "google": ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"),
    "microsoft": ("MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_SECRET"),
}


def _set_oauth_keys(monkeypatch, *providers):
    """Configure exactly these providers' keys (none = no Google/Microsoft)."""
    for provider, names in _OAUTH_ENV.items():
        for name in names:
            if provider in providers:
                monkeypatch.setenv(name, "test-value")
            else:
                monkeypatch.delenv(name, raising=False)


@pytest.fixture
def oauth_keys(monkeypatch):
    _set_oauth_keys(monkeypatch, "google", "microsoft")


class TestWithoutOAuthKeys:
    """No Google/Microsoft keys (the current deployment): the page offers
    only the manual form, never buttons that can only fail."""

    def test_only_the_manual_form_is_offered(self, client, monkeypatch):
        _set_oauth_keys(monkeypatch)
        _, page = _page(client)
        assert "/create_org/google" not in page and "/create_org/microsoft" not in page
        assert "Sign up with Google" not in page and "Sign up with Microsoft" not in page
        assert "or register manually" not in page
        assert "No documents or business registration number needed" not in page
        assert "Create your admin account with your email, mobile number and a password" in page
        _manual_form(page)

    def test_one_configured_provider_shows_only_its_button(self, client, monkeypatch):
        _set_oauth_keys(monkeypatch, "google")
        _, page = _page(client)
        assert 'href="/create_org/google"' in page
        assert "/create_org/microsoft" not in page
        assert "Sign up with your Google work account" in page


class TestManualSignupFormIsOffered:
    def test_google_microsoft_and_manual_entry_all_present(self, client, oauth_keys):
        _, page = _page(client)
        assert 'href="/create_org/google"' in page
        assert 'href="/create_org/microsoft"' in page
        form_tag, _ = _manual_form(page)
        assert 'method="POST"' in form_tag

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

    def test_password_is_masked_and_company_details_wait_for_the_profile(self, client):
        _, page = _page(client)
        _, body = _manual_form(page)
        pw = re.search(r'<input[^>]*name="admin_password"[^>]*>', body).group(0)
        assert 'type="password"' in pw and 'autocomplete="new-password"' in pw
        mobile = re.search(r'<input[^>]*name="admin_mobile"[^>]*>', body).group(0)
        assert 'type="tel"' in mobile and "required" in mobile
        for name in ("company_name", "gst_number", "subdomain", "admin_username", "logo"):
            assert f'name="{name}"' not in body, f"{name} belongs to the company profile step"

    def test_copy_does_not_promise_oauth_perks_to_manual_signups(self, client, oauth_keys):
        """The 'no documents needed' line is true for Google/Microsoft only;
        the manual path asks for an email code and four documents."""
        _, page = _page(client)
        form_tag, body = _manual_form(page)
        before_form = page.split(form_tag)[0]
        assert "No documents or business registration number needed" in before_form
        assert "business documents" in before_form.split("or register manually")[1]

    def test_page_script_defines_every_handler_the_page_calls(self, client):
        _, page = _page(client)
        called = set(re.findall(r'on(?:click|input|blur)="\s*([A-Za-z_]\w*)\(', page))
        for fn in called:
            assert re.search(rf"function\s+{fn}\s*\(", page), f"{fn}() is called but not defined"


class TestRenderedFormReachesTheBackend:
    def test_submitting_the_fields_the_page_renders_starts_an_application(self, client, db_engine, monkeypatch):
        import blueprints.org as org_module
        sent = {}
        monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                            lambda email, company, otp: sent.setdefault("otp", otp) or True)

        _, page = _page(client)
        _, body = _manual_form(page)
        values = {
            "admin_email": "mf@manualform.test", "admin_mobile": "+91 98765 43210",
            "admin_password": "password123",
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
                "SELECT admin_username, admin_mobile, email_domain FROM att_master.tenant_applications "
                "WHERE admin_email=%s", ("mf@manualform.test",))
            assert cur.fetchone() == ("mf@manualform.test", "+919876543210", "manualform.test")
        finally:
            cur.execute("DELETE FROM att_master.tenant_applications WHERE admin_email=%s", ("mf@manualform.test",))
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

    def test_turnstile_is_allowed_on_the_home_page(self, client):
        """The home page carries the same registration form and widget
        (templates/landing.html), posting to /create_org."""
        csp = client.get("/").headers["Content-Security-Policy"]
        for name in ("script-src", "frame-src", "connect-src"):
            assert "https://challenges.cloudflare.com" in self._directive(csp, name), name

    @pytest.mark.parametrize("path", ["/get-started", "/checkin"])
    def test_turnstile_is_not_opened_up_elsewhere(self, client, path):
        resp = client.get(path, follow_redirects=False)
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "challenges.cloudflare.com" not in csp
