# HRzest — Project Map

HRzest is a multi-company HR platform: attendance (QR, face, GPS), leave, payroll, onboarding, performance and billing. One **Flask** server serves the website and the JSON API; a **PostgreSQL** database holds one schema per company; an **Expo / React Native** app talks to the same API.

This page splits the project into blocks, saying where each block lives and what it does. It's a map only — nothing here changes how the code is laid out.

```text
                ┌──────────────────────────┐      ┌───────────────────────────┐
  Browser  ───► │  UI: templates/ (Jinja2)  │      │  Mobile app: mobile/      │
                │  CSS + JS: static/        │      │  (Expo / React Native)    │
                └────────────┬─────────────┘      └─────────────┬─────────────┘
                             │  HTML pages + AJAX               │  JSON + Bearer token
                             ▼                                  ▼
                ┌───────────────────────────────────────────────────────────┐
                │  Back end (Flask): app.py · wsgi.py · blueprints/ · utils/ │
                │  API: /api/... routes (193) inside blueprints/            │
                │  Multi-company routing: /<company>/... (tenant_routing)   │
                └────────────────────────────┬──────────────────────────────┘
                                             ▼
                ┌───────────────────────────────────────────────────────────┐
                │  Database (PostgreSQL): att_master + one schema per company│
                │  database.py · migrations/                                │
                └───────────────────────────────────────────────────────────┘
```

**Contents:** [Folder layout by block](#folder-layout-by-block) · [1 UI](#1-ui--pages) · [2 CSS](#2-css--styling) · [3 Front-end JavaScript](#3-front-end-javascript) · [4 Back end](#4-back-end-flask) · [5 API](#5-api) · [6 Database](#6-database) · [7 Multi-company](#7-multi-company-tenancy) · [8 Mobile app](#8-mobile-app) · [9 Background jobs & integrations](#9-background-jobs--integrations) · [10 Security](#10-security) · [11 Tests & QA](#11-tests--qa) · [12 Deployment & operations](#12-deployment--operations) · [13 Docs](#13-documentation) · [Where do I change…?](#where-do-i-change)

---

## Folder layout by block

Every folder and top-level file in the repo, grouped by the block it belongs to. The sections after this one explain each block in detail.

**See it this way in VS Code:** *File → Open Workspace from File… → `HRZEST.code-workspace`*. The Explorer then lists the blocks in this order (1. UI, 2–3. CSS & JavaScript, 4–5. Back end & API, …), hides caches and virtual environments, and keeps the root files under **HRZEST** at the bottom. Nothing moves on disk.

```text
HRZEST/
│
│ ═══ 1. UI — pages ═══════════════════════════════════════════════════════════════
├── templates/                      77 Jinja2 pages: public site, sign-up, login, admin,
│   │                               HR, employee self-service, Platform Admin
│   ├── _*.html                     shared pieces (registration checklist, chat widget,
│   │                               login promo, employee sidebar)
│   └── soc/                        SOC console pages
│
│ ═══ 2. CSS  +  3. Front-end JavaScript ══════════════════════════════════════════
├── static/
│   ├── *.css, *.min.css            shared.min.css, enterprise-ui.css, landing_v2.css,
│   │                               auth_split.css, chat.css, toast.css, …
│   ├── *.js, *.min.js              toast.js, chat.js, darkmode.min.js, landing_v2.js,
│   │                               chart.umd.min.js, jsQR.min.js, …
│   ├── js/                         i18n.js
│   ├── fonts/, icons/              icon font subset, image icons
│   └── company_logos/, qrcodes/,   created at runtime (uploads), not in git
│       employee_docs/, id_card_templates/
├── soc_ui/                         SOC console CSS + JavaScript
├── vendor/tabler-icons/            full icon set (source of the subset in static/)
├── assets/                         marketing images
├── minify_static.py                rebuilds static/*.min.{css,js}
│
│ ═══ 4. Back end  +  5. API ══════════════════════════════════════════════════════
├── wsgi.py                         entry point: registers routes, middleware, scheduled jobs
├── app.py                          security headers/CSP, CSRF, sessions, which company a
│                                   request belongs to, template context, error pages
├── extensions.py                   Flask app, rate limiter, logging, security events
├── run_dev.py                      local development server
├── blueprints/                     34 route modules — the web pages AND the /api/... JSON API
│                                   org.py, auth.py, employees.py, employee_portal.py,
│                                   attendance.py, leave.py, payroll.py, billing.py,
│                                   platform_admin.py, soc.py, webhooks.py, …
├── utils/                          ~60 helpers: salary, billing, attendance, email, AI,
│                                   multi-company routing (tenant_routing.py), security
├── qr_generator.py                 signed employee QR codes
├── seed_platform_admin.py, setup_env.py, factory_reset.py, generate_cert.py
│
│ ═══ 6. Database  +  7. Multi-company ═══════════════════════════════════════════
├── database.py                     connection pools, company schemas, owner/runtime roles
├── migrations/                     platform tables (master.py), per-company tables
│                                   (core_tables.py, column_migrations.py), audit chain,
│                                   SOC tables, runner
│
│ ═══ 8. Mobile app ═══════════════════════════════════════════════════════════════
├── mobile/
│   ├── App.js, index.js            start-up and logged-out screens
│   ├── app.json, eas.json, metro.config.js, babel.config.js, package.json
│   ├── shims/                      Expo Go workaround
│   ├── assets/                     app icon, splash, logo
│   └── src/
│       ├── api/                    client.js — every server call, token, /<company>/ prefix
│       ├── screens/                admin/ (21), employee/ (24), manager/ (2),
│       │                           login, sign-up, launch, kiosk scanner, ID card
│       ├── components/             reusable pieces: admin/, attendance/, employee/, …
│       ├── navigation/             drawers and tabs per role
│       ├── store/                  AuthContext (session + company), ThemeContext
│       ├── theme/, constants/      website look, colours, spacing, typography
│       ├── utils/                  secure storage, offline punches, notifications, camera
│       ├── data/                   static policy / notification content
│       └── config.js               server address
├── desktop_agent/                  office Wi-Fi check used by the employee portal
│
│ ═══ 9. Background jobs & integrations ═════════════════════════════════════════
│     (inside wsgi.py, blueprints/ and utils/ — no folder of their own)
│
│ ═══ 10. Security ═════════════════════════════════════════════════════════════════
│     (inside app.py, utils/ and blueprints/ — see section 10; SOC console UI in soc_ui/)
│
│ ═══ 11. Tests & QA ═══════════════════════════════════════════════════════════════
├── tests/                          ~160 pytest files (back end, API, pages)
├── tests_e2e/                      Playwright browser tests
├── qa_audit/                       QA audit baselines
├── pytest.ini, requirements-dev.txt, docker-compose.test.yml
│
│ ═══ 12. Deployment & operations ════════════════════════════════════════════════
├── scripts/                        EC2 auto-deploy, backups, host hardening, DB roles,
│                                   audit-chain check, key rotation
├── .github/                        CI workflows, Dependabot
├── observability/                  logs + alerts (Alloy → Loki → Grafana)
├── nginx/, nginx.conf, nginx.conf.example, init-letsencrypt.sh
├── Containerfile, compose*.yaml, entrypoint.sh, deploy.sh, gunicorn.conf.py, Procfile
├── requirements.txt, requirements.lock, DEPENDENCIES.txt, .python-version
│
│ ═══ 13. Docs ══════════════════════════════════════════════════════════════════════
└── README.md, PROJECT_STRUCTURE.md, SECURITY.md, SECURITY_HARDENING.md, SOC.md,
    RESTORE.md, MODERNIZATION.md, QA_AUDIT.md, HRzest_Project_Overview.pptx
```

---

## 1. UI — pages

**Where:** `templates/` — 77 server-rendered Jinja2 pages. The back end fills them with data and sends finished HTML.

| Area | Pages |
| --- | --- |
| Public site & sign-up | `landing.html` (home), `create_org.html` (one-step sign-up + inline email code), `create_org_verify_otp.html` (Google/Microsoft sign-up code), `create_org_pay.html`, `create_org_setup_trial.html`, `create_org_upload_documents.html`, `create_org_pending.html`, `create_org_status.html` |
| Login & account security | `admin_login.html` (one login page for admin, HR, manager, employee), `mfa_verify.html`, `step_up_mfa.html`, `admin_mfa_required.html`, `*_forgot_password.html`, `*_reset_password.html`, `force_change_pin.html`, `security_lockout.html` |
| Company admin dashboard | `admin_base.html` (layout: sidebar, banners, registration checklist, chat widget), `admin.html` (dashboard), `employees.html`, `employee_detail.html`, `edit_employee.html`, `today_attendance.html`, `bulk_attendance.html`, `monthly_report.html`, `leave_*.html`, `overtime.html`, `salary.html`, `salary_report.html`, `payroll_settings.html`, `admin_payslips.html`, `onboarding*.html`, `offer_letter*.html`, `performance*.html`, `analytics.html`, `org_chart.html`, `activity_log.html`, `admin_tools.html`, `settings.html`, `profile.html`, `company_details.html`, `company_verification.html`, `seat_checkout.html`, `pay_overdue_bill.html`, `id_card_template_editor.html` |
| HR dashboard | `hr_dashboard.html` |
| Employee self-service | `employee_portal.html`, `employee_profile.html`, `employee_attendance_detail.html`, `my_compoff.html`, `my_onboarding.html`, `my_performance.html` |
| Platform Admin (SaaS operator) | `super_admin_*.html` — dashboard, company profiles, applications, duplicate alerts, audit log, support tickets, SOC view |
| SOC console | `soc/console.html`, `soc/login.html` |
| Shared pieces (`_` prefix) | `_registration_checklist.html`, `_admin_chat_widget.html`, `_auth_promo.html`, `_employee_sidebar.html` |

**Kiosk check-in** (`/<company>/checkin`, no login) is `index.html`, rendered by `blueprints/core.py`'s `checkin_page()`.

---

## 2. CSS & styling

| What | Where |
| --- | --- |
| Design tokens + shared utilities | `static/shared.min.css` |
| Admin dashboard look | `static/enterprise-ui.css` |
| Public site (home, sign-up) | `static/landing_v2.css`, `static/auth_split.css` (login/sign-up with promo panel) |
| Employee sidebar | `static/employee_sidebar.css` |
| Chat (WhatsApp-style) | `static/chat.css` |
| Toasts, animations | `static/toast.css`, `static/animate-core.min.css` |
| Icons | `static/tabler-icons.min.css` (subset) + `static/fonts/`, full set in `vendor/tabler-icons/` (`scripts/regenerate_icon_subset.py` rebuilds the subset) |
| SOC console | `soc_ui/soc.css`, `soc_ui/login.css` |
| Page-specific styles | A `<style>` block inside the page itself — 71 of 77 templates have one |

**Rules worth knowing**
- The page **Content-Security-Policy** (set in `app.py`) blocks `style="..."` attributes created by JavaScript at runtime. Use CSS classes, or `element.style.setProperty(...)`. Server-rendered `style=""` attributes are allowed (they're hashed into the CSP).
- Dark mode: admin pages add `body.dark-mode` (`static/darkmode.min.js`); give new components a `body.dark-mode …` rule.

---

## 3. Front-end JavaScript

Plain JavaScript (no framework, no build step).

| What | Where |
| --- | --- |
| Page logic | `<script>` blocks inside each template |
| Shared scripts | `static/toast.js` (notifications, replaces `alert()`), `static/chat.js` (chat thread), `static/darkmode.min.js`, `static/a11y-tabs.js`, `static/employee_sidebar.js`, `static/landing_v2.js`, `static/js/i18n.js` |
| Libraries | `static/chart.umd.min.js` (charts), `static/jsQR.min.js` (QR scanning in the browser), `static/animate-core.min.js` |
| SOC console | `soc_ui/soc-core.js`, `soc-views.js`, `soc-analysis.js` |

**Rules worth knowing**
- `app.py` injects a CSRF token into every page and every `fetch()` POST; inline `<script>` tags get a CSP nonce automatically.
- Put user-supplied text into the page with `textContent`, never `innerHTML` (the dashboard audit test `tests/test_dashboard_wiring.py` checks this).
- `minify_static.py` regenerates the `.min` files from their sources — run it after editing e.g. `static/shared.css`.

---

## 4. Back end (Flask)

**Language:** Python 3.13 (`.python-version`). **Framework:** Flask 3, served by Gunicorn.

| Layer | Where | What it does |
| --- | --- | --- |
| Entry points | `wsgi.py` (production / `python wsgi.py` locally), `run_dev.py` | Builds the app, registers every blueprint, adds the multi-company and proxy middleware, starts the scheduler |
| App core | `app.py` | Security headers + CSP, CSRF, session rules, which company a request belongs to (`_resolve_tenant`), template context (company settings, billing banners, registration checklist), error pages. No routes of its own. |
| Shared objects | `extensions.py` | The Flask `app`, rate limiter, logging, security-event logger, Redis |
| Routes ("blueprints") | `blueprints/` — 34 modules, one per area | See the table below |
| Business logic & helpers | `utils/` — ~60 modules | Calculations, integrations and security helpers called by the routes |
| One-off tools | `qr_generator.py` (signed employee QR codes), `seed_platform_admin.py`, `setup_env.py`, `factory_reset.py`, `generate_cert.py` | |

**Blueprints by area**

| Area | Modules |
| --- | --- |
| Sign-up & companies | `org.py` (one-step sign-up, company provisioning, dashboard checklist, company details, KYC documents), `oauth_signup.py` (Google / Microsoft sign-up), `company_profile.py` |
| Login & accounts | `auth.py` (login, MFA, password reset, WebAuthn) |
| Admin dashboard & settings | `admin_views.py`, `hr_dashboard.py`, `policies.py`, `email_blast.py` |
| Employees | `employees.py` (CRUD, photos, QR / ID cards), `employee_portal.py` (self-service + check-in APIs), `documents.py`, `onboarding.py`, `performance.py` |
| Attendance & leave | `attendance.py` (check-in/out, shifts, breaks, reports), `leave.py` (leave, holidays, overtime, comp-off, resignation), `daily_report.py` |
| Payroll | `payroll.py` (salary, payslips), `disbursement.py` (salary payouts) |
| Billing | `billing.py`, `seats.py`, `auto_debit.py`, `billing_dunning.py`, `trial_billing.py`, `webhooks.py` (Razorpay callbacks) |
| Platform Admin (SaaS operator) | `platform_admin.py` (`/super_admin`), `soc.py` (`/soc` security console) |
| Support & notifications | `tickets.py`, `notifications.py`, `ai_hrms.py` |
| Infrastructure | `core.py` (home page, CSP reports, mobile token API, web-session bridge), `health.py` (`/healthz`), `seo.py`, `privacy.py`, `honeypot_routes.py` |

**Main `utils/` groups**
- **Money:** `salary_utils.py`, `plan_limits.py`, `razorpay_utils.py`, `payment_history.py`, `payout_utils.py`, `receipt_pdf.py`
- **Attendance:** `attendance_utils.py`, `leave_utils.py`, `geo.py`, `face_utils.py`, `liveness.py`, `punch_integrity.py`, `config.py`
- **Email, chat & files:** `email_utils.py` (queued email), `chat_utils.py`, `push_utils.py`, `storage.py`, `csv_export.py`, `task_queue.py`
- **AI:** `ai_client.py`, `ai_assistant.py`, `ai_helpdesk.py`, `ai_resume_parser.py`, `ai_interview_evaluator.py`, `ai_attrition_analytics.py`
- **Shared:** `helpers.py` (company settings, uploads, `tpath()`, `static_url()`), `tenant_routing.py`, `request_guard.py`
- **Security:** see block 10.

---

## 5. API

The API is part of the back end: **193** of the server's ~506 routes start with `/api/`. They return JSON and are used by the mobile app and by the web pages' own `fetch()` calls. Every `/api/...` route is also reachable as `/api/v1/...`.

| Group | Examples | Used by |
| --- | --- | --- |
| Employee & check-in | `/api/employee/...` (57 routes): login, profile, attendance, QR/face check-in, leave, payslips | Mobile app (employee), kiosk |
| Admin | `/api/admin/...`, `/api/employees`, `/api/settings/...`, `/api/payroll/...`, `/api/shifts/...` | Mobile app (admin), dashboard pages |
| Registration checklist | `/api/admin/registration`, `/api/admin/registration/company`, `/api/admin/registration/documents` | Mobile app |
| Sign-up | `/api/create_org`, `/api/create_org/verify_otp` (creates the company, returns a login token), `/api/create_org/resend_otp` | Mobile app |
| Login | `/api/login`, `/api/login/verify` | Mobile app |
| HR dashboard | `/api/hr_dashboard/...` | HR dashboard page |
| Billing | `/api/billing/...`, `/api/seats/...`, `/api/auto_debit/...`, `/webhooks/<provider>` | Checkout pages, Razorpay |

**How the API knows who and which company**
- **Who:** the mobile app sends `Authorization: Bearer <token>` (tokens live in each company's `api_tokens` table, decorators `@api_required` / `@api_role_required` in `utils/auth.py`). Web pages use the session cookie + CSRF token.
- **Which company:** the first part of the URL — `/<company>/api/...` (see block 7). The mobile app adds it automatically (`mobile/src/api/client.js`).

---

## 6. Database

**PostgreSQL 16.** One database, split into schemas:

| Schema | What's in it |
| --- | --- |
| `att_master` | The **platform registry** shared by all companies (24 tables): `tenants` (every company and its URL), `tenant_applications` (sign-ups, KYC status), billing (`payment_orders`, `auto_debit_mandates`, `monthly_invoices`, `seat_topup_orders`, `billing_config`), `chat_messages`, `tenant_support_tickets`, `platform_admins`, `leads`, `page_views`, SOC tables (`soc_users`, `soc_cases`, …), `scheduler_job_runs` |
| `att_<company>` | **One per company** (~60 tables): `employees`, `attendance`, `admin_users`, `company_settings`, `shifts`, `leave_requests`, `payroll_runs`, `salary_config`, `onboarding_*`, `performance_*`, `tickets`, `notifications`, `api_tokens`, `audit_logs`, `security_events`, `punch_evidence`, … |
| Default schema (`DB_NAME`) | The server's default company — used when a URL carries no company |

| Code | What it does |
| --- | --- |
| `database.py` | Connection pools, `get_db_connection()` (current company), `get_master_db()`, `get_tenant_db(schema)`, `create_tenant_schema()`, the optional owner/runtime role split (`DB_OWNER_USER`), and a SQLite fallback for stand-alone demos |
| `migrations/master.py` | `att_master` tables |
| `migrations/core_tables.py`, `column_migrations.py` | Per-company tables and later column changes |
| `migrations/audit_integrity.py` | Tamper-evident (hash-chained) audit tables |
| `migrations/soc_tables.py`, `seed.py` | SOC tables, default data |
| `migrations/runner.py`, `orchestration.py` | Runs migrations once per code version at startup, for every company schema |

Backups and restore: `scripts/backup_db.sh`, `scripts/restore_test.sh`, `RESTORE.md`. Encryption-key rotation for personal data: `scripts/rotate_encryption_key.py`.

---

## 7. Multi-company (tenancy)

Every company has its own **URL** and its own **database schema**:

```text
https://<server>/<company>/login   ──►  tenants.subdomain = "<company>"  ──►  schema att_<company>
```

- `utils/tenant_routing.py` reads the first part of the URL, looks the company up in `att_master.tenants`, and hides that prefix from the rest of the app.
- `app.py`'s `_resolve_tenant()` binds the session to that company; switching to another company's URL clears the session.
- Sign-up makes the URL from the company name (`blueprints/org.py`, `_slug_for_company()`: "Acme Pvt. Ltd." → `acme-pvt-ltd`, plus `-2`… if taken).
- In templates, build links with `tpath('/path')` so they stay inside the current company.

---

## 8. Mobile app

**Where:** `mobile/` — Expo SDK 57, React Native 0.86, React 19. Admin, HR, manager and employee in one app. It runs in **Expo Go** for development (see `mobile/.env.example` for the server address, `EXPO_PUBLIC_API_BASE_URL`).

| Part | Where |
| --- | --- |
| Start-up & navigation | `App.js`, `index.js`, `src/navigation/` (admin / employee / manager drawers and tabs) |
| Screens | `src/screens/` — `admin/` (21), `employee/` (24), `manager/` (2), plus login, sign-up, launch, kiosk scanner, ID card, notifications |
| Components | `src/components/` — `admin/` (dashboard cards, registration checklist), `attendance/`, `employee/`, `holidays/`, `tickets/`, `policies/`, `profile/`, … and `SafeAreaView.js` |
| API calls | `src/api/client.js` — every server call, the Bearer token, and the `/<company>/` prefix |
| Login state | `src/store/AuthContext.js` (session, company, app lock), `src/store/ThemeContext.js` |
| Look | `src/theme/webAuthStyle.js` (website look for login / sign-up), `src/constants/` (colours) |
| Device features | `src/utils/` — secure storage, offline punch queue, push & local notifications, camera hand-off, device id, crash reporting |
| Config | `src/config.js` (server address), `app.json`, `eas.json`, `metro.config.js` (+ `shims/` workaround for Expo Go) |
| Tests | `__tests__/` folders next to the code (Jest, `npx jest` in `mobile/`) |

---

## 9. Background jobs & integrations

| What | Where |
| --- | --- |
| Scheduled jobs (APScheduler, started in `wsgi.py`): daily attendance report, auto-debit billing, billing reconciliation, dunning, salary disbursement prep, trial expiry + reminder, security detections, audit anchor + verify, privacy retention purge | `wsgi.py`, the matching `blueprints/` / `utils/` module, `utils/job_runs.py` (runs each job once across processes) |
| Worker threads: email queue, company-document verification | `utils/email_utils.py`, `utils/document_verification.py` |
| Heavy jobs (PDF / Excel) | `utils/task_queue.py` (Redis + RQ when configured) |
| Payments | Razorpay — `utils/razorpay_utils.py`, `blueprints/billing.py`, `webhooks.py` |
| Email | SMTP — `utils/email_utils.py` |
| AI | Anthropic Claude — `utils/ai_client.py` |
| Sign in with Google / Microsoft | `utils/oauth_client.py`, `blueprints/oauth_signup.py` |
| Push notifications | Expo push — `utils/push_utils.py` |
| Bot protection | Cloudflare Turnstile — `utils/auth.py` |

---

## 10. Security

| Area | Where |
| --- | --- |
| Login, passwords, lockout, MFA, passkeys | `utils/auth.py`, `utils/totp.py`, `utils/login_otp.py`, `utils/webauthn_utils.py`, `blueprints/auth.py` |
| Web firewall, IP trust, session risk | `utils/waf.py`, `utils/client_ip.py`, `utils/session_risk.py`, `utils/request_guard.py` |
| Headers, CSP, CSRF | `app.py` |
| Attendance fraud (fake GPS, replay, liveness) | `utils/punch_integrity.py`, `utils/liveness.py` |
| Tamper-evident audit log | `migrations/audit_integrity.py`, `utils/audit_chain.py`, `utils/audit_jobs.py`, `scripts/verify_audit_chain.py` |
| Personal data (DPDP / GDPR) | `utils/privacy.py`, `utils/privacy_jobs.py`, `utils/dlp.py`, `blueprints/privacy.py` |
| Detection & response | `utils/detections.py`, `utils/alerts.py`, SOC console (`blueprints/soc.py`, `utils/soc_*.py`, `soc_ui/`, `SOC.md`) |
| Decoys | `blueprints/honeypot_routes.py`, `utils/honeypot.py`, `utils/honeypot_report.py` |

More detail: `SECURITY.md`, `SECURITY_HARDENING.md`.

---

## 11. Tests & QA

| What | Where | Run |
| --- | --- | --- |
| Back-end tests (~3,400) | `tests/` — one file per area; `tests/conftest.py` makes a test database per worker; `tests/signup_flow.py` drives sign-up | `python -m pytest -n 4` |
| End-to-end browser tests | `tests_e2e/` (Playwright) | see `tests_e2e/README.md` |
| Mobile tests | `mobile/**/__tests__/` (Jest + React Native Testing Library) | `npx jest` in `mobile/` |
| Dashboard wiring audit | `tests/dashboard_audit.py` + `tests/test_dashboard_wiring.py` (dead links, unescaped HTML, unlabeled fields…) | part of pytest |
| QA audit baselines | `qa_audit/`, `QA_AUDIT.md` | |

---

## 12. Deployment & operations

| What | Where |
| --- | --- |
| Live server (EC2) | systemd + Gunicorn (`gunicorn.conf.py`) behind nginx, PostgreSQL on the same host. It **pulls `main` automatically** once its CI checks pass: `scripts/ec2_autodeploy.sh`, `scripts/hrzest-autodeploy.service` / `.timer`, `scripts/install_ec2_autodeploy.sh` |
| Container route | `Containerfile` (Podman), `compose.yaml` (+ `compose.prod.yaml`, `compose.lowmem.yaml`, `compose.image.yaml`), `entrypoint.sh`, `deploy.sh`, `scripts/deploy_verified_image.sh` |
| Web server | `nginx/nginx.conf.template` (rendered by `init-letsencrypt.sh`), `nginx.conf`, `nginx.conf.example` |
| CI (GitHub Actions) | `.github/workflows/ci-cd.yml` (lint, Bandit, pytest, Semgrep, Podman build), `security.yml`, `dast.yml`, `deploy.yml` (SSH deploy job, currently disabled); `.github/dependabot.yml` |
| Host & backups | `scripts/host_hardening.sh`, `scripts/backup_db.sh`, `hrzest-backup.service` / `.timer`, `scripts/provision_db_roles.py` |
| Logs & monitoring | `observability/` (Grafana Alloy → Loki → Grafana, security alert rules) |
| Dependencies | `requirements.txt`, `requirements.lock` (hash-pinned), `requirements-dev.txt`, `DEPENDENCIES.txt`; `mobile/package.json` |
| Other | `desktop_agent/wifi_posture_agent.py` (office Wi-Fi check for the employee portal), `Procfile` (PaaS hosts) |

---

## 13. Documentation

`README.md` (setup) · `PROJECT_STRUCTURE.md` (this map) · `SECURITY.md` · `SECURITY_HARDENING.md` · `SOC.md` · `RESTORE.md` (backup restore) · `MODERNIZATION.md` · `QA_AUDIT.md` · `HRzest_Project_Overview.pptx`

---

## Where do I change…?

| I want to change… | Block | Start here |
| --- | --- | --- |
| How a page looks | UI + CSS | `templates/<page>.html` (its `<style>` block), shared look in `static/*.css` |
| A button or form behaviour on a page | Front-end JS | the page's `<script>` block, or `static/*.js` |
| What happens when a form is submitted | Back end | the route in `blueprints/<area>.py` |
| A calculation (salary, leave, billing) | Back end | `utils/salary_utils.py`, `utils/leave_utils.py`, `utils/plan_limits.py` |
| Company sign-up / registration checklist | Back end + UI | `blueprints/org.py`, `templates/create_org.html`, `templates/_registration_checklist.html` |
| A table or column | Database | `migrations/core_tables.py` / `column_migrations.py` (per company) or `migrations/master.py` (platform) |
| Something the mobile app calls | API + Mobile | the `/api/...` route in `blueprints/`, and `mobile/src/api/client.js` |
| A mobile screen | Mobile app | `mobile/src/screens/...`, components in `mobile/src/components/...` |
| A scheduled job | Background jobs | `wsgi.py` (schedule) + the job's module |
| Security headers / CSP | Security | `app.py` (`_security_headers`) |
| Deployment | Operations | `scripts/ec2_autodeploy.sh`, `.github/workflows/ci-cd.yml` |
