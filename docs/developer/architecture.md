# Architecture

HRzest is one Flask application serving three audiences: the website
(server-rendered Jinja pages), the JSON API (used by the pages' own `fetch()`
calls and by the mobile app), and background jobs. Every company is a tenant
with its own PostgreSQL schema. For a folder-by-folder map see
[PROJECT_STRUCTURE.md](../../PROJECT_STRUCTURE.md); this page explains how the
pieces work together.

```text
 Browser ──HTML + fetch──┐            ┌── Mobile app (Expo) ── JSON + Bearer token
                         ▼            ▼
   nginx (TLS) ──► gunicorn (gthread) ──► WSGI middleware ──► Flask app
                                           │                   ├─ before_request hooks (security, company, CSRF)
                                           │                   ├─ blueprints/ (routes: pages + /api/...)
                                           │                   └─ after_request hooks (headers, CSP, telemetry)
                                           ▼
                        PostgreSQL: att_master + one schema per company (att_<company>)
   Per process: APScheduler jobs, email-queue worker, document-verification worker, async security-event writer
```

## Start-up

Production runs `gunicorn -c gunicorn.conf.py wsgi:application`. Locally,
`run_dev.py` imports the same `wsgi.py`, so it behaves the same.

`wsgi.py`, in order:

1. Loads `.env` and imports `extensions.py`, which creates the Flask `app`, JSON
   logging (logger `attendance`), the rate limiter (300 per minute by default,
   in memory unless Redis is configured), CORS for `/api/*`, session cookie
   settings, and `log_security_event()`. Outside development it also **refuses
   to start** on unsafe settings: a weak or missing `SECRET_KEY`, an unencrypted
   remote database connection, a bad `APP_URL`, or missing Redis when required.
2. Wraps the app in three WSGI middlewares; a request passes them in this order:
   - `CanonicalHostMiddleware` (`utils/canonical_host.py`): redirects `www.` to
     the bare domain;
   - `TenantPrefixMiddleware` (`utils/tenant_routing.py`): finds the company in
     the URL (below);
   - `ProxyFix` (`utils/client_ip.py`, `TRUSTED_PROXY_HOPS`): trusts nginx's
     `X-Forwarded-For` for the real client IP.
3. Imports `app.py`, which imports the `app_setup/` modules **in a fixed
   order**: `telemetry, config, templating, tenant, security, session_watch,
   csrf_csp, cache_control, errors`. Flask runs `before_request` hooks in
   registration order and `after_request` hooks in reverse, so this order is
   the request pipeline. It then registers every blueprint
   (`blueprints/__init__.py`, `register_all()`) and adds `/api/v1/...` aliases.
4. Runs database migrations (once per change to migration code; see
   [database.md](database.md#migrations)).
5. Starts the background workers and the scheduler (below).

`python app.py` also works, but it doesn't start the scheduler and always runs
migrations. Use `run_dev.py`.

## Request pipeline

### Before the route (`before_request`, in order)

| # | Hook | Where | What it does |
| --- | --- | --- | --- |
| 1 | `_assign_request_id` | `extensions.py` | Request ID for logs (`X-Request-ID`) |
| 2 | rate limiter | Flask-Limiter | Global and per-route limits → 429 |
| 3 | `_telemetry_start` | `app_setup/telemetry.py` | Starts the request timer |
| 4 | `_normalize_loopback_host` | `app_setup/tenant.py` | `127.0.0.1` → `localhost` (passkeys need it) |
| 5 | `_resolve_tenant` | `app_setup/tenant.py` | Decides the company (below) |
| 6 | `_enforce_ip_ban` | `app_setup/security.py` | Banned IP or range → 403 |
| 7 | `_waf_inspect_request` | `app_setup/security.py` | Firewall signatures → 403, and counts toward an auto-ban |
| 8 | `_enforce_session_lifetime` | `app_setup/security.py` | Session older than 8 hours → signed out |
| 9 | `_enforce_idle_timeout` | `app_setup/security.py` | Idle longer than the company's timeout → signed out |
| 10 | `_enforce_admin_mfa_enrollment` | `app_setup/security.py` | Admin/HR/manager without TOTP → `/admin/mfa-required` |
| 11 | `_enforce_org_profile` | `app_setup/security.py` | New company admin must finish the Organization profile first |
| 12 | `_block_private_uploads` | `app_setup/security.py` | Uploaded HR documents are never served as static files |
| 13 | `_watch_session_context` | `app_setup/session_watch.py` | Notes browser or network changes for detections (never blocks) |
| 14 | `_enforce_csrf` | `app_setup/csrf_csp.py` | CSRF check on POST/PUT/PATCH/DELETE |
| 15 | `_set_csp_nonce` | `app_setup/csrf_csp.py` | Nonce for this response's inline scripts |

Then the route's own guard decorator runs (see
[auth-and-security.md](auth-and-security.md#protecting-routes)), then the route.

### After the route (`after_request`, in the order they run)

1. `X-Robots-Tag: noindex` on private pages, and a page-view count for the
   public ones in `INDEXABLE_PATHS` (`blueprints/seo.py`).
2. Data-cache invalidation after a successful write (`utils/data_cache.py`).
3. `Cache-Control: no-store` on everything except static files and public
   robots/sitemap/llms.txt/security.txt (`app_setup/cache_control.py`).
4. CSRF meta tag, hidden form fields and helper scripts injected into HTML
   pages (`_inject_csrf_meta`).
5. Settings-cache refresh after a settings change.
6. **Security headers and the Content-Security-Policy** (`_security_headers`).
7. Login-success marker cookie, and re-binding the session to its company
   after a login cleared it (`app_setup/tenant.py`).
8. Request telemetry (route, status, latency) to `http_telemetry`.
9. Rate-limit and CORS headers; the request ID header.

### Errors

`app_setup/errors.py` handles 403, 404, 413, 429, 500, unhandled exceptions,
and `DatabaseUnavailable` (503). For `/api/` paths and `fetch()` requests they
answer JSON, `{"ok": false, "msg": ...}`; otherwise an HTML error page. Unhandled
errors are logged with the traceback and emailed to the company's admins, at most
once per 15 minutes per distinct error.

## Which company a request belongs to

Every company has a URL slug and a schema:
`https://hrzest.com/acme-corp/login` → `att_master.tenants.subdomain = 'acme-corp'`
→ schema in `tenants.db_name`.

1. **`TenantPrefixMiddleware`** looks at the first path segment. Unless it's a
   reserved word (`static`, `api`, `login`, `webhooks`, `super_admin`,
   `socperson`, ...), it looks the slug up among active companies. On a match it
   moves `/acme-corp` from the path into `SCRIPT_NAME`, so routes see
   `/login`, and records `hrz.tenant_slug` and `hrz.tenant_db` in the request
   environment. If the lookup fails, it lets the request through unchanged.
2. **`_resolve_tenant()`** sets `g.tenant_db`:
   - a session already bound to a company keeps it (re-checking every 5 minutes
     that the company is still active);
   - **opening another company's URL signs you out** of the first (the session
     is cleared and a `tenant.session_mismatch` event is logged);
   - a slug in the URL binds a fresh session to that company;
   - otherwise the default company (`DB_NAME`) is used. That's the case for the
     public site, local development, and API calls without a prefix.
   - A suspended company gets 403; one whose schema is missing tables gets 503.
3. **`get_db_connection()`** sets `search_path` to `g.tenant_db` on every borrow,
   so queries need no schema names.

A bare `/login` (from the landing page) finds the company from the credentials
and switches to it (`_bare_login_company`); see
[auth-and-security.md](auth-and-security.md#website-sign-in-login). Links must be
built with `tpath()` (inside the request) or `company_url()` (emails) so they
keep the company; see [conventions.md](conventions.md#urls-inside-a-company).

Note: **Switch company** inside the admin dashboard is a different thing. It
picks one of the sub-companies listed inside one tenant (`companies` table) and
doesn't change the schema.

## Blueprints

All routes live in `blueprints/`, registered in a fixed order by
`blueprints/__init__.py`. No blueprint uses a URL prefix; every route spells out
its full path. Every `/api/...` route is also available as `/api/v1/...` (same
function and guards). About 800 URL rules in total.

| Area | Blueprints |
| --- | --- |
| Sign-in and accounts | `auth`, `oauth_signup`, `my_sessions` |
| Companies and sign-up | `org` (package), `company_profile`, `hr_team`, `billing`, `seats`, `auto_debit`, `billing_dunning`, `webhooks` |
| Admin dashboard and settings | `admin_views` (package), `hr_dashboard`, `policies`, `email_blast`, `daily_report` |
| Employees | `employees` (package), `employee_portal` (package; self-service and check-in APIs), `documents`, `onboarding`, `performance` |
| Attendance and leave | `attendance`, `leave` |
| Payroll | `payroll`, `disbursement` |
| Support and AI | `tickets`, `notifications`, `ai_hrms` |
| Operators and security | `platform_admin` (package, `/super_admin`), `soc` (`/socperson`), `firewall_ingest`, `privacy`, `honeypot_routes` |
| Infrastructure | `core` (home page, CSP reports, token API), `health` (`/healthz`), `seo` (robots, sitemap, llms.txt, JSON-LD), `security_txt`, `legal` (`/privacy`, `/terms`) |

`blueprints/trial_billing.py` has no routes; it only provides scheduled jobs.

## Background work

Each gunicorn worker process imports `wsgi.py`, so each starts its own copy of
the following. Duplicates are prevented in the database, not by process.

### Scheduled jobs (APScheduler, `wsgi.py`)

17 jobs. Each run is wrapped by `once_per_fire()` (`utils/job_runs.py`): the
first process to insert `(job, minute)` into `att_master.scheduler_job_runs`
runs it, and the rest skip. If the database can't be reached, the run is skipped.
A job can ping `HEARTBEAT_URL_<JOB_ID>` after success for external monitoring.

| Time | Job |
| --- | --- |
| every minute | `health_snapshot` (server, database and app readings) |
| every 5 min | `security_detections` |
| every 10 min | `audit_anchor` (audit-chain anchors to the log stream) |
| 00:30 / 01:00 | `trial_ending_soon_check`, `trial_expiration_check` |
| 02:00 / 02:30 / 03:00 | `auto_debit_sync_and_bill`, `reconcile_billing_records`, `tenant_billing_dunning_check` |
| 03:30-03:55 | `privacy_retention_purge`, `http_telemetry_purge`, `audit_chain_verify`, `health_snapshot_purge`, `firewall_events_purge` |
| 04:00 | `salary_disbursement_prep` |
| 09:00 | `renewal_reminder` |
| 08:00 Asia/Kolkata | `soc_daily_digest` (`SOC_DIGEST_HOUR`) |
| 23:59 Asia/Kolkata | `daily_attendance_report` |

Times without a zone are server time (UTC).

### Worker threads

| Worker | What it does |
| --- | --- |
| `email-queue-worker` (`utils/email_utils.py`) | Every 15 s, sends queued emails from each company's `email_queue` table: claims rows with `FOR UPDATE SKIP LOCKED`, up to 3 attempts, then marks them failed. `send_email_async()` only queues; if queueing fails it sends directly. |
| `document-verification-worker` (`utils/document_verification.py`) | Checks company KYC documents uploaded at sign-up (automated checks with the Anthropic API, plus duplicate detection); failures go to manual review |
| `security-write-worker` (`utils/async_writer.py`) | Writes security events and login-failure records off the request thread. In memory, so writes can be lost if the process dies (by design). Tests switch it to synchronous mode. |
| telemetry flushers | Batch-write HTTP telemetry (every 60 s) and firewall events (every 5 s) |

## Logging and security events

- **Application logs**: JSON lines on stdout (logger `attendance`), with the
  request ID. Under systemd they're in `journalctl -u hrzest`.
- **`log_security_event(event_type, message, level=..., **fields)`** writes to
  three places: the log line; the `public.security_events` table (through the
  async writer, tagged with the company); and, for ERROR and CRITICAL, the alert
  channels (`SECURITY_ALERT_WEBHOOK_URL` for Discord/Slack,
  `SECURITY_ALERT_EMAIL`). The SOC console and the detections job read the
  table.
- Logs can be shipped to Loki and Grafana; see
  [observability/README.md](../../observability/README.md).

## The mobile app

A separate Expo / React Native app in `mobile/` that talks only to the JSON API
with Bearer tokens. See [mobile.md](mobile.md).
