"""Random tokens that a browser or the app sends back to us.

secrets.token_urlsafe() draws from A-Za-z0-9-_, so now and then a value
contains "0x" followed by hex-range characters (e.g. "0x4DdD"). The WAF's
SQL-injection rule (utils/waf.py, "0x[0-9a-fA-F]{4,}") then blocks the
request that carries it back -- a login code, an emailed link, an OAuth
callback or a temporary password failing at random for a real user. CI hit
exactly this with the liveness nonce (fixed separately with token_hex).

waf_safe_token() keeps token_urlsafe's format and length, so stored hashes,
column sizes, length checks and link shapes are unchanged, and redraws in
the rare case the WAF would flag the value. Checking against the WAF itself
(rather than hard-coding "no 0x") keeps this right if a signature is added.
The redraw removes a vanishing fraction of values, so the strength is
effectively the same as token_urlsafe(nbytes).
"""
import secrets

_MAX_DRAWS = 100  # a flagged draw is ~1 in 10,000; 100 in a row never happens


def waf_safe_token(nbytes=32):
    from utils.waf import _inspect_value  # lazy: utils.waf imports the app's extensions
    for _ in range(_MAX_DRAWS):
        token = secrets.token_urlsafe(nbytes)
        if _inspect_value("token", token) is None:
            return token
    raise RuntimeError("could not draw a WAF-safe token")  # pragma: no cover
