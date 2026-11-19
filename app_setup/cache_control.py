# -*- coding: utf-8 -*-
"""Central Cache-Control policy (security review OBS-01, 2026-11).

Every dynamic response is private by default: login, signup, password
recovery and reset pages, every authenticated page and every API response
carry

    Cache-Control: no-store, no-cache, must-revalidate, private
    Pragma: no-cache
    Expires: 0

so neither the browser's back/forward cache, a shared proxy nor a CDN keeps
a copy of a page holding user or tenant data, a CSRF token or a reset link.

The policy is an allow-list of what MAY be cached, not a list of what may
not. A new login, reset or data route is covered without anyone remembering
to add it here. Only these stay cacheable:

  * /static/...      CSS, JS, fonts and images (cached for an hour by
                     extensions.py's SEND_FILE_MAX_AGE_DEFAULT; tenant-prefixed
                     /<slug>/static/... arrives here with the slug stripped);
  * /favicon.ico, /robots.txt, /sitemap.xml and /.well-known/security.txt,
    which hold no user data and set their own Cache-Control.

Even the public landing page is not cacheable: _inject_csrf_meta writes a
per-session CSRF token into every HTML page.

There is no CDN in front of the site, and nginx only caches /static/ (see
nginx.conf, nginx/nginx.conf.template). Keep it that way for these paths.
"""
from flask import request

from extensions import app

NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
}

_CACHEABLE_PREFIXES = ("/static/",)
_CACHEABLE_PATHS = frozenset({
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/.well-known/security.txt",
})


def is_cacheable_path(path):
    """True for the few public, data-free paths a cache may keep."""
    return path in _CACHEABLE_PATHS or path.startswith(_CACHEABLE_PREFIXES)


@app.after_request
def _no_store_private_responses(response):
    if is_cacheable_path(request.path):
        return response
    for name, value in NO_STORE_HEADERS.items():
        response.headers[name] = value
    return response
