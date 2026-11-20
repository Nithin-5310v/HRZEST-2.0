# -*- coding: utf-8 -*-
"""Refuse new passwords that appear in known data breaches.

Security audit P4.2 (2026-11-20). Uses the Pwned Passwords range API with
k-anonymity: only the first 5 hex characters of the password's SHA-1 leave
this server, and the service answers with every breached hash sharing that
prefix (padded with decoys, so the response size reveals nothing). The
password itself, its full hash and who is setting it are never sent.

Fails OPEN: a timeout, an outage or blocked outbound HTTPS lets the password
through (it still has to meet the length rule) and logs a warning, so the
third-party service can never stop people from changing their password.

PASSWORD_BREACH_CHECK=0 turns the check off (the test suite does this, so it
never makes network calls).
"""
import hashlib
import os

import requests

from extensions import app_log

_RANGE_URL = "https://api.pwnedpasswords.com/range/"
_TIMEOUT_SECONDS = 3


def breach_check_enabled() -> bool:
    return os.environ.get("PASSWORD_BREACH_CHECK", "1").strip().lower() not in ("0", "false", "off", "no")


def password_is_breached(password: str) -> bool:
    """True if ``password`` is in the breach corpus; False if not, or if the
    check is off or could not be completed."""
    if not password or not breach_check_enabled():
        return False
    # SHA-1 is what the range API is keyed on; it is a lookup key here, not
    # a password hash we store (those are Argon2).
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        resp = requests.get(
            _RANGE_URL + prefix,
            headers={"Add-Padding": "true", "User-Agent": "password-breach-check"},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        app_log.warning("Breached-password check unavailable (%s); allowing the password", type(exc).__name__)
        return False
    if resp.status_code != 200:
        app_log.warning("Breached-password check returned HTTP %s; allowing the password", resp.status_code)
        return False
    for line in resp.text.splitlines():
        candidate, _, count = line.strip().partition(":")
        if candidate == suffix:
            # Padding entries carry a count of 0 and are not real breaches.
            try:
                return int(count) > 0
            except ValueError:
                return False
    return False
