# -*- coding: utf-8 -*-
"""Sending email through Microsoft 365 (Microsoft Graph's sendMail), for a
deployment whose mail goes out from a Microsoft 365 mailbox instead of an
SMTP server.

Uses an Entra ID (Azure AD) app registration with the client-credentials
flow: the app needs the Microsoft Graph *application* permission Mail.Send,
granted admin consent, and the sender (EMAIL_FROM_EMAIL) must be a real
mailbox or shared mailbox in that tenant. Configured in .env:

    EMAIL_PROVIDER=microsoft_graph
    MICROSOFT_TENANT_ID=...      MICROSOFT_CLIENT_ID=...
    MICROSOFT_CLIENT_SECRET=...  EMAIL_FROM_EMAIL=noreply@example.com
    EMAIL_FROM_NAME=HRzest       (optional)
    MICROSOFT_GRAPH_ENDPOINT=https://graph.microsoft.com/v1.0  (optional)

utils/email_utils.py picks this up in get_email_config() and routes
send_email_smtp() here, so every existing caller -- the email queue, OTPs,
payslips -- sends through Microsoft 365 unchanged.
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDER = "microsoft_graph"
_DEFAULT_ENDPOINT = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 20
# A token is reused until this many seconds before it expires.
_EXPIRY_MARGIN = 120

_token_lock = threading.Lock()
_token_cache = {}  # (tenant, client_id) -> (access_token, expires_at)


class GraphMailError(RuntimeError):
    """A token or sendMail request was refused. The message carries
    Microsoft's error code and text -- never the secret or the token."""


def config_from_env():
    """The Microsoft 365 config dict, or None when .env doesn't select it
    (EMAIL_PROVIDER=microsoft_graph plus tenant, client id, secret and a
    sender). Carries host/port/user like an SMTP config, because a few
    callers check those before sending (see utils/email_utils.py)."""
    if os.environ.get("EMAIL_PROVIDER", "").strip().lower() != PROVIDER:
        return None
    tenant = os.environ.get("MICROSOFT_TENANT_ID", "").strip()
    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
    secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
    sender = (os.environ.get("EMAIL_FROM_EMAIL") or os.environ.get("SMTP_FROM_EMAIL") or "").strip()
    if not (tenant and client_id and secret and sender):
        return None
    endpoint = (os.environ.get("MICROSOFT_GRAPH_ENDPOINT") or _DEFAULT_ENDPOINT).strip().rstrip("/")
    return {
        "provider": PROVIDER,
        "tenant_id": tenant,
        "client_id": client_id,
        "client_secret": secret,
        "endpoint": endpoint,
        "from_email": sender,
        "from_name": (os.environ.get("EMAIL_FROM_NAME") or os.environ.get("SMTP_FROM_NAME") or "HRzest.com").strip(),
        # For callers that only check an SMTP-shaped config is present, and
        # the Email Settings test page that shows host:port.
        "host": urllib.parse.urlsplit(endpoint).hostname or "graph.microsoft.com",
        "port": 443,
        "user": sender,
        "password": "",
    }


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310 -- fixed https Microsoft endpoints
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _error_text(body):
    """Microsoft's own error code and message from a refused request."""
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return "unreadable error response"
    err = data.get("error")
    if isinstance(err, dict):  # Graph: {"error": {"code", "message"}}
        return f"{err.get('code', '')}: {err.get('message', '')}".strip(": ")
    if err:  # Entra token endpoint: {"error", "error_description"}
        desc = (data.get("error_description") or "").splitlines()[0] if data.get("error_description") else ""
        return f"{err}: {desc}".strip(": ")
    return "no error details"


def _access_token(cfg):
    key = (cfg["tenant_id"], cfg["client_id"])
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] - _EXPIRY_MARGIN > time.time():
            return cached[0]
        tenant = urllib.parse.quote(cfg["tenant_id"], safe="")
        status, body = _post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            urllib.parse.urlencode({
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        if status != 200:
            raise GraphMailError(f"Microsoft 365 sign-in failed (HTTP {status}) -- {_error_text(body)}")
        data = json.loads(body)
        token = data["access_token"]
        _token_cache[key] = (token, time.time() + int(data.get("expires_in", 3599)))
        return token


def _recipients(to_email):
    if isinstance(to_email, (list, tuple, set)):
        addrs = list(to_email)
    else:
        addrs = str(to_email or "").split(",")
    return [{"emailAddress": {"address": a.strip()}} for a in addrs if a and a.strip()]


def send_mail(cfg, to_email, subject, html_body, attachment_bytes=None, attachment_filename=None):
    """Send one email through Microsoft Graph. Raises GraphMailError (or a
    network error) on failure, like smtplib does -- callers and the email
    queue already treat an exception as "not sent, retry"."""
    recipients = _recipients(to_email)
    if not recipients:
        raise GraphMailError("No recipient address.")
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": recipients,
        "from": {"emailAddress": {"name": cfg.get("from_name") or "", "address": cfg["from_email"]}},
    }
    if attachment_bytes and attachment_filename:
        message["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment_filename,
            "contentType": "application/octet-stream",
            "contentBytes": base64.b64encode(attachment_bytes).decode(),
        }]
    payload = json.dumps({"message": message, "saveToSentItems": False}).encode()
    url = f"{cfg['endpoint']}/users/{urllib.parse.quote(cfg['from_email'], safe='@')}/sendMail"
    for attempt in (1, 2):
        status, body = _post(url, payload, {
            "Authorization": f"Bearer {_access_token(cfg)}",
            "Content-Type": "application/json",
        })
        if status == 202:
            return
        if status == 401 and attempt == 1:
            # Token revoked or expired early: drop it and get a fresh one once.
            with _token_lock:
                _token_cache.pop((cfg["tenant_id"], cfg["client_id"]), None)
            continue
        break
    raise GraphMailError(f"Microsoft 365 refused the email (HTTP {status}) -- {_error_text(body)}")
