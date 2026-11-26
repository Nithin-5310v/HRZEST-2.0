# HRzest.com

A full-featured, self-hosted employee management and attendance platform built with Flask, PostgreSQL, face recognition, WebAuthn (passkeys), and GPS geo-fencing. Includes a React Native mobile app and a full CI/CD pipeline.

**Developers: start with the [developer guide](docs/developer/README.md)** — architecture, local setup, conventions, testing, release, security, database, configuration, mobile app and troubleshooting.

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
| **Org chart** | Multi-tenant org provisioning -- instant, via "Sign up with Google/Microsoft" (`/create_org`), auto-approved onto a 30-day free trial (`TRIAL_DURATION_DAYS` in `blueprints/trial_billing.py`), no documents or payment upfront (see `GOOGLE_OAUTH_CLIENT_ID`/`MICROSOFT_OAUTH_CLIENT_ID` in `.env.example`) |
| **Employee portal** | Self-service: attendance, payslips, leaves, profile, QR code |
| **Mobile app** | React Native app for employee check-in/out |

---

## Prerequisites

- Python 3.14 (CI and production; 3.12 also works for development)
- PostgreSQL 16+
- `cmake`, `dlib` build tools (for face recognition — see Containerfile for the full list)
- Node.js 18+ (for mobile app only)

> **Tip:** The easiest way to run everything locally is with Podman. Skip to [Podman quick start](#podman-quick-start).

---

## Local development setup

### 1. Clone and create a virtual environment

The code lives at **https://github.com/Nithin-5310v/HRZEST-2.0**. Day-to-day work
branches off `develop` (see [Contributing](#contributing)).

```bash
git clone https://github.com/Nithin-5310v/HRZEST-2.0.git
cd HRZEST-2.0
git checkout develop
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Mac/Linux:
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements-dev.txt` adds pytest, flake8 and the other tools the tests and
CI use; leave it out on a server.

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
python run_dev.py
```

`run_dev.py` sets development defaults (`APP_ENV=development`, so cookies work
over plain HTTP) and prints the local URLs. It serves `http://127.0.0.1:5000`,
or HTTPS if `cert.pem`/`key.pem` exist (`python scripts/generate_cert.py`
makes a self-signed pair). The first run creates all database tables.

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

Or, against your own Postgres instance, set the connection variables in your shell or `.env`.
The suite creates and empties its own database, `att_test` by default; to run two
checkouts side by side give each its own name with `HRZEST_TEST_DB` (it must
contain "test"). `-n 4` runs in parallel, one database per worker:

```bash
DB_HOST=127.0.0.1 DB_USER=postgres DB_PASS=yourpass HRZEST_TEST_DB=att_test python -m pytest tests/ -n 4
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

Production (https://hrzest.com) runs gunicorn under systemd on an AWS EC2
host. Nobody deploys by hand: releases go through the Jenkins pipeline in
[`Jenkinsfile`](Jenkinsfile) (setup and day-to-day use:
[ci/jenkins/README.md](ci/jenkins/README.md)):

1. Open a pull request into `develop`. GitHub Actions runs lint, tests and
   the security scans; once it's merged, Jenkins deploys `develop` to the
   development copy (`http://localhost:5050` on the Jenkins PC).
2. Open a pull request from `develop` into `main` and merge it with
   **Create a merge commit**. Jenkins only offers production for a merged
   pull request with passing checks, never a direct push to `main`.
3. Approve the build in Jenkins. That moves the `production` branch to the
   commit, and the server pulls and deploys it by itself
   (`scripts/ec2_autodeploy.sh`).

Server hardening steps are in
[docs/SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md). Before building the
mobile app for release, point `mobile/src/config.js` at the production URL.

### Self-hosting with containers

The repo can also run as containers on your own server (`compose.prod.yaml`):
set every `.env` variable (especially `SECRET_KEY`, `ENCRYPTION_KEY`,
`ADMIN_PASSWORD`, `APP_URL`, `ALLOWED_ORIGINS`), point DNS at the server, run
`./init-letsencrypt.sh` for a TLS certificate, then `./deploy.sh`.

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

- **Blueprints** — all routes live in `blueprints/` (37 modules and packages, registered in order by `blueprints/__init__.py`). `app.py` is a thin entry point: the request hooks live in `app_setup/` (imported in a fixed order), migrations in `migrations/`.
- **CSP** — Content-Security-Policy is generated dynamically per-response. Inline event handlers are sha256-hashed at render time; no `'unsafe-inline'` is used.
- **Multi-tenancy** — each company has its own URL path, `https://<server>/<company>/...`, and its own PostgreSQL schema within the shared database. `utils/tenant_routing.py` (`TenantPrefixMiddleware`) finds the company from the first path segment and `app_setup/tenant.py`'s `_resolve_tenant()` binds the request and session to it; see [docs/developer/architecture.md](docs/developer/architecture.md#which-company-a-request-belongs-to). `/create_org` self-signup is open by default (Turnstile-protected when `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` are set), with pricing tiers enforced from `utils/plan_limits.py`.
- **Platform admin console** — `/super_admin` (`blueprints/platform_admin/`), a cross-tenant operator panel (list all companies, change plan, suspend/reactivate) with its own login identity in `att_master.platform_admins`, separate from any tenant's `admin_users`. No account is seeded automatically — run `python scripts/seed_platform_admin.py --username <user> --email <email>` once per deployment. It asks for the password twice (or reads it from `--password-stdin`), refuses to change an existing account unless you add `--reset`, and a reset signs out every console session opened with the old password.
- **Rate limiting** — `flask-limiter` with an in-memory backend (no Redis in this deployment — counters are per-worker, not shared across `GUNICORN_WORKERS`).

---

## Security notes for operators

Read [SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md) before deploying. The short version:
set `HONEYPOT_REPORT_SECRET`, configure `BACKUP_ENCRYPTION` (backups refuse to write plaintext),
run `scripts/host_hardening.sh` on a new host, make sure the host preserves real client IPs
(pasta networking), and roll liveness out as `LIVENESS_MODE=warn` before `enforce`.

## Tech stack

- **Backend:** Python 3.14, Flask 3, PostgreSQL 16, Gunicorn
- **Auth:** bcrypt, WebAuthn (passkeys), session-based admin auth, Bearer token API auth
- **Security:** CSRF (custom), CSP (dynamic sha256), HSTS, rate limiting, Fernet PII encryption
- **Security hardening:** tamper-evident audit log (hash chain + off-box anchors), least-privilege DB roles, active liveness + replay/spoof detection on check-in, correlation detections, retention/erasure/consent, signed-image deploys -- see [SECURITY_HARDENING.md](docs/SECURITY_HARDENING.md) for what each does **and does not** cover
- **Face recognition:** `face_recognition` (dlib-based)
- **Mobile:** React Native (Expo)
- **DevOps:** Podman, nginx, Let's Encrypt, GitHub Actions CI/CD

---

## Contributing

1. Clone https://github.com/Nithin-5310v/HRZEST-2.0 and branch off `develop`
   (`git checkout -b fix/short-description develop`)
2. Copy `.env.example` to `.env` and configure for local dev
3. Run `python -m pytest tests/ -n 4` — all tests must pass
4. Open a pull request against **`develop`**; never push to `main` directly
   (see [Production deployment](#production-deployment))

**Never commit** `.env`, `cert.pem`, or `key.pem`.
