"""SOC console sign-in in a real browser: email -> emailed code -> passkey.

tests/test_soc_passkeys.py proves the server side with py_webauthn's checks
replaced; this proves the other half that only a browser can: the sign-in page,
soc_ui/login.js, the CSRF header on its fetches, navigator.credentials.create /
.get, and the bytes it sends back, all verified for real by py_webauthn.

The passkey lives in Chromium's virtual authenticator (DevTools WebAuthn domain),
which behaves like a platform authenticator with user verification. The emailed
code is captured in-process: the live server runs in this pytest process
(conftest.py), so replacing soc_auth.deliver_login_code reaches it.

The console is opened at http://localhost:<port>: browsers accept localhost as a
passkey's site (and as a secure context), but not an IP address.
"""
import pytest
from playwright.sync_api import expect

from utils import soc_auth

EMAIL = "e2e-soc-analyst@example.invalid"


@pytest.fixture
def soc(monkeypatch, db_conn, base_url):
    """The SOC console's settings for one test, and the codes it 'emails'."""
    codes = []
    monkeypatch.setenv("SOC_LOGIN_EMAILS", EMAIL)
    monkeypatch.setenv("SOC_PASSKEY", "on")
    monkeypatch.delenv("SOC_ALLOWED_CIDRS", raising=False)          # open: APP_ENV=development
    monkeypatch.setattr(soc_auth, "deliver_login_code", lambda email, code: codes.append(code) or True)

    def clean():
        cur = db_conn.cursor()
        cur.execute("DELETE FROM att_master.soc_passkeys WHERE user_id IN "
                    "(SELECT id FROM att_master.soc_users WHERE email=%s)", (EMAIL,))
        cur.execute("DELETE FROM att_master.soc_users WHERE email=%s", (EMAIL,))
        cur.close()
    clean()
    yield {"codes": codes, "url": base_url.replace("127.0.0.1", "localhost")}
    clean()


@pytest.fixture
def authenticator(page):
    """A virtual platform authenticator (fingerprint / face that always succeeds)."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    created = cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
        "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
        "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True}})
    return cdp, created["authenticatorId"]


def _passkeys(db_conn):
    cur = db_conn.cursor()
    cur.execute("SELECT credential_id, sign_count FROM att_master.soc_passkeys WHERE user_id IN "
                "(SELECT id FROM att_master.soc_users WHERE email=%s)", (EMAIL,))
    rows = cur.fetchall()
    cur.close()
    return rows


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
    page.click("button:has-text('Sign in')")
    expect(page.locator("#pk")).to_be_visible()


def test_passkey_is_created_at_first_sign_in_and_then_signs_in(page, soc, authenticator, db_conn):
    cdp, auth_id = authenticator

    # First sign-in: after the code, the console is still closed and asks for a new passkey.
    _email_and_code(page, soc, db_conn)
    expect(page.locator("h1")).to_have_text("Set up your passkey")
    page.goto(soc["url"] + "/socperson")
    expect(page).to_have_url(soc["url"] + "/socperson/login")          # no way round the step
    page.click("#pk")
    expect(page).to_have_url(soc["url"] + "/socperson")
    expect(page.locator("#whoName")).to_have_text(EMAIL)

    held = cdp.send("WebAuthn.getCredentials", {"authenticatorId": auth_id})["credentials"]
    assert len(held) == 1 and held[0]["rpId"] == "localhost"
    stored = _passkeys(db_conn)
    assert len(stored) == 1                                              # public key on the server

    # Sign out and back in: this time the same passkey is asked for and verified.
    page.click("#logoutBtn")
    expect(page).to_have_url(soc["url"] + "/socperson/login")
    _email_and_code(page, soc, db_conn)
    expect(page.locator("h1")).to_have_text("Confirm it's you")
    page.click("#pk")
    expect(page).to_have_url(soc["url"] + "/socperson")
    expect(page.locator("#whoName")).to_have_text(EMAIL)
    assert len(_passkeys(db_conn)) == 1                                  # used, not added
    assert _passkeys(db_conn)[0][1] >= 1                                 # signature counter advanced


def test_a_device_without_the_passkey_cannot_finish_sign_in(page, soc, authenticator, db_conn, browser):
    # Enrol on this device first.
    _email_and_code(page, soc, db_conn)
    page.click("#pk")
    expect(page).to_have_url(soc["url"] + "/socperson")

    # Another device: right inbox, right code, but its authenticator holds no passkey for us.
    other = browser.new_context()
    try:
        p2 = other.new_page()
        cdp2 = other.new_cdp_session(p2)
        cdp2.send("WebAuthn.enable")
        cdp2.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
            "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True}})
        _email_and_code(p2, soc, db_conn)
        expect(p2.locator("h1")).to_have_text("Confirm it's you")
        p2.click("#pk")
        # The browser finds no matching passkey and refuses; the page says so and stays closed.
        expect(p2.locator("#pkStatus")).to_contain_text("cancelled or timed out", timeout=90_000)
        p2.goto(soc["url"] + "/socperson")
        expect(p2).to_have_url(soc["url"] + "/socperson/login")
    finally:
        other.close()
