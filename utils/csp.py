# -*- coding: utf-8 -*-
"""Content-Security-Policy helpers: the per-request nonce and the inline
style="" hashes that style-src-attr allows.

Security audit P1 (2026-11-20). The app used to add the request's nonce to
*every* <script>/<style> tag in a finished response, and to hash *every*
style="" attribute it found there. Anything an attacker got into the page
(one stray ``|safe`` would do) was then signed with a valid nonce or hash,
so the CSP stopped nothing and only Jinja's autoescaping protected the page.

Now only markup the developers wrote gets through:

* **<script>/<style> tags** carry ``nonce="{{ csp_nonce }}"`` written in the
  template source. tests/test_csp_explicit_nonce.py fails CI for any tag
  without one. Nothing adds nonces to a rendered response any more.
* **style="" attributes** are hashed while the template is rendered, from the
  template's own source text. ``CspStyleAttrExtension`` wraps each attribute
  that is written in a template file in a ``{% filter csp_style_attr %}``
  block when the template is compiled. Markup that arrives through a variable
  never passes through that filter, so its styles get no hash and the browser
  blocks them.
* **HTML built in Python** (a few error pages and printable reports) goes
  through ``trusted_html()``, which the developer calls explicitly on markup
  the server assembled itself, with every interpolated value escaped. It
  stamps only <style> tags; a <script> in such a page never gets a nonce.
"""
import base64
import hashlib
import html as _html
import re

from flask import g, has_request_context
from jinja2.ext import Extension
from markupsafe import Markup

# A style="" attribute in template *source*. Single-quoted style='' attributes
# are not used in templates (the test suite checks), so only the double-quoted
# form is rewritten.
_SRC_STYLE_ATTR_RE = re.compile(r'(\bstyle\s*=\s*")([^"]*)(")', re.IGNORECASE)

# For trusted_html(): <style> tags without a nonce, and style attributes in
# either quote style. <script> is deliberately NOT matched: none of the
# Python-built pages needs an inline script, so a <script> that slipped into
# one through an unescaped value must stay blocked (security review
# 2026-11-20 found exactly that path in the attendance report).
_TAG_WITHOUT_NONCE_RE = re.compile(r'<(style)(?![^>]*\bnonce\s*=)(?=[\s>])', re.IGNORECASE)
_STYLE_ATTR_DQ_RE = re.compile(r'\bstyle\s*=\s*"([^"]*)"', re.IGNORECASE)
_STYLE_ATTR_SQ_RE = re.compile(r"\bstyle\s*=\s*'([^']*)'", re.IGNORECASE)


def csp_nonce():
    """This request's CSP nonce, or "" outside a request (e-mail rendering)."""
    if not has_request_context():
        return ""
    return getattr(g, "csp_nonce", "") or ""


def _sha256_source(value):
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode() + "'"


def allow_style_attr(value):
    """Allow one inline style="" value on this response.

    ``value`` is the attribute as written in the HTML. The browser hashes the
    value after decoding entities, so it is unescaped here first.
    """
    if not has_request_context():
        return
    hashes = getattr(g, "csp_style_attr_hashes", None)
    if hashes is None:
        hashes = g.csp_style_attr_hashes = set()
    hashes.add(_sha256_source(_html.unescape(str(value))))


def style_attr_hashes():
    """The style-src-attr hashes registered while this response was built."""
    if not has_request_context():
        return set()
    return set(getattr(g, "csp_style_attr_hashes", ()) or ())


def csp_style_attr_filter(value):
    """Jinja filter behind ``{% filter csp_style_attr %}``: allow the
    rendered attribute value and return it unchanged.

    The block's body arrives as Markup (Jinja has already escaped every
    variable in it), so it is returned as Markup to avoid escaping it twice.
    """
    allow_style_attr(value)
    return Markup(value)


class CspStyleAttrExtension(Extension):
    """Wrap every style="" attribute in template source in
    ``{% filter csp_style_attr %}`` when the template is compiled.

    This runs on the template file's text, which developers wrote, never on
    rendered output. Attributes inside a <script> block's JS strings are
    wrapped too; the rendered text is identical, and their hash is what lets
    that markup keep its style once the script inserts it.
    """

    def preprocess(self, source, name, filename=None):
        return _SRC_STYLE_ATTR_RE.sub(
            lambda m: (m.group(1) + "{% filter csp_style_attr %}" + m.group(2)
                       + "{% endfilter %}" + m.group(3)),
            source)


def trusted_html(markup):
    """Apply this response's CSP to HTML that server code assembled itself.

    Call it only on markup built from fixed strings plus values that have
    already been escaped (``html.escape``/``markupsafe.escape``). It adds the
    nonce to its <style> tags (never to <script>: these pages have none, so
    any script in them is an injection and stays blocked) and allows the
    style="" attributes in it.
    """
    text = str(markup)
    for pattern in (_STYLE_ATTR_DQ_RE, _STYLE_ATTR_SQ_RE):
        for match in pattern.finditer(text):
            allow_style_attr(match.group(1))
    nonce = csp_nonce()
    if nonce:
        text = _TAG_WITHOUT_NONCE_RE.sub(lambda m: f'<{m.group(1)} nonce="{nonce}"', text)
    return text
