# -*- coding: utf-8 -*-
"""Strip secrets out of URLs before they are written to access logs.

Security review 2026-11-20: password-reset, mobile bridge and offer-letter
links carry a one-time token in the URL path or query string. gunicorn's
access log (and the Referer of the next request) recorded those URLs in
full, and the logs are shipped on to the log server. gunicorn.conf.py's
RedactingLogger passes the request line, path, query and Referer through
redact_url().
"""
import re

# /<route>/<token>[/...]: the token segment becomes [redacted].
_PATH_TOKEN_RE = re.compile(
    r"(/(?:admin_reset_password|employee_reset_password|mobile_bridge_login|"
    r"offer_letter_pdf|offer_letter_respond)/)[^/?#\s\"]+",
    re.IGNORECASE,
)
# ?token=...&access_token=...&code=...: the value becomes [redacted].
_QUERY_TOKEN_RE = re.compile(
    r"([?&](?:token|access_token|reset_token|code|otp|state|password)=)[^&#\s\"]*",
    re.IGNORECASE,
)


def redact_url(text):
    """Return ``text`` with token path segments and secret query values
    replaced by ``[redacted]``. Non-strings come back unchanged."""
    if not isinstance(text, str) or not text:
        return text
    text = _PATH_TOKEN_RE.sub(r"\1[redacted]", text)
    return _QUERY_TOKEN_RE.sub(r"\1[redacted]", text)
