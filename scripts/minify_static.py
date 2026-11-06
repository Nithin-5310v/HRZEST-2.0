"""Regenerate static/*.min.{css,js} from their first-party source files.

Run this after editing static/shared.css or static/darkmode.js:
    pip install -r requirements-dev.txt
    python scripts/minify_static.py

Only minifies first-party source — vendored libraries under static/ (chart,
jsQR, tabler-icons) already ship pre-minified upstream.
"""
import os
import sys

# Run from anywhere: work relative to the repo root (this file lives in scripts/).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import rcssmin  # noqa: E402
import rjsmin  # noqa: E402

_TARGETS = [
    ("static/shared.css", "static/shared.min.css", rcssmin.cssmin),
    ("static/darkmode.js", "static/darkmode.min.js", rjsmin.jsmin),
    ("static/animate-core.css", "static/animate-core.min.css", rcssmin.cssmin),
    ("static/animate-core.js", "static/animate-core.min.js", rjsmin.jsmin),
]

if __name__ == "__main__":
    for src_path, out_path, minify in _TARGETS:
        if not os.path.exists(src_path):
            print(f"Skipping {src_path} (source file not present)")
            continue
        with open(src_path, encoding="utf-8") as f:
            source = f.read()
        minified = minify(source)
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(minified)
        before, after = len(source.encode()), len(minified.encode())
        print(f"{src_path} -> {out_path}: {before:,}B -> {after:,}B "
              f"({100 * (1 - after / before):.0f}% smaller)")
