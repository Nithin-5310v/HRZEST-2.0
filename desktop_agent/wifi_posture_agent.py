"""Windows Wi-Fi/network posture agent for HRzest.com.

WHY THIS EXISTS: a browser page cannot read Wi-Fi encryption type, ARP
tables, or DNS configuration — those aren't exposed to page JavaScript by
any browser, on any OS, ever (it's a sandbox boundary, not a missing
library). The only place that data is genuinely available is the OS itself.
This script runs as a small background service ON THE EMPLOYEE'S MACHINE,
reads real OS-level signals via netsh/arp/ipconfig, computes a 0-100 risk
score, and serves it on the loopback interface ONLY (127.0.0.1 — never the
network) at GET /risk. The employee portal page
(templates/employee_portal.html's "DEVICE POSTURE RELAY" script) polls that
URL from the browser tab, using ITS OWN authenticated session, and relays
the score to POST /api/employee/device_risk (blueprints/employee_portal.py)
— which is what actually blocks the UI and terminates the session once the
score exceeds 60. This agent has no credentials, sees no session cookie,
and cannot itself talk to the backend — it only ever answers a localhost
query with a number and a list of threat-vector labels.

Platform: Windows only (netsh/arp/ipconfig are Windows commands). There is
no equivalent for a plain web page on macOS/Linux either, for the same
sandboxing reason — the same "small local agent" pattern would need
platform-specific system calls (e.g. `iwconfig`/`nmcli` + `ip neigh` on
Linux, `airport`/`arp` on macOS) if this is ever extended past Windows.
Mobile is a separate, weaker story: iOS exposes essentially none of this to
any app, sandboxed or not. Android exposes SSID/BSSID/encryption type to a
native app via WifiManager (with location permission, since SSID counts as
location data) but not ARP tables — a React Native module could report
encryption weakness, not spoofing, on Android; nothing meaningful on iOS.

Deploy as a scheduled task at logon (run once, not as a click-to-launch
app — a security agent an employee can just close defeats the point):
    schtasks /Create /TN "AttendancePostureAgent" /TR "pythonw.exe C:\\path\\to\\wifi_posture_agent.py" /SC ONLOGON /RL LIMITED
"""
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT_HOST = "127.0.0.1"   # loopback ONLY — binding 0.0.0.0 would let any
# other host on the network query (or spoof)
# this employee's device-risk data. Never widen
# this without re-deriving the whole trust model.
AGENT_PORT = 47823          # matches AGENT_URL in employee_portal.html
ALLOWED_ORIGIN = os.environ.get("PORTAL_ORIGIN", "https://hrzest.com")
# A DNS-rebinding page reaches this server under its own hostname; refusing
# any Host other than the loopback address stops it reading the answer.
_ALLOWED_HOSTS = {f"127.0.0.1:{AGENT_PORT}", f"localhost:{AGENT_PORT}"}
CACHE_TTL_SEC = 10           # avoid re-shelling out on every rapid poll/retry
SUBPROCESS_TIMEOUT = 5

TRUSTED_DNS = {
    "192.168.1.1", "192.168.0.1", "192.168.137.1",  # common home/office router-as-resolver
    "1.1.1.1", "1.0.0.1",       # Cloudflare
    "8.8.8.8", "8.8.4.4",       # Google
    "9.9.9.9",                  # Quad9
}
_extra_dns = os.environ.get("TRUSTED_DNS_EXTRA", "")
if _extra_dns:
    TRUSTED_DNS |= {ip.strip() for ip in _extra_dns.split(",") if ip.strip()}

_TRUST_STORE_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "AttendanceAgent", "trusted_gateways.json",
)

_cache_lock = threading.Lock()
_cache = {"ts": 0, "result": None}


def _run(cmd):
    """subprocess.run wrapper: never raises, never hangs. A parsing failure
    here must degrade to 'unknown', not crash the agent or block a poll."""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return out.stdout or ""
    except Exception:
        return ""


def _wifi_interface_info():
    """Returns (ssid, authentication) from `netsh wlan show interfaces`,
    or (None, None) if not connected to Wi-Fi (e.g. on wired ethernet —
    that's not a Wi-Fi risk, so callers must treat this as N/A, not bad)."""
    out = _run(["netsh", "wlan", "show", "interfaces"])
    ssid_m = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.MULTILINE)
    auth_m = re.search(r"^\s*Authentication\s*:\s*(.+)$", out, re.MULTILINE)
    if not ssid_m or "State" not in out or "connected" not in out.lower():
        return None, None
    ssid = ssid_m.group(1).strip()
    auth = auth_m.group(1).strip() if auth_m else ""
    return (ssid or None), (auth or None)


def _score_encryption(authentication):
    """Weights chosen so a single severe finding (open Wi-Fi, or a gateway
    MAC that just changed under a pinned SSID) alone crosses the 60
    kill-threshold, matching the spec's 'immediately block' intent, while
    a single mild finding (unrecognized DNS) alone does not."""
    if authentication is None:
        return 0, []
    a = authentication.lower()
    if "open" in a or a == "":
        return 65, ["weak_encryption:open"]        # alone crosses the 60 threshold
    if "wep" in a:
        return 55, ["weak_encryption:wep"]          # broken, but nominally encrypted — needs
        # one more mild signal to cross 60
    if "wpa2" in a or "wpa3" in a:
        return 0, []
    if "wpa" in a:  # WPA1/TKIP without WPA2/3 in the string
        return 20, ["weak_encryption:wpa_tkip"]
    return 15, ["weak_encryption:unrecognized(%s)" % authentication[:30]]


def _default_gateway_ipv4():
    out = _run(["ipconfig", "/all"])
    for block in out.split("adapter"):
        m = re.search(r"Default Gateway[ .]*:\s*([^\r\n]*)", block)
        if not m:
            continue
        # Value may be on this line, or the IPv4 address may be on the
        # next indented line after an IPv6 link-local address.
        candidates = [m.group(1).strip()]
        tail = block[m.end():]
        candidates += [ln.strip() for ln in tail.splitlines()[:2]]
        for c in candidates:
            c = c.split("%")[0].strip()
            try:
                ip = ipaddress.ip_address(c)
                if ip.version == 4 and not ip.is_unspecified:
                    return str(ip)
            except ValueError:
                continue
    return None


def _dns_servers_ipv4():
    out = _run(["ipconfig", "/all"])
    servers = []
    for block in out.split("adapter"):
        m = re.search(r"DNS Servers[ .]*:\s*([^\r\n]*)", block)
        if not m:
            continue
        tail_lines = [m.group(1).strip()] + [ln.strip() for ln in block[m.end():].splitlines()[:3]]
        for ln in tail_lines:
            ln = ln.split("%")[0].strip()
            try:
                ip = ipaddress.ip_address(ln)
                if ip.version == 4:
                    servers.append(str(ip))
            except ValueError:
                break  # first non-IP line ends this adapter's DNS block
    return servers


def _gateway_mac(gateway_ip):
    if not gateway_ip:
        return None
    out = _run(["arp", "-a", gateway_ip])
    m = re.search(r"([0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2}", out)
    return m.group(0).lower().replace("-", ":") if m else None


def _load_trust_store():
    try:
        with open(_TRUST_STORE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_trust_store(store):
    try:
        os.makedirs(os.path.dirname(_TRUST_STORE_PATH), exist_ok=True)
        with open(_TRUST_STORE_PATH, "w") as f:
            json.dump(store, f)
    except Exception:
        pass  # best-effort; a failed pin write degrades to "always trust on
        # first sight next time" rather than crashing the agent


def _score_gateway(ssid, gateway_ip, gateway_mac):
    """Trust-on-first-use pinning, same idea as SSH host-key pinning: the
    first time we see a given SSID+gateway-IP pair, we pin its MAC. If that
    exact IP later answers ARP with a DIFFERENT MAC under the same SSID,
    something on the network changed who's answering as the gateway —
    the actual signature of ARP spoofing / rogue-AP gateway impersonation."""
    if not ssid or not gateway_ip or not gateway_mac:
        return 0, []
    store = _load_trust_store()
    key = f"{ssid}|{gateway_ip}"
    pinned = store.get(key)
    if pinned is None:
        store[key] = gateway_mac
        _save_trust_store(store)
        return 0, []
    if pinned != gateway_mac:
        return 65, ["arp_gateway_mac_changed"]   # alone crosses 60 —
        # this is the actual
        # spoofing signature
    return 0, []


def _score_dns(dns_servers):
    if not dns_servers:
        return 0, []
    if any(ip in TRUSTED_DNS for ip in dns_servers):
        return 0, []
    return 20, [f"untrusted_dns:{','.join(dns_servers[:3])}"]


def compute_posture():
    with _cache_lock:
        if _cache["result"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL_SEC:
            return _cache["result"]

    ssid, auth = _wifi_interface_info()
    if ssid is None:
        # Not on Wi-Fi (wired, or no interface) — no Wi-Fi risk to report.
        result = {"risk_score": 0, "threat_vectors": []}
    else:
        gateway_ip = _default_gateway_ipv4()
        gateway_mac = _gateway_mac(gateway_ip)
        dns_servers = _dns_servers_ipv4()

        score = 0
        vectors = []
        for s, v in (
            _score_encryption(auth),
            _score_gateway(ssid, gateway_ip, gateway_mac),
            _score_dns(dns_servers),
        ):
            score += s
            vectors += v
        result = {"risk_score": max(0, min(score, 100)), "threat_vectors": vectors}

    with _cache_lock:
        _cache["ts"] = time.time()
        _cache["result"] = result
    return result


class _RiskHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # don't spam a console window with every 30s poll

    def _refuse(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _caller_allowed(self):
        """Loopback Host only (blocks DNS rebinding), and a browser caller
        must be the portal itself (Origin), not any open tab."""
        if (self.headers.get("Host") or "").strip().lower() not in _ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == ALLOWED_ORIGIN

    def do_OPTIONS(self):
        # Chrome's Private Network Access preflight for a public page calling loopback.
        if self.path != "/risk" or not self._caller_allowed():
            self._refuse(403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path != "/risk":
            self._refuse(404)
            return
        if not self._caller_allowed():
            self._refuse(403)
            return
        body = json.dumps(compute_posture()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Scoped to the portal's own origin, not "*" — this endpoint must
        # not answer CORS fetches from an arbitrary page some other open
        # tab happens to be showing.
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((AGENT_HOST, AGENT_PORT), _RiskHandler)
    print(f"Wi-Fi posture agent listening on http://{AGENT_HOST}:{AGENT_PORT}/risk (loopback only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
