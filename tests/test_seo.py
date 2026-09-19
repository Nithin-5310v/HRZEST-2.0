"""SEO surface: robots.txt, sitemap.xml, canonical/description tags, and the
noindex default for every private page (blueprints/seo.py)."""
import xml.etree.ElementTree as ET

from blueprints.seo import INDEXABLE_PATHS
from utils.tenant_routing import RESERVED_PATH_SEGMENTS

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class TestRobotsTxt:
    def test_points_crawlers_at_the_sitemap_and_hides_apis(self, client, monkeypatch):
        monkeypatch.setenv("APP_URL", "https://seo.example.test/")
        monkeypatch.setenv("APP_ENV", "production")
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        body = resp.get_data(as_text=True)
        assert "Sitemap: https://seo.example.test/sitemap.xml" in body
        assert "Disallow: /api/" in body
        assert "Disallow: /super_admin" in body

    def test_login_pages_are_not_blocked_so_their_noindex_is_seen(self, client):
        body = client.get("/robots.txt").get_data(as_text=True)
        assert "Disallow: /admin_login" not in body
        assert "Disallow: /login" not in body

    def test_development_environment_blocks_everything(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        body = client.get("/robots.txt").get_data(as_text=True)
        assert "Disallow: /\n" in body
        assert "Sitemap:" not in body

    def test_falls_back_to_default_domain_without_app_url(self, client, monkeypatch):
        monkeypatch.delenv("APP_URL", raising=False)
        monkeypatch.setenv("APP_ENV", "production")
        assert "Sitemap: https://www.hrzest.com/sitemap.xml" in client.get("/robots.txt").get_data(as_text=True)

    def test_host_header_cannot_poison_the_sitemap_url(self, client, monkeypatch):
        monkeypatch.setenv("APP_URL", "https://real.example.test")
        monkeypatch.setenv("APP_ENV", "production")
        body = client.get("/robots.txt", headers={"Host": "evil.example"}).get_data(as_text=True)
        assert "evil.example" not in body
        assert "https://real.example.test/sitemap.xml" in body


class TestSitemap:
    def test_lists_exactly_the_indexable_pages(self, client, monkeypatch):
        monkeypatch.setenv("APP_URL", "https://seo.example.test")
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.mimetype == "application/xml"
        locs = [e.text for e in ET.fromstring(resp.data).findall("sm:url/sm:loc", _NS)]
        assert locs == [f"https://seo.example.test{p}" for p in INDEXABLE_PATHS]

    def test_names_cannot_be_claimed_as_a_company_slug(self):
        assert {"robots.txt", "sitemap.xml"} <= RESERVED_PATH_SEGMENTS


class TestIndexingRules:
    def test_landing_page_is_indexable_with_canonical(self, client, monkeypatch):
        monkeypatch.setenv("APP_URL", "https://seo.example.test")
        resp = client.get("/")
        assert resp.status_code == 200
        assert "X-Robots-Tag" not in resp.headers
        assert '<link rel="canonical" href="https://seo.example.test/">' in resp.get_data(as_text=True)

    def test_search_console_verification_tag_is_optional(self, client, monkeypatch):
        monkeypatch.delenv("GOOGLE_SITE_VERIFICATION", raising=False)
        assert "google-site-verification" not in client.get("/").get_data(as_text=True)
        monkeypatch.setenv("GOOGLE_SITE_VERIFICATION", "abc123token")
        assert '<meta name="google-site-verification" content="abc123token">' in client.get("/").get_data(as_text=True)

    def test_signup_page_is_indexable_with_description(self, client):
        resp = client.get("/create_org")
        assert resp.status_code == 200
        assert "X-Robots-Tag" not in resp.headers
        html = resp.get_data(as_text=True)
        assert '<meta name="description"' in html
        assert 'rel="canonical"' in html

    def test_tenant_prefixed_copy_of_a_public_page_is_noindex(self, client):
        resp = client.get("/", environ_overrides={"SCRIPT_NAME": "/acme"})
        assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"

    def test_private_pages_are_noindex(self, client):
        for path in ("/admin_login", "/checkin", "/employee_forgot_password", "/super_admin/login"):
            resp = client.get(path)
            assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow", path

    def test_redirects_and_errors_are_noindex(self, client):
        assert client.get("/get-started").headers.get("X-Robots-Tag") == "noindex, nofollow"
        assert client.get("/definitely-not-a-page").headers.get("X-Robots-Tag") == "noindex, nofollow"

    def test_crawler_files_and_static_assets_are_left_alone(self, client):
        for path in ("/robots.txt", "/sitemap.xml", "/static/favicon.svg"):
            assert "X-Robots-Tag" not in client.get(path).headers, path
