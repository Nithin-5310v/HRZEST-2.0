# Configuration

The server is configured with environment variables, normally in `.env` (copy
`.env.example`; `scripts/setup_env.py` fills in the two keys). `.env.example`
documents most of them in detail; this page groups them and adds the ones
`.env.example` doesn't list. **Never commit `.env`.**

Outside development (`APP_ENV` ≠ `development`) the app **refuses to start** on
unsafe settings: missing or weak `SECRET_KEY`, missing `ENCRYPTION_KEY`, a
plaintext connection to a remote database, a bad `APP_URL`, or missing Redis when
required.

## Core

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Signs sessions. At least 32 random characters in production. |
| `ENCRYPTION_KEY` | Encrypts personal data at rest. Required; **never change it without rotating** (`scripts/rotate_encryption_key.py`). |
| `ENCRYPTION_KEY_PREVIOUS` | Old keys, comma-separated, accepted for reading during a rotation |
| `APP_ENV` | `production` (default) or `development` (plain-HTTP cookies, relaxed start-up checks, SQLite fallback allowed) |
| `APP_URL` | Public base URL, no trailing slash (`https://hrzest.com`); used in emailed links. Required outside development. |
| `ALLOWED_ORIGINS` | CORS allow-list for `/api/*`. Required in production. |
| `LOGIN_ASKS_FOR_COMPANY` | `true` on a multi-company server: a bare `/login` searches companies, never the default database |
| `TRUSTED_PROXY_HOPS` | How many proxies in front (nginx = 1), for the real client IP |
| `PORT`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `LOG_LEVEL` | Server process settings (`gunicorn.conf.py`, `run_dev.py`) |
| `SSL_CERT_PATH`, `SSL_KEY_PATH` | Serve HTTPS directly (otherwise `cert.pem`/`key.pem` if present) |

## Database

| Variable | Purpose |
| --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_NAME` | Connection; `DB_NAME` is also the default company's schema |
| `DB_SSLMODE`, `DB_SSLROOTCERT` | TLS to the database; required for a remote database outside development (`DB_ALLOW_PLAINTEXT=1` overrides) |
| `DB_CONNECT_TIMEOUT`, `DB_STATEMENT_TIMEOUT_MS` | 10 s and 30,000 ms by default |
| `DB_POOL_MIN_CONN`, `DB_POOL_MAX_CONN`, `DB_POOL_WAIT_SECONDS` | Pool 5-20, wait 5 s before a 503 |
| `DB_OWNER_USER`, `DB_OWNER_PASS`, `DB_OWNER_POOL_MAX_CONN` | Optional least-privilege split (see [database.md](database.md#owner-and-runtime-roles-optional)) |
| `ALLOW_SQLITE_FALLBACK` | Allow the SQLite fallback outside development (don't) |
| `MIGRATIONS_FORCE` | `1` re-runs migrations on start even if unchanged |
| `TOKEN_PURGE_INTERVAL_SECONDS` | How often expired API tokens are cleaned up |

## Sign-in and accounts

| Variable | Purpose |
| --- | --- |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL` | The default company's first admin, created on first start. `ADMIN_PASSWORD` must meet the password rules (12+ characters, not the username or email, not in a known breach) or no admin is created. Remove it from `.env` once the admin exists |
| `MANDATORY_LOGIN_MFA`, `MANDATORY_ADMIN_MFA`, `MANDATORY_PLATFORM_ADMIN_MFA`, `REQUIRE_EMAIL_2FA` | Emailed sign-in codes and TOTP enrolment; all on by default |
| `PASSWORD_BREACH_CHECK` | Check new passwords against Pwned Passwords (on by default) |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | Cloudflare CAPTCHA after 2 failed sign-ins (off if unset) |
| `SESSION_RISK_THRESHOLD` | Score at which a session is killed (default 50) |
| `MANDATORY_ORG_PROFILE` | New company admins must complete the Organization page first (default on) |
| `KYC_LIMITED_MODE` | Restricted mode (no payouts or bulk email) until a company's documents are approved (default on) |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`, `MICROSOFT_OAUTH_CLIENT_ID` / `_SECRET`, `MS_OAUTH_TENANT` | "Sign up with Google / Microsoft" |

## Email

Either SMTP or Microsoft Graph. **Production uses Microsoft Graph.**

| Variable | Purpose |
| --- | --- |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM_NAME`, `SMTP_FROM_EMAIL` | SMTP (STARTTLS, or implicit TLS on 465) |
| `EMAIL_PROVIDER=microsoft_graph`, `MICROSOFT_TENANT_ID`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `EMAIL_FROM_EMAIL`, `EMAIL_FROM_NAME`, `MICROSOFT_GRAPH_ENDPOINT` | Microsoft 365 via Graph (`utils/graph_mail.py`). **Not in `.env.example`.** |
| `EMAIL_CONSOLE` | Print emails to the console instead of sending (development) |
| `SUPPORT_EMAIL`, `CONTACT_EMAIL` | Addresses shown to users |

A company can also save its own SMTP settings in the app (Settings → Email),
which override `.env` for that company.

## AI

| Variable | Purpose |
| --- | --- |
| `N8N_WEBHOOK_URL`, `N8N_WEBHOOK_SECRET` | HR Assistant via an n8n workflow (tried first) |
| `GEMINI_API_KEY` | HR Assistant via Gemini (second) |
| `ANTHROPIC_API_KEY` | HR Assistant via Claude (third); also the automated KYC document checks |

With none set, the chat says it isn't configured. The assistant gives each
question 40 seconds in total across the services, and skips a service that
failed in the last 2 minutes (`utils/ai_assistant.py`).

## Payments and billing

| Variable | Purpose |
| --- | --- |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | Razorpay; without the webhook secret `/webhooks/razorpay` rejects everything |
| `PAYMENTS_ENABLED` | Master switch (unset = on) |
| `RAZORPAY_ALLOW_TEST_KEYS` | Accept `rzp_test_` keys in production |
| `PAYOUT_PROVIDER` | Salary disbursement provider |
| `INVOICE_SELLER_*`, `INVOICE_SIGNATORY`, `INVOICE_GST_PERCENT`, `INVOICE_SAC_CODE`, `INVOICE_NUMBER_PREFIX` | GST invoice details |

## Check-in and attendance integrity

| Variable | Purpose |
| --- | --- |
| `LIVENESS_MODE` | `off`, `warn` (default) or `enforce` for face liveness challenges |
| `PHOTO_REPLAY_MODE`, `MOCK_LOCATION_POLICY` | `block` by default |
| `MOBILE_REQUIRE_LOCATION` | Require GPS for app check-in (default true) |
| `BIOMETRIC_CONSENT_REQUIRED` | Require recorded consent before face check-in |
| `FACE_LOG_RETENTION_DAYS`, `PUNCH_EVIDENCE_RETENTION_DAYS`, `CHECKIN_CHALLENGE_RETENTION_DAYS` | Retention (30 / 180 / 2 days) |
| `QR_LEGACY_ACCEPT_UNTIL` | Date until which old unsigned QR badges are still accepted |

The office location for the GPS fence is set per company in Settings, not in `.env`.

## Security operations

| Variable | Purpose |
| --- | --- |
| `WAF_BREACH_WINDOW_SECONDS`, `WAF_BREACH_THRESHOLD`, `WAF_BAN_MINUTES`, `WAF_NEVER_BAN_CIDRS`, `WAF_BLOCK_SCANNER_UA` | Firewall auto-ban: 5 breaches in 10 minutes → 15-minute ban |
| `HONEYPOT_REPORT_SECRET`, `HONEYPOT_BAN_MINUTES`, `HONEYPOT_TCP_BAN_MINUTES` | Decoy routes (7-day ban by default) |
| `SOC_LOGIN_EMAILS`, `SOC_ALLOWED_CIDRS`, `SOC_IDLE_MINUTES`, `SOC_SESSION_HOURS`, `SOC_AUTO_BAN`, `SOC_DIGEST`, `SOC_DIGEST_HOUR` | SOC console access and behaviour ([docs/SOC.md](../SOC.md)) |
| `DETECTIONS_INTERVAL_MINUTES`, `AUDIT_ANCHOR_INTERVAL_MINUTES`, `DETECT_*` | Detection job tuning |
| `SECURITY_ALERT_WEBHOOK_URL`, `SECURITY_ALERT_PLATFORM`, `SECURITY_ALERT_SIGNING_SECRET`, `SECURITY_ALERT_WEBHOOK_WINDOW`, `SECURITY_ALERT_EMAIL` | Where ERROR/CRITICAL security events are sent |
| `FIREWALL_AGENT_SECRET`, `FIREWALL_*` | Host firewall agent channel ([docs/FIREWALL.md](../FIREWALL.md)) |
| `CLAMAV_HOST`, `CLAMAV_PORT`, `MALWARE_SCAN_ENABLED` | Upload malware scanning (fails closed outside development) |

## Performance and caching

| Variable | Purpose |
| --- | --- |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REQUIRE_REDIS` | Shared rate limits and auto-ban counters across workers; without Redis they're per process. `REQUIRE_REDIS` defaults to true in production. |
| `DATA_CACHE_ENABLED`, `DATA_CACHE_TTL_SECONDS` | Read cache for dashboard data |
| `SETTINGS_CACHE_TTL_SECONDS`, `COMPANIES_CACHE_TTL_SECONDS`, `ONBOARDING_CACHE_TTL_SECONDS` | In-process cache lifetimes |
| `DB_SKIP_REDUNDANT_SET_SEARCH_PATH`, `DB_SEARCH_PATH_REVALIDATE_EVERY` | Skip repeated `SET search_path` (off by default) |

## Backups and observability

| Variable | Purpose |
| --- | --- |
| `BACKUP_ENCRYPTION` (`gpg` or `age`), `BACKUP_GPG_RECIPIENT`, `BACKUP_AGE_RECIPIENT`, `BACKUP_REMOTE`, `BACKUP_RETAIN_DAYS`, `BACKUP_HEARTBEAT_URL` | Encrypted backups; plaintext backups are refused |
| `HEARTBEAT_URL_<JOB_ID>` | Ping after each successful scheduled job |
| `HTTP_TELEMETRY`, `HTTP_TELEMETRY_RETENTION_DAYS`, `HEALTH_RETENTION_DAYS` | Request and health telemetry |
| `GRAFANA_URL`, `LOKI_URL` | Links into the monitoring stack |

## Mobile push

| Variable | Purpose |
| --- | --- |
| `EXPO_ACCESS_TOKEN` | Optional, for Expo push notifications |

## The mobile app's own settings

The app reads `EXPO_PUBLIC_API_BASE_URL` (server address) and, optionally,
`EXPO_PUBLIC_SENTRY_DSN`, from `mobile/.env` at build time. See
[mobile.md](mobile.md#configuration).
