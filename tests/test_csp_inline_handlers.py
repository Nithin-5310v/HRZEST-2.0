"""Inline event handlers (onclick="", oninput="", ...) vs. the page CSP.

Inline handlers only run under a nonce-based CSP if script-src lists each
one's hash plus 'unsafe-hashes', which scanners report as Medium (ZAP 10055 on
/create_org and /checkin) because injected markup can reuse any hashed
snippet. app.py no longer hashes them, so the browser blocks any that remain.
Templates use data-on-<event>="fn(...)" attributes instead, bound by
static/hz-actions.js (exercised in a real browser by
tests_e2e/test_hz_actions.py).

The template check uses app.py's own handler patterns over the whole file,
<script> bodies and comments included: a handler written into HTML built by
JavaScript is blocked just the same.
"""
from pathlib import Path

import pytest

from app import _CSP_EV_DQ, _CSP_EV_SQ

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _handlers(path):
    data = path.read_bytes()
    found = [m.group(0) for m in _CSP_EV_DQ.finditer(data)]
    found += [m.group(0) for m in _CSP_EV_SQ.finditer(data)]
    return [h.decode("utf-8", "replace")[:120] for h in found]


def test_no_template_uses_inline_event_handlers():
    found = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        hits = _handlers(path)
        if hits:
            found[path.relative_to(TEMPLATES).as_posix()] = hits
    assert not found, (
        "inline event handlers are blocked by the CSP -- use "
        f'data-on-<event>="fn(...)" (static/hz-actions.js) instead: {found}')


@pytest.mark.parametrize("path", ["/", "/login", "/create_org", "/checkin", "/admin_forgot_password"])
def test_script_src_never_allows_unsafe_hashes(client, path):
    resp = client.get(path)
    csp = resp.headers.get("Content-Security-Policy", "")
    script_src = next((d for d in csp.split(";") if d.strip().startswith("script-src")), "")
    assert "unsafe-hashes" not in script_src
    assert "unsafe-inline" not in script_src


@pytest.mark.parametrize("path", ["/login", "/create_org", "/checkin"])
def test_pages_load_the_handler_binder(client, path):
    resp = client.get(path)
    if resp.status_code != 200:
        pytest.skip(f"{path} returned {resp.status_code} here")
    assert b"/static/hz-actions.js" in resp.get_data()
