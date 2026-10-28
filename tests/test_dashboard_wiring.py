"""Dashboards have no code gaps.

Runs tests/dashboard_audit.py over every maintained dashboard template and pins each
category at zero, so a new dead link, undefined handler, unescaped render, unhandled
fetch, hardcoded number, raw-entity string or unlabeled form field fails CI instead of
shipping. Also compiles the templates, syntax-checks each page's JavaScript, and runs
the shared toast against hostile input.

If a fetch() is deliberately left to its caller to handle, say so on the line above it:
    // audit-ok: <who catches it>
"""
import os
import re
import shutil
import subprocess

import pytest

from extensions import app as flask_app
from tests.dashboard_audit import ROOT, audit

DASHBOARDS = [
    "admin.html", "hr_dashboard.html", "employee_portal.html", "analytics.html",
    "super_admin_dashboard.html", "super_admin_soc_dashboard.html", "super_admin_audit_log.html",
    "super_admin_applications.html", "super_admin_application_detail.html", "super_admin_tickets.html",
    "super_admin_ticket_detail.html", "super_admin_company_profile.html", "super_admin_duplicate_alerts.html",
]
CATEGORIES = [
    "unrouted_urls",        # a button/form/fetch that 404s or 405s
    "undefined_handlers",   # onclick="fn()" where fn does not exist
    "missing_ids",          # getElementById('x').y where #x does not exist
    "unescaped_html",       # ${...} put into innerHTML without escaping
    "no_error_handling",    # fetch() with no try/catch or .catch()
    "hardcoded_numbers",    # invented KPI values
    "literal_entities",     # '&#9203;' shown as text
    "a11y",                 # unlabeled fields / icon-only controls
]
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def reports():
    return {page: audit("templates/" + page, flask_app.url_map) for page in DASHBOARDS}


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("page", DASHBOARDS)
def test_no_gaps(reports, page, category):
    found = reports[page][category]
    assert not found, f"{page}: {category}: {found if isinstance(found, int) else found[:8]}"


@pytest.mark.parametrize("expr,expected", [
    ("'/api/a/' + id + '/b'", "/api/a/1/b"),                                   # concatenation
    ("`/api/a/${id}/b`", "/api/a/1/b"),                                        # template literal
    ("`/api/layers${force ? '?refresh=1' : ''}`", "/api/layers"),              # optional query string, not a path segment
    ("`/api/x?a=${v}`", "/api/x"),
    ("someVariable", None),
])
def test_url_reconstruction(expr, expected):
    from tests.dashboard_audit import _url_from_expr
    assert _url_from_expr(expr) == expected


@pytest.mark.parametrize("page", DASHBOARDS)
def test_template_compiles(page):
    flask_app.jinja_env.get_template(page)


@pytest.mark.skipif(not NODE, reason="node is required to syntax-check page scripts")
@pytest.mark.parametrize("page", DASHBOARDS)
def test_inline_javascript_parses(page, tmp_path):
    html = open(os.path.join(ROOT, "templates", page), encoding="utf-8").read()
    html = re.sub(r"<!--.*?-->|[{]#.*?#[}]", "", html, flags=re.S)      # a comment may mention "<script>"
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    if not scripts:
        pytest.skip("no inline script")
    js = "\n;\n".join(scripts)
    js = re.sub(r"[{][{].*?[}][}]", "0", js, flags=re.S)
    js = re.sub(r"[{]%.*?%[}]", "", js, flags=re.S)
    f = tmp_path / "page.js"
    f.write_text(js, encoding="utf-8")
    out = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[:500]


# ── The shared toast is on every dashboard; alert() is routed through it ──────
TOAST_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
function el(tag) {
  const e = { tag, children: [], attrs: {}, className: '', textContent: '', classList: { add() {}, remove() {} },
    appendChild(c) { this.children.push(c); return c; }, setAttribute(k, v) { this.attrs[k] = v; }, remove() {} };
  Object.defineProperty(e, 'innerHTML', { set(v) { throw new Error('innerHTML assigned: ' + v); }, get() { return ''; } });
  return e;
}
const body = el('body');
const sandbox = { window: {}, document: { getElementById: id => body.children.find(c => c.id === id) || null,
                                            createElement: el, body },
                  requestAnimationFrame: f => f(), setTimeout: () => 0 };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8') + '\n;this.showToast = showToast;', sandbox);
const PAYLOAD = '<img src=x onerror=alert(1)>';
sandbox.showToast(PAYLOAD, 'info');
sandbox.showToast('x', 'error" onmouseover="evil()', 'alert-triangle" onclick="evil()');
sandbox.window.alert(PAYLOAD);
sandbox.showToast(null);
const toasts = body.children[0].children;
const out = toasts.map(t => ({ cls: t.className, icon: t.children[0].children[0].className, text: t.children[1].textContent }));
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(not NODE, reason="node is required to execute toast.js")
def test_toast_treats_messages_as_text_and_validates_class_names(tmp_path):
    import json
    harness = tmp_path / "toast_check.js"
    harness.write_text(TOAST_HARNESS, encoding="utf-8")
    out = subprocess.run([NODE, str(harness), os.path.join(ROOT, "static", "toast.js")],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[:600]          # innerHTML assignment would have thrown
    toasts = json.loads(out.stdout)
    payload = "<img src=x onerror=alert(1)>"
    assert toasts[0]["text"] == payload                   # shown literally, never parsed
    assert toasts[2]["text"] == payload                   # window.alert() goes through the same path
    assert toasts[3]["text"] == ""                        # null/undefined -> empty, not "null"
    for t in toasts:
        assert '"' not in t["cls"] + t["icon"] and "=" not in t["cls"] + t["icon"]
    assert toasts[1]["cls"] == "toast info" and toasts[1]["icon"] == "ti ti-info-circle"   # bad type/icon fell back


# ── The shared chat thread (static/chat.js): admin widget + super admin chat ──
CHAT_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
function el(tag) {
  const e = { tag, children: [], attrs: {}, className: '', _text: '', style: {}, parentNode: null,
    scrollTop: 0, scrollHeight: 1000, clientHeight: 400,
    appendChild(c) {
      const kids = c.tag === '#frag' ? c.children.splice(0) : [c];
      kids.forEach(k => { k.parentNode = this; this.children.push(k); });
      return c;
    },
    replaceChild(n, o) { this.children[this.children.indexOf(o)] = n; n.parentNode = this; return o; },
    setAttribute(k, v) { this.attrs[k] = v; } };
  Object.defineProperty(e, 'textContent', {
    set(v) { this._text = v; this.children = []; },
    get() { return this._text + this.children.map(c => c.textContent).join(''); } });
  Object.defineProperty(e, 'innerHTML', { set(v) { throw new Error('innerHTML assigned: ' + v); }, get() { return ''; } });
  return e;
}
const sandbox = { window: {}, document: { createElement: el, createDocumentFragment: () => el('#frag') } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const C = sandbox.window.HZChat;
const PAYLOAD = '<img src=x onerror=alert(1)>';
const msgs = [
  { id: 1, sender_kind: 'admin', sender_name: PAYLOAD, message: PAYLOAD, created_at: '2026-10-27T10:00:00+05:30',
    read_by_platform: true, read_by_company: true },
  { id: 2, sender_kind: 'admin', sender_name: PAYLOAD, message: 'same run', created_at: '2026-10-27T10:01:00+05:30',
    read_by_platform: true, read_by_company: true },
  { id: 3, sender_kind: 'platform_admin', sender_name: 'pa', message: 'reply', created_at: '2026-10-27T10:02:00+05:30',
    read_by_platform: true, read_by_company: false },
];
const opts = { isMine: m => m.sender_kind === 'platform_admin', seenByOther: m => m.read_by_company };
const box = el('div');
const rows = () => box.children.map(r => ({ cls: r.className, text: r.textContent }));
const out = {};
C.render(box, msgs, opts);
out.first = rows();
const firstRow = box.children[1];
C.render(box, msgs, opts);                              // a poll with nothing new
out.untouched = box.children[1] === firstRow;
msgs[2].read_by_company = true;
C.render(box, msgs, opts);
out.seen = rows()[3].text;
const p = C.addPending(box, 'typed text');
out.pending = rows()[4];
p.fail();
out.failed = rows()[4];
C.render(box, msgs, Object.assign({ force: true }, opts));
out.failedKept = rows().length;
out.initials = [C.initials('Trends'), C.initials('Magzest Consultancy Services'), C.initials('')];
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(not NODE, reason="node is required to execute chat.js")
def test_chat_thread_renders_text_only_bubbles_with_ticks(tmp_path):
    import json
    harness = tmp_path / "chat_check.js"
    harness.write_text(CHAT_HARNESS, encoding="utf-8")
    out = subprocess.run([NODE, str(harness), os.path.join(ROOT, "static", "chat.js")],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr[:600]          # innerHTML assignment would have thrown
    r = json.loads(out.stdout)
    payload = "<img src=x onerror=alert(1)>"
    day, a, b, mine = r["first"]
    assert day["cls"] == "hz-day"
    assert a["cls"] == "hz-msg hz-theirs hz-first" and a["text"].startswith(payload + payload)  # name, then text
    assert b["cls"] == "hz-msg hz-theirs" and payload not in b["text"]   # same sender a minute later: no repeat name
    assert mine["cls"] == "hz-msg hz-mine hz-first" and mine["text"].endswith("Sent")
    assert r["untouched"] is True                         # unchanged poll leaves the DOM (and scroll) alone
    assert r["seen"].endswith("Seen")
    assert r["pending"]["text"].startswith("typed text") and r["pending"]["text"].endswith("Sending")
    assert "hz-failed" in r["failed"]["cls"] and r["failed"]["text"] == "typed textNot sent"
    assert r["failedKept"] == 5                           # a refresh doesn't silently drop the unsent message
    assert r["initials"] == ["TR", "MC", "?"]
