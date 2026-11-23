# -*- coding: utf-8 -*-
"""Server-side list of signed-in sessions, so people can see where they are
signed in and end the sessions they do not recognise, and the SOC console can
list (and revoke) every signed-in session across companies.

Sessions are signed cookies with no server-side record of their own, so
app_setup/session_watch.py calls touch() on signed-in requests: the first one
of a session adds a row to the company's user_sessions table, and later ones
refresh it at most every REFRESH_SECONDS. It also gives the session a
session_risk row (score 0, 'active'), so the existing kill switch, and the SOC
console's existing revoke action, can end any signed-in session.

A session counts as signed in while all of these hold: it has not been ended
(here, by logout, a password change, the risk engine or the SOC: session_risk
status), it is younger than the absolute lifetime (PERMANENT_SESSION_LIFETIME,
8 h), and it was used within the company's idle timeout
(company_settings.session_timeout, 30 min by default) plus the refresh
interval. Ending one goes through utils.session_risk.revoke_session_id, the
same path logout uses: the app refuses that cookie on its very next request.
"""
import time

from utils.session_context import session_ref
from utils.session_cutoff import issued_before_cutoff_sql

REFRESH_SECONDS = 300
COUNT_TTL_SECONDS = 60
_count_cache = {"at": 0.0, "value": None}


def lifetime_seconds():
    from extensions import app
    value = app.config.get("PERMANENT_SESSION_LIFETIME", 28800)
    return int(value.total_seconds()) if hasattr(value, "total_seconds") else int(value)


# Signed in: not ended anywhere, within the absolute lifetime and the idle timeout,
# and started after the last "sign out everyone" (utils/session_cutoff.py).
# Parameters: lifetime seconds, refresh seconds.
ALIVE_SQL = (
    "s.ended_at IS NULL AND s.started_at > NOW() - (%s * INTERVAL '1 second') "
    "AND s.last_seen_at > NOW() - (COALESCE((SELECT session_timeout FROM company_settings ORDER BY id LIMIT 1), 30) "
    "  * INTERVAL '1 minute') - (%s * INTERVAL '1 second') "
    "AND NOT EXISTS (SELECT 1 FROM session_risk r WHERE r.sid = s.sid AND r.status IN ('compromised', 'revoked')) "
    f"AND NOT {issued_before_cutoff_sql('s.started_at')}"  # nosec B608 -- fixed column name
)


def touch(session, identifier, kind, ip, network, browser):
    """Record or refresh this request's session. Never raises."""
    from extensions import app_log
    from utils.helpers.db import _db
    sid = session.get("_sid")
    if not sid or not identifier:
        return
    ref, now = session_ref(sid), time.time()
    first = session.get("_reg") != ref
    if not first and now - float(session.get("_reg_at") or 0) < REFRESH_SECONDS:
        return
    try:
        with _db() as (cur, conn):
            cur.execute(
                "INSERT INTO user_sessions (sid, identifier, kind, ip, network, browser) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (sid) DO UPDATE SET last_seen_at = NOW(), ip = EXCLUDED.ip, "
                "network = EXCLUDED.network, browser = EXCLUDED.browser",
                (sid[:64], identifier[:150], kind, (ip or "")[:64], (network or "")[:64], (browser or "")[:80]))
            if first:
                cur.execute("INSERT INTO session_risk (sid, identifier, attempt_type, score, status, last_reason) "
                            "VALUES (%s, %s, %s, 0, 'active', 'signed in') ON CONFLICT (sid) DO NOTHING",
                            (sid[:64], identifier[:150], kind))
            conn.commit()
        session["_reg"], session["_reg_at"] = ref, now
    except Exception as exc:
        app_log.warning("session registry update failed: %s", type(exc).__name__)


def _rows(cur, identifier, kind):
    cur.execute(
        "SELECT s.sid, s.browser, s.network, EXTRACT(EPOCH FROM NOW() - s.started_at), "
        "EXTRACT(EPOCH FROM NOW() - s.last_seen_at) FROM user_sessions s "
        "WHERE s.identifier = %s AND s.kind = %s AND " + ALIVE_SQL + " ORDER BY s.last_seen_at DESC LIMIT 50",
        (identifier, kind, lifetime_seconds(), REFRESH_SECONDS))
    return cur.fetchall()


def list_for(identifier, kind, current_sid):
    """The caller's own signed-in sessions, newest use first. Session ids never
    leave the server: each is named by its 12-character reference."""
    from utils.helpers.db import _db
    with _db() as (cur, _conn):
        rows = _rows(cur, identifier, kind)
    return [{"ref": session_ref(sid), "current": sid == current_sid, "browser": browser or "Unknown browser",
             "network": network or "unknown network", "started_seconds_ago": int(started),
             "active_seconds_ago": int(active)} for sid, browser, network, started, active in rows]


def end_for(identifier, kind, current_sid, ref=None, all_others=False):
    """End one of the caller's other sessions (by reference), or all of them.
    -> (ended count, "current" if `ref` names the caller's own session)."""
    from utils.helpers.db import _db
    from utils.session_risk import revoke_session_id
    with _db() as (cur, _conn):
        rows = _rows(cur, identifier, kind)
    targets = []
    for sid, *_ in rows:
        if all_others and sid != current_sid:
            targets.append(sid)
        elif ref and session_ref(sid) == ref:
            if sid == current_sid:
                return 0, "current"
            targets.append(sid)
    ended = 0
    for sid in targets:
        if revoke_session_id(sid, identifier, "ended by the account holder from another session"):
            ended += 1
    if targets:
        with _db() as (cur, conn):
            cur.execute("UPDATE user_sessions SET ended_at = NOW(), ended_reason = 'ended by the account holder' "
                        "WHERE sid = ANY(%s)", (targets,))
            conn.commit()
    return ended, None


def count_signed_in():
    """How many company sessions are signed in right now, across companies (the
    SOC console's live strip). One short query per company, cached for
    COUNT_TTL_SECONDS. -> {"count", "errors"} (companies that could not be read)."""
    now = time.monotonic()
    if _count_cache["value"] is not None and now - _count_cache["at"] < COUNT_TTL_SECONDS:
        return _count_cache["value"]
    from utils import soc_scope
    from utils.soc_db import each_tenant
    accounts = soc_scope.test_accounts()

    def per_tenant(t, cur):
        if soc_scope.is_test_company(t["slug"]):
            return None
        cur.execute("SELECT COUNT(*) FROM user_sessions s WHERE lower(s.identifier) <> ALL(%s) AND " + ALIVE_SQL,
                    (accounts, lifetime_seconds(), REFRESH_SECONDS))
        return cur.fetchone()[0]
    counts, errors = each_tenant(per_tenant)
    value = {"count": sum(counts), "errors": len(errors)}
    _count_cache.update(at=now, value=value)
    return value


def signed_in_everywhere(include_sid=False, limit=300):
    """Every signed-in session across companies, for the SOC console (your
    SOC_TEST_ACCOUNTS / SOC_TEST_COMPANIES left out, as everywhere there)."""
    from utils import soc_scope
    from utils.soc_db import each_tenant
    accounts = soc_scope.test_accounts()

    def per_tenant(t, cur):
        if soc_scope.is_test_company(t["slug"]):
            return None
        cur.execute(
            "SELECT s.sid, s.identifier, s.kind, s.browser, s.network, s.ip, "
            "EXTRACT(EPOCH FROM NOW() - s.started_at), EXTRACT(EPOCH FROM NOW() - s.last_seen_at) "
            "FROM user_sessions s WHERE lower(s.identifier) <> ALL(%s) AND " + ALIVE_SQL +
            " ORDER BY s.last_seen_at DESC LIMIT %s",
            (accounts, lifetime_seconds(), REFRESH_SECONDS, limit))
        return [{"tenant_id": t["id"], "company": t["company"], "slug": t["slug"], "sid": sid, "ref": session_ref(sid),
                 "identifier": ident, "type": kind, "browser": browser, "network": network, "ip": ip,
                 "started_seconds_ago": int(started), "active_seconds_ago": int(active)}
                for sid, ident, kind, browser, network, ip, started, active in cur.fetchall()]
    groups, errors = each_tenant(per_tenant)
    flat = [s for g in groups for s in g]
    flat.sort(key=lambda s: s["active_seconds_ago"])
    if not include_sid:
        for s in flat:
            s.pop("sid")
    return {"sessions": flat[:limit], "total": len(flat), "errors": errors}
