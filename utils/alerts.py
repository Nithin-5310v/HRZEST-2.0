# -*- coding: utf-8 -*-
"""Real-time security alerting -- Slack/Discord webhook and email delivery.

Wired into extensions.log_security_event(): any event logged at ERROR or
CRITICAL severity is also pushed here, so this never runs on the hot path
for routine INFO/WARNING logging -- only for events someone decided are
worth paging a human for (account lockouts, malware detections,
injection-shaped input, privilege-escalation attempts, honeypot trap
hits).

Delivery is fire-and-forget on a background thread (same pattern as
send_email_async in utils/email_utils.py) -- a webhook outage or a slow
Slack/Discord response must never add latency to the request that
triggered the alert, and must never turn a security event into a second,
unrelated 500 error.
"""
import os
import re
import html
import json
import time
import hmac
import hashlib
import threading
import urllib.request
import urllib.error

from extensions import app_log

_WEBHOOK_URL = os.environ.get("SECURITY_ALERT_WEBHOOK_URL", "").strip()
_PLATFORM = os.environ.get("SECURITY_ALERT_PLATFORM", "discord").strip().lower()  # "discord" | "slack"
_SIGNING_SECRET = os.environ.get("SECURITY_ALERT_SIGNING_SECRET", "").strip()
_TIMEOUT_SECONDS = 5

# ── Payload sanitization ───────────────────────────────────────────────────
# Allowlist, not a blocklist: only these field names are ever forwarded into
# an alert. A future call site that passes something unexpected under a new
# key gets silently dropped rather than leaked -- failing closed by default,
# since this module has no way to know every field a caller might someday
# pass.
_ALLOWED_FIELDS = {
    "identifier", "ip", "path", "method", "reason", "filename", "ext",
    "content_type", "actual_role", "attempt_type", "failed_count",
    "locked_until", "signature", "size_mb", "required", "pattern",
}

# Redact by VALUE too, regardless of field name -- defense in depth against
# a field that's allowlisted for a legitimate reason (e.g. "reason") but
# happens to contain something that looks like a credential in a specific
# call.
_SECRET_LOOKING_RE = re.compile(
    r"(password|passwd|pwd|secret|token|bearer\s|api[_-]?key|authorization\s*:)",
    re.IGNORECASE,
)
_MAX_FIELD_LEN = 200
_MAX_DESC_LEN = 1000


def _sanitize_fields(fields: dict) -> dict:
    clean = {}
    for k, v in (fields or {}).items():
        if k not in _ALLOWED_FIELDS or v is None:
            continue
        s = str(v)
        if _SECRET_LOOKING_RE.search(k) or _SECRET_LOOKING_RE.search(s):
            clean[k] = "[redacted]"
            continue
        clean[k] = s[:_MAX_FIELD_LEN] + ("…" if len(s) > _MAX_FIELD_LEN else "")
    return clean


def _sanitize_description(description: str) -> str:
    s = str(description or "")
    if _SECRET_LOOKING_RE.search(s):
        return "[description redacted -- contained a credential-shaped value]"
    return s[:_MAX_DESC_LEN] + ("…" if len(s) > _MAX_DESC_LEN else "")


# ── Raw-object masking ──────────────────────────────────────────────────────
# Different problem from _sanitize_fields above, and deliberately not
# merged with it: _sanitize_fields is an ALLOWLIST over a flat **fields
# dict of keys THIS app's own call sites already chose and control -- right
# default for that case (unknown key => drop, fail closed).
#
# mask_raw_payload is for the opposite case: an object of genuinely
# unknown shape (a caught exception's context, a raw upstream error
# payload, a dumped request object) that you still want mostly visible for
# debugging, with only the sensitive parts redacted. An allowlist can't do
# that -- it would drop everything not pre-approved. So this walks the
# object recursively and masks by KEY NAME (covers PII fields this app
# already treats as sensitive elsewhere -- pan_number/uan_number/
# bank_account in utils/helpers.py's encrypt_pii, plus the generic
# password/token/secret family) and by VALUE SHAPE (catches a credential
# or PII value sitting under an innocuous key name, e.g. a stack trace
# that happens to include one).
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|bearer|api[_-]?key|authorization|"
    r"pan_?number|uan_?number|bank_?account|aadhar|ssn|credit_?card|cvv|"
    r"^email$|^phone$|^mobile$)",
    re.IGNORECASE,
)
_EMAIL_VALUE_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Long unbroken alnum/base64-ish runs -- the shape of an API key, JWT
# segment, or password hash, regardless of what key it's stored under.
_TOKEN_VALUE_RE = re.compile(r"\b[A-Za-z0-9+/_=-]{24,}\b")
_MAX_MASK_DEPTH = 4
_MASK_LIST_LIMIT = 20


def _mask_value(v):
    s = str(v)
    if _EMAIL_VALUE_RE.search(s):
        return "[redacted-email]"
    if _TOKEN_VALUE_RE.search(s):
        return "[redacted-token]"
    return s[:_MAX_FIELD_LEN] + ("…" if len(s) > _MAX_FIELD_LEN else "")


def mask_raw_payload(raw, _depth=0):
    """Recursively mask an object of unknown shape before it's ever
    serialized toward an external webhook. Safe to call on anything
    JSON-shaped: dict, list, or scalar.

    Depth-bounded (default 4) rather than unbounded recursion -- a
    maliciously or accidentally deep/cyclic-looking structure (some
    exception objects have circular __context__/__cause__ chains) must
    not be able to hang or crash the alerting path itself; anything past
    the depth limit collapses to a marker string instead of recursing
    further.
    """
    if _depth >= _MAX_MASK_DEPTH:
        return "[max depth reached]"
    if isinstance(raw, dict):
        out = {}
        for k, v in raw.items():
            key_str = str(k)
            if _SENSITIVE_KEY_RE.search(key_str):
                out[key_str] = "[redacted]"
            else:
                out[key_str] = mask_raw_payload(v, _depth + 1)
        return out
    if isinstance(raw, (list, tuple)):
        items = [mask_raw_payload(v, _depth + 1) for v in list(raw)[:_MASK_LIST_LIMIT]]
        if len(raw) > _MASK_LIST_LIMIT:
            items.append(f"...[{len(raw) - _MASK_LIST_LIMIT} more items truncated]")
        return items
    if raw is None or isinstance(raw, (int, float, bool)):
        return raw
    return _mask_value(raw)


# ── Payload construction ────────────────────────────────────────────────────
_SEVERITY_COLOR = {
    "INFO": 0x3B82F6, "WARNING": 0xF59E0B, "ERROR": 0xDC2626, "CRITICAL": 0x7F1D1D,
}


def _build_payload(event_type, description, severity, timestamp, fields):
    if _PLATFORM == "slack":
        field_text = "\n".join(f"*{k}:* {v}" for k, v in fields.items()) or "--"
        return {
            "text": f":rotating_light: *{severity} -- {event_type}*",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text",
                 "text": f"🚨 {severity} -- {event_type}"[:150]}},
                {"type": "section", "text": {"type": "mrkdwn", "text": description}},
                {"type": "section", "text": {"type": "mrkdwn", "text": field_text}},
                {"type": "context", "elements": [
                    {"type": "mrkdwn", "text": f"HRzest.com · {timestamp}"}
                ]},
            ],
        }
    # Discord (default)
    return {
        "embeds": [{
            "title": f"🚨 {severity} -- {event_type}"[:256],
            "description": description,
            "color": _SEVERITY_COLOR.get(severity, 0x64748B),
            "timestamp": timestamp,
            "fields": [
                {"name": k, "value": v or "--", "inline": True}
                for k, v in list(fields.items())[:24]  # Discord caps embeds at 25 fields
            ],
            "footer": {"text": "HRzest.com -- security alert"},
        }]
    }


def _sign(body: bytes) -> str:
    """HMAC-SHA256 over the raw request body. Discord/Slack's own webhook
    endpoints don't check this -- the webhook URL itself is the auth
    mechanism on that side. This header exists for anything YOU put in
    front of delivery later (an internal relay/ingestion endpoint that
    forwards to chat + a SIEM), so a spoofed or tampered alert is
    detectable rather than trusted on the strength of "it arrived"."""
    return hmac.new(_SIGNING_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _deliver(payload: dict):
    if not _WEBHOOK_URL.startswith(("https://", "http://")):
        app_log.warning("Security alert webhook URL has an unexpected scheme; refusing to send")
        return
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if _SIGNING_SECRET:
        headers["X-Signature-256"] = "sha256=" + _sign(body)
    req = urllib.request.Request(_WEBHOOK_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # nosec B310
            if resp.status >= 300:
                app_log.warning("Security alert webhook returned HTTP %s", resp.status)
    except urllib.error.URLError as e:
        # Never let a webhook outage become a second failure on top of the
        # security event that triggered it -- log locally and move on.
        app_log.warning("Security alert webhook delivery failed: %s", e)


# ── Email channel ────────────────────────────────────────────────────────────
# SECURITY_ALERT_EMAIL = comma-separated recipients. Sent through the app's
# normal SMTP config (Email Settings page / SMTP_* env vars -- see
# utils.email_utils.get_email_config) and its DB-backed email queue, so
# delivery is retried like every other system email. Throttled per event type:
# a brute-force flood must not become an inbox flood.
_EMAIL_THROTTLE_SECONDS = 300
_email_last_sent = {}
_email_lock = threading.Lock()


def _alert_recipients():
    raw = os.environ.get("SECURITY_ALERT_EMAIL", "")
    return [a.strip() for a in raw.split(",") if "@" in a]


def _email_throttled(event_type: str) -> bool:
    now = time.time()
    with _email_lock:
        if now - _email_last_sent.get(event_type, 0) < _EMAIL_THROTTLE_SECONDS:
            return True
        _email_last_sent[event_type] = now
        return False


def _build_email(event_type, description, severity, timestamp, fields):
    """(subject, html) -- every interpolated value is escaped: descriptions and
    fields can carry attacker-controlled text (login names, paths)."""
    esc = html.escape
    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#64748b">{esc(k)}</td><td>{esc(v)}</td></tr>'
        for k, v in fields.items()
    )
    body = (
        f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#0f172a">'
        f'<h2 style="margin:0 0 8px;color:#b91c1c">{esc(severity)} &mdash; {esc(event_type)}</h2>'
        f'<p style="margin:0 0 12px">{esc(description)}</p>'
        f'<table style="border-collapse:collapse">{rows}</table>'
        f'<p style="margin-top:16px;color:#64748b;font-size:12px">HRzest.com security alert &middot; {esc(timestamp)}</p>'
        f'</div>'
    )
    return f"[HRzest security {severity}] {event_type}"[:200], body


def _send_email_alert(recipients, event_type, description, severity, timestamp, fields):
    """Background-thread body. Any failure is logged and swallowed -- an
    alerting problem must never surface as a second error."""
    try:
        from utils.email_utils import get_email_config, send_email_async  # lazy: avoids an import cycle
        config = get_email_config()
        if not (config.get("host") and config.get("user")):
            app_log.warning("Security alert email skipped: no SMTP configured (Email Settings or SMTP_* env vars)")
            return
        subject, body = _build_email(event_type, description, severity, timestamp, fields)
        for to_addr in recipients:
            send_email_async(to_addr, subject, body, config)
    except Exception as e:
        app_log.warning("Security alert email failed: %s", e)


def send_security_alert(event_type: str, description: str, severity: str = "ERROR", **fields):
    """Fire an immediate alert for a critical security event, on every
    configured channel: the webhook (SECURITY_ALERT_WEBHOOK_URL) and/or email
    (SECURITY_ALERT_EMAIL).

    No-ops silently (just a debug log line) if neither is configured --
    this must be safe to call unconditionally from every environment,
    including local dev and CI, without requiring either to exist.
    """
    recipients = _alert_recipients()
    if not _WEBHOOK_URL and not recipients:
        app_log.debug("send_security_alert called but no alert channel configured -- skipping")
        return
    severity = severity.upper()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    clean_description = _sanitize_description(description)
    clean_fields = _sanitize_fields(fields)

    if _WEBHOOK_URL:
        payload = _build_payload(
            event_type=event_type, description=clean_description,
            severity=severity, timestamp=timestamp, fields=clean_fields,
        )
        threading.Thread(target=_deliver, args=(payload,), daemon=True).start()

    if recipients and not _email_throttled(event_type):
        threading.Thread(
            target=_send_email_alert,
            args=(recipients, event_type, clean_description, severity, timestamp, clean_fields),
            daemon=True,
        ).start()
