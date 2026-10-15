# -*- coding: utf-8 -*-
"""Native, signature-based Web Application Firewall.

Complements the structural defenses already in place elsewhere (parameterized
SQL everywhere a query is built, CSP + html.escape for XSS, secure_filename +
magic-byte checks for uploads -- see utils/helpers.py) with an interception
layer that inspects every incoming request for known attack *shapes* and
rejects them before a route handler ever runs. Registered as an
app.before_request hook in app.py, right after _enforce_ip_ban.

Patterns are deliberately multi-token / structural rather than single
characters or words, so a legitimate apostrophe in a name ("O'Brien"), a
hyphenated address, or the word "select" in a dropdown label never trips a
false positive -- each pattern requires the *shape* of an actual injection
attempt (a keyword next to the syntax that would make it executable).
"""
import os
import re
import time
import datetime
import threading
from collections import defaultdict, deque

from extensions import log_security_event, redis_client
from utils.client_ip import is_protected_ip

_SQLI_PATTERNS = [
    # UNION-based exfiltration
    re.compile(r"\bunion\b[^;]{0,40}\bselect\b", re.IGNORECASE),
    # Stacked/terminated statements
    re.compile(r";\s*(drop|delete|update|insert|exec|alter|truncate)\b", re.IGNORECASE),
    # Numeric tautologies: OR 1=1, AND 2=2, etc.
    re.compile(r"\bor\b\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+['\"]?", re.IGNORECASE),
    re.compile(r"\band\b\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+['\"]?", re.IGNORECASE),
    # String tautologies: OR 'a'='a', OR 'x'='x, OR ''='', etc.
    re.compile(r"\bor\b\s+['\"]([^'\"]{0,20})['\"]\s*=\s*['\"]\1['\"]?", re.IGNORECASE),
    re.compile(r"\bor\b\s+'[^']*'\s*=\s*'[^']*'", re.IGNORECASE),
    re.compile(r"\bor\b\s+\"[^\"]*\"\s*=\s*\"[^\"]*\"", re.IGNORECASE),
    # Comment-terminator shapes. These used to be a bare `--\s` and a bare
    # `/*...*/`, which also blocked ordinary prose ("Great work -- keep it
    # up", "receipt attached -- thanks") on every non-login form/JSON field
    # and fed the auto-ban counter with false positives. A comment marker
    # is only an injection tell when it TERMINATES the value (that's the
    # whole point of the attack: kill the rest of the query), or sits right
    # after statement syntax / a SQL keyword.
    #   admin'--   ' OR 1=1 --   x') --   x' -- -
    re.compile(r"['\"]\s*\)*\s*--+\s*-?\s*$"),
    #   1; -- ...   (stacked statement then comment). A bare `) --` is NOT
    #   matched: "(approx 1200) -- receipt attached" is ordinary prose.
    re.compile(r";\s*--(?:\s|$)"),
    # Empty inline comment: UN/**/ION, SEL/**/ECT -- no legitimate text has one
    re.compile(r"/\*\s*\*/"),
    #   ... WHERE id=1 --<EOL>   (keyword ... trailing comment)
    re.compile(r"\b(?:or|and|union|select|where|from)\b[^\n]{0,80}?--\s*$", re.IGNORECASE),
    # Inline-comment keyword splitting: UN/**/ION, SELECT/**/password
    re.compile(r"\b(?:union|select|from|where|and|or|insert|update|delete|drop|exec)\b\s*/\*.*?\*/",
               re.IGNORECASE | re.DOTALL),
    re.compile(r"/\*!\d*"),  # MySQL-style versioned comment
    # OR true/false literals
    re.compile(r"\bor\b\s+(true|false)\b", re.IGNORECASE),
    re.compile(r"\band\b\s+(true|false)\b", re.IGNORECASE),
    # Dangerous proc/functions
    re.compile(r"\bxp_cmdshell\b", re.IGNORECASE),
    re.compile(r"\bexec\s*\(", re.IGNORECASE),
    re.compile(r"\bwaitfor\s+delay\b", re.IGNORECASE),
    # Schema enumeration
    re.compile(r"\bselect\b[^;]{0,60}\bfrom\b[^;]{0,60}\binformation_schema\b", re.IGNORECASE),
]

# Hex/char encoding evasion -- split out from _SQLI_PATTERNS above because
# these two specifically fire on base64-encoded binary payloads (face
# check-in, photo upload) by coincidence, not attack intent: base64's
# alphabet (A-Za-z0-9+/=) contains "0" immediately followed by "x" and 4+
# more hex-range characters purely by chance in any sufficiently long
# string, with no word-boundary or special-character requirement to filter
# false hits the way every other pattern above has. Measured against 200
# synthetic 60KB base64 photo payloads: 47 (23.5%) were blocked before this
# split existed. See _looks_like_encoded_blob() below for the exemption.
_SQLI_ENCODING_EVASION_PATTERNS = [
    re.compile(r"0x[0-9a-fA-F]{4,}"),
    re.compile(r"\bchar\s*\(\s*\d+", re.IGNORECASE),
]

# A value shaped like an opaque base64/binary blob -- optionally prefixed
# with a data: URI header, then nothing but the base64 alphabet (plus
# whitespace some encoders wrap output with) for at least
# _ENCODED_BLOB_MIN_LEN characters. Deliberately a *shape* test, not a
# fixed allowlist of field names like "face_image"/"photo": a new
# image/signature/scan field added later gets this exemption automatically
# instead of silently reintroducing the false-positive bug.
#
# This cannot be used to smuggle a real attack past the encoding-evasion
# checks it exempts: every character those two patterns actually need
# ("0x" immediately followed by hex digits, or "char(" followed by a
# digit) IS itself valid base64 alphabet, but building an *exploitable*
# SQL/XSS/path-traversal payload out of nothing but [A-Za-z0-9+/=] is not
# possible for the OTHER patterns in _SQLI_PATTERNS/_XSS_PATTERNS/
# _PATH_TRAVERSAL_PATTERNS above and below -- those require a space, quote,
# semicolon, angle bracket, or ".." sequence, none of which are valid
# base64 characters. A genuine attack hidden inside a field an attacker
# claims is "base64" would have to include one of those characters to do
# anything, which immediately breaks the fullmatch below and falls through
# to full scanning (including the encoding-evasion patterns) unexempted.
_ENCODED_BLOB_RE = re.compile(
    r"^(?:data:[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+;base64,)?[A-Za-z0-9+/_=\s]+$"
)
_ENCODED_BLOB_MIN_LEN = 256  # no real SQLi/XSS/path payload is this long AND this uniform


def _looks_like_encoded_blob(value: str) -> bool:
    return len(value) >= _ENCODED_BLOB_MIN_LEN and bool(_ENCODED_BLOB_RE.match(value))


_XSS_PATTERNS = [
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"\bon(?:load|error|click|mouseover|focus|blur|change|submit)\s*=", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"<iframe\b", re.IGNORECASE),
    re.compile(r"<svg\b[^>]*\bonload\b", re.IGNORECASE),
    re.compile(r"data\s*:\s*text/html", re.IGNORECASE),
]

_PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),
    re.compile(r"\.\.\\"),
    re.compile(r"%2e%2e", re.IGNORECASE),
    re.compile(r"%00"),
    re.compile(r"\x00"),
]


def _first_match(patterns, value):
    for pat in patterns:
        m = pat.search(value)
        if m:
            return m.group(0)
    return None


def _sqli_signature(s, _skip_encoding_evasion=None):
    """_skip_encoding_evasion is computed lazily (None -> figured out here)
    rather than always passed by the caller, so every existing direct call
    (including tests/test_waf.py's parametrized cases, which call this with
    one positional arg) keeps working unchanged."""
    if _skip_encoding_evasion is None:
        _skip_encoding_evasion = _looks_like_encoded_blob(s)
    hit = _first_match(_SQLI_PATTERNS, s)
    if hit:
        return hit
    if _skip_encoding_evasion:
        return None
    return _first_match(_SQLI_ENCODING_EVASION_PATTERNS, s)


def _xss_signature(s):
    return _first_match(_XSS_PATTERNS, s)


def _path_traversal_signature(s):
    return _first_match(_PATH_TRAVERSAL_PATTERNS, s)


_CHECKS = (
    ("waf.sqli_blocked", _sqli_signature),
    ("waf.xss_blocked", _xss_signature),
    ("waf.path_traversal_blocked", _path_traversal_signature),
)


def _inspect_value(field, value):
    """Returns (event_type, field, matched) for the first signature that
    fires against a single string value, or None."""
    if not isinstance(value, str) or not value:
        return None
    for event_type, check in _CHECKS:
        matched = check(value)
        if matched:
            return event_type, field, matched
    return None


def _flatten_json(obj, prefix=""):
    """Yields (field_path, string_value) pairs from an arbitrarily nested
    JSON body -- attack payloads can hide in any nested string field, not
    just top-level ones."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten_json(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten_json(v, f"{prefix}[{i}]")
    elif isinstance(obj, str):
        yield prefix, obj


def inspect_request(request):
    """Scans query string, form fields (not file bodies), JSON body, and
    path segments for known attack signatures. Returns the first
    (event_type, field, matched) found, or None if the request looks clean.
    """
    for key, value in request.args.items():
        hit = _inspect_value(f"args.{key}", value)
        if hit:
            return hit

    if request.form:
        for key, value in request.form.items():
            hit = _inspect_value(f"form.{key}", value)
            if hit:
                return hit

    if request.files:
        for key, file_storage in request.files.items():
            hit = _inspect_value(f"files.{key}.filename", file_storage.filename or "")
            if hit:
                return hit

    if request.is_json:
        body = request.get_json(silent=True)
        if body is not None:
            for field, value in _flatten_json(body):
                hit = _inspect_value(f"json.{field}", value)
                if hit:
                    return hit

    if request.view_args:
        for key, value in request.view_args.items():
            hit = _inspect_value(f"path.{key}", str(value))
            if hit:
                return hit

    return None


# ── Progressive auto-ban on repeated breaches ──────────────────────────────
# Per-worker in-memory counter -- same documented limitation as extensions.py's
# Flask-Limiter storage (no Redis in this stack). Under multiple gunicorn
# workers the effective threshold is BREACH_THRESHOLD * worker_count, which
# is an acceptable trade for a mechanism that needs zero extra infrastructure
# and still closes the loop: a scripted attacker sprays multiple workers at
# random, but every worker independently escalates to the same shared
# banned_ips row (database.py), so the ban itself is not per-worker even
# though the counter that triggers it is.
# Configurable via env so an operator can tune WAF sensitivity (a genuine
# false-positive rate, or a genuinely more/less hostile traffic mix) without
# a code change/redeploy -- same "sensible default, env overrides it"
# pattern used throughout this codebase. Defaults are the values this file
# has always shipped with, unchanged.
_BREACH_WINDOW_SECONDS = int(os.environ.get("WAF_BREACH_WINDOW_SECONDS", "600"))
_BREACH_THRESHOLD = int(os.environ.get("WAF_BREACH_THRESHOLD", "5"))
_BAN_MINUTES = int(os.environ.get("WAF_BAN_MINUTES", "15"))

_breach_lock = threading.Lock()
_breach_log = defaultdict(deque)  # ip -> deque[timestamp]


def record_breach_and_maybe_ban(ip, reason):
    """Call on every WAF block or rate-limit breach. Once an IP crosses
    _BREACH_THRESHOLD breaches inside _BREACH_WINDOW_SECONDS, inserts a
    temporary ban into banned_ips -- the same table and INSERT shape the SOC
    dashboard's manual ban-ip endpoint uses (blueprints/admin_views.py), so
    the existing _enforce_ip_ban before_request hook (app.py) blocks the IP
    on its very next request with no new blocking mechanism needed.

    Uses Redis (shared across gunicorn workers) when extensions.redis_client
    is configured, falling back to the in-memory per-worker counter
    otherwise -- including if a configured Redis becomes unreachable
    mid-request, so a transient Redis blip degrades the counter rather than
    breaking request handling.
    """
    if not ip:
        return
    if is_protected_ip(ip):
        # Loopback / RFC1918 / CGNAT / allow-listed, or an address we could
        # not parse: banning it would not stop one attacker, it would ban
        # every visitor that shares the address (see utils/client_ip.py).
        # The request itself was already rejected by whoever called us.
        _note_ban_skipped(ip, reason)
        return
    if redis_client is not None:
        try:
            _record_breach_redis(ip, reason)
            return
        except Exception as e:
            log_security_event(
                "waf.redis_error", f"Redis breach-counter call failed, using in-memory fallback: {e}",
                level="WARNING", identifier=ip,
            )
    if _record_breach_memory(ip):
        _auto_ban(ip, reason)


def _record_breach_redis(ip, reason):
    """Fixed-window counter (INCR + EXPIRE-if-new) rather than the
    in-memory version's exact rolling window -- a reasonable approximation
    for an anti-abuse threshold, and the same fixed-window approach
    Flask-Limiter's own Redis storage uses."""
    key = f"waf:breach:{ip}"
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, _BREACH_WINDOW_SECONDS)
    if count < _BREACH_THRESHOLD:
        return
    redis_client.delete(key)
    _auto_ban(ip, reason)


def _record_breach_memory(ip):
    """Returns True once the in-memory rolling-window counter for this IP
    crosses _BREACH_THRESHOLD (and resets it), False otherwise."""
    now = time.time()
    with _breach_lock:
        dq = _breach_log[ip]
        dq.append(now)
        while dq and now - dq[0] > _BREACH_WINDOW_SECONDS:
            dq.popleft()
        if len(dq) < _BREACH_THRESHOLD:
            return False
        dq.clear()
    return True


_SKIP_NOTE_INTERVAL_SECONDS = 600
_skip_note_lock = threading.Lock()
_skip_note_last = {}


def _note_ban_skipped(ip, reason):
    """Throttled (one per address per 10 min) WARNING so an operator can see
    that bans are being withheld -- the usual cause is the container
    gateway hiding client IPs, which is a deployment bug worth fixing, not
    a state to run in silently."""
    now = time.time()
    with _skip_note_lock:
        if now - _skip_note_last.get(ip, 0) < _SKIP_NOTE_INTERVAL_SECONDS:
            return
        _skip_note_last[ip] = now
    log_security_event(
        "waf.ban_skipped_shared_ip",
        "Auto-ban withheld: source address is non-routable/shared and cannot identify one client",
        level="WARNING", identifier=ip, reason=reason,
    )


def ban_ip(ip, reason, banned_by="system:auto", minutes=None):
    """Insert/extend a ban in public.banned_ips. The single write path for
    automated bans (WAF breaches, HTTP + TCP honeypots) so the shared-address
    protection and the "never shorten an existing ban" rule live in one
    place. Written to the `public` copy of the table on purpose: tenants each
    have their own banned_ips and the search_path would otherwise drop an
    automated ban into whichever tenant URL the attacker happened to be on,
    leaving every other tenant URL open to them (app.py's _enforce_ip_ban
    checks both). minutes=None bans until manually lifted. Returns True when
    a ban row was written."""
    if not ip or is_protected_ip(ip):
        return False
    expires_at = (datetime.datetime.now() + datetime.timedelta(minutes=minutes)) if minutes else None
    from database import get_db_connection
    try:
        db = get_db_connection()
        cursor = db.cursor()
        try:
            # An existing permanent ban (expires_at NULL) stays permanent,
            # and an existing longer ban is never shortened by a later,
            # shorter automated one.
            cursor.execute(
                "INSERT INTO public.banned_ips (ip, reason, banned_by, expires_at) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (ip) DO UPDATE SET reason=EXCLUDED.reason, banned_by=EXCLUDED.banned_by, "
                "banned_at=CURRENT_TIMESTAMP, "
                "expires_at=CASE WHEN banned_ips.expires_at IS NULL THEN NULL "
                "WHEN EXCLUDED.expires_at IS NULL THEN NULL "
                "ELSE GREATEST(banned_ips.expires_at, EXCLUDED.expires_at) END",
                (ip, reason, banned_by, expires_at),
            )
            db.commit()
        finally:
            cursor.close()
            db.close()
        return True
    except Exception as e:
        log_security_event(
            "waf.ban_write_failed", f"Failed to write IP ban: {e}",
            level="ERROR", identifier=ip,
        )
        return False


def _auto_ban(ip, reason):
    if ban_ip(ip, reason, banned_by="system:auto", minutes=_BAN_MINUTES):
        log_security_event(
            "waf.auto_ban", f"IP auto-banned for {_BAN_MINUTES} minutes after repeated breaches",
            level="ERROR", identifier=ip, reason=reason,
        )
