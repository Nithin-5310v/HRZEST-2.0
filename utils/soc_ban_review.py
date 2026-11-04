# -*- coding: utf-8 -*-
"""Ban review queue: automated detections propose, a SOC analyst decides.

The WAF breach counter (repeated attack payloads or rate-limit breaches) and
the HTTP/TCP honeypots used to write straight into banned_ips. That locked out
whole customer offices behind one shared address -- a 9 a.m. login rush trips
the per-IP rate limits, one infected office PC (or the customer's own security
scan) touches a honeypot path -- for up to 7 days, with nobody deciding.

Now they call raise_review() instead, which records the address in
att_master.soc_ban_reviews. The SOC console's "Ban review" view lists them and
an analyst chooses:

  ban      ban for a chosen duration (through soc_actions.ban_ip, so the
           shared-address guard and the SOC audit trail apply as for any ban)
  dismiss  false positive; a later detection re-opens the item
  trust    known-good address (a customer office, a partner's scanner):
           later detections only add to its hit count until the trust expires

The request that triggered a detection is still rejected as before (WAF 403,
rate-limit 429, honeypot 404); only the follow-on ban waits for a person.

SOC_AUTO_BAN=true restores the old behaviour for deployments nobody watches
around the clock: the detection is banned at once with the suggested duration
and recorded here as already decided by system:auto, so the console still
shows what happened and an analyst can lift it.
"""
import datetime
import os

from extensions import log_security_event
from utils import soc_auth
from utils.soc_db import read_cursor, write_cursor
from utils.soc_util import clamp_int, iso, looks_like_ip

STATUSES = ("pending", "banned", "dismissed", "trusted")
ACTIONS = ("ban", "dismiss", "trust")
SOURCES = ("waf", "ratelimit", "honeypot", "honeypot-tcp")
MAX_BAN_MINUTES = 60 * 24 * 365
MAX_TRUST_DAYS = 365
LIST_LIMIT = 200

_COLUMNS = ("ip", "status", "source", "reason", "suggested_minutes", "hits", "first_seen", "last_seen",
            "decided_by", "decided_at", "decision_note", "trusted_until")


def auto_ban_enabled():
    return os.environ.get("SOC_AUTO_BAN", "false").strip().lower() in ("1", "true", "yes", "on")


def raise_review(ip, source, reason, suggested_minutes=None):
    """Record a detection for analyst review (see module docstring).

    suggested_minutes is what the detector would have banned for (None means
    "until lifted"). Never raises: a failure here must not turn the request
    that triggered it into a 500. Returns the item's status after the call
    ("pending", "trusted", "banned"), or None when nothing was recorded."""
    from utils.client_ip import is_protected_ip
    ip = (ip or "").strip()
    if not looks_like_ip(ip) or is_protected_ip(ip):
        # Shared/private/never-ban addresses: banning them would block every
        # visitor behind them, so they are never proposed either.
        return None
    source = source if source in SOURCES else "waf"
    reason = (reason or "detection")[:500]
    if suggested_minutes is not None:
        suggested_minutes = clamp_int(suggested_minutes, 15, 1, MAX_BAN_MINUTES)
    try:
        with write_cursor("att_master") as cur:
            cur.execute("SELECT status, trusted_until FROM soc_ban_reviews WHERE ip=%s FOR UPDATE", (ip,))
            row = cur.fetchone()
            was = row[0] if row else None
            trust_live = was == "trusted" and (row[1] is None or row[1] > datetime.datetime.now())
            status = "trusted" if trust_live else "pending"
            cur.execute(
                "INSERT INTO soc_ban_reviews (ip, status, source, reason, suggested_minutes) "
                "VALUES (%s, 'pending', %s, %s, %s) "
                "ON CONFLICT (ip) DO UPDATE SET "
                "  hits = soc_ban_reviews.hits + 1, last_seen = CURRENT_TIMESTAMP, "
                "  source = EXCLUDED.source, reason = EXCLUDED.reason, "
                # Keep the strongest suggestion seen: NULL (until lifted) wins.
                "  suggested_minutes = CASE WHEN soc_ban_reviews.suggested_minutes IS NULL "
                "    OR EXCLUDED.suggested_minutes IS NULL THEN NULL "
                "    ELSE GREATEST(soc_ban_reviews.suggested_minutes, EXCLUDED.suggested_minutes) END, "
                "  status = %s, "
                # Re-opening an item clears the previous decision.
                "  decided_by = CASE WHEN %s = 'pending' AND soc_ban_reviews.status <> 'pending' "
                "    THEN NULL ELSE soc_ban_reviews.decided_by END, "
                "  decided_at = CASE WHEN %s = 'pending' AND soc_ban_reviews.status <> 'pending' "
                "    THEN NULL ELSE soc_ban_reviews.decided_at END",
                (ip, source, reason, suggested_minutes, status, status, status),
            )
    except Exception as exc:
        log_security_event("secops.ban_review_failed", f"Could not record ban review: {exc}",
                           level="ERROR", identifier=ip, reason=reason)
        return None

    if was != status and status == "pending":
        # Logged once per opening, not per hit, so a noisy address doesn't
        # flood the event stream; the hit count lives on the review row.
        log_security_event("secops.ban_review_raised", "Address awaiting a SOC ban decision",
                           level="WARNING", identifier=ip, source=source, reason=reason)
    if status == "pending" and auto_ban_enabled():
        return _auto_ban(ip, reason, suggested_minutes)
    return status


def _auto_ban(ip, reason, minutes):
    from utils import waf
    if not waf.ban_ip(ip, reason, banned_by="system:auto", minutes=minutes):
        return "pending"
    log_security_event("waf.auto_ban", "IP auto-banned (SOC_AUTO_BAN)", level="ERROR",
                       identifier=ip, reason=reason, minutes=minutes)
    _mark(ip, "banned", "system:auto", "SOC_AUTO_BAN", None)
    return "banned"


def _mark(ip, status, actor, note, trusted_until):
    with write_cursor("att_master") as cur:
        cur.execute(
            "UPDATE soc_ban_reviews SET status=%s, decided_by=%s, decided_at=CURRENT_TIMESTAMP, "
            "decision_note=%s, trusted_until=%s WHERE ip=%s",
            (status, actor, note or None, trusted_until, ip),
        )
        return cur.rowcount


def list_reviews(status="pending", limit=LIST_LIMIT):
    """-> {reviews: [...], counts: {status: n}} for the console."""
    status = status if status in STATUSES else "pending"
    limit = clamp_int(limit, LIST_LIMIT, 1, LIST_LIMIT)
    with read_cursor("att_master") as cur:
        cur.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM soc_ban_reviews WHERE status=%s "
            "ORDER BY last_seen DESC LIMIT %s",
            (status, limit),
        )
        rows = [{c: iso(v) for c, v in zip(_COLUMNS, r)} for r in cur.fetchall()]
        cur.execute("SELECT status, COUNT(*) FROM soc_ban_reviews GROUP BY status")
        counts = {s: 0 for s in STATUSES}
        counts.update({s: n for s, n in cur.fetchall()})
    return {"reviews": rows, "counts": counts, "status": status, "auto_ban": auto_ban_enabled()}


def decide(ip, action, actor, actor_ip, minutes=None, days=None, note=None):
    """Apply an analyst decision. -> (ok, message)."""
    ip = ip.strip() if isinstance(ip, str) else ""
    note = note.strip()[:240] if isinstance(note, str) else ""
    if not looks_like_ip(ip):
        return False, "That is not a valid IP address."
    if action not in ACTIONS:
        return False, "Choose ban, dismiss or trust."
    with read_cursor("att_master") as cur:
        cur.execute("SELECT reason, source FROM soc_ban_reviews WHERE ip=%s", (ip,))
        row = cur.fetchone()
    if not row:
        return False, "No review is recorded for that address."
    detected, source = row

    if action == "ban":
        from utils import soc_actions
        reason = f"Review ({source}): {detected}"
        if note:
            reason = f"{reason} | {note}"
        ok, msg = soc_actions.ban_ip(ip, reason[:240], minutes, actor, actor_ip)
        if not ok:
            return False, msg
        _mark(ip, "banned", actor, note, None)
        return True, "banned"

    if action == "dismiss":
        _mark(ip, "dismissed", actor, note, None)
        soc_auth.audit(actor, "ban_review_dismiss", target=ip, detail=note or None, ip=actor_ip)
        return True, "dismissed"

    # trust: days=0 / empty means until revoked
    if days in (None, "", 0, "0"):
        until = None
    else:
        until = datetime.datetime.now() + datetime.timedelta(days=clamp_int(days, 30, 1, MAX_TRUST_DAYS))
    _mark(ip, "trusted", actor, note, until)
    soc_auth.audit(actor, "ban_review_trust", target=ip,
                   detail=f"{note + ' | ' if note else ''}{'until revoked' if until is None else 'until ' + iso(until)}",
                   ip=actor_ip)
    return True, "trusted"
