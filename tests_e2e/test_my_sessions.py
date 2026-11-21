""""Where you're signed in" (Settings, static/js/my-sessions.js) in a real
browser: two browsers signed in to the same admin account; one lists both,
signs the other out, and the other is sent to the sign-in page on its next
click.
"""
import re

from playwright.sync_api import expect

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"


def test_sign_out_another_browser(browser, base_url, login_as, db_conn):
    from utils.auth import generate_password_hash

    username, password = "e2e_sessions_admin", "Sessions#Passw0rd!E2E"
    cur = db_conn.cursor()
    cur.execute("INSERT INTO admin_users (username, password, email, is_active) VALUES (%s,%s,%s,1) "
                "ON CONFLICT (username) DO UPDATE SET password=EXCLUDED.password, is_active=1",
                (username, generate_password_hash(password), "e2e-sessions@example.invalid"))
    # This database outlives a run, and so would the last run's sessions (they are
    # still within their 8 hours): start from none.
    cur.execute("DELETE FROM user_sessions WHERE identifier=%s", (username,))
    cur.close()
    here_ctx = browser.new_context(user_agent=CHROME)
    there_ctx = browser.new_context(user_agent=FIREFOX)
    try:
        here, there = here_ctx.new_page(), there_ctx.new_page()
        # JavaScript errors and failed session API calls. Not every console error:
        # the Settings page already logs one CSP warning about an inline style,
        # with or without this card.
        problems = []
        here.on("pageerror", lambda exc: problems.append(f"page error: {exc}"))
        here.on("response", lambda r: "/api/my/sessions" in r.url and r.status >= 400
                and problems.append(f"HTTP {r.status} {r.request.method} {r.url}"))
        login_as(here, username, password)
        here.wait_for_url(re.compile(r"/admin$"))
        login_as(there, username, password)
        there.wait_for_url(re.compile(r"/admin$"))

        here.goto(f"{base_url}/settings?tab=email")
        box = here.locator("[data-my-sessions]")
        expect(box).to_contain_text("This browser", timeout=10_000)
        expect(box).to_contain_text("Firefox on Linux")
        here.on("dialog", lambda d: d.accept())
        box.locator(".my-session", has_text="Firefox on Linux").get_by_role("button", name="Sign out", exact=True).click()
        expect(box).to_contain_text("Signed out of 1 session", timeout=10_000)
        expect(box).not_to_contain_text("Firefox on Linux")

        there.goto(f"{base_url}/admin")
        assert re.search(r"/(admin[-_])?login", there.url), there.url     # e.g. /admin-login?locked=1
        assert problems == [], "\n".join(problems)
    finally:
        here_ctx.close()
        there_ctx.close()
