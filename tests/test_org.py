"""
Org blueprint tests — multi-tenant self-registration (/create_org).

Signup is open by default now (no shared-secret gate) -- Turnstile
protects it instead, and turnstile_enabled() is False in tests (no
TURNSTILE_SITE_KEY/SECRET_KEY configured), so the captcha check no-ops
here exactly like the rest of the suite's login-flow tests already rely
on. Billing is flat per-employee (utils.plan_limits.PER_EMPLOYEE_PAISE)
-- there's no plan tier to select or validate anymore.

The full provisioning path creates a real Postgres schema via
create_tenant_schema() + init_tenant_db() (which runs the entire init_db()
schema bootstrap against it) — genuinely heavy, but this is exactly the
kind of "quiet until it silently breaks" flow worth one real end-to-end
test for, with explicit cleanup (DROP SCHEMA) after.

Run with:
    python -m pytest tests/test_org.py -v
"""
import pytest

from tests.signup_flow import (
    MOBILE, application_id_from, capture_codes, company_slug, random_gst, save_company_details, signup_company,
    signup_verified, start_signup, submit_profile, upload_documents, verify_email,
)

_random_gst = random_gst   # other test modules import it from here


def _drop_schema(db_engine, schema_name):
    cur = db_engine.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (schema_name,))
    cur.close()


# ===========================================================================
# Signup page / validation paths (no schema creation, fast)
# ===========================================================================

class TestSignupPage:
    def test_get_page_renders(self, client, monkeypatch):
        # The Google/Microsoft buttons only show for providers with keys set.
        for name in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
                     "MICROSOFT_OAUTH_CLIENT_ID", "MICROSOFT_OAUTH_CLIENT_SECRET"):
            monkeypatch.setenv(name, "test-value")
        resp = client.get("/create_org")
        assert resp.status_code == 200
        assert b"Start Your Organisation" in resp.data
        assert b"/create_org/google" in resp.data
        assert b"/create_org/microsoft" in resp.data

    def test_page_shows_flat_per_employee_rate(self, client):
        """The flat per-employee rate used to be shown on /create_org itself
        -- the "Redesign registration page" commit dropped the marketing
        sidebar it lived in (dead pricing-display JS included). Pricing is
        now shown only on the landing page ("/"), which is where this
        checks instead."""
        import utils.plan_limits as plan_limits
        resp = client.get("/")
        assert plan_limits.format_price_inr(plan_limits.PER_EMPLOYEE_PAISE).encode() in resp.data


class TestGetStartedPage:
    """/get-started is retired -- the landing page ("/") now links directly
    to /login and /create_org instead of routing through this extra hop.
    /get-started is kept only as a redirect to "/" for old bookmarks/links.
    See TestLandingPageLinks below for the replacement coverage."""

    def test_get_page_redirects_to_landing(self, client):
        resp = client.get("/get-started", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"].rstrip("/") in ("", "/")


class TestLandingPageLinks:
    """The apex landing page is now the SaaS entry point (login-by-subdomain
    vs register) that /get-started used to be."""

    def test_links_to_create_org_and_login(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"/create_org" in resp.data
        assert b"/login" in resp.data


class TestLeadSubmission:
    """/api/leads backs the landing page's "Request Demo" modal
    (templates/landing.html, static/landing_v2.js) for visitors not ready
    to self-register yet."""

    def test_submit_lead_stores_all_fields(self, client, db_engine):
        resp = client.post("/api/leads", json={
            "name": "Jordan Lead", "email": "jordan.lead@test.local",
            "phone": "+91 98765 43210", "company_name": "Lead Test Co",
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute(
            "SELECT name, email, phone, company_name FROM att_master.leads WHERE email=%s",
            ("jordan.lead@test.local",),
        )
        row = cur.fetchone()
        assert row == ("Jordan Lead", "jordan.lead@test.local", "+91 98765 43210", "Lead Test Co")
        cur.execute("DELETE FROM att_master.leads WHERE email=%s", ("jordan.lead@test.local",))
        db_engine.commit()

    def test_missing_email_rejected(self, client):
        resp = client.post("/api/leads", json={"name": "No Email"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


class TestFeedbackSubmission:
    """/api/feedback backs the landing page's Feedback & Suggestions form
    (templates/landing.html's #feedback section, static/landing_v2.js) --
    was previously a pure client-side fake-success simulation (form hidden,
    "Thanks!" banner shown, no request ever sent)."""

    def test_submit_feedback_stores_all_fields(self, client, db_engine):
        resp = client.post("/api/feedback", json={
            "feedback_type": "Biometrics", "rating": 4,
            "email": "feedback-test@test.local", "message": "Fingerprint sync is great.",
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute(
            "SELECT feedback_type, rating, email, message FROM att_master.feedback WHERE email=%s",
            ("feedback-test@test.local",),
        )
        row = cur.fetchone()
        assert row == ("Biometrics", 4, "feedback-test@test.local", "Fingerprint sync is great.")
        cur.execute("DELETE FROM att_master.feedback WHERE email=%s", ("feedback-test@test.local",))
        db_engine.commit()

    def test_missing_message_rejected(self, client):
        resp = client.post("/api/feedback", json={"feedback_type": "General Praise"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_email_is_optional(self, client, db_engine):
        resp = client.post("/api/feedback", json={"feedback_type": "User Experience", "message": "Nice UI."})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT email FROM att_master.feedback WHERE message='Nice UI.'")
        assert cur.fetchone() == (None,)
        cur.execute("DELETE FROM att_master.feedback WHERE message='Nice UI.'")
        db_engine.commit()

    def test_invalid_email_rejected(self, client):
        resp = client.post("/api/feedback", json={"message": "hi", "email": "not-an-email"})
        assert resp.status_code == 400

    def test_rating_out_of_range_rejected(self, client):
        resp = client.post("/api/feedback", json={"message": "hi", "rating": 7})
        assert resp.status_code == 400

    def test_rating_is_optional(self, client, db_engine):
        resp = client.post("/api/feedback", json={"message": "No rating given."})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT rating FROM att_master.feedback WHERE message='No rating given.'")
        assert cur.fetchone() == (None,)
        cur.execute("DELETE FROM att_master.feedback WHERE message='No rating given.'")
        db_engine.commit()

    def test_unknown_feedback_type_normalized_to_default(self, client, db_engine):
        resp = client.post("/api/feedback", json={"feedback_type": "Nonsense Type", "message": "test normalize"})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT feedback_type FROM att_master.feedback WHERE message='test normalize'")
        assert cur.fetchone() == ("Feature Request",)
        cur.execute("DELETE FROM att_master.feedback WHERE message='test normalize'")
        db_engine.commit()

    def test_message_length_is_capped(self, client, db_engine):
        long_message = "x" * 3000
        resp = client.post("/api/feedback", json={"message": long_message})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT LENGTH(message) FROM att_master.feedback WHERE message LIKE 'xxx%'")
        assert cur.fetchone()[0] == 2000
        cur.execute("DELETE FROM att_master.feedback WHERE message LIKE 'xxx%'")
        db_engine.commit()


class TestSignupValidation:
    """Signup is one step (POST /create_org): the company name and the
    admin's account -- email, mobile number, password. Anything wrong sends
    the visitor back to the form before an application exists."""

    @pytest.mark.parametrize("field, value", [
        ("company_name", ""), ("company_name", "!!! ---"), ("company_name", "x" * 201),
        ("admin_email", ""), ("admin_email", "not-an-email"),
        ("admin_email", "a" * 45 + "@x.test"),   # over 50 characters: it's the login username
        ("admin_mobile", ""), ("admin_mobile", "12345"), ("admin_mobile", "98765abcde"),
        ("admin_password", ""), ("admin_password", "short"),
    ])
    def test_bad_field_rejected(self, client, monkeypatch, field, value):
        codes = capture_codes(monkeypatch)
        data = {"company_name": "Valid Account Co", "admin_email": "valid@account.test", "admin_mobile": MOBILE,
                "admin_password": "password123"}
        data[field] = value
        resp = client.post("/create_org", data=data, follow_redirects=False)
        assert resp.headers["Location"] == "/create_org"
        assert codes == []

    def test_valid_signup_starts_an_application_with_the_email_as_username(self, client, db_engine, monkeypatch):
        codes = capture_codes(monkeypatch)
        resp = start_signup(client, "  Owner@Account-Start.TEST ", "  Account   Start  Co ", mobile="+91 98765-43210")
        assert resp.headers["Location"].startswith("/create_org/verify_otp?application_id=")
        assert codes, "the verification code must be emailed"
        cur = db_engine.cursor()
        cur.execute("SELECT admin_username, admin_email, admin_mobile, email_domain, company_name, subdomain, status "
                    "FROM att_master.tenant_applications WHERE id=%s", (application_id_from(resp),))
        row = cur.fetchone()
        cur.close()
        # The company URL is picked when the code is verified, not before.
        assert row == ("owner@account-start.test", "owner@account-start.test", "+919876543210",
                       "account-start.test", "Account Start Co", "", "started")


class TestOneStepSignupPage:
    """The code is entered on the signup page itself -- there are no
    separate wizard steps any more."""

    def test_signup_form_asks_only_for_the_company_name_and_account(self, client):
        page = client.get("/create_org").get_data(as_text=True)
        for field in ("company_name", "admin_email", "admin_mobile", "admin_password"):
            assert f'name="{field}"' in page, field
        for field in ("gst_number", "subdomain", "logo", "otp_code"):
            assert f'name="{field}"' not in page, field
        assert "wizard-step" not in page

    def test_no_portal_url_hint_on_signup(self, client):
        """The "Your team's portal: .../your-company/login" hint under
        Company Name was removed from the sign-up page on request; it must
        not come back, and no hard-coded www.hrzest.com address either."""
        page = client.get("/create_org").get_data(as_text=True)
        assert "Your team's portal" not in page
        assert "/your-company/login</strong>" not in page
        assert "www.hrzest.com/your-company" not in page

    def test_code_entry_is_the_signup_page_with_the_details_locked_in(self, client, monkeypatch):
        capture_codes(monkeypatch)
        resp = start_signup(client, "lockedin@code.test", "Locked In Co")
        page = client.get(resp.headers["Location"]).get_data(as_text=True)
        assert "Start Your Organisation" in page
        assert "Locked In Co" in page and "lockedin@code.test" in page
        assert 'name="otp_code"' in page and 'action="/create_org/verify_otp"' in page
        assert 'action="/create_org/resend_otp"' in page and "Change details" in page
        assert 'name="admin_password"' not in page and "wizard-step" not in page

    def test_code_entry_without_the_signup_session_starts_over(self, client, monkeypatch):
        capture_codes(monkeypatch)
        application_id = application_id_from(start_signup(client, "nosession@code.test"))
        with client.session_transaction() as sess:
            sess.pop("org_access_token", None)
        resp = client.get(f"/create_org/verify_otp?application_id={application_id}")
        assert resp.headers["Location"] == "/create_org"


class TestLegacyProfileStep:
    """An application whose email was verified before step 1 asked for the
    company name still finishes at the old company-profile form."""

    def _verified_without_company(self, client, db_engine, monkeypatch, email):
        codes = capture_codes(monkeypatch)
        application_id = application_id_from(start_signup(client, email))
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.tenant_applications SET company_name='' WHERE id=%s", (application_id,))
        cur.close()
        db_engine.commit()
        resp = verify_email(client, application_id, codes[-1])
        assert resp.headers["Location"] == f"/create_org/profile?application_id={application_id}"
        return application_id

    def test_profile_needs_a_verified_email(self, client, monkeypatch):
        capture_codes(monkeypatch)
        application_id = application_id_from(start_signup(client, "early@profile.test"))
        page = client.get(f"/create_org/profile?application_id={application_id}")
        assert page.headers["Location"] == "/create_org"
        resp = submit_profile(client, application_id, "Too Early Co", "too-early-co")
        assert resp.headers["Location"] == "/create_org"

    def test_profile_still_creates_an_old_applications_company(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        subdomain = "legacy-profile-co"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = self._verified_without_company(client, db_engine, monkeypatch, "legacy@profile.test")
            assert "Set Up Your Company Profile" in client.get(
                f"/create_org/profile?application_id={application_id}").get_data(as_text=True)
            resp = submit_profile(client, application_id, "Legacy Profile Co", subdomain)
            assert resp.headers["Location"] == f"/create_org/upload_documents?application_id={application_id}"
            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenant_applications WHERE id=%s", (application_id,))
            assert cur.fetchone()[0] == "provisioned"
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)


class TestDevModeSignupStillVerifiesEmail:
    """conftest forces APP_ENV=development -- the mode the EC2 host ran in.
    create_org() used to skip the email-code screen there, which also
    skipped the provisioning that only follows a verified code, so the
    applicant ended on a pending page nothing ever advanced. The code is
    now emailed and checked in every APP_ENV."""

    _EMAIL = "dm@devmodesignup.test"

    def _status(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("SELECT status, email_verified_at FROM att_master.tenant_applications WHERE admin_email=%s",
                    (self._EMAIL,))
        row = cur.fetchone()
        cur.close()
        return row

    def test_signup_emails_a_code_and_shows_the_code_entry(self, client, db_engine, monkeypatch):
        codes = capture_codes(monkeypatch)
        resp = start_signup(client, self._EMAIL)
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/create_org/verify_otp?application_id=")
        assert codes, "the verification code must be emailed"
        assert self._status(db_engine) == ("started", None)

    def test_wrong_or_blank_code_is_refused(self, client, db_engine, monkeypatch):
        capture_codes(monkeypatch)
        application_id = application_id_from(start_signup(client, self._EMAIL))
        for code in ("", "000000"):   # real codes are 100000-999999
            bad = verify_email(client, application_id, code)
            assert bad.headers["Location"] == f"/create_org/verify_otp?application_id={application_id}"
        assert self._status(db_engine) == ("started", None)

    def test_documents_cannot_be_uploaded_before_the_code_is_verified(self, client, db_engine, monkeypatch):
        capture_codes(monkeypatch)
        application_id = application_id_from(start_signup(client, self._EMAIL))
        upload = client.post("/create_org/upload_documents", data={"application_id": application_id},
                             content_type="multipart/form-data")
        assert upload.headers["Location"].startswith("/create_org/upload_documents?application_id=")
        assert self._status(db_engine) == ("started", None)


# ===========================================================================
# Full provisioning -- real schema creation
# ===========================================================================

class TestSignupFlow:
    """Signup end to end: step 1 only starts an application and emails a
    code; the code creates the company (blueprints/org.py's
    _create_signup_company) and logs its admin into the dashboard, whose
    checklist collects the GSTIN, the KYC documents (reviewed after the
    fact) and payment. These tests walk the real pipeline."""

    def _login_platform_admin(self, client):
        import time as _time
        with client.session_transaction() as sess:
            sess["platform_admin_logged_in"] = True
            sess["platform_admin_username"] = "test_platform_admin"
            sess["platform_admin_last_activity"] = _time.time()

    def test_full_flow_provisions_real_tenant_schema(self, client, db_engine, monkeypatch):
        """The company is provisioned the moment the code is verified --
        before its GSTIN or any document. KYC review still happens, but it's
        tracked separately (doc_review_status) and doesn't gate login or
        schema creation."""
        from app import init_master_db
        init_master_db()

        company_name = "E2E Gated Org"
        subdomain = company_slug(company_name)
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        gst = random_gst()
        try:
            application_id = signup_company(client, monkeypatch, "e2e-gated@test.local", company_name, gst_number=gst)

            cur = db_engine.cursor()
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name=%s", (schema_name,)
            )
            assert cur.fetchone() is not None, "tenant schema was not created"

            cur.execute("SELECT db_name, status, plan, subscription_status, admin_email, gst_number "
                        "FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            row = cur.fetchone()
            assert row is not None, "tenant was not registered in att_master.tenants"
            assert row[0] == schema_name
            assert row[1] == "active"
            import utils.plan_limits as plan_limits
            assert row[2] == plan_limits.PLAN_LABEL
            # Every self-serve company starts trialing -- nobody pays
            # upfront; auto-debit is the checklist's payment step.
            assert row[3] == "trialing"
            assert row[4] == "e2e-gated@test.local"
            assert row[5] == gst

            # The admin's login username is their email; the mobile number
            # from step 1 becomes the company's contact phone.
            cur.execute(f'SELECT username FROM "{schema_name}".admin_users WHERE username=%s',
                        ("e2e-gated@test.local",))
            assert cur.fetchone() is not None, "admin user was not seeded into the new tenant schema"
            cur.execute(f'SELECT contact_phone FROM "{schema_name}".company_settings WHERE id=1')
            assert cur.fetchone()[0] == "+919876543210"

            cur.execute(
                "SELECT status, tenant_id, doc_review_status, gst_number FROM att_master.tenant_applications "
                "WHERE id=%s", (application_id,)
            )
            app_row = cur.fetchone()
            assert app_row[0] == "provisioned"
            assert app_row[1] is not None
            # Documents were uploaded (inside signup_company) and
            # auto-approved in that same call.
            assert app_row[2] == "approved"
            assert app_row[3] == gst

            # A platform-admin "approve" click against an application
            # that's already auto-approved is a harmless no-op -- it must
            # NOT attempt to re-provision or change tenants.status.
            self._login_platform_admin(client)
            resp = client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)
            assert resp.status_code in (301, 302)
            cur.execute(
                "SELECT status, doc_review_status FROM att_master.tenant_applications WHERE id=%s",
                (application_id,)
            )
            assert cur.fetchone() == ("provisioned", "approved")
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active", "approval must not touch an already-active tenant's status"
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_the_code_opens_the_dashboard_with_the_registration_checklist(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()

        company_name = "E2E Straight In"
        slug = company_slug(company_name)
        schema_name = "att_" + slug.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            _application_id, landed = signup_verified(client, monkeypatch, "straightin@test.local", company_name)
            assert landed == slug
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == "straightin@test.local"
                assert sess.get("tenant_slug") == slug and sess.get("tenant_db") == schema_name
                assert "org_access_token" not in sess

            page = client.get(f"/{slug}/admin").get_data(as_text=True)
            assert "Complete your registration" in page and "0 of 3 done" in page
            for path in ("/settings/company_details", "/settings/company_verification", "/settings?tab=billing"):
                assert f'href="/{slug}{path}"' in page, path

            assert save_company_details(client, slug).headers["Location"] == f"/{slug}/admin"
            assert "1 of 3 done" in client.get(f"/{slug}/admin").get_data(as_text=True)

            upload_documents(client, slug)   # auto-approved in tests
            assert "2 of 3 done" in client.get(f"/{slug}/admin").get_data(as_text=True)

            # Payment: an active auto-debit mandate finishes the checklist.
            cur = db_engine.cursor()
            cur.execute("INSERT INTO att_master.auto_debit_mandates (tenant_schema, company_name, status) "
                        "VALUES (%s, %s, 'active')", (schema_name, company_name))
            cur.close()
            db_engine.commit()
            assert "Complete your registration" not in client.get(f"/{slug}/admin").get_data(as_text=True)
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.auto_debit_mandates WHERE tenant_schema=%s", (schema_name,))
            cur.close()
            db_engine.commit()
            _drop_schema(db_engine, schema_name)

    def test_company_details_needs_a_valid_gstin(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()

        company_name = "E2E Bad Gstin"
        slug = company_slug(company_name)
        schema_name = "att_" + slug.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            signup_verified(client, monkeypatch, "badgstin@test.local", company_name)
            for bad in (" ", "not-a-real-gstin"):
                resp = save_company_details(client, slug, gst_number=bad)
                assert resp.headers["Location"] == f"/{slug}/settings/company_details"
            page = client.get(f"/{slug}/settings/company_details").get_data(as_text=True)
            assert "GSTIN" in page and 'name="gst_number"' in page
            cur = db_engine.cursor()
            cur.execute("SELECT gst_number FROM att_master.tenants WHERE subdomain=%s", (slug,))
            assert cur.fetchone()[0] is None
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_admin_signs_in_with_their_email(self, client, db_engine, monkeypatch):
        """The sign-up email is the admin's username -- also when a phone
        keyboard capitalises it."""
        from app import init_master_db
        init_master_db()

        company_name = "Email Login Org"
        subdomain = company_slug(company_name)
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            signup_company(client, monkeypatch, "owner@emaillogin.test", company_name)
            with client.session_transaction() as sess:
                sess.clear()
            resp = client.post(f"/{subdomain}/login", data={
                "identifier": "Owner@EmailLogin.test", "password": "password123",
            }, follow_redirects=False)
            assert resp.status_code == 302 and resp.headers["Location"] == f"/{subdomain}/admin"
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == "owner@emaillogin.test"
        finally:
            _drop_schema(db_engine, schema_name)

    def test_company_named_like_a_reserved_url_gets_a_numbered_one(self, client, db_engine, monkeypatch):
        """"master" is both reserved and the master registry's own schema
        (att_master) -- a company called Master gets /master-2 and its
        tables never land in att_master."""
        from app import init_master_db
        init_master_db()
        _drop_schema(db_engine, "att_master_2")
        try:
            _application_id, slug = signup_verified(client, monkeypatch, "master@reserved.test", "Master")
            assert slug == "master-2"
            cur = db_engine.cursor()
            cur.execute("SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='att_master' AND table_name='admin_users'")
            assert cur.fetchone() is None, "tenant schema migration leaked into the master registry schema"
            cur.close()
        finally:
            _drop_schema(db_engine, "att_master_2")

    def test_taken_company_url_gets_the_next_number(self, client, db_engine, monkeypatch):
        """A URL already held by a differently named company (e.g. one a
        platform admin created by hand) moves the new company to -2."""
        from app import init_master_db
        init_master_db()

        company_name = "E2E Taken Url"
        taken = company_slug(company_name)
        schema_name = "att_" + taken.replace("-", "_") + "_2"
        _drop_schema(db_engine, schema_name)
        cur = db_engine.cursor()
        cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (taken,))
        cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name) VALUES (%s, %s, %s)",
                    ("Unrelated Holder Ltd", taken, "att_" + taken.replace("-", "_")))
        cur.close()
        db_engine.commit()
        try:
            _application_id, slug = signup_verified(client, monkeypatch, "takenurl@test.local", company_name)
            assert slug == f"{taken}-2"
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (taken,))
            cur.close()
            db_engine.commit()
            _drop_schema(db_engine, schema_name)

    def test_duplicate_company_name_blocked_with_generic_message_and_alert(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()

        company_name = "Acme Duplicate Test"
        subdomain = company_slug(company_name)
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            signup_company(client, monkeypatch, "dupname@test.local", company_name)
            with client.session_transaction() as sess:
                sess.clear()

            # A second, unrelated registrant tries the SAME company name
            # (different case/whitespace) -- blocked at step 1 with a
            # generic message that never reveals the real owner's details.
            codes = capture_codes(monkeypatch)
            resp = start_signup(client, "impersonator@test.local", "  acme duplicate test  ")
            assert resp.headers["Location"] == "/create_org"
            assert codes == []
            page = client.get("/create_org")
            assert b"already" in page.data
            assert b"dupname@test.local" not in page.data

            cur = db_engine.cursor()
            cur.execute(
                "SELECT conflicting_company_name, conflicting_admin_email "
                "FROM att_master.tenant_duplicate_alerts WHERE attempted_admin_email=%s",
                ("impersonator@test.local",)
            )
            alert = cur.fetchone()
            assert alert is not None, "duplicate attempt was not recorded for platform-admin review"
            assert alert == ("Acme Duplicate Test", "dupname@test.local")
            cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE attempted_admin_email=%s",
                        ("impersonator@test.local",))
            db_engine.commit()
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_otp_lockout_after_max_attempts(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()

        email = "lockout@test.local"
        try:
            codes = capture_codes(monkeypatch)
            application_id = application_id_from(start_signup(client, email))
            otp = codes[-1]
            wrong_otp = "000000" if otp != "000000" else "111111"

            for _ in range(5):
                verify_email(client, application_id, wrong_otp)

            # Even the CORRECT code is now refused -- the attempt cap trips
            # regardless of what's submitted next.
            resp = verify_email(client, application_id, otp)
            assert resp.status_code in (301, 302)
            assert resp.headers["Location"].startswith("/create_org/verify_otp")

            resp = client.get(f"/create_org/verify_otp?application_id={application_id}", follow_redirects=True)
            assert b"Too many incorrect attempts" in resp.data
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.tenant_applications WHERE admin_email=%s", (email,))
            db_engine.commit()
            cur.close()

    def test_application_rejected_by_platform_admin_suspends_provisioned_tenant(self, client, db_engine, monkeypatch):
        """The tenant is already live by the time a platform admin ever
        gets to review its KYC documents -- rejection can't "prevent"
        provisioning, so it suspends the live tenant instead, reusing the
        same enforcement utils/tenant_routing.py already applies to any
        platform-admin-suspended tenant."""
        from app import init_master_db
        init_master_db()

        company_name = "Reject Me Org"
        subdomain = company_slug(company_name)
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = signup_company(client, monkeypatch, "reject@test.local", company_name)
            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active", "tenant should already be live before any review happens"

            self._login_platform_admin(client)
            resp = client.post(f"/super_admin/applications/{application_id}/reject",
                               data={"reason": "Documents did not match."}, follow_redirects=False)
            assert resp.status_code in (301, 302)

            cur.execute(
                "SELECT status, doc_review_status, rejection_reason FROM att_master.tenant_applications WHERE id=%s",
                (application_id,)
            )
            assert cur.fetchone() == ("provisioned", "rejected", "Documents did not match.")
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "suspended", "a rejected review must suspend the live tenant"
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_duplicate_gst_blocked_even_with_a_wholly_different_company_name(self, client, db_engine, monkeypatch):
        """The trial-abuse case check_duplicate_name() alone can't catch:
        the same real business registers again under a completely different
        company name. Its GSTIN, entered in the dashboard's company details,
        must be refused."""
        from app import init_master_db
        init_master_db()

        first, second = "Gst Dupe Org One", "A Totally Different Business"
        schemas = ["att_" + company_slug(name).replace("-", "_") for name in (first, second)]
        shared_gst = _random_gst()
        for schema in schemas:
            _drop_schema(db_engine, schema)
        try:
            signup_company(client, monkeypatch, "gstdupe1@test.local", first, gst_number=shared_gst)

            # Wholly unrelated company name/URL/email -- only the GSTIN
            # is reused.
            _application_id, slug = signup_verified(client, monkeypatch, "gstimpersonator@test.local", second)
            resp = save_company_details(client, slug, gst_number=shared_gst)
            assert resp.headers["Location"] == f"/{slug}/settings/company_details"
            page = client.get(resp.headers["Location"])
            assert b"already" in page.data
            assert b"gstdupe1@test.local" not in page.data

            cur = db_engine.cursor()
            cur.execute(
                "SELECT match_type, conflicting_admin_email FROM att_master.tenant_duplicate_alerts "
                "WHERE attempted_admin_email=%s", ("gstimpersonator@test.local",)
            )
            assert cur.fetchone() == ("gst", "gstdupe1@test.local"), "GST duplicate attempt was not recorded"
            cur.execute("SELECT gst_number FROM att_master.tenants WHERE subdomain=%s", (slug,))
            assert cur.fetchone()[0] is None, "a duplicate GSTIN must never be saved"
            cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE attempted_admin_email=%s",
                        ("gstimpersonator@test.local",))
            db_engine.commit()
            cur.close()
        finally:
            for schema in schemas:
                _drop_schema(db_engine, schema)

    def test_duplicate_admin_email_blocked_even_with_a_different_company_name(self, client, db_engine, monkeypatch):
        """Same trial-abuse case as GST above: the registrant only reuses
        their own email address -- caught at step 1."""
        from app import init_master_db
        init_master_db()

        company_name = "Email Dupe Org One"
        schema_name = "att_" + company_slug(company_name).replace("-", "_")
        shared_email = "emaildupe-owner@test.local"
        _drop_schema(db_engine, schema_name)
        try:
            signup_company(client, monkeypatch, shared_email, company_name)
            with client.session_transaction() as sess:
                sess.clear()
            cur = db_engine.cursor()
            cur.execute("SELECT COUNT(*) FROM att_master.tenant_applications WHERE admin_email=%s", (shared_email,))
            applications_before = cur.fetchone()[0]

            # Case/whitespace-varied but the same real address -- must
            # still be caught.
            codes = capture_codes(monkeypatch)
            resp = start_signup(client, f"  {shared_email.upper()}  ", "Wholly Unrelated Venture")
            assert resp.headers["Location"] == "/create_org"
            assert codes == []
            assert b"already" in client.get("/create_org").data

            cur.execute(
                "SELECT match_type FROM att_master.tenant_duplicate_alerts "
                "WHERE attempted_admin_email=%s AND match_type='email'", (shared_email,)
            )
            assert cur.fetchone() is not None, "email duplicate attempt was not recorded for platform-admin review"
            cur.execute("SELECT COUNT(*) FROM att_master.tenant_applications WHERE admin_email=%s", (shared_email,))
            assert cur.fetchone()[0] == applications_before, "an email-duplicate signup must not start an application"

            cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE attempted_admin_email=%s",
                        (shared_email,))
            db_engine.commit()
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)


class TestCompanyUrlFromName:
    """org._slug_for_company(): the company URL step 1's name turns into."""

    @pytest.mark.parametrize("name, slug", [
        ("Acme Pvt. Ltd.", "acme-pvt-ltd"),
        ("  ACME   Corp  ", "acme-corp"),
        ("R&D -- Labs 24x7", "r-d-labs-24x7"),
        ("x" * 60, "x" * 40),
        ("Login", "login-2"),        # reserved path segment
        ("API", "api-2"),
        ("!!! ---", None),
    ])
    def test_slug(self, name, slug):
        from blueprints.org import _slug_for_company
        assert _slug_for_company(name) == slug

    def test_numbered_slug_stays_within_the_length_limit(self, db_engine):
        from blueprints.org import _slug_for_company
        base = "y" * 40
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name) VALUES (%s, %s, %s)",
                    ("Long Name Holder", base, "att_" + base))
        cur.close()
        db_engine.commit()
        try:
            assert _slug_for_company("Y" * 60) == "y" * 38 + "-2"
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (base,))
            cur.close()
            db_engine.commit()


class TestCompanyNameFuzzyMatching:
    """check_duplicate_name() used to be an exact (post-normalization)
    string match only -- trivially defeated by adding/dropping a word
    ("Acme Pvt Ltd" -> "Acme India"). It now also runs a normalized
    SequenceMatcher comparison against every existing tenant, unit-tested
    here directly (no schema provisioning needed -- the function only
    ever reads att_master.tenants)."""

    def _insert_fake_tenant(self, db_engine, company_name, subdomain, admin_email="owner@test.local", gst=None):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, gst_number) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (company_name, subdomain, "att_" + subdomain.replace("-", "_"), admin_email, gst)
        )
        tenant_id = cur.fetchone()[0]
        cur.close()
        db_engine.commit()
        return tenant_id

    def _cleanup(self, db_engine, subdomain):
        cur = db_engine.cursor()
        cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
        db_engine.commit()
        cur.close()

    def test_legal_suffix_variant_matches_exactly(self, db_engine):
        from blueprints.org import check_duplicate_name
        self._insert_fake_tenant(db_engine, "Fuzzy Test Widgets Private Limited", "fuzzy-widgets-a")
        try:
            row, match_type = check_duplicate_name("Fuzzy Test Widgets Pvt Ltd")
            assert row is not None
            assert match_type == "exact", "stripping the legal-entity suffix should make these identical"
        finally:
            self._cleanup(db_engine, "fuzzy-widgets-a")

    def test_near_identical_name_matches_fuzzy(self, db_engine):
        from blueprints.org import check_duplicate_name
        self._insert_fake_tenant(db_engine, "Fuzzy Test Consolidated Enterprises", "fuzzy-widgets-b")
        try:
            row, match_type = check_duplicate_name("Fuzzy Test Consolidated Enterprise")
            assert row is not None
            assert match_type == "fuzzy"
        finally:
            self._cleanup(db_engine, "fuzzy-widgets-b")

    def test_genuinely_different_name_does_not_match(self, db_engine):
        from blueprints.org import check_duplicate_name
        self._insert_fake_tenant(db_engine, "Fuzzy Test Consolidated Enterprises", "fuzzy-widgets-c")
        try:
            row, match_type = check_duplicate_name("A Completely Unrelated Bakery")
            assert row is None
            assert match_type is None
        finally:
            self._cleanup(db_engine, "fuzzy-widgets-c")

    def test_check_duplicate_gst_direct(self, db_engine):
        from blueprints.org import check_duplicate_gst
        gst = _random_gst()
        self._insert_fake_tenant(db_engine, "Gst Direct Test Co", "gst-direct-test", gst=gst)
        try:
            row = check_duplicate_gst(gst)
            assert row is not None
            assert row[1] == "Gst Direct Test Co"
            assert check_duplicate_gst(_random_gst()) is None
        finally:
            self._cleanup(db_engine, "gst-direct-test")

    def test_check_duplicate_admin_email_direct(self, db_engine):
        from blueprints.org import check_duplicate_admin_email
        self._insert_fake_tenant(db_engine, "Email Direct Test Co", "email-direct-test",
                                 admin_email="Direct.Owner@Test.Local")
        try:
            row = check_duplicate_admin_email("  direct.owner@test.local  ")
            assert row is not None
            assert row[1] == "Email Direct Test Co"
            assert check_duplicate_admin_email("nobody-else@test.local") is None
        finally:
            self._cleanup(db_engine, "email-direct-test")


class TestSignupOtpCleartextLoggingGate:
    """Finding #12 (Medium): the no-SMTP-configured fallback in
    send_org_signup_otp_email() used to log the raw OTP code unconditionally
    whenever SMTP wasn't configured -- gated only on "no email_cfg", not on
    APP_ENV. A production deployment with SMTP creds missing/expired/
    misconfigured would write the real signup-verification code to
    application logs, letting anyone with log/SIEM read access complete
    email verification for an arbitrary address. Now explicitly gated on
    APP_ENV == "development"; production instead logs an ERROR with no code
    in it at all."""

    def test_development_with_no_smtp_still_logs_the_code(self, monkeypatch):
        import blueprints.org as org_module
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setattr(org_module, "get_email_config", lambda: None)
        mock_log = type("M", (), {"calls": []})()
        monkeypatch.setattr(org_module.app_log, "warning",
                            lambda *a, **k: mock_log.calls.append(("warning", a, k)))
        monkeypatch.setattr(org_module.app_log, "error",
                            lambda *a, **k: mock_log.calls.append(("error", a, k)))
        ok = org_module.send_org_signup_otp_email("someone@test.local", "Acme", "123456")
        assert ok is False
        assert any(lvl == "warning" and "123456" in args for lvl, args, _ in mock_log.calls)

    def test_production_with_no_smtp_does_not_log_the_code(self, monkeypatch):
        import blueprints.org as org_module
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setattr(org_module, "get_email_config", lambda: None)
        mock_log = type("M", (), {"calls": []})()
        monkeypatch.setattr(org_module.app_log, "warning",
                            lambda *a, **k: mock_log.calls.append(("warning", a, k)))
        monkeypatch.setattr(org_module.app_log, "error",
                            lambda *a, **k: mock_log.calls.append(("error", a, k)))
        ok = org_module.send_org_signup_otp_email("someone@test.local", "Acme", "123456")
        assert ok is False
        # The code must not appear in ANY logged call, at any level.
        for _lvl, args, _kwargs in mock_log.calls:
            for a in args:
                assert "123456" not in str(a)
        # ...but the failure must still be visible to ops as an ERROR.
        assert any(lvl == "error" for lvl, _args, _kwargs in mock_log.calls)
