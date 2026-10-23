# -*- coding: utf-8 -*-
"""Decoy TCP listeners -- "system32_crypto_admin" -- for ports nothing
legitimate on this stack ever uses (FTP 21, Telnet 23, SMTP 25, MSSQL
1433, MySQL 3306, RDP 3389 -- the six commonly-scanned service ports with
no legitimate use on this stack).

Any connection here is unambiguous: there is no real service behind any
of these ports, so every single hit is either a port scan or an active
exploitation attempt, never a false positive from a legitimate user. That
certainty is what makes it safe to page on immediately (see
send_security_alert below) in a way a real-service anomaly detector never
could be.

Deliberately minimal -- no protocol emulation, no parsing of what the
client sends. This process never interprets attacker-supplied bytes as
anything other than opaque logging data, which is what keeps a honeypot
itself from becoming the next vulnerability. It accepts a connection,
waits briefly for whatever the client sends, logs it, and closes.

Runs as its own tiny standalone process (python -m utils.honeypot) -- no
Flask, no DB dependency, so it starts and keeps running independently of
the main app's health. It imports only utils.alerts and
utils.honeypot_report, both stdlib-only, and needs just SECURITY_ALERT_* and
HONEYPOT_REPORT_* in its environment -- never the app's SECRET_KEY, DB
credentials or ENCRYPTION_KEY (compose.yaml passes an explicit allow-list
instead of env_file). Besides the chat/email alert it posts a signed
{ip, port, protocol} report to the app (utils/honeypot_report.py) so the
source address is banned at the web tier too.
"""
import asyncio
import datetime
import json
import logging
import os

log = logging.getLogger("honeypot")

SERVICE_NAME = "system32_crypto_admin"

# bind_port -> (public_port, protocol_label). This container runs
# unprivileged (same as nginx -- see compose.yaml/deploy.sh), so it can't
# bind ports <1024 directly. FTP/Telnet/SMTP are DNAT-redirected from
# their real public ports at the host level (scripts/host_hardening.sh's
# ufw before.rules block,
# same mechanism already used for 80->8080/443->8443) -- this listener
# only ever sees the high internal port, so public_port here is purely
# for accurate logging/alerting of what the attacker actually targeted.
# MSSQL/MySQL/RDP are all >1024 already and need no redirect.
DECOY_PORTS = {
    8021: (21, "ftp"),
    8023: (23, "telnet"),
    8025: (25, "smtp"),
    1433: (1433, "mssql"),
    3306: (3306, "mysql"),
    3389: (3389, "rdp"),
}

_READ_TIMEOUT_SECONDS = 5
_REPORT_TIMEOUT_SECONDS = 5
_MAX_CAPTURE_BYTES = 512


def _log_path():
    return os.environ.get("HONEYPOT_LOG_PATH", "/var/log/honeypot/hits.jsonl")


def _record_hit(port: int, protocol: str, peer_ip: str, captured: bytes):
    """Full, unredacted forensic record -- this is threat intel, not one of
    the app's own secrets, so unlike send_security_alert below it is not
    sanitized. Appended as one JSON line per hit; rotate/ship this file
    (CloudWatch agent, journald, whatever the deployment already uses for
    container logs) rather than growing it unbounded here."""
    entry = {
        "time": datetime.datetime.utcnow().isoformat() + "Z",
        "service": SERVICE_NAME,
        "port": port,
        "protocol": protocol,
        "source_ip": peer_ip,
        "captured_bytes": len(captured),
        "captured_preview": captured[:_MAX_CAPTURE_BYTES].decode("latin-1"),
    }
    log.warning(json.dumps(entry))
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.error("honeypot: failed to write hit log: %s", e)


def _alert_hit(port: int, protocol: str, peer_ip: str, captured: bytes):
    """Summary-only, sanitized alert to the existing Slack/Discord webhook
    (utils/alerts.py) -- deliberately NOT the raw captured bytes (those
    could be an attacker-submitted credential; send_security_alert's own
    redaction would likely catch it, but the full unredacted capture
    belongs in the forensic log above, not relayed into a chat channel)."""
    from utils.alerts import send_security_alert
    preview = captured[:80].decode("latin-1", errors="replace") if captured else ""
    send_security_alert(
        event_type="honeypot.connection",
        description=f"Connection to decoy {protocol} port {port} -- no legitimate service listens here",
        severity="WARNING",
        identifier=f"{SERVICE_NAME}:{port}",
        ip=peer_ip,
        reason=preview,
        pattern=f"port={port} protocol={protocol}",
    )


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, bind_port: int):
    port, protocol = DECOY_PORTS.get(bind_port, (bind_port, "unknown"))
    peer = writer.get_extra_info("peername")
    peer_ip = peer[0] if peer else "unknown"
    captured = b""
    try:
        captured = await asyncio.wait_for(reader.read(_MAX_CAPTURE_BYTES), timeout=_READ_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, ConnectionError):
        # Expected, not an error: most portscan connections send nothing
        # before timing out or disconnecting -- the connection itself is
        # still logged unconditionally via _record_hit() below regardless.
        pass
    finally:
        try:
            writer.close()
        except Exception as exc:
            log.debug("honeypot writer.close() failed for %s: %s", peer_ip, exc)

    _record_hit(port, protocol, peer_ip, captured)
    try:
        _alert_hit(port, protocol, peer_ip, captured)
    except Exception as e:
        # A webhook outage must never crash the listener -- same principle
        # as send_security_alert's own fire-and-forget design.
        log.error("honeypot: alert dispatch failed: %s", e)
    await _report_to_app(port, protocol, peer_ip, len(captured))


async def _report_to_app(port: int, protocol: str, peer_ip: str, captured_bytes: int):
    """Best-effort signed report to the app (no-op unless HONEYPOT_REPORT_URL
    and HONEYPOT_REPORT_SECRET are set). Run in an executor with a hard cap:
    a hung app must never stall the accept loop."""
    from utils.honeypot_report import report_hit
    try:
        loop = asyncio.get_running_loop()
        ok = await asyncio.wait_for(
            loop.run_in_executor(None, report_hit, peer_ip, port, protocol, captured_bytes),
            timeout=_REPORT_TIMEOUT_SECONDS,
        )
        if not ok:
            log.debug("honeypot: report to app not delivered (unconfigured or rejected)")
    except Exception as e:
        log.warning("honeypot: report to app failed: %s", e)


async def _serve_port(bind_port: int):
    async def _handler(reader, writer):
        await _handle_connection(reader, writer, bind_port)

    server = await asyncio.start_server(_handler, "0.0.0.0", bind_port)  # nosec B104
    public_port, protocol = DECOY_PORTS[bind_port]
    log.info("honeypot: bound %d, decoying public port %d (%s)", bind_port, public_port, protocol)
    async with server:
        await server.serve_forever()


async def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.info("%s starting -- decoy ports: %s", SERVICE_NAME, sorted(DECOY_PORTS))
    await asyncio.gather(*(_serve_port(p) for p in DECOY_PORTS))


if __name__ == "__main__":
    asyncio.run(run())
