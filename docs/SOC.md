# SOC console (`/soc`)

A standalone security-operations console for the platform operator. It reads what the
product already records about attacks and abuse, lets an analyst investigate and respond,
and analyses raw logs. It is **not** linked from any other dashboard: you reach it by
typing `https://<host>/soc`.

## Getting in

1. Create the first administrator (there are no default credentials):

   ```
   python scripts/seed_soc_user.py --username alice --role admin --email alice@example.com
   ```

   It prompts for a password (12+ characters) and prints a **one-time setup code**.
2. Open `/soc/login`, enter the username and password, then the setup code, then scan the QR
   code with an authenticator app and enter the 6-digit code. The setup code is single-use.
3. Every later sign-in is password + 6-digit code. Codes are single-use (a code that has
   already been accepted is refused for the rest of its 30-second window).

The setup code exists so someone who learns only a new account's password cannot enrol *their*
authenticator first. Hand over the password and the code through different channels. If a user
loses their phone, an admin uses **Users & audit → Reset MFA**, which issues a fresh setup code.

## Roles

| | viewer | analyst | admin |
|---|:-:|:-:|:-:|
| Overview, events, alerts, lookups, cases (read), tenants, posture, detections | ✔ | ✔ | ✔ |
| Open / update cases, add notes | | ✔ | ✔ |
| Ban / unban IPs, revoke sessions | | ✔ | ✔ |
| Log analysis, CSV export, audit-chain verification, Loki search | | ✔ | ✔ |
| Manage SOC users, read the SOC audit trail | | | ✔ |

Viewers never receive raw session identifiers. A role change, deactivation or password reset
ends that user's console session on their next request.

## Settings (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `SOC_ALLOWED_CIDRS` | unset | Comma-separated CIDRs allowed to reach `/soc`. Others get a plain 404. A malformed value denies everyone. **Set this in production.** |
| `SOC_IDLE_MINUTES` | 15 | Sign-out after this long without a user action (background refresh does not count). |
| `SOC_SESSION_HOURS` | 8 | Absolute session lifetime. |
| `LOKI_URL` | unset | Enables the **Logs** view (read-only LogQL). Read from the environment only, never from a request. |
| `SOC_LOG_MAX_MB` | 5 | Largest log the analyser accepts. |
| `SOC_VERIFY_MAX_ROWS` | 500000 | Above this many `security_events` rows, use `scripts/verify_audit_chain.py` instead of the button. |

## What each view shows

* **Overview** – KPIs, events over time by severity, the busiest sources and accounts, the latest alerts and the MITRE ATT&CK techniques observed. The *threat level* is rule-based and lists its reasons.
* **Alerts / Events** – search and filter `security_events` (text, level, area, IP, account, tenant); open any event for its fields, related activity from the same source/account, its audit-chain hash and ATT&CK mapping; open a case or ban the source from there.
* **Lookup** – everything recorded about an IP or an account: what it did, who/what it touched, paths requested, recent events.
* **Log analysis** – drop in an nginx/Apache access log, JSON lines (including this app's own) or sshd/auth log. Finds SQL injection, XSS, path traversal, command injection, SSRF, scanner user agents, sensitive-file probes, login brute force, credential stuffing, "possible success after many attempts", path enumeration, request floods and 5xx spikes; scores each source and recommends block/watch; exports IOCs and can ban every recommended public address in one step. Nothing is stored.
* **Logs (Loki)** – optional LogQL search of shipped logs.
* **Cases** – open / investigating / contained / resolved / false positive, assignee, notes timeline.
* **Response** – current IP bans (add, lift) and risky sessions across tenants (revoke).
* **Tenant risk**, **Attendance fraud** – per-company activity and punch-integrity signals (mock location, replayed photos, shared devices, liveness failures).
* **Detections** – the correlation rules, their thresholds and when each last fired.
* **Audit integrity** – verifies the hash chain over `security_events` on demand.
* **Posture** – live configuration checks with the setting to change. Reports whether a control is on, never a secret.
* **Users & audit** (admin) – accounts and the append-only record of everything done in the console.

## How it is protected

* Separate accounts, session keys and guard from every other login; other sessions grant nothing here and a console session grants nothing elsewhere.
* Password (12+ chars) **and** TOTP; lockout after 5 failures for 15 minutes; failures indistinguishable from unknown users; session re-validated against the database on every request.
* Unreachable at `/<company>/soc`; `soc` is a reserved URL segment so no company can claim it.
* Every response is `no-store` and `noindex`; its JS/CSS are served only to a signed-in session.
* Every query is parameterised and runs read-only with a statement timeout. Event data is rendered as text only (no `innerHTML`, no inline script), enforced by `tests/test_soc_frontend_static.py`.
* Authenticated analysts are exempt from the WAF only on `/soc/api/` so they can search for attack strings; the login page stays inspected. Pasted logs are uploaded as a file for the same reason.
* Every login, failure and action goes to `att_master.soc_audit_log` (append-only by trigger) and is mirrored into the normal security stream so it ships off-box with everything else.

## Limits to know about

* `security_events` has no tenant column; tenant views attribute events by the first segment of the request path, so events outside a tenant URL are not attributed.
* There is no GeoIP or threat-intelligence enrichment.
* The append-only trigger stops the application role from rewriting the SOC audit trail, not a database owner; forward it off-box for that (see `SECURITY_HARDENING.md`).
* Cross-tenant views read each tenant in turn (cached 45 s, 300 tenants max, short per-tenant timeout); a tenant that errors is skipped and counted.
* The Loki view has been tested against a stand-in, not a live Loki.
* It complements, and does not replace, a SIEM: it sees what this application emits, not your hosts or network.
