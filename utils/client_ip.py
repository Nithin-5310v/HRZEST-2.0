# -*- coding: utf-8 -*-
"""Client-IP trust helpers.

Every IP-keyed control in this app (WAF auto-ban, the honeypot's ban, the
`banned_ips` enforcement hook, Flask-Limiter buckets) assumes
`request.remote_addr` identifies ONE client. That assumption fails when the
TCP peer nginx sees is the container runtime's own gateway rather than the
real visitor -- rootless Podman's slirp4netns port-forwarder does exactly
that (see nginx/nginx.conf.template and blueprints/auth.py's comment on
admin_login). In that state every visitor on the internet shares a single
private IP, so:

  * 5 WAF hits from one attacker would ban the whole site for everyone;
  * a single scanner touching /.env would permanently ban the whole site;
  * one 300/min Flask-Limiter bucket would be shared by every user.

The fix for the root cause is deployment-level (see docs/SECURITY_HARDENING.md:
pasta networking / host networking / a trusted edge that forwards the real
address). This module is the in-app safety net so the app degrades to "no
IP ban" instead of "self-inflicted outage" while that is being fixed, and
shouts about it so nobody has to discover it from a support ticket.

Deliberately stdlib-only at import time (no `extensions`/Flask import) so
the tiny honeypot process can import utils.alerts without dragging the
whole Flask app in -- see utils/alerts.py.
"""
import ipaddress
import logging
import os
import threading

_log = logging.getLogger("attendance")

# Networks that can never identify a single remote visitor on the public
# internet. Explicit CIDRs rather than `not ip.is_global`: the documentation
# ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) are "not global"
# too, but the test suite -- and any lab/staging setup -- uses them as
# stand-ins for real client addresses, and they must stay bannable.
_BUILTIN_PROTECTED_CIDRS = (
    "0.0.0.0/32", "::/128",          # unspecified
    "127.0.0.0/8", "::1/128",        # loopback
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # RFC1918
    "100.64.0.0/10",                 # CGNAT / many container gateways
    "169.254.0.0/16", "fe80::/10",   # link-local
    "fc00::/7",                      # IPv6 ULA
)


def _parse_networks(cidrs):
    nets = []
    for raw in cidrs:
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            _log.warning("client_ip: ignoring invalid CIDR %r", raw)
    return nets


_BUILTIN_NETS = _parse_networks(_BUILTIN_PROTECTED_CIDRS)


def _extra_networks():
    """Operator-supplied never-ban CIDRs (office egress IPs, uptime probes,
    the reverse proxy). Re-read on each call: it's a handful of CIDRs and
    this keeps tests/operators from needing a process restart."""
    return _parse_networks(os.environ.get("WAF_NEVER_BAN_CIDRS", "").split(","))


def parse_ip(ip):
    """-> ipaddress object or None. Unwraps IPv4-mapped IPv6 and strips a
    zone id (fe80::1%eth0)."""
    if not ip or not isinstance(ip, str):
        return None
    ip = ip.strip().split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def is_protected_ip(ip) -> bool:
    """True when `ip` must never be IP-banned or used as a per-client
    identity: unknown/unparseable, non-routable, or operator-allow-listed.
    Fails safe -- an address we can't parse is not something to ban,
    because "unknown" would collapse every such client into one row."""
    addr = parse_ip(ip)
    if addr is None:
        return True
    for net in _BUILTIN_NETS:
        if addr.version == net.version and addr in net:
            return True
    for net in _extra_networks():
        if addr.version == net.version and addr in net:
            return True
    return False


# ── Proxy trust ──────────────────────────────────────────────────────────────
def trusted_proxy_hops() -> int:
    """Number of reverse proxies in front of gunicorn whose X-Forwarded-*
    headers are trusted (ProxyFix `x_for`). 1 = nginx only (the shipped
    topology); 2 = e.g. Cloudflare -> nginx. Never more than the real
    count: trusting an extra hop lets a client forge its own address."""
    try:
        return max(0, min(int(os.environ.get("TRUSTED_PROXY_HOPS", "1")), 5))
    except ValueError:
        return 1


def apply_proxy_fix(wsgi_app):
    from werkzeug.middleware.proxy_fix import ProxyFix
    hops = trusted_proxy_hops()
    if hops == 0:
        return wsgi_app
    return ProxyFix(wsgi_app, x_for=hops, x_proto=hops, x_host=hops)


# ── Shared-IP detection ──────────────────────────────────────────────────────
_warned_lock = threading.Lock()
_warned = False


def warn_if_client_ip_collapsed(ip, app_env=None):
    """Logs CRITICAL once per process the first time a production request
    arrives from a non-routable address -- the signature of the container
    gateway collapsing every visitor into one IP. Cheap (one bool check on
    the hot path after the first hit)."""
    global _warned
    if _warned:
        return
    app_env = app_env if app_env is not None else os.environ.get("APP_ENV", "production")
    if app_env == "development":
        return
    with _warned_lock:
        if _warned:
            return
        _warned = True
    _log.critical(
        "client_ip: production request arrived from non-routable address %s. If this is "
        "real internet traffic, the container network is hiding client IPs (rootless "
        "Podman slirp4netns). IP bans and per-IP rate limits are DISABLED for such "
        "addresses to avoid locking out every user -- fix the deployment (see "
        "docs/SECURITY_HARDENING.md, 'Real client IP').", ip,
    )


def _reset_warning_for_tests():
    global _warned
    _warned = False


# ── Rate-limit identity ──────────────────────────────────────────────────────
def rate_limit_key():
    """Flask-Limiter key function.

    Routable client address -> the address itself (unchanged behaviour).
    Shared/non-routable address -> the address PLUS the signed-session
    identity when there is one, so an authenticated user doesn't share a
    bucket with every other visitor (and with whoever is attacking the
    login form). Anonymous traffic stays in the single shared bucket on
    purpose: any client-controlled discriminator (User-Agent, unvalidated
    bearer token) would let an attacker mint unlimited buckets.
    """
    from flask import request, session
    ip = request.remote_addr or "unknown"
    if not is_protected_ip(ip):
        return ip
    ident = session.get("employee_id") or session.get("admin_username")
    if ident:
        return f"{ip}|{ident}"
    return ip
