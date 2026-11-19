"""Security review OBS-06 (2026-11): RFC 9116 security.txt, the /security.txt
redirect and the disclosure policy page (blueprints/security_txt.py)."""
from datetime import datetime, timedelta, timezone

from blueprints.security_txt import EXPIRES


def test_well_known_security_txt(client):
    resp = client.get("/.well-known/security.txt")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "text/plain; charset=utf-8"
    body = resp.get_data(as_text=True)
    assert "Contact: mailto:security@hrzest.com\n" in body
    assert f"Expires: {EXPIRES}\n" in body
    assert "Preferred-Languages: en\n" in body
    assert "Canonical: https://hrzest.com/.well-known/security.txt\n" in body
    assert "Policy: https://hrzest.com/security-policy\n" in body
    assert "no-store" not in resp.headers.get("Cache-Control", "")
    assert "X-Robots-Tag" not in resp.headers


def test_root_security_txt_redirects(client):
    resp = client.get("/security.txt")
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/.well-known/security.txt")


def test_policy_page(client):
    resp = client.get("/security-policy")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "security@hrzest.com" in page and "vulnerability" in page.lower()
    assert "X-Robots-Tag" not in resp.headers, "the policy page may be indexed"


def test_expires_is_in_the_future_and_under_a_year():
    """RENEWAL REMINDER: this fails 60 days before security.txt's Expires date.
    Move EXPIRES in blueprints/security_txt.py about a year ahead (under a year)."""
    expires = datetime.strptime(EXPIRES, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert expires - now > timedelta(days=60), "security.txt Expires is close: renew it"
    assert expires - now <= timedelta(days=366), "RFC 9116: Expires should be less than a year away"


def test_paths_are_never_company_slugs():
    from utils.tenant_routing import RESERVED_PATH_SEGMENTS
    assert {".well-known", "security.txt", "security-policy"} <= RESERVED_PATH_SEGMENTS
