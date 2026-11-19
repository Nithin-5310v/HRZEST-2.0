"""Random values that a browser or the app sends back must never trip the WAF.

secrets.token_urlsafe() occasionally yields "0x" + hex-range characters
(CI saw a liveness nonce with "0x4DdD"), and utils/waf.py blocks that as
SQL injection -- so a login code, emailed link, OAuth callback or temporary
password would fail at random for a real user. utils/tokens.waf_safe_token
redraws such values; these tests pin that, and guard against a new raw
token_urlsafe() for a value that travels back to us.
"""
import pathlib
import re
import secrets

import pytest

from extensions import app as flask_app
from utils import tokens
from utils.waf import _inspect_value

FLAGGED = "ab0x4DdDcdEFgh0x9fA1zzzzzzzzzzzzzzzzzzzzzzz"  # the CI shape, 43 chars like token_urlsafe(32)


def _draws(monkeypatch, *values):
    """Make secrets.token_urlsafe return these values in order."""
    it = iter(values)
    calls = []

    def fake(n=32):
        calls.append(n)
        return next(it)
    monkeypatch.setattr(tokens.secrets, "token_urlsafe", fake)
    return calls


def test_the_ci_value_really_is_blocked_by_the_waf():
    assert _inspect_value("json.nonce", FLAGGED) is not None


@pytest.mark.parametrize("nbytes", [8, 9, 24, 32])
def test_same_format_and_length_as_token_urlsafe(nbytes):
    for _ in range(50):
        t = tokens.waf_safe_token(nbytes)
        assert len(t) == len(secrets.token_urlsafe(nbytes))
        assert re.fullmatch(r"[A-Za-z0-9_-]+", t)
        assert _inspect_value("token", t) is None


def test_redraws_a_value_the_waf_would_block(monkeypatch):
    clean = "C" * 43
    calls = _draws(monkeypatch, FLAGGED, FLAGGED, clean)
    assert tokens.waf_safe_token(32) == clean
    assert calls == [32, 32, 32]


def test_org_application_access_token_redraws(monkeypatch):
    from blueprints.org.applications import _generate_access_token
    _draws(monkeypatch, FLAGGED, "D" * 43)
    assert _generate_access_token() == "D" * 43


def test_login_mfa_challenge_id_redraws(monkeypatch, db_engine):
    from utils.login_otp import issue_challenge
    _draws(monkeypatch, FLAGGED, "E" * 43)
    with flask_app.test_request_context():
        cur = db_engine.cursor()
        cid, _code = issue_challenge(cur, "admin_users", "waf_token_admin", 300)
        cur.close()
    assert cid == "E" * 43


# Raw token_urlsafe() is fine only for values nobody sends back to us.
_ALLOWED_RAW = (
    "generate_password_hash(",  # random password nobody ever types: only its hash is kept
    "g.csp_nonce",              # goes out in a header and the HTML, never comes back
    "code_verifier",            # PKCE secret: server -> OAuth provider, never via the browser
)


def test_no_raw_token_urlsafe_for_values_sent_back():
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for folder in ("blueprints", "utils", "app_setup"):
        for path in (root / folder).rglob("*.py"):
            if path.name == "tokens.py":
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "token_urlsafe(" in line and not any(a in line for a in _ALLOWED_RAW):
                    offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    assert not offenders, "use utils.tokens.waf_safe_token for values sent back:\n" + "\n".join(offenders)
