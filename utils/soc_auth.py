# -*- coding: utf-8 -*-
"""Authentication, sessions and audit for the SOC console.

Deliberately separate from the tenant admin / employee / platform-admin
logins: its own accounts (att_master.soc_users), its own session keys
(`soc_*`), its own guard. A session from any other surface grants nothing
here, and a SOC session grants nothing anywhere else.

Controls
--------
* password (>= 12 chars) + mandatory TOTP; no way to skip the second factor
* TOTP codes are single-use (the accepted 30 s step is recorded atomically)
* lockout after 5 consecutive failures for 15 minutes; failures are logged
* unknown usernames cost the same hashing work as known ones (no timing oracle)
* session: 15 min idle / 8 h absolute (env-tunable), re-validated against the
  database on every request so deactivation and role changes bite immediately
* optional network allow-list (SOC_ALLOWED_CIDRS): other sources get a 404
* roles: viewer < analyst < admin, enforced per route
* every login, failure and action is written to the append-only soc_audit_log
"""
import functools
import ipaddress
import os
import re
import secrets
import time

import pyotp
from flask import g, jsonify, redirect, request, session

from utils.soc_db import read_cursor, write_cursor

ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}
MIN_PASSWORD_LENGTH = 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
PENDING_TTL_SECONDS = 5 * 60
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,39}$")
_ISSUER = "HRzest SOC"

# A valid hash of a random value, verified against when the username is unknown
# so a miss costs the same as a hit.
_DUMMY_HASH = None


def _env_int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


def idle_seconds():
    return _env_int("SOC_IDLE_MINUTES", 15, 1, 240) * 60


def absolute_seconds():
    return _env_int("SOC_SESSION_HOURS", 8, 1, 24) * 3600


# ── network allow-list ───────────────────────────────────────────────
_NETS_CACHE = (None, [])


def _allowed_networks():
    global _NETS_CACHE
    raw = os.environ.get("SOC_ALLOWED_CIDRS", "").strip()
    if _NETS_CACHE[0] == raw:
        return _NETS_CACHE[1]
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    _NETS_CACHE = (raw, nets)
    return nets


def ip_allowed(ip):
    """True when no allow-list is configured, or `ip` is inside it. A configured
    but unparseable list fails CLOSED (nothing allowed) rather than open."""
    raw = os.environ.get("SOC_ALLOWED_CIDRS", "").strip()
    if not raw:
        return True
    nets = _allowed_networks()
    if not nets:
        return False
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return any(addr in n for n in nets)


def reachable():
    """False when this request must be answered with a plain 404: it came from outside
    the allow-list, or under a tenant URL prefix (/<company>/soc -- the tenant middleware
    would strip the slug and serve the console there). The console exists at /soc only."""
    if request.environ.get("hrz.tenant_slug"):
        return False
    return ip_allowed(request.remote_addr)


# ── audit ────────────────────────────────────────────────────────────
_LOGIN_ACTIONS = frozenset({"login", "login_failed", "account_locked", "login_blocked_locked", "login_blocked_disabled",
                            "logout"})


def audit(actor, action, target=None, detail=None, ip=None):
    """Append to soc_audit_log. Never raises: auditing must not break the action
    (a failure is logged loudly instead)."""
    try:
        with write_cursor("att_master") as cur:
            cur.execute(
                "INSERT INTO soc_audit_log (actor, action, target, detail, ip) VALUES (%s,%s,%s,%s,%s)",
                ((actor or "-")[:60], action[:80], (target or None) and str(target)[:200],
                 (detail or None) and str(detail)[:2000], (ip or None) and str(ip)[:64]))
    except Exception as exc:        # pragma: no cover - logged for operators
        try:
            from extensions import app_log
            app_log.error("soc_audit_log write failed (%s): %s", action, exc)
        except Exception:
            pass
    # Login events are mirrored by their own call sites; mirror everything else
    # so analyst actions also reach the off-box log stream.
    if action not in _LOGIN_ACTIONS:
        event("soc.action", f"SOC action: {action}", level="INFO", identifier=actor, soc_action=action,
              soc_target=(str(target)[:120] if target else None))


def event(event_type, message, level="WARNING", **fields):
    """Mirror a SOC auth event into the normal security stream (so the console
    sees its own use) without ever raising."""
    try:
        from extensions import log_security_event
        log_security_event(event_type, message, level=level, **fields)
    except Exception:
        pass


# ── users ────────────────────────────────────────────────────────────
_USER_COLS = ("id", "username", "email", "password_hash", "role", "totp_secret", "totp_enabled",
              "totp_last_step", "enroll_code_hash", "active", "failed_count", "locked_until", "last_login_at",
              "last_login_ip", "password_changed_at", "created_by", "created_at")


# `is_locked` is computed by the database (its own clock and timezone), never by
# comparing a naive TIMESTAMP with time.time() in the app's timezone.
_SELECT = ", ".join(_USER_COLS) + ", (locked_until IS NOT NULL AND locked_until > NOW())"


def _row_user(row):
    if not row:
        return None
    d = dict(zip(_USER_COLS, row))
    d["is_locked"] = bool(row[len(_USER_COLS)])
    return d


def get_user(username):
    with read_cursor("att_master") as cur:
        cur.execute("SELECT " + _SELECT + " FROM soc_users WHERE username=%s",  # nosec B608 -- constant column list
                    ((username or "").strip().lower(),))
        return _row_user(cur.fetchone())


def get_user_by_id(uid):
    with read_cursor("att_master") as cur:
        cur.execute("SELECT " + _SELECT + " FROM soc_users WHERE id=%s", (uid,))  # nosec B608 -- constant column list
        return _row_user(cur.fetchone())


def public_user(u):
    """The fields safe to return to a browser."""
    return {k: (v.isoformat(sep=" ") if hasattr(v, "isoformat") else v) for k, v in u.items()
            if k in ("id", "username", "email", "role", "totp_enabled", "active", "failed_count",
                     "locked_until", "last_login_at", "last_login_ip", "created_by", "created_at")}


def list_users():
    with read_cursor("att_master") as cur:
        cur.execute("SELECT " + _SELECT + " FROM soc_users ORDER BY username")  # nosec B608 -- constant column list
        return [public_user(_row_user(r)) for r in cur.fetchall()]


def validate_password(pw, username=""):
    if not pw or len(pw) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if username and username.lower() in pw.lower():
        return False, "Password must not contain the username."
    if len(set(pw)) < 5:
        return False, "Password is too repetitive."
    return True, None


_SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O/1/I


def new_setup_code():
    """12 characters (~60 bits) shown once to whoever creates / resets the
    account and handed to the user out-of-band with the initial password."""
    raw = "".join(secrets.choice(_SETUP_ALPHABET) for _ in range(12))
    return raw[:4] + "-" + raw[4:8] + "-" + raw[8:]


def _norm_setup_code(code):
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def create_user(username, password, role="viewer", email=None, created_by=None):
    """-> (ok, message, setup_code). The caller audits. `setup_code` is returned
    exactly once and only its hash is stored: a new account cannot enrol its
    authenticator without it, so someone who learns only the password cannot
    claim the second factor first."""
    from utils.auth import generate_password_hash
    username = (username or "").strip().lower()
    if not _USERNAME_RE.match(username):
        return False, "Username must be 3-40 characters: lowercase letters, digits, dot, dash, underscore.", None
    if role not in ROLE_RANK:
        return False, "Unknown role.", None
    ok, err = validate_password(password, username)
    if not ok:
        return False, err, None
    code = new_setup_code()
    try:
        with write_cursor("att_master") as cur:
            cur.execute(
                "INSERT INTO soc_users (username, email, password_hash, role, created_by, enroll_code_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING RETURNING id",
                (username, (email or None) and email[:200], generate_password_hash(password), role, created_by,
                 generate_password_hash(_norm_setup_code(code))))
            if cur.fetchone() is None:
                return False, "That username already exists.", None
    except Exception:
        return False, "Could not create the account.", None
    return True, "created", code


def verify_setup_code(user, code):
    from utils.auth import check_password_hash
    if not user.get("enroll_code_hash"):
        return False
    return check_password_hash(user["enroll_code_hash"], _norm_setup_code(code))


def set_password(uid, new_password, username=""):
    from utils.auth import generate_password_hash
    ok, err = validate_password(new_password, username)
    if not ok:
        return False, err
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET password_hash=%s, password_changed_at=NOW(), failed_count=0, "
                    "locked_until=NULL WHERE id=%s", (generate_password_hash(new_password), uid))
    return True, None


def update_user(uid, *, role=None, active=None):
    sets, params = [], []
    if role is not None:
        if role not in ROLE_RANK:
            return False
        sets.append("role=%s")
        params.append(role)
    if active is not None:
        sets.append("active=%s")
        params.append(bool(active))
    if not sets:
        return False
    params.append(uid)
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET " + ", ".join(sets) + " WHERE id=%s", params)  # nosec B608 -- fixed column names
        return cur.rowcount == 1


def reset_mfa(uid):
    """Remove the user's authenticator and issue a fresh one-time setup code
    (returned once; only its hash is stored)."""
    from utils.auth import generate_password_hash
    code = new_setup_code()
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET totp_secret=NULL, totp_enabled=FALSE, totp_last_step=0, enroll_code_hash=%s "
                    "WHERE id=%s", (generate_password_hash(_norm_setup_code(code)), uid))
    return code


def unlock(uid):
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET failed_count=0, locked_until=NULL WHERE id=%s", (uid,))


def active_admin_count():
    with read_cursor("att_master") as cur:
        cur.execute("SELECT COUNT(*) FROM soc_users WHERE role='admin' AND active")
        return cur.fetchone()[0]


# ── password step ────────────────────────────────────────────────────
def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        from utils.auth import generate_password_hash
        _DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(24))
    return _DUMMY_HASH


def register_failure(user, ip, reason):
    """Count one failed attempt (password, setup code or TOTP) against the account
    and lock it after MAX_FAILURES. -> True when this failure locked the account."""
    with write_cursor("att_master") as cur:
        cur.execute(
            "UPDATE soc_users SET failed_count = failed_count + 1, "
            "locked_until = CASE WHEN failed_count + 1 >= %s THEN NOW() + (%s * INTERVAL '1 second') ELSE locked_until END "
            "WHERE id=%s RETURNING failed_count",
            (MAX_FAILURES, LOCKOUT_SECONDS, user["id"]))
        fails = cur.fetchone()[0]
    audit(user["username"], "login_failed", detail=f"{reason} (failure {fails})", ip=ip)
    if fails >= MAX_FAILURES:
        event("soc.lockout", "SOC account locked after repeated failures", level="ERROR", identifier=user["username"])
        audit(user["username"], "account_locked", ip=ip)
        return True
    event("soc.login_failed", "SOC login failed", identifier=user["username"])
    return False


def authenticate(username, password, ip):
    """-> (user | None, code). code in: ok | bad_credentials | locked | disabled.
    The caller must show the SAME message for bad_credentials and disabled."""
    from utils.auth import check_password_hash
    user = get_user(username)
    stored = user["password_hash"] if user else _dummy_hash()
    good = check_password_hash(stored, password or "")
    if user is None:
        audit(username or "-", "login_failed", detail="unknown user", ip=ip)
        event("soc.login_failed", "SOC login failed (unknown user)", identifier=(username or "")[:60])
        return None, "bad_credentials"
    if user["is_locked"]:
        audit(user["username"], "login_blocked_locked", ip=ip)
        return None, "locked"
    if not good:
        return None, ("locked" if register_failure(user, ip, "bad password") else "bad_credentials")
    if not user["active"]:
        audit(user["username"], "login_blocked_disabled", ip=ip)
        return None, "disabled"
    return user, "ok"


# ── TOTP ─────────────────────────────────────────────────────────────
def new_totp_secret(uid):
    """Generate and store (encrypted, not yet enabled) a fresh secret."""
    from utils.helpers import encrypt_pii
    secret = pyotp.random_base32()
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET totp_secret=%s, totp_enabled=FALSE, totp_last_step=0 WHERE id=%s",
                    (encrypt_pii(secret), uid))
    return secret


def totp_qr(username, secret):
    import base64
    import io
    import qrcode
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=_ISSUER)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def verify_totp(user, code, *, enabling=False, now=None):
    """True once per 30 s step. `enabling` marks the factor active on first success."""
    from utils.helpers import decrypt_pii
    code = re.sub(r"\s+", "", code or "")
    if not re.fullmatch(r"\d{6}", code) or not user.get("totp_secret"):
        return False
    secret = decrypt_pii(user["totp_secret"])
    totp = pyotp.TOTP(secret)
    t = time.time() if now is None else now
    current = int(t // 30)
    for step in (current - 1, current, current + 1):
        if step <= (user.get("totp_last_step") or 0):
            continue
        if secrets.compare_digest(totp.at(step * 30), code):
            with write_cursor("att_master") as cur:
                # Atomic claim: two concurrent submissions of one code cannot both win.
                cur.execute(
                    "UPDATE soc_users SET totp_last_step=%s" + (", totp_enabled=TRUE, enroll_code_hash=NULL" if enabling else "") +
                    " WHERE id=%s AND totp_last_step < %s", (step, user["id"], step))
                return cur.rowcount == 1
    return False


# ── session ──────────────────────────────────────────────────────────
_SESSION_KEYS = ("soc_uid", "soc_user", "soc_role", "soc_at", "soc_last", "soc_pwv", "soc_pending",
                 "soc_pending_at", "soc_setup_ok")


def start_pending(user):
    for k in _SESSION_KEYS:
        session.pop(k, None)
    session["soc_pending"] = user["id"]
    session["soc_pending_at"] = time.time()


def pending_user():
    uid = session.get("soc_pending")
    if not uid or (time.time() - session.get("soc_pending_at", 0)) > PENDING_TTL_SECONDS:
        return None
    return get_user_by_id(uid)


def establish(user, ip):
    """Promote a fully-verified user to a session. Starts a fresh session (and a
    fresh CSRF token) so nothing from before login survives into it."""
    session.clear()
    now = time.time()
    session["soc_uid"] = user["id"]
    session["soc_user"] = user["username"]
    session["soc_role"] = user["role"]
    session["soc_at"] = now
    session["soc_last"] = now
    # Opaque stamp of the credential this session was issued against; a later
    # password change makes it differ, which ends the session (no clock maths).
    session["soc_pwv"] = str(user.get("password_changed_at"))
    session["_csrf"] = secrets.token_hex(32)
    session.permanent = False
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET failed_count=0, locked_until=NULL, last_login_at=NOW(), last_login_ip=%s "
                    "WHERE id=%s", (ip, user["id"]))
    audit(user["username"], "login", ip=ip)
    event("soc.login_success", "SOC console login", level="INFO", identifier=user["username"])


def end_session():
    who = session.get("soc_user")
    for k in _SESSION_KEYS:
        session.pop(k, None)
    return who


def _session_user():
    """The live, DB-validated user for this request's session, or None."""
    if hasattr(g, "_soc_user"):
        return g._soc_user
    g._soc_user = None
    uid = session.get("soc_uid")
    if not uid:
        return None
    now = time.time()
    if now - session.get("soc_last", 0) > idle_seconds() or now - session.get("soc_at", 0) > absolute_seconds():
        end_session()
        return None
    try:
        user = get_user_by_id(uid)
    except Exception:
        g._soc_db_error = True          # fail closed; the guard answers 503 for API calls
        return None
    if not user or not user["active"]:
        end_session()
        return None
    # A role or password change since login ends the session at once.
    if user["role"] != session.get("soc_role"):
        end_session()
        return None
    if str(user.get("password_changed_at")) != session.get("soc_pwv"):
        end_session()
        return None
    # Background polling (X-SOC-Poll) must not count as activity, or an unattended
    # open tab would keep the session alive forever and defeat the idle timeout.
    if request.headers.get("X-SOC-Poll") != "1":
        session["soc_last"] = now
    g._soc_user = user
    return user


def current_user():
    return _session_user()


def soc_required(min_role="viewer"):
    """Route guard. Pages redirect to /soc/login; API calls get JSON 401/403."""
    need = ROLE_RANK[min_role]

    def deco(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            api = request.path.startswith("/soc/api/")
            if not reachable():
                event("soc.ip_denied", "SOC console request refused (outside allow-list or tenant-prefixed)", ip=request.remote_addr)
                return ("Not found", 404)
            user = _session_user()
            if user is None:
                if getattr(g, "_soc_db_error", False) and api:
                    return jsonify({"ok": False, "msg": "SOC datastore unavailable"}), 503
                if api:
                    return jsonify({"ok": False, "msg": "Sign in required"}), 401
                return redirect("/soc/login")
            if ROLE_RANK.get(user["role"], 0) < need:
                audit(user["username"], "forbidden", target=request.path, ip=request.remote_addr)
                return jsonify({"ok": False, "msg": "Your role cannot do that"}), 403
            return view(*args, **kwargs)
        wrapped._hrz_auth_guard = "soc"     # read by tests/test_route_auth_guards.py
        return wrapped
    return deco
