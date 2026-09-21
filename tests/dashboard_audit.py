"""Static audit of dashboard templates: finds the gaps that actually break a page.

Given a Jinja template (and the static JS files it loads) it reports:

  unrouted_urls     fetch()/XHR URLs, <form action>, <a href> and location.href
                    targets that no registered Flask route serves with that
                    HTTP method -- a button that 404s or 405s.
  undefined_handlers  onclick=/onchange=/... attributes calling a function that
                    is not defined anywhere the page can see -- a ReferenceError.
  missing_ids       getElementById('x') / querySelector('#x') targets that exist
                    nowhere in the page (markup or JS-built markup) -- a null
                    dereference waiting to happen.
  unescaped_html    `${...}` interpolated into innerHTML/insertAdjacentHTML
                    without an escaping helper -- stored XSS if the value is data.
  no_error_handling fetch() calls with neither try/catch nor .catch() -- a failed
                    request leaves the panel blank with no message.
  hardcoded_numbers KPI-style elements whose initial text is a non-zero literal,
                    and Jinja `or <number>` fallbacks that invent data.
  a11y              icon-only buttons/links with no accessible name, form fields
                    with no label, images without alt.

It is deliberately heuristic (regexes, not a JS parser); every finding is a
place a human should look, and the tests using it pin the count at zero for
the dashboards we maintain.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORM_RE = re.compile(r"<form\b([^>]*)>", re.I | re.S)
ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
EVENT_ATTR_RE = re.compile(
    r"""\bon(?:click|change|input|submit|keyup|keydown|blur|focus|load|error)\s*=\s*"([^"]*)\"""", re.I)
CALL_RE = re.compile(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(")
GETID_RE = re.compile(r"""getElementById\(\s*['"]([^'"$`{]+)['"]\s*\)""")
QSID_RE = re.compile(r"""querySelector\(\s*['"]#([\w-]+)['"]\s*\)""")
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)
QUOTES = "'\"`"
BACKSLASH = chr(92)

JS_GLOBALS = {
    "alert", "confirm", "prompt", "event", "if", "for", "while", "switch", "return", "function", "typeof", "new",
    "parseInt", "parseFloat", "Number", "String", "Boolean", "Array", "Object", "JSON", "Math", "Date", "Promise",
    "fetch", "setTimeout", "setInterval", "clearTimeout", "clearInterval", "encodeURIComponent", "decodeURIComponent",
    "isNaN", "requestAnimationFrame", "console", "escape", "Error", "URL", "FormData", "Set", "Map", "catch", "await",
    "this", "void", "delete", "in", "of", "else", "do", "try", "with", "Intl", "Chart", "Notification", "atob", "btoa",
    "URLSearchParams", "Blob", "File", "FileReader", "Image", "Audio", "Event", "CustomEvent", "Headers", "Response",
    "Request", "AbortController", "RegExp", "Symbol", "BigInt", "queueMicrotask", "structuredClone", "matchMedia",
    "getComputedStyle", "open", "close", "print", "focus", "blur", "scrollTo", "scrollBy", "postMessage", "eval",
}
ESCAPERS = ("escHtml", "escapeHtml", "esc(", "escHTML", "sanitize", "textContent", "encodeURIComponent", "Number(",
            "parseInt(", "toLocaleString", "toFixed", "laNum", ".length")


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _gather(template_rel, seen):
    """Template text with every {% extends %} / {% include %} it pulls in, recursively."""
    if template_rel in seen:
        return ""
    seen.add(template_rel)
    path = os.path.join(ROOT, template_rel)
    if not os.path.exists(path):
        return ""
    text = _read(path)
    for name in re.findall(r"""[{]%\s*(?:extends|include)\s+['"]([^'"]+)['"]""", text):
        text += "\n" + _gather("templates/" + name, seen)
    return text


def load_page(template_rel):
    """Template text (with its includes/parents) plus every local /static/*.js it loads."""
    text = _gather(template_rel, set())
    scripts = []
    for attrs, body in SCRIPT_RE.findall(text):
        src = dict((k.lower(), a or b) for k, a, b in ATTR_RE.findall(attrs)).get("src", "")
        src = re.sub(r"\{\{\s*static_url\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}", r"/static/\1", src)
        if src.startswith("/static/") and src.endswith(".js"):
            path = os.path.join(ROOT, src.lstrip("/").split("?")[0])
            if os.path.exists(path):
                scripts.append(_read(path))
        elif body.strip():
            scripts.append(body)
    return text, "\n".join(scripts)


# ── URL extraction ───────────────────────────────────────────────────────────
def _first_arg(src, start):
    """Text of the first call argument that begins at src[start] (just after the '(')."""
    depth, quote, i = 0, None, start
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == BACKSLASH:
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        i += 1
    return src[start:i]


def _url_from_expr(expr):
    """"'/a/' + id + '/b'" or a template literal -> '/a/1/b'. None if it is not URL-like."""
    parts, quote, buf, cur = [], None, "", ""
    for ch in expr:
        if quote:
            if ch == quote:
                parts.append(("s", buf))
                quote, buf = None, ""
            else:
                buf += ch
        elif ch in QUOTES:
            if cur.strip():
                parts.append(("v", cur.strip()))
            cur = ""
            quote = ch
        elif ch == "+":
            if cur.strip():
                parts.append(("v", cur.strip()))
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(("v", cur.strip()))
    if not parts or parts[0][0] != "s":
        return None
    url = "".join(text if kind == "s" else "1" for kind, text in parts)
    url = re.sub(r"[$][{][^}]*['\"]\?[^}]*[}]", "", url)   # ${cond ? '?x=1' : ''} is an optional query string
    url = re.sub(r"[$][{][^}]*[}]", "1", url)          # any other JS template expression
    url = re.sub(r"[{][{][^}]*[}][}]", "1", url)       # Jinja expression
    url = re.sub(r"[{]%.*?%[}]", "", url)              # Jinja statement
    return url.split("?")[0].split("#")[0]


def _routable(url_map, path, method):
    from werkzeug.exceptions import HTTPException
    if not path.startswith("/") or path.startswith("//") or path.startswith("/static/"):
        return True
    try:
        url_map.bind("localhost").match(path, method=method)
        return True
    except HTTPException as exc:
        return getattr(exc, "code", None) in (301, 302, 307, 308)   # a redirect still means the route exists
    except Exception:
        return False


def _tpath(value):
    m = re.search(r"tpath\(\s*['\"]([^'\"]+)['\"]\s*\)", value)
    return m.group(1) if m else value


def unrouted_urls(text, js, url_map):
    bad = []
    hay = js + "\n" + text
    for m in re.finditer(r"\bfetch\(", hay):
        url = _url_from_expr(_first_arg(hay, m.end()))
        if not url or not url.startswith("/"):
            continue
        window = hay[m.end():m.end() + 400].split("fetch(")[0]
        mm = re.search(r"""method\s*:\s*['"](\w+)['"]""", window)
        method = mm.group(1).upper() if mm else "GET"
        if not _routable(url_map, url, method):
            bad.append((method, url))
    for m in re.finditer(r"""\.open\(\s*['"](GET|POST|PUT|PATCH|DELETE)['"]\s*,\s*""", js):
        url = _url_from_expr(_first_arg(js, m.end()))
        if url and url.startswith("/") and not _routable(url_map, url, m.group(1)):
            bad.append((m.group(1), url))
    for m in re.finditer(r"""(?:window[.])?location(?:[.]href)?\s*=\s*(?=['"`])""", hay):
        url = _url_from_expr(_first_arg(hay, m.end()).split(";")[0])
        if url and url.startswith("/") and not _routable(url_map, url, "GET"):
            bad.append(("GET", url))
    for attrs in FORM_RE.findall(text):
        a = dict((k.lower(), (x or y)) for k, x, y in ATTR_RE.findall(attrs))
        action = _tpath(a.get("action", ""))
        if not action.startswith("/") or "{{" in action:
            continue
        method = a.get("method", "GET").upper()
        if not _routable(url_map, action.split("?")[0], method):
            bad.append((method, action.split("?")[0]))
    for attrs, _inner in ANCHOR_RE.findall(text):
        a = dict((k.lower(), (x or y)) for k, x, y in ATTR_RE.findall(attrs))
        href = _tpath(a.get("href", ""))
        if href.startswith("/") and "{{" not in href and not href.startswith("//"):
            path = re.sub(r"[{]%.*?%[}]", "", href).split("?")[0].split("#")[0]
            if not _routable(url_map, path, "GET"):
                bad.append(("GET", path))
    return sorted(set(bad))


# ── JavaScript wiring ────────────────────────────────────────────────────────
def _defined_functions(text, js):
    src = text + "\n" + js
    names = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", src))
    names |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", src))
    names |= set(re.findall(r"window[.]([A-Za-z_$][\w$]*)\s*=", src))
    names |= set(re.findall(
        r"(?<![\w.])([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|[(][^)]*[)]\s*=>|[A-Za-z_$][\w$]*\s*=>)", src))
    return names


def undefined_handlers(text, js):
    defined = _defined_functions(text, js)
    bad = set()
    for handler in EVENT_ATTR_RE.findall(text):
        code = re.sub(r"[{][{].*?[}][}]|[{]%.*?%[}]", "0", handler)
        code = re.sub(r"'[^']*'|\"[^\"]*\"", "''", code)
        for name in CALL_RE.findall(code):
            if name not in defined and name not in JS_GLOBALS:
                bad.add(name)
    return sorted(bad)


def missing_ids(text, js):
    have = set(re.findall(r"""\bid\s*=\s*["']([^"'$`{]+)["']""", text + "\n" + js))
    have |= set(re.findall(r"""\bid\s*=\s*[\\]?["']([\w-]+)""", text + "\n" + js))
    # Only lookups that are dereferenced on the spot -- getElementById('x').value -- can
    # crash; `const el = getElementById('x'); if (el) ...` (or ?.) is a guarded lookup.
    unguarded = set(re.findall(r"""getElementById\(\s*['"]([^'"$`{]+)['"]\s*\)\s*(?:[.]|\[)""", js))
    unguarded |= set(re.findall(r"""querySelector\(\s*['"]#([\w-]+)['"]\s*\)\s*(?:[.]|\[)""", js))
    return sorted(i for i in unguarded if i not in have)


def unescaped_html(js):
    """`${expr}` inside a template literal assigned to innerHTML / insertAdjacentHTML."""
    findings = []
    src = _strip_comments(js)
    for m in re.finditer(r"(?:innerHTML\s*[+]?=|insertAdjacentHTML[(][^,]+,)\s*(?=`)", src):
        i = m.end() + 1                       # just inside the opening backtick
        while i < len(src) and src[i] != "`":
            if src[i] == BACKSLASH:
                i += 2
            elif src.startswith("${", i):
                close = _match_close(src, i + 1)      # the matching } of ${ ... }, nested braces/strings included
                if close < 0:
                    break
                e = src[i + 2:close - 1].strip()
                if not any(tok in e for tok in ESCAPERS):
                    findings.append(e[:70])
                i = close
            else:
                i += 1
    return sorted(set(findings))


def _strip_comments(js):
    """Blank out // and /* */ comments (keeping length) so apostrophes in prose can't confuse the scanners."""
    out, i, n, quote = [], 0, len(js), None
    while i < n:
        ch = js[i]
        if quote:
            out.append(ch)
            if ch == BACKSLASH and i + 1 < n:
                out.append(js[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
        elif ch in QUOTES:
            quote = ch
            out.append(ch)
            i += 1
        elif js.startswith("//", i) and (i == 0 or js[i - 1] != ":"):
            j = js.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif js.startswith("/*", i):
            j = js.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in js[i:j]))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _match_close(src, open_idx):
    """Index just past the bracket that closes src[open_idx] ('(' '[' or '{'); -1 if unbalanced."""
    depth, quote, i, n = 0, None, open_idx, len(src)
    while i < n:
        ch = src[i]
        if quote:
            if ch == BACKSLASH:
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _chain_has_catch(src, end_idx):
    """After a call ends at end_idx, does the .then()/.catch()/.finally() chain include .catch(?"""
    i = end_idx
    while True:
        m = re.compile(r"\s*[.](then|catch|finally)\s*[(]").match(src, i)
        if not m:
            return False
        if m.group(1) == "catch":
            return True
        close = _match_close(src, m.end() - 1)
        if close < 0:
            return False
        i = close


def _inside_try(src, pos):
    """True when src[pos] sits inside the { } body of a try statement."""
    for m in re.finditer(r"\btry\s*[{]", src[:pos]):
        close = _match_close(src, m.end() - 1)
        if close < 0 or close > pos:
            return True
    return False


def no_error_handling(js):
    """fetch() calls whose failure is unhandled: not inside a try{} and no .catch() on the promise chain.
    (A fetch whose caller handles the rejection is a judgement call this cannot see -- those are
    reviewed by hand and marked with a `// audit-ok: <reason>` comment on the fetch line.)"""
    src = _strip_comments(js)
    raw_lines = js.split("\n")
    bad = 0
    for m in re.finditer(r"\bfetch\s*[(]", src):
        line_no = src.count("\n", 0, m.start())
        if "audit-ok" in raw_lines[line_no] or (line_no and "audit-ok" in raw_lines[line_no - 1]):
            continue
        if _inside_try(src, m.start()):
            continue
        close = _match_close(src, m.end() - 1)
        if close > 0 and _chain_has_catch(src, close):
            continue
        bad += 1
    return bad


# ── Data & accessibility ─────────────────────────────────────────────────────
def hardcoded_numbers(text):
    out = []
    kpi = r"""<[^>]*\bid\s*=\s*["']([\w-]*(?:kpi|Kpi|count|Count|total|Total|stat|Stat)[\w-]*)["'][^>]*>\s*(\d[\d,.]*)\s*<"""
    for m in re.finditer(kpi, text):
        if m.group(2).strip("0,.") != "":          # an initial 0 is normal state; any other number is invented
            out.append("#%s=%s" % (m.group(1), m.group(2)))
    for m in re.finditer(r"[{][{][^}]*\bor\s+(\d{2,})\s*[}][}]", text):
        out.append("jinja-fallback:" + m.group(0)[:50])
    return sorted(set(out))


def _attrs(raw):
    return dict((k.lower(), (x or y)) for k, x, y in ATTR_RE.findall(raw))


def _visible(inner):
    """Text a user (or screen reader) gets from an element. A Jinja expression counts as text --
    <a>{{ company.name }}</a> is not icon-only."""
    inner = re.sub(r"[{][{].*?[}][}]", "x", inner)
    return re.sub(r"<[^>]+>|[{]%.*?%[}]", "", inner).strip()


def a11y(text):
    issues = []
    for attrs, inner in re.findall(r"<button\b([^>]*)>(.*?)</button>", text, re.I | re.S):
        a = _attrs(attrs)
        if not _visible(inner) and not (a.get("aria-label") or a.get("title") or a.get("aria-labelledby")):
            issues.append("icon-only <button> without aria-label: " + (a.get("onclick", "")[:40] or a.get("id", "")))
    for attrs, inner in ANCHOR_RE.findall(text):
        a = _attrs(attrs)
        if a.get("href") and not _visible(inner) and not (a.get("aria-label") or a.get("title")):
            issues.append("icon-only <a> without aria-label: " + a.get("href", "")[:40])
    for tag in re.findall(r"<img\b[^>]*>", text, re.I):
        if not re.search(r"\balt\s*=", tag, re.I):
            issues.append("<img> without alt: " + tag[:50])
    labels_for = set(re.findall(r"""<label\b[^>]*\bfor\s*=\s*["']([^"']+)["']""", text, re.I))
    wrapped = [(m.start(), m.end()) for m in re.finditer(r"<label\b[^>]*>.*?</label>", text, re.I | re.S)]
    for m in re.finditer(r"<(?:input|select|textarea)\b[^>]*>", text, re.I):
        tag = m.group(0)
        if re.search(r"""type\s*=\s*["'](?:hidden|submit|button|image)["']""", tag, re.I):
            continue
        if re.search(r"\b(?:aria-label|aria-labelledby|title)\s*=", tag, re.I):
            continue
        idm = re.search(r"""\bid\s*=\s*["']([^"']+)["']""", tag)
        if idm and idm.group(1) in labels_for:
            continue
        if any(a <= m.start() < b for a, b in wrapped):     # <label>Text <input></label>
            continue
        issues.append("form field without a label: " + tag[:60])
    return issues


def literal_entities(js):
    """HTML entities in a string that is shown as PLAIN TEXT (textContent / innerText / showMsg / alert /
    showToast): they are not decoded there, so the user sees the raw '&#9203;'."""
    pattern = (r"(?:(?:textContent|innerText)\s*=\s*['\"`]|(?:showMsg|alert|showToast)\(\s*['\"`])"
               r"[^'\"`\n]*&#?[a-zA-Z0-9]+;")
    return sorted(set(m.group(0)[:70] for m in re.finditer(pattern, js)))


def audit(template_rel, url_map):
    text, js = load_page(template_rel)
    return {
        "unrouted_urls": unrouted_urls(text, js, url_map),
        "undefined_handlers": undefined_handlers(text, js),
        "missing_ids": missing_ids(text, js),
        "unescaped_html": unescaped_html(js),
        "no_error_handling": no_error_handling(js),
        "hardcoded_numbers": hardcoded_numbers(text),
        "literal_entities": literal_entities(js),
        "a11y": a11y(text),
    }
