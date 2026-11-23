# Sign-in, roles and security

How people sign in, how routes are protected, and the security mechanisms you'll
meet when changing code. For the hosting side (server hardening, backups, audit
anchors, liveness rollout) see [SECURITY_HARDENING.md](../SECURITY_HARDENING.md);
for the security policy see [SECURITY.md](../../SECURITY.md).

## Who can sign in

| Who | Where | Kind of session | Code |
| --- | --- | --- | --- |
| Company admin, HR, manager, custom role | website `/login`, app | web session `admin_logged_in` / Bearer token `admin` | `blueprints/auth.py`, `blueprints/core.py` |
| Employee | website `/login`, app | web session `employee_id` / Bearer token `employee` | same |
| Platform admin (operator over all companies) | `/super_admin/login` | separate session keys `platform_admin_*` | `blueprints/platform_admin/` |
| SOC analyst | `/socperson/login` | separate session keys `soc_*` | `blueprints/soc.py`, `utils/soc_auth.py` |
| Shared kiosk device (no person signed in) | `/<company>/checkin` | none; identity comes from signed QR + face/fingerprint | `blueprints/attendance.py` |

## Website sign-in (`/login`)

`blueprints/auth.py`, `admin_login()`. Also served at `/admin_login` and
`/admin-login`. Rate limits: 20 per minute, 60 per 15 minutes, 150 per hour.

1. **Which company.** With no company in the URL (the landing page's Sign in),
   `_bare_login_company()` searches every active company for these credentials
   and switches the request to the one that matches. If the password matches in
   more than one, the page asks for the company. `/<company>/login` skips this.
2. **Which account.** People sign in with their **email**. `_sign_in_name()`
   maps it to an account: `admin_users` by email or email-username, then
   employees by encrypted-email blind index. An employee ID or username still
   works as typed, as a quiet fallback.
3. **Lockout.** 4 failures lock the identifier for 15 minutes
   (`_LOGIN_MAX_ATTEMPTS`, `utils/auth.py`).
4. **CAPTCHA.** After 2 failures, Cloudflare Turnstile is required, but only
   when `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` are set. Without them
   this step is skipped.
5. **Password check.** Admin accounts are tried first, then employees. After a
   correct password: inactive account → generic error; employment ended →
   "access ended" message; company KYC rejected → blocked (admins only). Old
   hashes are upgraded to Argon2id on success.
6. **Emailed code (MFA).** With `MANDATORY_LOGIN_MFA` on (the default), a 6-digit
   code is emailed and must be entered at `/mfa_verify`. The code is stored
   server-side as an HMAC, lasts 5 minutes, allows 5 tries, and is single use;
   the session only holds a pointer to the challenge.
7. **Session.** `begin_admin_session()` or `_finish_employee_login()` clears the
   session and starts a fresh one. Admins go to `/admin` (HR to `/hr_dashboard`,
   managers to `/leave_holidays`); employees to `/employee_portal`, or to
   `/force_change_pin` if they must change a first-login password.

Unknown accounts take the same time as known ones (`dummy_password_check`), and
every failure shows the same "Invalid credentials" message.

### Session rules

| Rule | Value | Where |
| --- | --- | --- |
| Cookie | `__Host-session`, secure in production (`APP_ENV`) | `extensions.py` |
| Absolute lifetime | 8 hours | `PERMANENT_SESSION_LIFETIME`, `app_setup/security.py` |
| Idle timeout | company setting `session_timeout`, default 30 minutes | `app_setup/security.py` |
| Kill switch | a session flagged compromised is refused on its next request | `utils/session_risk.py` |
| Password changed elsewhere | older sessions are refused (`_pwv` stamp) | `utils/auth.py` |
| Account deactivated / employment ended | refused on the next request | `utils/auth.py` |

## App sign-in (Bearer tokens)

`blueprints/core.py`:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/signin` | The app's single sign-in box: finds the company (409 `company_required` if the account is in several), maps email to account, then signs in as admin or employee. The answer includes `account` and `company_slug`. |
| `POST /api/login` + `POST /api/login/verify` | Admin sign-in; with MFA on, the first call returns `{mfa_required, challenge_id}` and emails a code, the second exchanges it for a token |
| `POST /api/employee/login` | Employee sign-in by employee ID (no emailed code on this path) |
| `POST /api/logout`, `POST /api/employee/logout` | Delete the presented token |

Tokens are `secrets.token_hex(32)`. Only their **SHA-256 hash** is stored, in the
company's `api_tokens` table (`token_type` `admin` or `employee`), and they last
**24 hours**. Changing a password deletes all of that account's tokens. The app
sends `Authorization: Bearer <token>` and prefixes every call with
`/<company>/` (`mobile/src/api/client.js`).

## Forgot password

`/forgot_password` (also `/admin_forgot_password`, `/employee_forgot_password`)
and `POST /api/forgot_password`. Rate limits: 5 per minute, 20 per hour.

- Takes the email the person signs in with. With no company in the URL it
  searches every active company.
- Emails a link to every matching account. The token is `token_hex(32)`; its
  SHA-256 hash is stored in `reset_token`, valid for **3 hours**. The link names
  the company it belongs to (`company_url()`).
- **It always answers with the same message**, whether or not an account
  matched ("If an account matches, a password reset link has been sent..."),
  so the form can't be used to check whether an email is registered. The real
  outcome is written to the server log only. (On 19 Nov 2026 this briefly said
  "No account found"; the 20 Nov security review changed it back.)

## Kiosk check-in factors (not website sign-in)

- **Signed QR codes**: each employee's QR holds `<employee_id>.<hmac>`
  (`qr_generator.py`); the server verifies the signature.
- **Face**: compared with the employee's registered face; active liveness
  challenges and replay checks in `utils/liveness.py` and
  `utils/punch_integrity.py` (`LIVENESS_MODE` = `warn` or `enforce`).
- **Fingerprint / passkey (WebAuthn)**: `/webauthn/*` routes in
  `blueprints/auth.py`. A successful verification leaves a single-use proof valid
  for 120 seconds that the check-in then consumes.
- **Mobile biometric**: `/api/employee/mobile-biometric-nonce` and `-attest`.
  This is a convenience check, not cryptographic proof (the code says so).

`/api/employee/qr-face-checkin` requires a signed-in employee session or app
token; there is no anonymous check-in from the app.

## Protecting routes

Every route must carry one of these decorators (all in `utils/auth.py` unless
noted). Each tags its wrapper, and `tests/test_route_auth_guards.py` fails for
any route without a tag, unless it's in `ALLOWED_UNGUARDED` with a written
reason (public pages, webhooks verified by signature, routes that check inline).

| Decorator | Lets in | Refuses a page with | Refuses an API call with |
| --- | --- | --- | --- |
| `@admin_required` | any admin-side web session (admin, HR, manager, custom role) | redirect to login | JSON 401; 402 if the company's bill is overdue |
| `@employee_required` | an employee web session | redirect to login (`/admin` if an admin is also signed in; `/force_change_pin` if pending) | JSON 401 (409 admin also signed in, 403 password change pending) |
| `@any_user_required` | admin or employee session | redirect | JSON 401 (AJAX requests only) |
| `@role_required("admin", "hr", ...)` | admin session with one of these roles (or base type) | flash + redirect to `/admin` | JSON 403 "Insufficient permissions." |
| `@require_access("payroll", "edit")` | layered under the above: Roles & Access permission | flash + redirect | JSON 403 |
| `@api_required` | Bearer token of type `admin` | – | 401 / 403 / 402 |
| `@api_role_required(...)` | under `@api_required`: exact role match | – | 403 |
| `@api_require_access(module, action)` | under `@api_required`: Roles & Access permission | – | 403 |
| `@employee_api_required` | Bearer token of type `employee` (or the owner admin's token for their linked employee profile) | – | 401 |
| `@session_or_bearer_required(...)` | a session **or** a token of the allowed kinds | – | 401 |
| `_platform_admin_required` (`blueprints/platform_admin/`) | platform admin session | **404** (the console isn't advertised) | 404 |
| `soc_required(min_role)` (`utils/soc_auth.py`) | SOC session of at least that role | 404 outside the allowed networks; else redirect | 401 / 403 |

Rules:

- **Never answer an API call with a redirect.** Guards go through `_reject()`,
  which returns JSON for `/api/` paths and AJAX requests and a redirect for
  pages. A redirect makes `fetch()` load the HTML login page, which the caller
  can't read.
- Use the narrowest guard that fits, and check object ownership inside the route
  where it matters (`enforce_ownership()`).
- TOTP step-up for sensitive actions: `@require_email_2fa`, `@require_payout_2fa`.

## Roles and permissions

- **Built-in roles:** `admin` (the owner and full admins) and `employee`. HR and
  manager are kept as roles too. Companies can add **custom roles**.
- **Roles & Access grid** (Settings → Roles): per role, `none` / `view` / `edit`
  for each module: employees, attendance, payroll, leaves, announcements,
  tickets, overtime, performance, onboarding, resignations, reports,
  admin_tools, organization. Code: `utils/helpers/rbac.py` (`can_access()`),
  tables `roles` and `role_permissions`. `admin` always passes; anything not
  granted is denied.
- **Team scope:** a role can see the whole company or only its team. Team means
  the employees whose `manager_id` or `assigned_hr_username` points at the user,
  recursively (`utils/helpers/scoping.py`). Filter lists with
  `hr_scope_subquery()`, and check single records with `hr_scope_denied()`.
- Giving an employee an admin-side role creates a linked `admin_users` account
  named after their employee ID; removing it deactivates that account at once.
- Admin, HR and manager accounts must enrol TOTP before continuing
  (`app_setup/security.py`).

## Passwords

- **New passwords:** at least **12** characters (maximum 256), mustn't contain
  the person's name, username, email or company name, and are checked against
  known breached passwords (Pwned Passwords, k-anonymity;
  `PASSWORD_BREACH_CHECK`, fails open if the service is down).
- **Storage:** Argon2id. Older bcrypt and pbkdf2 hashes still verify and are
  upgraded at the next sign-in.

## Request-level protection

Every request passes these before it reaches a route (order and details in
[architecture.md](architecture.md#request-pipeline)):

- **IP bans:** `banned_ips` table. Repeated firewall or rate-limit breaches give
  a 15-minute ban; the honeypot gives 7 days.
- **Firewall (WAF):** `utils/waf.py` checks query strings, form fields, JSON
  bodies and path segments for SQL-injection, XSS and path-traversal shapes,
  and answers 403 "Request blocked by security policy". Server-generated values
  that come back from clients must be WAF-safe (`utils/tokens.py`).
- **CSRF:** every form and `fetch()` POST from a page carries a token, injected
  automatically. API calls with a Bearer token, or with no signed-in session,
  are exempt (`app_setup/csrf_csp.py`).
- **Content-Security-Policy:** built per response with nonces for inline scripts
  and hashes for server-rendered style attributes; no `unsafe-inline`.
- **Rate limits:** Flask-Limiter, in memory per worker (no Redis in this
  deployment).

## Personal data

- Employee email and phone are encrypted at rest (AES-256-GCM, `hz2:` prefix)
  with a separate blind index for lookups. Reads decrypt transparently. Details
  in [database.md](database.md#encrypted-personal-data) and
  [ENCRYPTION_AT_REST.md](../ENCRYPTION_AT_REST.md).
- The app refuses to start without a valid `ENCRYPTION_KEY`.
- Privacy tooling (export, erasure, retention): `utils/privacy.py`,
  `blueprints/privacy.py`.

## Audit and detection

- **Tamper-evident audit log:** `audit_logs` and `security_events` are
  hash-chained and append-only for the app's own database role
  (`migrations/audit_integrity.py`, `scripts/verify_audit_chain.py`).
- **Security events:** `log_security_event(...)` (`extensions.py`) writes
  structured events; CRITICAL ones also go to the alert webhook.
- **Detections and the SOC console:** `utils/detections.py`,
  [docs/SOC.md](../SOC.md).
