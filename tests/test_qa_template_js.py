"""JS-03: the settings page's tab metadata has no duplicate keys.

crumbData in templates/settings.html defined `company` twice; in a JS
object literal the last one silently wins, so the first entry was dead and
the two disagreed about the tab's title. No database needed (runnable with
--noconftest).
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _object_keys(source, name):
    body = re.search(rf"var {name}\s*=\s*\{{(.*?)\n\s*\}};", source, re.S).group(1)
    return re.findall(r"^\s*([A-Za-z_]\w*)\s*:", body, re.M)


def test_settings_tab_metadata_keys_are_unique():
    html = (ROOT / "templates" / "settings.html").read_text(encoding="utf-8")
    keys = _object_keys(html, "crumbData")
    assert len(keys) == len(set(keys)), sorted(k for k in keys if keys.count(k) > 1)
    assert "company" in keys


# ── SEC-02: untrusted text never goes through innerHTML or an inline handler ──

def _function_body(source, name):
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}", start) if "\n}" in source[start:] else len(source)]


def test_onboarding_task_title_is_not_interpolated_into_onclick_or_innerhtml():
    html = (ROOT / "templates" / "my_onboarding.html").read_text(encoding="utf-8")
    button = next(line for line in html.splitlines() if "openDoneModal(" in line and "onclick" in line)
    onclick = re.search(r'onclick="([^"]*)"', button).group(1)
    assert "t[1]" not in onclick                     # the title isn't spliced into JS source
    assert 'data-title="{{ t[1] }}"' in html          # it travels as an auto-escaped attribute
    body = _function_body(html, "openDoneModal")
    assert "+ title" not in body and "createTextNode(title)" in body
