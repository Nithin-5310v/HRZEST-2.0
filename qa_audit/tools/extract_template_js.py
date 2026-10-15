"""Extract inline <script> blocks from Jinja templates into standalone .js
files so ESLint can lint them. Jinja is replaced with neutral JS so the
file still parses:
  {{ expr }}           -> null   (inside quotes: left as an empty string)
  {% ... %} / {# #}    -> removed (whole tag)
Each output file is named <template>__<n>.js and starts with a comment
mapping its line 1 back to the template line, so findings can be traced.

Usage: python qa_audit/tools/extract_template_js.py templates qa_audit/baseline/template_js
"""
import pathlib
import re
import sys

SCRIPT_RE = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S | re.I)
EXPR_IN_QUOTES = re.compile(r"(['\"`])([^'\"`\n]*?)\{\{.*?\}\}([^'\"`\n]*?)\1")
EXPR = re.compile(r"\{\{.*?\}\}", re.S)
STMT = re.compile(r"\{%-?.*?-?%\}", re.S)
COMMENT = re.compile(r"\{#.*?#\}", re.S)


def neutralise(js):
    js = COMMENT.sub("", js)
    js = STMT.sub("", js)
    # Expressions inside string literals: keep the literal, drop the expression.
    prev = None
    while prev != js:
        prev = js
        js = EXPR_IN_QUOTES.sub(lambda m: m.group(1) + m.group(2) + m.group(3) + m.group(1), js)
    return EXPR.sub("null", js)


def main(src_dir, out_dir):
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for tpl in sorted(pathlib.Path(src_dir).rglob("*.html")):
        text = tpl.read_text(encoding="utf-8", errors="ignore")
        for i, m in enumerate(SCRIPT_RE.finditer(text)):
            attrs = m.group("attrs").lower()
            if "src=" in attrs or "application/ld+json" in attrs or "text/template" in attrs:
                continue
            body = m.group("body")
            if not body.strip():
                continue
            start_line = text.count("\n", 0, m.start("body")) + 1
            name = f"{tpl.stem}__{i}.js"
            (out / name).write_text(
                f"/* source: {tpl.as_posix()} line {start_line} */\n" + neutralise(body),
                encoding="utf-8",
            )
            count += 1
    print(f"extracted {count} inline script block(s)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
