# HRZEST — QA & Architecture Audit

Branch: `qa-audit-fixes` · Started 2026-10-06 · Status: **Phase 4 in progress.** On 2026-10-07 qa-audit-fixes was fast-forwarded onto `security-fixes` (c689aae; that session's final run: 2,680 passed / 0 failed / 7 skipped). The previous uncommitted working tree is kept as `stash@{0}`, byte-identical to commit 31d8f4f.
Coverage baseline (2026-10-07, at 0d08d81, 4 xdist workers on isolated databases): **82%** line coverage; full suite 2,688 passed / 0 failed / 7 skipped in 47 min.

## 0. Owner decisions (2026-10-06)

1. **Scope:** High → Medium → Low. **Hotfixes first:** BUG-02 (fail loudly instead of falling back to SQLite in production), then BUG-01 together with BUG-30 (per-company, shift-aware rules). In Medium, do these first: attendance % (BUG-03/04), payslip mismatch (BUG-05), IST dates (BUG-17…27), API errors as HTML (ERR-01), and the **axios advisory (DEP-01)**. The large refactors are **not** in Phase 4 (see §6).
2. **Conflicts:** wait for `security-fixes` to finish, rebase onto it, and only then start Phase 4. Don't touch that branch's files before then. SEC-04/05/06 and the Razorpay part of TEST-01 stay with that session.
3. **Testing:** targeted tests for each fix; the full suite every 5–8 commits and at the end; fix TEST-02 early. **The full suite must be green before merge.**
4. **Load test:** against `att_test` only, run locally, with MFA and rate limiting disabled **only through environment variables of that one process**. No default or config change is committed, and I'll confirm afterwards that no config changed.
5. **NV answers:** the reply arrived with the template placeholders unfilled, so the safe defaults apply until the owner answers. NV-03: blank all external keys in tests regardless. NV-07/NV-08: keep. NV-09: assume a working-copy build is possible (fix SEC-01). NV-10/NV-11: no change to rounding or to the "Present = half day" rule.

Raw tool output is in `qa_audit/baseline/`. The helper scripts I wrote for this audit are in `qa_audit/tools/`.

---

## 1. Project map (Phase 1.1)

| Area | Contents |
|---|---|
| Entry points | `wsgi.py` (gunicorn `wsgi:application` via `entrypoint.sh` / `Procfile`), `app.py` (`python app.py` dev server), `run_dev.py` |
| Request flow | gunicorn (gthread, `workers=min(2·CPU+1, 8)`, 4 threads) → `ProxyFix` → `TenantPrefixMiddleware` (strips `/<company-slug>` into `SCRIPT_NAME`) → Flask app (`extensions.app`) → `before_request` hooks in `app.py` (perf timer, tenant resolve, billing lock, IP ban, WAF, session lifetime/idle, MFA enrolment, private-upload block, CSRF) → blueprint view → `after_request` (security headers/CSP nonce + CSRF injection, tenant session re-stamp) |
| Blueprints (31) | Route decorators per module: admin_views 50, employee_portal 37, leave 36, platform_admin 31, auth 30, payroll 28, attendance 28, onboarding 27, employees 25, org 24, core 18, performance 14, hr_dashboard 13, documents 13, company_profile 10, notifications 9, honeypot_routes 9, tickets 7, disbursement 7, policies 6, oauth_signup 4, seats 3, health 3, billing_dunning 3, auto_debit 3, ai_hrms 3, seo 2, daily_report 2, billing 2, webhooks 1, email_blast 1, which is **449 route decorators** plus auto-generated `/api/v1/*` aliases |
| utils (38 modules) | auth, login_otp, helpers (1,669 lines), email_utils, salary_utils, attendance_utils, leave_utils, face_utils, webauthn_utils, totp, session_risk, waf, tenant_routing, razorpay_utils, payment_history, plan_limits, storage, document_verification, ai_* (6), push_utils, alerts, dlp, honeypot, … |
| Database | PostgreSQL, one schema per tenant plus `att_master` (tenant registry, signup applications, platform admins, billing). About 72 tables, created by `migrations/{core_tables,column_migrations,master,orchestration,seed}.py`. Pool: `psycopg2` `ThreadedConnectionPool` (`database.py`) with an SQLite fallback |
| Background work | APScheduler with 7 nightly jobs (`wsgi.py`), an email-queue worker thread, a document-verification worker thread, and an async login-attempt writer (`utils/async_writer.py`) |
| Templates / static | 73 Jinja templates (largest: `employee_portal.html` at 212 KB), inline JS in 64 `<script>` blocks, `static/` (hand-written JS: a11y-tabs, employee_sidebar, i18n, landing_v2, toast; vendored minified chart/jsQR) |
| Mobile | `mobile/`: **Expo 57 / React Native 0.86 / React 19** (219 files), jest tests |
| Tests | 91 `tests/test_*.py` files, about 2,700 tests (pytest, pytest-randomly, one shared Postgres DB), plus `tests_e2e/` (Playwright, not run by default) |
| Scripts | `scripts/` (backup, lint fix, icon subset, onboarding-docs migration), `desktop_agent/` (WiFi posture agent), `setup_env.py`, `seed_platform_admin.py`, `factory_reset.py`, `generate_cert.py`, `minify_static.py`, `deploy.sh`, `init-letsencrypt.sh` |
| Config / deploy | `Containerfile`, `compose{,.prod,.lowmem}.yaml`, `docker-compose.test.yml`, `nginx/`, `gunicorn.conf.py`, `.env.example`, `.github/workflows/{ci-cd,deploy,security}.yml`, `observability/` |

## 2. Baseline metrics (Phase 1.2–1.3)

| Tool | Result (before) | Output |
|---|---|---|
| pytest (last full run, 2026-10-06) | 2,668 collected; **2,645 passed / 16 failed / 7 skipped**. 14 of the failures come from real Razorpay calls (TEST-01); 2 were caused by my own test fixture and were fixed and re-verified (405/405 re-run) | `tests/` |
| Coverage % (pytest-cov, full suite at 0d08d81, `-n 4`) | **82%** (19,702 statements, 3,541 missed); 2,688 passed / 0 failed / 7 skipped in 47 min. Lowest: task_queue 0%, ai_client 36%, razorpay_utils 42%, document_verification 49%, database.py 56% (see TEST-04) | `coverage_report.txt`, `pytest_cov.txt` (`coverage.json` is regenerable and not committed) |
| ruff (0.16.10 defaults; no project config) | 1,134 findings (390 auto-fixable) | `ruff.txt`, `ruff_stats.txt` |
| pylint blueprints utils | 2,597 messages, **score 8.45/10** | `pylint.txt` |
| mypy blueprints utils (`--ignore-missing-imports`) | 46 lines: 20 real type errors, 26 annotation notes | `mypy.txt` |
| bandit -r blueprints utils | 33 findings (0 High; 18 B608 = all verified safe fixed-fragment SQL; 12 B105 false positives); 113 `# nosec` | `bandit.txt/json` |
| bandit (CI flags `-ll -iii`, whole repo) | 0 issues (after the earlier MD5 fix) | — |
| pip-audit -r requirements.txt | **0 known vulnerabilities** | `pip_audit.txt` |
| npm audit (mobile) | **71 advisories: 55 high, 16 moderate, 0 critical** | `npm_audit_mobile.json` |
| radon cc | 4,664 blocks, **average complexity A (3.54)**; 33 blocks rated D/E/F, 6 rated F (≥41) | `radon_cc.txt` |
| radon mi | 160 A / 14 B / 16 C (the C files are the 7 biggest blueprints plus test files) | `radon_mi.txt` |
| vulture blueprints utils | 493 raw (452 "unused functions", nearly all decorator-registered routes); **1 real** unreferenced function plus 4 used only by tests | `vulture*.txt` |
| jscpd (py/js/markup/css, incl. tests) | **4.34% duplicated** (214 clones, 5,490 lines): markup 11.36%, css 8.94%, js 3.88%, python 1.0% | `jscpd/` |
| ESLint (eslint:recommended) static + inline template JS | 33 problems (1 real bug: duplicate object key; 3 extraction artifacts) | `eslint_browser.txt` |
| ESLint (recommended + react + react-hooks) mobile/src | 94 problems: 49 unused vars, 23 empty blocks, 18 hook-deps warnings, 4 useless escapes | `eslint_mobile.txt` |
| Structure scan | 16 files > 800 lines; 165 functions > 50 lines (88 without docstrings); 47 loops containing DB calls; 3 early-return-before-close paths | `structure.txt` |
| a11y heuristics (templates) | 261 form controls without a programmatic label, 13 icon-only buttons/links without `aria-label`, 1 `<img>` without `alt` | `a11y.txt` |
| Unguarded request parsing | 80 `int()/float()/fromisoformat()` calls on request input outside any `try` | `unguarded_int.txt` |

## 3. Method and limits (Phase 2)

- **Read in depth:** auth/session/MFA/tenancy code (carried over from the security review), `database.py`, `wsgi.py`, `gunicorn.conf.py`, `utils/config.py`, `utils/salary_utils.py`, the attendance check-in paths, the employee-portal payroll summary, the admin/HR dashboard analytics, leave request, onboarding save handlers, disbursement and billing jobs, error handlers, `Containerfile`, `.containerignore`, conftest.
- **Read selectively, guided by tools** (every hit verified by reading the code before it was registered): the rest of `blueprints/`, `utils/`, `migrations/`, the 73 templates (inline JS extracted and linted; a11y heuristics), `static/` JS, and `mobile/src` (ESLint, jscpd, npm audit).
- **Not yet done:** a browser run for console errors and broken links (Playwright, `tests_e2e/`), the load test, migration-lock measurement and coverage. All of these need the database or a running app and are scheduled after the shared-DB window. A line-by-line read of every one of about 37k Python lines, 73 templates and 219 mobile files was **not** done. Findings in those areas come from tools plus targeted reading, and new issues found during Phase 4 will be added here.

## 4. Issue register (Phase 3)

Sorted by severity, then effort (S < 1 h, M = 1–4 h, L > 4 h). "Status" is updated during Phase 4.
**Mechanical lint rule types** (import order, encoding headers, …) are one row per rule, with every location kept in the raw tool output. **Defects** have their own row per module, so each can be fixed and committed on its own.
Rows marked **[security-fixes]** are being handled by the other session on branch `security-fixes`. I won't touch them here, to avoid duplicate work and merge conflicts.

### 4.1 Confirmed issues

| ID | Category | Sev | File:line | Problem | Impact | Proposed fix | Effort | Status |
|---|---|---|---|---|---|---|---|---|
| BUG-02 | Correctness / resilience | High | database.py:567-574, 586-602 | If Postgres is unreachable when the pool is built (startup, or a rebuild after one failed `getconn` with 1 retry after 0.1 s), the worker **silently switches to a local SQLite file**. There's no `APP_ENV` guard, and it never switches back | A brief DB blip (failover, restart, "too many clients") can leave a production worker on an empty local DB. Writes are lost, tenants appear empty, and the first-run setup wizard is served | Fail closed outside `APP_ENV=development`: raise/503 and keep retrying Postgres. Keep SQLite for dev only | S | **Fixed** in 75933b6 (test: tests/test_qa_db_resilience.py); full suite green at 2fac33b (2,723 passed) |
| TEST-01 | Tests | High | tests/conftest.py:12-24 | conftest loads the real `.env` and neutralises no external-service credentials | **Real Razorpay orders are created** on every local run (e.g. `order_TkYAKaeHmcb8hw`); 14 tests fail depending on the network and `.env`. AI/push/alert/SMTP calls are possible too (NV-03) | Blank every external-service variable in conftest (`RAZORPAY_*`, `ANTHROPIC/GEMINI_API_KEY`, `EXPO_ACCESS_TOKEN`, `SECURITY_ALERT_WEBHOOK_URL`, `SMTP_*`, `TURNSTILE_*`, `REDIS_*`, `HEARTBEAT_URL_*`); optionally block sockets | S | **Fixed**: Razorpay on security-fixes (c689aae, `razorpay_demo_mode` fixture); every other external-service variable blanked in conftest (test: tests/test_test_infrastructure.py); full suite green at 2fac33b (2,723 passed) |
| SCALE-08 | Scalability | High | app.py:947-960, 1145-1147 (`_KILLSWITCH_SCRIPT`); blueprints/core.py:167-210 (`/api/session/risk-stream`) | Every authenticated HTML page opens an `EventSource` to an endpoint that holds a **synchronous gthread worker thread** for ~20 s and is then reconnected by the browser | Each open logged-in tab permanently occupies one of ~32 production threads (8 workers × 4); about 32 open tabs stall the whole app for everyone | Replace the stream with a short poll (immediate response) or drop the client listener (the real kill switch is server-side, `_reject_if_compromised`); otherwise async workers | S | **Fixed** (test: tests/test_qa_session_risk_stream.py): same route and client, answers immediately with SSE `retry: 15000`; a compromised tab is told within ~15 s instead of ~2 s, and server-side rejection is unchanged; full suite green at 2fac33b (2,723 passed) |
| SCALE-06 | Scalability / correctness | High | utils/email_utils.py:218-260 (`_drain_queue_for_schema`), started per worker in wsgi.py | Every gunicorn worker runs the email-queue drainer. It SELECTs 10 pending rows with no lock and marks each `sending` only just before sending it; the UPDATE has no `AND status='pending'` | **Duplicate emails** (payslips, blasts, reports, codes) whenever two workers overlap during a batch; rows stranded in `sending` after a crash are never retried | Claim atomically: `UPDATE … SET status='sending' WHERE id IN (SELECT id … FOR UPDATE SKIP LOCKED LIMIT 10) RETURNING …`; requeue `sending` rows older than N minutes | S | **Fixed** (test: tests/test_qa_email_queue_claim.py): atomic `UPDATE … FOR UPDATE SKIP LOCKED … RETURNING` claim; new `email_queue.claimed_at` (idempotent `ADD COLUMN IF NOT EXISTS` in every tenant via column migrations); rows stuck in `sending` > 15 min are requeued. Full-suite confirmation pending |
| SCALE-01 | Scalability | High | wsgi.py:134-190, gunicorn.conf.py:9 | APScheduler starts in **every** gunicorn worker (up to 8, no `preload_app`, no lock) | Every nightly job runs N times: daily report emailed N×; trial reminders and dunning race (check-then-act). Disbursement runs and invoices are protected only by DB unique constraints, which make the extra runs error out | Elect one scheduler with `pg_try_advisory_lock`, or a `RUN_SCHEDULER` flag in a single process | M | **Fixed** (test: tests/test_qa_scheduler_once.py): each run claims (job id, scheduled minute) in `att_master.scheduler_job_runs` (`INSERT … ON CONFLICT DO NOTHING`); only the winner runs and pings the heartbeat. Works across workers and hosts; no lock to lose. Full-suite confirmation pending |
| SCALE-02 | Scalability | High | wsgi.py:46-58, gunicorn.conf.py:20 | Every worker boot runs `init_master_db` + `init_db` + migrations for **every tenant schema**, including recycles every ~1,000 requests (`max_requests`) | Boot time grows with tenant count. `ALTER TABLE … ADD COLUMN IF NOT EXISTS` takes an ACCESS EXCLUSIVE lock even when the column exists, on live tables during traffic (lock impact measured in NV-02) | Run migrations once per deploy (entrypoint step) behind an advisory lock with a schema-version check; workers skip | M | **Fixed** (test: tests/test_qa_startup_migrations.py): `migrations/runner.py` serialises boots with `pg_advisory_lock` and migrates only when the hash of `migrations/*.py` differs from `att_master.schema_migrations`; failures don't record the version; `MIGRATIONS_FORCE=1` re-runs; factory_reset clears the version. Not covered: `database._ensure_pg_schema` still runs 6 `ALTER … IF NOT EXISTS` on each pool creation. Full-suite confirmation pending |
| SCALE-09 | Scalability / resilience | High | database.py:617-628 (`_borrow_connection`) | Any `getconn()` failure, including `PoolError: connection pool exhausted` when all `DB_POOL_MAX_CONN` (20) connections are legitimately in use, rebuilds the **whole pool** (5+ new connections) and never closes the old one; its checked-out connections still return to it | A load spike multiplies the connection count instead of queueing; repeated rebuilds reach Postgres `max_connections` ("too many clients"), and since BUG-02 that is a 503 for every request on that worker. Measured in the load test | Rebuild only on a broken pool (`OperationalError`/`InterfaceError`); on exhaustion wait briefly for a free connection, then return 503; close the replaced pool's idle connections | S | **Fixed** (test: tests/test_qa_pool_exhaustion.py): exhaustion waits up to `DB_POOL_WAIT_SECONDS` (default 5) for a free connection, then 503; only a broken or closed pool is rebuilt. Full-suite confirmation pending |
| BUG-30 | Correctness / duplication | High | blueprints/attendance.py:1290 (`/attendance`), :1393 (`process_punch` ← `/api/attendance/checkin`); blueprints/employee_portal.py:1027 (`/api/employee/checkin`), :1160 (`/api/employee/sync_punches`), :1363 (`/api/employee/qr-face-checkin`) | Late/half-day/logout classification is copy-pasted in 5 places; only the web kiosk honours the employee's **assigned shift**, and the other 4 use the company default | The same employee is stored as "Late" or "Full Day" depending on which device they checked in from; feeds payroll | One shared, per-company, shift-aware classifier used by all 5 endpoints (fixed together with BUG-01) | M | **Fixed** (test: tests/test_qa_shift_aware_checkin.py): `classify_login`/`classify_logout` in utils/attendance_utils.py, fed by `get_employee_shift`, used by all 5 paths; full suite green at 2fac33b (2,723 passed) |
| SEC-07 | Security (tenant isolation) | High | database.py:107 (`SET search_path TO "<tenant>", public`) | A table missing from a tenant schema **silently resolves to the shared `public` table**, which holds the default/original company's data and is the target of slug-less requests | Cross-company reads/writes for any tenant lacking a table (the code itself notes older tenants missed tables added only to core_tables). Whether it happened is NV-16 | Option A: at startup and provisioning, verify every tenant has every table and refuse to route otherwise. Option B: drop `public` from the tenant `search_path` (needs a check that no shared function/extension lives in `public`). **Risky, so options to be confirmed with the owner before fixing** | M | Owner input |
| BUG-01 | Correctness (multi-tenant) | High | utils/config.py:15-60 (45 uses: employee_portal 18, attendance 8, salary_utils 6, admin_views 5, payroll 2, attendance_utils 2, leave 1) | Shift times and payroll rules (`SHIFT_*`, `GRACE_MINUTES`, `LATE_DEDUCTION_RATE`, `HALF_DAY_RATE`, `HOLIDAY_PAY`, `LEAVE_PAY`) are **process-wide globals**, loaded from the default schema at import and overwritten by whichever tenant last saved settings (`admin_views.py:1099,1442`, `attendance.py:255`) | One tenant's shift and deduction rates decide another tenant's late/half-day marks and salary deductions; different workers disagree | Per-tenant cached accessors (reuse `_cache_get`/`_tenant_cache_key` like `get_company_settings`); replace the `cfg.*` reads; remove the globals | L | **Fixed** (test: tests/test_qa_tenant_rules.py): `cfg.shift_rules()`/`cfg.salary_rules()` read the current company's cached settings; saves invalidate the cache; startup loads removed; full suite green at 2fac33b (2,723 passed) |
| BUG-03 | Correctness | Medium | blueprints/admin_views.py:2212-2234 | Analytics "Attendance %" = distinct employees present in the month ÷ (working days × employees) | 10 employees present all 20 days shows **5%**; "absent days" is inflated | Count employee-days (`COUNT(*)` of attended rows, or distinct `(employee_id, date)`) | S | **Fixed** (test: tests/test_qa_attendance_pct.py): shared `attendance_pct_series()` counts employee-days on past working days only (capped at 100%), and only for current employees |
| BUG-04 | Correctness | Medium | blueprints/hr_dashboard.py:316-331 | Same formula in the HR dashboard reports | Same as BUG-03 | Same as BUG-03 | S | **Fixed** (test: tests/test_qa_attendance_pct.py): uses the same `attendance_pct_series()` |
| BUG-06 | Correctness / DoS | Medium | blueprints/leave.py (request_leave, ~146-160) | No upper bound on the leave date range; one INSERT per day in a loop | One request (2026→2099) inserts about 27k rows | Cap the range (e.g. 60 days), validate dates, batch insert | S | Open |
| BUG-31 | Correctness | Medium | blueprints/leave.py:450-500 (`leave_action`) | Approve/reject doesn't check the current status (the bulk path does: `status='Pending'`) | Re-approving deducts the balance **again**; rejecting an approved leave never refunds the balance or clears the 'Approved Leave' attendance mark | Only transition from `Pending` (`UPDATE … WHERE id=%s AND status='Pending' RETURNING`), as `bulk_leave_action` does | S | Open |
| BUG-32 | Correctness / duplication | Medium | blueprints/leave.py:974-1004 (`api_leave_action`, mobile) | The mobile approve/decline only updates `status`; unlike the web `leave_action` it doesn't deduct `leave_balances`, mark the day 'Approved Leave' in attendance, or check `Pending` | Leave approved from the phone **never reduces the balance**; attendance shows the day as absent | Share one approval service between the web and API twins (also fixes BUG-31 for both) | M | Open |
| BUG-17 | Correctness (timezone) | Medium | blueprints/admin_views.py:83, 290, 356, 793-794, 2166, 2663 | "Today" comes from the server clock (`date.today()`, UTC in the container) while check-ins are stored with `company_today()` | Dashboard today/late/absent counts show **yesterday** between 00:00 and 05:30 IST, and are wrong for non-IST tenants | Use `company_today()` / `company_now()` | S | **Fixed** (test: tests/test_qa_company_dates.py) |
| BUG-18 | Correctness (timezone) | Medium | blueprints/hr_dashboard.py:214, 256, 289, 382, 407, 489 | Same as BUG-17 | Same | Same | S | **Fixed** (test: tests/test_qa_company_dates.py) |
| BUG-19 | Correctness (timezone) | Medium | blueprints/attendance.py:400-401, 475, 580, 660, 816, 918-919 | Same (today views, bulk-mark default, default period) | Same | Same | S | **Fixed** (test: tests/test_qa_company_dates.py) |
| BUG-20 | Correctness (timezone) | Medium | blueprints/leave.py:74, 95, 217, 292, 330-331, 491, 583, 1287, 1524 | Same (leave/holiday year defaults, "today" checks, OT default date) | Wrong day/year around midnight IST | Same | S | **Fixed** (test: tests/test_qa_company_dates.py) |
| BUG-23 | Correctness (timezone) | Medium | blueprints/onboarding.py:194, 259, 321, 408, 539, 634, 655, 1499, 1534, 1537, 1670, 1673 | Same (onboarding start/due dates, offer-letter dates) | Due dates off by one around midnight IST | Same | S | **Fixed** (test: tests/test_qa_company_dates.py): dates use `company_today()`; completion timestamps store the company's wall-clock time like attendance times |
| BUG-28 | Correctness | Medium | utils/ai_attrition_analytics.py:33 | Counts `status IN ('Late','Absent')`, but the app stores `'Late Login'`, and absences have no row | The attrition "late/absent" risk signal is effectively always 0 | Use the real statuses and count working days without a row | S | **Fixed** (test: tests/test_qa_attrition_signal.py): counts 'Late Login' rows plus working days (since joining, excluding holidays and approved leave) with no check-in |
| PERF-01 | Performance | Medium | utils/auth.py:672, 744 | Every Bearer-token API request runs `DELETE FROM api_tokens WHERE expires_at < NOW()` and commits; there's no index on `expires_at` | A write plus a table scan on every mobile API call | Clean up periodically (scheduler or 1% sampling) and add an index on `expires_at` | S | Open |
| PERF-02 | Performance | Medium | utils/ai_attrition_analytics.py:28-40 | 2 queries per employee (N+1) | 2N+1 queries per analytics request | One grouped query per signal | S | **Fixed** by the BUG-28 commit (test: tests/test_qa_attrition_signal.py::test_query_count_does_not_grow_with_headcount): 5 queries whatever the headcount, was 2N+1 |
| ERR-01 | Error handling | Medium | utils/helpers.py:1602 (`_error_page`), app.py:1343-1385 | 403/404/405/500 always return an HTML page, even for `/api/*` (only 429 has a JSON branch) | The mobile app can't parse errors; generic "something went wrong" | JSON body for `/api/*` and JSON-accepting requests, mirroring the 429 handler | S | **Fixed** (test: tests/test_qa_api_errors.py): `_error_page` answers `{"ok": false, "msg": <title>}` for API/AJAX/JSON requests (403/404/405/500 and any HTTP error); the 429/503 handlers share the same `wants_json_response()` check |
| CONFIG-01 | Config | Medium | gunicorn.conf.py:9, compose.lowmem.yaml:28 | `GUNICORN_WORKERS` is set in the low-memory profile but never read | The low-memory host still runs up to 8 workers, each loading dlib/OpenCV (OOM risk) | `workers = int(os.environ.get("GUNICORN_WORKERS", default))` | S | **Fixed** (test: tests/test_qa_gunicorn_config.py): `GUNICORN_WORKERS` and `GUNICORN_THREADS` are read; defaults unchanged |
| SEC-01 | Security / config | Medium | .containerignore | Doesn't exclude `private_uploads/`, `backups/`, `pg_data/`, `.venv_test/`, `qa_audit/` | An image built from a working copy can contain employee documents, DB dumps and raw DB files (see NV-09) | Add those paths; build from a clean clone in CI | S | **Fixed** (test: tests/test_qa_container_ignore.py): adds `**/.env*` (keeping `.env.example`), uploads, backups, `pg_data/`, `instance/`, SQL dumps, virtualenvs, `qa_audit/` and coverage/pytest caches. Building from a clean clone in CI is still recommended (NV-09) |
| SEC-08 | Security (attendance integrity) | Medium | blueprints/employee_portal.py:958 (`/api/employee/checkin`) | The geofence runs only `if lat and lon:`; a request **without coordinates is accepted**, even when the company has location enforcement on (the kiosk route requires location in that case) | An employee can mark attendance from anywhere by omitting GPS | When `location_enabled`, require lat/lon (400 if missing), same as `/attendance` | S | Open |
| SEC-09 | Security (attendance integrity) | Medium | blueprints/employee_portal.py:978-1000 (`punched_at`), :1080 (`/api/employee/sync_punches`) | The client-supplied `punched_at` is accepted anywhere in the **last 24 h** on the live check-in endpoint (meant for offline sync) | Backdating: arrive at 11:00, submit 08:55, avoid late/half-day deductions | Ignore `punched_at` on the live endpoint; for offline sync cap the window, never move a punch earlier than the last server-seen activity, and flag synced punches for admin review | M | Open |
| SEC-05 | Security | Medium | blueprints/employees.py:124 | Admin "reset password" sets the employee's password to their employee ID | Trivially guessable credentials until changed | Random one-time password plus forced change | S | **Fixed on security-fixes (37a2eac)** |
| DEP-01 | Dependencies | Medium | mobile/package.json:27 (axios) + lockfile | axios < 1.19.1 (High advisory, ships in the app); moment, http-cache-semantics, source-map-js, brace-expansion, braces, micromatch advisories | Known-vulnerable runtime library | `npm audit fix` (non-breaking) + jest run | S | Open |
| TEST-03 | Tests | Medium | tests/conftest.py (`_reset_db_after_test`) | The per-test DB restore doesn't clear in-process caches (settings/auth/pending counts/companies); with pytest-randomly the order changes every run | Order-dependent flakiness (seen in practice: stale `face_enabled`) | Call `invalidate_settings_cache()` and `invalidate_pending_counts_cache()` in teardown | S | **Fixed** (test: tests/test_test_infrastructure.py): teardown clears settings/auth, pending-counts, companies, onboarding, plan-rate, extra-cost, platform employee-count and TOTP caches; full suite green at 2fac33b (2,723 passed) |
| BUG-05 | Correctness (payroll) | Medium | blueprints/employee_portal.py `employee_portal()` ("this month" earnings card), blueprints/payroll.py `my_payslip_summary` | The portal's earnings card and `/my_payslip_summary` each compute net pay with their own rules: late days paid in full, half day fixed at 50%, holidays and approved leave ignored; `my_payslip_summary` also deducts a flat 12% PF. Underneath, the payslip document (CTC structure, LOP = absent days × gross/26, PF/PT/TDS, half and late days paid in full) and the disbursed amount (`compute_salary_entry`: per-day salary with the company's late/half-day rates, no PF/PT/TDS) are two different models | Employees see figures that match neither their payslip nor what is paid | Make every view use the one authoritative calculation, once the owner chooses it (NV-19). The never-displayed 3-month summary is gone (PERF-03) | M | Owner input (NV-19) |
| BUG-34 | Correctness (payroll) | Medium | blueprints/payroll.py `/api/salary_report`, `/api/send_salary_email` | The mobile admin API computed salaries without the month's holidays or the employee's approved leave (both paid as absences), without incentives, and built the emailed payslip without the employee's CTC or the company's PF/PT/TDS settings | The mobile salary report and the emailed payslip disagreed with the web report, the disbursement and the downloaded payslip (e.g. net 24,000 vs 25,750 for one paid holiday plus a 750 incentive) | Reuse `compute_salary_data_for_month()` and one shared payslip builder | S | **Fixed** (test: tests/test_qa_api_payslip_parity.py) |
| BUG-07 | Correctness | Medium | blueprints/leave.py (request_leave) | Leave rows are inserted for Sundays and holidays, and nothing checks for duplicates or overlaps | Leave day counts include non-working days; the same date can be requested twice (balance effect: NV-05) | Skip non-working days; reject overlaps (unique `(employee_id, leave_date)` or a pre-check) | M | Open |
| TEST-02 | Tests | Medium | tests/conftest.py:17 | The test DB name is hard-coded (`att_test`), so runs can't be parallel or isolated per session; the full suite takes ~100 min | Slow feedback; two sessions collided today | Read the name from an env var; per-worker DBs with pytest-xdist | M | **Fixed** in 0d08d81 (test: tests/test_test_infrastructure.py); full suite green at 2fac33b (2,723 passed) |
| TEST-04 | Tests | Medium | qa_audit/baseline/coverage_report.txt | Coverage is 82% overall, but the riskiest modules are the least covered: `database.py` 56% (pool rebuild/failover), `utils/razorpay_utils.py` 42%, `utils/document_verification.py` 49% (claim worker), `utils/task_queue.py` 0%, `utils/email_utils.py` 72% (queue drainer), `blueprints/disbursement.py` 73% | Regressions in payments, payouts, background workers and DB failover go unnoticed | Add tests alongside the fixes for SCALE-06/07/09 and BUG-02; payment/payout paths in demo mode | M | Open |
| TEST-05 | Tests | Medium | tests/conftest.py (`_restore_snapshot`) | The per-test reset used `TRUNCATE … RESTART IDENTITY CASCADE` on every table in `public` and `att_master`; TRUNCATE gives each table and index a new data file and deletes the old one -- over a million file creates/deletes per run. Checkpoints spent ~1 h fsyncing ~570k files, and on 2026-10-07 a checkpoint fsync racing a delete PANICked the local Postgres 16 on Windows (`could not fsync file …: Invalid argument`) into a crash recovery that took ~1 h of redo (plus a lost overnight attempt) | Local test runs can take down the developer's whole Postgres, dev DB included | Empty tables with DELETE under `session_replication_role = replica` (keeps the files), sequences already reset with setval | S | **Fixed** (test: tests/test_test_infrastructure.py::test_reset_does_not_rewrite_table_files) |
| CONFIG-03 | Config | Medium | requirements.txt, Containerfile:40-41 | All Python dependencies are `>=` floors with no lock file; the runtime stage runs an unpinned `pip install --upgrade wheel jaraco.context` | Non-reproducible builds; a new release can break production | pip-tools lock (`requirements.lock`) used by Containerfile and CI | M | Open |
| SCALE-07 | Scalability / correctness | Medium | utils/document_verification.py:294-307 (`_document_verification_worker`) | Same claim race as SCALE-06 for KYC verification jobs (`verification_job_status`) | The same application is verified by several workers at once: duplicate AI/OCR calls (cost) and duplicate applicant emails; stuck `processing` rows | Same `FOR UPDATE SKIP LOCKED` claim plus stale-claim recovery | S | **Fixed** (test: tests/test_qa_verification_claim.py): `_claim_verification_jobs()` claims atomically; new `tenant_applications.verification_claimed_at` (idempotent ALTER in init_master_db); jobs stuck in `processing` > 30 min are requeued |
| SCALE-04 | Scalability | Medium | utils/helpers.py:652-653, 859, 1163-1164; utils/totp.py:28; blueprints/platform_admin.py:50 | In-memory caches are per worker; only settings/auth use Redis, and only when it's configured | Settings changes and badges take up to the TTL to reach other workers; behaviour differs by worker | Route the remaining caches through the existing `_cache_get`/Redis path | M | Open |
| SCALE-05 | Scalability | Medium | utils/storage.py, `private_uploads/`, `dataset/`, `static/qrcodes/` | Uploads, face images and QR codes are on local disk | Can't run more than one app server | Object storage (S3-compatible) behind `utils/storage.py` | L | Phase 6 — needs sign-off |
| BUG-08 | Correctness | Low | blueprints/payroll.py: 27 calls (193 … 1269) | Unvalidated `int()` on request input | `?year=abc` gives a 500, a logged traceback and an **error-alert webhook** | Shared `arg_int(name, default, lo, hi)` helper returning 400 | S | Open |
| BUG-09 | Correctness | Low | blueprints/performance.py: 19 calls (221 … 679) | Same | Same | Same | S | Open |
| BUG-10 | Correctness | Low | blueprints/leave.py: 12 calls (74 … 1671) + `fromisoformat` in request_leave | Same | Same | Same | S | Open |
| BUG-11 | Correctness | Low | blueprints/attendance.py:324, 356, 400, 401, 918, 919 | Same | Same | Same | S | Open |
| BUG-12 | Correctness | Low | blueprints/hr_dashboard.py:215, 216, 257, 258, 408, 409 | Same | Same | Same | S | Open |
| BUG-13 | Correctness | Low | blueprints/onboarding.py:472, 473, 736, 739 | Same | Same | Same | S | Open |
| BUG-14 | Correctness | Low | blueprints/employee_portal.py:362, 363, 552 | Same | Same | Same | S | Open |
| BUG-15 | Correctness | Low | blueprints/employees.py:2175, 2176 | Same | Same | Same | S | Open |
| BUG-16 | Correctness | Low | blueprints/admin_views.py:2131 | Same | Same | Same | S | Open |
| BUG-21 | Correctness (timezone) | Low | blueprints/payroll.py:193-194, 212, 347-360, 807-808, 900, 925, 1001, 1095, 1173-1174, 1231-1232, 1268-1269, 1467 | Default payroll period from the server clock | Wrong default month on the 1st before 05:30 IST | `company_today()` | S | Open |
| BUG-22 | Correctness (timezone) | Low | blueprints/employee_portal.py:1310, 1336, 1421-1422, 1491-1492 | Same (default period, file timestamps) | Same | Same | S | Open |
| BUG-24 | Correctness (timezone) | Low | blueprints/performance.py:220, 324, 677 | Same | Same | Same | S | Open |
| BUG-25 | Correctness (timezone) | Low | blueprints/employees.py:232, 1187, 1191 | Same | Same | Same | S | Open |
| BUG-26 | Correctness (timezone) | Low | blueprints/daily_report.py:126, 157 | The report day comes from the server date; the job runs at 23:59 server time (05:29 IST) | The report covers an unexpected day/time for IST tenants | Per-tenant `company_today()` and schedule in the tenant TZ | S | Open |
| BUG-27 | Correctness (timezone) | Low | blueprints/billing_dunning.py:57, 101, 242, 244; blueprints/auto_debit.py:423, 611, 618 | Platform billing periods use the server-local date | Month boundary at UTC midnight, not IST | Use one explicit platform TZ (IST) | S | Open |
| BUG-29 | Correctness (UI) | Low | templates/settings.html (~line 2215, `crumbData`) | Duplicate `company` key in an object literal | The Companies breadcrumb shows the wrong title/subtitle | Remove the stale duplicate | S | Open |
| BUG-33 | Correctness | Low | blueprints/leave.py:984 | The mobile leave decline writes `'Declined'` (the **resignation** vocabulary) where leaves use `'Rejected'` | Mobile-declined leaves are missing from the 'rejected' counts (leave.py:1209) and badges (leave_holidays.html:376, employee_portal.html:1044) | Write `'Rejected'`; migrate existing leave rows from 'Declined' to 'Rejected' | S | Open |
| SEC-02 | Security | Low | templates/my_onboarding.html:223, 269 | Task title placed in `onclick` with hand-rolled quote escaping (backslash not escaped), then into `innerHTML` | DOM/HTML injection from an admin-authored title (CSP blocks script execution) | `|tojson` plus `textContent` | S | Open |
| SEC-03 | Security | Low | templates/index.html:754 | `showError()` writes the server message with `innerHTML` | HTML injection if a message ever contains user data | `textContent` | S | Open |
| SEC-04 | Security | Low | blueprints/admin_views.py:2685, blueprints/platform_admin.py:940 | Raw exception text shown (admin-only screens) | Internal detail disclosure to admins | Generic message; details in logs | S | **Fixed on security-fixes (37a2eac/b27c5be)** |
| SEC-06 | Security | Low | blueprints/billing.py:47 | `/api/billing/create_order` not gated by the application access token | Anyone can stage orders for an approved application id | Same `_verify_application_access` gate as L5 | S | **Fixed on security-fixes (bd13977)** |
| SEC-10 | Security (authorization) | Low | blueprints/payroll.py `/api/send_salary_email`, `/send_salary_email` | Any admin-side role (HR, manager, SOC analyst) can trigger any employee's payslip email; the salary report routes are admin-only | Low: the slip goes only to the employee's own address, but it's an unrestricted bulk-sendable action | `@api_role_required("admin")` / `role_required("admin")`, matching the report routes | S | Open |
| CONFIG-02 | Config / CI | Low | .github/workflows/security.yml | bandit runs with `\|\| true`; Python 3.13 vs 3.11 elsewhere | The gate never fails | Make it blocking; align the Python version | S | Bandit part fixed (06faebb); Python-version part Open |
| CONFIG-04 | Config | Low | database.py:39 | `DB_CONNECT_TIMEOUT` defaults to 1 s in code (10 in `.env.example`) | Spurious connection failures under load or across a network, which then trigger BUG-02 | Default 10 s | S | Open |
| CONFIG-05 | Config | Low | Containerfile, compose*.yaml | No `TZ` set, so the container runs UTC | Exposes every server-clock bug above | Don't rely on it: fix BUG-17…27; optionally set `TZ` for logs | S | Open |
| UX-01 | Correctness (UX) | Low | utils/helpers.py:859-866 | `invalidate_pending_counts_cache()` is never called after an approval or rejection | Sidebar badges are stale for up to 30 s per worker | Call it from leave/resignation/ticket state changes | S | Open |
| CONN-01 | Reliability | Low | blueprints/onboarding.py:299-300, 474-476; blueprints/org.py:1273 | Early `return` before `db.close()` (no `with`/`finally`) | Mitigated by `_PooledConnection.__del__`, but relies on GC timing | Validate before borrowing, or `with`/`finally` | S | Open |
| PERF-03 | Performance | Low | blueprints/employee_portal.py:661-705 | 9 queries in a loop on every portal load | Slower portal | Fixed by BUG-05 | S | **Fixed** (test: tests/test_qa_portal_queries.py): the summary was never displayed (`recent_payslips` is unused by every template), so the loop was removed rather than rewritten |
| PERF-04 | Performance | Low | blueprints/email_blast.py:108-114 | One INSERT per recipient in a loop | Slow blasts to large companies | `execute_values` batch insert | S | Open |
| PERF-05 | Performance | Low | blueprints/hr_dashboard.py:338 | Headcount query per month (6) | 6 round trips | One grouped query | S | Open |
| PERF-07 | Performance | Low | admin_views.py:1309, leave.py:396, onboarding.py:713, helpers.py:917 | `SELECT *` | Over-fetching; fragile to schema changes | Explicit columns | S | Open |
| DEAD-01 | Dead code | Low | utils/razorpay_utils.py:75 | `webhook_configured()` is never referenced | Noise | Remove | S | Open |
| DEAD-02 | Dead code | Low | utils/config.py:15-17 | `OFFICE_LAT/OFFICE_LON/OFFICE_RADIUS_M` (hard-coded coordinates) unused | Misleading config | Remove | S | Open |
| DEAD-03 | Dead code | Low | static/faces | Empty 0-byte tracked file | Noise | Remove | S | Open |
| DEAD-04 | Dead code | Low | blueprints/admin_views.py:2678-2680 | `if err:` branch unreachable (`send_email_smtp` returns None; it raises on failure) | Misleading | Remove the branch | S | Open |
| DEAD-05 | Dead code | Low | mobile/src (49 locations, `eslint_mobile.txt`) | Unused variables and imports | Bundle noise | Remove | S | Open |
| DEAD-08 | Dead code | Low | app.py (242 lines like `# home migrated to blueprints/core.py`) | Leftover migration stub comments | Size and noise in the main module | Remove | S | Open |
| DUP-05 | Duplication | Low | utils/auth.py:482-498 vs 609-625 | AJAX-detection block copied in `admin_required`/`role_required` | Drift risk | Use the existing `_is_ajax_request()` | S | Open |
| DUP-06 | Duplication | Low | blueprints/admin_views.py:2203-2210, blueprints/hr_dashboard.py:309-314 | "i months back" via 28-day stepping copied (correct only for i ≤ 10) | Fragile if reused for 12 months | Shared `month_bounds(today, n)` helper | S | Open |
| DUP-10 | Duplication | Low | utils/ai_assistant.py:336-352 vs 376-392 | Duplicated HTTP call block | Drift | Helper | S | Open |
| DUP-11 | Duplication | Low | utils/ai_client.py:31-44 vs 80-93 | Same | Drift | Helper | S | Open |
| DUP-12 | Duplication | Low | blueprints/documents.py:36-50 vs 389-403 | Duplicated list/format block | Drift | Helper | S | Open |
| DUP-13 | Duplication | Low | blueprints/policies.py:35-44 vs 60-70 | Same | Drift | Helper | S | Open |
| DUP-14 | Duplication | Low | blueprints/core.py:331-340 vs 661-670 | Same | Drift | Helper | S | Open |
| QUAL-02 | Code quality | Low | 228 locations (ruff I001) | Unsorted imports | Noise | `ruff --fix --select I001` | S | Open |
| QUAL-03 | Code quality | Low | 80 files (ruff UP009) | `# -*- coding: utf-8 -*-` headers (redundant in Py3) | Noise | autofix | S | Open |
| QUAL-04 | Code quality | Low | pylint W0612 (26), ruff RUF059 (79) | Unused (unpacked) variables | Noise; hides real mistakes | Rename to `_` or remove | S | Open |
| QUAL-05 | Code quality | Low | pylint W0404 (20), W0611 (9), ruff F401 (1) | Reimports and unused imports | Noise | Remove | S | Open |
| QUAL-06 | Code quality | Low | pylint W1203 (11) | f-strings in logging calls | Formatting cost even when the level is disabled | `%s` args | S | Open |
| QUAL-07 | Code quality | Low | ruff RUF100 (25) | Unused `# noqa` | Noise | autofix | S | Open |
| QUAL-08 | Code quality | Low | ruff FURB167 21, PLR0402 19, UP031 8, C408 7, misc. | Mechanical modernisations | Noise | Safe autofixes only | S | Open |
| QUAL-09 | Code quality | Low | utils/oauth_client.py:204, 207, 217 | `raise … from` missing | Lost exception context | `from exc` | S | Open |
| JS-01 | Frontend | Low | template/static JS (14 `no-empty`) | Empty `catch {}` blocks | Silent failures | Log, or handle | S | Open |
| JS-02 | Frontend | Low | template JS (9 `no-inner-declarations`, 6 `no-useless-escape`) | Style issues that can confuse | — | Fix | S | Open |
| JS-03 | Frontend | Low | templates/settings.html (`crumbData`) | The tab metadata object defines `company` twice; the last one silently wins, so the first entry is dead and the two disagree about the tab's title (the 1 real ESLint `no-dupe-keys` finding in the baseline) | Confusing to maintain; an edit to the first entry would have no effect | Remove the dead entry (keeps today's visible title) | S | **Fixed** (test: tests/test_qa_template_js.py) |
| MOB-01 | Mobile | Low | mobile/src (23 `no-empty`) | Swallowed errors in the app | Silent failures | Handle/log | S | Open |
| MOB-02 | Mobile | Low | mobile/src (4 `no-useless-escape`) | Style | — | Fix | S | **Fixed**: the 4 `\-` at the end of character classes in CompanySignupScreen.js (ESLint 4 → 0; old and new regexes give identical results on all 128 ASCII characters) |
| A11Y-03 | Accessibility | Low | templates/offer_letter.html (11) | Unlabelled form controls | Screen-reader users | `for=`/`aria-label` | S | Open |
| A11Y-04 | Accessibility | Low | templates/performance.html (10) | Same | Same | Same | S | Open |
| A11Y-05 | Accessibility | Low | templates/leave_holidays.html (10) | Same | Same | Same | S | Open |
| DUP-01 | Duplication | Low | admin_login, force_change_pin, mfa_verify, admin/employee reset & forgot, admin_mfa_required (.html) | Each repeats the whole `<head>` plus an 80–170-line CSS block | 11% template duplication; style drift | `auth_base.html` that the pages extend | M | Open |
| DUP-02 | Duplication | Low | create_org, create_org_{verify_otp,upload_documents,status,pay,setup_trial}.html | Same | Same | `create_org_base.html` | M | Open |
| DUP-03 | Duplication | Low | super_admin_{audit_log,tickets,ticket_detail,applications,application_detail,duplicate_alerts,company_profile,soc_dashboard}.html | Same | Same | `super_admin_base.html` | M | Open |
| DUP-04 | Duplication | Low | my_onboarding, my_performance, my_compoff (.html) | Same | Same | Extend the employee base | M | Open |
| DUP-07 | Duplication | Low | billing.py:288-300, seats.py:109-121, billing_dunning.py:278-286 | Payment-verify blocks copied | Drift in payment code | Shared helper | M | Open |
| DUP-08 | Duplication | Low | mobile Admin/Employee/ManagerDrawerContent.js | ~200 lines duplicated across drawers | Drift | Shared drawer component | M | Open |
| DUP-09 | Duplication | Low | mobile AttendanceScreen.js:307-407 vs SalaryPayslipsScreen.js:330-431; CompOffStatsGrid vs CompOffSummaryCard; Bank/Emergency/Personal/Contact screens | Copied UI blocks | Drift | Shared components | M | Open |
| QUAL-10 | Types | Low | mypy: database.py:675/683, plan_limits.py:55-56/89-90, helpers.py:1016-1151, implicit-Optional ×4 | Type errors (`None` paths) | Latent `None` crashes in edge paths | Annotate/guard | M | Open |
| MISP-01 | Misplaced code | Low | utils.helpers ↔ push_utils, email_utils ↔ helpers, auth ↔ helpers (pylint R0401); 257 function-level imports | Import cycles worked around with local imports | Fragile import order | Move shared primitives into a leaf module | M | Open |
| MISP-02 | Misplaced code | Low | 25 inline HTML email bodies in 16 modules (leave.py ×6, org.py ×4, …) | Presentation HTML built in route code | Hard to maintain; escaping is per call | Jinja email templates | M | Open |
| PERF-06 | Performance | Low | 22 `EXTRACT(MONTH/YEAR FROM date)` predicates | Defeat date indexes | Slower monthly queries as data grows | Date-range predicates | M | Open |
| A11Y-01 | Accessibility | Low | templates/employees.html (74 controls, 2 icon-only buttons) | Unlabelled controls | Screen readers | Labels / `aria-label` | M | Open |
| A11Y-02 | Accessibility | Low | templates/settings.html (40) | Same | Same | Same | M | Open |
| A11Y-06 | Accessibility | Low | remaining templates (`a11y.txt`, < 8 each), 13 icon-only buttons/links, 1 `<img>` without alt | Same | Same | Same | M | Open |
| DEAD-09 | Dead code | Low | database.py:111-509 (`_SqlitePool`, `_seed_sqlite_db`, ~400 lines) | SQLite fallback layer in the production DB module | Size and risk (BUG-02) | After BUG-02, move to a dev-only module or remove | M | Phase 6 — needs sign-off |
| QUAL-01 | Code quality | Low | 308 `except Exception` (ruff BLE001) / 240 (pylint W0718) | Broad catches, many deliberately fail-open | Hidden bugs | Audit and narrow in critical paths (auth, payroll, billing) first | L | Phase 6 — needs sign-off |
| QUAL-11 | Code quality | Low | 16 files > 800 lines (admin_views 2,715 … core_tables 829) | Very large modules | Maintainability | Split by feature | L | Phase 6 — needs sign-off |
| QUAL-12 | Code quality | Low | 165 functions > 50 lines, 88 without docstrings | Long functions | Maintainability | Extract helpers, add docstrings | L | Phase 6 — needs sign-off |
| QUAL-13 | Complexity | Low | blueprints/employees.py:941 `add_employee_page` (CC 84) | Very high complexity | Bug-prone | Extract validators/services | L | Phase 6 — needs sign-off |
| QUAL-14 | Complexity | Low | blueprints/employee_portal.py:305 `employee_portal` (CC 73, 498 lines) | Same | Same | Same | L | Phase 6 — needs sign-off |
| QUAL-15 | Complexity | Low | blueprints/performance.py:854 `performance_import` (CC 63) | Same | Same | Same | L | Phase 6 — needs sign-off |
| QUAL-16 | Complexity | Low | blueprints/admin_views.py:2160 `analytics` (CC 58, 369 lines) | Same | Same | Same | L | Phase 6 — needs sign-off |
| QUAL-17 | Complexity | Low | blueprints/attendance.py:1120 `attendance` check-in (CC 53) | Same | Same | Same | L | Phase 6 — needs sign-off |
| QUAL-18 | Complexity | Low | blueprints/employee_portal.py:1207 `api_employee_qr_face_checkin` (CC 43) | Same | Same | Same | L | Phase 6 — needs sign-off |
| QUAL-19 | Code quality | Low | templates/employee_portal.html (212 KB) | One giant template | Slow to edit and render | Split into includes/macros | L | Phase 6 — needs sign-off |

### 4.2 Needs verification

| ID | Category | Sev (if confirmed) | File:line | Question | How I'll confirm it |
|---|---|---|---|---|---|
| NV-01 | Scalability | High | database.py:554-555, gunicorn.conf.py:9, compose.yaml (postgres) | Pool max 20 per worker × up to 8 workers = 160 > Postgres default `max_connections` 100 | Load test at 200 users (Phase 5); count connections in `pg_stat_activity` |
| NV-02 | Scalability | High | SCALE-02 | How long boot-time `ALTER TABLE … IF NOT EXISTS` blocks traffic | On `att_test`: hold a reader transaction, run the migration, check `pg_locks` |
| NV-03 | Tests | Medium | tests/conftest.py | Do tests also reach Anthropic/Gemini, Expo push, the security-alert webhook, SMTP or Turnstile? It depends on your `.env`, which I'm not allowed to read | You confirm which keys are set, or I make the TEST-01 fix regardless |
| NV-04 | Performance | Medium | check-in (face match), uploads (ClamAV) | Synchronous CPU/IO-heavy work in request threads | Load test on check-in |
| NV-05 | Correctness | Medium | blueprints/leave.py (approval path) | Does approving a multi-day leave deduct balance for Sundays/holidays (BUG-07)? | **Resolved: yes, confirmed by reading leave_action; see BUG-07** |
| NV-06 | Mobile | Medium | mobile/src (18 `react-hooks/exhaustive-deps`) | Each could be a stale-closure bug | Review each hook |
| NV-07 | Dead code | Low | utils/ai_resume_parser.py | Used only by tests; is this a planned feature? | Your input |
| NV-08 | Dead code | Low | assets/{mobile_app,waving_avatar,hero_dashboard}.jpg (~1.9 MB) | No reference in the repo; maybe used by an external hosted page | Your input |
| NV-09 | Security | Medium | SEC-01 | Are production images ever built from a working copy rather than a clean clone? | Your input / `deploy.sh` check |
| NV-10 | Correctness | Low | utils/salary_utils.py:263-299 | Money computed in `float` with `round(…, 2)`; what is the payroll rounding policy (paise vs rupee)? | Your input |
| NV-11 | Correctness | Medium | utils/salary_utils.py:269, employee_portal.py:684 | `"Present"` attendance type is paid as a **half day**; intended? | Your input |
| NV-12 | Tests | — | coverage | Untested critical paths | **Done** (coverage run 2026-10-07) → confirmed as TEST-04 |
| NV-13 | Frontend | — | templates | JS console errors and broken links | Headless browser crawl (`tests_e2e`/Playwright) |
| NV-14 | Dependencies | Medium | mobile (Expo/Jest/Metro chains, ~60 advisories) | No non-breaking fix available (npm suggests downgrades) | Track upstream releases; upgrade Expo SDK when fixed |
| NV-15 | Security | Medium | BUG-02 | Can the setup wizard be completed on a worker that has fallen back to SQLite? | Test after the BUG-02 fix (should become impossible) |
| NV-16 | Security | High | SEC-07 | Does any **production** tenant schema lack a table that exists in `public`? | Owner runs `qa_audit/tools/bug01_impact_check.py` (read-only); its "Tenant isolation" section lists every missing table per tenant |
| NV-17 | Correctness | High | BUG-01 / BUG-30 | Are stored attendance classifications in production already wrong? | Same script: recomputes each stored status with the company's own rules and the employee's shift (§7) |
| NV-18 | Security (policy) | High | blueprints/employee_portal.py:942 (`/api/employee/checkin`) | Mobile self check-in needs only the login token (no QR/face/fingerprint) and never consults the company's check-in policy (`allowed_checkin_combos`, M1). Is token-only mobile check-in an intended, separately allowed method? | Owner decision. If not intended: gate it behind a company setting that defaults to off when face/QR is required |
| NV-19 | Correctness (payroll) | High | BUG-05 | Which net-pay calculation is authoritative: the **disbursed amount** (per-day salary with the company's late/half-day rates, no statutory deductions) or the **payslip** (CTC structure, LOP per absent day on a 26-day basis, PF/PT/TDS, half and late days paid in full)? Today an employee's payslip net can differ from the amount disbursed | Your input |

### 4.3 Summary counts (confirmed register)

| Category | High | Medium | Low | Total |
|---|---|---|---|---|
| BUG (correctness) | 3 | 14 | 17 | 34 |
| SCALE | 5 | 3 | 0 | 8 |
| TEST | 1 | 4 | 0 | 5 |
| PERF | 0 | 2 | 5 | 7 |
| SEC | 1 | 4 | 5 | 10 |
| CONFIG | 0 | 2 | 3 | 5 |
| ERR | 0 | 1 | 0 | 1 |
| DEP | 0 | 1 | 0 | 1 |
| DUP | 0 | 0 | 14 | 14 |
| DEAD | 0 | 0 | 7 | 7 |
| QUAL (incl. complexity, types) | 0 | 0 | 19 | 19 |
| MISP | 0 | 0 | 2 | 2 |
| JS / MOB (frontend & mobile lint) | 0 | 0 | 5 | 5 |
| A11Y | 0 | 0 | 6 | 6 |
| UX / CONN | 0 | 0 | 2 | 2 |
| **Total** | **10** | **31** | **85** | **126** |

Plus 19 "Needs verification" items. 12 rows are deferred to Phase 6 (§6). **4 rows** are being handled on `security-fixes` by the other session (SEC-04, SEC-05, SEC-06, part of CONFIG-02), and the Razorpay part of TEST-01 too.

## 5. Phase 4 plan (approved)

Starts only after `security-fixes` is finished and `qa-audit-fixes` is rebased onto it (§0.2). One commit per issue (`fix(ID): …`). A failing test comes first for every bug, security or measurable performance fix. Targeted tests run per fix; the full suite runs every 5–8 commits and at the end, and must be green before merge.

| Batch | Issues (in order) |
|---|---|
| 1 · Hotfixes | **BUG-02** (no SQLite fallback outside development; fail loudly) → **BUG-01 + BUG-30** (per-company, shift-aware classification and payroll rules) |
| 2 · Test infrastructure | **TEST-02** (configurable/parallel test DB) → TEST-01 (non-Razorpay external keys) → TEST-03 (cache resets) |
| 3 · Remaining High | SCALE-08 (SSE thread hold) → SCALE-09 (pool rebuild on exhaustion) → SCALE-06 (email-queue claim race) → SCALE-01 (single scheduler) → SCALE-02 (migrations once per deploy) → SEC-07 (after the owner picks option A/B) |
| 4 · Prioritised Medium | BUG-03, BUG-04 (attendance %) → BUG-05 (+PERF-03) (payslip summary) → BUG-17, 18, 19, 20, 23 (IST dates) → ERR-01 (JSON API errors) → DEP-01 (axios) |
| 5 · Other Medium | TEST-04 (tests land with the fixes they cover), SCALE-07, SEC-08, SEC-09, BUG-06, BUG-07, BUG-31, BUG-32, BUG-28, PERF-01, PERF-02, CONFIG-01, SEC-01, CONFIG-03, SCALE-04 |
| 6 · Low | Low S items, then Low M items (skipping the Phase 6 rows) |

Left to the `security-fixes` session: SEC-04, SEC-05, SEC-06, the bandit part of CONFIG-02, and the Razorpay part of TEST-01.

## 6. Phase 6 — large refactors (NOT started; needs owner sign-off)

| ID | What | Why it's large | Suggested approach |
|---|---|---|---|
| QUAL-11 | 16 modules > 800 lines (admin_views 2,715 … core_tables 829) | Touches most routes | Split one blueprint per sub-feature, one PR each, starting with `admin_views.py` |
| QUAL-12 | 165 functions > 50 lines (88 without docstrings) | Broad | Opportunistically, whenever a function is touched for another fix |
| QUAL-13…18 | Six F-rated functions (CC 43–84): `add_employee_page`, `employee_portal`, `performance_import`, `analytics`, `attendance` check-in, `api_employee_qr_face_checkin` | Core flows; high regression risk | Characterisation tests first, then extract validation/service functions |
| QUAL-19 | `templates/employee_portal.html` (212 KB) | One template for the whole portal | Split into `{% include %}` partials per tab |
| QUAL-01 | 308 broad `except Exception` | Many are deliberate fail-open choices | Audit auth, payroll and billing paths first and narrow where fail-closed is correct |
| SCALE-05 | Local-disk uploads, face images and QR codes | Infrastructure change (object storage, migration of existing files) | S3-compatible backend behind `utils/storage.py` |
| DEAD-09 | ~400-line SQLite fallback layer in `database.py` | After BUG-02 it's dev-only | Move to a dev-only module or delete |

## 7. BUG-01 / BUG-30: can existing data already be wrong, and how to check

**What could be stored wrong.** Payslips are recomputed every time they're viewed, so the **stored** risk is in `attendance`:
- `status`: Full Day / Late / Half Day Login, decided at check-in from shift start + grace and the half-day time.
- `logout_status` and `attendance_type`: decided at check-out from the shift times.

Payroll then pays from `attendance_type`. Also stored: `salary_disbursement_items.amount` (payout batches prepared by the nightly job) and any payslips already **emailed**.

**When it could have happened**
- *BUG-01:* only if more than one company is served by the same app **and** their shift times, grace minutes, deduction rates or holiday/leave pay differ, from each other or from the default schema (which every worker loads at startup). If every company uses identical settings, BUG-01 changed nothing.
- *BUG-30:* any employee **with an assigned shift** who checked in through the mobile app, the admin API, offline sync or QR + face was classified with the company default shift instead.

**How to check (read-only, run where the app's DB variables are set, e.g. the app container)**

```
python qa_audit/tools/bug01_impact_check.py --since 2026-07-01          # all companies
python qa_audit/tools/bug01_impact_check.py --since 2026-07-01 --schema acme --samples 20
```

The script opens its connection with `default_transaction_read_only=on`, so it can't change data. It prints three things:
1. Each company's settings side by side. If they're all identical, BUG-01 had no effect.
2. Per company and month, how many stored attendance rows differ from what that company's own rules and the employee's assigned shift give, with example rows.
3. Every tenant table missing from a tenant schema, which resolves to the shared `public` table (SEC-07 / NV-16).

Mismatches are leads, not proof. A company that changed its shift times, or rows corrected by hand, can differ legitimately.

**If mismatches appear in months already paid:** re-run the payroll comparison for those months after the BUG-01/BUG-30 fix, and decide with HR whether to correct the stored rows (a one-off script I can write, run only with your approval) and settle any differences in the next payroll.
