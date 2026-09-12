#!/usr/bin/env python3
"""Regenerate static/tabler-icons.min.css + static/fonts/tabler-icons.woff(2)
as a subset containing only the glyphs HRZEST actually uses.

Why: the full Tabler Icons 2.47.0 set ships ~5000 glyphs (woff2 alone is
~760KB) loaded on every admin page; this app references under 200 of them.
See vendor/tabler-icons/ for the untouched original release -- that's the
input this script subsets from, so a future icon addition doesn't require
re-downloading Tabler Icons from scratch.

Usage:
    pip install "fonttools[woff]" brotli
    python scripts/regenerate_icon_subset.py

Run this whenever a template/JS file starts using a `ti-<name>` class that
isn't already in the subset -- the app will otherwise silently render a
missing-glyph box for it. If you add a new *dynamic* icon expression (a
Jinja ternary/if-elif building "ti-{{ ... }}", or a JS callsite passing an
icon name as a variable), add every name it can resolve to below in
DYNAMIC_ICON_NAMES -- static grep can't see through those.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_CSS = REPO_ROOT / "vendor" / "tabler-icons" / "tabler-icons-full.css"
VENDOR_WOFF2 = REPO_ROOT / "vendor" / "tabler-icons" / "tabler-icons-full.woff2"
VENDOR_WOFF = REPO_ROOT / "vendor" / "tabler-icons" / "tabler-icons-full.woff"
OUT_CSS = REPO_ROOT / "static" / "tabler-icons.min.css"
OUT_WOFF2 = REPO_ROOT / "static" / "fonts" / "tabler-icons.woff2"
OUT_WOFF = REPO_ROOT / "static" / "fonts" / "tabler-icons.woff"

# Bump this whenever the subset's glyph set changes, to bust any browser/CDN
# cache still holding the old file under the same URL.
FONT_VERSION_QUERY = "v2.47.0-subset1"

SCAN_GLOBS = [
    "templates/*.html",
    "static/*.js",
    "static/js/*.js",
    "blueprints/*.py",
    "utils/*.py",
]

# Icon names reachable only through runtime string-building that static
# grep can't resolve -- found by manually reading every `ti-{{ ... }}` /
# `ti-{% if %}...{% endif %}` expression and every JS call site that passes
# an explicit `icon` argument (e.g. static/toast.js's showToast()).
DYNAMIC_ICON_NAMES = [
    "circle-check", "alert-circle", "info-circle",  # admin_base.html flash rendering
    "circle-x",                                      # create_org*.html flash rendering
    "lock", "users",                                  # employees.html seat-cap icon
    "circle-dashed",                                  # my_onboarding.html status icon
    "check",                                          # onboarding_detail.html status icon
    "clock",                                          # onboarding_detail.html status icon
    "file-check", "file-off",                         # super_admin_application_detail.html
    # admin_base.html's live-stats toast passes icon='clock-alert' as a bare
    # showToast() argument (not literal "ti-clock-alert" text, so plain grep
    # can't see it). This name doesn't exist in Tabler Icons 2.47.0 either --
    # a pre-existing broken icon reference, not something this script fixes.
    # Listed here anyway so it shows up in the "doesn't exist" warning below
    # instead of silently vanishing from view.
    "clock-alert",
]


def find_used_icon_names() -> set[str]:
    used = set(DYNAMIC_ICON_NAMES)
    pattern = re.compile(r"\bti-([a-z][a-z0-9-]*)\b")
    for glob_pat in SCAN_GLOBS:
        for path in REPO_ROOT.glob(glob_pat):
            used.update(pattern.findall(path.read_text(encoding="utf-8")))
    return used


def load_glyph_map(css_text: str) -> dict[str, str]:
    return dict(re.findall(r'\.ti-([a-z0-9-]+):before\{content:"\\([0-9a-fA-F]+)"\}', css_text))


def main() -> None:
    vendor_css = VENDOR_CSS.read_text(encoding="utf-8-sig")
    glyphs = load_glyph_map(vendor_css)
    print(f"Full Tabler Icons set: {len(glyphs)} glyphs")

    used = find_used_icon_names()
    keep = sorted((name, glyphs[name]) for name in used if name in glyphs)
    missing = sorted(name for name in used if name not in glyphs)
    if missing:
        print(f"WARNING: {len(missing)} referenced icon(s) don't exist in Tabler Icons "
              f"2.47.0 -- already broken on the full font too, left as-is: {missing}")
    print(f"Keeping {len(keep)} glyphs actually used")

    from fontTools import subset as ft_subset

    unicodes = ",".join(f"U+{cp}" for _, cp in keep)
    for src, dst, flavor in ((VENDOR_WOFF2, OUT_WOFF2, "woff2"), (VENDOR_WOFF, OUT_WOFF, "woff")):
        ft_subset.main([
            str(src),
            f"--output-file={dst}",
            f"--unicodes={unicodes}",
            f"--flavor={flavor}",
            "--no-hinting",
            "--layout-features=",
            "--glyph-names",
            "--notdef-outline",
        ])

    header_end = vendor_css.index("@font-face")
    base_block = vendor_css[header_end:vendor_css.index("}", vendor_css.index(".ti{")) + 1]
    base_block = base_block.replace("tabler-icons.woff2?v2.47.0", f"tabler-icons.woff2?{FONT_VERSION_QUERY}")
    base_block = base_block.replace('tabler-icons.woff?"', f'tabler-icons.woff?{FONT_VERSION_QUERY}"')
    rules = "".join(f'.ti-{name}:before{{content:"\\{cp}"}}' for name, cp in keep)
    new_css = (
        "/*!\n"
        " * Tabler Icons 2.47.0 by tabler - https://tabler.io\n"
        " * License - https://github.com/tabler/tabler-icons/blob/master/LICENSE\n"
        " * Subsetted (Wave 3 item 19) to the glyphs actually referenced across\n"
        " * HRZEST's templates/static JS -- see scripts/regenerate_icon_subset.py.\n"
        " */" + base_block + rules
    )
    OUT_CSS.write_text(new_css, encoding="utf-8")

    print(f"Wrote {OUT_CSS} ({len(new_css)} bytes, was {len(vendor_css)})")
    print(f"Wrote {OUT_WOFF2} ({OUT_WOFF2.stat().st_size} bytes, was {VENDOR_WOFF2.stat().st_size})")
    print(f"Wrote {OUT_WOFF} ({OUT_WOFF.stat().st_size} bytes, was {VENDOR_WOFF.stat().st_size})")


if __name__ == "__main__":
    main()
