"""Launch checklist (2026-11-23): Privacy Policy and Terms pages, /signup as
the sign-up page's clean address, llms.txt, social preview tags, breadcrumbs,
business structured data and server-side page-view counts."""
import json
import pathlib
import re

import pytest
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
PUBLIC_PAGES = ["/", "/signup", "/privacy", "/terms", "/security-policy"]


def _jsonld(html):
    return [json.loads(m) for m in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_page_is_indexable_and_fully_tagged(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "X-Robots-Tag" not in r.headers
    html = r.get_data(as_text=True)
    assert len(re.findall(r"<h1[\s>]", html)) == 1
    assert len(re.findall(r"<title>", html)) == 1
    assert re.search(r'<meta name="description" content="[^"]{40,}"', html)
    assert f'<link rel="canonical" href="https://hrzest.com{path}">' in html
    assert re.search(r'<meta property="og:image" content="https://hrzest.com/static/brand/og-image\.png', html)
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert 'rel="apple-touch-icon"' in html
    for block in _jsonld(html):         # every JSON-LD block parses
        assert block["@context"] == "https://schema.org"


def test_page_titles_are_unique(client):
    titles = [re.search(r"<title>(.*?)</title>", client.get(p).get_data(as_text=True), re.S).group(1)
              for p in PUBLIC_PAGES]
    assert len(set(titles)) == len(titles)


@pytest.mark.parametrize("path,name", [
    ("/privacy", "Privacy Policy"), ("/terms", "Terms of Service"),
    ("/security-policy", "Security policy"), ("/signup", "Sign up"),
])
def test_breadcrumbs(client, path, name):
    html = client.get(path).get_data(as_text=True)
    crumbs = [b for b in _jsonld(html) if b.get("@type") == "BreadcrumbList"]
    assert len(crumbs) == 1
    items = crumbs[0]["itemListElement"]
    assert [i["name"] for i in items] == ["Home", name]
    assert items[-1]["item"] == f"https://hrzest.com{path}"


def test_legal_pages_use_the_invoice_business_details(client):
    for path in ("/privacy", "/terms"):
        html = client.get(path).get_data(as_text=True)
        assert "contact@hrzest.com" in html and "500072" in html
        assert "Last updated:" in html
    terms = client.get("/terms").get_data(as_text=True)
    assert "30-day free trial" in terms


@pytest.mark.parametrize("old,new", [("/privacy-policy", "/privacy"), ("/terms-and-conditions", "/terms")])
def test_legal_aliases_redirect(client, old, new):
    r = client.get(old)
    assert r.status_code == 301 and r.headers["Location"] == new


def test_footer_and_signup_link_the_legal_pages(client):
    landing = client.get("/").get_data(as_text=True)
    assert 'href="/privacy"' in landing and 'href="/terms"' in landing
    signup = client.get("/signup").get_data(as_text=True)
    assert "By signing up you agree to our" in signup


def test_old_signup_address_redirects_permanently(client):
    r = client.get("/create_org")
    assert r.status_code == 301 and r.headers["Location"] == "/signup"
    r = client.get("/create_org?free=1")
    assert r.status_code == 301 and r.headers["Location"] == "/signup?free=1"


def test_no_page_links_to_the_old_signup_address(client):
    for path in PUBLIC_PAGES:
        assert 'href="/create_org"' not in client.get(path).get_data(as_text=True)


def test_landing_has_business_structured_data(client):
    blocks = _jsonld(client.get("/").get_data(as_text=True))
    graph = next(b["@graph"] for b in blocks if "@graph" in b)
    types = {node["@type"] for node in graph}
    assert {"Organization", "ProfessionalService", "WebSite"} <= types
    org = next(n for n in graph if n["@type"] == "Organization")
    assert org["address"]["postalCode"] == "500072"
    assert org["address"]["addressLocality"] == "Hyderabad"
    assert org["address"]["addressRegion"] == "Telangana"
    assert org["email"] == "contact@hrzest.com"


def test_llms_txt(client):
    r = client.get("/llms.txt")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "text/plain; charset=utf-8"
    body = r.get_data(as_text=True)
    assert body.startswith("# HRzest\n\n> ")
    for path in ("/signup", "/privacy", "/terms", "/security-policy"):
        assert f"(https://hrzest.com{path})" in body
    assert "30-day free trial" in body


def test_sitemap_lists_the_public_pages(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    for path in PUBLIC_PAGES:
        assert f"<loc>https://hrzest.com{path}</loc>" in body
    assert "create_org" not in body


def test_new_top_level_paths_are_reserved():
    from utils.tenant_routing import RESERVED_PATH_SEGMENTS
    assert {"signup", "privacy", "terms", "llms.txt", "privacy-policy", "terms-and-conditions"} <= RESERVED_PATH_SEGMENTS


def test_brand_images():
    sizes = {"og-image.png": (1200, 630), "apple-touch-icon.png": (180, 180),
             "favicon-32.png": (32, 32), "icon-512.png": (512, 512)}
    for name, size in sizes.items():
        with Image.open(ROOT / "static" / "brand" / name) as im:
            assert im.size == size


class TestPageViewCounting:
    """Server-side analytics: real page loads of public pages only."""

    @pytest.fixture
    def counted(self, monkeypatch):
        seen = []
        monkeypatch.setattr("utils.analytics.track_page_view", seen.append)
        return seen

    def test_counts_a_visit_to_each_public_page(self, client, counted):
        ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36"}
        for path in PUBLIC_PAGES:
            client.get(path, headers=ua)
        assert counted == PUBLIC_PAGES

    @pytest.mark.parametrize("ua", ["Mozilla/5.0 (compatible; Googlebot/2.1)", "curl/8.5.0",
                                    "facebookexternalhit/1.1", "Mozilla/5.0 Chrome-Lighthouse"])
    def test_ignores_bots_and_scripts(self, client, counted, ua):
        client.get("/privacy", headers={"User-Agent": ua})
        assert counted == []

    def test_ignores_private_pages_redirects_and_head(self, client, counted):
        ua = {"User-Agent": "Mozilla/5.0 Chrome/131.0"}
        client.get("/login", headers=ua)
        client.get("/create_org", headers=ua)       # 301, not a page view
        client.head("/privacy", headers=ua)
        client.get("/does-not-exist", headers=ua)
        assert counted == []
