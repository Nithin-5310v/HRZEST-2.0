# -*- coding: utf-8 -*-
"""Sign out everyone: one platform-wide cut-off time.

Anything signed in before the cut-off is refused on its next request, in every
company at once, and the person simply signs in again:

  * browser sessions: app_setup/security.py's _enforce_session_lifetime compares
    the session's _session_created with cut_off_epoch();
  * API tokens (web pages and the mobile app): utils/auth._TOKEN_NOT_STALE_SQL,
    which every Bearer check shares, refuses a token created before it;
  * mobile WebView bridge links (blueprints/core.py) likewise;
  * the session registry (utils/session_registry.ALIVE_SQL) stops listing them.

Sessions are signed cookies with no server-side store, so without this the
only way to end all of them at once is to change SECRET_KEY and restart.

Set from the SOC console (Response -> Signed in -> Sign out everyone, admin
role) or, when the console is out of reach, scripts/sign_out_everyone.py.

The time is one row of public.session_cutoff, always read schema-qualified so
it applies whichever company the request belongs to. SOC console sessions are
not affected: they carry no _session_created and have their own
"Sign out everywhere".
"""
import time

# No app imports at module level: utils/auth.py imports this while it loads.

# How long a worker trusts its copy of the cut-off. Another worker's session
# can outlive a new cut-off by at most this long.
CACHE_SECONDS = 5

_cache = {"at": float("-inf"), "epoch": 0.0}


def issued_before_cutoff_sql(column):
    """SQL that is true when `column` (an issue time, TIMESTAMP, a call-site
    literal such as "t.created_at") is before the cut-off."""
    return f"EXISTS (SELECT 1 FROM public.session_cutoff sc WHERE sc.valid_after > {column})"  # nosec B608 -- a call-site literal


def _read():
    from utils.helpers.db import _db
    with _db() as (cur, _conn):
        cur.execute("SELECT EXTRACT(EPOCH FROM valid_after), set_by FROM public.session_cutoff WHERE id = 1")
        return cur.fetchone()


def cut_off_epoch():
    """The cut-off as epoch seconds; 0.0 when nobody ever set one. If the read
    fails, the last value read stays in force: a database hiccup must neither
    take the app down nor let back in sessions that were ended."""
    now = time.monotonic()
    if now - _cache["at"] < CACHE_SECONDS:
        return _cache["epoch"]
    try:
        row = _read()
        _cache["epoch"] = float(row[0]) if row else 0.0
    except Exception as exc:
        from extensions import app_log
        app_log.warning("session cut-off unreadable, keeping the last value: %s", type(exc).__name__)
    _cache["at"] = now
    return _cache["epoch"]


def last_cutoff():
    """-> {"at": datetime, "by": who} of the current cut-off, or None. Uncached.
    `at` is the database's own wall clock (no zone), like every other
    timestamp the SOC console shows."""
    from utils.helpers.db import _db
    with _db() as (cur, _conn):
        cur.execute("SELECT valid_after::timestamp, set_by FROM public.session_cutoff WHERE id = 1")
        row = cur.fetchone()
    return {"at": row[0], "by": row[1]} if row else None


def sign_out_everyone(actor):
    """Move the cut-off to now. -> the new cut-off as epoch seconds."""
    from utils.helpers.db import _db
    with _db() as (cur, conn):
        cur.execute(
            "INSERT INTO public.session_cutoff (id, valid_after, set_by) VALUES (1, NOW(), %s) "
            "ON CONFLICT (id) DO UPDATE SET valid_after = EXCLUDED.valid_after, set_by = EXCLUDED.set_by "
            "RETURNING EXTRACT(EPOCH FROM valid_after)",
            ((actor or "-")[:150],))
        epoch = float(cur.fetchone()[0])
        conn.commit()
    _cache.update(at=time.monotonic(), epoch=epoch)
    return epoch


def clear_cache():
    _cache.update(at=float("-inf"), epoch=0.0)
