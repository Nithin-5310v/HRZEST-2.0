# HRzest.com

A full-featured, self-hosted employee management and attendance platform built with Flask, PostgreSQL, face recognition, WebAuthn (passkeys), and GPS geo-fencing. Includes a React Native mobile app and a full CI/CD pipeline.

---

## Features

| Module | What it does |
|---|---|
| **Attendance** | Face recognition, QR code scan, GPS geo-fence, WebAuthn (fingerprint/passkey), manual check-in/out |
| **Employees** | Full CRUD, photo capture, QR generation, ID card PDF, department/role management |
| **Payroll** | Salary rules, monthly reports, payslip emails, Excel export, payroll lock |
| **Leave** | Leave requests, approval workflow, leave types, holiday calendar |
| **Performance** | KPIs, reviews, hike/bonus workflow, PDF export |
| **Onboarding** | Template-based onboarding task assignment |
| **Documents** | Employee document upload and management |
| **Tickets** | Internal support ticket system |
| **Notifications** | In-app notification feed (admin and employee) |
| **Org chart** | Multi-tenant org provisioning -- instant, via "Sign up with Google/Microsoft" (`/create_org`), auto-approved onto a 2-day free trial, no documents or payment upfront (see `GOOGLE_OAUTH_CLIENT_ID`/`MICROSOFT_OAUTH_CLIENT_ID` in `.env.example`) |
| **Employee portal** | Self-service: attendance, payslips, leaves, profile, QR code |
| **Mobile app** | React Native app for employee check-in/out |

---

## Prerequisites

- Python 3.11+
- PostgreSQL 16+
- `cmake`, `dlib` build tools (for face recognition — see Containerfile for the full list)
- Node.js 18+ (for mobile app only)

> **Tip:** The easiest way to run everything locally is with Podman. Skip to [Podman quick start](#podman-quick-start).

---

## Local development setup

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/Nithin-magzest/emplyeee-attendance.git
cd employee-attendance-system
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in every value. The required ones are:

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask session signing key — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ENCRYPTION_KEY` | Fernet key for PII encryption — generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DB_HOST` | PostgreSQL host (`localhost` for local dev) |
| `DB_USER` | PostgreSQL username |
| `DB_PASS` | PostgreSQL password |
| `DB_NAME` | Database name (created automatically on first run) |
| `ADMIN_PASSWORD` | Initial admin account password |

### 4. Run the development server

```bash
python wsgi.py
```

The server starts on `https://localhost:5000` (self-signed cert) or `http://localhost:5000` if no cert is found. The first run creates all database tables automatically.

**Default admin login:** username `admin`, password from `ADMIN_PASSWORD` in `.env`.

---

## Running tests

```bash
# Run all tests with coverage
python -m pytest tests/ -v --tb=short --cov=. --cov-report=term-missing

# Run a specific test file
python -m pytest tests/test_comprehensive.py -v
```

Tests require a running PostgreSQL instance — there is no SQLite/mock fallback for the suite (the SQLite path in `database.py` is a low-fidelity manual-dev convenience only; it has no schema-per-tenant isolation and can't validate tenant boundaries). If you don't already have a local Postgres, spin up a disposable one that matches CI exactly:

```bash
docker compose -f docker-compose.test.yml up -d
# wait for it to report healthy, then:
DB_HOST=127.0.0.1 DB_PORT=5432 DB_USER=postgres DB_PASS=ci_testpass DB_NAME=att_test \
  SECRET_KEY=ci-test-secret-key FLASK_ENV=testing python -m pytest tests/ -v
```

Or, against your own Postgres instance, set the connection variables in your shell or `.env`:

```bash
DB_HOST=127.0.0.1 DB_USER=postgres DB_PASS=yourpass DB_NAME=att_test python -m pytest tests/
```

The CI pipeline runs tests against a real PostgreSQL 16 container — not mocks. This prevents mock/prod divergence.

---

## Podman quick start

Runs the full stack (app + PostgreSQL + nginx) locally:

```bash
cp .env.example .env   # fill in your values first
podman-compose up --build
```

The app is available at `http://localhost` (nginx proxies to the Flask app on port 5000).

---

## Production deployment

**Quick checklist:**

- [ ] Set all `.env` variables — especially `SECRET_KEY`, `ENCRYPTION_KEY`, `ADMIN_PASSWORD`, `APP_URL`, `ALLOWED_ORIGINS`
- [ ] Point your domain DNS to the server IP
- [ ] Run `./init-letsencrypt.sh` to provision a Let's Encrypt TLS certificate
- [ ] Run `./deploy.sh` to pull, build, and start the containers
- [ ] Update `mobile/src/config.js` → `API_BASE_URL = 'https://yourdomain.com'` before building the mobile app

**Container hardening (compose.yaml):** every service runs as non-root with
a read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, and a
pids/memory/CPU limit. Pulled images (postgres, clamav, nginx,
certbot — not the locally-built `app` image) are labeled for
`podman auto-update`, which `deploy.sh` enables as a daily systemd timer —
it pulls a new image if the upstream tag's digest changes and rolls back
automatically if the new image fails its healthcheck. Container logs are
capped at 3×10MB per service to prevent unbounded disk growth.

---

## Environment variable reference

All variables are documented with examples in [.env.example](.env.example).

| Variable | Required | Default | Notes |
|---|---|---|---|
| `SECRET_KEY` | Yes | — | Flask session key |
| `ENCRYPTION_KEY` | Yes | — | PII field encryption |
| `APP_ENV` | No | `production` | Set to `development` to disable secure cookies |
| `DB_HOST` / `DB_USER` / `DB_PASS` / `DB_NAME` | Yes | — | PostgreSQL connection |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Yes | — | Admin account credentials |
| `OFFICE_LAT` / `OFFICE_LON` | No | — | GPS geo-fence centre |
| `APP_URL` | No | *(request host)* | Trusted base URL for email links |
| `ALLOWED_ORIGINS` | No | `*` | CORS whitelist — set your domain in production |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | No | — | SMTP for email delivery (any provider) |
| `ANTHROPIC_API_KEY` | No | *(disabled)* | Enables the employee portal's AI chat widget |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | No | *(disabled)* | CAPTCHA challenge on `/admin_login` after 2 failed attempts |

---

## Project structure

```
employee-attendance-system/
├── app.py                  # Shared setup only: hooks, context processors, DB init/migrations, error handlers (no routes)
├── wsgi.py                 # WSGI entry point, blueprint registration
├── extensions.py           # Shared Flask app, rate limiter, session config
├── database.py             # PostgreSQL connection helpers
├── blueprints/             # All routes (21 blueprints, ~18k lines total)
│   ├── health.py           # /healthz, /favicon.ico
│   ├── notifications.py    # /api/notifications, /web/notifications/*
│   ├── auth.py, employees.py, attendance.py, payroll.py, leave.py, ...
│   └── ...                 # see wsgi.py header for the full registered list
├── utils/
│   ├── auth.py             # Auth decorators, lockout, password hashing
│   ├── helpers.py          # Audit log, caching, encryption, validation
│   ├── config.py           # Shift/salary runtime constants
│   ├── email_utils.py      # SMTP + async email queue
│   └── attendance_utils.py # Attendance calculation logic
├── templates/              # Jinja2 HTML templates
├── static/                 # CSS, JS, images
├── mobile/                 # React Native mobile app
├── tests/                  # pytest integration tests
├── Containerfile
├── compose.yaml
├── compose.prod.yaml
├── nginx/
└── .env.example
```

---

## Architecture notes

- **Blueprint migration complete** — all routes live in `blueprints/` (21 blueprints, ~18k lines). `app.py` (~3.3k lines) now holds only shared setup: context processors, request hooks (CSRF/WAF/CSP/session timeout), DB schema init/migrations, and error handlers. See `wsgi.py` for the full blueprint registration list.
- **CSP** — Content-Security-Policy is generated dynamically per-response. Inline event handlers are sha256-hashed at render time; no `'unsafe-inline'` is used.
- **Multi-tenancy** — subdomain-based tenant routing via `_resolve_tenant()` in `app.py`. Each organisation gets its own PostgreSQL schema within the shared database. `/create_org` self-signup is open by default (Turnstile-protected when `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` are set), with pricing tiers enforced from `utils/plan_limits.py`. Company subdomains require wildcard DNS/TLS (not yet configured — see the SaaS pivot plan) to actually route; until then, new tenants provision correctly but aren't reachable by subdomain.
- **Platform admin console** — `/super_admin` (`blueprints/platform_admin.py`), a cross-tenant operator panel (list all companies, change plan, suspend/reactivate) with its own login identity in `att_master.platform_admins`, separate from any tenant's `admin_users`. No account is seeded automatically — run `python seed_platform_admin.py --username <user> --email <email>` (prompts for the password, or pass `--password`) once per deployment; safe to re-run since it upserts by username.
- **Rate limiting** — `flask-limiter` with an in-memory backend (no Redis in this deployment — counters are per-worker, not shared across `GUNICORN_WORKERS`).

---

## Tech stack

- **Backend:** Python 3.11, Flask 3, PostgreSQL 16, Gunicorn
- **Auth:** bcrypt, WebAuthn (passkeys), session-based admin auth, Bearer token API auth
- **Security:** CSRF (custom), CSP (dynamic sha256), HSTS, rate limiting, Fernet PII encryption
- **Face recognition:** `face_recognition` (dlib-based)
- **Mobile:** React Native (Expo)
- **DevOps:** Podman, nginx, Let's Encrypt, GitHub Actions CI/CD

---

## Contributing

1. Fork the repo and create a feature branch
2. Copy `.env.example` to `.env` and configure for local dev
3. Run `python -m pytest tests/ -v` — all tests must pass
4. Open a pull request against `master`

**Never commit** `.env`, `cert.pem`, or `key.pem`.
