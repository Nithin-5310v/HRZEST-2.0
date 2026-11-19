"""Security review OBS-01 (2026-11): auth, signup, password-recovery and
reset pages, authenticated pages and API responses must never be cached;
only static assets and the public SEO/security files may be.
Policy: app_setup/cache_control.py."""
import pytest

NO_STORE = "no-store, no-cache, must-revalidate, private"


def _assert_no_store(resp):
    assert resp.headers.get("Cache-Control") == NO_STORE
    assert resp.headers.get("Pragma") == "no-cache"
    assert resp.headers.get("Expires") == "0"


@pytest.mark.parametrize("path", [
    "/login",
    "/admin_login",
    "/create_org",
    "/admin_forgot_password",
    "/employee_forgot_password",
    "/super_admin/login",
    "/socperson/login",
    "/",
])
def test_auth_and_signup_pages_are_not_cached(client, path):
    resp = client.get(path)
    assert resp.status_code < 500
    _assert_no_store(resp)


@pytest.mark.parametrize("path", ["/admin_reset_password/not-a-real-token",
                                  "/employee_reset_password/not-a-real-token"])
def test_reset_link_pages_are_not_cached(client, path):
    # Whatever an unknown token renders (an error page or a redirect), the
    # response must not be cached: real reset links carry a secret.
    resp = client.get(path)
    assert resp.status_code != 404, "route exists"
    _assert_no_store(resp)


def test_authenticated_page_is_not_cached(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    resp = client.get("/employees")
    _assert_no_store(resp)


def test_api_responses_are_not_cached(client):
    _assert_no_store(client.get("/api/employees"))           # 401 without a token
    _assert_no_store(client.post("/api/login", json={}))     # login API


def test_redirects_and_errors_are_not_cached(client):
    _assert_no_store(client.get("/no-such-page-anywhere"))
    _assert_no_store(client.post("/login", data={"identifier": "x", "password": "y"}))


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml"])
def test_public_seo_files_stay_cacheable(client, path):
    resp = client.get(path)
    assert "no-store" not in (resp.headers.get("Cache-Control") or "")
    assert resp.headers.get("Pragma") is None


def test_static_assets_stay_cacheable(client):
    resp = client.get("/static/hz-actions.js")
    assert resp.status_code == 200
    assert "no-store" not in (resp.headers.get("Cache-Control") or "")


def test_cacheable_allow_list_is_small():
    """Adding a path here makes it cacheable by browsers and proxies -- it
    must never be a page with user or tenant data."""
    from app_setup.cache_control import is_cacheable_path
    assert is_cacheable_path("/static/x.css")
    for private in ("/login", "/admin_forgot_password", "/admin_reset_password/t", "/api/employees",
                    "/super_admin", "/employee_portal", "/static_but_not_really"):
        assert not is_cacheable_path(private), private
