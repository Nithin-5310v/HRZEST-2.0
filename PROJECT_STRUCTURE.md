# 🏢 HRzest.com — Architecture & Structure Guide

This document outlines the modular directory structure and architectural layers of HRzest.com.

---

## 📐 Project Directory Layout

```
employee-attendance/
├── app.py                      # Core Flask application factory & middleware
├── wsgi.py                     # WSGI production entry point
├── run_dev.py                  # Local development server runner
├── database.py                 # Unified DB manager (PostgreSQL + SQLite fallback)
├── extensions.py               # Shared extensions (Limiter, Logger, Redis, Security WAF)
├── gunicorn.conf.py            # Production Gunicorn WSGI server configuration
│
├── blueprints/                 # Modular Flask Route Controllers (21 blueprints, all routes — app.py has none)
│   ├── admin_views.py          # Admin settings, dashboard, analytics
│   ├── ai_hrms.py              # AI-assisted candidate screening & HR helpdesk
│   ├── attendance.py           # Shift scheduling, breaks, time tracking
│   ├── auth.py                 # Multi-role authentication & WebAuthn biometric login
│   ├── billing.py              # Per-employee billing & pricing tier calculations
│   ├── core.py                 # Home page, CSP reporting, session-risk stream, token REST API
│   ├── daily_report.py        # Automated nightly email reports (APScheduler)
│   ├── documents.py            # Document upload & verification with ClamAV
│   ├── email_blast.py          # Broadcast emails & announcement dispatch
│   ├── employee_portal.py     # Employee self-service dashboard & profile management
│   ├── employees.py            # Employee CRUD, photo/QR code management
│   ├── health.py                # /healthz endpoint for Podman/Nginx health checks
│   ├── leave.py                 # Leave applications, approvals, & comp-offs
│   ├── notifications.py        # In-app notifications & read receipts
│   ├── onboarding.py            # Automated onboarding workflows & checklists
│   ├── org.py                   # Multi-company setup & organization management
│   ├── payroll.py               # Salary structure, payslips, & bulk email distribution
│   ├── performance.py           # KPI metrics, reviews, & appraisals
│   ├── platform_admin.py       # SaaS Platform Super Admin console (/super_admin)
│   └── tickets.py               # HR helpdesk ticketing & resignation requests
│
├── utils/                      # Core Business Logic & Security Helpers
│   ├── ai_assistant.py         # Employee AI Chat assistant (n8n / Claude)
│   ├── async_writer.py         # Non-blocking async DB logger
│   ├── auth.py                 # Password hashing (bcrypt) & lockout mechanisms
│   ├── clamav.py               # Uploaded document malware scanner
│   ├── device_risk.py          # Network & device risk posture evaluation
│   ├── email.py                # SMTP email dispatcher (any provider)
│   ├── geo.py                  # Geofenced check-in distance math (Haversine)
│   ├── helpers.py              # Cryptographic helper functions & DB context wrappers
│   ├── mfa.py                  # Multi-factor TOTP authentication logic
│   ├── pii.py                  # Fernet AES-256 PII encryption/decryption at rest
│   ├── salary.py               # Monthly salary calculation & loss-of-pay engine
│   ├── security_logs.py        # Real-time security alert dispatcher (Webhooks)
│   ├── session_risk.py        # High-risk session evaluation & auto-invalidation
│   ├── waf.py                  # SQL-injection/XSS shape detector & IP auto-banner
│   └── webauthn.py             # FIDO2 / WebAuthn biometric attestations
│
├── templates/                  # Server-Rendered Jinja2 HTML5 Views
│   ├── admin.html              # Main Admin Dashboard
│   ├── admin_base.html         # Admin Layout Template
│   ├── admin_login.html        # Unified Login View
│   ├── checkin.html            # Kiosk Face / QR Check-in Page
│   ├── compliance_center.html  # Compliance Overview
│   ├── employee_portal.html    # Employee Portal
│   ├── employees.html          # Employee Directory & Shift Management
│   ├── pricing.html            # Product Tiers & Pricing Page
│   ├── settings.html           # System Settings Page
│   └── super_admin_dashboard.html  # Platform Admin Console
│
├── static/                     # Static Web Assets (CSS, JS, Fonts)
│   ├── darkmode.min.js         # Theme Switcher
│   ├── jsQR.min.js             # Client-side QR Scanner Engine
│   ├── shared.min.css          # Design Tokens & Master CSS Utilities
│   ├── tabler-icons.min.css    # UI Icon System
│   └── toast.js / toast.css    # Toast Alert Notifications
│
├── tests/                      # Pytest Test Suite
├── Containerfile                # Container definition (Python 3.13-slim + non-root appuser), built by Podman
├── compose.yaml                # Production Podman Compose orchestration
├── deploy.sh                   # One-command automated VPS deployment script
├── setup_env.py                # Automated local .env generator
├── nginx.conf                  # Production Reverse Proxy & SSL Configuration
└── Procfile                    # Cloud PaaS entrypoint (Render / Railway / Heroku)
```

---

## 🏛️ Layer Responsibilities

1. **Routing Layer (`blueprints/`)**: Each module handles a single domain. Endpoints enforce role authorization (`@admin_required`, `@role_required`) and input validation before delegating to helper services.
2. **Security & Logic Layer (`utils/`)**: Encapsulates encryption, WebAuthn, WAF scanning, salary logic, and malware checks cleanly outside of HTTP handler functions.
3. **Database Abstraction (`database.py`)**: Uses PostgreSQL connection pooling in production, with seamless fallback to SQLite mode when running standalone/demo.
4. **Presentation Layer (`templates/` + `static/`)**: Server-rendered HTML pages powered by a cohesive design system using HSL color tokens, dark mode support, and micro-animations.

## Security modules added by the hardening work

| Module | Purpose |
|---|---|
| `utils/client_ip.py` | Never-ban list, trusted-proxy hops, shared-IP-aware rate-limit key |
| `utils/honeypot_report.py` | HMAC-signed honeypot -> app report channel (stdlib only) |
| `migrations/audit_integrity.py`, `utils/audit_chain.py`, `utils/audit_jobs.py` | Hash-chained audit tables, verifier, scheduled anchors |
| `utils/liveness.py`, `utils/punch_integrity.py` | Active liveness, challenge store, replay/mock-location/office-network policy, punch evidence |
| `utils/detections.py` | Correlation rules over security_events / audit_logs / punch_evidence |
| `utils/privacy.py`, `utils/privacy_jobs.py`, `blueprints/privacy.py` | Retention, subject access, erasure, consent ledger |
| `scripts/provision_db_roles.py` | One-time split of owner / runtime database roles |
| `scripts/verify_audit_chain.py`, `scripts/rotate_encryption_key.py` | Chain verification (+ anchors), Fernet key rotation |
| `scripts/backup_db.sh`, `scripts/restore_test.sh` | Encrypted offsite backups; restore proof |
| `scripts/host_hardening.sh`, `scripts/deploy_verified_image.sh`, `compose.image.yaml` | Host firewall/NAT/podman setup; signed-image deploy by digest |
