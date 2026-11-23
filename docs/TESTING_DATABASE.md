# Test database setup

The test suite rewrites every table after each test, so it needs its own
local PostgreSQL database and a role that may run that reset. This page is the
fix for roadmap items HR-07, HR-08 and HR-09 (21 Nov 2026).

## 1. Check what pytest will use

```bash
python scripts/test_db_doctor.py            # reads ./.env
```

The doctor prints the server, the test database name (`HRZEST_TEST_DB`, plus
the xdist worker suffix), and the role. It then says whether that role may
reset the database, and ends with `READY` or a list of fixes. It never prints
passwords.

## 2. Least-privilege setup

Do this on a **test server only**. The app's runtime role never gets
superuser.

```bash
PG_ADMIN_USER=postgres PG_ADMIN_PASS=... \
HRZEST_OWNER_ROLE=hrzest_owner HRZEST_OWNER_PASS=... \
HRZEST_APP_ROLE=hrzest_app     HRZEST_APP_PASS=... \
python scripts/provision_db_roles.py
```

Then, as `postgres` (PostgreSQL 15 or later):

```sql
GRANT SET ON PARAMETER session_replication_role TO hrzest_owner;
```

Run pytest with:

```bash
DB_USER=hrzest_app DB_PASS=... DB_OWNER_USER=hrzest_owner DB_OWNER_PASS=... python -m pytest
```

Before PostgreSQL 15, only a superuser can set that parameter. Use a
disposable local instance in that case. CI's least-privilege job does exactly
this (`.github/workflows/deploy.yml`).

## 3. What the harness enforces

- **Test database name:** it must contain `test`.
- **Local hosts only:** `DB_HOST` must be local (`localhost`, `127.0.0.1`,
  `::1`). A disposable container host has to be named in
  `HRZEST_TEST_ALLOWED_DB_HOSTS`.
- **One run per database:** a second run on the same database is refused.
  Give each run its own `HRZEST_TEST_DB`.
- **Fail fast:** if the role can't reset the database, the run stops once
  with these instructions, rather than failing every test's teardown.
- **Safe teardown:** the cursor is always closed and caches are always
  cleared, even when a restore fails.

## 4. Services the tests don't need

- **ClamAV:** tests use a stand-in for the scanner. Outside development,
  uploads are rejected when the scanner is unavailable (fail closed), and the
  production startup check warns if `CLAMAV_HOST` is missing.
- **Redis:** it's blanked for tests. The data cache and the rate-limit store
  fall back accordingly.
