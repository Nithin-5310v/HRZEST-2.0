# -*- coding: utf-8 -*-
"""IP location and anonymizer intelligence for the SOC console.

For each public address the console shows where it is (country / region /
city), whose network it is (ASN, provider, hostname) and whether it is an
anonymizer (VPN, Tor, open proxy) or a hosting / datacenter range -- the
cases where the location is the relay's, not the person's.

Provider: proxycheck.io v2 over HTTPS (location + proxy/VPN/Tor typing in one
call). Exposure is kept as small as an online lookup allows:

  * only the server calls the provider; the analyst's browser never does;
  * only globally routable addresses are sent -- private, shared, loopback
    and never-ban addresses are answered locally and never leave the box;
  * every answer is cached in att_master.soc_ip_intel, so an address is sent
    at most once per SOC_GEO_TTL_DAYS (default 7);
  * the URL is a constant (no SSRF), calls time out fast, and a provider
    outage only means "location unavailable" -- never a broken view.

Environment:
  SOC_GEO_PROVIDER   proxycheck (default) | off
  SOC_GEO_API_KEY    optional proxycheck.io key (free tier without one is
                     ~1,000 lookups/day; a free key raises that)
  SOC_GEO_TTL_DAYS   cache lifetime, 1-90 days (default 7)
"""
import json
import os
import urllib.parse
import urllib.request

from utils.client_ip import is_protected_ip, parse_ip
from utils.soc_db import read_cursor, write_cursor
from utils.soc_util import clamp_int, iso

PROVIDER_NAME = "proxycheck.io"
_URL = "https://proxycheck.io/v2/"
MAX_LOOKUPS_PER_CALL = 50       # provider round trip per request is bounded
_TIMEOUT_S = 5

# proxycheck "type" values that mean the visible address is a relay.
_ANONYMIZER_TYPES = {"tor": "Tor", "vpn": "VPN", "openvpn": "VPN", "socks": "Proxy", "socks4": "Proxy",
                     "socks5": "Proxy", "http": "Proxy", "https": "Proxy", "compromised server": "Compromised host",
                     "inference engine": "Proxy"}


def enabled():
    return os.environ.get("SOC_GEO_PROVIDER", "proxycheck").strip().lower() not in ("", "off", "none", "0", "false", "no")


def _ttl_days():
    return clamp_int(os.environ.get("SOC_GEO_TTL_DAYS"), 7, 1, 90)


def scope_of(ip):
    """-> 'public' | 'private' | 'invalid'. Private covers every address that
    cannot identify one internet visitor (RFC1918, CGNAT, loopback, link-local,
    operator never-ban CIDRs)."""
    addr = parse_ip(ip)
    if addr is None:
        return "invalid"
    if not addr.is_global or is_protected_ip(ip):
        return "private"
    return "public"


def _private_info(ip):
    return {"ip": ip, "scope": "private", "location": None,
            "note": ("Private or shared address: this is a proxy, load balancer, container gateway or office "
                     "network, not a visitor on the internet. If most traffic shows this address, the app is "
                     "not reading the real client IP -- set TRUSTED_PROXY_HOPS to the number of proxies in front of it.")}


def _yes(v):
    return str(v or "").strip().lower() == "yes"


def normalize(ip, raw):
    """proxycheck.io v2 record -> the console's shape."""
    ctype = str(raw.get("type") or "").strip()
    key = ctype.lower()
    anonymizer = _ANONYMIZER_TYPES.get(key)
    if anonymizer is None and _yes(raw.get("proxy")):
        anonymizer = "VPN" if "vpn" in key else "Proxy"
    if raw.get("vpn") and _yes(raw.get("vpn")):
        anonymizer = anonymizer or "VPN"
    datacenter = key == "hosting"
    city, region, country = raw.get("city"), raw.get("region"), raw.get("country")
    parts = [p for p in (city, region if region != city else None, country) if p]
    if anonymizer == "Tor":
        verdict = "Tor exit node: the location is the exit relay, not the person. Treat the true origin as unknown."
    elif anonymizer:
        verdict = (f"{anonymizer}: the location is the relay's, not the person's. Attackers commonly use these "
                   "to hide origin and rotate addresses; a ban may only move them to the next exit.")
    elif datacenter:
        verdict = ("Hosting / datacenter range: real people rarely browse from here. Typical of bots, scanners "
                   "and attack infrastructure (or a corporate proxy / uptime monitor).")
    else:
        verdict = None
    return {
        "ip": ip, "scope": "public",
        "location": ", ".join(parts) or None,
        "city": city, "region": region, "country": country, "country_code": raw.get("isocode"),
        "continent": raw.get("continent"), "timezone": raw.get("timezone"),
        "lat": raw.get("latitude"), "lon": raw.get("longitude"),
        "asn": raw.get("asn"), "provider": raw.get("provider"), "organisation": raw.get("organisation"),
        "hostname": raw.get("hostname"), "range": raw.get("range"),
        "connection_type": ctype or None,
        "proxy": _yes(raw.get("proxy")) or bool(anonymizer),
        "anonymizer": anonymizer, "datacenter": datacenter, "verdict": verdict,
        "source": PROVIDER_NAME,
    }


def _fetch(ips):
    """One batched provider call. -> {ip: raw record}. Raises on transport errors."""
    params = {"vpn": "1", "asn": "1"}
    key = os.environ.get("SOC_GEO_API_KEY", "").strip()
    if key:
        params["key"] = key
    body = urllib.parse.urlencode({"ips": ",".join(ips)}).encode()
    req = urllib.request.Request(_URL + "?" + urllib.parse.urlencode(params), data=body, method="POST",
                                 headers={"Accept": "application/json", "User-Agent": "hrzest-soc/1"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:     # nosec B310 -- constant https URL
        data = json.loads(resp.read(2 * 1024 * 1024))
    if not isinstance(data, dict) or data.get("status") in ("denied", "error"):
        raise RuntimeError(str((data or {}).get("message") or "provider refused the lookup")[:200])
    return {ip: rec for ip, rec in data.items() if ip in ips and isinstance(rec, dict)}


def _cached(ips):
    if not ips:
        return {}
    with read_cursor("att_master") as cur:
        cur.execute("SELECT ip, data, fetched_at FROM soc_ip_intel WHERE ip = ANY(%s) "
                    "AND fetched_at > NOW() - (%s * INTERVAL '1 day')", (list(ips), _ttl_days()))
        out = {}
        for ip, data, ts in cur.fetchall():
            try:
                rec = json.loads(data)
            except ValueError:
                continue
            rec["fetched_at"] = iso(ts)
            out[ip] = rec
        return out


def _store(records):
    if not records:
        return
    with write_cursor("att_master") as cur:
        for ip, rec in records.items():
            cur.execute("INSERT INTO soc_ip_intel (ip, data, fetched_at) VALUES (%s, %s, NOW()) "
                        "ON CONFLICT (ip) DO UPDATE SET data = EXCLUDED.data, fetched_at = EXCLUDED.fetched_at",
                        (ip, json.dumps(rec)))


def lookup(ips):
    """-> {ip: info} for every valid address given. Never raises.

    info.scope is 'public' (with location fields when available), 'private'
    (answered locally, never sent anywhere) or 'unavailable' (provider off,
    over quota or unreachable)."""
    out, public = {}, []
    for ip in dict.fromkeys(str(i).strip() for i in (ips or []) if i):
        s = scope_of(ip)
        if s == "private":
            out[ip] = _private_info(ip)
        elif s == "public":
            public.append(ip)
    if not public:
        return out
    if not enabled():
        out.update({ip: {"ip": ip, "scope": "unavailable", "note": "IP lookup is turned off (SOC_GEO_PROVIDER=off)."}
                    for ip in public})
        return out
    try:
        hits = _cached(public)
    except Exception:
        hits = {}
    out.update(hits)
    misses = [ip for ip in public if ip not in hits][:MAX_LOOKUPS_PER_CALL]
    fresh, err = {}, None
    if misses:
        try:
            fresh = {ip: normalize(ip, raw) for ip, raw in _fetch(misses).items()}
        except Exception as exc:
            err = f"Location lookup failed ({type(exc).__name__})."
        try:
            _store(fresh)
        except Exception:
            pass
        out.update(fresh)
    for ip in public:
        if ip not in out:
            out[ip] = {"ip": ip, "scope": "unavailable",
                       "note": err or "Not looked up yet (per-request limit); it will resolve on the next load."}
    return out
