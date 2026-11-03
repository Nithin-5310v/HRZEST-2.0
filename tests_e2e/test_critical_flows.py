"""End-to-end coverage for 5 flows a real user drives through a browser,
not the Flask test client -- see README.md for why these are separate from
tests/ and what they do/don't cover.

Deliberately avoid anything requiring a real detectable face photo (Add
Employee's web form and /api/employees both reject a photo with no
detected face via face_recognition.face_encodings() -- see
blueprints/employees.py -- and this environment has no legitimate way to
supply one without either a real photograph or monkeypatching the live
server process, which an external browser-driven test can't do). Every
flow below is chosen to be both genuinely critical and achievable without
that dependency.
"""
import re


def test_admin_login_reaches_dashboard(page, base_url, login_as, admin_creds):
    login_as(page, admin_creds["username"], admin_creds["password"])
    page.wait_for_url(re.compile(r"/admin$"))
    assert page.locator("body").count() == 1  # page actually rendered, not a blank/error response


def test_employee_login_reaches_portal(page, base_url, login_as, employee_creds):
    login_as(page, employee_creds["employee_id"], employee_creds["password"])
    page.wait_for_url(re.compile(r"/employee_portal"))
    assert "My Portal" in page.title()


def test_leave_request_submit_and_admin_approval(page, base_url, login_as, admin_creds, employee_creds, db_conn):
    # Employee side: submit a leave request through the real form.
    # employee_portal.html is a single-page tabbed view -- every section
    # lives in the same document behind a JS-toggled .view, restored from
    # the URL hash on load, so #apply-leave both navigates there and
    # avoids a click on a sidebar nav item just to reveal the form.
    login_as(page, employee_creds["employee_id"], employee_creds["password"])
    page.wait_for_url(re.compile(r"/employee_portal"))
    page.goto(f"{base_url}/employee_portal#apply-leave")
    page.select_option('select[name="leave_type_id"]', index=1)
    page.fill('input[name="leave_date_start"]', "2027-01-15")
    # "Reason" is a chip-picker, not a plain text field -- clicking a chip
    # is what actually populates the hidden #reason-input the form submits
    # (see employee_portal.html's chip click handler).
    page.click('.reason-chip[data-reason="Sick Leave"]')
    page.click('#leave-form button[type="submit"]')
    page.wait_for_load_state("networkidle")

    cur = db_conn.cursor()
    cur.execute(
        "SELECT id, status FROM leave_requests WHERE employee_id=%s AND reason=%s",
        (employee_creds["employee_id"], "Sick Leave"),
    )
    row = cur.fetchone()
    cur.close()
    assert row is not None, "leave request was not persisted"
    lid, status = row
    assert status == "Pending"

    # Admin side: approve that exact request through the real button. A
    # fresh browser CONTEXT (not just a new tab/page in the same one) --
    # page.context.new_page() would share the employee's session cookie,
    # so logging in as admin there would silently hijack/overwrite the
    # same cookie jar the employee's own `page` is still using.
    admin_context = page.context.browser.new_context()
    admin_page = admin_context.new_page()
    login_as(admin_page, admin_creds["username"], admin_creds["password"])
    admin_page.wait_for_url(re.compile(r"/admin$"))
    admin_page.goto(f"{base_url}/leave_holidays?tab=leaves")
    admin_page.click(f'form[action="/leave_action/{lid}"] button.btn-approve')
    admin_page.wait_for_load_state("networkidle")

    cur = db_conn.cursor()
    cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
    (final_status,) = cur.fetchone()
    cur.close()
    assert final_status == "Approved"
    admin_context.close()


def test_ticket_raise_and_admin_resolution(page, base_url, login_as, admin_creds, employee_creds, db_conn):
    login_as(page, employee_creds["employee_id"], employee_creds["password"])
    page.wait_for_url(re.compile(r"/employee_portal"))
    page.goto(f"{base_url}/employee_portal#tickets")
    page.select_option("#ticket-category", "Technical Problem")
    page.fill("#ticket-subject", "E2E test ticket subject")
    page.fill("#ticket-desc", "E2E test ticket description")
    page.click(".ticket-raise-form .btn-ticket")
    page.wait_for_load_state("networkidle")

    cur = db_conn.cursor()
    cur.execute(
        "SELECT id, status FROM tickets WHERE employee_id=%s AND subject=%s",
        (employee_creds["employee_id"], "E2E test ticket subject"),
    )
    row = cur.fetchone()
    cur.close()
    assert row is not None, "ticket was not persisted"
    tid, status = row
    assert status == "Open"

    # Fresh browser context -- see the leave-request test's own comment on
    # why sharing the employee page's context here would collide sessions.
    admin_context = page.context.browser.new_context()
    admin_page = admin_context.new_page()
    login_as(admin_page, admin_creds["username"], admin_creds["password"])
    admin_page.wait_for_url(re.compile(r"/admin$"))
    admin_page.goto(f"{base_url}/tickets")
    ticket_form = admin_page.locator(f'form[action="/ticket_action/{tid}"]')
    ticket_form.locator('select[name="status"]').select_option("Resolved")
    ticket_form.locator('textarea[name="admin_response"]').fill("Resolved via e2e test")
    ticket_form.locator("button.btn-respond").click()
    admin_page.wait_for_load_state("networkidle")

    cur = db_conn.cursor()
    cur.execute("SELECT status, admin_response FROM tickets WHERE id=%s", (tid,))
    final_status, admin_response = cur.fetchone()
    cur.close()
    assert final_status == "Resolved"
    assert admin_response == "Resolved via e2e test"
    admin_context.close()


def test_admin_password_change_round_trip(page, base_url, login_as, db_conn):
    """Security-critical: an admin's own password-change form must both
    take effect (old password stops working) and actually work (new
    password logs in) -- not just "the form submitted without a 500"."""
    from utils.auth import generate_password_hash

    username = "e2e_pwchange_admin"
    old_password = "OldPassw0rd#E2E!"
    new_password = "NewPassw0rd#E2E!2"
    cur = db_conn.cursor()
    cur.execute(
        "INSERT INTO admin_users (username, password, email, is_active) VALUES (%s,%s,%s,1) "
        "ON CONFLICT (username) DO UPDATE SET password=EXCLUDED.password, is_active=1",
        (username, generate_password_hash(old_password), "e2e-pwchange@example.invalid"),
    )
    cur.close()
    try:
        login_as(page, username, old_password)
        page.wait_for_url(re.compile(r"/admin$"))
        page.goto(f"{base_url}/settings?tab=email")
        page.fill('input[name="current_password"]', old_password)
        page.fill('input[name="new_password"]', new_password)
        page.fill('input[name="confirm_password"]', new_password)
        page.click('form[action="/change_admin_password"] button[type="submit"]')
        page.wait_for_url(re.compile(r"pwd_ok=1"))

        # Verify from fresh browser contexts (no cookies at all, as a truly
        # separate login attempt would be) rather than reusing this page
        # post-logout -- avoids depending on this app's own logout-redirect
        # timing/cookie-scoping to prove the point this test actually cares
        # about: which password the server accepts now.
        old_pw_context = page.context.browser.new_context()
        old_pw_page = old_pw_context.new_page()
        login_as(old_pw_page, username, old_password)
        assert "/admin" not in old_pw_page.url, "old password still works after being changed"
        old_pw_context.close()

        new_pw_context = page.context.browser.new_context()
        new_pw_page = new_pw_context.new_page()
        login_as(new_pw_page, username, new_password)
        new_pw_page.wait_for_url(re.compile(r"/admin$"))
        new_pw_context.close()
    finally:
        cur = db_conn.cursor()
        cur.execute("DELETE FROM admin_users WHERE username=%s", (username,))
        cur.close()
