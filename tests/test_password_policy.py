"""Security audit P4.2 (2026-11-20): new passwords need 12+ characters and
must not be known from a data breach (utils/password_breach.py).

The breach check is faked here: it never touches the network in tests.
"""
import hashlib

import pytest
import requests

from utils import password_breach
from utils.auth import MIN_PASSWORD_LENGTH, validate_new_password

BREACHED = "Breached-Password-01"
_SHA = hashlib.sha1(BREACHED.encode(), usedforsecurity=False).hexdigest().upper()


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


@pytest.fixture
def breach_check_on(monkeypatch):
    monkeypatch.setenv("PASSWORD_BREACH_CHECK", "1")
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return _FakeResponse(f"{'0' * 35}:3\r\n{_SHA[5:]}:42\r\n{'F' * 35}:0")

    monkeypatch.setattr(password_breach.requests, "get", fake_get)
    return calls


def test_minimum_length_is_twelve():
    assert MIN_PASSWORD_LENGTH == 12
    ok, msg = validate_new_password("Elevenchars")  # 11
    assert not ok and "12 characters" in msg
    assert validate_new_password("Twelve-chars")[0]  # 12


def test_absurdly_long_password_is_refused():
    assert not validate_new_password("x" * 257)[0]


def test_breached_password_is_refused(breach_check_on):
    ok, msg = validate_new_password(BREACHED)
    assert not ok
    assert "breach" in msg


def test_only_the_five_character_prefix_leaves_the_server(breach_check_on):
    validate_new_password(BREACHED)
    assert len(breach_check_on) == 1
    url = breach_check_on[0]["url"]
    assert url == "https://api.pwnedpasswords.com/range/" + _SHA[:5]
    assert BREACHED not in url and _SHA[5:] not in url
    assert breach_check_on[0]["headers"]["Add-Padding"] == "true"
    assert breach_check_on[0]["timeout"]


def test_unbreached_password_is_allowed(breach_check_on):
    assert validate_new_password("Not-In-The-Fake-List-77")[0]


def test_padding_entries_with_count_zero_do_not_count(monkeypatch):
    monkeypatch.setenv("PASSWORD_BREACH_CHECK", "1")
    monkeypatch.setattr(password_breach.requests, "get",
                        lambda *a, **k: _FakeResponse(f"{_SHA[5:]}:0"))
    assert not password_breach.password_is_breached(BREACHED)


@pytest.mark.parametrize("failure", ["timeout", "http_503"])
def test_check_fails_open(monkeypatch, failure):
    """An outage of the third-party service must not block password changes."""
    monkeypatch.setenv("PASSWORD_BREACH_CHECK", "1")

    def fake_get(*_a, **_k):
        if failure == "timeout":
            raise requests.Timeout("slow")
        return _FakeResponse("", status_code=503)

    monkeypatch.setattr(password_breach.requests, "get", fake_get)
    assert validate_new_password(BREACHED)[0]


def test_check_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("PASSWORD_BREACH_CHECK", "0")

    def boom(*_a, **_k):
        raise AssertionError("no network call when the check is off")

    monkeypatch.setattr(password_breach.requests, "get", boom)
    assert validate_new_password(BREACHED)[0]


def test_company_signup_uses_the_shared_rule():
    """The signup validator (web /create_org and the app's /api/create_org)
    goes through validate_new_password like every set/reset path."""
    from blueprints.org.validation import _validate_signup_account
    msg = _validate_signup_account("Policy Test Co", "policy@example.com", "9876543210", "Elevenchars")
    assert msg and "12 characters" in msg
