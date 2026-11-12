import pytest

PUBLIC_PAGES = ["/", "/checkin", "/create_org"]
ADMIN_MODULES = [
    "/admin", "/employees", "/monthly_report", "/salary_report",
    "/leave_holidays", "/announcements", "/tickets", "/overtime",
    "/performance", "/onboarding", "/resignations", "/analytics",
    "/admin_tools", "/settings", "/profile",
]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_page_opens(page, base_url, path):
    assert page.goto(base_url + path).status == 200


def test_wrong_password_is_refused(page, login_as, admin_creds):
    login_as(page, admin_creds["username"], "wrong-password")
    page.wait_for_load_state()
    assert page.locator('input[name="password"]').is_visible()


@pytest.fixture(scope="module")
def admin_page(browser, base_url, admin_creds):
    """One logged-in admin page for every module test: /login allows 20
    requests a minute, so logging in per test trips the rate limit."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{base_url}/login")
    page.fill('input[name="identifier"]', admin_creds["username"])
    page.fill('input[name="password"]', admin_creds["password"])
    page.click('button[type="submit"]')
    page.wait_for_url("**/admin")
    yield page
    context.close()


@pytest.mark.parametrize("path", ADMIN_MODULES)
def test_admin_module_opens(admin_page, base_url, path):
    assert admin_page.goto(base_url + path).status == 200
    assert admin_page.locator(".sidebar").is_visible()
