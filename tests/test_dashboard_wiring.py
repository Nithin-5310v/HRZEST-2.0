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
