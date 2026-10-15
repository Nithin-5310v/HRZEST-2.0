"""Filter vulture's "unused function" output down to real dead-code candidates.

vulture can't see Flask's decorator-based registration, so every route,
before/after_request hook, error handler, template filter/global and
scheduler job looks unused to it. This drops any function that:
  - carries a registering decorator, or
  - is referenced by name anywhere else in the repo (Python, templates, JS,
    tests) other than its own `def` line.
What's left is printed as "candidate" -- still to be confirmed by hand.

Usage: python qa_audit/tools/vulture_filter.py qa_audit/baseline/vulture.txt [--exclude-tests]
  --exclude-tests  don't count references from tests/ -- finds code only tests still call
"""
import pathlib
import re
import sys

REGISTERING = re.compile(
    r"@\s*[\w.]*\.(route|get|post|put|delete|patch|before_request|after_request|before_app_request|"
    r"errorhandler|app_errorhandler|context_processor|app_context_processor|template_filter|"
    r"app_template_filter|template_global|teardown_request|teardown_appcontext|cli\.command|"
    r"command|task|limit|exempt)\b"
)
ROOT = pathlib.Path(".")
SKIP_DIRS = {".venv_test", ".venv", "venv", "node_modules", "hashi", "qa_audit", ".git", "__pycache__", "dataset"}


def iter_source_files():
    for p in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix in {".py", ".html", ".js", ".jsx", ".txt", ".yml", ".yaml", ".sh"} and p.is_file():
            yield p


def main(vulture_out):
    corpus = {}
    for p in iter_source_files():
        try:
            corpus[p] = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
    pat = re.compile(r"^(.+?):(\d+): unused function '(\w+)' \((\d+)% confidence\)")
    candidates = []
    for line in pathlib.Path(vulture_out).read_text(encoding="utf-8", errors="ignore").splitlines():
        m = pat.match(line)
        if not m:
            continue
        path, lineno, name = m.group(1), int(m.group(2)), m.group(3)
        src = pathlib.Path(path)
        lines = corpus.get(src) or src.read_text(encoding="utf-8", errors="ignore")
        if isinstance(lines, str):
            lines = lines.splitlines()
        # Look back over the decorator block above the def.
        i = lineno - 2
        decorated = False
        while i >= 0 and lines[i].lstrip().startswith(("@", ")")):
            if REGISTERING.search(lines[i]):
                decorated = True
                break
            i -= 1
        if decorated:
            continue
        # Referenced anywhere else (by bare name, url_for('bp.name'), string, attribute)?
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        refs = 0
        for p, text in corpus.items():
            for hit in word.finditer(text):
                # ignore the def line itself
                if p == src:
                    line_no = text.count("\n", 0, hit.start()) + 1
                    if line_no == lineno:
                        continue
                refs += 1
            if refs:
                break
        if refs:
            continue
        candidates.append(f"{path}:{lineno}: {name}")
    print("\n".join(candidates))
    print(f"\n{len(candidates)} candidate(s)", file=sys.stderr)


if __name__ == "__main__":
    if "--exclude-tests" in sys.argv:
        SKIP_DIRS.update({"tests", "tests_e2e"})
    main(sys.argv[1])
