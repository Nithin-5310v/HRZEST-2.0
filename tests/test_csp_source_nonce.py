"""The CSP nonce goes only on the <script>/<style> tags we wrote.

app_setup/templating.py's CspNonceExtension adds it to the tags in a
template's source when the template is compiled; Python-built pages add it
with utils.helpers.csp_nonce_attr(). Nothing stamps it onto the finished
response any more: that used to give an injected <script> a valid nonce
too, so the CSP stopped nothing. These tests pin both halves -- our tags
carry the response's nonce, injected ones don't.
"""
import re

import pytest
from flask import g, render_template_string
from markupsafe import Markup

from extensions import app as flask_app

_OPEN_TAG_RE = re.compile(r"<(?:script|style)\b[^>]*>", re.IGNORECASE)
_PAYLOAD = "<script>alert(1)</script><style>body{display:none}</style>"


def _render(source, **context):
    with flask_app.test_request_context():
        g.csp_nonce = "test-nonce-123"
        return render_template_string(source, **context)


def _response_nonce(resp):
    match = re.search(r"'nonce-([^']+)'", resp.headers.get("Content-Security-Policy", ""))
    assert match, "HTML responses must carry a nonce-based CSP"
    return match.group(1)


def test_tags_in_template_source_get_the_nonce():
    html = _render('<script>var a = 1;</script><style>p{}</style><script src="/x.js" defer></script>')
    tags = _OPEN_TAG_RE.findall(html)
    assert len(tags) == 3
    assert all('nonce="test-nonce-123"' in tag for tag in tags), tags


def test_markup_arriving_through_a_variable_gets_no_nonce():
    """The whole point: a stored-XSS payload that slips past escaping (a
    |safe filter, a Markup() value) must stay blocked by the browser."""
    html = _render("<p>{{ a|safe }}</p><p>{{ b }}</p>", a=_PAYLOAD, b=Markup(_PAYLOAD))
    assert html.count("<script>alert(1)</script>") == 2
    assert html.count("<style>body{display:none}</style>") == 2
    assert "nonce" not in html


def test_a_tag_that_already_has_a_nonce_is_left_alone():
    html = _render('<script nonce="{{ csp_nonce() }}">x</script>')
    assert html == '<script nonce="test-nonce-123">x</script>'


def test_only_html_templates_are_rewritten():
    from app_setup.templating import CspNonceExtension
    ext = CspNonceExtension(flask_app.jinja_env)
    assert ext.preprocess("<script>", "mail_body.txt") == "<script>"
    assert ext.preprocess("<script>", "page.html") == '<script nonce="{{ csp_nonce() }}">'
    # render_template_string() passes no name -- those are HTML too.
    assert ext.preprocess("<style>", None) == '<style nonce="{{ csp_nonce() }}">'


def test_outside_a_request_the_nonce_is_empty():
    """A template rendered with no request (a background job's email body):
    no nonce, no error. Straight through the Jinja environment -- the app's
    context processors need a request, the nonce extension doesn't."""
    with flask_app.app_context():
        html = flask_app.jinja_env.from_string("<style>p{}</style>").render()
    assert html == '<style nonce="">p{}</style>'


def test_response_processing_does_not_bless_injected_tags():
    """Regression: an after_request pass stamped the nonce onto every
    <script>/<style> in the finished HTML, so markup injected into a page
    ran as if we had written it. Our own appended scripts still get it."""
    from flask import Response
    page = f"<html><head></head><body><p>{_PAYLOAD}</p></body></html>"
    with flask_app.test_request_context("/"):
        g.csp_nonce = "test-nonce-123"
        resp = flask_app.process_response(Response(page, mimetype="text/html"))
        html = resp.get_data(as_text=True)
    assert "<script>alert(1)</script>" in html
    assert "<style>body{display:none}</style>" in html
    appended = [t for t in _OPEN_TAG_RE.findall(html) if t not in ("<script>", "<style>")]
    assert appended, "the CSRF/client-log scripts should have been appended"
    assert all('nonce="test-nonce-123"' in t for t in appended), appended


@pytest.mark.parametrize("path", ["/", "/login", "/signup"])
def test_every_tag_on_a_real_page_carries_the_response_nonce(client, path):
    """Template tags plus the CSRF/client-log/hz-actions scripts
    _inject_csrf_meta appends -- all ours, so all must carry this
    response's nonce (it differs per response)."""
    resp = client.get(path)
    if resp.status_code != 200:
        pytest.skip(f"{path} returned {resp.status_code} here")
    nonce = _response_nonce(resp)
    tags = _OPEN_TAG_RE.findall(resp.get_data(as_text=True))
    assert tags
    missing = [tag for tag in tags if f'nonce="{nonce}"' not in tag]
    assert not missing, missing


def test_python_built_page_carries_the_response_nonce(client, seed_employee):
    """my_attendance_pdf is an f-string page, not a template (csp_nonce_attr)."""
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    resp = client.get("/my_attendance_pdf")
    assert resp.status_code == 200
    assert f'<style nonce="{_response_nonce(resp)}">' in resp.get_data(as_text=True)
