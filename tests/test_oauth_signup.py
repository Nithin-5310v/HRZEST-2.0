"""
OAuth signup/login tests -- utils/oauth_client.py + blueprints/oauth_signup.py.

Network calls to Google/Microsoft are never made in this suite: oauth_client's
_post_form()/_get_json() (its only two functions that touch urllib) are
monkeypatched directly, and blueprint-level tests monkeypatch
blueprints.oauth_signup's own `complete_oauth` reference (module-level
imports bind their own name -- patching utils.oauth_client.complete_oauth
would not affect the copy blueprints/oauth_signup.py already imported).

Run with:
    python -m pytest tests/test_oauth_signup.py -v
"""
import pytest

from extensions import app as flask_app
import utils.oauth_client as oauth_client
import blueprints.oauth_signup as oauth_signup


def _drop_schema(db_engine, schema_name):
    cur = db_engine.cursor()
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
    cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (schema_name,))
    db_engine.commit()
    cur.close()


@pytest.fixture
def google_creds(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")


# ===========================================================================
# utils/oauth_client.py -- state store + PKCE + complete_oauth()
# ===========================================================================

class TestStateStore:
    def test_save_and_consume_round_trip(self, db_engine):
        oauth_client._save_state("state-abc-123", "google", "signup", "verifier-xyz", None)
        result = oauth_client._consume_state("state-abc-123", "google")
        assert result == ("verifier-xyz", "signup", None)

    def test_consume_is_single_use(self, db_engine):
        oauth_client._save_state("state-once", "google", "signup", "v", None)
        assert oauth_client._consume_state("state-once", "google") is not None
        assert oauth_client._consume_state("state-once", "google") is None

    def test_unknown_state_returns_none(self, db_engine):
        assert oauth_client._consume_state("never-existed", "google") is None

    def test_wrong_provider_does_not_match(self, db_engine):
        oauth_client._save_state("state-provider-mix", "google", "signup", "v", None)
        assert oauth_client._consume_state("state-provider-mix", "microsoft") is None
        # Row is still there for the correct provider (DELETE...WHERE provider=%s
        # only removed it if the provider matched).
        assert oauth_client._consume_state("state-provider-mix", "google") is not None

    def test_login_mode_carries_tenant_db(self, db_engine):
        oauth_client._save_state("state-login", "microsoft", "login", "v", "att_acme")
        assert oauth_client._consume_state("state-login", "microsoft") == ("v", "login", "att_acme")


class TestBuildAuthorizeUrl:
    def test_raises_when_not_configured(self, db_engine, monkeypatch):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        with pytest.raises(RuntimeError):
            oauth_client.build_authorize_url("google", "https://example.test/cb", mode="signup")

    def test_url_has_pkce_and_state_and_persists_row(self, db_engine, google_creds):
        url = oauth_client.build_authorize_url("google", "https://example.test/cb", mode="signup")
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "state=" in url
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = qs["state"][0]
        # The row this call must have written is retrievable (and single-use).
        consumed = oauth_client._consume_state(state, "google")
        assert consumed is not None
        code_verifier, mode, tenant_db = consumed
        assert mode == "signup"
        assert tenant_db is None
        assert len(code_verifier) > 20

    def test_microsoft_tenant_defaults_to_common(self, db_engine, monkeypatch):
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "id")
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET", "secret")
        monkeypatch.delenv("MS_OAUTH_TENANT", raising=False)
        url = oauth_client.build_authorize_url("microsoft", "https://example.test/cb", mode="signup")
        assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")


class TestCompleteOAuth:
    def _prep_state(self, mode="signup", tenant_db=None):
        oauth_client._save_state("state-complete", "google", mode, "verifier-abc", tenant_db)

    def test_happy_path(self, db_engine, google_creds, monkeypatch):
        self._prep_state()
        monkeypatch.setattr(oauth_client, "_post_form", lambda url, fields: {"access_token": "tok-123"})
        monkeypatch.setattr(
            oauth_client, "_get_json",
            lambda url, token: {"sub": "g-sub-1", "email": "Person@Acme.com", "email_verified": True, "name": "Person"},
        )
        identity = oauth_client.complete_oauth("google", "auth-code", "state-complete", "https://example.test/cb")
        assert identity == {
            "sub": "g-sub-1", "email": "person@acme.com", "name": "Person",
            "provider": "google", "mode": "signup", "tenant_db": None,
        }

    def test_unknown_state_raises(self, db_engine, google_creds):
        with pytest.raises(oauth_client.OAuthError):
            oauth_client.complete_oauth("google", "code", "no-such-state", "https://example.test/cb")

    def test_missing_access_token_raises(self, db_engine, google_creds, monkeypatch):
        self._prep_state()
        monkeypatch.setattr(oauth_client, "_post_form", lambda url, fields: {})
        with pytest.raises(oauth_client.OAuthError):
            oauth_client.complete_oauth("google", "code", "state-complete", "https://example.test/cb")

    def test_missing_email_raises(self, db_engine, google_creds, monkeypatch):
        self._prep_state()
        monkeypatch.setattr(oauth_client, "_post_form", lambda url, fields: {"access_token": "t"})
        monkeypatch.setattr(oauth_client, "_get_json", lambda url, token: {"sub": "s1"})
        with pytest.raises(oauth_client.OAuthError):
            oauth_client.complete_oauth("google", "code", "state-complete", "https://example.test/cb")

    def test_unverified_google_email_raises(self, db_engine, google_creds, monkeypatch):
        self._prep_state()
        monkeypatch.setattr(oauth_client, "_post_form", lambda url, fields: {"access_token": "t"})
        monkeypatch.setattr(
            oauth_client, "_get_json",
            lambda url, token: {"sub": "s1", "email": "a@b.com", "email_verified": False},
        )
        with pytest.raises(oauth_client.OAuthError):
            oauth_client.complete_oauth("google", "code", "state-complete", "https://example.test/cb")

    def test_microsoft_missing_email_verified_claim_is_treated_as_verified(self, db_engine, monkeypatch):
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "id")
        monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET", "secret")
        oauth_client._save_state("state-ms", "microsoft", "signup", "v", None)
        monkeypatch.setattr(oauth_client, "_post_form", lambda url, fields: {"access_token": "t"})
        monkeypatch.setattr(
            oauth_client, "_get_json",
            lambda url, token: {"sub": "ms-1", "email": "person@work.com", "name": "Person"},  # no email_verified key
        )
        identity = oauth_client.complete_oauth("microsoft", "code", "state-ms", "https://example.test/cb")
        assert identity["email"] == "person@work.com"


# ===========================================================================
# blueprints/oauth_signup.py -- route-level, complete_oauth() mocked out
# ===========================================================================

def _start_signup(client, monkeypatch, sub, email, name):
    """Returns from the provider for `email` and lands on the emailed-code
    step. Returns (pending_id, token, emailed_code)."""
    import urllib.parse
    import blueprints.org as org_module
    sent = {}
    monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                        lambda to, company, otp: sent.update(to=to, otp=otp) or True)
    monkeypatch.setattr(
        oauth_signup, "complete_oauth",
        lambda *a, **k: {"sub": sub, "email": email, "name": name,
                         "provider": "google", "mode": "signup", "tenant_db": None},
    )
    resp = client.get("/create_org/google/callback?code=abc&state=xyz", follow_redirects=False)
    assert resp.status_code in (301, 302)
    location = resp.headers["Location"]
    assert location.startswith("/create_org/oauth/verify?")
    assert sent.get("to") == email, "the verification code must be emailed to the provider's address"
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    return qs["id"][0], qs["token"][0], sent["otp"]


def _submit_code(client, pending_id, token, otp_code):
    return client.post("/create_org/oauth/verify", data={
        "application_id": pending_id, "token": token, "otp_code": otp_code,
    }, follow_redirects=False)


def _tenant_count(db_engine, email):
    cur = db_engine.cursor()
    cur.execute("SELECT COUNT(*) FROM att_master.tenants WHERE admin_email=%s", (email,))
    count = cur.fetchone()[0]
    cur.close()
    return count


class TestSignupEmailVerification:
    """Signing up with Google/Microsoft never creates the company before the
    code emailed to that address is entered."""

    EMAIL = "admin@otpgatedco.com"

    def _drop_created(self, db_engine):
        cur = db_engine.cursor()
        cur.execute("SELECT db_name FROM att_master.tenants WHERE admin_email=%s", (self.EMAIL,))
        row = cur.fetchone()
        cur.close()
        if row:
            _drop_schema(db_engine, row[0])

    def test_callback_emails_a_code_and_creates_nothing_yet(self, client, db_engine, monkeypatch):
        pending_id, token, otp = _start_signup(client, monkeypatch, "g-otp-1", self.EMAIL, "Admin")
        assert len(otp) == 6
        assert _tenant_count(db_engine, self.EMAIL) == 0
        with client.session_transaction() as sess:
            assert not sess.get("admin_logged_in")
        page = client.get(f"/create_org/oauth/verify?id={pending_id}&token={token}")
        assert page.status_code == 200
        assert self.EMAIL.encode() in page.data

    def test_wrong_or_blank_code_is_refused(self, client, db_engine, monkeypatch):
        pending_id, token, _otp = _start_signup(client, monkeypatch, "g-otp-2", self.EMAIL, "Admin")
        for code in ("", "000000"):   # real codes are 100000-999999
            resp = _submit_code(client, pending_id, token, code)
            assert resp.headers["Location"] == f"/create_org/oauth/verify?id={pending_id}&token={token}"
        assert _tenant_count(db_engine, self.EMAIL) == 0

    def test_too_many_wrong_codes_lock_out_the_right_one(self, client, db_engine, monkeypatch):
        pending_id, token, otp = _start_signup(client, monkeypatch, "g-otp-3", self.EMAIL, "Admin")
        for _ in range(5):
            _submit_code(client, pending_id, token, "000000")
        _submit_code(client, pending_id, token, otp)
        assert _tenant_count(db_engine, self.EMAIL) == 0

    def test_expired_code_is_refused(self, client, db_engine, monkeypatch):
        pending_id, token, otp = _start_signup(client, monkeypatch, "g-otp-4", self.EMAIL, "Admin")
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.oauth_signup_pending SET otp_expires_at = NOW() - INTERVAL '1 minute' "
                    "WHERE id=%s", (int(pending_id),))
        db_engine.commit()
        cur.close()
        _submit_code(client, pending_id, token, otp)
        assert _tenant_count(db_engine, self.EMAIL) == 0

    def test_wrong_token_cannot_use_the_code(self, client, db_engine, monkeypatch):
        pending_id, _token, otp = _start_signup(client, monkeypatch, "g-otp-5", self.EMAIL, "Admin")
        resp = _submit_code(client, pending_id, "not-the-token", otp)
        assert resp.headers["Location"] == "/create_org"
        assert client.get(f"/create_org/oauth/verify?id={pending_id}&token=not-the-token").status_code == 302
        assert _tenant_count(db_engine, self.EMAIL) == 0

    def test_malformed_token_never_reaches_a_redirect(self, client, db_engine, monkeypatch):
        pending_id, token, otp = _start_signup(client, monkeypatch, "g-otp-8", self.EMAIL, "Admin")
        for bad in (token + "&next=https://evil.example/x", token[:10]):
            assert _submit_code(client, pending_id, bad, otp).headers["Location"] == "/create_org"
            resend = client.post("/create_org/oauth/resend", data={"application_id": pending_id, "token": bad})
            assert resend.headers["Location"] == "/create_org"
        assert _tenant_count(db_engine, self.EMAIL) == 0

    def test_resend_replaces_the_code(self, client, db_engine, monkeypatch):
        import blueprints.org as org_module
        pending_id, token, first = _start_signup(client, monkeypatch, "g-otp-6", self.EMAIL, "Admin")
        sent = {}
        monkeypatch.setattr(org_module, "send_org_signup_otp_email",
                            lambda to, company, otp: sent.update(to=to, otp=otp) or True)
        resp = client.post("/create_org/oauth/resend", data={"application_id": pending_id, "token": token})
        assert resp.headers["Location"] == f"/create_org/oauth/verify?id={pending_id}&token={token}"
        assert sent["to"] == self.EMAIL and sent["otp"]
        if sent["otp"] != first:
            _submit_code(client, pending_id, token, first)
            assert _tenant_count(db_engine, self.EMAIL) == 0

    def test_code_is_single_use(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        try:
            pending_id, token, otp = _start_signup(client, monkeypatch, "g-otp-7", self.EMAIL, "Admin")
            assert _submit_code(client, pending_id, token, otp).headers["Location"].endswith("/admin")
            assert _submit_code(client, pending_id, token, otp).headers["Location"] == "/create_org"
            assert _tenant_count(db_engine, self.EMAIL) == 1
        finally:
            self._drop_created(db_engine)


class TestSignupRoutes:
    def test_start_redirects_to_provider_when_configured(self, client, google_creds, monkeypatch):
        monkeypatch.setattr(oauth_signup, "build_authorize_url", lambda *a, **k: "https://accounts.google.com/fake")
        resp = client.get("/create_org/google", follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "https://accounts.google.com/fake"

    def test_start_shows_error_when_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        resp = client.get("/create_org/google", follow_redirects=True)
        assert resp.status_code == 200
        assert b"isn&#39;t available" in resp.data or b"isn't available" in resp.data

    def test_unknown_provider_404s(self, client):
        assert client.get("/create_org/facebook").status_code == 404
        assert client.get("/create_org/facebook/callback").status_code == 404

    def test_verified_code_provisions_tenant_and_logs_in(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        schema_name = None
        try:
            pending_id, token, otp = _start_signup(client, monkeypatch, "g-newco-1", "admin@oauthtestco.com", "New Admin")
            resp = _submit_code(client, pending_id, token, otp)
            assert resp.status_code in (301, 302)
            location = resp.headers["Location"]
            assert "/admin" in location
            # Recover the subdomain the route actually picked (derived from
            # the email domain "oauthtestco.com" -> "Oauthtestco" -> slug).
            cur = db_engine.cursor()
            cur.execute(
                "SELECT subdomain, db_name, status, subscription_status, email_domain FROM att_master.tenants "
                "WHERE admin_email=%s",
                ("admin@oauthtestco.com",)
            )
            row = cur.fetchone()
            assert row is not None, "tenant was not provisioned"
            subdomain, schema_name, status, subscription_status, email_domain = row
            assert status == "active"
            assert subscription_status == "trialing"
            assert email_domain == "oauthtestco.com"
            assert location.endswith(f"/{subdomain}/admin")

            # Session is authenticated as the new admin.
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == "admin@oauthtestco.com"

            # admin_users row got the OAuth identity linked, and an
            # unusable (non-empty, bcrypt) password rather than blank.
            from database import get_tenant_db
            tconn = get_tenant_db(schema_name)
            tcur = tconn.cursor()
            tcur.execute("SELECT password, oauth_provider, oauth_subject FROM admin_users WHERE username=%s", ("admin@oauthtestco.com",))
            pw, provider, subject = tcur.fetchone()
            assert provider == "google"
            assert subject == "g-newco-1"
            assert pw and pw.startswith("$2")
            tcur.close()
            tconn.close()
        finally:
            if schema_name:
                _drop_schema(db_engine, schema_name)

    def test_duplicate_company_domain_rejected(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        schema_name = "att_existingco"
        _drop_schema(db_engine, schema_name)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, plan, payment_option, status, email_domain) "
            "VALUES ('Existing Co', 'existingco', %s, 'first@existingco.com', 'per_employee', 'trial', 'active', 'existingco.com')",
            (schema_name,)
        )
        db_engine.commit()
        try:
            monkeypatch.setattr(
                oauth_signup, "complete_oauth",
                lambda *a, **k: {
                    "sub": "g-second-1", "email": "second@existingco.com", "name": "Second Admin",
                    "provider": "google", "mode": "signup", "tenant_db": None,
                },
            )
            resp = client.get("/create_org/google/callback?code=abc&state=xyz", follow_redirects=True)
            assert resp.status_code == 200
            assert b"already been created" in resp.data

            cur.execute("SELECT COUNT(*) FROM att_master.tenants WHERE admin_email=%s", ("second@existingco.com",))
            assert cur.fetchone()[0] == 0
        finally:
            cur.execute("DELETE FROM att_master.tenants WHERE db_name=%s", (schema_name,))
            db_engine.commit()
            cur.close()

    def test_personal_domain_bypasses_domain_uniqueness(self, client, db_engine, monkeypatch):
        """Two different people signing up with @gmail.com addresses must
        both succeed -- the domain-uniqueness rule only applies to
        non-personal (company) domains. See PERSONAL_EMAIL_DOMAINS."""
        from app import init_master_db
        init_master_db()
        first_schema = "att_gmailperson1"
        _drop_schema(db_engine, first_schema)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, plan, payment_option, status, email_domain) "
            "VALUES ('Gmail Person1', 'gmailperson1', %s, 'person1@gmail.com', 'per_employee', 'trial', 'active', 'gmail.com')",
            (first_schema,)
        )
        db_engine.commit()
        second_schema = None
        try:
            pending_id, token, otp = _start_signup(client, monkeypatch, "g-gmailperson2", "person2@gmail.com", "Person Two")
            resp = _submit_code(client, pending_id, token, otp)
            assert resp.status_code in (301, 302)
            cur.execute("SELECT db_name FROM att_master.tenants WHERE admin_email=%s", ("person2@gmail.com",))
            row = cur.fetchone()
            assert row is not None, "second Gmail signup was wrongly blocked by domain uniqueness"
            second_schema = row[0]
        finally:
            _drop_schema(db_engine, first_schema)
            if second_schema:
                _drop_schema(db_engine, second_schema)
            cur.close()


class TestLoginRoutes:
    def test_unknown_provider_404s(self, client):
        assert client.get("/oauth_login/facebook").status_code == 404
        assert client.get("/oauth/callback/facebook").status_code == 404

    def test_callback_with_no_linked_account_shows_error(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        schema_name = "att_nolinkco"
        _drop_schema(db_engine, schema_name)
        from database import create_tenant_schema
        from app import init_tenant_db
        create_tenant_schema(schema_name)
        with flask_app.app_context():
            init_tenant_db(schema_name)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, plan, payment_option, status) "
            "VALUES ('No Link Co', 'nolinkco', %s, 'admin@nolinkco.com', 'per_employee', 'online', 'active')",
            (schema_name,)
        )
        db_engine.commit()
        try:
            monkeypatch.setattr(
                oauth_signup, "complete_oauth",
                lambda *a, **k: {
                    "sub": "unlinked-sub", "email": "someone@nolinkco.com", "name": "Someone",
                    "provider": "google", "mode": "login", "tenant_db": schema_name,
                },
            )
            resp = client.get("/oauth/callback/google?code=abc&state=xyz", follow_redirects=True)
            assert resp.status_code == 200
            assert b"No account here is linked" in resp.data
            with client.session_transaction() as sess:
                assert not sess.get("admin_logged_in")
        finally:
            _drop_schema(db_engine, schema_name)

    def test_callback_with_linked_account_logs_in(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        schema_name = "att_linkedco"
        _drop_schema(db_engine, schema_name)
        from database import create_tenant_schema, get_tenant_db
        from app import init_tenant_db
        create_tenant_schema(schema_name)
        with flask_app.app_context():
            init_tenant_db(schema_name)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, plan, payment_option, status) "
            "VALUES ('Linked Co', 'linkedco', %s, 'admin@linkedco.com', 'per_employee', 'online', 'active')",
            (schema_name,)
        )
        db_engine.commit()
        tconn = get_tenant_db(schema_name)
        tcur = tconn.cursor()
        # No admin_users row exists yet -- create_tenant_schema()+init_tenant_db()
        # above only creates the (empty) tables; provision_tenant() is what
        # normally seeds the first admin row, which this test bypasses on
        # purpose to set up the OAuth link directly.
        tcur.execute(
            "INSERT INTO admin_users (username, password, email, oauth_provider, oauth_subject) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("admin@linkedco.com", "unusable", "admin@linkedco.com", "google", "linked-sub-1")
        )
        tconn.commit()
        tcur.close()
        tconn.close()
        try:
            monkeypatch.setattr(
                oauth_signup, "complete_oauth",
                lambda *a, **k: {
                    "sub": "linked-sub-1", "email": "admin@linkedco.com", "name": "Admin",
                    "provider": "google", "mode": "login", "tenant_db": schema_name,
                },
            )
            resp = client.get("/oauth/callback/google?code=abc&state=xyz", follow_redirects=False)
            assert resp.status_code in (301, 302)
            assert resp.headers["Location"].endswith("/linkedco/admin")
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
                assert sess.get("admin_username") == "admin@linkedco.com"
        finally:
            _drop_schema(db_engine, schema_name)
