# -*- coding: utf-8 -*-
"""www -> apex redirect at the WSGI layer (security review OBS-09, 2026-11).

hrzest.com is the canonical host. The redirect belongs in nginx (see
SECURITY_REMEDIATION.md, OBS-09), because the live site file isn't in this
repo. This middleware is the backup that ships with a normal deploy: a
request for https://www.<canonical>/... gets a redirect to
https://<canonical>/... with the same path and query string, before any
before_request hook, ban, WAF or tenant-slug handling runs.

  * GET/HEAD -> 301. Any other method -> 308, so a POST stays a POST
    instead of a browser silently turning it into a GET.
  * The canonical host comes from APP_URL (the server's .env has
    https://hrzest.com), never from the request. With no APP_URL, or an
    IP/localhost one (local dev, the dev copy), it does nothing.
  * It sets no headers apart from Location, so the one-place rule for
    security headers is unaffected.
"""
import os
import re
from urllib.parse import urlsplit

from werkzeug.sansio.utils import get_current_url
from werkzeug.urls import iri_to_uri
from werkzeug.utils import redirect

_IP_OR_LOCAL = re.compile(r"^(localhost|[\d.]+|\[?[0-9a-f:]+\]?)$", re.IGNORECASE)


def canonical_origin():
    """(scheme, host) from APP_URL, or None when there's no real domain to enforce."""
    parts = urlsplit(os.environ.get("APP_URL", "").strip())
    host = (parts.hostname or "").lower()
    if not host or _IP_OR_LOCAL.match(host) or host.startswith("www."):
        return None
    return (parts.scheme or "https"), host


def _request_host(environ):
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or ""
    return host.rsplit(":", 1)[0].lower() if not host.startswith("[") else host.lower()


class CanonicalHostMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        origin = canonical_origin()
        if origin and _request_host(environ) == "www." + origin[1]:
            scheme, host = origin
            location = iri_to_uri(get_current_url(
                scheme, host,
                environ.get("SCRIPT_NAME", ""),
                environ.get("PATH_INFO", ""),
                environ.get("QUERY_STRING", "").encode("latin-1"),
            ))
            code = 301 if environ.get("REQUEST_METHOD", "GET") in ("GET", "HEAD") else 308
            return redirect(location, code=code)(environ, start_response)
        return self.wsgi_app(environ, start_response)
