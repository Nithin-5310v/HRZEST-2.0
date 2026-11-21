# -*- coding: utf-8 -*-
"""Leaked-password check against Have I Been Pwned's Pwned Passwords API.

Used by utils/auth.py's validate_new_password() whenever a password is set
or changed, as NIST SP 800-63B asks: reject passwords already known from
breaches, since those are the ones attackers try first.

k-anonymity: only the first 5 hex characters of the password's SHA-1 leave
this server. The API answers with every hash suffix sharing that prefix
(hundreds of them) and the match is made here, so neither the password nor
its full hash is ever sent. The Add-Padding header makes every answer a
similar size, so the prefix can't be inferred from the response length.

Fails open: if the API is slow or unreachable the password is accepted and
a warning logged -- an outage there must not stop people setting passwords.
PASSWORD_BREACH_CHECK=false turns the lookup off (the test suite, air-gapped
installs).
"""
import hashlib
import os
import urllib.request

from extensions import app_log

_RANGE_URL = "https://api.pwnedpasswords.com/range/"
_TIMEOUT_SECONDS = 3


def breach_check_enabled():
    return os.environ.get("PASSWORD_BREACH_CHECK", "true").strip().lower() in ("true", "1", "yes")


def times_pwned(password):
    """How often the password appears in HIBP's breach corpus: 0 if never,
    None if the lookup couldn't be made (network error, timeout, bad reply)."""
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    req = urllib.request.Request(
        _RANGE_URL + prefix,
        headers={"Add-Padding": "true", "User-Agent": "password-breach-check"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # nosec B310 -- fixed https URL
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        app_log.warning("Pwned Passwords lookup failed, accepting the password: %s", type(exc).__name__)
        return None
    for line in body.splitlines():
        hash_suffix, _, count = line.strip().partition(":")
        if hash_suffix.upper() == suffix:
            try:
                # Padding rows carry a count of 0 -- not a real match.
                return int(count)
            except ValueError:
                return 1
    return 0


def is_pwned_password(password):
    """True only when the check is enabled and HIBP positively knows the
    password. Disabled or unreachable -> False (fail open)."""
    if not password or not breach_check_enabled():
        return False
    return bool(times_pwned(password))
