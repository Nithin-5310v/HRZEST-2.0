"""SOC console sign-in in a real browser: email -> emailed code -> password.

tests/test_soc_password.py proves the server side; this proves what a person
actually does on the page: the forms, their CSRF tokens, the browser's own
checks on the password fields, and that the console stays shut until the
password step is done. The first sign-in sets the password, later ones enter it.

The emailed code is captured in-process: the live server runs in this pytest
process (conftest.py), so replacing soc_auth.deliver_login_code reaches it.
"""
import pytest
from playwright.sync_api import expect

from utils import soc_auth

EMAIL = "e2e-soc-analyst@example.invalid"
PASSWORD = "e2e long passphrase 4 soc"


@pytest.fixture
def soc(monkeypatch, db_conn, base_url):
    """The SOC console's settings for one test, and the codes it 'emails'."""
    codes = []
    monkeypatch.setenv("SOC_LOGIN_EMAILS", EMAIL)
    monkeypatch.delenv("SOC_ALLOWED_CIDRS", raising=False)          # open: APP_ENV=development
    monkeypatch.setattr(soc_auth, "deliver_login_code", lambda email, code: codes.append(code) or True)

    def clean():
        cur = db_conn.cursor()
        cur.execute("DELETE FROM att_master.soc_users WHERE email=%s", (EMAIL,))
        cur.close()
    clean()
    # The app sends 127.0.0.1 on to localhost (app.py); start there so URLs compare equal.
    yield {"codes": codes, "url": base_url.replace("127.0.0.1", "localhost")}
    clean()


def _email_and_code(page, soc, db_conn):
    # A code goes out at most once a minute; the test does not wait for that.
    cur = db_conn.cursor()
    cur.execute("UPDATE att_master.soc_users SET login_code_sent_at=NULL WHERE email=%s", (EMAIL,))
    cur.close()
    sent_before = len(soc["codes"])
    page.goto(soc["url"] + "/socperson/login")
    page.fill('input[name="email"]', EMAIL)
    page.click("button:has-text('Send sign-in code')")
    expect(page.locator("h1")).to_have_text("Check your email")
    assert len(soc["codes"]) == sent_before + 1
    page.fill('input[name="code"]', soc["codes"][-1])
    page.click("button:has-text('Continue')")


def test_the_password_is_set_at_first_sign_in_and_then_asked_for(page, soc, db_conn):
    # First sign-in: after the code, the console is still closed and asks for a new password.
    _email_and_code(page, soc, db_conn)
    expect(page.locator("h1")).to_have_text("Set your password")
    page.goto(soc["url"] + "/socperson")
    expect(page).to_have_url(soc["url"] + "/socperson/login")          # no way round the step
    expect(page.locator("h1")).to_have_text("Set your password")

    page.fill('input[name="password"]', PASSWORD)
    page.fill('input[name="confirm"]', PASSWORD + "x")                 # a typo in the repeat
    page.click("button:has-text('Set password and sign in')")
    expect(page.locator(".alert")).to_have_text("The two passwords do not match.")
    page.fill('input[name="password"]', PASSWORD)
    page.fill('input[name="confirm"]', PASSWORD)
    page.click("button:has-text('Set password and sign in')")
    expect(page).to_have_url(soc["url"] + "/socperson")
    expect(page.locator("#whoName")).to_have_text(EMAIL)

    # Sign out and back in: this time the password is asked for, not set.
    page.click("#logoutBtn")
    expect(page).to_have_url(soc["url"] + "/socperson/login")
    _email_and_code(page, soc, db_conn)
    expect(page.locator("h1")).to_have_text("Enter your password")
    page.fill('input[name="password"]', "not the password")
    page.click("button:has-text('Sign in')")
    expect(page.locator(".alert")).to_have_text("That password is not correct.")
    page.goto(soc["url"] + "/socperson")
    expect(page).to_have_url(soc["url"] + "/socperson/login")
    page.fill('input[name="password"]', PASSWORD)
    page.click("button:has-text('Sign in')")
    expect(page).to_have_url(soc["url"] + "/socperson")
    expect(page.locator("#whoName")).to_have_text(EMAIL)


def test_the_browser_refuses_a_too_short_password_before_sending_it(page, soc, db_conn):
    _email_and_code(page, soc, db_conn)
    field = page.locator('input[name="password"]')
    assert field.get_attribute("minlength") == str(soc_auth.MIN_PASSWORD_LENGTH)
    assert field.get_attribute("autocomplete") == "new-password"
    field.fill("short")
    page.fill('input[name="confirm"]', "short")
    page.click("button:has-text('Set password and sign in')")
    assert field.evaluate("el => el.validity.tooShort") is True
    expect(page.locator("h1")).to_have_text("Set your password")      # nothing was submitted
