"""A bare /login -- the landing page's Sign in, no company in the URL --
(blueprints/auth.py's admin_login): the credentials name the company, so
the person just signs in. With LOGIN_ASKS_FOR_COMPANY on (a multi-company
server) only the companies are searched, never the default database; the
company step (?company=) is left for links and for an account that's in
more than one company. Off, an account in the default database still signs
in there (local dev and the rest of the suite use it).

The suite's own tenant row (conftest.py) is the company looked up here:
"Test Co" at /att-test-suite/."""
import pytest

SLUG = "att-test-suite"


@pytest.fixture
def asks_for_company(monkeypatch):
    monkeypatch.setenv("LOGIN_ASKS_FOR_COMPANY", "true")


def test_off_by_default_bare_login_is_the_sign_in_form(client, monkeypatch):
    monkeypatch.delenv("LOGIN_ASKS_FOR_COMPANY", raising=False)
    html = client.get("/login").get_data(as_text=True)
    assert 'name="identifier"' in html
    assert 'name="company"' not in html


def test_bare_login_is_the_sign_in_form(client, asks_for_company):
    html = client.get("/login").get_data(as_text=True)
    assert 'name="identifier"' in html and 'name="password"' in html
    assert 'name="company"' not in html


@pytest.mark.parametrize("typed", [
    SLUG,
    SLUG.upper(),
    f"hrzest.com/{SLUG}",
    f"https://hrzest.com/{SLUG}/login",
    "Test Co",
    "  test co  ",
])
def test_company_url_or_name_goes_to_its_login(client, asks_for_company, typed):
    resp = client.get("/login", query_string={"company": typed})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/{SLUG}/login")


def test_unknown_company_stays_on_the_step_with_an_error(client, asks_for_company):
    resp = client.get("/login", query_string={"company": "no-such-company-xyz"})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "couldn&#39;t find that company" in html or "couldn't find that company" in html
    assert 'name="company"' in html


def test_credentials_on_bare_login_sign_in_to_their_company(client, asks_for_company, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"], "password": seed_admin["password"],
    })
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        assert sess.get("admin_logged_in")
        assert sess.get("tenant_slug") == SLUG


def test_employee_id_on_bare_login_signs_in_to_their_company(client, asks_for_company, seed_employee):
    resp = client.post("/login", data={
        "identifier": seed_employee["employee_id"], "password": seed_employee["password"],
    })
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        assert sess.get("employee_id") == seed_employee["employee_id"]
        assert sess.get("tenant_slug") == SLUG


def test_wrong_password_on_bare_login_signs_in_nowhere(client, asks_for_company, seed_admin):
    resp = client.post("/login", data={"identifier": seed_admin["username"], "password": "not-the-password"})
    assert resp.status_code == 200
    assert 'name="identifier"' in resp.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")


def test_unknown_account_on_bare_login_signs_in_nowhere(client, asks_for_company):
    resp = client.post("/login", data={"identifier": "nobody-here@example.test", "password": "whatever-123"})
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in") and not sess.get("employee_id")


def test_account_in_two_companies_asks_which(client, asks_for_company, seed_admin, monkeypatch):
    import blueprints.auth as auth
    monkeypatch.setattr(auth, "_company_for_credentials", lambda identifier, password: "many")
    resp = client.post("/login", data={
        "identifier": seed_admin["username"], "password": seed_admin["password"],
    })
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'name="company"' in html and "more than one company" in html
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")


def test_without_the_setting_the_default_database_still_signs_in(client, monkeypatch, seed_admin):
    monkeypatch.delenv("LOGIN_ASKS_FOR_COMPANY", raising=False)
    resp = client.post("/login", data={
        "identifier": seed_admin["username"], "password": seed_admin["password"],
    })
    assert resp.status_code in (301, 302)
    with client.session_transaction() as sess:
        assert sess.get("admin_logged_in")
        assert not sess.get("tenant_slug")  # stayed in the default database


def test_company_login_page_is_the_normal_form(client, asks_for_company):
    html = client.get(f"/{SLUG}/login").get_data(as_text=True)
    assert 'name="identifier"' in html
    assert 'name="company"' not in html


# The app's sign-in screen resolves the typed company first
# (/api/company/resolve), so "no such company" isn't reported as a wrong password.
@pytest.mark.parametrize("typed", [SLUG, f"https://hrzest.com/{SLUG}/login", "Test Co"])
def test_api_resolves_a_company_url_or_name(client, typed):
    resp = client.get("/api/company/resolve", query_string={"q": typed})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "slug": SLUG}


def test_api_unknown_company_is_404_with_a_message(client):
    resp = client.get("/api/company/resolve", query_string={"q": "no-such-company-xyz"})
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["ok"] is False and "couldn't find that company" in body["msg"]
