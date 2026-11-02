"""Emailed reset links must name the company and open in any browser.

The forgot-password forms store the reset token in the company's own
database, so the emailed link needs the company's /<company> prefix. It is
added explicitly (utils/helpers.py's company_url), not taken from the
request's URL: a bare /login whose company step is done remembers the
company only in the session, and a link built from that URL opened from a
mail app (no session) landed in no company and reported "invalid link".

The suite's own company is "Test Co" at /att-test-suite/ (conftest.py).
"""
import os
import re

import pytest

import blueprints.auth as auth
from extensions import app as flask_app

SLUG = "att-test-suite"


@pytest.fixture
def sent(monkeypatch):
    mails = []
    monkeypatch.setattr(auth, "get_email_config", lambda: {"host": "smtp.test"})
    monkeypatch.setattr(auth, "send_email_smtp", lambda to, subject, html, cfg: mails.append(html))
    monkeypatch.setattr(auth, "send_email_async", lambda to, subject, html, cfg: mails.append(html))
    return mails


@pytest.fixture
def admin_email(db_engine, seed_admin):
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET email='owner@acme.test' WHERE username=%s", (seed_admin["username"],))
    db_engine.commit()
    cur.close()
    return "owner@acme.test"


def _link(html, page):
    match = re.search(r'href="([^"]*/%s/[^"]+)"' % page, html)
    assert match, f"no {page} link in the email"
    return match.group(1)


def _opens_in_a_fresh_browser(link):
    """The emailed link, opened with no cookies at all (a mail app, another device)."""
    path = re.sub(r"^https?://[^/]+", "", link)
    with flask_app.test_client() as fresh:
        page = fresh.get(path)
    return page.status_code == 200 and b"invalid" not in page.data.lower()


def test_admin_link_from_the_company_url(client, admin_email, sent):
    client.post(f"/{SLUG}/admin_forgot_password", data={"email": admin_email})
    link = _link(sent[0], "admin_reset_password")
    assert f"/{SLUG}/admin_reset_password/" in link
    assert _opens_in_a_fresh_browser(link)


def test_admin_link_from_a_bare_url_with_the_company_in_the_session(client, admin_email, sent):
    # A bare /login after its company step: the company is only in the session.
    with client.session_transaction() as sess:
        sess["tenant_db"] = os.environ["DB_NAME"]
        sess["tenant_slug"] = SLUG
    client.post("/admin_forgot_password", data={"email": admin_email})
    link = _link(sent[0], "admin_reset_password")
    assert f"/{SLUG}/admin_reset_password/" in link
    assert _opens_in_a_fresh_browser(link)


def test_employee_link_names_the_company_and_opens(client, seed_employee, sent):
    client.post(f"/{SLUG}/employee_forgot_password", data={"employee_id": seed_employee["employee_id"]})
    link = _link(sent[0], "employee_reset_password")
    assert f"/{SLUG}/employee_reset_password/" in link
    assert _opens_in_a_fresh_browser(link)


@pytest.mark.parametrize("page", ["admin_forgot_password", "employee_forgot_password"])
def test_no_company_known_goes_to_the_company_step_first(client, monkeypatch, page):
    monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")
    resp = client.get(f"/{page}")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")
    # Inside a company the form is shown as usual.
    assert client.get(f"/{SLUG}/{page}").status_code == 200
