# Stack review: what is current, what was changed, what to do next

Scope: "make it fast, use the best tools, upgrade everything". The approach was
to **measure first** and only change what the numbers or an end-of-life notice
justified. Anything that could damage tenant isolation, data, or a production
database is listed under *Recommended, not done* with the reason and the trigger
for doing it.

## 1. Verdict

The platform is already on a modern stack. The request handlers are fast; the
time is lost in **database round-trips**, and a few supporting components are
stale or end-of-life.

| Component | Was | Status |
|---|---|---|
| Python | 3.13 (working tree) | current — keep |
| Flask / Werkzeug / gunicorn / psycopg2 / redis-py / cryptography / numpy / pillow | all at the newest release (floors only, no lockfile) | **already latest**; added a hash-pinned lock so it *stays* reproducible |
| PostgreSQL | 16 | supported to Nov 2028 — keep (major upgrade needs dump/restore; see §5) |
| Redis | 7-alpine | **→ 8-alpine** (cache only: no data to migrate) |
| nginx / certbot / ClamAV | floating `alpine` / `latest` / `stable` | keep (auto-update tags) |
| Loki | 3.3.2 | **→ 3.6.18** |
| Grafana | 11.4.0 | **→ 12.4.13** |
| Promtail | 3.3.2 | **end-of-life (Mar 2026) → Grafana Alloy 1.20.1** (config provided + validated) |
| Mobile (Expo 57 / RN 0.86) | aligned in the working tree | keep — Expo 58 is still the `next` channel |
| `entrypoint.sh` | runs `migrate_plans.py` | that file does not exist in the repo; step removed |

## 2. Measurement: where the time goes

Round-trips to the database per request, on the same data, warm (median of 5).
A "round-trip" is a statement the server must answer: every query **plus** the
`SET search_path` that `get_db_connection()` sends on *every* borrow.

| Request | Before | Batched guard (default) | + skip redundant `SET` (opt-in) |
|---|---|---|---|
| `GET /admin` | 21 | 17 | **14** |
| `GET /employees` | 26 | 22 | **20** |
| `GET /today_present` | 19 | 15 | **13** |
| `GET /monthly_report` | 19 | 15 | **12** |
| `GET /salary_report` | 26 | 22 | **16** |
| admin JSON API | 8 | 4 | **2** |

Why it matters: handler time is single-digit milliseconds, but against a
networked Postgres (RDS) each round-trip is ~1–5 ms, so an admin page was
spending **40–100 ms waiting**, about a third of it on `SET search_path`.
Local numbers (3–8 ms/request) understate this because the database is on the
same machine. Replacing Flask or gunicorn would not touch this cost.

Reproduce: `python scripts/profile_requests.py` (disposable database only;
it refuses unless `DB_NAME` contains `test`). Run it again with
`DB_SKIP_REDUNDANT_SET_SEARCH_PATH=true` for the right-hand column.

## 3. What changed

| Change | Effect | Risk / default |
|---|---|---|
| **One query for the three per-request guard checks** (`utils/request_guard.py`, `utils/auth.py::_prime_guard_state`): kill-switch flag, account active, password-changed-since-login | −4 round-trips on every authenticated web request | Same semantics, per-request cache bound to the request object, dropped at request end, 5 s hard TTL; falls back to the original queries on any error. **On by default.** Two bugs in the first version were caught by the existing suites and fixed (stale password stamp; cache outliving a preserved context) — both have regression tests. |
| **Throttled expired-token purge** (`TOKEN_PURGE_INTERVAL_SECONDS`, default 60) | removes a write + borrow from every mobile/API request | Purge is housekeeping only — every lookup already filters `expires_at > NOW()`. Per tenant schema. `0` restores the old behaviour. **On by default.** |
| **Skip a redundant `SET search_path`** (`DB_SKIP_REDUNDANT_SET_SEARCH_PATH`) | −2 to −10 round-trips per page | **Opt-in, default OFF.** A wrong skip would serve one tenant from another's schema. State is kept on the psycopg2 connection itself; cleared on dirty/closed returns and failed SETs; forced real `SET` every `DB_SEARCH_PATH_REVALIDATE_EVERY` (200) reuses; adversarial tests incl. 8 threads × interleaved tenants. Enable on **staging first**, compare, then production. |
| `requirements.lock` + `scripts/relock.sh` | reproducible, hash-verified builds (`--require-hashes`) | Built with `uv` for Linux/py3.13. Containerfile uses it only with `--build-arg REQUIREMENTS_FILE=requirements.lock` (default unchanged). CI shows a non-blocking "lock is stale" warning. Verified: installs under hash enforcement on Python 3.13 and imports (except `dlib`, which needs a compiler). |
| Redis 7 → 8, Loki 3.3 → 3.6, Grafana 11.4 → 12.4 | supported versions | Redis is a cache with persistence disabled; Loki/Grafana config format unchanged. Not run (no container runtime here). |
| Promtail → **Alloy** (`observability/alloy/`) | Promtail is EOL: no more security fixes | `alloy fmt` + `alloy validate` pass; matches Alloy's own conversion of the old config. Old files kept for rollback. Cutover steps in `observability/README.md`. |
| `entrypoint.sh` dead step removed | a real migration failure would no longer be hidden behind it | none |

## 4. Verification

* New tests: `test_request_guard` (21), `test_search_path_tracking` (11).
* Re-run: auth hardening, session risk, API tokens, route guards, device risk,
  BOLA ownership, auth, auth routes/blueprint, auth utils.
* **Full existing suite, run locally** (2,991 tests, embedded PostgreSQL 16,
  Python 3.12 — this machine has no 3.13, so the 3.13 claim rests on the CI
  matrix and the hash-pinned install check above): 2,877 passed, 16 skipped,
  **42 failed, 56 errors**. Triage of every one of them:
  * **22 failures — mine, by design, fixed in the tests.** The strict
    location rule for mobile punches (`MOBILE_REQUIRE_LOCATION`) rejects the old
    fixtures that punch without coordinates. `tests/conftest.py` now defaults it
    off for the legacy suite and `test_punch_integrity.py` turns it on for the
    tests that exercise the rule. Re-run of the six affected files: 264 passed.
  * **1 failure — a real regression from the security phase, fixed.**
    `platform_admin_delete_tenant` was switched to the owner connection
    (`get_owner_master_db`) but the test still injected its "DROP SCHEMA fails"
    fault into `get_master_db`, so the fault never fired and the drop really ran.
    The route now imports the owner helper at module level and the test patches
    that name. Re-run with `test_platform_admin.py`: 113 passed.
  * **19 failures + 56 errors — environmental, now verified.** Every traceback
    was `face_recognition is None`, because `dlib` (a compiled dependency) was
    not installed here. With it installed, the seven affected files pass
    **177/177**. `dlib` could not be compiled on this machine (no C++
    toolchain), so the test venv used `dlib-bin` 20.0.1.post1, a community-built
    wheel (sha256 `86a3f7e0…e029`) — **not** the official dlib project and not
    used in the lock or image. `face-recognition` 1.3.0 and
    `face-recognition-models` 0.3.0 were installed from `requirements.lock`
    under `--require-hashes`. Because the tests mock most of the library, this
    shows the application code is correct, not that dlib's detector works on
    real faces.
  * Every test that failed or errored in the full run now has a passing
    re-run. What has **not** been done is a single uninterrupted pass of all
    2,991 tests on the final tree (about four hours here); the fixes were
    verified file by file instead.
* **Not run here:** the container image build from the lock (needs a container
  runtime and a ~15-minute `dlib` compile), the Alloy/Loki/Grafana/Redis images,
  and a load test against a networked database. The opt-in switch should be
  measured on staging with real RTT before it is turned on.

## 5. Recommended, not done (and why)

1. **psycopg2 → psycopg 3** (`psycopg[binary,pool]`). The real fix for the
   round-trip tax: pipeline mode sends `SET search_path` and the first query in
   one trip, and it has a built-in pool. Cost: ~240 call sites, different
   exception/cursor semantics, `psycopg2.errors` imports. Do it as a staged
   migration behind the existing `database.py` wrapper, one blueprint at a time,
   with the full suite green at each step. Until then the opt-in switch captures
   most of the benefit.
2. **Replace `face_recognition`/`dlib`.** Unmaintained since 2020, needs a
   10–20 minute compile and a `pkg_resources` shim, and runs HOG detection on
   the request thread (hundreds of ms per punch, more with the liveness burst).
   A maintained ONNX-runtime embedding model would be faster and more accurate,
   and removes the toolchain from the image. It changes the matching distance
   scale, so it needs a bake-off on **your** enrolled photos and re-tuned
   thresholds — not a blind swap. Short of that: move verification to the `rq`
   worker already in `requirements.txt`.
3. **PostgreSQL 16 → 17/18.** No pressing reason (16 is supported to Nov 2028).
   When you do: new major in a second container, `pg_dump | pg_restore` (or
   `pg_upgrade`) rehearsed on a restored backup, `scripts/restore_test.sh` to
   prove it, then cut over. Never just change the image tag on an existing volume.
4. **Connection budget.** `workers (≤8) × DB_POOL_MAX_CONN (20)` can reach 160
   connections against PostgreSQL's default `max_connections=100`, and
   `DB_POOL_MIN_CONN=5` keeps 40 idle. Size `DB_POOL_MAX_CONN` to roughly
   threads + headroom (≈8), or put PgBouncer in front. Left unchanged because
   the right number depends on your instance size and concurrency.
5. **Tenant lookup per request** (`SELECT … FROM tenants WHERE subdomain=…`) is
   cacheable for a few seconds, but a suspension or billing lock would then lag
   by that TTL; do it together with explicit invalidation in the platform-admin
   write paths.
6. **Python 3.14** once `dlib`/`webauthn` publish wheels for it; **Expo 58** when
   it leaves the `next` channel (`npx expo install expo@latest --fix`, then
   `npx expo-doctor`).
7. **Not recommended:** swapping Flask for an async framework, or gunicorn for
   uvicorn/granian. The app is synchronous and DB-bound with 3–8 ms handlers;
   the change would be large and move no measurable number.
