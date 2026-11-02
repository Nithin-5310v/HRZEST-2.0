"""Emailed reset links must keep the company's /<company> URL prefix.

The forgot-password forms run inside a company (/<company>/admin_forgot_password)
and store the reset token in that company's database. The emailed link used
to be <site>/admin_reset_password/<token> -- without the prefix it opened in
no company, found no token and said the link was invalid.
"""
import re

import pytest

import blueprints.auth as auth

COMPANY = {"SCRIPT_NAME": "/acme"}


@pytest.fixture
def sent(monkeypatch):
    mails = []
    monkeypatch.setattr(auth, "get_email_config", lambda: {"host": "smtp.test"})
    monkeypatch.setattr(auth, "send_email_smtp", lambda to, subject, html, cfg: mails.append(html))
    monkeypatch.setattr(auth, "send_email_async", lambda to, subject, html, cfg: mails.append(html))
    return mails


def _link(html, page):
    match = re.search(r'href="([^"]*/%s/[^"]+)"' % page, html)
    assert match, f"no {page} link in the email"
    return match.group(1)


def test_admin_reset_link_keeps_the_company(client, db_engine, seed_admin, sent):
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET email='owner@acme.test' WHERE username=%s", (seed_admin["username"],))
    db_engine.commit()
    cur.close()
    client.post("/admin_forgot_password", data={"email": "owner@acme.test"}, environ_overrides=COMPANY)
    assert "/acme/admin_reset_password/" in _link(sent[0], "admin_reset_password")


def test_employee_reset_link_keeps_the_company_and_opens(client, seed_employee, sent):
    client.post("/employee_forgot_password", data={"employee_id": seed_employee["employee_id"]},
                environ_overrides=COMPANY)
    link = _link(sent[0], "employee_reset_password")
    assert "/acme/employee_reset_password/" in link
    token = link.rsplit("/", 1)[1]
    page = client.get(f"/employee_reset_password/{token}", environ_overrides=COMPANY)
    assert page.status_code == 200 and b"invalid" not in page.data.lower()
