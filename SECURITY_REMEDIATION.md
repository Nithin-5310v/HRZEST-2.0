# Security review remediation — November 2026

This covers the external review of 18 Nov 2026 (OBS-01 to OBS-09, plus Section 9 on sessions and CSRF). For each finding it says what changed, gives the manual steps for anything outside the repo, and explains how to deploy, verify and roll back.

Branch: `fix/security-review-2026-11`, one commit per finding. To check the live state at any time, run `bash scripts/verify_security_fixes.sh`.

## Status checklist

| Finding | Severity | Status | Where |
|---|---|---|---|
| OBS-01 No cache-control on auth pages | Low | **Fixed in code** | `app_setup/cache_control.py` |
| OBS-02 No DMARC record | Medium | **Needs manual step**: DNS + Microsoft 365 | [Runbook](#obs-02-dmarc-and-dkim) |
| OBS-03 No DNSSEC | Low | **Needs manual step**: Hostinger | [Runbook](#obs-03-dnssec) |
| OBS-04 No CAA record | Low | **Needs manual step**: DNS | [Runbook](#obs-04-caa) |
| OBS-05 Public SSH, SHA-1 MACs | Medium | **Needs manual step**: files are ready in `scripts/host-hardening/` | [Runbook](#obs-05-ssh-security-group-fail2ban-updates) |
| OBS-06 No security.txt | Low | **Fixed in code**. Create the `security@hrzest.com` mailbox before deploying | `blueprints/security_txt.py` |
| OBS-07 robots.txt reveals /super_admin | Info | **Fixed in code**, and the route audit's gaps are closed | `blueprints/seo.py`, `blueprints/platform_admin/__init__.py` |
| OBS-08 Legacy X-XSS-Protection | Info | **Fixed in code** | `app_setup/csrf_csp.py` |
| OBS-09 www and apex both serve 200 | Info | **Fixed in code** (app redirect). **Manual step** for the nginx redirect | `utils/canonical_host.py`, [Runbook](#obs-09-nginx-www-redirect) |
| Section 9 Sessions and CSRF | — | **Fixed in code** | `utils/session_risk.py` |

## What we're running

- **App:** Flask on Python 3.12, run by gunicorn under systemd (`hrzest.service`, `127.0.0.1:8000`) from a git checkout at `/home/ubuntu/apps/HRZEST`.
- **Proxy:** Ubuntu's own nginx, with the site file `/etc/nginx/sites-available/hrzest`. That file is **not in this repo**. `nginx.conf`, `nginx.conf.example` and `nginx/nginx.conf.template` are templates the server doesn't use. Only `scripts/nginx-hardening.conf` (`server_tokens off`) is installed from here.
- **No CDN or load balancer:** both `hrzest.com` and `www.hrzest.com` point straight at 18.61.48.198. Cloudflare is used only for the Turnstile captcha.
- **TLS:** one Let's Encrypt certificate covering `hrzest.com` and `www.hrzest.com`. No AWS ACM anywhere.
- **DNS and registrar:** both are **Hostinger** (nameservers `aurora`/`nebula.dns-parking.com`), not Route 53.
- **Mail:** Microsoft 365. The app sends through the Microsoft Graph API (`utils/graph_mail.py`).
- **Deploys:** PR into `develop` → Jenkins → dev copy. Then PR `develop` → `main` → Jenkins tests → your approval → the `production` branch moves → the EC2 timer pulls and restarts.
- **Security headers:** each is sent once, by the app (`app_setup/csrf_csp.py`). Production nginx adds none, so there were no duplicates to remove. The ground-rule headers and the `__Host-session` cookie flags are unchanged, and both the tests and the verify script check them.

---

## Changes in code

### OBS-01: cache headers

`app_setup/cache_control.py` adds the following headers to **every** response except a short allow-list of public, data-free paths:

```
Cache-Control: no-store, no-cache, must-revalidate, private
Pragma: no-cache
Expires: 0
```

The allow-list is `/static/…`, `/favicon.ico`, `/robots.txt`, `/sitemap.xml` and `/.well-known/security.txt`.

Because it's an allow-list, every login, signup, forgot-password and reset page, every authenticated page and every API response is covered, including routes added later. The landing page isn't cached either, because every HTML page carries a per-session CSRF token.

The per-route `Cache-Control` copies in the SOC console, support-ticket downloads and the session stream were removed, so the header is set in one place.

There's no CDN, and nginx caches only `/static/`, so no bypass rule is needed. If a CDN is ever added, give it a "bypass cache" rule for everything outside `/static/`.

### OBS-06: security.txt

- **`/.well-known/security.txt`:** served as `text/plain; charset=utf-8`, with `Contact: mailto:security@hrzest.com`, `Expires: 2027-11-01T00:00:00.000Z`, `Preferred-Languages: en`, `Canonical` and `Policy`.
- **`/security.txt`:** redirects there with a 301.
- **`/security-policy`:** a new vulnerability-disclosure page. It's indexable and in the sitemap.

**Renewal:** `tests/test_security_txt.py` starts failing 60 days before `Expires`. To renew, move `EXPIRES` in `blueprints/security_txt.py` about 11 months ahead and check that the mailbox still works.

> **Before deploying:** create `security@hrzest.com` in Microsoft 365 (a shared mailbox, or an alias on an existing one). The file advertises it.

### OBS-07: robots.txt, the operator console and the route audit

- **robots.txt:** now disallows only `/api/`. It no longer names `/super_admin`, `/webhooks/` or `/csp-report`.
- **Search engines:** private pages are kept out by the existing `X-Robots-Tag: noindex, nofollow` header, which every page outside `/`, `/create_org` and `/security-policy` already carries. That includes the whole admin side and `/super_admin/login`.
- **Console returns 404:** every `/super_admin` page now answers **404** to anyone without a super-admin session, instead of redirecting to the login page. That covers anonymous visitors, tenant admins, HR and employees. A super-admin whose session has gone idle is still sent to `/super_admin/login`. **Bookmark `/super_admin/login`**: `/super_admin` itself now 404s until you're signed in.

**Route audit.** Every `/api/` (210), `/super_admin` (36) and webhook route was checked.

- **Authentication:** no route is reachable without it, except ones that are deliberately public. `tests/test_route_auth_guards.py` already fails CI for any route with no auth decorator that isn't on a reasoned allow-list.
- **`/super_admin`:** all 34 routes other than login use `_platform_admin_required`.
- **Webhooks:** `/webhooks/<provider>` verifies the HMAC signature before parsing the body, and fails closed when no secret is set.
- **OAuth callbacks:** they use server-side single-use `state` plus PKCE.

The audit did find **role and team-scope gaps** for staff roles. These are now fixed, with tests in `tests/test_route_audit_fixes.py`:

| Route | Problem | Fix |
|---|---|---|
| `POST /api/attendance/checkin` | Any staff token could punch any employee, with no biometric check | Company admin token only |
| `POST /api/holidays` | Any staff token could add a holiday (changes payroll days) | Admin or HR, same as DELETE |
| `/api/admin/attendance/office_networks` | Any staff session could rewrite the anti-spoofing IP allow-list | Company admin only |
| `/api/admin/attendance/flagged` | Company-wide for every role | Limited to the caller's team |
| `/update_employee_photo/<id>`, `/admin_id_card/<id>`, `/admin_view_id_card/<id>` | Team-scoped roles could replace face photos or download anyone's QR ID card | Team scope (`hr_scope_denied`) |
| `/upload_document`, `/download_document/<id>`, `/delete_document/<id>` | Team-scoped roles could reach any employee's documents | Team scope |
| `/onboarding_admin_task_update` | IDOR: scope was checked on the form's `ob_id`, but the form's `task_id` was updated | Owner read from the task row |
| `/my_onboarding_task_done` (web + API) | Trusted the form's `ob_id` | Read from the task row |
| `resolve_admin_identity()` (email blast, AI routes) | A deactivated admin's open session still worked | Full `session_account_ok()` check |

Open items from the audit are listed under [Still open](#still-open).

### OBS-08: X-XSS-Protection

It is now `X-XSS-Protection: 0`, following OWASP guidance: it explicitly disables the old browser filter. The CSP is unchanged.

### OBS-09: canonical host

- **Canonical host:** `hrzest.com`. The canonical tags, `og:url`, the sitemap and the `robots.txt` sitemap line all use it, along with the code's fallback when `APP_URL` is unset. The server's `.env` already has `APP_URL=https://hrzest.com`.
- **Backup redirect in the app:** `utils/canonical_host.py` is the outermost WSGI layer. It redirects `https://www.hrzest.com/…` to `https://hrzest.com/…` with the path and query kept: 301 for GET/HEAD, 308 otherwise, so a POST stays a POST. It does nothing without a real `APP_URL` domain, so local dev and the dev copy are unaffected.
- **Primary redirect:** the nginx change in the [runbook](#obs-09-nginx-www-redirect). It also covers `http://www…` in one hop.

### Section 9: sessions and CSRF

| Check | Before | Now |
|---|---|---|
| New session ID at login | Yes (`session.clear()` + new `_sid`/`_csrf`) | Unchanged, now tested |
| New session ID on password change | Other sessions were ended by the password-version stamp; the current one kept its ID | Current session gets a new `_sid` **and** a new CSRF token; the old ID is revoked |
| Logout ends the session server-side | **No.** Cookie sessions only cleared the browser's copy, so a copied cookie stayed valid until it timed out | **Yes.** Logout writes `status='revoked'` for the session ID before responding, and every auth check refuses it. Covers tenant admin/HR, employee, super-admin and SOC logouts |
| Idle timeout | Admin/employee: company setting (default 30 min). Super-admin: 30 min. SOC: 15 min | Unchanged, now tested |
| Absolute timeout | 8 h (SOC: 8 h, configurable) | Unchanged, now tested |

The session that was exposed in the terminal during testing is now dead: its own timeouts have long passed. **Rotating `SECRET_KEY`** in the server `.env` would also invalidate every existing cookie and signed value, if you want a clean slate. Everyone would have to sign in again.

**CSRF.** One central check (`app_setup/csrf_csp.py::_enforce_csrf`) runs on **every** POST, PUT, PATCH and DELETE. It compares the submitted token with `session["_csrf"]` using `compare_digest`, so the token is bound to the session, and it's renewed at login and now also on password change. Requests that don't need a CSRF token:

| Route | Why |
|---|---|
| `/webhooks/<provider>` | Server-to-server, no cookie. The HMAC signature is verified instead |
| `/csp-report`, `/api/client-log` | Browser beacons that only write logs, size-capped and rate-limited |
| `/api/*` carrying `Authorization: Bearer …` | Can't be forged cross-site without a CORS preflight |
| `/api/*` with no logged-in session | Nothing to ride on: login, signup, leads, kiosk check-in |

Every other state-changing route validates the token, including `/login`, which is protected against login CSRF. The check is skipped only when `app.testing` is true, i.e. in the test suite.

---

## Runbooks (outside the repo)

### OBS-02: DMARC and DKIM

**Who sends mail as @hrzest.com** (searched in the code):

| Sender | Path | SPF |
|---|---|---|
| The app (OTPs, welcome mail, alerts, invoices; `noreply@hrzest.com`) | Microsoft 365 via the Graph API (`EMAIL_PROVIDER=microsoft_graph`) | Covered by `include:spf.protection.outlook.com` |
| People's mailboxes (`contact@`, `security@`, …) | Microsoft 365 | Covered |
| SMTP fallback (`SMTP_*` in `.env`, previously Brevo) | **Not active**: commented out on the server since 2026-11-02 | If it's ever turned back on, add `include:spf.brevo.com` and set up Brevo's DKIM first |
| A company's own SMTP (Settings → Email) | Sends from *that company's* domain | Not hrzest.com; nothing to add |

No SendGrid, SES or Mailgun is used. **The current SPF needs no change.**

**1. Confirm DKIM is on in Microsoft 365.** Do this first. `adkim=s` needs real `d=hrzest.com` signatures.

1. Go to <https://security.microsoft.com/authentication?viewid=DKIM> (Email & collaboration → Policies & rules → Threat policies → Email authentication settings → **DKIM**).
2. Select **hrzest.com**. "Sign messages for this domain with DKIM signatures" must be **Enabled**. If it's off, turn it on. The two selector CNAMEs it needs already exist in DNS.
3. Check: send an email from the app (for example a password-reset OTP) to a Gmail address and open **Show original**. You should see `DKIM: 'PASS' with domain hrzest.com` and `SPF: PASS`. Alternatively, in Exchange Online PowerShell: `Get-DkimSigningConfig -Identity hrzest.com | fl Enabled,Status` should show `Enabled: True`, `Status: Valid`.

**2. Create the report mailbox:** `dmarc-reports@hrzest.com`, as a shared mailbox or an alias. Aggregate reports arrive as zipped XML. A free DMARC report viewer makes them readable.

**3. Publish the record.** In Hostinger hPanel go to **Domains → hrzest.com → DNS / Nameservers → DNS records → Add record**:

| Type | Name | TTL | Value |
|---|---|---|---|
| TXT | `_dmarc` | 3600 | `v=DMARC1; p=none; rua=mailto:dmarc-reports@hrzest.com; fo=1; adkim=s; aspf=s; pct=100` |

Check it with `dig +short _dmarc.hrzest.com TXT`.

**4. Tighten it in stages.** Move to the next stage only when the reports show that everything legitimate passes.

| When | Record change | What to watch |
|---|---|---|
| Weeks 0–4 | `p=none` (as above) | Every source sending as hrzest.com. All should be Microsoft 365, passing both SPF and DKIM with alignment |
| Then 2 weeks | `p=quarantine; pct=25`, then `pct=100` | Nothing legitimate landing in spam |
| Then | `p=reject; pct=100` | Spoofed mail is refused outright |

If a legitimate sender shows up failing, fix it (add its SPF include and DKIM) before tightening further.

### OBS-03: DNSSEC

Hostinger is both the registrar and the DNS host. Today `delegationSigned` is false and there are no DS or DNSKEY records.

1. In hPanel open **Domains → hrzest.com**, then the **DNSSEC** section (under the domain's DNS or nameserver settings).
2. **If Hostinger offers "Enable DNSSEC" for its own nameservers:** turn it on. Hostinger signs the zone and, as the registrar, publishes the DS record itself.
3. **If hPanel only lets you *add DS records*** (meaning it can't sign its own zone), move DNS to a host that signs and paste its DS into hPanel:
   - **Cloudflare (free):** add the zone, copy **every record** across (A, www, MX, SPF/DMARC TXT, both DKIM CNAMEs, CAA), switch the nameservers in hPanel, wait for the zone to go active, then turn on **DNS → Settings → DNSSEC** and add the DS record it shows in hPanel → DNSSEC.
   - **Route 53:** create the hosted zone, copy the records, then **enable DNSSEC signing**. This needs a **customer-managed KMS key in us-east-1** (asymmetric, `ECC_NIST_P256`, sign/verify) with a key policy allowing `dnssec-route53.amazonaws.com`. Create the KSK, switch the nameservers, then add the DS record at Hostinger.
4. **Check** after 1–2 hours:
   ```
   dig hrzest.com DNSKEY +dnssec   # DNSKEY + RRSIG records
   dig hrzest.com DS +short        # non-empty
   dig @1.1.1.1 hrzest.com A +dnssec | grep flags   # includes "ad"
   ```
   Then check <https://dnsviz.net/d/hrzest.com/dnssec/> for no errors.

**Rollback order matters.** Remove the DS record at the registrar **first**. Wait at least 48 h (the parent's DS TTL). Only then turn off signing. Doing it the other way round makes the domain stop resolving for validating resolvers.

### OBS-04: CAA

Checked: only Let's Encrypt issues for this domain. There's no ACM, CloudFront or load balancer, so `amazon.com` is **not** needed. Add it if you ever put ACM in front.

In hPanel → DNS records, add three CAA records on the root (`@`):

| Flag | Tag | Value |
|---|---|---|
| 0 | issue | `letsencrypt.org` |
| 0 | issuewild | `;` |
| 0 | iodef | `mailto:security@hrzest.com` |

Check with `dig hrzest.com CAA +short`.

**The certbot renewal on the server keeps working**, because it's Let's Encrypt. Test without waiting: `sudo certbot renew --dry-run`.

### OBS-05: SSH, security group, fail2ban, updates

The host is Ubuntu 24.04 with OpenSSH 9.6p1, and `ubuntu` is the only login (`AllowUsers ubuntu`). No sshd config, security group or provisioning lives in the repo, so this is manual. Deploys don't use SSH (Jenkins moves the `production` branch and the server pulls), so tightening SSH can't break deploys.

**Before you start:**
- **Keep your current SSH/PuTTY session open throughout.** Test from a second window before closing it.
- **`AllowTcpForwarding no`** turns off PuTTY tunnels, for example to reach the database from your PC. If you use one, set that line to `yes` in the drop-in before installing.
- **Host key change:** `HostKeyAlgorithms` drops ECDSA host keys. If PuTTY or `plink -hostkey` remembered the server's ECDSA key, PuTTY will warn once that the host key isn't cached. Compare it with `sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` before accepting.
- **Drop-in filename:** it's `00-hrzest-ssh-hardening.conf`, **not** `99-`. sshd keeps the **first** value it reads for each setting, and Ubuntu reads `sshd_config.d/*.conf` alphabetically, so a `99-` file loses to EC2's `50-cloud-init.conf` and silently doesn't apply.

**1. Install** once the deploy has brought the files onto the server:
```bash
cd /home/ubuntu/apps/HRZEST
sudo bash scripts/host-hardening/install_ssh_hardening.sh
```
The script:
1. Refuses to run if `ubuntu` has no authorized key.
2. Backs up `/etc/ssh` to `/root/ssh-config-backup-<time>.tgz`.
3. Installs the drop-in and runs `sudo sshd -t`. If that fails, it removes the drop-in.
4. Checks the **effective** config (`sshd -T`): no SHA-1 MAC, no password logins.
5. Runs `systemctl reload ssh`.
6. Installs **fail2ban** (sshd jail: 3 tries in 10 min → 1 h ban; recidive jail → 1 week) and **unattended-upgrades** (daily security updates, no automatic reboot).

**2. Test from a second window:** `ssh -i <key> ubuntu@18.61.48.198`, or a new PuTTY session. Then close the first one.

**3. Security group** (AWS console, region **ap-south-2 / Hyderabad**):
1. Go to **EC2 → Instances →** the HRZEST instance **→ Security → Security groups →** the group **→ Edit inbound rules**.
2. Remove any rule for port **22** with source `0.0.0.0/0` or `::/0`.
3. Add **SSH, TCP 22, Source: My IP** (a single `/32`) for each trusted address, with a description such as "Nithin office".
4. Leave **80** and **443** open to `0.0.0.0/0` and `::/0`.
5. **Save rules.**

Your ISP address changes often. When SSH times out, update the "My IP" rule.

**Better: no port 22 at all (SSM Session Manager).**
1. Create an IAM role for EC2 with **AmazonSSMManagedInstanceCore**, and attach it via **Instance → Actions → Security → Modify IAM role**.
2. Ubuntu 24.04 AMIs already include the SSM agent (`sudo snap services amazon-ssm-agent`).
3. Connect with **EC2 → Connect → Session Manager**, or `aws ssm start-session --target <instance-id>`.
4. Once that works, **delete the port-22 rule** entirely.

**4. Check** from an allowed address:
```bash
nmap -Pn -p 22 --script ssh2-enum-algos,ssh-auth-methods hrzest.com
```
Expect no `hmac-sha1` and `publickey` as the only auth method. From any other address the port should be **filtered**. `scripts/verify_security_fixes.sh` runs this for you.

**Rollback:** from the still-open session, `sudo rm /etc/ssh/sshd_config.d/00-hrzest-ssh-hardening.conf && sudo systemctl reload ssh`. To restore the backup, use `sudo tar -xzf /root/ssh-config-backup-*.tgz -C /`. To lift a fail2ban ban, use `sudo fail2ban-client set sshd unbanip <ip>`. If you're locked out entirely, use **EC2 Instance Connect** or **Session Manager** from the AWS console.

### OBS-09: nginx www redirect

The block to add is in `scripts/host-hardening/nginx-www-redirect.conf`. On the server:

1. Back up the site file:
   ```bash
   sudo cp /etc/nginx/sites-available/hrzest /etc/nginx/sites-available/hrzest.bak-$(date +%F)
   sudo certbot certificates    # note the certificate path, normally /etc/letsencrypt/live/hrzest.com/
   sudo nano /etc/nginx/sites-available/hrzest
   ```
2. Make three edits:
   - In the `listen 443` server block, set `server_name hrzest.com;`.
   - In the `listen 80` block, set `return 301 https://hrzest.com$request_uri;`.
   - Paste the `server { … server_name www.hrzest.com; … return 301 https://hrzest.com$request_uri; }` block from the file. Adjust the certificate paths if `certbot certificates` showed different ones.
3. Validate and reload:
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```
4. Check:
   ```bash
   curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' 'https://www.hrzest.com/test?x=1'
   # → 301 https://hrzest.com/test?x=1
   ```

Don't add security headers to the www block. They're set once, by the app, and HSTS `includeSubDomains` from the apex already covers www.

**Rollback:** `sudo cp /etc/nginx/sites-available/hrzest.bak-<date> /etc/nginx/sites-available/hrzest && sudo nginx -t && sudo systemctl reload nginx`.

---

## Deploy steps

1. **Before merging:** create the `security@hrzest.com` and `dmarc-reports@hrzest.com` mailboxes in Microsoft 365.
2. **Into `develop`:** open a PR from `fix/security-review-2026-11` into `develop` and merge it. Jenkins tests it and deploys to the dev copy (`http://localhost:5050`). Spot-check `/.well-known/security.txt`, `/security-policy`, a login page's headers, and that `/super_admin` gives 404 but `/super_admin/login` works.
3. **Into `main`:** open a PR from `develop` into `main` and merge it with **"Create a merge commit"**. Approve the build in Jenkins. The server deploys within about 2 minutes.
4. **Check:** run `bash scripts/verify_security_fixes.sh`. Everything in OBS-01, 06, 07, 08, the HTTPS-www check and the ground rules should PASS. DNS, SSH and `http://www` stay FAIL until the next step.
5. **Manual steps, in this order:**
   1. nginx www redirect.
   2. SSH hardening and fail2ban.
   3. Security group.
   4. CAA.
   5. DKIM check, then DMARC `p=none`.
   6. DNSSEC.
6. **Check again:** re-run the verify script. Start the DMARC staging timer.

## Rollback

- **App code:** move `production` back to the previous `main` commit. Jenkins can do this, or run `git push origin +<previous main sha>:refs/heads/production`. The server deploys it within about 2 minutes. Each fix is its own commit, so a single one can also be reverted with `git revert <sha>` through the normal PR flow.
- **Sessions:** the change is additive. Rows marked `revoked` in `session_risk` are harmless if the code is rolled back.
- **Host and DNS:** see the rollback notes in each runbook above. For DNSSEC, remove the DS record first.

## Still open

These came out of the route audit and need a decision or a larger change, so they weren't bundled into this branch:

1. **Mobile (Bearer) admin APIs don't apply team scope for HR or custom team roles.** Leave, overtime and resignation lists and actions, tickets, documents, `/api/employees/<id>` (which returns `salary_per_day`), onboarding, `/api/monthly_report`, `/api/compoff` and `/api/shifts/assign` all give an HR or team-role **token** the whole company, while the website versions are scoped. The fix is a Bearer version of `hr_scope_denied`, keyed on the token's role `data_scope`, applied to about 15 routes. This should be its own PR with mobile testing.
2. **Company-wide actions for team-scoped roles:** `/api/admin/email-blast` (`target_type=all`), `/api/ai/attrition-analytics` and `/api/ai/evaluate-interview` work for any admin-side role. **Decision needed:** which roles may email the whole company or see company-wide attrition risk?
3. **OAuth login:**
   - `state` isn't tied to the browser that started the flow, which allows login CSRF. The fix is a short-lived nonce cookie stored with the state.
   - The OAuth login skips the password login's MFA step, the KYC-rejection block and the employment-ended check. **Confirm** whether that's intended.
4. **Super-admin sessions** don't re-read `platform_admins`, so a deleted operator stays signed in until the 30-minute idle or 8-hour absolute timeout. Logout now revokes the session. Deletion could do the same.
5. **`/api/employee/auth-config`** (public) returns the office latitude, longitude and geofence radius, which helps someone spoofing GPS.
6. **"QR only" check-in mode**, if a company enables it, accepts a photo of someone's ID-card QR. That's by design, but worth a warning in Settings.
7. **Razorpay webhooks** have no replay window. The handlers are idempotent, so this is low risk.
