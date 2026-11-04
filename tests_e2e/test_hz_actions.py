"""static/hz-actions.js in a real browser, under the same kind of CSP the
app sends: nonce-only script-src, no 'unsafe-hashes' -- so inline onclick=""
handlers are blocked and data-on-* attributes are the only way in.

Needs no app server or database: the page is served from a Playwright route.
"""
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ACTIONS_JS = (Path(__file__).resolve().parent.parent / "static" / "hz-actions.js").read_text(encoding="utf-8")
NONCE = "testnonce123"
CSP = f"default-src 'self'; script-src 'self' 'nonce-{NONCE}'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"

PAGE = f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<script nonce="{NONCE}">
  window.calls = [];
  function rec() {{ calls.push(Array.prototype.slice.call(arguments)); }}
  function veto() {{ calls.push('veto'); return false; }}
  var NS = {{ inner: function (x) {{ calls.push(['ns', x, this === NS]); }} }};
</script>
<button id="lit" data-on-click="rec('a', 2, -1.5, true, null, 'it\\'s')">x</button>
<button id="refs" data-id="42" value="v" data-on-click="rec(this.id, this.dataset.id, +this.dataset.id, !this.dataset.missing, event.type)">x</button>
<button id="ns" data-on-click="NS.inner('n')">x</button>
<a id="link" href="#moved" data-on-click="return veto()">x</a>
<button id="selfm" data-on-click="this.remove()">x</button>
<button id="evil1" data-on-click="rec(1); rec(2)">x</button>
<button id="evil2" data-on-click="rec.constructor('calls.push(666)')()">x</button>
<button id="evil3" data-on-click="rec(document.cookie)">x</button>
<button id="inline" onclick="calls.push('inline ran')">x</button>
<div id="hov" style="color: rgb(1, 2, 3)" data-hover-style="color:rgb(9, 9, 9);transform:scale(2)">h</div>
<img id="broken" src="data:image/png;base64,AAAA" data-on-error="rec('img-error', this.id)">
<div id="host"></div>
<script src="/hz-actions.js"></script>
</body></html>"""


@pytest.fixture(scope="module")
def browser():
    # One Playwright instance per module: the sync API can't run two at once.
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(browser):
    pg = browser.new_page()

    def handle(route):
        if route.request.url.endswith("/hz-actions.js"):
            route.fulfill(body=ACTIONS_JS, content_type="application/javascript")
        else:
            route.fulfill(body=PAGE, content_type="text/html", headers={"Content-Security-Policy": CSP})

    pg.route("http://hz.test/**", handle)
    pg.goto("http://hz.test/")
    pg.wait_for_function("!!window.HZActions")
    # Let the broken <img>'s error (and any catch-up) settle, then keep
    # what it recorded for its own test.
    pg.wait_for_timeout(200)
    pg.img_calls = pg.evaluate("calls.splice(0)")
    yield pg
    pg.close()


def _calls(page):
    return page.evaluate("calls.splice(0)")


def test_literal_arguments(page):
    page.click("#lit")
    assert _calls(page) == [["a", 2, -1.5, True, None, "it's"]]


def test_this_and_event_references(page):
    page.click("#refs")
    assert _calls(page) == [["refs", "42", 42, True, "click"]]


def test_dotted_path_keeps_its_owner_as_this(page):
    page.click("#ns")
    assert _calls(page) == [["ns", "n", True]]


def test_return_false_prevents_default(page):
    page.click("#link")
    assert _calls(page) == ["veto"]
    assert page.evaluate("location.hash") == ""


def test_method_on_the_element(page):
    page.click("#selfm")
    assert page.query_selector("#selfm") is None


def test_rejects_anything_but_a_single_call(page):
    for sel in ("#evil1", "#evil2"):
        page.click(sel)
    assert _calls(page) == []


def test_only_this_and_event_are_reachable(page):
    page.click("#evil3")
    assert _calls(page) == []


def test_csp_still_blocks_inline_handlers(page):
    page.click("#inline")
    assert _calls(page) == []


def test_image_that_failed_before_binding_still_reports(page):
    # The broken <img> may error before hz-actions.js binds it; the handler
    # must still run -- exactly once, not again for the browser's own event.
    assert page.img_calls == [["img-error", "broken"]]


def test_elements_added_later_are_bound(page):
    page.evaluate("""document.getElementById('host').innerHTML =
        '<button id="late" data-on-click="rec(\\'late\\', this.id)">x</button>'""")
    page.wait_for_selector("#late")
    page.wait_for_timeout(50)  # MutationObserver callback
    page.click("#late")
    assert _calls(page) == [["late", "late"]]


def test_hover_style_applies_and_restores(page):
    page.hover("#hov")
    assert page.eval_on_selector("#hov", "e => [e.style.color, e.style.transform]") == ["rgb(9, 9, 9)", "scale(2)"]
    page.mouse.move(0, 0)
    assert page.eval_on_selector("#hov", "e => [e.style.color, e.style.transform]") == ["rgb(1, 2, 3)", ""]


def test_helpers(page):
    page.evaluate("""document.getElementById('host').innerHTML =
        '<div id="tgt" style="display:block"></div><input id="inp" value="x">'""")
    page.evaluate("HZ.toggleDisplay('tgt', 'flex')")
    assert page.eval_on_selector("#tgt", "e => e.style.display") == "none"
    page.evaluate("HZ.toggleDisplay('tgt', 'flex')")
    assert page.eval_on_selector("#tgt", "e => e.style.display") == "flex"
    page.evaluate("HZ.disableIf('inp', true, true)")
    assert page.eval_on_selector("#inp", "e => [e.disabled, e.value]") == [True, ""]


TOAST_JS = (Path(__file__).resolve().parent.parent / "static" / "toast.js").read_text(encoding="utf-8")
CONFIRM_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<script src="/toast.js"></script>
<form id="f" method="post" action="/submitted" data-on-submit="return hzConfirmSubmit(event, 'Delete?')">
  <button id="go" type="submit">Delete</button>
</form>
<script src="/hz-actions.js"></script>
</body></html>"""


@pytest.mark.parametrize("answer, submitted", [(True, True), (False, False)])
def test_hz_confirm_submit_through_data_on_submit(browser, answer, submitted):
    """toast.js's hzConfirmSubmit cancels the submit, asks, then replays it on
    the form (event.currentTarget) -- which needs the listener bound on the
    form itself, as hz-actions.js does."""
    page = browser.new_page()
    posts = []

    def handle(route):
        url = route.request.url
        if url.endswith("/hz-actions.js"):
            route.fulfill(body=ACTIONS_JS, content_type="application/javascript")
        elif url.endswith("/toast.js"):
            route.fulfill(body=TOAST_JS, content_type="application/javascript")
        elif url.endswith("/submitted"):
            posts.append(route.request.method)
            route.fulfill(body="<p id=done>ok</p>", content_type="text/html")
        else:
            route.fulfill(body=CONFIRM_PAGE, content_type="text/html",
                          headers={"Content-Security-Policy": CSP.replace("'nonce-" + NONCE + "'", "")})

    page.route("http://hz.test/**", handle)
    page.goto("http://hz.test/")
    page.wait_for_function("!!window.HZActions && typeof window.hzConfirmSubmit === 'function'")
    # Resolve like a real dialog: after the click's dispatch has finished
    # (a same-task resolve replays the submit while the browser is still
    # firing the first one, and it ignores requestSubmit() then).
    page.evaluate(f"window.hzConfirm = () => new Promise(r => setTimeout(() => r({'true' if answer else 'false'}), 20))")
    page.click("#go")
    if submitted:
        page.wait_for_selector("#done")
    else:
        page.wait_for_timeout(300)
    assert posts == (["POST"] if submitted else [])
    page.close()
