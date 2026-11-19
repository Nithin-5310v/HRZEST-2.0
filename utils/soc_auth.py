# -*- coding: utf-8 -*-
"""Authentication, sessions and audit for the SOC console (/socperson).

Deliberately separate from the tenant admin / employee / platform-admin
logins: its own accounts (att_master.soc_users), its own session keys
(`soc_*`), its own guard. A session from any other surface grants nothing
here, and a SOC session grants nothing anywhere else.

Controls
--------
* passwordless: an email address, then a 6-digit code sent to it
* only addresses listed in SOC_LOGIN_EMAILS can sign in ("a@x.com, b@y.com:analyst";
  the role defaults to admin). Any other address gets the same "check your inbox"
  screen and no email, so the form cannot be used to learn who is authorised
* codes: 10 minutes, single use (cleared by the statement that accepts them),
  stored only as an HMAC keyed with the app secret, at most one sent per minute
* lockout after 5 consecutive wrong codes for 15 minutes; failures are logged
* session: 15 min idle / 8 h absolute (env-tunable), re-validated against the
  database AND SOC_LOGIN_EMAILS on every request, so deactivation, removal from
  the list and role changes bite immediately
* network allow-list (SOC_ALLOWED_CIDRS): other sources get a 404. Unset, the
  console is closed everywhere except APP_ENV=development
* roles: viewer < analyst < admin, enforced per route
* every login, failure and action is written to the append-only soc_audit_log
"""
import functools
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import time

from flask import current_app, g, jsonify, redirect, request, session

from utils.soc_db import read_cursor, write_cursor

ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}
DEFAULT_ROLE = "admin"
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
LOGIN_CODE_TTL_SECONDS = 10 * 60
RESEND_SECONDS = 60
PENDING_TTL_SECONDS = LOGIN_CODE_TTL_SECONDS
_EMAIL_RE = re.compile(r"^[^@\s,:]+@[^@\s,:]+\.[^@\s,:]+$")
_USERNAME_MAX = 60          # soc_users.username / soc_audit_log.actor
# Accounts made for SOC_LOGIN_EMAILS never had a password; this value is not a
# valid hash, so nothing can ever verify against it.
_NO_PASSWORD = "!email-code-login"


def _env_int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


def idle_seconds():
    return _env_int("SOC_IDLE_MINUTES", 15, 1, 240) * 60


def absolute_seconds():
    return _env_int("SOC_SESSION_HOURS", 8, 1, 24) * 3600


def _is_development():
    return os.environ.get("APP_ENV", "production") == "development"


# ── network allow-list ───────────────────────────────────────────────
_NETS_CACHE = (None, [])
_EVERYWHERE = (ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0"))


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
    """True when `ip` is inside SOC_ALLOWED_CIDRS. Unset, the console is open only
    in local development (APP_ENV=development); everywhere else it stays closed
    until the operator lists the networks -- `0.0.0.0/0,::/0` opens it on purpose.
    A configured but unparseable list fails CLOSED (nothing allowed)."""
    raw = os.environ.get("SOC_ALLOWED_CIDRS", "").strip()
    if not raw:
        return _is_development()
    nets = _allowed_networks()
    if not nets:
        return False
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return any(addr in n for n in nets)


def allow_list_state():
    """-> one of: restricted | everywhere | invalid | unset_open (development) |
    unset_closed. For the posture check; never the ranges themselves."""
    if not os.environ.get("SOC_ALLOWED_CIDRS", "").strip():
        return "unset_open" if _is_development() else "unset_closed"
    nets = _allowed_networks()
    if not nets:
        return "invalid"
    return "everywhere" if any(n in nets for n in _EVERYWHERE) else "restricted"


def reachable():
    """False when this request must be answered with a plain 404: it came from outside
    the allow-list, or under a tenant URL prefix (/<company>/socperson -- the tenant middleware
    would strip the slug and serve the console there). The console exists at /socperson only."""
    if request.environ.get("hrz.tenant_slug"):
        return False
    return ip_allowed(request.remote_addr)


# ── who may sign in ──────────────────────────────────────────────────
_EMAILS_CACHE = (None, {})


def normalize_email(value):
    return value.strip().lower()[:200] if isinstance(value, str) else ""


def looks_like_email(value):
    return bool(_EMAIL_RE.match(value or ""))


def login_emails():
    """{address: role} from SOC_LOGIN_EMAILS, e.g. "you@example.com, ana@example.com:analyst".
    Entries with an unknown role or an address too long to be a username are
    skipped (and logged), never widened to a default."""
    global _EMAILS_CACHE
    raw = os.environ.get("SOC_LOGIN_EMAILS", "").strip()
    if _EMAILS_CACHE[0] == raw:
        return _EMAILS_CACHE[1]
    allowed = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        addr, _, role = entry.partition(":")
        addr, role = normalize_email(addr), (role.strip().lower() or DEFAULT_ROLE)
        if not looks_like_email(addr) or len(addr) > _USERNAME_MAX or role not in ROLE_RANK:
            _log_warning("SOC_LOGIN_EMAILS: ignoring an invalid entry (bad address, longer than "
                         f"{_USERNAME_MAX} characters, or unknown role)")
            continue
        allowed[addr] = role
    _EMAILS_CACHE = (raw, allowed)
    return allowed


def login_role(email):
    """The role SOC_LOGIN_EMAILS grants `email`, or None when it is not listed."""
    return login_emails().get(normalize_email(email))


def _log_warning(msg):
    try:
        from extensions import app_log
        app_log.warning(msg)
    except Exception:
        pass


def _mask(email):
    local, _, domain = (email or "").partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


# ── audit ────────────────────────────────────────────────────────────
_LOGIN_ACTIONS = frozenset({"login", "login_failed", "account_locked", "login_blocked_locked", "login_blocked_disabled",
                            "login_code_sent", "login_code_undelivered", "logout"})


def audit(actor, action, target=None, detail=None, ip=None):
    """Append to soc_audit_log. Never raises: auditing must not break the action
    (a failure is logged loudly instead). -> the stored row's created_at, or
    None when the write failed."""
    stored_at = None
    try:
        with write_cursor("att_master") as cur:
            cur.execute(
                "INSERT INTO soc_audit_log (actor, action, target, detail, ip) VALUES (%s,%s,%s,%s,%s) RETURNING created_at",
                ((actor or "-")[:60], action[:80], (target or None) and str(target)[:200],
                 (detail or None) and str(detail)[:2000], (ip or None) and str(ip)[:64]))
            stored_at = cur.fetchone()[0]
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
    return stored_at


def event(event_type, message, level="WARNING", **fields):
    """Mirror a SOC auth event into the normal security stream (so the console
    sees its own use) without ever raising."""
    try:
        from extensions import log_security_event
        log_security_event(event_type, message, level=level, **fields)
    except Exception:
        pass


# ── users ────────────────────────────────────────────────────────────
_USER_COLS = ("id", "username", "email", "role", "active", "failed_count", "locked_until", "last_login_at",
              "last_login_ip", "created_by", "created_at")


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


def get_user_by_email(email):
    """The console account for `email`: active accounts first, then the oldest."""
    email = normalize_email(email)
    if not email:
        return None
    with read_cursor("att_master") as cur:
        cur.execute("SELECT " + _SELECT + " FROM soc_users WHERE lower(email)=%s "  # nosec B608 -- constant column list
                    "ORDER BY active DESC, id LIMIT 1", (email,))
        return _row_user(cur.fetchone())


def public_user(u):
    """The fields safe to return to a browser."""
    return {k: (v.isoformat(sep=" ") if hasattr(v, "isoformat") else v) for k, v in u.items()
            if k in ("id", "username", "email", "role", "active", "failed_count",
                     "locked_until", "is_locked", "last_login_at", "last_login_ip", "created_by", "created_at")}


def list_users():
    with read_cursor("att_master") as cur:
        cur.execute("SELECT " + _SELECT + " FROM soc_users ORDER BY username")  # nosec B608 -- constant column list
        users = [public_user(_row_user(r)) for r in cur.fetchall()]
    for u in users:
        u["listed"] = login_role(u.get("email")) is not None
    return users


def _ensure_user(email, role):
    """Find or create the account for an address SOC_LOGIN_EMAILS authorises, with
    its role brought in line with the list. -> user | None."""
    user = get_user_by_email(email) or get_user(email)
    if user is None:
        try:
            with write_cursor("att_master") as cur:
                cur.execute(
                    "INSERT INTO soc_users (username, email, password_hash, role, created_by) "
                    "VALUES (%s,%s,%s,%s,'SOC_LOGIN_EMAILS') ON CONFLICT (username) DO NOTHING",
                    (email, email, _NO_PASSWORD, role))
        except Exception as exc:    # pragma: no cover - logged for operators
            _log_warning(f"SOC account for {_mask(email)} could not be created: {exc}")
            return None
        user = get_user_by_email(email) or get_user(email)
    if user is not None and user["role"] != role:
        update_user(user["id"], role=role)
        user = get_user_by_id(user["id"])
    return user


def update_user(uid, *, role=None, active=None):
    sets, params = [], []
    if role is not None:
        if not isinstance(role, str) or role not in ROLE_RANK:
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


def unlock(uid):
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET failed_count=0, locked_until=NULL WHERE id=%s", (uid,))


def active_admin_count():
    with read_cursor("att_master") as cur:
        cur.execute("SELECT COUNT(*) FROM soc_users WHERE role='admin' AND active")
        return cur.fetchone()[0]


def register_failure(user, ip, reason):
    """Count one failed attempt against the account and lock it after
    MAX_FAILURES. -> True when this failure locked the account."""
    with write_cursor("att_master") as cur:
        cur.execute(
            "UPDATE soc_users SET failed_count = failed_count + 1, "
            "locked_until = CASE WHEN failed_count + 1 >= %s THEN NOW() + (%s * INTERVAL '1 second') ELSE locked_until END "
            "WHERE id=%s RETURNING failed_count",
            (MAX_FAILURES, LOCKOUT_SECONDS, user["id"]))
        fails = cur.fetchone()[0]
    audit(user["username"], "login_failed", detail=f"{reason} (failure {fails})", ip=ip)
    if fails >= MAX_FAILURES:
        # A locked account's outstanding code is void: it cannot be used once the lock lifts.
        _clear_code(user["id"])
        event("soc.lockout", "SOC account locked after repeated failures", level="ERROR", identifier=user["username"])
        audit(user["username"], "account_locked", ip=ip)
        return True
    event("soc.login_failed", "SOC login failed", identifier=user["username"])
    return False


# ── one-time email codes ─────────────────────────────────────────────
def _code_hash(uid, code):
    """Codes are stored only as an HMAC keyed with the app's secret: a copy of the
    table alone is not enough to recover one (6 digits would fall to a plain hash)."""
    key = current_app.secret_key
    key = key.encode() if isinstance(key, str) else (key or b"")
    return hmac.new(key, f"soc-login:{uid}:{code}".encode(), hashlib.sha256).hexdigest()


def _clear_code(uid):
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET login_code_hash=NULL, login_code_expires_at=NULL WHERE id=%s", (uid,))


def deliver_login_code(email, code):
    """Email the code. -> True once it is queued. Without SMTP the code is written
    to the log only under APP_ENV=development; in production it is never logged,
    since anyone who can read the logs would otherwise hold a working sign-in."""
    import html as _html
    from extensions import app_log
    from utils.email_utils import get_email_config, send_email_async
    cfg = get_email_config()
    if not cfg:
        if _is_development():
            app_log.warning("SOC sign-in code for %s is %s (no SMTP configured; development only)", email, code)
        else:
            app_log.error("SOC sign-in code for %s was not sent: SMTP is not configured.", _mask(email))
        return False
    minutes = LOGIN_CODE_TTL_SECONDS // 60
    body = f"""
<div style="font-family:Segoe UI,sans-serif;max-width:480px;margin:auto;background:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #1e293b;">
  <div style="background:#1d4ed8;padding:22px 26px;color:#fff;">
    <div style="font-size:18px;font-weight:700;">Security Operations sign-in</div>
  </div>
  <div style="padding:26px;">
    <p style="color:#cbd5e1;font-size:13px;">Use this code to finish signing in. It expires in {minutes} minutes and can only be used once.</p>
    <div style="text-align:center;margin:22px 0;padding:16px;background:#090d16;border-radius:12px;">
      <span style="font-size:32px;font-weight:800;letter-spacing:6px;color:#60a5fa;">{_html.escape(code)}</span>
    </div>
    <p style="font-size:12px;color:#64748b;">If you did not just try to sign in, someone else entered your address.
      They cannot get in without this code; do not share it.</p>
  </div>
</div>"""
    send_email_async(email, "Your Security Operations sign-in code", body, cfg)
    return True


def request_login_code(email, ip):
    """Step 1. Sends a code when `email` is authorised, the account is usable and no
    code went out in the last RESEND_SECONDS. Returns nothing on purpose: the
    caller shows the same screen whatever happened here."""
    email = normalize_email(email)
    role = login_role(email)
    if role is None:
        audit(email or "-", "login_failed", detail="code requested for an address that is not authorised", ip=ip)
        event("soc.login_failed", "SOC sign-in code refused (address not authorised)", identifier=email[:60])
        return
    user = _ensure_user(email, role)
    if user is None:
        return
    if user["is_locked"]:
        audit(user["username"], "login_blocked_locked", ip=ip)
        return
    if not user["active"]:
        audit(user["username"], "login_blocked_disabled", ip=ip)
        return
    code = f"{secrets.randbelow(10 ** 6):06d}"
    with write_cursor("att_master") as cur:
        # One code per RESEND_SECONDS, claimed atomically; a new code replaces the old.
        cur.execute(
            "UPDATE soc_users SET login_code_hash=%s, login_code_expires_at=NOW() + (%s * INTERVAL '1 second'), "
            "login_code_sent_at=NOW() WHERE id=%s "
            "AND (login_code_sent_at IS NULL OR login_code_sent_at < NOW() - (%s * INTERVAL '1 second'))",
            (_code_hash(user["id"], code), LOGIN_CODE_TTL_SECONDS, user["id"], RESEND_SECONDS))
        claimed = cur.rowcount == 1
    if not claimed:
        return                      # one went out moments ago and is still valid
    if deliver_login_code(email, code):
        audit(user["username"], "login_code_sent", ip=ip)
        event("soc.login_code_sent", "SOC sign-in code sent", level="INFO", identifier=user["username"])
    else:
        audit(user["username"], "login_code_undelivered", detail="email not configured", ip=ip)


def verify_login_code(email, code, ip):
    """Step 2. -> (user | None, status); status in: ok | bad_code | locked.
    The caller shows the SAME message for every bad_code."""
    email = normalize_email(email)
    code = re.sub(r"\s+", "", code or "")
    role = login_role(email)
    user = get_user_by_email(email) if role else None
    if user is None:
        audit(email or "-", "login_failed", detail="code entered for an address that is not authorised", ip=ip)
        event("soc.login_failed", "SOC login failed (address not authorised)", identifier=email[:60])
        return None, "bad_code"
    if user["is_locked"]:
        audit(user["username"], "login_blocked_locked", ip=ip)
        return None, "locked"
    if not user["active"]:
        audit(user["username"], "login_blocked_disabled", ip=ip)
        return None, "bad_code"
    if re.fullmatch(r"\d{6}", code):
        with write_cursor("att_master") as cur:
            # Atomic single use: the statement that accepts the code also clears it,
            # so two submissions of one code cannot both win.
            cur.execute(
                "UPDATE soc_users SET login_code_hash=NULL, login_code_expires_at=NULL "
                "WHERE id=%s AND login_code_hash=%s AND login_code_expires_at > NOW() RETURNING id",
                (user["id"], _code_hash(user["id"], code)))
            accepted = cur.fetchone() is not None
        if accepted:
            if user["role"] != role:
                update_user(user["id"], role=role)
            return get_user_by_id(user["id"]), "ok"
    return None, ("locked" if register_failure(user, ip, "bad sign-in code") else "bad_code")


# ── session ──────────────────────────────────────────────────────────
_SESSION_KEYS = ("soc_uid", "soc_user", "soc_role", "soc_at", "soc_last", "soc_pending_email", "soc_pending_at",
                 "soc_pk_uid", "soc_pk_at", "soc_pk_challenge", "soc_pk_mode")
PASSKEY_STEP_SECONDS = 5 * 60


def start_passkey_step(user):
    """The emailed code was right: the passkey (utils/soc_passkeys.py) is all that
    stands between this browser and a session. Nothing else carries over."""
    for k in _SESSION_KEYS:
        session.pop(k, None)
    session["soc_pk_uid"] = user["id"]
    session["soc_pk_at"] = time.time()


def passkey_step_user():
    """The user half-way through sign-in (code done, passkey not yet), still
    active, unlocked and listed -- or None."""
    uid = session.get("soc_pk_uid")
    if not uid or time.time() - session.get("soc_pk_at", 0) > PASSKEY_STEP_SECONDS:
        return None
    user = get_user_by_id(uid)
    if not user or not user["active"] or user["is_locked"] or login_role(user.get("email")) is None:
        return None
    return user


def start_pending(email):
    for k in _SESSION_KEYS:
        session.pop(k, None)
    session["soc_pending_email"] = normalize_email(email)
    session["soc_pending_at"] = time.time()


def pending_email():
    email = session.get("soc_pending_email")
    if not email or (time.time() - session.get("soc_pending_at", 0)) > PENDING_TTL_SECONDS:
        return None
    return email


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
    # A role change since login ends the session at once -- in the database or in
    # SOC_LOGIN_EMAILS -- and so does taking the address off that list.
    if user["role"] != session.get("soc_role") or login_role(user.get("email")) != user["role"]:
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
    """Route guard. Pages redirect to /socperson/login; API calls get JSON 401/403."""
    need = ROLE_RANK[min_role]

    def deco(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            api = request.path.startswith("/socperson/api/")
            if not reachable():
                event("soc.ip_denied", "SOC console request refused (outside allow-list or tenant-prefixed)", ip=request.remote_addr)
                return ("Not found", 404)
            user = _session_user()
            if user is None:
                if getattr(g, "_soc_db_error", False) and api:
                    return jsonify({"ok": False, "msg": "SOC datastore unavailable"}), 503
                if api:
                    return jsonify({"ok": False, "msg": "Sign in required"}), 401
                return redirect("/socperson/login")
            if ROLE_RANK.get(user["role"], 0) < need:
                audit(user["username"], "forbidden", target=request.path, ip=request.remote_addr)
                return jsonify({"ok": False, "msg": "Your role cannot do that"}), 403
            return view(*args, **kwargs)
        wrapped._hrz_auth_guard = "soc"     # read by tests/test_route_auth_guards.py
        return wrapped
    return deco
