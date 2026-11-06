"""Settings page tabs: ARIA roles and keyboard navigation (static/a11y-tabs.js).

The palette revert in 5378b13 dropped the <script> include and the wiring
block from settings.html, which left the tab strip mouse-only for keyboard
and screen-reader users. This renders the real /settings page (with the
app's own CSP) in Chromium, every request served by the Flask test client,
and checks that the tabs are announced as tabs and follow the arrow keys.
"""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def settings_page(client, seed_admin):
    # A plain test client, not the fixture's `with` one: Playwright calls the
    # route handler outside that preserved request context. `client` is
    # still requested for the test app config it sets.
    from extensions import app as flask_app
    client = flask_app.test_client()
    client.post("/login", data={"identifier": seed_admin["username"],
                                "password": seed_admin["password"]})

    def forward(route):
        req = route.request
        resp = client.open(req.url.split("hz.test", 1)[1], method=req.method,
                           data=req.post_data_buffer,
                           headers={"Content-Type": req.headers.get("content-type", "")})
        route.fulfill(status=resp.status_code, body=resp.get_data(),
                      headers={k: v for k, v in resp.headers.items()
                               if k.lower() not in ("content-length", "set-cookie")})

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page()
        page.route("http://hz.test/**", forward)
        page.goto("http://hz.test/settings")
        page.wait_for_function("() => !!window.ezWireTabList")
        yield page
        browser.close()


def test_script_is_included(client, seed_admin):
    client.post("/login", data={"identifier": seed_admin["username"],
                                "password": seed_admin["password"]})
    assert b"a11y-tabs.js" in client.get("/settings").data


TABS = ["s3tab-org", "s3tab-fin", "s3tab-sys"]


def _active_tab(page):
    return page.evaluate("() => document.querySelector('.s3-tab.active').id")


def test_group_tabs_expose_tab_roles(settings_page):
    active = _active_tab(settings_page)
    for tab_id in TABS:
        tab = settings_page.locator("#" + tab_id)
        panel_id = tab_id.replace("s3tab-", "s3panel-")
        assert tab.get_attribute("role") == "tab"
        assert tab.get_attribute("aria-controls") == panel_id
        assert settings_page.locator("#" + panel_id).get_attribute("role") == "tabpanel"
        selected = tab_id == active
        assert tab.get_attribute("aria-selected") == ("true" if selected else "false")
        assert tab.get_attribute("tabindex") == ("0" if selected else "-1")


def test_arrow_key_moves_to_and_activates_next_tab(settings_page):
    start = _active_tab(settings_page)
    expected = TABS[(TABS.index(start) + 1) % len(TABS)]
    settings_page.focus("#" + start)
    settings_page.keyboard.press("ArrowRight")
    assert settings_page.evaluate("() => document.activeElement.id") == expected
    assert _active_tab(settings_page) == expected
    assert settings_page.locator("#" + expected).get_attribute("aria-selected") == "true"
    assert settings_page.locator("#" + start).get_attribute("aria-selected") == "false"


def test_sub_section_items_are_tabs(settings_page):
    item = settings_page.locator("#ddi-payroll")
    assert item.get_attribute("role") == "tab"
    assert item.get_attribute("aria-controls") == "ss-payroll"


def test_dropdown_toggle_reports_expanded_state(settings_page):
    # The toggles are hidden in the current layout, so open the menu the way
    # their click handler does and check the state is announced.
    btn = settings_page.locator("#s3panel-org .subnav-dd-btn")
    assert btn.get_attribute("aria-haspopup") == "true"
    assert btn.get_attribute("aria-expanded") == "false"
    settings_page.evaluate("() => document.getElementById('dd-org').classList.add('open')")
    settings_page.wait_for_function(
        "() => document.querySelector('#s3panel-org .subnav-dd-btn').getAttribute('aria-expanded') === 'true'")
