"""Basic accessibility scan of Jinja templates (static HTML heuristics):
  - <img> without an alt attribute
  - form controls (input/select/textarea, excluding hidden/submit/button
    types) with no aria-label/aria-labelledby/title and no <label for=id>
    pointing at them and not wrapped inside a <label>
  - <button> / <a> with no text content and no aria-label (icon-only)
Prints per-template counts, then totals.
Usage: python qa_audit/tools/a11y_scan.py templates
"""
import pathlib
import re
import sys
from html.parser import HTMLParser

SKIP_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}


class Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.img_no_alt = 0
        self.controls = []          # (id, has_aria, inside_label)
        self.label_for = set()
        self.label_depth = 0
        self.iconless = 0
        self._stack = []            # open <button>/<a> tags: [has_aria, text_chars]

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img" and "alt" not in a:
            self.img_no_alt += 1
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag in ("input", "select", "textarea"):
            if tag == "input" and (a.get("type") or "text").lower() in SKIP_INPUT_TYPES:
                return
            has_aria = any(k in a for k in ("aria-label", "aria-labelledby", "title"))
            self.controls.append((a.get("id"), has_aria, self.label_depth > 0))
        if tag in ("button", "a"):
            self._stack.append([any(k in a for k in ("aria-label", "aria-labelledby", "title")), 0])

    def handle_endtag(self, tag):
        if tag == "label" and self.label_depth:
            self.label_depth -= 1
        if tag in ("button", "a") and self._stack:
            has_aria, chars = self._stack.pop()
            if not has_aria and chars == 0:
                self.iconless += 1

    def handle_data(self, data):
        if self._stack and data.strip():
            self._stack[-1][1] += len(data.strip())


def strip_jinja(text):
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = re.sub(r"\{%.*?%\}", "", text, flags=re.S)
    return re.sub(r"\{\{.*?\}\}", "x", text, flags=re.S)


def main(src):
    totals = [0, 0, 0]
    rows = []
    for tpl in sorted(pathlib.Path(src).rglob("*.html")):
        s = Scan()
        try:
            s.feed(strip_jinja(tpl.read_text(encoding="utf-8", errors="ignore")))
        except Exception as exc:  # malformed markup -- report and continue
            rows.append(f"{tpl}: parse error {exc}")
            continue
        unlabeled = sum(1 for cid, aria, inside in s.controls if not aria and not inside and (not cid or cid not in s.label_for))
        if s.img_no_alt or unlabeled or s.iconless:
            rows.append(f"{tpl.as_posix():60s} img-no-alt={s.img_no_alt:3d}  unlabeled-controls={unlabeled:3d}  "
                        f"empty-buttons/links={s.iconless:3d}")
        totals[0] += s.img_no_alt
        totals[1] += unlabeled
        totals[2] += s.iconless
    print("\n".join(rows))
    print(f"\nTOTAL img-no-alt={totals[0]} unlabeled-controls={totals[1]} empty-buttons/links={totals[2]}")


if __name__ == "__main__":
    main(sys.argv[1])
