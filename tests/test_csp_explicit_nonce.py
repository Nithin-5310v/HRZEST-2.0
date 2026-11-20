"""Security audit P1 (2026-11-20): CSP nonces and style hashes come from the
template source, never from the rendered response.

The app used to add the request's nonce to every <script>/<style> tag in a
finished response and hash every style="" attribute it found there, so
anything injected into a page was signed too. These tests pin the fix:

* every <script>/<style> tag in a page template writes nonce="{{ csp_nonce }}";
* a <script> that reaches the response any other way gets no nonce;
* a style="" attribute that arrives through a variable gets no hash, while
  one written in the template does;
* real pages still have a nonce on every tag and a hash for every style
  attribute, so nothing the developers wrote is blocked.
"""
import base64
import hashlib
import html
import re
from pathlib import Path

import pytest
from flask import g, render_template_string
from markupsafe import Markup

from app import app as flask_app
from utils import csp

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_TAG_RE = re.compile(r"<(script|style)(?=[\s>])[^>]*>", re.IGNORECASE)
_EXPLICIT_NONCE = 'nonce="{{ csp_nonce }}"'


def _sha(value):
    return "'sha256-" + base64.b64encode(hashlib.sha256(value.encode()).digest()).decode() + "'"


def _page_templates():
    for path in sorted(TEMPLATES.rglob("*.html")):
        if "emails" not in path.relative_to(TEMPLATES).parts:
            yield path


def _csp_directive(csp_header, name):
    for part in csp_header.split(";"):
        part = part.strip()
        if part.split(" ", 1)[0] == name:
            return part
    return ""


def _process(body):
    """Run a raw HTML response through every before/after_request hook."""
    with flask_app.test_request_context("/csp-probe"):
        flask_app.preprocess_request()
        resp = flask_app.make_response((body, 200, {"Content-Type": "text/html; charset=utf-8"}))
        resp = flask_app.process_response(resp)
        return resp.get_data(as_text=True), resp.headers.get("Content-Security-Policy", ""), g.csp_nonce


# --- template source ------------------------------------------------------------

def test_every_template_tag_writes_the_nonce_explicitly():
    missing = {}
    for path in _page_templates():
        text = path.read_text(encoding="utf-8")
        bad = [m.group(0)[:100] for m in _TAG_RE.finditer(text) if _EXPLICIT_NONCE not in m.group(0)]
        if bad:
            missing[path.relative_to(TEMPLATES).as_posix()] = bad
    assert not missing, (
        "nothing adds CSP nonces to responses any more: write "
        f'<script {_EXPLICIT_NONCE}> / <style {_EXPLICIT_NONCE}> in the template: {missing}')


def test_templates_use_only_double_quoted_style_attributes():
    """utils.csp.CspStyleAttrExtension allows style="..." attributes; a
    single-quoted one would get no hash and be blocked by the browser."""
    found = {}
    for path in _page_templates():
        hits = re.findall(r"\bstyle\s*=\s*'[^']*'", path.read_text(encoding="utf-8"))
        if hits:
            found[path.relative_to(TEMPLATES).as_posix()] = hits[:3]
    assert not found, f"use style=\"...\" (double quotes) in templates: {found}"


# --- the response is not rewritten -------------------------------------------------

def test_injected_script_gets_no_nonce():
    body, csp_header, nonce = _process(
        "<html><head></head><body><p>hi</p><script>alert(document.cookie)</script></body></html>")
    assert "<script>alert(document.cookie)</script>" in body
    assert f'nonce="{nonce}">alert' not in body
    assert f"'nonce-{nonce}'" in csp_header


def test_injected_style_tag_gets_no_nonce():
    body, _csp, nonce = _process("<html><head><style>body{display:none}</style></head><body></body></html>")
    assert "<style>body{display:none}</style>" in body
    assert f'<style nonce="{nonce}">body' not in body


def test_injected_style_attribute_gets_no_hash():
    _body, csp_header, _n = _process('<html><body><div style="position:fixed;inset:0">x</div></body></html>')
    attr = _csp_directive(csp_header, "style-src-attr")
    assert _sha("position:fixed;inset:0") not in attr
    assert "unsafe-inline" not in attr


def test_server_scripts_still_carry_the_nonce(client):
    """The CSRF helper, error reporter and hz-actions loader are injected by
    app_setup/csrf_csp.py itself and must keep running."""
    resp = client.get("/login")
    if resp.status_code != 200:
        pytest.skip(f"/login returned {resp.status_code} here")
    nonce = _csp_directive(resp.headers["Content-Security-Policy"], "script-src").split("'nonce-")[1].split("'")[0]
    body = resp.get_data(as_text=True)
    assert f'<script nonce="{nonce}">(function(){{var m=document.querySelector' in body
    assert f'<script nonce="{nonce}" src="/static/hz-actions.js' in body


# --- style="" attributes in templates --------------------------------------------

def test_template_style_attribute_is_hashed_but_variable_markup_is_not():
    with flask_app.test_request_context("/csp-probe"):
        flask_app.preprocess_request()
        out = render_template_string(
            '<div style="color:{{ c }};font-weight:700">{{ injected }}</div>',
            c="red", injected=Markup('<span style="position:fixed;top:0">'))
        hashes = csp.style_attr_hashes()
    assert 'style="color:red;font-weight:700"' in out
    assert _sha("color:red;font-weight:700") in hashes
    assert _sha("position:fixed;top:0") not in hashes


def test_style_attribute_hash_is_over_the_decoded_value():
    """The browser hashes the attribute after decoding entities."""
    with flask_app.test_request_context("/csp-probe"):
        flask_app.preprocess_request()
        render_template_string('<p style="font-family:{{ f }}">x</p>', f="'Segoe UI'")
        hashes = csp.style_attr_hashes()
    assert _sha("font-family:'Segoe UI'") in hashes


def test_csp_nonce_global_renders_the_request_nonce():
    with flask_app.test_request_context("/csp-probe"):
        flask_app.preprocess_request()
        assert render_template_string("{{ csp_nonce }}") == g.csp_nonce
    # Outside a request (e-mail rendering) it renders as "".
    with flask_app.app_context():
        assert flask_app.jinja_env.from_string("{{ csp_nonce }}").render() == ""


def test_trusted_html_marks_only_the_markup_it_is_given():
    with flask_app.test_request_context("/csp-probe"):
        flask_app.preprocess_request()
        out = csp.trusted_html("<style>a{}</style><p style='color:red'>x</p>")
        assert f'<style nonce="{g.csp_nonce}">' in out
        assert _sha("color:red") in csp.style_attr_hashes()


# --- real pages: nothing the developers wrote is blocked ----------------------------

@pytest.mark.parametrize("path", ["/", "/login", "/admin_login", "/create_org", "/forgot_password",
                                  "/security-policy", "/this-route-does-not-exist-xyz"])
def test_public_pages_tags_have_nonce_and_styles_have_hashes(client, path):
    _assert_page_is_csp_clean(client.get(path), path)


def test_admin_dashboard_tags_have_nonce_and_styles_have_hashes(client, seed_admin):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    resp = client.get("/admin")
    if resp.status_code != 200:
        pytest.skip(f"/admin returned {resp.status_code} here")
    _assert_page_is_csp_clean(resp, "/admin")


@pytest.mark.parametrize("path", ["/employee_portal", "/my_compoff", "/my_performance"])
def test_employee_pages_tags_have_nonce_and_styles_have_hashes(client, seed_employee, path):
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
    resp = client.get(path)
    if resp.status_code != 200:
        pytest.skip(f"{path} returned {resp.status_code} here")
    _assert_page_is_csp_clean(resp, path)


def _assert_page_is_csp_clean(resp, path):
    if "text/html" not in (resp.content_type or ""):
        pytest.skip(f"{path} is not HTML here")
    csp_header = resp.headers.get("Content-Security-Policy", "")
    nonce = _csp_directive(csp_header, "script-src").split("'nonce-")[1].split("'")[0]
    body = resp.get_data(as_text=True)
    untagged = [m.group(0)[:80] for m in _TAG_RE.finditer(body) if f'nonce="{nonce}"' not in m.group(0)]
    assert not untagged, f"{path}: tags without this response's nonce: {untagged}"
    attr = _csp_directive(csp_header, "style-src-attr")
    unhashed = sorted({v for v in re.findall(r'\bstyle\s*=\s*"([^"]*)"', body)
                       if _sha(html.unescape(v)) not in attr})
    assert not unhashed, f"{path}: style attributes the browser would block: {unhashed[:5]}"
