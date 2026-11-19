"""Sign in with email + password; forgot password by email, from anywhere.

Owner's request (2026-11-19): people sign in with their email, not their
employee ID -- an employee ID or username still works quietly as a fallback
(owner's choice, so nobody without an email on file is locked out). "Forgot
password" takes the email, works from the landing page with no company
chosen (it used to bounce to /login), sends the reset email when an account
has that email, and otherwise says plainly that there is no such account.

The suite's one company is "Test Co" at /att-test-suite/ (conftest.py).
"""
import pytest

import blueprints.auth as auth
from extensions import app as flask_app
from utils.helpers import encrypt_contact

EMP_EMAIL = "Priya.Employee@Acme.test"   # mixed case on purpose: matching ignores case


@pytest.fixture
def employee_email(db_engine, seed_employee):
    """The seeded employee's email, stored the way the app stores it
    (encrypted, with its blind index)."""
    enc, bidx = encrypt_contact("email", EMP_EMAIL.lower())
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET email=%s, email_bidx=%s WHERE employee_id=%s",
                (enc, bidx, seed_employee["employee_id"]))
    cur.close()
    return EMP_EMAIL


@pytest.fixture
def sent(monkeypatch):
    mails = []
    monkeypatch.setattr(auth, "get_email_config", lambda: {"host": "smtp.test"})
    monkeypatch.setattr(auth, "send_email_smtp", lambda to, subject, html, cfg: mails.append((to, html)))
    monkeypatch.setattr(auth, "send_email_async", lambda to, subject, html, cfg: mails.append((to, html)))
    return mails


# ── signing in ───────────────────────────────────────────────────────────────

class TestWebLoginByEmail:
    def test_employee_signs_in_with_email(self, client, seed_employee, employee_email):
        resp = client.post("/login", data={"identifier": employee_email, "password": seed_employee["password"]})
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("employee_id") == seed_employee["employee_id"]

    def test_admin_signs_in_with_email(self, client, seed_admin):
        resp = client.post("/login", data={"identifier": "ADMIN@test.local", "password": seed_admin["password"]})
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("admin_logged_in")

    def test_employee_id_still_works_as_a_fallback(self, client, seed_employee):
        resp = client.post("/login", data={"identifier": seed_employee["employee_id"],
                                           "password": seed_employee["password"]})
        assert resp.status_code == 302

    def test_wrong_password_for_an_email_is_refused(self, client, seed_employee, employee_email):
        resp = client.post("/login", data={"identifier": employee_email, "password": "Wrong-Pass-1"})
        assert b"Check your email and password." in resp.data

    def test_landing_page_login_finds_the_company_from_the_email(
            self, client, seed_employee, employee_email, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = client.post("/login", data={"identifier": employee_email, "password": seed_employee["password"]})
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("tenant_slug") == "att-test-suite"
            assert sess.get("employee_id") == seed_employee["employee_id"]

    def test_the_form_asks_for_email(self, client):
        page = client.get("/login").data
        assert b">Email</label>" in page and b"you@company.com" in page
        assert b"/forgot_password" in page


class TestAppSigninByEmail:
    def test_employee_email_signs_in_as_employee(self, client, seed_employee, employee_email, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = client.post("/api/signin", json={
            "identifier": employee_email, "password": seed_employee["password"]})
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["account"] == "employee" and body["employee_id"] == seed_employee["employee_id"]
        assert body["company_slug"] == "att-test-suite"

    def test_admin_email_signs_in_as_admin(self, client, seed_admin):
        resp = client.post("/api/signin", json={"identifier": "admin@test.local", "password": seed_admin["password"]})
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["account"] == "admin" and body["username"] == seed_admin["username"]


# ── forgot password ──────────────────────────────────────────────────────────

class TestForgotPasswordByEmail:
    def test_landing_page_link_opens_the_form_without_a_company(self, client, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = client.get("/forgot_password")
        assert resp.status_code == 200
        assert b'name="email"' in resp.data

    def test_known_employee_email_sends_the_link_and_says_so(
            self, client, seed_employee, employee_email, sent, monkeypatch, db_engine):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = client.post("/forgot_password", data={"email": employee_email})
        assert resp.status_code == 200
        assert b"Reset email sent" in resp.data and b"<dialog open" in resp.data
        assert len(sent) == 1
        to, html = sent[0]
        assert to.lower() == employee_email.lower()
        assert "/att-test-suite/employee_reset_password/" in html
        cur = db_engine.cursor()
        cur.execute("SELECT reset_token FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0]
        cur.close()

    def test_unknown_email_says_there_is_no_such_account(self, client, sent, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = client.post("/forgot_password", data={"email": "nobody@nowhere.test"})
        assert b"No account found with this email address." in resp.data
        assert b"<dialog open" in resp.data
        assert sent == []

    def test_known_admin_email(self, client, seed_admin, sent):
        resp = client.post("/forgot_password", data={"email": "admin@test.local"})
        assert b"A password reset link has been sent to admin@test.local" in resp.data
        assert len(sent) == 1 and "/admin_reset_password/" in sent[0][1]

    def test_empty_box(self, client):
        resp = client.post("/forgot_password", data={"email": ""})
        assert b"Please enter your email address." in resp.data

    def test_company_url_searches_only_that_company(self, client, seed_employee, employee_email, sent):
        resp = client.post("/att-test-suite/forgot_password", data={"email": employee_email})
        assert b"Reset email sent" in resp.data
        assert "/att-test-suite/employee_reset_password/" in sent[0][1]


class TestAppForgotPassword:
    def test_sent(self, client, seed_employee, employee_email, sent, monkeypatch):
        monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
        resp = client.post("/api/forgot_password", json={"email": employee_email})
        body = resp.get_json()
        assert resp.status_code == 200 and body["ok"] and body["code"] == "sent"
        assert len(sent) == 1

    def test_unknown_email_is_404(self, client, sent):
        resp = client.post("/api/forgot_password", json={"email": "ghost@nowhere.test"})
        assert resp.status_code == 404
        assert resp.get_json() == {"ok": False, "code": "not_found",
                                   "msg": "No account found with this email address."}

    def test_empty_is_400(self, client):
        assert client.post("/api/forgot_password", json={}).status_code == 400

    def test_email_service_missing_is_503(self, client, seed_admin, monkeypatch):
        monkeypatch.setattr(auth, "get_email_config", lambda: None)
        resp = client.post("/api/forgot_password", json={"email": "admin@test.local"})
        assert resp.status_code == 503 and resp.get_json()["code"] == "not_configured"


def test_request_context_company_is_restored(client, seed_employee, employee_email, sent, monkeypatch):
    """Searching every company switches this request between them; the page
    afterwards must not render as whichever company was searched last."""
    from flask import g
    monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
    with flask_app.test_request_context("/forgot_password", method="POST"):
        assert not hasattr(g, "tenant_db")
        auth._request_password_reset(employee_email)
        assert not hasattr(g, "tenant_db")
