"""
Path-based tenant resolution + cross-tenant session isolation.

Tenants are now identified by URL path (www.hrzest.com/<company-slug>/...)
via utils/tenant_routing.py's WSGI middleware, not by subdomain. Subdomains
used to make cross-tenant session leakage impossible for free (a host-only
cookie for acme.hrzest.com is never sent to beta.hrzest.com by the browser
itself); now every tenant shares one hostname, so app.py's _resolve_tenant()
has to enforce that binding itself -- session["tenant_slug"] must match the
slug in the current URL, or the session is hard-cleared. This file is the
dedicated regression coverage for that mechanism, per the migration plan.

Provisions two real tenant schemas (same heavy-but-necessary pattern as
tests/test_org.py's TestSuccessfulProvisioning), with explicit DROP SCHEMA
cleanup after each test.
"""
import pytest

ADMIN_A = "tpi_admin_a@test.local"   # company A's admin; signup makes the email the username


def _drop_schema(db_engine, schema_name):
    cur = db_engine.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (schema_name,))
    cur.close()


def _provision(client, monkeypatch, subdomain, admin_email):
    """Walks the whole signup (tests/signup_flow.py: company name and
    account, email code, then the dashboard's company details and
    documents, then platform-admin approval) so the fixture ends with a
    real, live tenant schema whose admin signs in with admin_email. The
    company is named after its URL, which is made from the name."""
    from tests.signup_flow import signup_company
    application_id = signup_company(client, monkeypatch, admin_email, subdomain)

    with client.session_transaction() as sess:
        sess["platform_admin_logged_in"] = True
        sess["platform_admin_username"] = "tpi_platform_admin"
        import time as _time
        sess["platform_admin_last_activity"] = _time.time()
    resp = client.post(f"/super_admin/applications/{application_id}/approve", follow_redirects=False)
    assert resp.status_code in (301, 302), resp.data

    return "att_" + subdomain.replace("-", "_")


@pytest.fixture
def two_tenants(client, db_engine, monkeypatch):
    from app import init_master_db
    init_master_db()

    slug_a, slug_b = "tpi-tenant-a", "tpi-tenant-b"
    schema_a = "att_" + slug_a.replace("-", "_")
    schema_b = "att_" + slug_b.replace("-", "_")
    _drop_schema(db_engine, schema_a)
    _drop_schema(db_engine, schema_b)
    try:
        _provision(client, monkeypatch, slug_a, ADMIN_A)
        _provision(client, monkeypatch, slug_b, "tpi_admin_b@test.local")
        # Provisioning (via the platform-admin approve step) resolves
        # g.tenant_db/session["tenant_db"] and sets platform_admin_* keys as
        # a side effect -- clear the session so the isolation tests below
        # start from a clean, logged-out slate rather than accidentally
        # inheriting either.
        with client.session_transaction() as sess:
            sess.clear()
        yield slug_a, schema_a, slug_b, schema_b
    finally:
        _drop_schema(db_engine, schema_a)
        _drop_schema(db_engine, schema_b)


class TestPathBasedLogin:
    def test_login_under_company_slug_sets_tenant_session(self, client, two_tenants):
        slug_a, schema_a, _, _ = two_tenants
        resp = client.post(f"/{slug_a}/login", data={
            "identifier": ADMIN_A, "password": "password123",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith(f"/{slug_a}/")
        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") is True
            assert sess.get("tenant_db") == schema_a
            assert sess.get("tenant_slug") == slug_a

    def test_unknown_slug_falls_through_unresolved(self, client, two_tenants):
        # A path segment that isn't a real tenant is just... not a tenant.
        # No WSGI strip happens, so this 404s (or otherwise fails) like any
        # other nonexistent route -- it must not be silently treated as a
        # valid company.
        resp = client.get("/not-a-real-company-xyz/admin", follow_redirects=False)
        assert resp.status_code in (404, 302)
        if resp.status_code == 302:
            # If it redirects (e.g. to a login page), it must not be
            # anyone's actual tenant dashboard.
            assert "not-a-real-company-xyz" not in resp.headers.get("Location", "")


class TestCrossTenantSessionIsolation:
    def test_switching_company_url_hard_clears_session(self, client, two_tenants):
        """The core security property: a session bound to company A must
        not remain usable once the browser is on company B's URL."""
        slug_a, schema_a, slug_b, schema_b = two_tenants

        client.post(f"/{slug_a}/login", data={
            "identifier": ADMIN_A, "password": "password123",
        }, follow_redirects=False)
        with client.session_transaction() as sess:
            assert sess.get("tenant_slug") == slug_a  # sanity check

        # Same browser/cookie jar, now requests company B's dashboard.
        resp = client.get(f"/{slug_b}/admin", follow_redirects=False)

        # Must NOT serve company A's admin dashboard under B's URL.
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith(f"/{slug_b}/")

        # The stale A-bound session must be gone entirely, not just
        # "not admin_logged_in for B" -- a hard clear, per product decision.
        with client.session_transaction() as sess:
            assert not sess.get("admin_logged_in")
            assert sess.get("tenant_db") != schema_a
            assert sess.get("tenant_slug") != slug_a

    def test_matching_slug_does_not_clear_session(self, client, two_tenants):
        """Sanity check for the mismatch logic itself: repeated requests to
        the SAME company's URL must never trigger a false-positive clear."""
        slug_a, schema_a, _, _ = two_tenants
        client.post(f"/{slug_a}/login", data={
            "identifier": ADMIN_A, "password": "password123",
        }, follow_redirects=False)

        resp = client.get(f"/{slug_a}/admin", follow_redirects=False)
        assert resp.status_code == 200

        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") is True
            assert sess.get("tenant_slug") == slug_a

    def test_global_route_does_not_clear_tenant_session(self, client, two_tenants):
        """Requests that carry no company slug at all (marketing pages,
        health checks, token-based API calls) must not be treated as a
        mismatch -- only an actual DIFFERENT company's URL should clear."""
        slug_a, _, _, _ = two_tenants
        client.post(f"/{slug_a}/login", data={
            "identifier": ADMIN_A, "password": "password123",
        }, follow_redirects=False)

        resp = client.get("/healthz", follow_redirects=False)
        assert resp.status_code == 200

        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") is True
            assert sess.get("tenant_slug") == slug_a


class TestBareLoginKeepsSessionCompany:
    """A browser that visited /<slug>/ and then opens the bare /login gets
    that company's login page (the session binding resolves it). The login
    clears the session, and the company must survive that even though the
    URL carries no slug to re-stamp it from -- production saw every MFA
    code rejected here: issued in the company's schema, checked in the
    default one."""

    def test_mfa_code_from_bare_login_verifies(self, client, two_tenants, monkeypatch):
        import blueprints.auth as auth_module
        slug_a, schema_a, _, _ = two_tenants
        codes = []
        monkeypatch.setattr(auth_module, "send_mfa_login_email",
                            lambda to, user, role, secret, code: codes.append(code))
        monkeypatch.setitem(client.application.config, "MANDATORY_LOGIN_MFA", True)

        client.get(f"/{slug_a}/login")
        resp = client.post("/login", data={
            "identifier": ADMIN_A, "password": "password123",
        }, follow_redirects=False)
        assert resp.headers["Location"] == "/mfa_verify"

        resp = client.post("/mfa_verify", data={"otp_code": codes[-1]}, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/admin"
        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") is True
            assert sess.get("tenant_db") == schema_a
        assert client.get("/admin").status_code == 200

    def test_login_without_mfa_stays_in_session_company(self, client, two_tenants):
        slug_a, schema_a, _, _ = two_tenants
        client.get(f"/{slug_a}/login")
        resp = client.post("/login", data={
            "identifier": ADMIN_A, "password": "password123",
        }, follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in") is True
            assert sess.get("tenant_db") == schema_a
            assert sess.get("tenant_slug") == slug_a


class TestFormFieldCannotSelectTenant:
    """Defense-in-depth regression coverage: a POST body field that happens
    to look like a tenant/company identifier (company_id) must never be able
    to select a *different* tenant's schema than the one already bound by
    the session/URL slug. g.tenant_db is resolved once per request by
    app.py's _resolve_tenant() from the URL slug alone (see module docstring
    above); nothing downstream re-derives it from request.form. This test
    proves that property holds for a real handler (set_company_pin) rather
    than just asserting it by reading the code."""

    def test_company_id_form_field_cannot_write_into_other_tenant_schema(
        self, client, db_engine, two_tenants
    ):
        slug_a, schema_a, slug_b, schema_b = two_tenants

        # A company row that lives only in tenant B's schema.
        cur = db_engine.cursor()
        cur.execute(f'SET search_path TO "{schema_b}", public')
        cur.execute("INSERT INTO companies (name, code) VALUES (%s, %s) RETURNING id",
                    ("Tenant B Co", "TBC"))
        company_id_in_b = cur.fetchone()[0]
        cur.execute("SET search_path TO public")
        cur.close()

        # Log in as tenant A's admin -- session/tenant_db is now bound to
        # schema_a, exactly like every other request in this file.
        client.post(f"/{slug_a}/login", data={
            "identifier": ADMIN_A, "password": "password123",
        }, follow_redirects=False)
        with client.session_transaction() as sess:
            assert sess.get("tenant_db") == schema_a  # sanity check

        # Try to set a PIN on tenant B's company by id, from tenant A's
        # session. If g.tenant_db were ever derived from this form field
        # instead of the session, this write would land in schema_b.
        resp = client.post("/set_company_pin", data={
            "company_id": str(company_id_in_b), "pin": "9999",
        }, follow_redirects=False)
        assert resp.status_code == 302

        # Tenant B's row must be untouched -- the request executed (if at
        # all) against schema_a, where this id doesn't correspond to any
        # row tenant B owns.
        cur = db_engine.cursor()
        cur.execute(f'SET search_path TO "{schema_b}", public')
        cur.execute("SELECT pin FROM companies WHERE id=%s", (company_id_in_b,))
        row = cur.fetchone()
        cur.execute("SET search_path TO public")
        cur.close()
        assert row is not None
        assert row[0] is None, "cross-tenant company_id form field wrote into another tenant's schema"
