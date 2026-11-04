"""Templates must not send developer comments to the browser.

An HTML comment (<!-- ... -->) is part of every response; a Jinja comment
({# ... #}) is dropped when the template renders. Developer notes in HTML
comments describe internals (file names, routes, how signup works) to anyone
who views the source, and ZAP reports them (rule 10027, CWE-615). Write notes
as {# ... #}.

Allowed: comments inside <script>/<style> (JavaScript-built markup, CSS) and
IE conditional comments.
"""
import pathlib
import re

from jinja2 import Environment

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"
_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
_CODE_BLOCK = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def _html_comments(text):
    blocks = [(m.start(), m.end()) for m in _CODE_BLOCK.finditer(text)]
    for m in _COMMENT.finditer(text):
        if any(a < m.start() < b for a, b in blocks) or m.group(1).lstrip().startswith(("[if", "<![")):
            continue
        yield text.count("\n", 0, m.start()) + 1, m.group(1).strip()[:60]


def test_templates_use_jinja_comments_not_html_comments():
    found = [f"{p.relative_to(TEMPLATES)}:{line}: {snippet!r}"
             for p in sorted(TEMPLATES.rglob("*.html"))
             for line, snippet in _html_comments(p.read_text(encoding="utf-8"))]
    assert not found, "Use {# ... #} instead of <!-- ... --> (sent to every visitor):\n" + "\n".join(found)


def test_every_template_still_parses():
    env = Environment()
    for p in TEMPLATES.rglob("*.html"):
        env.parse(p.read_text(encoding="utf-8"))   # raises TemplateSyntaxError on a broken comment
