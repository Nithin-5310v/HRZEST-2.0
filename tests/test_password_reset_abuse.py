"""Roadmap HR-04 (21 Nov 2026): password-reset abuse cases.

"Test expired, malformed, replayed, already-used and cross-account reset
tokens", prove tokens are stored only as hashes, that a reset ends other
sessions, and that the reset page can't leak its token through caches or
the Referer. Enumeration-resistant answers are covered in
tests/test_email_login_and_reset.py; token redaction in access logs in
tests/test_security_review_2026_11_20.py.
"""
import datetime
import hashlib
import secrets

import pytest

from utils.auth import check_password_hash

NEW_PW = "Reset-Orbit-Kettle-77"


def _set_token(db_engine, table, key_col, key, expired=False):
    token = secrets.token_urlsafe(32)
    expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=-1 if expired else 1)
    cur = db_engine.cursor()
    cur.execute(f"UPDATE {table} SET reset_token=%s, reset_token_expiry=%s WHERE {key_col}=%s",  # nosec B608 -- test literals
                (hashlib.sha256(token.encode()).hexdigest(), expiry, key))
    cur.close()
    return token


def _password_hash(db_engine, table, key_col, key):
    cur = db_engine.cursor()
    cur.execute(f"SELECT password FROM {table} WHERE {key_col}=%s", (key,))  # nosec B608 -- test literals
    value = cur.fetchone()[0]
    cur.close()
    return value


def _post_reset(client, route, token, password=NEW_PW):
    return client.post(f"/{route}/{token}", data={"new_password": password, "confirm_password": password})


@pytest.fixture
def admin_target(seed_admin):
    return ("admin_reset_password", "admin_users", "username", seed_admin["username"])


@pytest.fixture
def employee_target(seed_employee):
    return ("employee_reset_password", "employees", "employee_id", seed_employee["employee_id"])


@pytest.fixture(params=["admin", "employee"])
def target(request, admin_target, employee_target):
    return admin_target if request.param == "admin" else employee_target


def test_expired_token_cannot_change_the_password(client, db_engine, target):
    route, table, key_col, key = target
    before = _password_hash(db_engine, table, key_col, key)
    token = _set_token(db_engine, table, key_col, key, expired=True)
    resp = _post_reset(client, route, token)
    assert resp.status_code == 200
    assert _password_hash(db_engine, table, key_col, key) == before


def test_token_works_once_and_never_again(client, db_engine, target):
    route, table, key_col, key = target
    token = _set_token(db_engine, table, key_col, key)
    assert _post_reset(client, route, token).status_code == 200
    first = _password_hash(db_engine, table, key_col, key)
    assert check_password_hash(first, NEW_PW)
    # Replay with a different password: refused, nothing changes.
    _post_reset(client, route, token, password="Another-Orbit-Kettle-88")
    assert _password_hash(db_engine, table, key_col, key) == first


@pytest.mark.parametrize("bad", ["x", "A" * 600, "..%2f..%2fetc", "<script>alert(1)</script>", "' OR '1'='1", "%00"])
def test_malformed_tokens_are_refused_without_errors(client, db_engine, target, bad):
    route, table, key_col, key = target
    before = _password_hash(db_engine, table, key_col, key)
    resp = client.post(f"/{route}/{bad}", data={"new_password": NEW_PW, "confirm_password": NEW_PW})
    # 200 "invalid link", 404, or 403 when the WAF stops an attack-shaped token first.
    assert resp.status_code in (200, 403, 404)
    assert _password_hash(db_engine, table, key_col, key) == before


def test_admin_token_cannot_reset_an_employee_or_the_reverse(client, db_engine, admin_target, employee_target):
    a_route, a_table, a_col, a_key = admin_target
    e_route, e_table, e_col, e_key = employee_target
    admin_before = _password_hash(db_engine, a_table, a_col, a_key)
    emp_before = _password_hash(db_engine, e_table, e_col, e_key)
    admin_token = _set_token(db_engine, a_table, a_col, a_key)
    emp_token = _set_token(db_engine, e_table, e_col, e_key)
    _post_reset(client, e_route, admin_token)
    _post_reset(client, a_route, emp_token)
    assert _password_hash(db_engine, a_table, a_col, a_key) == admin_before
    assert _password_hash(db_engine, e_table, e_col, e_key) == emp_before


def test_tokens_are_stored_only_as_hashes(client, db_engine, seed_admin, monkeypatch):
    """The emailed link carries the raw token; the database holds only its SHA-256."""
    from blueprints import auth as auth_module
    sent = []
    monkeypatch.setattr(auth_module, "get_email_config", lambda: {"host": "smtp.test"})
    monkeypatch.setattr(auth_module, "send_email_smtp", lambda to, subject, html, cfg, **_: sent.append(html))
    monkeypatch.setattr(auth_module, "send_email_async", lambda to, subject, html, cfg, **_: sent.append(html))
    client.post("/forgot_password", data={"email": "admin@test.local"})
    if not sent:
        pytest.skip("reset email path not reachable with this fixture")
    raw = sent[0].split("/admin_reset_password/")[1].split('"')[0].split("<")[0]
    cur = db_engine.cursor()
    cur.execute("SELECT reset_token FROM admin_users WHERE username=%s", (seed_admin["username"],))
    stored = cur.fetchone()[0]
    cur.close()
    assert stored != raw
    assert stored == hashlib.sha256(raw.encode()).hexdigest()
    assert len(raw) >= 32


def test_reset_ends_other_sessions(client, db_engine, seed_admin):
    """A session opened before the reset must not survive it."""
    from flask.testing import FlaskClient  # noqa: F401 -- type only
    victim = client.application.test_client()
    victim.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    if victim.get("/admin").status_code != 200:
        pytest.skip("admin login needs MFA here")
    token = _set_token(db_engine, "admin_users", "username", seed_admin["username"])
    assert _post_reset(client, "admin_reset_password", token).status_code == 200
    resp = victim.get("/admin")
    assert resp.status_code in (302, 401)


@pytest.mark.parametrize("route", ["admin_reset_password", "employee_reset_password"])
def test_reset_page_cannot_leak_its_token(client, route):
    resp = client.get(f"/{route}/{secrets.token_urlsafe(32)}")
    cc = resp.headers.get("Cache-Control", "")
    assert "no-store" in cc
    # The token is in the URL: no full URL may go to another site in a Referer.
    assert resp.headers.get("Referrer-Policy") in ("no-referrer", "strict-origin-when-cross-origin", "same-origin")
