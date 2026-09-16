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
import secrets
import pytest


def _random_gst():
    """A syntactically valid, but not otherwise meaningful, 15-character
    GSTIN -- matches blueprints/org.py's _GST_RE so create_org()/
    api_create_org() accept it, freshly random each call so unrelated
    tests/companies never collide on check_duplicate_gst()."""
    letters = ''.join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(5))
    return (
        f"{secrets.randbelow(100):02d}{letters}{secrets.randbelow(10000):04d}"
        f"{secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{secrets.choice('123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
        f"Z{secrets.choice('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
    )


def _drop_schema(db_engine, schema_name):
    cur = db_engine.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (schema_name,))
    cur.close()


# ===========================================================================
# Signup page / validation paths (no schema creation, fast)
# ===========================================================================

class TestSignupPage:
    def test_get_page_renders(self, client):
        resp = client.get("/create_org")
        assert resp.status_code == 200
        assert b"Register Your Organisation" in resp.data

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
    def test_missing_required_fields_rejected(self, client):
        resp = client.post("/create_org", data={
            "company_name": "", "subdomain": "",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_missing_admin_email_rejected(self, client):
        # admin_email is required now (was optional before) -- needed for
        # password reset and as the tenant's primary contact.
        resp = client.post("/create_org", data={
            "company_name": "Acme", "subdomain": "acme-noemail",
            "admin_username": "admin", "admin_password": "password123",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_invalid_admin_email_rejected(self, client):
        resp = client.post("/create_org", data={
            "company_name": "Acme", "subdomain": "acme-bademail",
            "admin_username": "admin", "admin_password": "password123",
            "admin_email": "not-an-email",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_invalid_subdomain_format_rejected(self, client):
        resp = client.post("/create_org", data={
            "company_name": "Acme", "subdomain": "Not Valid!",
            "admin_username": "admin", "admin_password": "password123",
            "admin_email": "admin@acme.test",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)

    def test_short_password_rejected(self, client):
        resp = client.post("/create_org", data={
            "company_name": "Acme", "subdomain": "acme-test",
            "admin_username": "admin", "admin_password": "short",
            "admin_email": "admin@acme.test",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)

    @pytest.mark.parametrize("subdomain", [
        "hrms", "www", "api", "admin", "master", "super_admin",
    ])
    def test_reserved_subdomain_rejected(self, client, subdomain):
        # _resolve_tenant() (app.py) parses any 3-label host as
        # <label1>.<rest> -- registering "www" would silently hijack
        # www.hrzest.com from that point on. "hrms" stays reserved too,
        # a holdover from the old hrms.gradzest.com domain.
        resp = client.post("/create_org", data={
            "company_name": "Evil Org", "subdomain": subdomain,
            "admin_username": "evil_admin", "admin_password": "password123",
            "admin_email": "evil@test.local",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers.get("Location") == "/create_org"


# ===========================================================================
# Full provisioning — real schema creation, one end-to-end test
# ===========================================================================

class TestGatedSignupFlow:
    """Signup is no longer instant: POST /create_org now only starts a
    pending application (blocking a duplicate company name up front) and
    emails an OTP; provision_tenant() isn't called until a platform admin
    approves the application (blueprints/platform_admin.py) after
    reviewing the uploaded KYC documents. These tests walk the real
    pipeline end-to-end rather than assuming the old single-request
    instant-provision behavior."""

    import io as _io

    _FAKE_PDF = b"%PDF-1.4\n" + b"x" * 20
    _FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20

    def _start_application(self, client, monkeypatch, **overrides):
        import blueprints.org as org_module
        # org.py has an APP_ENV=development convenience branch (added
        # 2026-09-01) that skips the OTP screen entirely for local
        # browser testing without real SMTP -- conftest.py forces
        # APP_ENV=development for the whole suite, so without this
        # override every test in this class would silently take that
        # bypass instead of exercising the real OTP flow these tests are
        # actually verifying (including OTP-lockout, which has nothing to
        # lock out if the screen never appears). Scoped to just this
        # request via monkeypatch so the dev bypass itself stays intact
        # for its real purpose outside the test suite.
        monkeypatch.setenv("APP_ENV", "production")
        # _scan_for_malware() (utils/helpers.py) also reads APP_ENV to decide
        # whether to fail open or closed when ClamAV is unreachable -- forcing
        # "production" above for the OTP gate would otherwise also flip
        # malware scanning to fail-closed, and there's no local ClamAV to
        # reach in this test environment. _MALWARE_SCAN_ENABLED is read once
        # at import time (not per-request like APP_ENV), so it has to be
        # patched directly rather than via monkeypatch.setenv.
        import utils.helpers as helpers_module
        monkeypatch.setattr(helpers_module, "_MALWARE_SCAN_ENABLED", False)
        captured = {}
        monkeypatch.setattr(
            org_module, "send_org_signup_otp_email",
            lambda email, company, otp: captured.setdefault("otp", otp) or True
        )
        payload = {
            "company_name": "Gated Flow Org", "subdomain": "gated-flow-org",
            "admin_username": "gf_admin", "admin_password": "password123",
            "admin_email": "gf@test.local", "email_domain": "test.local",
            "gst_number": _random_gst(),
        }
        payload.update(overrides)
        resp = client.post("/create_org", data=payload, follow_redirects=False)
        return resp, captured.get("otp")

    def _application_id_from_redirect(self, resp):
        location = resp.headers["Location"]
        return int(location.rsplit("=", 1)[-1])

    def _verify_otp(self, client, application_id, otp_code):
        return client.post("/create_org/verify_otp", data={
            "application_id": application_id, "otp_code": otp_code,
        }, follow_redirects=False)

    def _upload_documents(self, client, application_id):
        data = {
            "application_id": str(application_id),
            "registration_cert": (self._io.BytesIO(self._FAKE_PDF), "cert.pdf"),
            "address_proof": (self._io.BytesIO(self._FAKE_PDF), "address.pdf"),
            "visiting_card": (self._io.BytesIO(self._FAKE_PNG), "card.png"),
            "name_board_photo": (self._io.BytesIO(self._FAKE_PNG), "board.png"),
        }
        return client.post("/create_org/upload_documents", data=data,
                           content_type="multipart/form-data", follow_redirects=False)

    def _login_platform_admin(self, client):
        import time as _time
        with client.session_transaction() as sess:
            sess["platform_admin_logged_in"] = True
            sess["platform_admin_username"] = "test_platform_admin"
            sess["platform_admin_last_activity"] = _time.time()

    def _run_full_pipeline(self, client, monkeypatch, **overrides):
        """Start -> verify OTP (which now provisions the tenant instantly,
        see blueprints/org.py's _provision_application_instantly()) ->
        upload docs, leaving the application at status='provisioned',
        doc_review_status='pending_review'. Returns application_id."""
        resp, otp = self._start_application(client, monkeypatch, **overrides)
        assert resp.status_code in (301, 302), resp.data
        application_id = self._application_id_from_redirect(resp)
        assert otp is not None, "OTP email was never sent"

        resp = self._verify_otp(client, application_id, otp)
        assert resp.status_code in (301, 302)
        assert "/create_org/upload_documents" in resp.headers["Location"], resp.headers.get("Location")

        resp = self._upload_documents(client, application_id)
        assert resp.status_code in (301, 302)
        assert "/create_org/status" in resp.headers["Location"], resp.headers.get("Location")

        return application_id

    def test_full_flow_provisions_real_tenant_schema(self, client, db_engine, monkeypatch):
        """Every self-serve signup lane now provisions the tenant
        INSTANTLY right after OTP verification -- before any document is
        uploaded, let alone reviewed by a platform admin. KYC review still
        happens, but it's tracked separately (doc_review_status) and no
        longer gates login/schema creation at all."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-gated-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = self._run_full_pipeline(
                client, monkeypatch, subdomain=subdomain, company_name="E2E Gated Org",
                admin_username="e2e_admin", admin_email="e2e-gated@test.local",
            )

            cur = db_engine.cursor()
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name=%s", (schema_name,)
            )
            assert cur.fetchone() is not None, "tenant schema was not created"

            cur.execute("SELECT db_name, status, plan, subscription_status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
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

            cur.execute(f'SELECT username FROM "{schema_name}".admin_users WHERE username=%s', ("e2e_admin",))
            assert cur.fetchone() is not None, "admin user was not seeded into the new tenant schema"

            cur.execute(
                "SELECT status, tenant_id, doc_review_status FROM att_master.tenant_applications WHERE id=%s",
                (application_id,)
            )
            app_row = cur.fetchone()
            assert app_row[0] == "provisioned"
            assert app_row[1] is not None
            # Documents were uploaded (inside _run_full_pipeline) but not
            # yet reviewed -- KYC review is fully decoupled from provisioning now.
            assert app_row[2] == "pending_review"

            # Platform-admin approval, for an already-provisioned
            # application, is now a no-op confirmation of the KYC review --
            # it must NOT attempt to re-provision or change tenants.status.
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

    def test_subdomain_colliding_with_master_registry_schema_rejected(self, client, db_engine):
        # subdomain "master" is caught by the reserved-subdomain blocklist
        # at step 1, before any application row is even created --
        # unchanged behavior from before the gated flow.
        from app import init_master_db
        init_master_db()

        resp = client.post("/create_org", data={
            "company_name": "Evil Org", "subdomain": "master",
            "admin_username": "evil_admin", "admin_password": "password123",
            "admin_email": "evil@test.local",
        }, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers.get("Location") == "/create_org"

        cur = db_engine.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='att_master' AND table_name='admin_users'"
        )
        polluted = cur.fetchone() is not None
        cur.close()
        assert not polluted, "tenant schema migration leaked into the master registry schema"

    def test_duplicate_subdomain_rejected_at_verification(self, client, db_engine, monkeypatch):
        # Subdomain availability is now enforced at OTP-verification time
        # (provision_tenant() runs right there, see
        # _provision_application_instantly()) -- not at step-1 submission,
        # and not at platform-admin approval either. Two applicants could
        # both submit the same slug, but only the first to verify their
        # OTP can ever actually provision it.
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-dup-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            # NOTE: /create_org's org_access_token lives in the session
            # cookie, one at a time -- verify each applicant fully before
            # starting the next, or the second _start_application()
            # overwrites the first's token and _load_application() fails
            # for an unrelated reason (token mismatch, not the subdomain
            # race this test actually means to exercise).
            resp1, otp1 = self._start_application(
                client, monkeypatch, subdomain=subdomain, company_name="Dup Org One",
                admin_username="dup_admin1", admin_email="dup1@test.local",
            )
            app1 = self._application_id_from_redirect(resp1)
            resp = self._verify_otp(client, app1, otp1)
            assert resp.status_code in (301, 302)
            assert "/create_org/upload_documents" in resp.headers["Location"]

            resp2, otp2 = self._start_application(
                client, monkeypatch, subdomain=subdomain, company_name="Dup Org Two",
                admin_username="dup_admin2", admin_email="dup2@test.local",
            )
            app2 = self._application_id_from_redirect(resp2)

            # Second applicant's OTP is correct too, but provisioning now
            # fails inside verify_otp itself, since the slug is taken --
            # flashed error, redirected back to the start of the form.
            resp = self._verify_otp(client, app2, otp2)
            assert resp.status_code in (301, 302)
            assert resp.headers["Location"] == "/create_org"

            cur = db_engine.cursor()
            cur.execute("SELECT status, tenant_id FROM att_master.tenant_applications WHERE id=%s", (app1,))
            row1 = cur.fetchone()
            assert row1[0] == "provisioned"
            assert row1[1] is not None

            cur.execute("SELECT status, tenant_id FROM att_master.tenant_applications WHERE id=%s", (app2,))
            row2 = cur.fetchone()
            assert row2[0] == "otp_verified", "a failed provisioning attempt must not silently mark provisioned"
            assert row2[1] is None

            cur.execute("SELECT COUNT(*) FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == 1, "only the first applicant's tenant should exist"
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_duplicate_company_name_blocked_with_generic_message_and_alert(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-dupname-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = self._run_full_pipeline(
                client, monkeypatch, subdomain=subdomain, company_name="Acme Duplicate Test",
                admin_username="dupname_admin", admin_email="dupname@test.local",
            )
            self._login_platform_admin(client)
            client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active"

            # A second, unrelated registrant tries the SAME company name
            # (different case/whitespace) -- must be blocked with a
            # generic message that never reveals the real owner's details.
            resp = client.post("/create_org", data={
                "company_name": "  acme duplicate test  ", "subdomain": "some-other-slug",
                "admin_username": "impersonator", "admin_password": "password123",
                "admin_email": "impersonator@test.local", "email_domain": "impersonator.test",
                # Deliberately a DIFFERENT GST than the original application's --
                # this test isolates the company-name-based match, not GST dedup
                # (see TestGstAndEmailDuplicateChecks below for that).
                "gst_number": _random_gst(),
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"already" in resp.data
            assert b"dupname@test.local" not in resp.data

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

        subdomain = "e2e-otp-lockout"
        try:
            resp, otp = self._start_application(
                client, monkeypatch, subdomain=subdomain, company_name="OTP Lockout Org",
                admin_username="lockout_admin", admin_email="lockout@test.local",
            )
            application_id = self._application_id_from_redirect(resp)
            wrong_otp = "000000" if otp != "000000" else "111111"

            for _ in range(5):
                self._verify_otp(client, application_id, wrong_otp)

            # Even the CORRECT code is now refused -- the attempt cap trips
            # regardless of what's submitted next.
            resp = self._verify_otp(client, application_id, otp)
            assert resp.status_code in (301, 302)
            assert resp.headers["Location"].startswith("/create_org/verify_otp")

            resp = client.get(f"/create_org/verify_otp?application_id={application_id}", follow_redirects=True)
            assert b"Too many incorrect attempts" in resp.data
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM att_master.tenant_applications WHERE subdomain=%s", (subdomain,))
            db_engine.commit()
            cur.close()

    def test_application_rejected_by_platform_admin_suspends_provisioned_tenant(self, client, db_engine, monkeypatch):
        """The tenant is already live by the time a platform admin ever
        gets to review its KYC documents (provisioning happens right after
        OTP verification now) -- rejection can no longer "prevent"
        provisioning, so it suspends the already-live tenant instead,
        reusing the same enforcement utils/tenant_routing.py already
        applies to any platform-admin-suspended tenant."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-reject-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            application_id = self._run_full_pipeline(
                client, monkeypatch, subdomain=subdomain, company_name="Reject Me Org",
                admin_username="reject_admin", admin_email="reject@test.local",
            )
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
            application_id = self._run_full_pipeline(
                client, monkeypatch, subdomain=subdomain, company_name="Gst Dupe Org One",
                admin_username="gstdupe_admin1", admin_email="gstdupe1@test.local", gst_number=shared_gst,
            )
            self._login_platform_admin(client)
            client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active"

            # Wholly unrelated company name/subdomain/email -- only the
            # GST number is reused.
            resp = client.post("/create_org", data={
                "company_name": "A Totally Different Business", "subdomain": "totally-different-biz",
                "admin_username": "gstimpersonator", "admin_password": "password123",
                "admin_email": "gstimpersonator@test.local", "email_domain": "gstimpersonator.test",
                "gst_number": shared_gst,
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"already" in resp.data
            assert b"gstdupe1@test.local" not in resp.data

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
            cur.execute("DELETE FROM att_master.tenant_applications WHERE subdomain=%s", ("totally-different-biz",))
            db_engine.commit()
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_duplicate_admin_email_blocked_even_with_different_name_and_gst(self, client, db_engine, monkeypatch):
        """Same trial-abuse case as GST above, but the registrant changes
        company name AND GST and only reuses their own email address."""
        from app import init_master_db
        init_master_db()

        subdomain = "e2e-emaildupe-org"
        schema_name = "att_" + subdomain.replace("-", "_")
        shared_email = "emaildupe-owner@test.local"
        _drop_schema(db_engine, schema_name)
        try:
            application_id = self._run_full_pipeline(
                client, monkeypatch, subdomain=subdomain, company_name="Email Dupe Org One",
                admin_username="emaildupe_admin1", admin_email=shared_email,
            )
            self._login_platform_admin(client)
            client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            assert cur.fetchone()[0] == "active"

            resp = client.post("/create_org", data={
                "company_name": "Yet Another Unrelated Business", "subdomain": "yet-another-unrelated-biz",
                "admin_username": "emailimpersonator", "admin_password": "password123",
                # Case/whitespace-varied but the same real address -- must
                # still be caught.
                "admin_email": f"  {shared_email.upper()}  ", "email_domain": "unrelated.test",
                "gst_number": _random_gst(),
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"already" in resp.data

            cur.execute(
                "SELECT match_type FROM att_master.tenant_duplicate_alerts WHERE attempted_company_name=%s",
                ("Yet Another Unrelated Business",)
            )
            alert = cur.fetchone()
            assert alert is not None, "email duplicate attempt was not recorded for platform-admin review"
            assert alert[0] == "email"

            cur.execute("SELECT 1 FROM att_master.tenants WHERE subdomain=%s", ("yet-another-unrelated-biz",))
            assert cur.fetchone() is None, "an email-duplicate signup must never reach provisioning"

            cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE attempted_company_name=%s",
                        ("Yet Another Unrelated Business",))
            cur.execute("DELETE FROM att_master.tenant_applications WHERE subdomain=%s", ("yet-another-unrelated-biz",))
            db_engine.commit()
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_missing_gst_rejected(self, client):
        resp = client.post("/create_org", data={
            "company_name": "No Gst Co", "subdomain": "no-gst-co",
            "admin_username": "nogst_admin", "admin_password": "password123",
            "admin_email": "nogst@test.local", "email_domain": "test.local",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"GST" in resp.data

    def test_malformed_gst_rejected(self, client):
        resp = client.post("/create_org", data={
            "company_name": "Bad Gst Co", "subdomain": "bad-gst-co",
            "admin_username": "badgst_admin", "admin_password": "password123",
            "admin_email": "badgst@test.local", "email_domain": "test.local",
            "gst_number": "not-a-real-gstin",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"valid" in resp.data.lower()


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
    APP_ENV == "development" (mirroring _verify_application_otp's own dev
    bypass); production instead logs an ERROR with no code in it at all."""

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
