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
