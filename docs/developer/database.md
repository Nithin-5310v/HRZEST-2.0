# Database

PostgreSQL 16. One database holds the platform registry plus **one schema per
company**. Code: `database.py`, `migrations/`, `utils/helpers/db.py`,
`utils/pii_crypto.py`.

## Schemas

| Schema | Holds |
| --- | --- |
| `att_master` | The platform: `tenants` (every company, its URL slug and schema), `tenant_applications`, billing tables, `platform_admins`, `login_otp_challenges`, SOC and telemetry tables, `schema_migrations` |
| `att_<company>` | One per company, ~60 tables: `employees`, `attendance`, `admin_users`, `company_settings`, `shifts`, leave, payroll, onboarding, performance, `tickets`, `notifications`, `api_tokens`, `audit_logs`, `security_events`, `roles`, `role_permissions`, ... |
| `public` (`DB_NAME`) | The server's default company, used when a request names no company |

## Getting a connection

| Function | Gives you |
| --- | --- |
| `get_db_connection()` | A connection to **the current request's company**: `search_path` is set to `g.tenant_db` (or `public`) every time it's borrowed |
| `with _db() as (cursor, conn):` | The same, as a context manager that always returns the connection; **it doesn't commit**, call `conn.commit()` |
| `get_master_db()` | `att_master` |
| `get_tenant_db(schema)` | A named company schema (scripts, cross-company jobs) |
| `transaction(conn)` | Several statements as one transaction (connections are autocommit by default) |

- **Pool:** `ThreadedConnectionPool`, 5 to 20 connections (`DB_POOL_MIN_CONN`,
  `DB_POOL_MAX_CONN`). When it's exhausted a request waits up to
  `DB_POOL_WAIT_SECONDS` (5), then gets a 503.
- **Statement timeout:** 30 seconds by default.
- `cursor(buffered=True)` and `dictionary=` are accepted and ignored; they're
  left over from the MySQL era. `conn.close()` returns the connection to the
  pool.
- **Which company:** `g.tenant_db` is set per request by `_resolve_tenant()`
  (`app_setup/tenant.py`): from the session, else the `/<company>/` URL prefix,
  else `DB_NAME`. See [architecture.md](architecture.md#which-company-a-request-belongs-to).

### When PostgreSQL is down

- **Production:** requests get **503** "Service temporarily unavailable" with
  `Retry-After: 30`, and a CRITICAL `db.unavailable` security event is raised
  (`DatabaseUnavailable`, `app_setup/errors.py`). The next request tries
  PostgreSQL again.
- **Development** (`APP_ENV=development` or `ALLOW_SQLITE_FALLBACK=true`): the
  app falls back to a local SQLite file, `instance/local_fallback.db`. It has no
  per-company schemas, so it's only good for a quick look. When tests suddenly
  fail en masse with "no such table", PostgreSQL was down and this fallback
  kicked in (see [troubleshooting.md](troubleshooting.md)).

### Owner and runtime roles (optional)

With `DB_OWNER_USER` / `DB_OWNER_PASS` set, the app's normal role (`DB_USER`) can
only read and write rows. A separate owner pool is used only inside
`with owner_privileges():` for migrations, creating companies and the audit
verifier. The runtime role also loses UPDATE and DELETE on the audit tables, so
the app itself can't rewrite history. Set up with
`scripts/provision_db_roles.py`. Without these variables, one role does
everything.

## Migrations

### When they run

On every worker start, `wsgi.py` calls `run_startup_migrations()`
(`migrations/runner.py`):

1. It takes a PostgreSQL advisory lock, so workers migrate one at a time.
2. It hashes the source of every `migrations/*.py` file and compares that with
   the version stored in `att_master.schema_migrations`.
3. If they differ, it runs, in order:
   - `init_master_db()`: `att_master` tables;
   - `init_db()`: the default schema (core tables → column migrations →
     default data);
   - `_run_migrations_for_all_tenants()`: core tables and column migrations for
     **every** company schema.
4. Only a fully successful run records the new version, so a failure is retried
   on the next start. `MIGRATIONS_FORCE=1` forces a run.

So migrations run **once per change to migration code**, not on every start. A
company whose schema is still missing tables afterwards is listed in
`tenants.schema_missing_tables` and its requests get 503 until that's fixed.

### Which file holds what

| File | Holds |
| --- | --- |
| `migrations/master.py` | `att_master` tables |
| `migrations/core_tables.py` | `CREATE TABLE IF NOT EXISTS` for every per-company table, plus seed roles and permissions |
| `migrations/column_migrations.py` | Changes to existing tables: `ADD COLUMN IF NOT EXISTS`, indexes, data fixes, backfills |
| `migrations/seed.py` | The `company_settings` row and the admin account from `.env` |
| `migrations/audit_integrity.py` | Hash-chained audit tables and their triggers |
| `migrations/soc_tables.py`, `telemetry_tables.py` | SOC console and telemetry tables |
| `migrations/runner.py`, `orchestration.py` | When and in what order the above run |

### Adding a table or column

Every statement must be safe to run again (`IF NOT EXISTS`), because migrations
re-run whenever any migration file changes.

**A new column** on a per-company table: add the statement to the list in
`_run_column_migrations()` in `migrations/column_migrations.py`. Each statement
runs and commits on its own. Real example:

```python
"ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS linked_employee_id VARCHAR(50) DEFAULT NULL",
```

If the table is created in `core_tables.py`, add the column to its
`CREATE TABLE` as well, so new companies get it directly.

**A new per-company table:** add `CREATE TABLE IF NOT EXISTS ...` to
`_init_core_tables()` in `migrations/core_tables.py`. Existing companies get it
too, because the migration run re-runs `_init_core_tables()` for every company
schema. If it has an `updated_at` column, attach the trigger with
`_attach_updated_at_trigger(cursor, "<table>")`.

**A one-time data change** (rewriting existing rows): guard it with
`_applied_migrations`, so it runs once per schema:

```python
cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='leave_declined_to_rejected_v1'")
if not cursor.fetchone():
    cursor.execute("UPDATE leave_requests SET status='Rejected' WHERE status='Declined'")
    cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('leave_declined_to_rejected_v1')")
    db.commit()
```

**A platform table:** add it to `migrations/master.py`.

After adding a migration, run the test suite: it creates a fresh database from
scratch, so a statement that only works on an existing schema fails there.

## Encrypted personal data

Employee `email` and `phone` (and some pay fields) are encrypted at rest.

- **Format:** AES-256-GCM, stored as `hz2:<base64>`. Keys are derived from
  `ENCRYPTION_KEY`. `ENCRYPTION_KEY_PREVIOUS` (comma-separated) lists old keys
  that are still accepted for reading after a rotation. Legacy Fernet values
  still decrypt.
- **Reading is automatic:** the connection decrypts any `hz2:` text when rows are
  fetched. Use `raw_text(cursor)` if you really need the stored form.
- **Writing:** `enc, bidx = encrypt_contact("email", value)` and store both
  `email=enc` and `email_bidx=bidx` (`utils/helpers/crypto.py`).
- **Searching:** encrypted values can't be compared in SQL. Look up by blind
  index: `WHERE email_bidx = ANY(%s)` with `contact_lookup_keys("email", value)`
  (one index per configured key, so rows indexed before a rotation still match).
- **Rotating keys:** `scripts/rotate_encryption_key.py`. Never just replace
  `ENCRYPTION_KEY`: existing values would become unreadable.

More: [ENCRYPTION_AT_REST.md](../ENCRYPTION_AT_REST.md).

## Backups and restore

`scripts/backup_db.sh` (encrypted; refuses to write plaintext), run by the
`hrzest-backup` timer on the server. Restoring and testing a restore:
[RESTORE.md](../RESTORE.md).

## Tests

The test suite uses its own databases (`att_test`, one per worker) and restores
them after every test. See [testing.md](testing.md).
