#!/usr/bin/env python3
"""Fill a local or dev HRZEST with real security telemetry by using it.

Every event this produces is real: the script is just another client making
HTTP requests to a running server -- signing in, failing to sign in, browsing,
exporting payroll, calling the mobile API, probing like a scanner -- and the
app records what it saw exactly as it would for anyone else. Nothing is
written to the database directly and no numbers are made up, so the SOC
console (/socperson) shows what the app really did with that traffic.

    python scripts/generate_real_events.py http://localhost:5000 [--company acme]

Scenarios (all by default; pick with --scenario, repeatable):
  browse          an admin signs in on the website and opens a few pages
  failed-logins   one address tries many made-up accounts (credential
                  stuffing): auth.failure events, then the credential_stuffing
                  detection. Never the real admin account, so nothing of
                  yours gets locked out
  probes          a scanner: honeypot paths, unknown pages, SQL injection and
                  path traversal in the URL, an attack-tool User-Agent ->
                  honeypot, WAF and 404 events, Ban review, the Traffic view
  exports         the admin downloads the salary report 6 times -> audit rows
                  and the mass_export detection
  session-switch  the admin's session cookie used from another browser and
                  network -> session.browser_changed / network_changed and
                  the session_browser_switch detection
  api             the mobile API with Bearer tokens (admin; employee too with
                  GEN_EMPLOYEE_ID), and a location check-in when the company
                  allows one

Credentials come from the environment, never the command line (shell history,
process lists): GEN_ADMIN_USER / GEN_ADMIN_PASSWORD (browse, exports,
session-switch, api) and optionally GEN_EMPLOYEE_ID / GEN_EMPLOYEE_PASSWORD.
Scenarios that need a missing credential are skipped and say so.

Source addresses: each simulated client sends X-Forwarded-For with an address
from the documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24:
never real hosts). The app uses it only if it trusts one proxy hop
(TRUSTED_PROXY_HOPS=1); otherwise every request comes from 127.0.0.1, which
the SOC console leaves out as the server's own traffic, and the script warns.
The scanner address will usually end up banned or queued for Ban review.

Safety: the target must be this machine or a private-network address. Any
other host needs --allow-host with that exact host name, and the production
hosts (hrzest.com, www.hrzest.com, plus GEN_REFUSE_HOSTS) are refused
whatever is passed. Exit code 0 on success, 2 when refused, 1 on an error.
"""
import argparse
import html
import http.client
import http.cookiejar
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

SCENARIOS = ("browse", "failed-logins", "probes", "exports", "session-switch", "api")
PRODUCTION_HOSTS = ("hrzest.com", "www.hrzest.com")
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"
PHONE_APP = "okhttp/4.12.0"
SCANNER = "sqlmap/1.8#stable (https://sqlmap.org)"

# Documentation addresses (RFC 5737): one per role, so each shows as its own source.
ADDR = {"admin": "203.0.113.10", "stuffer": "198.51.100.66", "scanner": "192.0.2.77",
        "elsewhere": "198.51.100.30", "phone": "203.0.113.44"}


class Refused(Exception):
    """The target is not one this script may be pointed at."""


def check_target(url, allow_host=None):
    """-> the normalised base URL, or raise Refused."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused(f"{url!r} is not an http(s) URL")
    host = parts.hostname.lower().rstrip(".")
    refused = {h.strip().lower() for h in PRODUCTION_HOSTS + tuple(os.environ.get("GEN_REFUSE_HOSTS", "").split(",")) if h.strip()}
    if host in refused or any(host.endswith("." + h) for h in refused):
        raise Refused(f"{host} is a production host: this script never runs against production")
    local = host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local")
    try:
        addr = ipaddress.ip_address(host)
        local = local or addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        pass
    if not local and host != (allow_host or "").strip().lower().rstrip("."):
        raise Refused(f"{host} is not this machine or a private-network address; pass --allow-host {host} "
                      "if it is a dev or staging server you run")
    return f"{parts.scheme}://{parts.netloc}"


class _BrowserCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    """Send Secure cookies to http://localhost as browsers do (RFC 6265bis):
    the app marks its session cookie Secure everywhere and relies on that
    exemption for local development."""

    def return_ok_secure(self, cookie, request):
        host = urllib.parse.urlsplit(request.get_full_url()).hostname or ""
        if cookie.secure and (host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")):
            return True
        return super().return_ok_secure(cookie, request)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                      # report 3xx as they are, like the app sent them


def answering_address(host, port):
    """The first of `host`'s addresses that accepts a connection, or None.

    "localhost" often resolves to ::1 first while a dev server listens on
    127.0.0.1 only; each request would then wait for the refused IPv6 attempt
    (about 2 s on Windows). Browsers settle on the address that answers; so
    does this, once. The Host header still says localhost (the app redirects
    127.0.0.1 to localhost)."""
    seen = []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for family, _, _, _, sockaddr in infos:
        if sockaddr[0] in seen:
            continue
        seen.append(sockaddr[0])
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.connect(sockaddr)
            return sockaddr[0]
        except OSError:
            continue
    return None


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    """Plain-HTTP requests connect to one known address (see answering_address)."""

    def __init__(self, address):
        super().__init__()
        self.address = address

    def http_open(self, req):
        address = self.address

        class _Connection(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.create_connection((address, self.port), self.timeout)
        return self.do_open(_Connection, req)


class Client:
    """One simulated browser or phone: its own cookies, User-Agent and address."""

    def __init__(self, base, prefix, ip, user_agent, tally, timeout=15, pin=None):
        self.base, self.prefix, self.ip, self.ua, self.tally, self.timeout = base, prefix, ip, user_agent, tally, timeout
        self.jar = http.cookiejar.CookieJar(policy=_BrowserCookiePolicy())
        handlers = [urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect]
        if pin and base.startswith("http://"):
            handlers.append(_PinnedHTTPHandler(pin))
        self.opener = urllib.request.build_opener(*handlers)
        self.token = None

    def url(self, path, company=True):
        return self.base + (self.prefix if company else "") + path

    def call(self, method, path, form=None, body=None, headers=None, company=True):
        """-> (status, text). Never raises for an HTTP status."""
        h = {"User-Agent": self.ua, "X-Forwarded-For": self.ip, "Accept": "text/html,application/json"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.url(path, company), data=data, headers=h, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                status, text = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            status, text = err.code, err.read().decode("utf-8", "replace")
        self.tally[status] += 1
        return status, text

    def csrf(self, path):
        _, page = self.call("GET", path)
        m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', page) or re.search(r'name="csrf-token"\s+content="([^"]+)"', page)
        return html.unescape(m.group(1)) if m else ""

    def web_login(self, identifier, password):
        """Sign in through the website's login form. -> True when it led into the app."""
        status, _ = self.call("POST", "/login", form={"identifier": identifier, "password": password,
                                                      "_csrf_token": self.csrf("/login")})
        signed_in = any(c.name.endswith("session") for c in self.jar) and status in (302, 303)
        if signed_in:
            st, _ = self.call("GET", "/admin")
            signed_in = st == 200
        return signed_in

    def api_login(self, path, body):
        status, text = self.call("POST", path, body=body)
        try:
            self.token = json.loads(text).get("token") if status == 200 else None
        except ValueError:
            self.token = None
        return bool(self.token)


# ── scenarios ───────────────────────────────────────────────────────────────
def browse(ctx):
    c = ctx.client("admin", CHROME)
    if not c.web_login(ctx.admin_user, ctx.admin_password):
        return "admin sign-in did not succeed (wrong GEN_ADMIN_* credentials, a lockout, or a required second factor)"
    for path in ("/admin", "/employees", "/settings", "/salary_report", "/activity_log", "/no-such-page"):
        c.call("GET", path)
    return "signed in and opened 6 pages (one of them missing on purpose)"


def failed_logins(ctx):
    c = ctx.client("stuffer", CHROME)
    tried = 0
    for n in range(1, 7):
        for _ in range(2):
            c.call("POST", "/login", form={"identifier": f"gen.user{n}@example.test", "password": f"Spring{2024 + n}!",
                                           "_csrf_token": c.csrf("/login")})
            tried += 1
    return f"{tried} wrong passwords against 6 made-up accounts from {c.ip}"


def probes(ctx):
    c = ctx.client("scanner", SCANNER)
    paths = ["/.env", "/wp-login.php", "/phpmyadmin", "/.git/config", "/admin.php", "/old-site/",
             "/employees?q=%27%20OR%201%3D1--", "/download?file=..%2F..%2F..%2Fetc%2Fpasswd",
             "/search?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E"]
    for path in paths:
        c.call("GET", path, company=not path.startswith(("/.env", "/wp-", "/phpmyadmin", "/.git")))
    return f"{len(paths)} scanner requests from {c.ip}"


def exports(ctx):
    c = ctx.client("admin", CHROME)
    if not c.web_login(ctx.admin_user, ctx.admin_password):
        return "admin sign-in did not succeed"
    year = time.gmtime().tm_year
    ok = sum(1 for month in range(1, 7) if c.call("GET", f"/salary_report_export?year={year}&month={month}")[0] == 200)
    return f"{ok} of 6 salary report exports downloaded"


def session_switch(ctx):
    c = ctx.client("admin", CHROME)
    if not c.web_login(ctx.admin_user, ctx.admin_password):
        return "admin sign-in did not succeed"
    c.call("GET", "/employees")
    c.ua, c.ip = FIREFOX, ADDR["elsewhere"]          # the same cookie, from another browser and network
    status, _ = c.call("GET", "/employees")
    return f"session reused from {FIREFOX.split(';')[1].strip()} at {c.ip} (answered {status})"


def api(ctx):
    notes = []
    admin = ctx.client("phone", PHONE_APP)
    if admin.api_login("/api/login", {"username": ctx.admin_user, "password": ctx.admin_password}):
        for path in ("/api/employees", "/api/salary_report", "/api/payroll/status"):
            admin.call("GET", path)
        notes.append("admin API: 3 calls")
    else:
        notes.append("admin API sign-in did not succeed (or needs a second factor)")
    if ctx.employee_id and ctx.employee_password:
        emp = ctx.client("phone", PHONE_APP)
        if emp.api_login("/api/employee/login", {"employee_id": ctx.employee_id, "password": ctx.employee_password}):
            status, text = emp.call("POST", "/api/employee/checkin", body={"lat": 12.9716, "lon": 77.5946})
            notes.append("employee check-in " + ("recorded" if status == 200 else f"refused ({status}): " + _msg(text)))
        else:
            notes.append("employee API sign-in did not succeed")
    else:
        notes.append("no GEN_EMPLOYEE_ID: employee API skipped")
    return "; ".join(notes)


def _msg(text):
    try:
        return str(json.loads(text).get("msg", ""))[:120]
    except ValueError:
        return text[:80]


RUNNERS = {"browse": browse, "failed-logins": failed_logins, "probes": probes, "exports": exports,
           "session-switch": session_switch, "api": api}
NEEDS_ADMIN = {"browse", "exports", "session-switch", "api"}


class Context:
    def __init__(self, base, company, tally, pin=None):
        self.base, self.prefix, self.tally, self.pin = base, (f"/{company.strip('/')}" if company else ""), tally, pin
        self.admin_user = os.environ.get("GEN_ADMIN_USER", "")
        self.admin_password = os.environ.get("GEN_ADMIN_PASSWORD", "")
        self.employee_id = os.environ.get("GEN_EMPLOYEE_ID", "")
        self.employee_password = os.environ.get("GEN_EMPLOYEE_PASSWORD", "")

    def client(self, role, user_agent):
        return Client(self.base, self.prefix, ADDR[role], user_agent, self.tally, pin=self.pin)


def main(argv=None):
    p = argparse.ArgumentParser(description="Make real requests to a local/dev HRZEST so the SOC console has real data.")
    p.add_argument("url", help="the running server, e.g. http://localhost:5000")
    p.add_argument("--company", help="company URL slug, when the app runs one company per URL prefix")
    p.add_argument("--scenario", action="append", choices=SCENARIOS, help="run only these (default: all)")
    p.add_argument("--rounds", type=int, default=1, help="repeat the scenarios this many times (default 1)")
    p.add_argument("--pause", type=float, default=0.5, help="seconds between scenarios (default 0.5)")
    p.add_argument("--allow-host", help="run against this exact non-local host (a dev or staging server you run)")
    args = p.parse_args(argv)

    try:
        base = check_target(args.url, args.allow_host)
    except Refused as why:
        print(f"Refused: {why}.", file=sys.stderr)
        return 2
    tally = Counter()
    parts = urllib.parse.urlsplit(base)
    pin = answering_address(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    ctx = Context(base, args.company, tally, pin)
    probe = Client(base, "", ADDR["admin"], CHROME, Counter(), pin=pin)
    try:
        status, _ = probe.call("GET", "/healthz", company=False)
    except (urllib.error.URLError, OSError) as err:
        print(f"Could not reach {base} ({type(err).__name__}): is the server running?", file=sys.stderr)
        return 1
    if status != 200:
        print(f"{base}/healthz answered {status}: is the server running?", file=sys.stderr)
        return 1

    chosen = args.scenario or list(SCENARIOS)
    print(f"Target {base}{ctx.prefix}  scenarios: {', '.join(chosen)}  rounds: {max(1, args.rounds)}")
    for rnd in range(max(1, args.rounds)):
        for name in chosen:
            if name in NEEDS_ADMIN and not (ctx.admin_user and ctx.admin_password):
                print(f"  {name:15} skipped: set GEN_ADMIN_USER and GEN_ADMIN_PASSWORD")
                continue
            try:
                result = RUNNERS[name](ctx)
            except (urllib.error.URLError, OSError) as err:
                result = f"could not reach the server ({type(err).__name__})"
            print(f"  {name:15} {result}")
            time.sleep(max(0.0, args.pause))
    print("Responses: " + ", ".join(f"{code}×{n}" for code, n in sorted(tally.items())))
    print("In the SOC console: Events and Alerts (sign-in failures, WAF, honeypot), Detections, Traffic,\n"
          "Ban review, Response -> Signed in. The sources are 192.0.2.x / 198.51.100.x / 203.0.113.x; if the\n"
          "console shows nothing from them, start the server with TRUSTED_PROXY_HOPS=1 (without it every request\n"
          "comes from 127.0.0.1, which the console leaves out as the server's own traffic).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
