# -*- coding: utf-8 -*-
"""Signed channel between the host's firewall agent and the app.

  POST /webhooks/firewall/events     the agent reports block / allow decisions
                                     from the layers outside the app (AWS edge
                                     WAF, VPC flow logs, host firewall, nginx +
                                     ModSecurity, fail2ban)
  GET  /webhooks/firewall/blocklist  the agent fetches the active ban list to
                                     enforce in the host firewall and the AWS
                                     WAF block list

Both are server-to-server: no session, no tenant. Authenticated by an
HMAC-SHA256 over `<timestamp>.<body>` (for the GET, `<timestamp>.GET <path>`)
keyed with FIREWALL_AGENT_SECRET, with a 5-minute skew window; the same scheme
as the honeypot channel (utils/honeypot_report.py). Without a secret of at
least 32 characters both answer 404, as an unknown webhook provider does, so
nothing is reachable until an operator opts in.

CSRF: exempt, like every /webhooks/ path (app_setup/csrf_csp.py). WAF: the
agent base64-encodes attacker-supplied text (request paths, rule messages), so
the reports pass the WAF layers they describe without an exemption.
"""
from flask import Blueprint, jsonify, request

from extensions import limiter, log_security_event
from utils import firewall_events

firewall_ingest_bp = Blueprint("firewall_ingest", __name__)

MAX_BODY_BYTES = 2 * 1024 * 1024


def _signature_ok(message):
    key = firewall_events.secret()
    if not key:
        return None             # channel closed
    return firewall_events.verify(
        message,
        request.headers.get(firewall_events.TIMESTAMP_HEADER, ""),
        request.headers.get(firewall_events.SIGNATURE_HEADER, ""),
        key,
    )


def _refused():
    log_security_event("firewall.agent_signature_invalid", "Firewall agent request with a bad signature",
                       level="ERROR")
    return jsonify({"ok": False}), 401


@firewall_ingest_bp.route("/webhooks/firewall/events", methods=["POST"])
@limiter.limit("240 per minute")
def report_events():
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        return jsonify({"ok": False, "msg": "Report too large."}), 413
    raw = request.get_data(cache=True)
    if len(raw) > MAX_BODY_BYTES:
        return jsonify({"ok": False, "msg": "Report too large."}), 413
    ok = _signature_ok(raw)
    if ok is None:
        return jsonify({"ok": False}), 404
    if not ok:
        return _refused()
    payload = request.get_json(silent=True)
    accepted, result = firewall_events.ingest(payload, reporter_ip=request.remote_addr)
    if not accepted:
        return jsonify({"ok": False, "msg": result}), 400
    return jsonify({"ok": True, **result})


@firewall_ingest_bp.route(firewall_events.BLOCKLIST_PATH, methods=["GET"])
@limiter.limit("60 per minute")
def blocklist():
    ok = _signature_ok(firewall_events.blocklist_message())
    if ok is None:
        return jsonify({"ok": False}), 404
    if not ok:
        return _refused()
    return jsonify({"ok": True, **firewall_events.blocklist()})
