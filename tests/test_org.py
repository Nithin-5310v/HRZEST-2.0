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
    MOBILE, application_id_from, capture_codes, random_gst, signup_company, signup_to_profile,
    start_signup, submit_profile, verify_email,
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
    """Step 1 (POST /create_org) takes just the account -- email, mobile
    number, password. Anything wrong sends the visitor back to the form
    before an application exists."""

    @pytest.mark.parametrize("field, value", [
        ("admin_email", ""), ("admin_email", "not-an-email"),
        ("admin_email", "a" * 45 + "@x.test"),   # over 50 characters: it's the login username
        ("admin_mobile", ""), ("admin_mobile", "12345"), ("admin_mobile", "98765abcde"),
        ("admin_password", ""), ("admin_password", "short"),
    ])
    def test_bad_account_field_rejected(self, client, monkeypatch, field, value):
        codes = capture_codes(monkeypatch)
        data = {"admin_email": "valid@account.test", "admin_mobile": MOBILE, "admin_password": "password123"}
        data[field] = value
        resp = client.post("/create_org", data=data, follow_redirects=False)
        assert resp.headers["Location"] == "/create_org"
        assert codes == []

    def test_valid_account_starts_an_application_with_the_email_as_username(self, client, db_engine, monkeypatch):
        codes = capture_codes(monkeypatch)
        resp = start_signup(client, "  Owner@Account-Start.TEST ", mobile="+91 98765-43210")
        assert resp.headers["Location"].startswith("/create_org/verify_otp?application_id=")
        assert codes, "the verification code must be emailed"
        cur = db_engine.cursor()
        cur.execute("SELECT admin_username, admin_email, admin_mobile, email_domain, company_name, status "
                    "FROM att_master.tenant_applications WHERE id=%s", (application_id_from(resp),))
        row = cur.fetchone()
        cur.close()
        assert row == ("owner@account-start.test", "owner@account-start.test", "+919876543210",
                       "account-start.test", "", "started")


class TestCompanyProfileStep:
    """Step 3 (/create_org/profile): the company's name, GSTIN, URL and logo,
    only once the email code checks out."""

    def test_profile_needs_a_verified_email(self, client, monkeypatch):
        capture_codes(monkeypatch)
        application_id = application_id_from(start_signup(client, "early@profile.test"))
        page = client.get(f"/create_org/profile?application_id={application_id}")
        assert page.headers["Location"] == "/create_org"
        resp = submit_profile(client, application_id, "Too Early Co", "too-early-co")
        assert resp.headers["Location"] == "/create_org"

    def test_profile_page_offers_the_company_fields_and_plans(self, client, monkeypatch):
        application_id = signup_to_profile(client, monkeypatch, "page@profile.test")
        page = client.get(f"/create_org/profile?application_id={application_id}").get_data(as_text=True)
        assert 'action="/create_org/profile"' in page and "Set Up Your Company Profile" in page
        for field in ("company_name", "gst_number", "subdomain", "logo", "signup_plan", "application_id"):
            assert f'name="{field}"' in page, field
        assert 'id="planOptionPayNow"' in page and 'name="admin_password"' not in page

    def test_code_page_sends_an_already_verified_applicant_on_to_the_profile(self, client, monkeypatch):
        application_id = signup_to_profile(client, monkeypatch, "again@profile.test")
        resp = verify_email(client, application_id, "000000")
        assert resp.headers["Location"] == f"/create_org/profile?application_id={application_id}"

    @pytest.mark.parametrize("company_name, subdomain, gst, message", [
        ("", "blank-name-co", "", "required"),
        ("No Gst Co", "no-gst-co", "", "GST"),
        ("Bad Gst Co", "bad-gst-co", "not-a-real-gstin", "valid"),
        ("Evil Org", "login", None, "reserved"),
        ("Evil Org", "api", None, "reserved"),
        ("Evil Org", "webhooks", None, "reserved"),
    ])
    def test_bad_profile_returns_to_the_profile_form(self, client, monkeypatch, company_name, subdomain, gst, message):
        application_id = signup_to_profile(client, monkeypatch, f"bad-{subdomain}@profile.test")
        data = {"application_id": str(application_id), "company_name": company_name, "subdomain": subdomain,
                "gst_number": random_gst() if gst is None else gst}
        resp = client.post("/create_org/profile", data=data, content_type="multipart/form-data")
        assert resp.headers["Location"] == f"/create_org/profile?application_id={application_id}"
        page = client.get(resp.headers["Location"]).get_data(as_text=True)
        assert message.lower() in page.lower()


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

    def test_signup_emails_a_code_and_shows_the_verify_screen(self, client, db_engine, monkeypatch):
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
# Full provisioning — real schema creation, one end-to-end test
# ===========================================================================

class TestGatedSignupFlow:
    """The web wizard end to end: the account (POST /create_org) only
    starts an application and emails a code; the company profile, after
    the code, provisions the company (blueprints/org.py's
    _provision_application_instantly()); KYC documents follow and are
    reviewed after the fact. These tests walk the real pipeline."""

    def _login_platform_admin(self, client):
        import time as _time
        with client.session_transaction() as sess:
            sess["platform_admin_logged_in"] = True
            sess["platform_admin_username"] = "test_platform_admin"
            sess["platform_admin_last_activity"] = _time.time()

    def test_full_flow_provisions_real_tenant_schema(self, client, db_engine, monkeypatch):
        """The company is provisioned the moment its profile is submitted --
        before any document is uploaded, let alone reviewed by a platform
        admin. KYC review still happens, but it's tracked separately
        (doc_review_status) and no longer gates login/schema creation."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-gated-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = signup_company(client, monkeypatch, "e2e-gated@test.local", "E2E Gated Org", subdomain)

            cur = db_engine.cursor()
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name=%s", (schema_name,)
            )
            assert cur.fetchone() is not None, "tenant schema was not created"

            cur.execute("SELECT db_name, status, plan, subscription_status, admin_email FROM att_master.tenants "
                        "WHERE subdomain=%s", (subdomain,))
            row = cur.fetchone()
            assert row is not None, "tenant was not registered in att_master.tenants"
            assert row[0] == schema_name
            assert row[1] == "active"
            import utils.plan_limits as plan_limits
            assert row[2] == plan_limits.PLAN_LABEL
            # subscription_status='trialing' regardless of the chosen
            # payment_option ("online" here, the default) -- nobody pays
            # upfront anymore under this flow, see provision_tenant()'s
            # subscription_status parameter.
            assert row[3] == "trialing"
            assert row[4] == "e2e-gated@test.local"

            # The admin's login username is their email; the mobile number
            # from step 1 becomes the company's contact phone.
            cur.execute(f'SELECT username FROM "{schema_name}".admin_users WHERE username=%s',
                        ("e2e-gated@test.local",))
            assert cur.fetchone() is not None, "admin user was not seeded into the new tenant schema"
            cur.execute(f'SELECT contact_phone FROM "{schema_name}".company_settings WHERE id=1')
            assert cur.fetchone()[0] == "+919876543210"

            cur.execute(
                "SELECT status, tenant_id, doc_review_status FROM att_master.tenant_applications WHERE id=%s",
                (application_id,)
            )
            app_row = cur.fetchone()
            assert app_row[0] == "provisioned"
            assert app_row[1] is not None
            # Documents were uploaded (inside signup_company) and
            # auto-approved in that same call -- no human/AI review is
            # awaited before login works.
            assert app_row[2] == "approved"

            # A platform-admin "approve" click against an application
            # that's already auto-approved is a harmless no-op (the
            # underlying guard just declines to re-approve) -- it must
            # NOT attempt to re-provision or change tenants.status.
            self._login_platform_admin(client)
            resp = client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)
            assert resp.status_code in (301, 302)

            cur.execute(
                "SELECT status, doc_review_status FROM att_master.tenant_applications WHERE id=%s",
                (application_id,)
            )
            app_row = cur.fetchone()
            assert app_row[0] == "provisioned"
            assert app_row[1] == "approved"
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active", "approval must not touch an already-active tenant's status"
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_admin_signs_in_with_their_email(self, client, db_engine, monkeypatch):
        """The sign-up email is the admin's username -- also when a phone
        keyboard capitalises it."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-email-login"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            signup_company(client, monkeypatch, "owner@emaillogin.test", "Email Login Org", subdomain)
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

    def test_subdomain_colliding_with_master_registry_schema_rejected(self, client, db_engine, monkeypatch):
        # "master" is caught by the reserved-subdomain blocklist on the
        # profile step, before anything is provisioned.
        from app import init_master_db
        init_master_db()

        application_id = signup_to_profile(client, monkeypatch, "evil@test.local")
        resp = submit_profile(client, application_id, "Evil Org", "master")
        assert resp.headers.get("Location") == f"/create_org/profile?application_id={application_id}"

        cur = db_engine.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='att_master' AND table_name='admin_users'"
        )
        polluted = cur.fetchone() is not None
        cur.close()
        assert not polluted, "tenant schema migration leaked into the master registry schema"

    def test_taken_company_url_returns_to_the_profile_to_pick_another(self, client, db_engine, monkeypatch):
        # Company URL availability is enforced when the profile provisions
        # the company. A second applicant asking for a taken URL comes back
        # to the profile form, application intact, and can choose another.
        from app import init_master_db
        init_master_db()

        subdomain, other = "e2e-dup-org", "e2e-dup-org-two"
        schemas = ["att_" + s.replace("-", "_") for s in (subdomain, other)]
        for schema in schemas:
            _drop_schema(db_engine, schema)
        try:
            # One signup at a time: /create_org's org_access_token lives in
            # the session cookie, so a second start replaces the first's.
            app1 = signup_company(client, monkeypatch, "dup1@test.local", "Dup Org One", subdomain, documents=False)

            app2 = signup_to_profile(client, monkeypatch, "dup2@test.local")
            resp = submit_profile(client, app2, "Dup Org Two", subdomain)
            assert resp.headers["Location"] == f"/create_org/profile?application_id={app2}"
            assert b"already taken" in client.get(resp.headers["Location"]).data

            cur = db_engine.cursor()
            cur.execute("SELECT status, tenant_id FROM att_master.tenant_applications WHERE id=%s", (app1,))
            row1 = cur.fetchone()
            assert row1[0] == "provisioned" and row1[1] is not None
            cur.execute("SELECT status, tenant_id FROM att_master.tenant_applications WHERE id=%s", (app2,))
            row2 = cur.fetchone()
            assert row2[0] == "otp_verified", "a failed provisioning attempt must not silently mark provisioned"
            assert row2[1] is None
            cur.execute("SELECT COUNT(*) FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == 1, "only the first applicant's tenant should exist"

            resp = submit_profile(client, app2, "Dup Org Two", other)
            assert resp.headers["Location"] == f"/create_org/upload_documents?application_id={app2}"
            cur.execute("SELECT status FROM att_master.tenant_applications WHERE id=%s", (app2,))
            assert cur.fetchone()[0] == "provisioned"
            cur.close()
        finally:
            for schema in schemas:
                _drop_schema(db_engine, schema)

    def test_duplicate_company_name_blocked_with_generic_message_and_alert(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-dupname-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = signup_company(client, monkeypatch, "dupname@test.local", "Acme Duplicate Test",
                                            subdomain)
            self._login_platform_admin(client)
            client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active"

            # A second, unrelated registrant tries the SAME company name
            # (different case/whitespace) -- must be blocked with a
            # generic message that never reveals the real owner's details.
            # Deliberately a DIFFERENT GST: this isolates the name match.
            app2 = signup_to_profile(client, monkeypatch, "impersonator@test.local")
            resp = submit_profile(client, app2, "  acme duplicate test  ", "some-other-slug")
            page = client.get(resp.headers["Location"])
            assert page.status_code == 200
            assert b"already" in page.data
            assert b"dupname@test.local" not in page.data

            cur.execute(
                "SELECT conflicting_company_name, conflicting_admin_email "
                "FROM att_master.tenant_duplicate_alerts WHERE attempted_admin_email=%s",
                ("impersonator@test.local",)
            )
            alert = cur.fetchone()
            assert alert is not None, "duplicate attempt was not recorded for platform-admin review"
            assert alert[0] == "Acme Duplicate Test"
            assert alert[1] == "dupname@test.local"
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
        gets to review its KYC documents (provisioning happens with the
        company profile) -- rejection can no longer "prevent"
        provisioning, so it suspends the already-live tenant instead,
        reusing the same enforcement utils/tenant_routing.py already
        applies to any platform-admin-suspended tenant."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-reject-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = signup_company(client, monkeypatch, "reject@test.local", "Reject Me Org", subdomain)
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
            row = cur.fetchone()
            assert row[0] == "provisioned", "status must stay 'provisioned' -- the tenant still exists"
            assert row[1] == "rejected"
            assert row[2] == "Documents did not match."

            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "suspended", "a rejected review must suspend the live tenant"
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_duplicate_gst_blocked_even_with_a_wholly_different_company_name(self, client, db_engine, monkeypatch):
        """The trial-abuse case check_duplicate_name() alone can't catch:
        the same real business re-registers under a completely different
        company name (no string similarity at all) to get a second free
        trial. check_duplicate_gst() must block this even though the name
        check would let it straight through."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-gstdupe-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        shared_gst = _random_gst()
        _drop_schema(db_engine, schema_name)
        try:
            application_id = signup_company(client, monkeypatch, "gstdupe1@test.local", "Gst Dupe Org One",
                                            subdomain, gst_number=shared_gst)
            self._login_platform_admin(client)
            client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active"

            # Wholly unrelated company name/URL/email -- only the GST
            # number is reused.
            app2 = signup_to_profile(client, monkeypatch, "gstimpersonator@test.local")
            resp = submit_profile(client, app2, "A Totally Different Business", "totally-different-biz", shared_gst)
            page = client.get(resp.headers["Location"])
            assert page.status_code == 200
            assert b"already" in page.data
            assert b"gstdupe1@test.local" not in page.data

            cur.execute(
                "SELECT match_type, conflicting_admin_email FROM att_master.tenant_duplicate_alerts "
                "WHERE attempted_admin_email=%s",
                ("gstimpersonator@test.local",)
            )
            alert = cur.fetchone()
            assert alert is not None, "GST duplicate attempt was not recorded for platform-admin review"
            assert alert[0] == "gst"
            assert alert[1] == "gstdupe1@test.local"

            cur.execute("SELECT 1 FROM att_master.tenants WHERE subdomain=%s", ("totally-different-biz",))
            assert cur.fetchone() is None, "a GST-duplicate signup must never reach provisioning"

            cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE attempted_admin_email=%s",
                        ("gstimpersonator@test.local",))
            cur.execute("DELETE FROM att_master.tenant_applications WHERE admin_email=%s",
                        ("gstimpersonator@test.local",))
            db_engine.commit()
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_duplicate_admin_email_blocked_even_with_different_name_and_gst(self, client, db_engine, monkeypatch):
        """Same trial-abuse case as GST above: the registrant only reuses
        their own email address -- caught at the account step, before any
        company details are asked for."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-emaildupe-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        shared_email = "emaildupe-owner@test.local"
        _drop_schema(db_engine, schema_name)
        try:
            application_id = signup_company(client, monkeypatch, shared_email, "Email Dupe Org One", subdomain)
            self._login_platform_admin(client)
            client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active"
            cur.execute("SELECT COUNT(*) FROM att_master.tenant_applications WHERE admin_email=%s", (shared_email,))
            applications_before = cur.fetchone()[0]

            # Case/whitespace-varied but the same real address -- must
            # still be caught.
            codes = capture_codes(monkeypatch)
            resp = start_signup(client, f"  {shared_email.upper()}  ")
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
