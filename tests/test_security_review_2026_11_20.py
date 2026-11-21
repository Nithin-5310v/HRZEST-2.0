"""Regression tests for the full security review of 2026-11-20 (after the
P1-P4 audit fixes): authorization gaps for custom roles, information leaks,
injection paths and hardening. One test (or a few) per fix.
"""
import pytest
from flask import g

from extensions import app as flask_app

ROLE = "audit_receptionist"


@pytest.fixture
def custom_role(db_engine):
    """A company-scope custom role with access only to announcements."""
    cur = db_engine.cursor()
    cur.execute("INSERT INTO roles (role_key, display_name, base_type) VALUES (%s,'Receptionist','custom') "
                "ON CONFLICT (role_key) DO NOTHING", (ROLE,))
    cur.execute("INSERT INTO role_permissions (role_key, module, access) VALUES (%s,'announcements','edit') "
                "ON CONFLICT (role_key, module) DO NOTHING", (ROLE,))
    yield ROLE
    cur.execute("DELETE FROM role_permissions WHERE role_key=%s", (ROLE,))
    cur.execute("DELETE FROM roles WHERE role_key=%s", (ROLE,))
    cur.close()


def _role_session(client, role, username="AUDITR1"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_role"] = role
        sess["admin_username"] = username


# ── Authorization ────────────────────────────────────────────────────────────

class TestCustomRoleCannotUseUnrelatedModules:
    def test_cannot_edit_employee_email(self, client, custom_role, seed_employee, db_engine):
        """Account takeover: change the email, then reset the password."""
        _role_session(client, custom_role)
        client.post("/edit_employee", data={"emp_id": seed_employee["employee_id"], "name": "Test Employee",
                                             "email": "attacker@example.com"})
        cur = db_engine.cursor()
        cur.execute("SELECT email FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        from utils.helpers import decrypt_pii
        assert decrypt_pii(cur.fetchone()[0]) != "attacker@example.com"
        cur.close()

    def test_cannot_add_employee(self, client, custom_role, db_engine):
        _role_session(client, custom_role)
        client.post("/add_employee_page", data={"emp_id": "GHOST1", "name": "Ghost"})
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM employees WHERE employee_id='GHOST1'")
        assert cur.fetchone() is None
        cur.close()

    def test_cannot_correct_attendance(self, client, custom_role, seed_employee):
        _role_session(client, custom_role)
        resp = client.post("/correct_attendance", data={
            "emp_id": seed_employee["employee_id"], "date": "2026-11-02", "attendance_type": "Full Day"})
        assert resp.status_code in (302, 403)
        assert "/correct_attendance" not in resp.headers.get("Location", "")

    @pytest.mark.parametrize("path", ["/lock_payroll", "/unlock_payroll"])
    def test_cannot_lock_or_unlock_payroll(self, client, custom_role, path):
        _role_session(client, custom_role)
        resp = client.post(path, data={"year": "2026", "month": "10"},
                           headers={"Accept": "application/json"})
        assert resp.status_code in (302, 403)

    def test_cannot_send_email_blast(self, client, custom_role):
        _role_session(client, custom_role)
        resp = client.post("/api/admin/email-blast", json={"target_type": "all", "subject": "s", "body": "b"})
        assert resp.status_code in (401, 403)


def test_self_approval_blocked_for_custom_roles():
    from blueprints.leave import _approving_own_record
    with flask_app.test_request_context("/"):
        from flask import session
        session["admin_role"] = "team_lead"
        session["admin_username"] = "EMP9"
        assert _approving_own_record("EMP9") is True
        assert _approving_own_record("EMP8") is False
        session["admin_role"] = "admin"
        assert _approving_own_record("EMP9") is False


def test_team_scope_reads_the_api_token_owner():
    """A team-scoped token is scoped like the same person's web session."""
    from utils.helpers import admin_actor
    with flask_app.test_request_context("/"):
        g.api_user = "HR42"
        g._api_user_role = "senior_hr"
        assert admin_actor() == ("HR42", "senior_hr")


# ── Injection ────────────────────────────────────────────────────────────────

def test_trusted_html_never_gives_a_script_a_nonce():
    from utils import csp
    with flask_app.test_request_context("/"):
        flask_app.preprocess_request()
        out = csp.trusted_html("<style>a{}</style><td><script src=//evil.example/x.js></script></td>")
        assert f'<style nonce="{g.csp_nonce}">' in out
        assert "<script src=//evil.example/x.js>" in out
        assert f'<script nonce="{g.csp_nonce}"' not in out


def test_unknown_attendance_type_is_refused(client, seed_admin, seed_employee, db_engine):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    client.post("/correct_attendance", data={
        "emp_id": seed_employee["employee_id"], "date": "2026-11-03",
        "attendance_type": "<script src=//evil.example/x.js></script>"})
    cur = db_engine.cursor()
    cur.execute("SELECT 1 FROM attendance WHERE employee_id=%s AND attendance_type LIKE '%%script%%'",
                (seed_employee["employee_id"],))
    assert cur.fetchone() is None
    cur.close()


def test_spreadsheet_cells_cannot_hold_formulas():
    from decimal import Decimal
    from utils.csv_export import spreadsheet_safe
    assert spreadsheet_safe('=HYPERLINK("https://evil")') == '\'=HYPERLINK("https://evil")'
    assert spreadsheet_safe("@SUM(A1)").startswith("'")
    assert spreadsheet_safe("Acme Ltd") == "Acme Ltd"
    assert spreadsheet_safe(Decimal("12.50")) == Decimal("12.50")
    assert spreadsheet_safe(-5) == -5


def test_smtp_host_must_be_public(monkeypatch):
    from utils.email_utils import smtp_host_problem
    monkeypatch.setenv("APP_ENV", "production")
    assert smtp_host_problem("169.254.169.254", 587)
    assert smtp_host_problem("127.0.0.1", 25)
    assert smtp_host_problem("10.0.0.5", 465)
    assert smtp_host_problem("smtp.office365.com", 6379)  # not a mail port
    assert smtp_host_problem("8.8.8.8", 587) is None


def test_image_decompression_bomb_limit_is_set():
    from PIL import Image
    assert Image.MAX_IMAGE_PIXELS <= 25_000_000


def test_logo_file_names_are_unguessable():
    import inspect
    from utils.helpers import uploads
    src = inspect.getsource(uploads.save_uploaded_logo)
    assert "token_hex" in src


# ── Information disclosure ───────────────────────────────────────────────────

def test_forgot_password_answer_is_the_same_for_unknown_accounts(client):
    resp = client.post("/api/forgot_password", json={"email": "nobody-here@example.com"})
    assert resp.status_code == 200
    assert "If an account matches" in resp.get_json()["msg"]


def test_webauthn_status_reveals_only_availability(client):
    assert set(client.get("/webauthn/status").get_json()) == {"webauthn_available"}


def test_public_checkin_config_has_no_office_location(client):
    data = client.get("/api/employee/auth-config").get_json()
    assert not {"office_lat", "office_lon", "geo_radius"} & set(data)


def test_access_log_redacts_tokens():
    from utils.log_redaction import redact_url
    line = 'GET /acme/admin_reset_password/abcDEF123 HTTP/1.1'
    assert "abcDEF123" not in redact_url(line)
    assert "secret" not in redact_url("/create_org/status/5?token=secret&x=1")
    assert redact_url("/acme/admin") == "/acme/admin"


def test_proxy_fix_does_not_trust_forwarded_host():
    import inspect
    from utils import client_ip
    assert "x_host=0" in inspect.getsource(client_ip.apply_proxy_fix)


# ── OAuth ────────────────────────────────────────────────────────────────────

def test_oauth_callback_without_the_browser_binding_is_refused(client, monkeypatch):
    from blueprints import oauth_signup
    called = []
    monkeypatch.setattr(oauth_signup, "complete_oauth", lambda *a, **k: called.append(1))
    resp = client.get("/oauth/callback/google?code=c&state=attacker-state")
    assert resp.status_code == 302
    assert not called
