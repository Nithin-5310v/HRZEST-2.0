"""
Platform admin blueprint tests (blueprints/platform_admin.py) -- the
cross-tenant SaaS operator console at /super_admin/*.

tests/test_org.py already exercises the /super_admin/applications*
approve/reject happy paths (via its gated-signup pipeline helpers) and the
/super_admin/duplicate-alerts creation side (via a blocked duplicate
signup) -- this file deliberately does NOT re-walk those same full
pipelines. It covers everything else in the blueprint: the real
login+MFA+logout flow, the dashboard's summary counts, cost/lead/tenant
management, the applications queue/detail/document-streaming views (access
control specifically), the duplicate-alerts admin-facing list/acknowledge
flow, and the per-tenant support chat.

Most routes only need `_platform_admin_required` to see
session["platform_admin_logged_in"]/["platform_admin_username"] truthy --
they never look up a real att_master.platform_admins row (see
blueprints/platform_admin.py's _platform_admin_required) -- so most tests
below reuse the same session_transaction bypass tests/test_org.py's
TestGatedSignupFlow._login_platform_admin() already established. Only
TestLoginFlow, which tests the real credential+MFA path itself, needs an
actual seeded platform_admins row.

Run with:
    python -m pytest tests/test_platform_admin.py -v
"""
import os
import re
import time
import tempfile

import pytest

PA_USERNAME = "pa_test_bypass_admin"


def _login_platform_admin(client, username=PA_USERNAME):
    with client.session_transaction() as sess:
        sess["platform_admin_logged_in"] = True
        sess["platform_admin_username"] = username
        sess["platform_admin_last_activity"] = time.time()


def _drop_schema(db_engine, schema_name):
    cur = db_engine.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (schema_name,))
    cur.close()


# ===========================================================================
# Real login + MFA + logout flow
# ===========================================================================

class TestLoginFlow:
    LOGIN_USERNAME = "pa_real_login_admin"
    LOGIN_PASSWORD = "PaRealLogin@123"

    @pytest.fixture(autouse=True)
    def _mfa_enabled(self, client):
        """MANDATORY_PLATFORM_ADMIN_MFA has no conftest.py override (unlike
        MANDATORY_LOGIN_MFA/MANDATORY_ADMIN_MFA) -- it inherits whatever
        this environment's .env says (this repo's own .env sets it False
        for local-dev convenience). Force it True for the real login-flow
        tests below so they exercise the actual MFA step regardless of the
        environment running them, same pattern tests/test_login_mfa.py uses
        for MANDATORY_LOGIN_MFA."""
        original = client.application.config.get("MANDATORY_PLATFORM_ADMIN_MFA")
        client.application.config["MANDATORY_PLATFORM_ADMIN_MFA"] = True
        yield
        client.application.config["MANDATORY_PLATFORM_ADMIN_MFA"] = original

    @pytest.fixture
    def platform_admin_row(self, db_engine):
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute("DELETE FROM att_master.platform_admins WHERE username=%s", (self.LOGIN_USERNAME,))
        cur.execute(
            "INSERT INTO att_master.platform_admins (username, password, email) VALUES (%s,%s,%s)",
            (self.LOGIN_USERNAME, generate_password_hash(self.LOGIN_PASSWORD), "pa_real_login@test.local"),
        )
        yield
        cur.execute("DELETE FROM att_master.platform_admins WHERE username=%s", (self.LOGIN_USERNAME,))
        cur.close()

    def _capture_otp(self, monkeypatch):
        import blueprints.platform_admin as pa_module
        captured = {}
        monkeypatch.setattr(
            pa_module, "send_mfa_login_email",
            lambda email, username, role_label, secret, otp_code: captured.setdefault("otp", otp_code) or True,
        )
        return captured

    def test_login_page_renders(self, client):
        resp = client.get("/super_admin/login")
        assert resp.status_code == 200

    def test_already_logged_in_redirects_to_dashboard(self, client):
        _login_platform_admin(client)
        resp = client.get("/super_admin/login", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin"

    def test_wrong_password_rejected(self, client, platform_admin_row):
        resp = client.post("/super_admin/login", data={
            "username": self.LOGIN_USERNAME, "password": "wrong-password",
        })
        assert resp.status_code == 200
        assert b"Invalid credentials." in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("platform_admin_logged_in")

    def test_unknown_username_rejected(self, client):
        resp = client.post("/super_admin/login", data={
            "username": "no_such_platform_admin", "password": "whatever123",
        })
        assert resp.status_code == 200
        assert b"Invalid credentials." in resp.data

    def test_correct_password_renders_mfa_step_on_same_page(self, client, platform_admin_row, monkeypatch):
        # Merged single-page flow: credentials POST re-renders /super_admin/login
        # in its step-2 (OTP) state instead of redirecting to a separate page.
        captured = self._capture_otp(monkeypatch)
        resp = client.post("/super_admin/login", data={
            "username": self.LOGIN_USERNAME, "password": self.LOGIN_PASSWORD,
        }, follow_redirects=False)
        assert resp.status_code == 200
        assert b"Check Your Email" in resp.data
        assert captured.get("otp"), "MFA OTP email was never 'sent'"
        with client.session_transaction() as sess:
            assert sess.get("platform_mfa_pending") is True
            assert sess.get("platform_mfa_user") == self.LOGIN_USERNAME
            assert not sess.get("platform_admin_logged_in")

    def test_login_without_pending_session_shows_credentials_step(self, client):
        resp = client.get("/super_admin/login", follow_redirects=False)
        assert resp.status_code == 200
        assert b"Check Your Email" not in resp.data

    def test_restart_query_param_clears_pending_mfa_session(self, client, platform_admin_row, monkeypatch):
        self._capture_otp(monkeypatch)
        client.post("/super_admin/login", data={
            "username": self.LOGIN_USERNAME, "password": self.LOGIN_PASSWORD,
        })
        resp = client.get("/super_admin/login?restart=1", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"
        with client.session_transaction() as sess:
            assert not sess.get("platform_mfa_pending")

    def test_mfa_verify_wrong_code_rejected(self, client, platform_admin_row, monkeypatch):
        self._capture_otp(monkeypatch)
        client.post("/super_admin/login", data={
            "username": self.LOGIN_USERNAME, "password": self.LOGIN_PASSWORD,
        })
        resp = client.post("/super_admin/login", data={"otp_code": "000000"})
        assert resp.status_code == 200
        assert b"Invalid verification code." in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("platform_admin_logged_in")

    def test_mfa_verify_correct_code_completes_login(self, client, db_engine, platform_admin_row, monkeypatch):
        captured = self._capture_otp(monkeypatch)
        client.post("/super_admin/login", data={
            "username": self.LOGIN_USERNAME, "password": self.LOGIN_PASSWORD,
        })
        resp = client.post("/super_admin/login", data={"otp_code": captured["otp"]}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin"
        with client.session_transaction() as sess:
            assert sess.get("platform_admin_logged_in") is True
            assert sess.get("platform_admin_username") == self.LOGIN_USERNAME
            assert sess.get("platform_admin_last_activity")

    def test_mfa_code_expires_after_ttl(self, client, platform_admin_row, monkeypatch):
        self._capture_otp(monkeypatch)
        client.post("/super_admin/login", data={
            "username": self.LOGIN_USERNAME, "password": self.LOGIN_PASSWORD,
        })
        with client.session_transaction() as sess:
            sess["platform_mfa_issued_at"] = time.time() - 400  # > _MFA_OTP_TTL_SEC (300)
        resp = client.get("/super_admin/login")
        assert resp.status_code == 200
        assert b"Your code expired" in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("platform_mfa_pending")

    def test_logout_clears_session(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/logout", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"
        with client.session_transaction() as sess:
            assert not sess.get("platform_admin_logged_in")


class TestPlatformAdminRequiredGuard:
    def test_dashboard_requires_login(self, client):
        resp = client.get("/super_admin", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_idle_timeout_clears_session_and_redirects(self, client):
        _login_platform_admin(client)
        with client.session_transaction() as sess:
            sess["platform_admin_last_activity"] = time.time() - 3600  # > 30min idle cap
        resp = client.get("/super_admin", follow_redirects=True)
        assert resp.status_code == 200
        assert b"session expired due to inactivity" in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("platform_admin_logged_in")


# ===========================================================================
# Dashboard
# ===========================================================================

class TestDashboard:
    def test_dashboard_renders_for_logged_in_admin(self, client):
        _login_platform_admin(client)
        resp = client.get("/super_admin")
        assert resp.status_code == 200

    def test_dashboard_reflects_pending_application_and_alert_counts(self, client, db_engine):
        _login_platform_admin(client)
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM att_master.tenant_applications WHERE status='pending_review'")
        before_pending = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM att_master.tenant_duplicate_alerts WHERE acknowledged=0")
        before_alerts = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO att_master.tenant_applications "
            "(company_name, subdomain, admin_username, admin_email, admin_password_hash, access_token_hash, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,'pending_review') RETURNING id",
            ("Dash Count Co", "dash-count-co", "dc_admin", "dc@test.local", "hash", "tok"),
        )
        app_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO att_master.tenant_duplicate_alerts "
            "(attempted_company_name, attempted_admin_email, conflicting_tenant_id, conflicting_company_name) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            ("Dup Co", "dup@test.local", 999999, "Existing Co"),
        )
        alert_id = cur.fetchone()[0]
        try:
            resp = client.get("/super_admin")
            assert resp.status_code == 200
            m_apps = re.search(rb'Applications\s*<span[^>]*>(\d+)</span>', resp.data)
            assert m_apps, "pending-applications badge not found on dashboard"
            assert int(m_apps.group(1)) == before_pending + 1

            m_alerts = re.search(rb'Duplicate Alerts\s*<span[^>]*>(\d+)</span>', resp.data)
            assert m_alerts, "duplicate-alerts badge not found on dashboard"
            assert int(m_alerts.group(1)) == before_alerts + 1
        finally:
            cur.execute("DELETE FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE id=%s", (alert_id,))
            cur.close()

    def test_dashboard_activity_feed_shows_honeypot_hit_with_ip(self, client, db_engine):
        """The dashboard's Recent Activity preview (_recent_platform_activity())
        must include deception-honeypot trap hits and their source IP --
        previously it only queried platform_admin./billing./org. prefixes,
        so a scanner bot tripping /.env or /wp-admin/ never showed up here
        at all, only buried in the DB with no UI surfacing it."""
        _login_platform_admin(client)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO security_events (event_type, level, message, identifier, ip) VALUES "
            "('secops.honeypot_triggered', 'CRITICAL', "
            "'Deception honeypot trap ''/wp-admin/'' triggered by automated scanner bot', NULL, '198.51.100.23') "
            "RETURNING id"
        )
        event_id = cur.fetchone()[0]
        db_engine.commit()
        try:
            resp = client.get("/super_admin")
            assert resp.status_code == 200
            assert b"Deception honeypot trap" in resp.data
            assert b"198.51.100.23" in resp.data
        finally:
            cur.execute("SET audit.bypass = 'on'")
            cur.execute("DELETE FROM security_events WHERE id=%s", (event_id,))
            cur.execute("SET audit.bypass = 'off'")
            db_engine.commit()
            cur.close()

    def test_dashboard_shows_recent_feedback(self, client, db_engine):
        """Product feedback from the public landing page (POST /api/feedback,
        blueprints/org.py) is read-only on the dashboard --
        blueprints/platform_admin.py's _recent_feedback()."""
        _login_platform_admin(client)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.feedback (feedback_type, rating, email, message) "
            "VALUES ('Biometrics', 5, 'dash-fb@test.local', 'Dashboard visibility check message') RETURNING id"
        )
        fb_id = cur.fetchone()[0]
        try:
            resp = client.get("/super_admin")
            assert resp.status_code == 200
            body = resp.data.decode("utf-8", "ignore")
            assert "Dashboard visibility check message" in body
            assert "Product Feedback" in body
        finally:
            cur.execute("DELETE FROM att_master.feedback WHERE id=%s", (fb_id,))
            cur.close()

    def test_chat_button_onclick_survives_quotes_in_company_name(self, client, db_engine):
        """Regression test: the Chat button used to build its onclick via
        `tenantChatOpen({{ t.id }}, {{ t.company_name | tojson }})` --
        tojson emits literal double quotes (valid JSON), which collided with
        the onclick="..." attribute's own double-quote delimiter and
        truncated it mid-string for every company, not just ones with an
        actual quote in the name (see the fix: data-tenant-id/data-company-name
        attributes, Jinja-default-escaped, read via element.dataset instead)."""
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ('Quote"Co', "quote-co-chat-test", "att_quote_co_chat_test"),
        )
        tenant_id = cur.fetchone()[0]
        try:
            _login_platform_admin(client)
            resp = client.get("/super_admin")
            assert resp.status_code == 200
            html_text = resp.data.decode("utf-8")
            assert f'data-tenant-id="{tenant_id}"' in html_text
            assert "&#34;" in html_text or "&quot;" in html_text  # the embedded quote, safely escaped
            assert 'onclick="tenantChatOpen(this.dataset.tenantId, this.dataset.companyName)"' in html_text
            # The old bug: a literal, unescaped `"` breaking out of the onclick attribute.
            assert f'onclick="tenantChatOpen({tenant_id}, "' not in html_text
        finally:
            cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
            cur.close()


# ===========================================================================
# Costs
# ===========================================================================

class TestSetCosts:
    def test_requires_login(self, client):
        resp = client.post("/super_admin/costs", data={}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_valid_costs_update(self, client, db_engine):
        _login_platform_admin(client)
        cur = db_engine.cursor()
        cur.execute("SELECT monthly_aws_paise, monthly_maintenance_paise FROM att_master.platform_costs WHERE id=1")
        original = cur.fetchone()
        try:
            resp = client.post("/super_admin/costs", data={
                "monthly_aws": "123.45", "monthly_maintenance": "67.89",
            }, follow_redirects=False)
            assert resp.status_code in (301, 302)
            cur.execute("SELECT monthly_aws_paise, monthly_maintenance_paise FROM att_master.platform_costs WHERE id=1")
            row = cur.fetchone()
            assert row == (12345, 6789)
        finally:
            cur.execute(
                "UPDATE att_master.platform_costs SET monthly_aws_paise=%s, monthly_maintenance_paise=%s WHERE id=1",
                original,
            )
            cur.close()

    def test_negative_costs_rejected(self, client, db_engine):
        _login_platform_admin(client)
        cur = db_engine.cursor()
        cur.execute("SELECT monthly_aws_paise, monthly_maintenance_paise FROM att_master.platform_costs WHERE id=1")
        original = cur.fetchone()
        resp = client.post("/super_admin/costs", data={
            "monthly_aws": "-5", "monthly_maintenance": "10",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Enter valid non-negative amounts" in resp.data
        cur.execute("SELECT monthly_aws_paise, monthly_maintenance_paise FROM att_master.platform_costs WHERE id=1")
        assert cur.fetchone() == original
        cur.close()


# ===========================================================================
# Leads
# ===========================================================================

class TestSetLeadStatus:
    @pytest.fixture
    def lead_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.leads (name, email, company_name, status) "
            "VALUES (%s,%s,%s,'new') RETURNING id",
            ("Lead Status Test", "lead-status@test.local", "Lead Status Co"),
        )
        lead_id = cur.fetchone()[0]
        yield lead_id
        cur.execute("DELETE FROM att_master.leads WHERE id=%s", (lead_id,))
        cur.close()

    def test_requires_login(self, client, lead_row):
        resp = client.post(f"/super_admin/leads/{lead_row}/status", data={"status": "contacted"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_valid_status_update(self, client, db_engine, lead_row):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/leads/{lead_row}/status", data={"status": "converted"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.leads WHERE id=%s", (lead_row,))
        assert cur.fetchone()[0] == "converted"
        cur.close()

    def test_invalid_status_rejected(self, client, db_engine, lead_row):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/leads/{lead_row}/status", data={"status": "bogus"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid lead status." in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.leads WHERE id=%s", (lead_row,))
        assert cur.fetchone()[0] == "new"
        cur.close()


# ===========================================================================
# Tenant creation -- the password-hashing round trip is the key regression
# this covers (provision_tenant() now takes admin_password_hash, not
# plaintext -- a signature change platform_admin_create_tenant() must hash
# for correctly).
# ===========================================================================

def _new_company_email_code(client, monkeypatch, admin_email):
    """Clicks the New Company form's "Send code" for admin_email and returns
    the code that was emailed -- creating a company requires it."""
    import blueprints.org as org_module
    sent = {}
    monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                        lambda to, company, otp: sent.update(to=to, otp=otp) or True)
    resp = client.post("/super_admin/tenants/send_email_code", json={"admin_email": admin_email})
    assert resp.status_code == 200, resp.data
    assert sent["to"] == admin_email.lower()
    return sent["otp"]


class TestCreateTenant:
    def test_requires_login(self, client):
        resp = client.post("/super_admin/tenants/create", data={}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_create_tenant_hashes_password_and_admin_can_log_in(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        _login_platform_admin(client)

        subdomain = "sa-create-tenant"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            resp = client.post("/super_admin/tenants/create", data={
                "company_name": "SA Create Tenant Co",
                "subdomain": subdomain,
                "admin_username": "sact_admin",
                "admin_password": "SactPass@123",
                "admin_email": "sact@test.local",
                "email_code": _new_company_email_code(client, monkeypatch, "sact@test.local"),
                "payment_option": "manual",
                "email_domain": "test.local",
            }, follow_redirects=False)
            assert resp.status_code in (301, 302), resp.data

            cur = db_engine.cursor()
            cur.execute("SELECT db_name, status FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            row = cur.fetchone()
            assert row is not None, "tenant was not registered"
            assert row[0] == schema_name
            assert row[1] == "active"

            cur.execute(f'SELECT password FROM "{schema_name}".admin_users WHERE username=%s', ("sact_admin",))
            stored_hash = cur.fetchone()[0]
            # Never the plaintext password itself, and looks like a real
            # bcrypt hash -- catches a regression back to passing plaintext
            # straight through to provision_tenant().
            assert stored_hash != "SactPass@123"
            assert stored_hash.startswith("$2")
            cur.close()

            # The actual regression check: the submitted password must
            # round-trip through whatever hash got stored.
            login_resp = client.post(f"/{subdomain}/login", data={
                "identifier": "sact_admin", "password": "SactPass@123",
            }, follow_redirects=False)
            assert login_resp.status_code in (301, 302), login_resp.data
            assert login_resp.headers["Location"].startswith(f"/{subdomain}/")
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == "sact_admin"
        finally:
            _drop_schema(db_engine, schema_name)

    def test_full_email_in_domain_field_is_accepted(self, client, db_engine, monkeypatch):
        # The "Employee Email Domain" field sits next to "Admin Email" and
        # browsers autofill an email into it -- that used to bounce back
        # with "Enter a valid domain" no matter how often it was retried.
        from app import init_master_db
        init_master_db()
        _login_platform_admin(client)

        subdomain = "sa-email-domain"
        schema_name = "att_" + subdomain.replace("-", "_")
        _drop_schema(db_engine, schema_name)
        try:
            resp = client.post("/super_admin/tenants/create", data={
                "company_name": "SA Email Domain Co",
                "subdomain": subdomain,
                "admin_username": "saed_admin",
                "admin_password": "SaedPass@123",
                "admin_email": "saed@test.local",
                "email_code": _new_company_email_code(client, monkeypatch, "saed@test.local"),
                "payment_option": "manual",
                "email_domain": "saed@Test.Local",
            }, follow_redirects=False)
            assert resp.status_code in (301, 302), resp.data

            cur = db_engine.cursor()
            cur.execute(f'SELECT email_domain FROM "{schema_name}".company_settings WHERE id=1')
            assert cur.fetchone()[0] == "test.local"
            cur.close()
        finally:
            _drop_schema(db_engine, schema_name)

    def test_invalid_fields_rejected_without_provisioning(self, client, db_engine):
        _login_platform_admin(client)
        resp = client.post("/super_admin/tenants/create", data={
            "company_name": "Bad Co",
            "subdomain": "super_admin",  # reserved
            "admin_username": "bad_admin",
            "admin_password": "password123",
            "admin_email": "bad@test.local",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM att_master.tenants WHERE subdomain='super_admin'")
        assert cur.fetchone() is None
        cur.close()


# ===========================================================================
# Tenant status (activate/suspend)
# ===========================================================================

class TestTenantStatus:
    @pytest.fixture
    def tenant_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ("Status Test Co", "status-test-co", "att_status_test_co"),
        )
        tenant_id = cur.fetchone()[0]
        yield tenant_id
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
        cur.close()

    def test_requires_login(self, client, tenant_row):
        resp = client.post(f"/super_admin/tenants/{tenant_row}/status", data={"status": "suspended"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_suspend_and_reactivate(self, client, db_engine, tenant_row):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tenants/{tenant_row}/status", data={"status": "suspended"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.tenants WHERE id=%s", (tenant_row,))
        assert cur.fetchone()[0] == "suspended"

        resp = client.post(f"/super_admin/tenants/{tenant_row}/status", data={"status": "active"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur.execute("SELECT status FROM att_master.tenants WHERE id=%s", (tenant_row,))
        assert cur.fetchone()[0] == "active"
        cur.close()

    def test_invalid_status_rejected(self, client, db_engine, tenant_row):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tenants/{tenant_row}/status", data={"status": "deleted"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid status." in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.tenants WHERE id=%s", (tenant_row,))
        assert cur.fetchone()[0] == "active"
        cur.close()


class TestBulkTenantStatus:
    @pytest.fixture
    def two_tenant_rows(self, db_engine):
        cur = db_engine.cursor()
        ids = []
        for i in range(2):
            cur.execute(
                "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
                "VALUES (%s,%s,%s,'active') RETURNING id",
                (f"Bulk Test Co {i}", f"bulk-test-co-{i}", f"att_bulk_test_co_{i}"),
            )
            ids.append(cur.fetchone()[0])
        yield ids
        cur.execute("DELETE FROM att_master.tenants WHERE id=ANY(%s)", (ids,))
        cur.close()

    def test_requires_login(self, client, two_tenant_rows):
        resp = client.post("/super_admin/tenants/bulk-status",
                           data={"status": "suspended", "tenant_ids": [str(i) for i in two_tenant_rows]},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_bulk_deactivate_and_reactivate(self, client, db_engine, two_tenant_rows):
        _login_platform_admin(client)
        resp = client.post("/super_admin/tenants/bulk-status",
                           data={"status": "suspended", "tenant_ids": [str(i) for i in two_tenant_rows]},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"2 companies updated." in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.tenants WHERE id=ANY(%s)", (two_tenant_rows,))
        assert [r[0] for r in cur.fetchall()] == ["suspended", "suspended"]

        resp = client.post("/super_admin/tenants/bulk-status",
                           data={"status": "active", "tenant_ids": [str(i) for i in two_tenant_rows]},
                           follow_redirects=True)
        assert resp.status_code == 200
        cur.execute("SELECT status FROM att_master.tenants WHERE id=ANY(%s)", (two_tenant_rows,))
        assert [r[0] for r in cur.fetchall()] == ["active", "active"]
        cur.close()

    def test_no_selection_rejected(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/tenants/bulk-status", data={"status": "suspended"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"No companies were selected." in resp.data

    def test_invalid_status_rejected(self, client, db_engine, two_tenant_rows):
        _login_platform_admin(client)
        resp = client.post("/super_admin/tenants/bulk-status",
                           data={"status": "deleted", "tenant_ids": [str(two_tenant_rows[0])]},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid status." in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM att_master.tenants WHERE id=%s", (two_tenant_rows[0],))
        assert cur.fetchone()[0] == "active"
        cur.close()


class TestDeleteTenant:
    @pytest.fixture
    def deletable_tenant(self, db_engine):
        """A tenant with a REAL schema (not just a tenants row) so the test
        can verify DROP SCHEMA actually ran, not just that the tenants row
        disappeared."""
        schema = "att_delete_test_co"
        cur = db_engine.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'CREATE TABLE "{schema}".marker (id SERIAL PRIMARY KEY, note TEXT)')
        cur.execute(f'INSERT INTO "{schema}".marker (note) VALUES (%s)', ("still here",))
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ("Delete Test Co", "delete-test-co", schema),
        )
        tenant_id = cur.fetchone()[0]
        yield tenant_id, "delete-test-co", schema
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.close()

    def test_requires_login(self, client, deletable_tenant):
        tenant_id, subdomain, _schema = deletable_tenant
        resp = client.post(f"/super_admin/tenants/{tenant_id}/delete",
                           data={"confirm_subdomain": subdomain}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_wrong_confirmation_text_is_refused(self, client, db_engine, deletable_tenant):
        tenant_id, subdomain, schema = deletable_tenant
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tenants/{tenant_id}/delete",
                           data={"confirm_subdomain": "not-the-right-subdomain"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"you must type" in resp.data

        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM att_master.tenants WHERE id=%s", (tenant_id,))
        assert cur.fetchone() is not None
        cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name=%s", (schema,))
        assert cur.fetchone() is not None, "schema must survive a rejected confirmation"
        cur.close()

    def test_correct_confirmation_drops_schema_and_tenant_row(self, client, db_engine, deletable_tenant):
        tenant_id, subdomain, schema = deletable_tenant
        _login_platform_admin(client, username="delete_test_admin")
        resp = client.post(f"/super_admin/tenants/{tenant_id}/delete",
                           data={"confirm_subdomain": subdomain}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"permanently deleted" in resp.data

        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM att_master.tenants WHERE id=%s", (tenant_id,))
        assert cur.fetchone() is None, "tenants row must be gone"
        cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name=%s", (schema,))
        assert cur.fetchone() is None, "the tenant's actual Postgres schema must be dropped"
        cur.close()

    def test_confirmation_is_case_insensitive(self, client, db_engine, deletable_tenant):
        tenant_id, subdomain, schema = deletable_tenant
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tenants/{tenant_id}/delete",
                           data={"confirm_subdomain": subdomain.upper()}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"permanently deleted" in resp.data

    def test_unknown_tenant_flashes_not_found(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/tenants/999999999/delete",
                           data={"confirm_subdomain": "whatever"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Company not found." in resp.data

    def test_payment_history_survives_deletion(self, client, db_engine, deletable_tenant):
        """The whole point of this feature: billing/transaction records
        must keep showing correctly in the Payments feed after the tenant
        (and all its own data) is gone -- payment_orders/seat_topup_orders/
        monthly_invoices carry their own company_name/subdomain rather than
        depending on a live tenants row."""
        tenant_id, subdomain, schema = deletable_tenant
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id) "
            "VALUES (%s,'starter',10,9900,%s,%s,'del_admin','del_admin@test.local','paid',%s)",
            (f"demo_order_del_test_{tenant_id}", "Delete Test Co", subdomain, tenant_id),
        )
        db_engine.commit()
        try:
            _login_platform_admin(client, username="payment_survives_admin")
            resp = client.post(f"/super_admin/tenants/{tenant_id}/delete",
                               data={"confirm_subdomain": subdomain}, follow_redirects=True)
            assert resp.status_code == 200

            cur.execute(
                "SELECT company_name, subdomain, status FROM att_master.payment_orders WHERE tenant_id=%s",
                (tenant_id,),
            )
            row = cur.fetchone()
            assert row is not None, "payment_orders row must survive tenant deletion"
            assert row[0] == "Delete Test Co"
            assert row[1] == subdomain
            assert row[2] == "paid"

            dashboard_resp = client.get("/super_admin")
            assert b"Delete Test Co" in dashboard_resp.data  # still shown in the Payments feed
        finally:
            cur.execute("DELETE FROM att_master.payment_orders WHERE tenant_id=%s", (tenant_id,))
            cur.close()


class TestDashboardSearchPaginationAndExport:
    @pytest.fixture
    def searchable_tenant(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ("Zephyr Search Target Co", "zephyr-search-target", "att_zephyr_search_target"),
        )
        tenant_id = cur.fetchone()[0]
        yield tenant_id
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
        cur.close()

    def test_search_filters_to_matching_company(self, client, searchable_tenant):
        _login_platform_admin(client)
        resp = client.get("/super_admin?q=Zephyr+Search+Target")
        assert resp.status_code == 200
        assert b"Zephyr Search Target Co" in resp.data

        resp = client.get("/super_admin?q=NoSuchCompanyXYZ123")
        assert resp.status_code == 200
        assert b"Zephyr Search Target Co" not in resp.data
        assert b"No companies match your search." in resp.data

    def test_out_of_range_page_clamps_instead_of_erroring(self, client, searchable_tenant):
        _login_platform_admin(client)
        resp = client.get("/super_admin?page=99999")
        assert resp.status_code == 200

    def test_export_tenants_csv(self, client, searchable_tenant):
        _login_platform_admin(client)
        resp = client.get("/super_admin/export/tenants.csv?q=Zephyr+Search+Target")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("text/csv")
        assert b"Zephyr Search Target Co" in resp.data
        assert resp.data.startswith(b"Company,Subdomain,Status")

    def test_export_payments_csv_requires_login(self, client):
        resp = client.get("/super_admin/export/payments.csv", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_export_payments_csv(self, client):
        _login_platform_admin(client)
        resp = client.get("/super_admin/export/payments.csv")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("text/csv")
        assert resp.data.startswith(b"Kind,Company,Subdomain")


# ===========================================================================
# Applications queue / detail / document streaming
# (approve/reject happy paths are covered by tests/test_org.py already --
# this focuses on the queue list, detail view, and document access control.)
# ===========================================================================

class TestApplicationsQueueAndDetail:
    @pytest.fixture
    def application_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_applications "
            "(company_name, subdomain, admin_username, admin_email, admin_password_hash, access_token_hash, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,'pending_review') RETURNING id",
            ("Queue Test Co", "queue-test-co", "qt_admin", "qt@test.local", "hash", "tok"),
        )
        app_id = cur.fetchone()[0]
        yield app_id
        cur.execute("DELETE FROM att_master.tenant_applications WHERE id=%s", (app_id,))
        cur.close()

    def test_queue_requires_login(self, client):
        resp = client.get("/super_admin/applications", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_queue_lists_pending_application(self, client, application_row):
        _login_platform_admin(client)
        resp = client.get("/super_admin/applications")
        assert resp.status_code == 200
        assert b"Queue Test Co" in resp.data

    def test_queue_default_filter_excludes_rejected(self, client, db_engine, application_row):
        _login_platform_admin(client)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_applications "
            "(company_name, subdomain, admin_username, admin_email, admin_password_hash, access_token_hash, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,'rejected') RETURNING id",
            ("Rejected Queue Co", "rejected-queue-co", "rq_admin", "rq@test.local", "hash", "tok"),
        )
        rejected_id = cur.fetchone()[0]
        try:
            resp = client.get("/super_admin/applications")
            assert resp.status_code == 200
            assert b"Queue Test Co" in resp.data
            assert b"Rejected Queue Co" not in resp.data

            resp_all = client.get("/super_admin/applications?status=all")
            assert resp_all.status_code == 200
            assert b"Queue Test Co" in resp_all.data
            assert b"Rejected Queue Co" in resp_all.data
        finally:
            cur.execute("DELETE FROM att_master.tenant_applications WHERE id=%s", (rejected_id,))
            cur.close()

    def test_detail_requires_login(self, client, application_row):
        resp = client.get(f"/super_admin/applications/{application_row}", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_detail_shows_application(self, client, application_row):
        _login_platform_admin(client)
        resp = client.get(f"/super_admin/applications/{application_row}")
        assert resp.status_code == 200
        assert b"Queue Test Co" in resp.data

    def test_detail_unknown_id_flashes_and_redirects(self, client):
        _login_platform_admin(client)
        resp = client.get("/super_admin/applications/999999999", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Application not found." in resp.data


class TestApplicationDocument:
    @pytest.fixture
    def application_with_doc(self, db_engine):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(b"%PDF-1.4 fake certificate contents")

        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_applications "
            "(company_name, subdomain, admin_username, admin_email, admin_password_hash, access_token_hash, "
            "status, doc_registration_cert) "
            "VALUES (%s,%s,%s,%s,%s,%s,'pending_review',%s) RETURNING id",
            ("Doc Test Co", "doc-test-co", "dt_admin", "dt@test.local", "hash", "tok", path),
        )
        app_id = cur.fetchone()[0]
        yield app_id, path
        cur.execute("DELETE FROM att_master.tenant_applications WHERE id=%s", (app_id,))
        cur.close()
        try:
            os.remove(path)
        except OSError:
            pass

    def test_document_route_requires_login(self, client, application_with_doc):
        app_id, _path = application_with_doc
        resp = client.get(f"/super_admin/applications/{app_id}/documents/registration_cert",
                          follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_valid_doc_kind_streams_file_from_db_row(self, client, application_with_doc):
        _login_platform_admin(client)
        app_id, _path = application_with_doc
        resp = client.get(f"/super_admin/applications/{app_id}/documents/registration_cert")
        assert resp.status_code == 200
        assert b"fake certificate contents" in resp.data

    def test_path_cannot_be_overridden_by_client_supplied_data(self, client, application_with_doc):
        """The document path always comes from the DB row for the given
        doc_kind -- there is no request field the client could substitute
        an arbitrary path into. Confirm extra client-supplied params are
        simply ignored and the DB-resident file is still what's served."""
        _login_platform_admin(client)
        app_id, _path = application_with_doc
        resp = client.get(
            f"/super_admin/applications/{app_id}/documents/registration_cert?path=/etc/passwd",
            data={"path": "/etc/passwd", "doc_registration_cert": "/etc/passwd"},
        )
        assert resp.status_code == 200
        assert b"fake certificate contents" in resp.data

    @pytest.mark.parametrize("bad_doc_kind", ["passport", "resume", "..", "registration_cert.."])
    def test_doc_kind_outside_whitelist_rejected(self, client, application_with_doc, bad_doc_kind):
        _login_platform_admin(client)
        app_id, _path = application_with_doc
        resp = client.get(f"/super_admin/applications/{app_id}/documents/{bad_doc_kind}",
                          follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid document type." in resp.data

    def test_doc_kind_with_path_separator_never_reaches_the_view(self, client, application_with_doc):
        """doc_kind containing a slash can't even match Flask's <doc_kind>
        route converter -- confirming this doesn't fall through to some
        other route that would treat it as a filesystem path."""
        _login_platform_admin(client)
        app_id, _path = application_with_doc
        resp = client.get(f"/super_admin/applications/{app_id}/documents/../../etc/passwd")
        assert resp.status_code == 404

    def test_document_not_uploaded_flashes_not_found(self, client, application_with_doc):
        _login_platform_admin(client)
        app_id, _path = application_with_doc
        # doc_address_proof was never set on this application row.
        resp = client.get(f"/super_admin/applications/{app_id}/documents/address_proof",
                          follow_redirects=True)
        assert resp.status_code == 200
        assert b"Document not found." in resp.data


class TestApplicationApproveRejectEdgeCases:
    """approve/reject happy paths are covered end-to-end in
    tests/test_org.py -- these only cover states that file doesn't."""

    def test_approve_unknown_application(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/applications/999999999/approve", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Application not found." in resp.data

    def test_approve_application_not_pending_review_rejected(self, client, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_applications "
            "(company_name, subdomain, admin_username, admin_email, admin_password_hash, access_token_hash, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,'rejected') RETURNING id",
            ("Already Rejected Co", "already-rejected-co", "ar_admin", "ar@test.local", "hash", "tok"),
        )
        app_id = cur.fetchone()[0]
        try:
            _login_platform_admin(client)
            resp = client.post(f"/super_admin/applications/{app_id}/approve", follow_redirects=True)
            assert resp.status_code == 200
            assert b"awaiting review" in resp.data
            cur.execute("SELECT status FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            assert cur.fetchone()[0] == "rejected"
        finally:
            cur.execute("DELETE FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            cur.close()

    def test_reject_unknown_application(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/applications/999999999/reject", data={"reason": "no"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Application not found." in resp.data


# ===========================================================================
# Duplicate alerts -- admin-facing list/acknowledge flow.
# (Creation-side coverage -- a blocked duplicate signup writing this row --
# lives in tests/test_org.py.)
# ===========================================================================

class TestDuplicateAlerts:
    @pytest.fixture
    def alert_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_duplicate_alerts "
            "(attempted_company_name, attempted_admin_email, conflicting_tenant_id, conflicting_company_name) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            ("Acme Impersonator", "impersonator@test.local", 999999, "Acme Real Co"),
        )
        alert_id = cur.fetchone()[0]
        yield alert_id
        cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE id=%s", (alert_id,))
        cur.close()

    def test_list_requires_login(self, client):
        resp = client.get("/super_admin/duplicate-alerts", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_list_shows_alert(self, client, alert_row):
        _login_platform_admin(client)
        resp = client.get("/super_admin/duplicate-alerts")
        assert resp.status_code == 200
        assert b"Acme Impersonator" in resp.data
        assert b"Acme Real Co" in resp.data

    def test_acknowledge_requires_login(self, client, alert_row):
        resp = client.post(f"/super_admin/duplicate-alerts/{alert_row}/acknowledge", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_acknowledge_marks_alert(self, client, db_engine, alert_row):
        _login_platform_admin(client, username="ack_platform_admin")
        resp = client.post(f"/super_admin/duplicate-alerts/{alert_row}/acknowledge", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/duplicate-alerts"

        cur = db_engine.cursor()
        cur.execute(
            "SELECT acknowledged, acknowledged_by FROM att_master.tenant_duplicate_alerts WHERE id=%s",
            (alert_row,),
        )
        row = cur.fetchone()
        assert row[0] == 1
        assert row[1] == "ack_platform_admin"
        cur.close()


class TestBulkAcknowledgeDuplicateAlerts:
    @pytest.fixture
    def two_alert_rows(self, db_engine):
        cur = db_engine.cursor()
        ids = []
        for i in range(2):
            cur.execute(
                "INSERT INTO att_master.tenant_duplicate_alerts "
                "(attempted_company_name, attempted_admin_email, conflicting_tenant_id, conflicting_company_name) "
                "VALUES (%s,%s,%s,%s) RETURNING id",
                (f"Bulk Ack Impersonator {i}", f"bulk-ack-{i}@test.local", 999999, "Bulk Ack Real Co"),
            )
            ids.append(cur.fetchone()[0])
        yield ids
        cur.execute("DELETE FROM att_master.tenant_duplicate_alerts WHERE id=ANY(%s)", (ids,))
        cur.close()

    def test_requires_login(self, client, two_alert_rows):
        resp = client.post("/super_admin/duplicate-alerts/bulk-acknowledge",
                           data={"alert_ids": [str(i) for i in two_alert_rows]}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_bulk_acknowledge_marks_all_selected(self, client, db_engine, two_alert_rows):
        _login_platform_admin(client, username="bulk_ack_admin")
        resp = client.post("/super_admin/duplicate-alerts/bulk-acknowledge",
                           data={"alert_ids": [str(i) for i in two_alert_rows]}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"2 alert(s) acknowledged." in resp.data
        cur = db_engine.cursor()
        cur.execute(
            "SELECT acknowledged, acknowledged_by FROM att_master.tenant_duplicate_alerts WHERE id=ANY(%s)",
            (two_alert_rows,),
        )
        rows = cur.fetchall()
        assert all(r[0] == 1 for r in rows)
        assert all(r[1] == "bulk_ack_admin" for r in rows)
        cur.close()

    def test_no_selection_rejected(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/duplicate-alerts/bulk-acknowledge", data={}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"No alerts were selected." in resp.data


# ===========================================================================
# Support chat with a tenant (att_master.chat_messages)
# ===========================================================================

class TestChat:
    @pytest.fixture
    def tenant_row(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ("Chat Test Co", "chat-test-co", "att_chat_test_co"),
        )
        tenant_id = cur.fetchone()[0]
        yield tenant_id
        cur.execute("DELETE FROM att_master.chat_messages WHERE tenant_schema='att_chat_test_co'")
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
        cur.close()

    def test_messages_requires_login(self, client, tenant_row):
        resp = client.get(f"/super_admin/chat/{tenant_row}/messages", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_messages_unknown_tenant_404s(self, client):
        _login_platform_admin(client)
        resp = client.get("/super_admin/chat/999999999/messages")
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_send_unknown_tenant_404s(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/chat/999999999/send", json={"message": "hi"})
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_send_and_list_messages(self, client, tenant_row):
        _login_platform_admin(client, username="chat_platform_admin")
        send_resp = client.post(f"/super_admin/chat/{tenant_row}/send", json={"message": "Hello from support"})
        assert send_resp.status_code == 200
        assert send_resp.get_json()["ok"] is True

        list_resp = client.get(f"/super_admin/chat/{tenant_row}/messages")
        assert list_resp.status_code == 200
        payload = list_resp.get_json()
        assert payload["ok"] is True
        assert payload["company_name"] == "Chat Test Co"
        assert any(m["message"] == "Hello from support" and m["sender_kind"] == "platform_admin"
                   for m in payload["messages"])

    def test_send_empty_message_rejected(self, client, tenant_row):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/chat/{tenant_row}/send", json={"message": "   "})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


# ===========================================================================
# Audit log (browsable view over security_events, scoped to
# platform-admin-relevant event_type prefixes -- see
# blueprints/platform_admin.py's _AUDIT_LOG_PREFIXES)
# ===========================================================================

class TestAuditLog:
    @pytest.fixture
    def audit_events(self, db_engine):
        """Writes directly to security_events rather than going through
        log_security_event()'s async background writer (see
        tests/test_security_events_log.py's docstring) -- these tests only
        care about the audit-log route's filtering/rendering, not the
        writer itself, so a synchronous INSERT keeps them fast and
        deterministic. INSERT isn't blocked by the append-only trigger
        (only UPDATE/DELETE are), so no bypass is needed to create rows --
        only to clean them up."""
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO security_events (event_type, level, message, identifier, ip) VALUES "
            "('platform_admin.rate_updated', 'WARNING', 'Per-employee rate changed from X to Y', 'AUDIT_TEST_ADMIN', NULL),"
            "('platform_admin.tenant_created', 'INFO', 'Platform admin created tenant Audit Test Co', 'AUDIT_TEST_ADMIN', NULL),"
            "('auth.admin_login_success', 'INFO', 'Should never appear -- not a platform-admin-scoped prefix', 'AUDIT_TEST_ADMIN', NULL),"
            "('secops.honeypot_triggered', 'CRITICAL', 'Deception honeypot trap ''/.env'' triggered by automated scanner bot', NULL, '203.0.113.77')"
        )
        db_engine.commit()
        yield
        cur.execute("SET audit.bypass = 'on'")
        cur.execute(
            "DELETE FROM security_events WHERE identifier='AUDIT_TEST_ADMIN' "
            "OR (event_type='secops.honeypot_triggered' AND ip='203.0.113.77')"
        )
        cur.execute("SET audit.bypass = 'off'")
        db_engine.commit()
        cur.close()

    def test_requires_login(self, client):
        resp = client.get("/super_admin/audit-log", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_shows_platform_admin_scoped_events_only(self, client, audit_events):
        _login_platform_admin(client)
        resp = client.get("/super_admin/audit-log")
        assert resp.status_code == 200
        assert b"Per-employee rate changed from X to Y" in resp.data
        assert b"Platform admin created tenant Audit Test Co" in resp.data
        assert b"Should never appear" not in resp.data

    def test_search_filters_by_message(self, client, audit_events):
        _login_platform_admin(client)
        resp = client.get("/super_admin/audit-log?q=Audit+Test+Co")
        assert resp.status_code == 200
        assert b"Platform admin created tenant Audit Test Co" in resp.data
        assert b"Per-employee rate changed" not in resp.data

    def test_level_filter(self, client, audit_events):
        _login_platform_admin(client)
        resp = client.get("/super_admin/audit-log?level=WARNING")
        assert resp.status_code == 200
        assert b"Per-employee rate changed from X to Y" in resp.data
        assert b"Platform admin created tenant Audit Test Co" not in resp.data

    def test_shows_honeypot_trap_hits_with_source_ip(self, client, audit_events):
        """secops.* (blueprints/honeypot_routes.py's deception-honeypot
        trap) must be audit-log-visible -- previously excluded entirely,
        since 'secops.' wasn't in _AUDIT_LOG_PREFIXES, so a scanner bot
        tripping a decoy route left no trace anywhere a platform admin
        would ever look."""
        _login_platform_admin(client)
        resp = client.get("/super_admin/audit-log")
        assert resp.status_code == 200
        assert b"Deception honeypot trap" in resp.data
        assert b"203.0.113.77" in resp.data

    def test_critical_level_filter_shows_only_honeypot_hit(self, client, audit_events):
        _login_platform_admin(client)
        resp = client.get("/super_admin/audit-log?level=CRITICAL")
        assert resp.status_code == 200
        assert b"Deception honeypot trap" in resp.data
        assert b"Per-employee rate changed from X to Y" not in resp.data

    def test_out_of_range_page_clamps_instead_of_erroring(self, client, audit_events):
        _login_platform_admin(client)
        resp = client.get("/super_admin/audit-log?page=99999")
        assert resp.status_code == 200


# ===========================================================================
# Mini-CRM: company profile (financial history + contacts + notes + tickets)
# ===========================================================================

class TestCompanyProfile:
    @pytest.fixture
    def crm_tenant(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status, admin_email) "
            "VALUES (%s,%s,%s,'active',%s) RETURNING id",
            ("CRM Test Co", "crm-test-co", "att_crm_test_co", "admin@crmtestco.local"),
        )
        tenant_id = cur.fetchone()[0]
        yield tenant_id
        cur.execute("DELETE FROM att_master.tenant_notes WHERE tenant_id=%s", (tenant_id,))
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
        cur.close()

    def test_requires_login(self, client, crm_tenant):
        resp = client.get(f"/super_admin/companies/{crm_tenant}", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_unknown_company_flashes_not_found(self, client):
        _login_platform_admin(client)
        resp = client.get("/super_admin/companies/999999999", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Company not found." in resp.data

    def test_profile_shows_company_details(self, client, crm_tenant):
        _login_platform_admin(client)
        resp = client.get(f"/super_admin/companies/{crm_tenant}")
        assert resp.status_code == 200
        assert b"CRM Test Co" in resp.data
        assert b"admin@crmtestco.local" in resp.data

    def test_profile_shows_only_this_companys_payments(self, client, db_engine, crm_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id) "
            "VALUES (%s,'starter',5,4950,'CRM Test Co','crm-test-co','crm_admin','a@crmtestco.local','paid',%s)",
            (f"demo_order_crm_profile_{crm_tenant}", crm_tenant),
        )
        cur.execute(
            "INSERT INTO att_master.payment_orders "
            "(razorpay_order_id, plan, employee_count, amount_paise, company_name, subdomain, "
            "admin_username, admin_email, status, tenant_id) "
            "VALUES (%s,'starter',5,4950,'Some Other Co','some-other-co','x','x@test.local','paid',%s)",
            (f"demo_order_crm_other_{crm_tenant}", crm_tenant + 1000000),
        )
        db_engine.commit()
        try:
            _login_platform_admin(client)
            resp = client.get(f"/super_admin/companies/{crm_tenant}")
            assert resp.status_code == 200
            assert b"CRM Test Co" in resp.data
            assert b"Some Other Co" not in resp.data
        finally:
            cur.execute("DELETE FROM att_master.payment_orders WHERE tenant_id IN (%s, %s)",
                        (crm_tenant, crm_tenant + 1000000))
            cur.close()


class TestTenantNotes:
    @pytest.fixture
    def crm_tenant(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ("Notes Test Co", "notes-test-co", "att_notes_test_co"),
        )
        tenant_id = cur.fetchone()[0]
        yield tenant_id
        cur.execute("DELETE FROM att_master.tenant_notes WHERE tenant_id=%s", (tenant_id,))
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
        cur.close()

    def test_requires_login(self, client, crm_tenant):
        resp = client.post(f"/super_admin/companies/{crm_tenant}/notes", data={"note": "hi"},
                           follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_add_note(self, client, db_engine, crm_tenant):
        _login_platform_admin(client, username="notes_admin")
        resp = client.post(f"/super_admin/companies/{crm_tenant}/notes",
                           data={"note": "Called about their overdue invoice."}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Note added." in resp.data
        assert b"Called about their overdue invoice." in resp.data
        assert b"notes_admin" in resp.data

        cur = db_engine.cursor()
        cur.execute("SELECT author, note FROM att_master.tenant_notes WHERE tenant_id=%s", (crm_tenant,))
        row = cur.fetchone()
        assert row[0] == "notes_admin"
        assert row[1] == "Called about their overdue invoice."
        cur.close()

    def test_empty_note_rejected(self, client, crm_tenant):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/companies/{crm_tenant}/notes", data={"note": "   "},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Note cannot be empty." in resp.data

    def test_unknown_company_rejected(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/companies/999999999/notes", data={"note": "hi"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Company not found." in resp.data


# ===========================================================================
# Mini-CRM: support ticket queue
# ===========================================================================

class TestSupportTickets:
    @pytest.fixture
    def ticket_tenant(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
            "VALUES (%s,%s,%s,'active') RETURNING id",
            ("Ticket Test Co", "ticket-test-co", "att_ticket_test_co"),
        )
        tenant_id = cur.fetchone()[0]
        yield tenant_id
        cur.execute("DELETE FROM att_master.tenant_ticket_comments WHERE ticket_id IN "
                    "(SELECT id FROM att_master.tenant_support_tickets WHERE tenant_id=%s)", (tenant_id,))
        cur.execute("DELETE FROM att_master.tenant_support_tickets WHERE tenant_id=%s", (tenant_id,))
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tenant_id,))
        cur.close()

    @pytest.fixture
    def open_ticket(self, db_engine, ticket_tenant):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenant_support_tickets (tenant_id, subject, description, priority, created_by) "
            "VALUES (%s,%s,%s,'high','fixture_admin') RETURNING id",
            (ticket_tenant, "Payroll export is broken", "CSV export times out for this company."),
        )
        ticket_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()
        return ticket_id

    def test_create_ticket_requires_login(self, client, ticket_tenant):
        resp = client.post(f"/super_admin/companies/{ticket_tenant}/tickets",
                           data={"subject": "x", "description": "y"}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_create_ticket(self, client, db_engine, ticket_tenant):
        _login_platform_admin(client, username="ticket_creator_admin")
        resp = client.post(
            f"/super_admin/companies/{ticket_tenant}/tickets",
            data={"subject": "Login not working", "description": "Admin can't sign in since yesterday.",
                  "priority": "urgent"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Ticket created." in resp.data
        assert b"Login not working" in resp.data

        cur = db_engine.cursor()
        cur.execute(
            "SELECT subject, description, status, priority, created_by FROM att_master.tenant_support_tickets "
            "WHERE tenant_id=%s", (ticket_tenant,)
        )
        row = cur.fetchone()
        assert row[0] == "Login not working"
        assert row[2] == "open"
        assert row[3] == "urgent"
        assert row[4] == "ticket_creator_admin"
        cur.close()

    def test_create_ticket_missing_fields_rejected(self, client, ticket_tenant):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/companies/{ticket_tenant}/tickets",
                           data={"subject": "", "description": ""}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Subject and description are required." in resp.data

    def test_create_ticket_invalid_priority_falls_back_to_normal(self, client, db_engine, ticket_tenant):
        _login_platform_admin(client)
        resp = client.post(
            f"/super_admin/companies/{ticket_tenant}/tickets",
            data={"subject": "Test", "description": "Test desc", "priority": "not-a-real-priority"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT priority FROM att_master.tenant_support_tickets WHERE tenant_id=%s", (ticket_tenant,))
        assert cur.fetchone()[0] == "normal"
        cur.close()

    def test_queue_requires_login(self, client):
        resp = client.get("/super_admin/tickets", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_queue_default_filter_shows_open_ticket(self, client, open_ticket):
        _login_platform_admin(client)
        resp = client.get("/super_admin/tickets")
        assert resp.status_code == 200
        assert b"Payroll export is broken" in resp.data
        assert b"Ticket Test Co" in resp.data

    def test_queue_status_filter_excludes_non_matching(self, client, open_ticket):
        _login_platform_admin(client)
        resp = client.get("/super_admin/tickets?status=resolved")
        assert resp.status_code == 200
        assert b"Payroll export is broken" not in resp.data

    def test_dashboard_shows_open_ticket_count(self, client, open_ticket):
        _login_platform_admin(client)
        resp = client.get("/super_admin")
        assert resp.status_code == 200
        m = re.search(rb'Support Tickets\s*<span[^>]*>(\d+)</span>', resp.data)
        assert m, "open-tickets badge not found on dashboard"
        assert int(m.group(1)) >= 1

    def test_detail_requires_login(self, client, open_ticket):
        resp = client.get(f"/super_admin/tickets/{open_ticket}", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_detail_shows_ticket(self, client, open_ticket):
        _login_platform_admin(client)
        resp = client.get(f"/super_admin/tickets/{open_ticket}")
        assert resp.status_code == 200
        assert b"Payroll export is broken" in resp.data
        assert b"CSV export times out for this company." in resp.data

    def test_detail_unknown_ticket_flashes_and_redirects(self, client):
        _login_platform_admin(client)
        resp = client.get("/super_admin/tickets/999999999", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Ticket not found." in resp.data

    def test_update_status_and_priority(self, client, db_engine, open_ticket):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tickets/{open_ticket}/update",
                           data={"status": "in_progress", "priority": "urgent"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Ticket updated." in resp.data

        cur = db_engine.cursor()
        cur.execute("SELECT status, priority, resolved_at FROM att_master.tenant_support_tickets WHERE id=%s",
                    (open_ticket,))
        row = cur.fetchone()
        assert row[0] == "in_progress"
        assert row[1] == "urgent"
        assert row[2] is None
        cur.close()

    def test_resolving_a_ticket_stamps_resolved_at(self, client, db_engine, open_ticket):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tickets/{open_ticket}/update",
                           data={"status": "resolved", "priority": "high"}, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT status, resolved_at FROM att_master.tenant_support_tickets WHERE id=%s", (open_ticket,))
        row = cur.fetchone()
        assert row[0] == "resolved"
        assert row[1] is not None
        cur.close()

    def test_reopening_a_resolved_ticket_clears_resolved_at(self, client, db_engine, open_ticket):
        _login_platform_admin(client)
        client.post(f"/super_admin/tickets/{open_ticket}/update", data={"status": "resolved", "priority": "high"})
        resp = client.post(f"/super_admin/tickets/{open_ticket}/update",
                           data={"status": "open", "priority": "high"}, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT resolved_at FROM att_master.tenant_support_tickets WHERE id=%s", (open_ticket,))
        assert cur.fetchone()[0] is None
        cur.close()

    def test_update_invalid_status_rejected(self, client, open_ticket):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tickets/{open_ticket}/update",
                           data={"status": "not-a-status", "priority": "high"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid status or priority." in resp.data

    def test_update_unknown_ticket_flashes_not_found(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/tickets/999999999/update",
                           data={"status": "open", "priority": "high"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Ticket not found." in resp.data

    def test_add_comment(self, client, db_engine, open_ticket):
        _login_platform_admin(client, username="comment_admin")
        resp = client.post(f"/super_admin/tickets/{open_ticket}/comment",
                           data={"comment": "Escalated to engineering."}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Comment added." in resp.data
        assert b"Escalated to engineering." in resp.data
        assert b"comment_admin" in resp.data

        cur = db_engine.cursor()
        cur.execute("SELECT author, comment FROM att_master.tenant_ticket_comments WHERE ticket_id=%s",
                    (open_ticket,))
        row = cur.fetchone()
        assert row[0] == "comment_admin"
        assert row[1] == "Escalated to engineering."
        cur.close()

    def test_empty_comment_rejected(self, client, open_ticket):
        _login_platform_admin(client)
        resp = client.post(f"/super_admin/tickets/{open_ticket}/comment", data={"comment": "  "},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Comment cannot be empty." in resp.data

    def test_comment_on_unknown_ticket_rejected(self, client):
        _login_platform_admin(client)
        resp = client.post("/super_admin/tickets/999999999/comment", data={"comment": "hi"},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b"Ticket not found." in resp.data
