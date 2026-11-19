"""Security review OBS-09 (2026-11): hrzest.com is canonical, and
www.hrzest.com redirects to it with the path and query string kept
(utils/canonical_host.py; nginx does the same in production)."""
import pytest

from utils.canonical_host import canonical_origin


@pytest.fixture
def prod_url(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://hrzest.com")


def test_www_get_redirects_with_path_and_query(client, prod_url):
    resp = client.get("/test?x=1&y=a%20b", base_url="https://www.hrzest.com")
    assert resp.status_code == 301
    assert resp.headers["Location"] == "https://hrzest.com/test?x=1&y=a%20b"


def test_www_tenant_path_and_root(client, prod_url):
    assert client.get("/acme/login", base_url="https://www.hrzest.com").headers["Location"] \
        == "https://hrzest.com/acme/login"
    assert client.get("/", base_url="https://www.hrzest.com").headers["Location"] == "https://hrzest.com/"


def test_www_post_keeps_its_method(client, prod_url):
    resp = client.post("/login", base_url="https://www.hrzest.com", data={"a": "b"})
    assert resp.status_code == 308
    assert resp.headers["Location"] == "https://hrzest.com/login"


def test_www_with_port_and_mixed_case(client, prod_url):
    resp = client.get("/x", base_url="https://WWW.HrZest.com:443")
    assert resp.status_code == 301 and resp.headers["Location"] == "https://hrzest.com/x"


def test_canonical_host_is_served_normally(client, prod_url):
    resp = client.get("/robots.txt", base_url="https://hrzest.com")
    assert resp.status_code == 200


def test_other_hosts_are_untouched(client, prod_url):
    # Never redirect a host we don't own to ours, or rewrite look-alikes.
    for host in ("https://evil.example", "https://wwwhrzest.com", "https://www.hrzest.com.evil.example"):
        assert client.get("/robots.txt", base_url=host).status_code == 200, host


@pytest.mark.parametrize("app_url", ["", "http://localhost:5000", "http://18.61.48.198", "https://www.hrzest.com"])
def test_no_redirect_without_a_real_canonical_domain(client, monkeypatch, app_url):
    monkeypatch.setenv("APP_URL", app_url)
    assert canonical_origin() is None
    assert client.get("/robots.txt", base_url="https://www.hrzest.com").status_code == 200


def test_canonical_tags_and_sitemap_use_the_apex(client, monkeypatch):
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    page = client.get("/").get_data(as_text=True)
    assert '<link rel="canonical" href="https://hrzest.com/">' in page
    assert '<meta property="og:url" content="https://hrzest.com/">' in page
    assert "www.hrzest.com" not in client.get("/sitemap.xml").get_data(as_text=True)
