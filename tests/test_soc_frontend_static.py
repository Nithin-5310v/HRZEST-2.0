"""SOC console front end: mechanical safety and consistency checks (no database, no browser).

The console renders attacker-controlled strings (paths, accounts, user agents, log lines), so
the rules that keep it XSS-safe are enforced here rather than left to code review.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = ROOT / "soc_ui"
TPL = ROOT / "templates" / "soc"
BLUEPRINT = ROOT / "blueprints" / "soc.py"
JS_FILES = sorted(UI.glob("*.js"))


def _read(p):
    return p.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_js_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# markup sinks / dynamic code. DOM text APIs (textContent, createTextNode, append) are the only way
# data may reach the page.
_SINKS = re.compile(r"\.(?:innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write|\beval\s*\(|new\s+Function\s*\("
                    r"|setTimeout\s*\(\s*['\"`]|setInterval\s*\(\s*['\"`]|\.srcdoc\b|javascript:", re.I)


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_js_has_no_markup_sinks(path):
    hits = [(i, line.strip()) for i, line in enumerate(_read(path).splitlines(), 1)
            if _SINKS.search(re.sub(r"//.*$", "", line))]
    assert hits == [], f"{path.name}: untrusted data could become markup: {hits}"


def test_js_never_builds_urls_from_untrusted_hrefs():
    """Every href built from data is either an in-app '#/...' hash, a mailto-free ATT&CK link
    validated by regex, or a download blob. A plain assignment to location from data would be a
    redirect sink."""
    for p in JS_FILES:
        for i, line in enumerate(_read(p).splitlines(), 1):
            if re.search(r"location(?:\.href)?\s*=\s*(?![\"'#/])\S", line) and "location.hash" not in line:
                pytest.fail(f"{p.name}:{i} assigns a non-literal to location: {line.strip()}")


TEMPLATES = sorted(TPL.glob("*.html"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_templates_are_csp_clean(path):
    """The app's CSP (nonce + hashes) is strict; these templates must need none of the exceptions."""
    html = _read(path)
    assert not re.search(r"\son[a-z]+\s*=", html, re.I), "inline event handler"
    assert not re.search(r"\sstyle\s*=", html, re.I), "inline style attribute"
    assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", html, re.I), "inline <script>"
    assert not re.search(r"<style", html, re.I), "inline <style>"
    assert "javascript:" not in html.lower()
    external = re.findall(r"(?:src|href|action)=\"(https?://[^\"]+)", html)
    assert external == [], f"external resources: {external}"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_templates_are_noindex_and_unlinked_from_search(path):
    assert 'name="robots"' in _read(path) and "noindex" in _read(path)


def test_every_form_post_carries_a_csrf_token():
    for path in TEMPLATES:
        html = _read(path)
        for form in re.findall(r"<form[^>]*method=\"post\"[^>]*>.*?</form>", html, re.S | re.I):
            assert "_csrf_token" in form, f"{path.name}: POST form without CSRF token"


def _blueprint_assets():
    src = _read(BLUEPRINT)
    pub = set(re.findall(r"[\"']([\w.-]+\.(?:css|js))[\"']", re.search(r"_PUBLIC_ASSETS = frozenset\(\{(.*?)\}\)", src, re.S).group(1)))
    priv = set(re.findall(r"[\"']([\w.-]+\.(?:css|js))[\"']", re.search(r"_PRIVATE_ASSETS = frozenset\(\{(.*?)\}\)", src, re.S).group(1)))
    return pub, priv


def test_assets_are_exactly_the_whitelisted_files():
    pub, priv = _blueprint_assets()
    on_disk = {p.name for p in UI.iterdir() if p.is_file()}
    assert on_disk == pub | priv, f"on disk only: {sorted(on_disk - pub - priv)}; whitelisted only: {sorted((pub | priv) - on_disk)}"
    assert pub == {"login.css"}, "only the login stylesheet may be served without a session"


def test_every_resource_the_templates_load_exists_and_is_served():
    pub, priv = _blueprint_assets()
    for path in TEMPLATES:
        for url in re.findall(r"(?:src|href)=\"(/[^\"#?]+)", _read(path)):
            if url.startswith("/soc/assets/"):
                assert url.rsplit("/", 1)[1] in pub | priv, f"{path.name}: {url} is not whitelisted"
            elif url.startswith("/static/"):
                assert (ROOT / url.lstrip("/")).exists(), f"{path.name}: {url} missing"
            elif url.startswith("/soc/"):
                pass        # page routes (login) are checked by the route tests
            else:
                pytest.fail(f"{path.name}: unexpected resource {url}")


def _route_regexes():
    rx = []
    for rule in re.findall(r"@soc_bp\.route\(\"(/soc/[^\"]+)\"", _read(BLUEPRINT)):
        parts = re.split(r"(<[^>]+>)", rule)
        rx.append(re.compile("^" + "".join("[^/]+" if x.startswith("<") else re.escape(x) for x in parts) + "$"))
    return rx


def test_every_api_url_the_js_calls_is_a_real_route():
    routes = _route_regexes()
    assert routes
    seen = set()
    for p in JS_FILES:
        for url in re.findall(r"['\"`](/soc/api/[^'\"`?]*)", _read(p)):
            probe = re.sub(r"\$\{[^}]*\}", "1", url)
            probe = probe + "1" if probe.endswith("/") else probe           # '/soc/api/cases/' + id
            seen.add(url)
            assert any(r.match(probe) for r in routes), f"{p.name}: {url} has no matching route"
    assert len(seen) >= 20, "expected the console to call the whole API"


def test_every_nav_entry_is_a_registered_view():
    core = _read(UI / "soc-core.js")
    nav = set(re.findall(r"'(\w+)'", re.search(r"var GROUPS = \[(.*?)\];", core, re.S).group(1))) - {
        "Monitor", "Investigate", "Respond", "Assess", "Administer"}
    registered = set()
    for p in JS_FILES:
        src = _read(p)
        registered |= set(re.findall(r"SOC\.register\(\{\s*id:\s*'(\w+)'", src))
        registered |= set(re.findall(r"eventsView\('(\w+)'", src))
    assert nav <= registered, f"nav lists views that do not exist: {sorted(nav - registered)}"
    assert registered <= nav | {"entity"} | nav, sorted(registered - nav)


def test_nothing_outside_the_console_links_to_it():
    """The console is reached by typing its URL. No other page, script or app screen may link to /soc."""
    link = re.compile(r"""(?:href|src|action)\s*=\s*["']/soc\b|["'`]/soc(?:/[^"'`]*)?["'`]\s*[,)]|window\.open\([^)]*/soc""")
    offenders = []
    candidates = list((ROOT / "templates").rglob("*.html")) + list((ROOT / "static").rglob("*.js")) + \
        list((ROOT / "static").rglob("*.html")) + list((ROOT / "mobile" / "src").rglob("*.js")) + \
        [ROOT / "blueprints" / "seo.py", ROOT / "blueprints" / "core.py", ROOT / "nginx.conf.example"]
    for p in candidates:
        if not p.exists() or TPL in p.parents:
            continue
        try:
            text = _read(p)
        except UnicodeDecodeError:
            continue
        if link.search(text):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], f"these files link to /soc: {offenders}"


def test_soc_is_a_reserved_url_segment():
    from utils.tenant_routing import RESERVED_PATH_SEGMENTS
    assert "soc" in RESERVED_PATH_SEGMENTS
