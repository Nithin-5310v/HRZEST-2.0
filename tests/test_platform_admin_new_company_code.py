"""The Platform Admin "New Company" form requires the code emailed to the
company's admin address before it creates the company -- the same email
verification every self-serve signup goes through (blueprints/platform_admin.py
platform_admin_send_new_company_code / platform_admin_create_tenant)."""
from tests.test_platform_admin import _login_platform_admin, _new_company_email_code


def _form(**overrides):
    data = {
        "company_name": "Code Gate Co", "subdomain": "code-gate-co", "admin_username": "cg_admin",
        "admin_password": "CodeGate@123", "admin_email": "owner@codegate.test", "payment_option": "manual",
        "email_domain": "codegate.test",
    }
    data.update(overrides)
    return data


def _tenant_exists(db_engine, subdomain="code-gate-co"):
    cur = db_engine.cursor()
    cur.execute("SELECT 1 FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
    found = cur.fetchone() is not None
    cur.close()
    return found


def _create(client, **overrides):
    return client.post("/super_admin/tenants/create", data=_form(**overrides), follow_redirects=True)


_NO_CODE_SENT = b"to email a verification code to the admin email, then enter it"


class TestSendCode:
    def test_requires_platform_admin_login(self, client):
        resp = client.post("/super_admin/tenants/send_email_code", json={"admin_email": "a@b.test"})
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"] == "/super_admin/login"

    def test_invalid_email_is_refused_without_sending(self, client, monkeypatch):
        import blueprints.org as org_module
        sent = []
        monkeypatch.setattr(org_module, "send_org_signup_otp_email", lambda *a: sent.append(a) or True)
        _login_platform_admin(client)
        resp = client.post("/super_admin/tenants/send_email_code", json={"admin_email": "not-an-email"})
        assert resp.status_code == 400 and resp.get_json()["ok"] is False
        assert sent == []

    def test_emails_a_six_digit_code(self, client, monkeypatch):
        _login_platform_admin(client)
        code = _new_company_email_code(client, monkeypatch, "Owner@CodeGate.test")
        assert len(code) == 6 and code.isdigit()


class TestCreateRequiresCode:
    def test_without_sending_a_code_nothing_is_created(self, client, db_engine):
        _login_platform_admin(client)
        resp = _create(client, email_code="123456")
        assert _NO_CODE_SENT in resp.data
        assert not _tenant_exists(db_engine)

    def test_blank_code_is_refused(self, client, db_engine, monkeypatch):
        _login_platform_admin(client)
        _new_company_email_code(client, monkeypatch, "owner@codegate.test")
        resp = _create(client, email_code="")
        assert b"Enter the verification code" in resp.data
        assert not _tenant_exists(db_engine)

    def test_wrong_code_is_refused(self, client, db_engine, monkeypatch):
        _login_platform_admin(client)
        _new_company_email_code(client, monkeypatch, "owner@codegate.test")
        resp = _create(client, email_code="000000")   # real codes are 100000-999999
        assert b"Incorrect or expired verification code" in resp.data
        assert not _tenant_exists(db_engine)

    def test_code_sent_to_another_address_is_refused(self, client, db_engine, monkeypatch):
        _login_platform_admin(client)
        code = _new_company_email_code(client, monkeypatch, "someone-else@codegate.test")
        resp = _create(client, email_code=code)
        assert b"sent to a different address" in resp.data
        assert not _tenant_exists(db_engine)

    def test_a_code_cannot_be_replayed(self, client, db_engine, monkeypatch):
        """A correct code is used up by its first create attempt (here one
        refused for a mismatched address), so it can't be submitted again."""
        _login_platform_admin(client)
        code = _new_company_email_code(client, monkeypatch, "someone-else@codegate.test")
        _create(client, email_code=code)
        resp = _create(client, admin_email="someone-else@codegate.test", email_code=code)
        assert _NO_CODE_SENT in resp.data
        assert not _tenant_exists(db_engine)


class TestDashboardForm:
    def test_form_has_send_code_and_required_code_field(self, client):
        _login_platform_admin(client)
        page = client.get("/super_admin").get_data(as_text=True)
        assert 'id="newCoSendCodeBtn"' in page and "function newCoSendEmailCode" in page
        assert 'name="email_code"' in page
        field = page[page.index('name="email_code"'):]
        assert "required" in field[:field.index("/>")]
