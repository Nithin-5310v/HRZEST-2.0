# -*- coding: utf-8 -*-
"""Deception honeypot decoy routes -- fake paths a real user never requests
(scanner-bot bait: /.env, /wp-admin/, /phpmyadmin, etc.), banning whatever
IP touches one. Extracted out of the removed SecOps dashboard
(blueprints/secops.py) so this auto-banning perimeter keeps working without
the dashboard/portal it used to live inside -- this file has no dependency
on that portal or its auth model, it's a plain unauthenticated trap.

Ban policy (see utils/waf.ban_ip + utils/client_ip.py):
  * never bans a non-routable/shared source address -- behind a container
    gateway that hides client IPs, one scanner hit used to permanently ban
    the entire site;
  * bans are time-limited (HONEYPOT_BAN_MINUTES, default 7 days) rather
    than permanent, since a scanner's address is routinely a reassigned
    cloud/CGNAT address.
"""
import os

from flask import Blueprint, request, jsonify
from extensions import log_security_event
import utils.waf as waf

honeypot_bp = Blueprint("honeypot_routes", __name__)

_DEFAULT_BAN_MINUTES = 7 * 24 * 60


def _ban_minutes():
    try:
        return max(1, int(os.environ.get("HONEYPOT_BAN_MINUTES", str(_DEFAULT_BAN_MINUTES))))
    except ValueError:
        return _DEFAULT_BAN_MINUTES


@honeypot_bp.route("/.env")
@honeypot_bp.route("/.git/config")
@honeypot_bp.route("/wp-admin/")
@honeypot_bp.route("/wp-login.php")
@honeypot_bp.route("/api/v1/internal/admin_dump")
@honeypot_bp.route("/actuator/health")
@honeypot_bp.route("/phpmyadmin")
@honeypot_bp.route("/backup.sql")
@honeypot_bp.route("/server-status")
def honeypot_trap():
    """Deception Honeypot Trap: Intercepts scanner bots and bans attacker IPs."""
    ip = request.remote_addr or "unknown"
    path = request.path
    log_security_event(
        "secops.honeypot_triggered",
        f"Deception honeypot trap '{path}' triggered by automated scanner bot",
        level="CRITICAL",
        ip=ip,
        path=path,
        method=request.method
    )
    banned = waf.ban_ip(
        ip, f"Honeypot Decoy Trap Triggered ({path})",
        banned_by="system:honeypot", minutes=_ban_minutes(),
    )
    if not banned:
        # Not an error: either the address is shared/non-routable (see
        # utils/client_ip.py) or the DB write failed (ban_ip logs that).
        log_security_event(
            "secops.honeypot_ban_withheld",
            "Honeypot hit did not result in an IP ban",
            level="WARNING", identifier=ip, path=path,
        )

    return jsonify({"error": "Resource not found", "status": 404}), 404


# ── TCP decoy reports (utils/honeypot.py -> /webhooks/honeypot) ──────────────
# The decoy-port honeypot runs as a separate, internet-facing container that
# deliberately holds none of the app's secrets. It tells the app about a hit
# through the generic signed-webhook route, using its own narrow
# HONEYPOT_REPORT_SECRET (see utils/honeypot_report.py). A hit on a port
# nothing legitimate listens on is unambiguous, so the source address is
# banned at the web tier too -- the scanner that just probed MySQL/RDP/FTP
# does not get to continue against the login forms.
from blueprints.webhooks import register_signature_verifier, register_webhook_handler  # noqa: E402
from utils import honeypot_report  # noqa: E402
from utils.client_ip import parse_ip  # noqa: E402


def _verify_honeypot_signature(raw_body, req):
    return honeypot_report.verify(
        raw_body,
        req.headers.get(honeypot_report.TIMESTAMP_HEADER, ""),
        req.headers.get(honeypot_report.SIGNATURE_HEADER, ""),
        os.environ.get("HONEYPOT_REPORT_SECRET", "").strip(),
    )


def _tcp_ban_minutes():
    try:
        return max(1, int(os.environ.get("HONEYPOT_TCP_BAN_MINUTES", "1440")))
    except ValueError:
        return 1440


def _on_tcp_decoy_hit(payload):
    ip = str(payload.get("ip") or "")[:45]
    protocol = str(payload.get("protocol") or "")[:16]
    try:
        port = int(payload.get("port"))
    except (TypeError, ValueError):
        port = 0
    if parse_ip(ip) is None:
        log_security_event(
            "honeypot.tcp_report_invalid", "Honeypot report carried an unparseable source address",
            level="WARNING", identifier=ip[:45] or "unknown",
        )
        return
    log_security_event(
        "honeypot.tcp_decoy_hit",
        "Connection to a TCP decoy port reported by the honeypot",
        level="WARNING", identifier=ip, ip=ip, pattern=f"port={port} protocol={protocol}",
    )
    waf.ban_ip(
        ip, f"TCP decoy port {port}/{protocol} touched",
        banned_by="system:honeypot-tcp", minutes=_tcp_ban_minutes(),
    )


register_signature_verifier("honeypot", _verify_honeypot_signature)
register_webhook_handler("honeypot", honeypot_report.EVENT_NAME, _on_tcp_decoy_hit)
