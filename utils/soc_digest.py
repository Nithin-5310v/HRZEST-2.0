# -*- coding: utf-8 -*-
"""Daily SOC summary email: the last 24 hours for everyone in SOC_LOGIN_EMAILS,
so a small team learns what happened without watching the console.

Scheduled by wsgi.py (job "soc_daily_digest", at SOC_DIGEST_HOUR, default
08:00 Asia/Kolkata) and sent once a day however many workers fire it
(utils/job_runs.py). It is sent on quiet days too: a summary that stops
arriving is itself a sign that something is wrong.

Content is the console's own genuine-activity data (utils/soc_scope.py): the
threat level, the Overview numbers against the previous day, detections by
rule, the busiest sources, what is waiting in Ban review and for how long,
open cases, and what was left out. Deliberately NOT in the email: account
identifiers and event messages. Those are personal data, and email sits outside
the console's network allow-list and sign-in. Source IP addresses are included:
they are what an analyst acts on.

SOC_DIGEST=off turns it off. Without email configured nothing is sent (logged).
"""
import datetime
import html
import os

from extensions import app_log
from utils import soc_auth, soc_ban_review, soc_queries
from utils import soc_taxonomy as tax

JOB_ID = "soc_daily_digest"
KPIS = (("events", "Events"), ("critical", "Critical"), ("error", "Errors"), ("detections", "Detections"),
        ("failed_logins", "Failed logins"), ("waf_blocks", "WAF blocks"), ("honeypot_hits", "Honeypot hits"),
        ("unique_ips", "Internet sources"))
TOP_SOURCES = 5
TOP_REVIEWS = 5


def enabled():
    return os.environ.get("SOC_DIGEST", "on").strip().lower() not in ("0", "off", "false", "no")


def digest_hour():
    try:
        return max(0, min(23, int(os.environ.get("SOC_DIGEST_HOUR", "8"))))
    except ValueError:
        return 8


def recipients():
    """Every address in SOC_LOGIN_EMAILS, except accounts disabled in the console."""
    out = []
    for email in sorted(soc_auth.login_emails()):
        user = soc_auth.get_user_by_email(email)
        if user is None or user["active"]:
            out.append(email)
    return out


def console_url():
    base = os.environ.get("APP_URL", "").strip().rstrip("/")
    return base + "/socperson" if base.startswith(("https://", "http://")) else None


def _db_local_now():
    """The database's LOCALTIMESTAMP: the same zone-less wall clock that
    soc_ban_reviews' TIMESTAMP columns are written in (NOW() carries an offset)."""
    from utils.soc_db import read_cursor
    with read_cursor("att_master") as cur:
        cur.execute("SELECT LOCALTIMESTAMP")
        return cur.fetchone()[0]


def _age(now, then):
    """'3 hours' / '2 days' from `then` (an ISO timestamp from the database) to `now`."""
    try:
        secs = (now - datetime.datetime.fromisoformat(then)).total_seconds()
    except (TypeError, ValueError):
        return None
    if secs < 3600:
        return f"{max(1, int(secs // 60))} minute(s)"
    if secs < 2 * 86400:
        return f"{int(secs // 3600)} hour(s)"
    return f"{int(secs // 86400)} days"


def build(hours=24):
    """The summary's data. Every number comes from the console's queries."""
    ov = soc_queries.overview(hours)
    totals = soc_queries.event_totals(hours)
    rules = {}
    for e in soc_queries.detections(hours)["events"]:
        rule = e["event_type"].split(".", 1)[-1]
        label = tax.DETECTIONS.get(rule, (rule,))[0]
        rules[label] = rules.get(label, 0) + 1
    pending = soc_ban_review.list_reviews("pending")
    now = soc_queries.pulse()["now"]
    oldest = min((r["first_seen"] for r in pending["reviews"] if r.get("first_seen")), default=None)
    return {
        "hours": hours, "now": now, "threat": ov["threat"], "kpi": ov["kpi"],
        "previous_events": totals["previous"],
        "detections": sorted(rules.items(), key=lambda x: (-x[1], x[0])),
        "sources": ov["top_ips"][:TOP_SOURCES],
        "pending": pending["counts"].get("pending", 0),
        "pending_oldest": _age(_db_local_now(), oldest) if oldest else None,
        "reviews": pending["reviews"][:TOP_REVIEWS],
        "auto_ban": pending.get("auto_ban", False),
        "open_cases": ov["cases"]["open"],
        "excluded": ov.get("excluded") or {},
        "proxy_hint": ov.get("proxy_hint", False),
    }


# ── rendering ────────────────────────────────────────────────────────
_CELL = "padding:6px 10px;border-bottom:1px solid #1e293b;font-size:13px;color:#cbd5e1;"
_HEAD = "padding:6px 10px;border-bottom:1px solid #334155;font-size:12px;color:#94a3b8;text-align:left;"
_TONE = {"normal": "#22c55e", "elevated": "#f59e0b", "high": "#f97316", "critical": "#ef4444"}


def _e(value):
    return html.escape(str(value), quote=True)


def _n(value):
    return f"{int(value or 0):,}"


def _table(headers, rows):
    head = "".join(f'<th style="{_HEAD}">{_e(h)}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f'<td style="{_CELL}">{c}</td>' for c in row) + "</tr>" for row in rows)
    return f'<table style="width:100%;border-collapse:collapse;margin:6px 0 18px;"><tr>{head}</tr>{body}</table>'


def _section(title, inner):
    return f'<h3 style="font-size:14px;color:#e2e8f0;margin:18px 0 6px;">{_e(title)}</h3>{inner}'


def _para(text, color="#94a3b8"):
    return f'<p style="font-size:13px;color:{color};margin:4px 0 12px;">{text}</p>'


def render(d, url=None):
    """-> (subject, html). Every value is escaped."""
    k, level = d["kpi"], d["threat"]["level"]
    alerts = (k.get("critical") or 0) + (k.get("error") or 0)
    subject = (f"SOC daily summary: threat {level}, {alerts} alert(s), "
               f"{d['pending']} awaiting ban review")

    prev = d["previous_events"]
    change = (f"{'+' if k['events'] >= prev else '-'}{_n(abs(k['events'] - prev))} vs the previous day"
              if prev or k["events"] else "none the previous day either")
    kpi_rows = [[_e(label), _n(k.get(key)) + (f' <span style="color:#64748b;">({_e(change)})</span>' if key == "events" else "")]
                for key, label in KPIS]
    if k.get("lockouts"):
        kpi_rows[4][1] += f' <span style="color:#64748b;">({_n(k["lockouts"])} lockouts)</span>'

    parts = [
        f'<div style="padding:12px 14px;border-radius:10px;background:#090d16;border-left:4px solid {_TONE.get(level, "#f59e0b")};">'
        f'<div style="font-size:15px;font-weight:700;color:#f1f5f9;">Threat level: {_e(level)}</div>'
        f'<div style="font-size:13px;color:#94a3b8;margin-top:4px;">{_e(". ".join(d["threat"]["reasons"]))}.</div></div>',
    ]
    if d.get("proxy_hint"):
        parts.append(_para("Warning: most recorded events come from the server's own proxy address, so real visitors "
                           "are not visible. Set TRUSTED_PROXY_HOPS (see Posture in the console).", "#f59e0b"))
    parts.append(_section(f"Last {d['hours']} hours", _table(["", "Count"], kpi_rows)))

    if d["detections"]:
        parts.append(_section("Detections", _table(["Rule", "Times"], [[_e(r), _n(c)] for r, c in d["detections"]])))
    else:
        parts.append(_section("Detections", _para("No correlation rule fired.")))

    if d["sources"]:
        rows = [[f'<span style="font-family:monospace;">{_e(s["ip"])}</span>', _n(s["events"]), _n(s["high"]),
                 "banned" if s.get("banned") else ""] for s in d["sources"]]
        parts.append(_section("Most active sources", _table(["Address", "Events", "High", ""], rows)))

    if d["pending"]:
        waited = f" The oldest has waited {_e(d['pending_oldest'])}." if d["pending_oldest"] else ""
        note = _para(f"{_n(d['pending'])} address(es) the WAF, rate limiter or honeypots want banned are waiting for "
                     f"a decision. Nothing is banned until someone decides.{waited}", "#fbbf24")
        rows = [[f'<span style="font-family:monospace;">{_e(r["ip"])}</span>', _e(r.get("source") or ""),
                 _n(r.get("hits"))] for r in d["reviews"]]
        parts.append(_section("Waiting in Ban review", note + _table(["Address", "Detected by", "Hits"], rows)))
    else:
        parts.append(_section("Ban review", _para("Nothing is waiting for a decision.")))

    parts.append(_para(f"Open cases: {_n(d['open_cases'])}.", "#cbd5e1"))
    x = d.get("excluded") or {}
    reasons = (("server", "from the server's own addresses"), ("console", "from the console's own use"),
               ("testing", "from your listed testing"))
    left = [f"{_n(x[key])} {what}" for key, what in reasons if x.get(key)]
    parts.append(_para("Genuine activity only" + (": left out " + ", ".join(left) if left else "") + ". "
                       "Account names and event details are not included in email; open the console for them."))
    if url:
        parts.append(f'<p style="margin:14px 0;"><a href="{_e(url)}" style="color:#60a5fa;">Open the SOC console</a></p>')
    parts.append(_para("Sent to the addresses in SOC_LOGIN_EMAILS. Set SOC_DIGEST=off on the server to stop it.",
                       "#64748b"))

    body = (
        '<div style="font-family:Segoe UI,sans-serif;max-width:620px;margin:auto;background:#0f172a;'
        'border-radius:16px;overflow:hidden;border:1px solid #1e293b;">'
        '<div style="background:#1d4ed8;padding:20px 24px;color:#fff;">'
        '<div style="font-size:18px;font-weight:700;">Security Operations: daily summary</div>'
        f'<div style="font-size:12px;opacity:0.85;margin-top:4px;">Last {d["hours"]} hours, to {_e(d["now"])} (server time)</div>'
        '</div><div style="padding:20px 24px;">' + "".join(parts) + "</div></div>"
    )
    return subject, body


def send_daily_digest():
    """The scheduled job. -> number of emails queued."""
    if not enabled():
        return 0
    to = recipients()
    if not to:
        app_log.info("SOC daily summary: SOC_LOGIN_EMAILS lists no active address, nothing to send.")
        return 0
    from utils.email_utils import get_email_config, send_email_async
    cfg = get_email_config()
    if not cfg:
        app_log.error("SOC daily summary not sent: email (SMTP) is not configured.")
        return 0
    subject, body = render(build(), console_url())
    for address in to:                      # one email each: recipients do not see each other
        send_email_async(address, subject, body, cfg)
    soc_auth.audit("system", "daily_digest_sent", detail=f"{len(to)} recipient(s)")
    return len(to)
