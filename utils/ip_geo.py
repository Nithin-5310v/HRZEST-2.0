# -*- coding: utf-8 -*-
"""Approximate location of an IP address, for the SOC console.

Read from GeoIP database files on this server: no address is ever sent to an
outside service. Two MaxMind-format (.mmdb) files, each optional:

  <GEOIP_DIR>/city.mmdb   city, region, country, coordinates
                          (DB-IP "IP to City Lite", or MaxMind GeoLite2-City)
  <GEOIP_DIR>/asn.mmdb    the network the address belongs to: AS number, owner
                          (DB-IP "IP to ASN Lite", or MaxMind GeoLite2-ASN)

GEOIP_DIR defaults to instance/geoip in the app checkout; GEOIP_CITY_DB and
GEOIP_ASN_DB name the files directly instead. scripts/update_geoip.sh fetches
them, monthly through hrzest-geoip.timer. A file replaced on disk is picked up
within a minute, without a restart. With neither file present every lookup
answers None and the console simply shows no location.

The answer is an estimate. Free databases are usually right about the country
and often about the city, and they place mobile, VPN and cloud addresses
wherever the network is registered, not where the person is.
"""
import datetime
import ipaddress
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_BATCH = 100             # addresses per lookup_many() call
STALE_DAYS = 60             # older than this, status() says the data is stale
_RECHECK_SECONDS = 60       # how often the files are stat()ed for a replacement
_CACHE_MAX = 20000          # cached answers per process before the cache is emptied
_TEXT_MAX = 120

_SHARED_NAT = ipaddress.ip_network("100.64.0.0/10")    # carrier-grade NAT (RFC 6598)
# Private in the everyday sense: RFC 1918 and IPv6 unique-local. (Python's
# is_private also covers documentation and benchmarking ranges; those are
# reported as 'reserved'.)
_PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))

_lock = threading.Lock()
_state = {"checked": 0.0, "city": None, "asn": None, "cache": {}}

# What each database's licence asks the product to show next to its data.
_ATTRIBUTION = {
    "DB-IP": ("IP geolocation by DB-IP", "https://db-ip.com"),
    "MaxMind": ("This product includes GeoLite2 data created by MaxMind", "https://www.maxmind.com"),
}


def paths():
    """-> (city_path, asn_path) as currently configured."""
    folder = os.environ.get("GEOIP_DIR") or os.path.join(ROOT, "instance", "geoip")
    return (os.environ.get("GEOIP_CITY_DB") or os.path.join(folder, "city.mmdb"),
            os.environ.get("GEOIP_ASN_DB") or os.path.join(folder, "asn.mmdb"))


def reset():
    """Forget the open databases and cached answers (tests; a changed config)."""
    with _lock:
        _state.update(checked=0.0, city=None, asn=None, cache={})


def _provider(db_type):
    t = (db_type or "").lower()
    if t.startswith("dbip"):
        return "DB-IP"
    if t.startswith("geolite") or t.startswith("geoip"):
        return "MaxMind"
    return db_type or "unknown"


def _open(path, current):
    """The database at `path`: `current` if the file hasn't changed, a fresh
    reader if it has, None if there is no usable file."""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    if current and current["path"] == path and current["mtime"] == mtime:
        return current
    try:
        import maxminddb
        reader = maxminddb.open_database(path)
        meta = reader.metadata()
    except Exception as exc:      # missing library, a half-written or foreign file
        log.warning("GeoIP database %s could not be opened: %s", path, exc)
        return None
    built = datetime.datetime.fromtimestamp(int(meta.build_epoch), datetime.timezone.utc)
    # The replaced reader isn't closed here: another thread may be reading it.
    # It closes when the last reference goes.
    return {"path": path, "mtime": mtime, "reader": reader, "type": meta.database_type,
            "provider": _provider(meta.database_type), "built": built}


def _databases():
    """-> (city, asn), re-reading the files at most once a minute."""
    with _lock:
        now = time.monotonic()
        if now - _state["checked"] >= _RECHECK_SECONDS or not _state["checked"]:
            city_path, asn_path = paths()
            city, asn = _open(city_path, _state["city"]), _open(asn_path, _state["asn"])
            if city is not _state["city"] or asn is not _state["asn"]:
                _state["cache"] = {}
            _state.update(checked=now, city=city, asn=asn)
        return _state["city"], _state["asn"]


def scope(addr):
    """Where an address sits, for the ones a GeoIP database can't place:
    'loopback', 'link-local', 'private', 'shared' (carrier NAT), 'multicast',
    'reserved' (documentation, benchmarking, ...) or 'public'."""
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link-local"
    if addr.version == 4 and addr in _SHARED_NAT:
        return "shared"
    if any(addr.version == net.version and addr in net for net in _PRIVATE):
        return "private"
    if addr.is_multicast:
        return "multicast"
    if not addr.is_global:
        return "reserved"
    return "public"


def _text(value):
    return value[:_TEXT_MAX] if isinstance(value, str) and value else None


def _name(rec):
    names = rec.get("names") if isinstance(rec, dict) else None
    return _text(names.get("en")) if isinstance(names, dict) else None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _place(record):
    record = record if isinstance(record, dict) else {}
    country = record.get("country") or record.get("registered_country") or {}
    subdivisions = record.get("subdivisions")
    location = record.get("location") or {}
    code = country.get("iso_code") if isinstance(country, dict) else None
    return {
        "country_code": code if isinstance(code, str) and len(code) == 2 else None,
        "country": _name(country),
        "region": _name(subdivisions[0]) if isinstance(subdivisions, list) and subdivisions else None,
        "city": _name(record.get("city")),
        "latitude": _number(location.get("latitude")) if isinstance(location, dict) else None,
        "longitude": _number(location.get("longitude")) if isinstance(location, dict) else None,
        "accuracy_km": _number(location.get("accuracy_radius")) if isinstance(location, dict) else None,
    }


def _network(record, prefix_len, ip):
    record = record if isinstance(record, dict) else {}
    asn = record.get("autonomous_system_number")
    network = None
    if prefix_len is not None:
        network = str(ipaddress.ip_network(f"{ip}/{prefix_len}", strict=False))
    return {"asn": asn if isinstance(asn, int) and not isinstance(asn, bool) else None,
            "org": _text(record.get("autonomous_system_organization")),
            "network": network if record else None}


def _parse(ip):
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr


def lookup(ip):
    """-> where `ip` is, as far as the databases know:

      {"scope": "public", "country_code", "country", "region", "city",
       "latitude", "longitude", "accuracy_km", "asn", "org", "network"}

    (any of those may be None), {"scope": "private"} and the like for an
    address no database can place, or None when `ip` isn't an address or no
    database is installed."""
    addr = _parse(ip)
    if addr is None:
        return None
    where = scope(addr)
    if where != "public":
        return {"scope": where}
    city, asn = _databases()
    if not city and not asn:
        return None
    key = str(addr)
    cache = _state["cache"]
    if key in cache:
        return cache[key]
    answer = {"scope": "public"}
    answer.update(_place(None))
    answer.update(_network(None, None, key))
    try:
        if city:
            answer.update(_place(city["reader"].get(key)))
        if asn:
            record, prefix_len = asn["reader"].get_with_prefix_len(key)
            answer.update(_network(record, prefix_len, key))
    except Exception as exc:      # a corrupt record: say nothing rather than fail the page
        log.warning("GeoIP lookup of %s failed: %s", key, exc)
        return None
    if len(cache) >= _CACHE_MAX:
        cache.clear()
    cache[key] = answer
    return answer


def lookup_many(ips):
    """-> {ip: lookup(ip)} for up to MAX_BATCH distinct addresses, keyed by the
    text the caller sent. Text that isn't an address is left out."""
    out = {}
    for ip in ips:
        if len(out) >= MAX_BATCH:
            break
        if not isinstance(ip, str) or ip in out or _parse(ip) is None:
            continue
        out[ip] = lookup(ip)
    return out


def status():
    """-> what is installed, for the console: which databases, how old, and the
    attribution their licences ask for."""
    city, asn = _databases()
    now = datetime.datetime.now(datetime.timezone.utc)
    databases, credits = [], []
    for kind, db in (("city", city), ("asn", asn)):
        if not db:
            continue
        age = (now - db["built"]).days
        databases.append({"kind": kind, "provider": db["provider"], "type": db["type"],
                          "built": db["built"].date().isoformat(), "age_days": age, "stale": age > STALE_DAYS})
        credit = _ATTRIBUTION.get(db["provider"])
        if credit and {"text": credit[0], "url": credit[1]} not in credits:
            credits.append({"text": credit[0], "url": credit[1]})
    return {"available": bool(databases), "databases": databases, "attribution": credits}
