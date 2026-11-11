"""Stored XSS: server values written straight into JS template literals.

The employee portal built its payslip panels with `...` template literals and
put the employee's name and designation in with a bare {{ emp[1] }} /
{{ emp[2] }}. Jinja's HTML escaping does not touch a backtick or "${", so a
name (set by admin/HR) such as  Eve`;alert(1);`  or  ${alert(1)}  broke out of
the literal and ran as script in that employee's session -- inside the page's
own nonce'd script, so the CSP could not stop it.

Server values now reach the page script (static/js/employee-portal.js) only
through an inert JSON block, <script type="application/json" id="portal-data">,
and are inserted into HTML with escHtml().
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def _jinja_in_template_literals(js):
    """Every {{ ... }} that sits inside a `...` template literal (comments,
    quoted strings and ${ } substitutions are tracked, not matched)."""
    found, i, n = [], 0, len(js)
    state, subst = None, []          # state: None | ' | " | ` | // | /*
    while i < n:
        c, two = js[i], js[i:i + 2]
        if two == "{{" and state == "`":
            j = js.find("}}", i)
            found.append(js[i:j + 2])
            i = j + 2
            continue
        if two in ("{{", "{%"):
            i = js.find("}}" if two == "{{" else "%}", i) + 2
            continue
        if state is None:
            if two in ("//", "/*"):
                state = two
                i += 2
                continue
            if c in "'\"`":
                state = c
            elif c == "}" and subst:
                subst.pop()
                state = "`"
        elif state == "//":
            if c == "\n":
                state = None
        elif state == "/*":
            if two == "*/":
                state = None
                i += 2
                continue
        elif state in "'\"":
            if c == "\\":
                i += 2
                continue
            if c in (state, "\n"):
                state = None
        elif state == "`":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                state = None
            elif two == "${":
                subst.append(1)
                state = None
                i += 2
                continue
        i += 1
    return found


def test_no_template_puts_jinja_inside_a_js_template_literal():
    hits = []
    for path in sorted((ROOT / "templates").rglob("*.html")):
        for m in _INLINE_SCRIPT.finditer(path.read_text(encoding="utf-8")):
            hits += [f"{path.relative_to(ROOT).as_posix()}: {expr}" for expr in _jinja_in_template_literals(m.group(1))]
    assert hits == [], "pass server values as JSON (tojson) and insert them with escHtml():\n" + "\n".join(hits)


def test_scanner_catches_the_original_pattern():
    assert _jinja_in_template_literals("x.innerHTML = `<div>{{ emp[1] }}</div>`;") == ["{{ emp[1] }}"]
    assert _jinja_in_template_literals("var a = {{ x | tojson }}; var b = `${y}`;") == []
    assert _jinja_in_template_literals("var s = `a ${ f(`{{ z }}`) } b`;") == ["{{ z }}"]


PAYLOAD = "Eve`;window.__pwned=1;//${window.__pwned=2}"


def test_malicious_name_is_never_spliced_into_a_template_literal(client, seed_employee, db_engine):
    emp_id = seed_employee["employee_id"]
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET name=%s, role=%s WHERE employee_id=%s", (PAYLOAD, PAYLOAD, emp_id))
    cur.close()
    with client.session_transaction() as sess:
        sess["employee_id"] = emp_id
    html = client.get("/employee_portal").get_data(as_text=True)
    marker = PAYLOAD.split("`")[0] + "`"
    blocks = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I)
    executable = [body for attrs, body in blocks if 'type="application/json"' not in attrs and "src=" not in attrs]
    data = [body for attrs, body in blocks if 'id="portal-data"' in attrs]
    # The raw name never appears in code that runs; it travels only in the inert
    # #portal-data JSON block, which static/js/employee-portal.js JSON.parse()s.
    assert not [b for b in executable if marker in b]
    assert len(data) == 1 and marker in data[0]
