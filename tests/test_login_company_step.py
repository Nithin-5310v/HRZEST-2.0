"""/login's company step (blueprints/auth.py's admin_login): with
LOGIN_ASKS_FOR_COMPANY on, a bare /login -- no company in the URL or the
session -- asks which company first and sends the person to that company's
/<company>/login, instead of signing in to the default database. Off, a
bare /login is unchanged (local dev and the rest of the suite use it).

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


def test_bare_login_asks_for_the_company(client, asks_for_company):
    html = client.get("/login").get_data(as_text=True)
    assert 'name="company"' in html
    assert 'name="identifier"' not in html
    assert 'name="password"' not in html


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


def test_password_posted_to_bare_login_signs_in_nowhere(client, asks_for_company, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"], "password": seed_admin["password"],
    })
    assert resp.status_code == 200
    assert 'name="company"' in resp.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")


def test_company_login_page_is_the_normal_form(client, asks_for_company):
    html = client.get(f"/{SLUG}/login").get_data(as_text=True)
    assert 'name="identifier"' in html
    assert 'name="company"' not in html
