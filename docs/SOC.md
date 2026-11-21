# SOC console (`/socperson`)

A standalone security-operations console for the platform operator. It reads what the
product already records about attacks and abuse, lets an analyst investigate and respond,
and analyses raw logs. It is **not** linked from any other dashboard: you reach it by
typing `https://<host>/socperson`.

## Genuine activity only

Every number, chart and list shows only what HRZEST itself recorded about real activity.
Nothing is sample data, and no address is looked up at an outside service. Three kinds of
recorded event are left out everywhere at once (`utils/soc_scope.py`):

* **The server's own traffic**: events whose address is loopback, private, CGNAT or
  link-local (the app itself, its proxy or container gateway, a LAN).
* **The console's own use**: analysts signing in and acting here, and any console event from an
  address that has signed in. Strangers probing `/socperson` stay visible: that is an attack.
  The console's own record of itself is **Users & audit**.
* **Your own testing**: addresses, accounts and companies listed in `SOC_TEST_CIDRS`,
  `SOC_TEST_ACCOUNTS` and `SOC_TEST_COMPANIES`.

The Overview says how many events each rule left out. If most recorded events come from a
private address, it also warns that the app is seeing its proxy instead of real visitors
(fix `TRUSTED_PROXY_HOPS`). Not filtered: the audit-chain check (it must see every row), bans
and cases (enforcement and decisions, not activity), and the Loki view (raw shipped logs).

## Daily summary email

Every morning (08:00 Asia/Kolkata by default) everyone in `SOC_LOGIN_EMAILS` gets one email
covering the last 24 hours (`utils/soc_digest.py`):
* the threat level and the Overview numbers, with total events against the day before,
* which detection rules fired and how often, and the busiest source addresses,
* what is waiting in **Ban review** and for how long (nothing is banned until someone decides),
* open cases, and how many events the genuine-activity rules left out.

It is sent on quiet days too, so a summary that stops arriving is itself a warning. Account
names and event details are left out: email is outside the console's network allow-list and
sign-in. It needs working SMTP; with `APP_URL` set it links to the console. Disabled accounts
are skipped. `SOC_DIGEST=off` turns it off; `SOC_DIGEST_HOUR` moves it.

## Getting in

There are no default accounts. Sign-in is an emailed one-time code followed by your password,
which you set yourself the first time, and only the addresses you list on the server can get a code.

**Before going live (and after any change to the server's `.env`)**, run on that server:

```
python scripts/soc_preflight.py        # add --db if email is set in the app's settings, not .env
```

It reports every setting the console depends on as PASS / WARN / FAIL with the fix (the app
starting at all, who can sign in, the network allow-list, email, alerts, the
proxy setting, the external dashboard), using the app's own rules, and never prints a secret or
an address. Exit code 1 means something would lock you out: fix it before approving the release.

1. In the server's `.env`, list who may sign in and allow your networks, then restart:

   ```
   SOC_LOGIN_EMAILS=you@example.com
   SOC_ALLOWED_CIDRS=203.0.113.0/24
   ```

   Each entry may name a role: `you@example.com, ana@example.com:analyst, ops@example.com:viewer`.
   No role means `admin`. Keep the addresses in `.env`, not in the repository.
2. Open `/socperson/login`, enter your address, and enter the 6-digit code from the email.
   Codes last 10 minutes, work once, and at most one is sent per minute ("Send a new code").
3. Then enter your password. **The first time there is none yet: you choose one right there**
   (12 characters or more, typed twice) and you are in. The console stays closed until this
   step is done, and a wrong password counts toward the same lockout as a wrong code (5 in a
   row locks the account for 15 minutes).
4. The account is created on your first sign-in; its role always follows `SOC_LOGIN_EMAILS`.

Any address gets the same "check your email" screen, so the form does not reveal who is
authorised; addresses that are not listed simply never receive a code. Email must work:
without SMTP the code is sent nowhere (with `APP_ENV=development` only, it is written to the
application log so the flow can be tried locally).

Passwords are stored only as Argon2id hashes (`utils/auth.py`). The step after the code can only
*set* a password when the account has none, never replace one, so someone who gets into a listed
inbox still cannot get past a password that is already set. The flip side: whoever completes the
first sign-in after the email code chooses the password, so sign in yourself soon after listing
an address (and soon after a reset).

**Forgot your password:** another admin uses **Users & audit → Reset password**, which also ends
every open session of that person; you set a new password at your next sign-in, after the code.
**Sole admin:** on the server, `python scripts/soc_reset_password.py you@example.com` does the same
(and clears a lockout). **Left signed in somewhere?** **Sign out everywhere** (bottom of the
sidebar) ends your sessions in every browser.

Accounts from the earlier username + password + authenticator login keep their role and history,
but their old password and authenticator are voided on upgrade: you set a new password at your
first sign-in.

To remove someone, take their address out of `SOC_LOGIN_EMAILS` and restart: their open session
ends on its next request. **Users & audit → Disable** is the immediate switch that needs no restart.

## Roles

| | viewer | analyst | admin |
|---|:-:|:-:|:-:|
| Overview, events, alerts, traffic, health, lookups, cases (read), tenants, posture, detections | ✔ | ✔ | ✔ |
| Open / update cases, add notes | | ✔ | ✔ |
| Ban / unban IPs, decide Ban review items, revoke sessions | | ✔ | ✔ |
| Log analysis, CSV export, audit-chain verification, Loki search | | ✔ | ✔ |
| Disable / enable / unlock SOC accounts, reset passwords, read the SOC audit trail | | | ✔ |

Viewers never receive raw session identifiers. A role change in `SOC_LOGIN_EMAILS`, removal
from it, deactivation, a password reset or "Sign out everywhere" ends that user's console sessions
on their next request.

## Settings (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `SOC_LOGIN_EMAILS` | unset | Who may sign in: comma-separated addresses, each optionally `:viewer`, `:analyst` or `:admin` (default `admin`). Unset means nobody can sign in. |
| `SOC_ALLOWED_CIDRS` | unset | Comma-separated CIDRs allowed to reach `/socperson`. Others get a plain 404. A malformed value denies everyone. **Unset, the console is closed everywhere except `APP_ENV=development`**; `0.0.0.0/0,::/0` opens it to every address on purpose. |
| `SOC_IDLE_MINUTES` | 15 | Sign-out after this long without a user action (background refresh does not count). |
| `SOC_SESSION_HOURS` | 8 | Absolute session lifetime. |
| `LOKI_URL` | unset | Enables the **Logs** view (read-only LogQL). Read from the environment only, never from a request. |
| `SOC_LOG_MAX_MB` | 5 | Largest log the analyser accepts. |
| `SOC_TEST_CIDRS` | unset | Addresses / ranges you test from (office, VPN, CI); their events are left out of every view. |
| `SOC_TEST_ACCOUNTS` | unset | Identifiers you test with (usernames, emails, employee IDs); left out of every view, including attendance punches and risky sessions. |
| `SOC_TEST_COMPANIES` | unset | Company URL slugs used only for testing; left out of every view, including Tenant activity, Attendance fraud and sessions. |
| `SOC_DIGEST` | on | `off` stops the daily summary email. |
| `SOC_DIGEST_HOUR` | 8 | Hour (0–23, Asia/Kolkata) the daily summary is sent. |
| `HTTP_TELEMETRY` | on | `off` stops counting requests for the **Traffic** view and the `http_error_spike` detection. |
| `HTTP_TELEMETRY_RETENTION_DAYS` | 14 | How long per-minute request counts are kept (purged nightly at 03:40). |
| `HEALTH_RETENTION_DAYS` | 30 | How long the per-minute server / database readings for the **Health** view are kept (purged nightly at 03:50). |
| `SOC_AUTO_BAN` | false | `true` bans automated detections at once (the old behaviour) instead of queueing them in **Ban review**. Leave off when an analyst watches the queue: customers often share one office or mobile-network address. |

## What each view shows

Every number is a count, time or state as recorded; where the console draws a conclusion (the threat level, a log-analysis score) it shows how. An event counts for the address it is about: usually whoever made the request, but a TCP-decoy hit, a ban review and a detection are about the address the honeypot, the WAF or the rule named, not the honeypot container or scheduler that wrote them.

* **Live feed** – while a console tab is open and visible, the browser keeps a Server-Sent Events connection to `/socperson/api/stream` (`utils/live_feed.py`). Each answer is short and ends with `retry: 5000`, so the browser reconnects every five seconds and sends the last event id it saw: new events, the header status and the *Right now* numbers arrive within seconds, and no worker thread is ever held open. It does not count as activity for the idle sign-out, stops while the tab is hidden, and falls back to polling every 30 seconds where EventSource is unavailable. The header shows *Live* while it is connected.
* **Overview** – a *Right now* strip updated by the live feed (failed sign-ins and alerts in the last hour and 24 hours, 401 / 403 answers in the last hour from the request telemetry, IPs banned now, open cases), then KPIs, events over time by severity, the busiest sources and accounts, the latest alerts and the MITRE ATT&CK techniques observed. The *threat level* is rule-based and lists its reasons.
* **Alerts / Events** – as soon as the live feed brings events that match the filters, the list says how many arrived and offers to show them (it never reshuffles under you); search and filter `security_events` (text, level, area, IP, account, tenant); open any event for its fields, related activity from the same source/account, its audit-chain hash and ATT&CK mapping; open a case or ban the source from there.
* **Traffic** – every request the app answered, counted per minute by route pattern (`/view_payslip/<emp_id>/...`, never the raw URL), method, status and company (`utils/http_telemetry.py`): requests and 4xx / 5xx over time, the 95th-percentile response time, counts per status code, and per route the request count, 50th / 95th / 99th percentile and slowest time and its 401 / 403 / 404 / 429 / 5xx answers. Response times come from a histogram, so they are shown as upper bounds ("≤ 250 ms"). Each worker writes its counts once a minute: with nothing written yet the view says *No telemetry recorded yet*, and if no worker has written for 3 minutes it says *Source offline* with the time of the last write. This console's own requests, static files and health checks are left out.
* **Health** – readings taken once a minute (`utils/health_telemetry.py`) from three sources: the **server** (CPU busy %, load average, memory and swap, uptime, disk use of the root and app filesystems), **PostgreSQL** (round-trip time of `SELECT 1`, connections against `max_connections`, active and idle-in-transaction sessions, the longest running query and open transaction, waiting locks, database size, commits / rollbacks per minute, deadlocks and cache-hit ratio) and the **app service** (how long the service and the snapshot's worker have been up, worker memory and threads), with trends over the window. CPU, memory, load and uptime need Linux `/proc`; on another OS the view says which readings could not be taken. Each source shows *No telemetry recorded yet* before its first reading, *Source offline since …* when its newest reading is over 3 minutes old, and the kind of failure (never its message) when a reading failed. Session states in PostgreSQL are only complete when the app's database role can read all statistics (`pg_read_all_stats`); the view says when it cannot.
* **Lookup** – everything recorded about an IP or an account: what it did, who/what it touched, paths requested, recent events, and whether it is banned (on the platform list or a company's own list, lifted from the list it is on).
* **Log analysis** – drop in an nginx/Apache access log, JSON lines (including this app's own) or sshd/auth log. Finds SQL injection, XSS, path traversal, command injection, SSRF, scanner user agents, sensitive-file probes, login brute force, credential stuffing, "possible success after many attempts", path enumeration, request floods and 5xx spikes; scores each source (points per kind of finding, shown on hover) and recommends block/watch; exports IOCs and can ban every recommended public address in one step. Nothing is stored.
* **Logs (Loki)** – optional LogQL search of shipped logs.
* **Cases** – open / investigating / contained / resolved / false positive, assignee, notes timeline.
* **Ban review** – addresses the WAF (repeated attack payloads), the rate limiter (repeated 429s) and the HTTP/TCP honeypots want banned. Nothing is banned automatically: each address waits here with its source, latest reason, hit count and a suggested duration, and an analyst chooses **Ban** (for a chosen time, through the same path and guards as a manual ban), **Dismiss** (false positive; the next detection re-opens it) or **Trust** (known-good address such as a customer office or an authorised scanner; further detections only add to its hit count until the trust ends). The offending requests themselves are still rejected. Every decision is in the SOC audit trail. `SOC_AUTO_BAN=true` restores immediate bans, still listed here.
* **Response** – current IP bans (add, lift) and risky sessions across tenants (revoke). A ban on one of your own `SOC_TEST_CIDRS` addresses is listed (so it can be lifted) and marked *your testing*, but not counted among active bans here or on the Overview. Your test accounts never take a place in the sessions list, and the page says when it shows only the newest 200.
* **Tenant activity**, **Attendance fraud** – per-company counts as recorded (events, alerts, unacknowledged duplicate-signup alerts; most alerts first) and punch-integrity signals (mock location, replayed photos, shared devices, liveness failures), counted over every punch in the window, with punches by your `SOC_TEST_ACCOUNTS` employee IDs or from `SOC_TEST_CIDRS` left out (the page says how many). No weighted risk score. Every company is read: there is no cap on how many.
* **Detections** – the correlation rules, their thresholds, and when each last fired and how often in the last 90 days, counted from the same genuine detection events the view lists.
* **Audit integrity** – verifies the hash chain over `security_events` on demand. The verifier streams the table, so memory stays flat at any size; a button press stops after 45 seconds on a very long chain, and the scheduled check (03:45 nightly) verifies all of it.
* **Posture** – live configuration checks with the setting to change. Reports whether a control is on, never a secret.
* **Users & audit** (admin) – accounts (whether each is still listed in `SOC_LOGIN_EMAILS`, disable / enable, unlock) and the append-only record of everything done in the console.

## How it is protected

* Separate accounts, session keys and guard from every other login; other sessions grant nothing here and a console session grants nothing elsewhere.
* Emailed one-time code for listed addresses only; the code is stored as an HMAC keyed with the app secret, expires in 10 minutes and is cleared by the statement that accepts it; lockout after 5 wrong codes for 15 minutes; listed and unlisted addresses see the same screens; session re-validated against the database and `SOC_LOGIN_EMAILS` on every request. With no password or authenticator, whoever controls a listed inbox can sign in, which is why the network allow-list is enforced in production.
* Unreachable at `/<company>/socperson`; `socperson` (and the old `soc`) are reserved URL segments so no company can claim them. The old `/soc` path answers 404.
* Every response is `no-store` and `noindex`; its JS/CSS are served only to a signed-in session.
* Every query is parameterised and runs read-only with a statement timeout. Event data is rendered as text only (no `innerHTML`, no inline script), enforced by `tests/test_soc_frontend_static.py`.
* Authenticated analysts are exempt from the WAF only on `/socperson/api/` so they can search for attack strings; the login page stays inspected. Pasted logs are uploaded as a file for the same reason.
* Every login, failure and action goes to `att_master.soc_audit_log` (append-only by trigger) and is mirrored into the normal security stream so it ships off-box with everything else.

## Limits to know about

* Each event records the company its request ran as (`security_events.tenant`, also inside the hash-chained `extra_json`). Events from before that was recorded fall back to the first segment of the request path, which usually does not name the company (the company prefix is taken out of the path before the app sees it), so older company activity is mostly unattributed. Platform pages belong to no company.
* There is no GeoIP or threat-intelligence enrichment, on purpose: the console shows only what HRZEST recorded. Location, VPN and hosting checks have to be done outside it.
* The append-only trigger stops the application role from rewriting the SOC audit trail, not a database owner; forward it off-box for that (see `SECURITY_HARDENING.md`).
* Cross-tenant views read each tenant in turn (cached 45 s, 300 tenants max, short per-tenant timeout); a tenant that errors is skipped and counted.
* The Loki view has been tested against a stand-in, not a live Loki.
* It complements, and does not replace, a SIEM: it sees what this application emits, not your hosts or network.
