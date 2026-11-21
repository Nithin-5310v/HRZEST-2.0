# Firewall layers

Every request to HRZEST passes up to five firewalls. Each one records what it refused, and all
of them report to one place: the SOC console's **Firewall** view (`/socperson`, Monitor →
Firewall). A ban made in the console reaches every layer that can enforce it within seconds.

```
 internet
    │
    ▼
 1  AWS edge        ALB + WAFv2: IP reputation, OWASP/SQLi/Linux/bad-input rules,      (optional, Terraform:
    (infra/terraform/edge)  per-IP rate limits, ban list, geo                        not deployed yet)
    │                VPC security groups (+ flow logs)
    ▼
 2  host firewall   ufw default-deny, [FW-DROP] logging, nftables ban list, sysctl     scripts/harden_host_firewall.sh
    │
    ▼
 3  nginx           TLS, methods, size/time caps, rate limits, ModSecurity + OWASP CRS  scripts/host-hardening/install_nginx_waf.sh
    │  fail2ban     bans repeat offenders at layer 2                                   scripts/host-hardening/install_fail2ban_web.sh
    ▼
 4  the app         IP + range bans, WAF signatures, header/scanner checks,            app_setup/security.py, utils/waf.py
                    account lockout, rate limits, ban review

 firewall agent (host service) ── reports 1-3 ──▶ app ──▶ SOC console ── ban list ──▶ agent ──▶ 1, 2
                                                   (4 records its own)
```

What runs where today: the EC2 host (Ubuntu 24.04) serves the site directly; layers 2-4 and the
agent are what this repository installs on it. Layer 1 is new infrastructure in front of the host;
the Terraform is ready but has not been applied (see [The AWS edge](#1-the-aws-edge)).

## How a ban travels

1. Something decides an address should be banned: an analyst (**Response → IP bans**, or **Ban
   review**), or, with `SOC_AUTO_BAN=true`, a detection.
2. It is written to `public.banned_ips` (one address) or `public.banned_cidrs` (a range, at most a
   /16 or IPv6 /32, never covering a private or never-ban range or the analyst's own address).
3. **The app** refuses that source on its next request (`_enforce_ip_ban`).
4. **The firewall agent** fetches the list every 15 s (`GET /webhooks/firewall/blocklist`, signed) and
   - drops the source at **the host firewall** (nftables sets `inet hrzest_fw block4/block6`), and
   - when the edge exists, puts it in **the AWS WAF block list**, so it is refused before it costs
     a TLS handshake.
5. Lifting the ban in the console removes it everywhere the same way.

The agent never blocks a live SSH session's address or anything in `HRZEST_FW_NEVER_BLOCK`, so a
mistaken ban can't lock an administrator out of the host. "Trust" in Ban review (a customer office)
is copied to the edge's trusted list, which exempts the address from the edge's rate limits and
geo rule. Trusted addresses still go through every attack rule.

Detections in the other direction: fail2ban bans (layer 3) are reported to the console and wait in
**Ban review** as source *fail2ban (host)*, where an analyst can make a short host-level ban
platform-wide, or trust the address. Nothing becomes a platform-wide ban without a person, unless
`SOC_AUTO_BAN=true`.

## The SOC console's Firewall view

Monitor → **Firewall** shows, for the last 1 h / 24 h / 7 d / 30 d:

* per layer: requests or packets blocked (and, at the edge, allowed: from the WAF's own counter);
* blocked over time, by layer; the most-blocked sources and the rules that fired most;
* **Reporters**: when each reporter (the app, each host's agent) last reported and the state of
  each layer it watches. A layer that stops reporting is shown as *not reporting* after 10 minutes,
  never as a reassuring zero;
* recent decisions, filterable by layer, decision and address. Each opens with what was recorded
  (rule, path, port, country as the edge saw it) and a one-click ban.

The view follows the console's genuine-activity rules: decisions about the server's own or private
addresses and your `SOC_TEST_CIDRS` are left out and counted separately.

What is stored (`att_master.firewall_events`, 90 days by default): one row per decision, or per
repeated decision within a few seconds (`hits` carries the count). Never request headers, cookies or
bodies. Attacker-supplied text (paths, rule messages) is stored as text and shown as text only.

**Allowed traffic is counted only at the edge**, from the WAF's metrics. Recording every accepted
packet or request in the app's database would be the wrong tool: those stay in nginx's access log
(`/var/log/nginx/hrzest-access.json`) and in CloudWatch (VPC flow logs with `ALL`).

## Rollout

Order matters: each step works on its own, and nothing here needs the AWS edge.

1. **App.** Merge and deploy as usual (develop → main → Jenkins approval). New tables are created at
   start-up. Then, in the server's `.env`:
   `FIREWALL_AGENT_SECRET=<48 random characters>` (the installer in step 5 can generate and add it),
   and restart `hrzest`.
2. **Host firewall** (as root on the host, from the checkout):
   ```bash
   sudo bash scripts/harden_host_firewall.sh --dry-run     # read it
   sudo bash scripts/harden_host_firewall.sh
   ```
   Keeps HTTP/HTTPS open and SSH reachable (rate-limited; the security group still decides who).
   `--ssh-from <your /32>` restricts SSH here too; `--no-ssh --i-am-on-ssm` closes it once you use
   SSM Session Manager. It refuses any option that would cut off the SSH session it runs in.
3. **nginx + ModSecurity**:
   ```bash
   sudo bash scripts/host-hardening/install_nginx_waf.sh --domain hrzest.com --dry-run
   sudo bash scripts/host-hardening/install_nginx_waf.sh --domain hrzest.com
   ```
   This **replaces `/etc/nginx/sites-available/hrzest`** (the file was never in the repository; the
   installer backs it up and restores it if `nginx -t` fails) with
   `scripts/host-hardening/nginx/hrzest-site.conf`, which includes the www redirect (OBS-09). Check
   the old file for anything the new one lacks before running it, or use `--no-site` to keep your
   file and add the printed lines to it yourself.
   ModSecurity starts **blocking**. If a legitimate request is refused:
   `--engine DetectionOnly` (logs, blocks nothing) at once, then add a narrow exclusion
   (`/etc/nginx/modsec/local-before-crs.conf` now; `hrzest-before-crs.conf` in the repository for good)
   and switch back.
4. **fail2ban web jails**: `sudo bash scripts/host-hardening/install_fail2ban_web.sh --ignore <office CIDRs>`.
5. **Firewall agent**:
   ```bash
   sudo bash scripts/firewall_agent/install_firewall_agent.sh --domain hrzest.com --app-env /home/ubuntu/apps/HRZEST/.env
   sudo systemctl restart hrzest     # if it added FIREWALL_AGENT_SECRET
   ```
   It runs `hrzest_fw_agent.py check` at the end. The Firewall view's Reporters table should show
   the host as *reporting* within a minute.
6. **The AWS edge**, when you decide to: [infra/terraform/edge/README.md](../infra/terraform/edge/README.md).

Rollback, per layer: `ufw disable` (or restore `/etc/ufw/*.hrzest-fw.bak`); the nginx installer's
backups are in `/etc/nginx/hrzest-backups/<time>/`; `rm /etc/fail2ban/jail.d/hrzest-web.local && systemctl reload fail2ban`;
`systemctl disable --now hrzest-fw-agent && nft delete table inet hrzest_fw`.

## The layers

### 1. The AWS edge

`infra/terraform/edge/`: an Application Load Balancer with an ACM certificate, a WAFv2 web ACL,
least-privilege security groups (load balancer → app only; app reachable only from the load balancer;
an optional RDS group), VPC flow logs and WAF logs to CloudWatch, and the instance role (SSM Session
Manager + exactly what the agent needs). Rules, in order: ban list; trusted label; per-IP rate limits
on sign-in (300 / 5 min), payroll (600 / 5 min) and everything (6000 / 5 min); optional geo; AWS
managed IP reputation, Common (OWASP Top 10), Known bad inputs, SQLi and Linux rule groups.

It is **not deployed**. It changes how traffic reaches production (DNS moves to the load balancer)
and adds monthly cost, so applying it is a decision, with a runbook. Notably the apex `hrzest.com`
can't point at a load balancer from Hostinger DNS (no alias records): DNS has to move to Route 53,
which also solves DNSSEC (OBS-03).

### 2. Host firewall (`scripts/harden_host_firewall.sh`)

ufw with deny-incoming / deny-routed defaults; HTTP, HTTPS and SSH as configured
(`--mode edge --web-from <VPC CIDR>` once the load balancer is in front). Optional restricted egress
(DNS, NTP, DHCP, HTTP(S), mail submission only) blocks reverse shells and exfiltration on other ports.
Every dropped packet is logged, rate-limited, as `[FW-DROP]` (and `-FWD`, `-OUT`, `-BL`, `-DOCKER`)
to `/var/log/ufw.log`. An nftables table holds the ban list the agent fills. Kernel hardening:
SYN cookies, reverse-path filtering, no ICMP redirects or source routing, martian logging.

Docker is not used on this host. If it is ever installed, the script adds a `DOCKER-USER` guard:
Docker's NAT rules otherwise publish container ports around ufw.

`scripts/host_hardening.sh` is the different script for the rootless-Podman container stack
(it adds 80→8080 redirects that would break this bare-metal host). `harden_host_firewall.sh` refuses
to run where that script's redirects are present.

### 3. nginx, ModSecurity, fail2ban

`install_nginx_waf.sh` installs Ubuntu's ModSecurity v3 module, OWASP CRS **4.30.0** (pinned by
SHA-256, checked against the CRS signing key when pinned), and:

* TLS 1.2/1.3 with 256-bit AEAD suites only; HSTS on nginx's own responses (the app sends it on its
  own; nothing is sent twice); OCSP stapling when the certificate supports it (Let's Encrypt
  certificates no longer carry an OCSP responder, so with them there is nothing to staple);
* methods `GET HEAD POST PUT PATCH DELETE OPTIONS` only; 20 MB bodies; slow-client timeouts;
* per-IP rate limits on sign-in, payroll and everything (`hzwaf_*` zones);
* CRS at paranoia level 1, anomaly threshold 5, blocking. This app's exclusions
  (`modsecurity/hrzest-before-crs.conf`): passwords/PINs/OTPs never inspected; sign-in identifiers on
  sign-in routes (the app answers injection-shaped usernames like wrong passwords); the signed session
  cookie; passkey payloads; check-in and profile photos sent as `data:` URIs; the SOC console's API
  (detection only). XML request bodies are refused: nothing in the app reads XML;
* a JSON audit log of refusals (`/var/log/nginx/modsec/audit.json`): address, time, method, URI and
  every rule that matched, never headers or bodies (ModSecurity v3 cannot redact Cookie or
  Authorization, so the headers part is not written at all).

Why CRS 4.30.0 rather than the 4.25.x LTS line: 4.25.2 uses a rule syntax (`XML://@*`) that Ubuntu
24.04's libmodsecurity 3.0.12 cannot load. Load any newer CRS with `nginx -t` first (the installer
does, and rolls back if it fails).

fail2ban jails: `hrzest-modsec` (5 ModSecurity refusals in 10 min → 1 h, growing for repeat
offenders), `hrzest-nginx-limit` (120 rate-limit refusals in 10 min → 15 min), `hrzest-nginx-auth`
(30 failed API sign-ins in 10 min → 1 h). Thresholds are high because customers share office and
mobile-network addresses. Behind the AWS edge a host-level ban does not stop web traffic (it arrives
from the load balancer), so there the Ban review step is what makes it effective.

### 4. The app

Already in place before this change: IP bans, the signature WAF (`utils/waf.py`), account lockout,
Flask-Limiter, the honeypot, detections and Ban review. Added here:

* **range bans** (`public.banned_cidrs`) enforced per request, and manageable in the console;
* **header inspection**: JNDI/Log4Shell lookups in any header, injection shapes in `User-Agent` and
  `Referer`, and attack tools that name themselves in `User-Agent` (sqlmap, nikto, nuclei, …;
  `WAF_BLOCK_SCANNER_UA=false` turns that one off). Decoy paths are left to the honeypot;
* every refusal (ban, WAF, scanner, rate limit) recorded as an `app` firewall event, aggregated per
  worker before one insert so a flood doesn't become database load;
* the signed agent channel and the Firewall view.

## Settings

App (`.env`):

| Variable | Default | Purpose |
|---|---|---|
| `FIREWALL_AGENT_SECRET` | unset | Shared with the agent (32+ characters). Unset: both agent endpoints answer 404. |
| `FIREWALL_EVENT_RETENTION_DAYS` | 90 | How long firewall decisions are kept. |
| `FIREWALL_REPORTER_STALE_MINUTES` | 10 | When a silent reporter is shown as not reporting. |
| `FIREWALL_FAIL2BAN_REVIEW_MINUTES` | 60 | Suggested ban length when a fail2ban ban is raised for review. |
| `WAF_BLOCK_SCANNER_UA` | true | Refuse requests from tools that name themselves in User-Agent. |

Agent (`/etc/hrzest/firewall-agent.env`): see
[firewall-agent.env.example](../scripts/firewall_agent/firewall-agent.env.example).

## Limits to know about

* **Tested in containers, not on the server.** Every host script and config was run on Ubuntu 24.04
  in containers: the nginx/ModSecurity layer against legitimate and hostile requests, fail2ban
  producing real nftables bans, ufw converging between modes and dropping real packets, the agent
  carrying each layer's real log lines to a stand-in app that checks signatures with the app's own
  code. The edge was checked with `terraform validate` only: AWS managed rules can't be run locally,
  which is why the runbook starts them in count mode.
* **CRS false positives** depend on real traffic. The exclusions cover every request shape found in
  the code (credentials, photos, passkeys, signed cookies, CSP reports, the SOC console). Watch the
  Firewall view's Proxy WAF layer for the first days; `--engine DetectionOnly` is the fast way back.
* **A ban at the host stops web traffic only in direct mode.** Behind the load balancer the edge's
  block list does that; the host ban list then protects SSH and anything else reaching the host directly.
* The agent posts to the local nginx and so through ModSecurity; reports carry attacker text
  base64-encoded so they are not themselves refused.
* Company-level ban lists (older rows in a tenant's own `banned_ips`) stay app-only: the edge and
  host can't tell companies apart.
