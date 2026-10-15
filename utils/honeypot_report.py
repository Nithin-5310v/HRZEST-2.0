# -*- coding: utf-8 -*-
"""Signed honeypot -> app report channel.

The TCP honeypot (utils/honeypot.py) runs as an internet-facing process and
must hold as few secrets as possible. This module is the one thing it
shares with the app: an HMAC over the report body, keyed by a dedicated
HONEYPOT_REPORT_SECRET that grants nothing except the ability to say "this
address touched a decoy port". It is NOT the app's SECRET_KEY, DB password,
or ENCRYPTION_KEY, so a compromised honeypot cannot be escalated with it.

The app side is blueprints/honeypot_routes.py, mounted behind the generic
/webhooks/<provider> route (signature check -> handler). The report carries
only {ip, port, protocol, captured_bytes}: never the attacker-supplied
bytes, which stay in the honeypot's own forensic log -- they are often
SQLi/shell strings that would only trip the WAF on the way in.

stdlib-only on purpose (no Flask/extensions import) so the honeypot
process stays tiny.
"""
import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.request

SIGNATURE_HEADER = "X-Honeypot-Signature"
TIMESTAMP_HEADER = "X-Honeypot-Timestamp"
MAX_SKEW_SECONDS = 300
EVENT_NAME = "tcp_decoy_hit"


def sign(body: bytes, timestamp: str, secret: str) -> str:
    """HMAC-SHA256 over `<timestamp>.<body>` -- binding the timestamp into
    the MAC means a captured request can't be replayed outside the skew
    window with a refreshed header."""
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify(body: bytes, timestamp: str, signature: str, secret: str, now=None) -> bool:
    if not (secret and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    if abs(now - ts) > MAX_SKEW_SECONDS:
        return False
    sig = signature[7:] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(sign(body, timestamp, secret), sig)


def build_request(url: str, secret: str, ip: str, port: int, protocol: str,
                  captured_bytes: int, now=None):
    """-> (body, headers). Split from the network call so it is unit-testable."""
    body = json.dumps({
        "event": EVENT_NAME,
        "ip": ip,
        "port": port,
        "protocol": protocol,
        "captured_bytes": captured_bytes,
    }, separators=(",", ":")).encode()
    ts = str(int(time.time() if now is None else now))
    headers = {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: ts,
        SIGNATURE_HEADER: "sha256=" + sign(body, ts, secret),
    }
    return body, headers


def _ssl_context(url: str, cafile: str):
    if not url.startswith("https://"):
        return None
    if cafile:
        # The app's internal hop uses a self-signed cert (cert.pem) whose
        # SAN won't include the compose service name. Trust exactly that
        # certificate (the cafile IS the trust anchor) and skip only the
        # hostname comparison -- this is pinning, not "verify=False".
        ctx = ssl.create_default_context(cafile=cafile)
        ctx.check_hostname = False
        return ctx
    return ssl.create_default_context()


def report_hit(ip: str, port: int, protocol: str, captured_bytes: int,
               url=None, secret=None, cafile=None, timeout=3) -> bool:
    """Blocking POST; call from an executor. Returns True on 2xx. No-op
    (False) when not configured. Never raises -- reporting is best-effort
    and must not be able to break the listener."""
    url = url if url is not None else os.environ.get("HONEYPOT_REPORT_URL", "").strip()
    secret = secret if secret is not None else os.environ.get("HONEYPOT_REPORT_SECRET", "").strip()
    cafile = cafile if cafile is not None else os.environ.get("HONEYPOT_REPORT_CAFILE", "").strip()
    if not url or not secret:
        return False
    if not url.startswith(("https://", "http://")):
        return False
    try:
        body, headers = build_request(url, secret, ip, port, protocol, captured_bytes)
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(  # nosec B310 -- URL is operator config (scheme checked above), never attacker input
                req, timeout=timeout, context=_ssl_context(url, cafile)) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False
