# -*- coding: utf-8 -*-
"""Notice when a signed-in session changes network or browser mid-session.

A session cookie copied to another machine keeps working until it expires.
What gives it away is that the requests now come from somewhere else: a
different browser, or a different network. This module remembers, inside the
session itself, the network and browser it was first used from and the ones it
was last seen from, and records a security event at each change:

  session.browser_changed  WARNING  the browser family changed (e.g. Chrome on
                                    Windows -> Firefox on Linux, or curl). A
                                    cookie does not move between browsers on its
                                    own; DevTools device emulation is the usual
                                    harmless cause.
  session.network_changed  INFO     the client network changed (IPv4 /24, IPv6
                                    /48). Common and usually harmless: phones
                                    move between Wi-Fi and mobile data.

The detections session_browser_switch and session_network_hopping
(utils/detections.py) turn these into alerts. Nothing here ends a session:
an analyst can revoke it from the SOC console (Response).

The state rides in Flask's signed session cookie, so a stolen cookie carries
the victim's context with it and the first request from the thief's browser is
the one that stands out. Only a browser family is kept, never the full
User-Agent, so a browser update does not count as a change.
"""
import hashlib
import ipaddress

_TOOLS = (("curl/", "curl"), ("wget/", "Wget"), ("python-requests", "python-requests"), ("python-urllib", "Python urllib"),
          ("okhttp", "okhttp"), ("go-http-client", "Go HTTP client"), ("postmanruntime", "Postman"),
          ("insomnia", "Insomnia"), ("java/", "Java"), ("node-fetch", "node-fetch"), ("axios", "axios"))


def network_of(ip):
    """The network an address belongs to for this comparison: IPv4 /24, IPv6 /48."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return ip or "unknown"
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def browser_of(user_agent):
    """Browser family and OS from a User-Agent, e.g. "Chrome on Windows". The
    version is left out on purpose."""
    ua = user_agent or ""
    low = ua.lower()
    if not ua.strip():
        return "no User-Agent"
    for needle, name in _TOOLS:
        if needle in low:
            return name
    if "edg/" in low or "edga/" in low or "edgios/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "samsungbrowser/" in low:
        browser = "Samsung Internet"
    elif "firefox/" in low or "fxios/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low or "chromium/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = "Other browser"
    if "android" in low:
        system = "Android"
    elif "iphone" in low or "ipad" in low or "ios" in low:
        system = "iOS"
    elif "windows" in low:
        system = "Windows"
    elif "mac os x" in low or "macintosh" in low:
        system = "macOS"
    elif "cros" in low:
        system = "ChromeOS"
    elif "linux" in low:
        system = "Linux"
    else:
        system = "other OS"
    return f"{browser} on {system}"


def session_ref(sid):
    """A short reference to a session for events and the console: enough to
    match events of one session, useless as a session id."""
    return hashlib.sha256(str(sid).encode("utf-8")).hexdigest()[:12]


def observe(session, ip, user_agent):
    """Compare this request with what the session was last seen from.
    -> list of ("browser" | "network", from, to, extra) changes, and updates
    the session. The first request of a session (or after its id changed)
    only records where it started."""
    sid = session.get("_sid")
    if not sid:
        return []
    ref = session_ref(sid)
    net, browser = network_of(ip), browser_of(user_agent)
    ctx = session.get("_ctx")
    if not isinstance(ctx, dict) or ctx.get("ref") != ref:
        session["_ctx"] = {"ref": ref, "net0": net, "ua0": browser, "net": net, "ua": browser, "nets": [net]}
        return []
    changes = []
    if browser != ctx.get("ua"):
        changes.append(("browser", ctx.get("ua"), browser, {"login_browser": ctx.get("ua0")}))
    if net != ctx.get("net"):
        seen = list(ctx.get("nets") or [])
        changes.append(("network", ctx.get("net"), net, {"login_network": ctx.get("net0"), "returning": net in seen}))
        if net not in seen:
            seen = (seen + [net])[-5:]
        ctx = dict(ctx, nets=seen)
    if changes:
        session["_ctx"] = dict(ctx, net=net, ua=browser)
    return changes
