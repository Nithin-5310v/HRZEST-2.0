# -*- coding: utf-8 -*-
"""State-changing actions available from the SOC console.

Kept apart from soc_queries.py (read-only) so it is obvious, and testable,
which functions can change anything. Every function returns (ok, message[, data])
and records the outcome in the append-only soc_audit_log.
"""
from utils import soc_auth
from utils.soc_db import list_tenants, write_cursor
from utils.soc_util import clamp_int, looks_like_ip

CASE_STATUSES = ("open", "investigating", "contained", "resolved", "false_positive")
SEVERITIES = ("critical", "high", "medium", "low", "info")
MAX_BULK_BAN = 50
MAX_BAN_MINUTES = 60 * 24 * 365


def _protected(ip):
    from utils.client_ip import is_protected_ip
    return bool(is_protected_ip(ip))


# ── IP bans ──────────────────────────────────────────────────────────
def ban_ip(ip, reason, minutes, actor, actor_ip):
    ip = (ip or "").strip()
    reason = (reason or "").strip()
    if not looks_like_ip(ip):
        return False, "That is not a valid IP address."
    if not 3 <= len(reason) <= 240:
        return False, "Give a reason (3-240 characters)."
    if ip == actor_ip:
        return False, "You cannot ban the address you are connected from."
    if _protected(ip):
        return False, ("That address is private, shared or on the never-ban list; banning it would block every "
                       "visitor behind it.")
    if minutes in (None, "", 0, "0"):
        minutes = None
    else:
        minutes = clamp_int(minutes, 60, 1, MAX_BAN_MINUTES)
    from utils import waf
    written = waf.ban_ip(ip, f"SOC: {reason}", banned_by=f"soc:{actor}", minutes=minutes)
    soc_auth.audit(actor, "ban_ip" if written else "ban_ip_failed", target=ip,
                   detail=f"{reason} | {'permanent' if minutes is None else str(minutes) + ' min'}", ip=actor_ip)
    if not written:
        return False, "The ban could not be written."
    _invalidate()
    return True, "banned"


def unban_ip(ip, actor, actor_ip):
    if not looks_like_ip((ip or "").strip()):
        return False, "That is not a valid IP address."
    with write_cursor("public") as cur:
        cur.execute("DELETE FROM public.banned_ips WHERE ip=%s", (ip.strip(),))
        removed = cur.rowcount
    soc_auth.audit(actor, "unban_ip", target=ip, detail="removed" if removed else "no ban found", ip=actor_ip)
    if not removed:
        return False, "That address is not banned."
    _invalidate()
    return True, "unbanned"


def bulk_ban(ips, reason, minutes, actor, actor_ip):
    """Ban several addresses (e.g. the 'block' verdicts from a log analysis).
    -> list of {ip, ok, message}. Each address is validated independently."""
    seen, results = set(), []
    for ip in list(ips or [])[:MAX_BULK_BAN]:
        ip = str(ip).strip()
        if ip in seen:
            continue
        seen.add(ip)
        ok, msg = ban_ip(ip, reason, minutes, actor, actor_ip)
        results.append({"ip": ip, "ok": ok, "message": msg})
    return results


# ── sessions ─────────────────────────────────────────────────────────
def revoke_session(tenant_id, sid, actor, actor_ip):
    """Mark a session compromised; the app's session guard reads this on the
    session's very next request (utils/session_risk.is_session_compromised)."""
    sid = (sid or "").strip()
    if not sid or len(sid) > 64:
        return False, "Invalid session id."
    tenant = next((t for t in list_tenants() if t["id"] == tenant_id), None)
    if not tenant:
        return False, "Unknown tenant."
    with write_cursor(tenant["schema"]) as cur:
        cur.execute("UPDATE session_risk SET status='compromised', last_reason=%s, updated_at=NOW() WHERE sid=%s",
                    (f"revoked by SOC ({actor})"[:300], sid))
        done = cur.rowcount
    soc_auth.audit(actor, "revoke_session", target=f"{tenant['slug']}:{sid[:8]}…",
                   detail="revoked" if done else "no such session", ip=actor_ip)
    if not done:
        return False, "No such session."
    _invalidate()
    return True, "revoked"


# ── cases ────────────────────────────────────────────────────────────
def create_case(title, severity, source_type, source_event_id, summary, actor, actor_ip):
    title = (title or "").strip()
    if not 3 <= len(title) <= 200:
        return False, "Give the case a title (3-200 characters).", None
    severity = severity if severity in SEVERITIES else "medium"
    source_type = source_type if source_type in ("event", "detection", "log_analysis", "ip", "manual") else "manual"
    event_id = None
    if source_event_id not in (None, ""):
        event_id = clamp_int(source_event_id, 0, 0, 2 ** 31 - 1) or None
    with write_cursor("att_master") as cur:
        if event_id:
            cur.execute("SELECT id FROM soc_cases WHERE source_event_id=%s AND status IN ('open','investigating','contained') "
                        "ORDER BY id DESC LIMIT 1", (event_id,))
            existing = cur.fetchone()
            if existing:
                return True, "A case is already open for this event.", existing[0]
        cur.execute(
            "INSERT INTO soc_cases (title, severity, source_type, source_event_id, summary, created_by, assignee) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (title, severity, source_type, event_id, (summary or "")[:5000] or None, actor, actor))
        cid = cur.fetchone()[0]
    soc_auth.audit(actor, "case_created", target=f"case:{cid}", detail=title[:200], ip=actor_ip)
    return True, "created", cid


def update_case(case_id, actor, actor_ip, *, status=None, assignee=None, severity=None, resolution=None):
    sets, params = ["updated_at=NOW()"], []
    if status is not None:
        if status not in CASE_STATUSES:
            return False, "Unknown status."
        sets.append("status=%s")
        params.append(status)
        sets.append("closed_at = " + ("NOW()" if status in ("resolved", "false_positive") else "NULL"))
    if assignee is not None:
        sets.append("assignee=%s")
        params.append((assignee or None) and assignee[:60])
    if severity is not None:
        if severity not in SEVERITIES:
            return False, "Unknown severity."
        sets.append("severity=%s")
        params.append(severity)
    if resolution is not None:
        sets.append("resolution=%s")
        params.append(resolution[:5000] or None)
    params.append(case_id)
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_cases SET " + ", ".join(sets) + " WHERE id=%s", params)  # nosec B608 -- fixed fragments, bound values
        done = cur.rowcount
    if not done:
        return False, "No such case."
    detail = ", ".join(f"{k}={v}" for k, v in (("status", status), ("assignee", assignee), ("severity", severity)) if v is not None)
    soc_auth.audit(actor, "case_updated", target=f"case:{case_id}", detail=detail, ip=actor_ip)
    return True, "updated"


def add_note(case_id, body, actor, actor_ip):
    body = (body or "").strip()
    if not 1 <= len(body) <= 5000:
        return False, "Write a note (1-5000 characters)."
    with write_cursor("att_master") as cur:
        cur.execute("SELECT 1 FROM soc_cases WHERE id=%s", (case_id,))
        if not cur.fetchone():
            return False, "No such case."
        cur.execute("INSERT INTO soc_case_notes (case_id, author, body) VALUES (%s,%s,%s)", (case_id, actor, body))
        cur.execute("UPDATE soc_cases SET updated_at=NOW() WHERE id=%s", (case_id,))
    soc_auth.audit(actor, "case_note", target=f"case:{case_id}", ip=actor_ip)
    return True, "added"


def _invalidate():
    from utils import soc_queries
    soc_queries.clear_cache()
