"""Run the startup migrations once per code version, one process at a time.

wsgi.py runs this on every gunicorn worker boot, and workers boot together
and recycle every ~1,000 requests (gunicorn max_requests). Running
init_master_db + init_db + every tenant's migrations on each of those boots
made boot time grow with the tenant count, let workers migrate in parallel,
and took ACCESS EXCLUSIVE locks on live tables (ALTER TABLE ... ADD COLUMN
IF NOT EXISTS locks even when the column exists) -- QA SCALE-02.

Now a worker takes a Postgres advisory lock (so workers migrate one at a
time), compares the version of the migration code with the one recorded in
att_master.schema_migrations, and only migrates when they differ -- i.e.
once per deploy. A failed migration doesn't record the version, so the
next boot retries. MIGRATIONS_FORCE=1 forces a re-run (e.g. after a
migration was skipped by its own per-statement error handling). New tenant
schemas are still built at signup by init_tenant_db().
"""
import hashlib
import os
import pathlib

from database import get_db_connection
from extensions import app_log

# Arbitrary, fixed key for pg_advisory_lock ("HRZM").
_LOCK_KEY = 0x48525A4D


def migrations_version():
    """Hash of every migration module's source: changes whenever a migration does."""
    digest = hashlib.sha256()
    for path in sorted(pathlib.Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _recorded_version(cur):
    cur.execute("CREATE SCHEMA IF NOT EXISTS att_master")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS att_master.schema_migrations ("
        "id INT PRIMARY KEY CHECK (id = 1), version VARCHAR(64) NOT NULL, "
        "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    cur.execute("SELECT version FROM att_master.schema_migrations WHERE id = 1")
    row = cur.fetchone()
    return row[0] if row else None


def _record_version(cur, version):
    cur.execute(
        "INSERT INTO att_master.schema_migrations (id, version, applied_at) VALUES (1, %s, NOW()) "
        "ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, applied_at = EXCLUDED.applied_at",
        (version,),
    )


def run_startup_migrations(init_master_db, init_db, run_all_tenant_migrations):
    """Returns True if this process ran the migrations, False if they were
    already current. Exceptions from the migrations propagate (wsgi.py logs
    them) after the lock is released."""
    def _migrate():
        init_master_db()
        init_db()
        run_all_tenant_migrations()

    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
    except Exception as exc:
        # No advisory locks (the dev-only SQLite fallback): migrate the old way.
        app_log.warning("Migration gate unavailable (%s) -- running startup migrations unconditionally", exc)
        _migrate()
        return True
    try:
        version = migrations_version()
        if _recorded_version(cur) == version and os.environ.get("MIGRATIONS_FORCE") != "1":
            app_log.info("Startup migrations already at version %s -- skipped", version)
            return False
        app_log.info("Running startup migrations (version %s)", version)
        _migrate()
        _record_version(cur, version)
        return True
    finally:
        try:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
            cur.close()
        finally:
            db.close()
