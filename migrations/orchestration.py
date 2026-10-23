import os
import re

from database import get_db_connection, owner_privileges, grant_runtime_privileges
from extensions import app_log

from .core_tables import _init_core_tables
from .column_migrations import _run_schema_migrations
from .seed import _seed_defaults_and_admin


def init_db(seed_admin=True):
    """Create/upgrade the schema, then seed defaults. Split into three
    ordered phases -- table creation, ALTER-TABLE migrations for existing
    installs, and one-time seeding -- so each phase is independently
    readable/testable instead of one 1000+ line function; the phases must
    still run in exactly this order (migrations assume their tables
    already exist, seeding assumes its columns already exist).

    seed_admin=False skips seeding the env-var admin (but still creates
    the blank company_settings row) -- see _seed_defaults_and_admin's
    docstring; used by init_tenant_db() for new SaaS tenant schemas."""
    # DDL + seeding run on the owner role when DB_OWNER_USER is configured
    # (no-op otherwise); the runtime role is then granted DML on whatever
    # tables this just created/changed -- see database.owner_privileges().
    with owner_privileges():
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        _init_core_tables(cursor, db)
        _run_schema_migrations(cursor, db)
        _seed_defaults_and_admin(cursor, db, seed_admin=seed_admin)
        grant_runtime_privileges(cursor)
        cursor.close()
        db.close()


_CREATE_TABLE_RE = re.compile(r'\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([\w."]+)', re.IGNORECASE)


class _TableRecorder:
    """Cursor proxy that notes every table a migration creates, so the set
    of tables a tenant must have comes from the migrations themselves, not
    from a hand-kept list (QA SEC-07). Schema-qualified names (att_master.x)
    aren't tenant tables and are ignored."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.tables = set()

    def execute(self, query, *args, **kwargs):
        if isinstance(query, str):
            for name in _CREATE_TABLE_RE.findall(query):
                if "." not in name:
                    self.tables.add(name.strip('"').lower())
        return self._cursor.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


def _schema_tables(cursor, schema):
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema,))
    return {r[0] for r in cursor.fetchall()}


def _schema_exists(cursor, schema):
    cursor.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (schema,))
    return cursor.fetchone() is not None


def _migrate_tenant(schema):
    """Create any missing table in one tenant schema, then apply the column
    migrations. Returns (tables the migrations create, tables present
    afterwards). Running only the column migrations here -- as this used
    to -- never gave an existing tenant a table added to core_tables after
    it was provisioned, and its queries then fell through the search_path
    to public.<table>: the default company's data (QA SEC-07)."""
    from database import get_tenant_db
    db = get_tenant_db(schema)
    cursor = db.cursor(buffered=True)
    recorder = _TableRecorder(cursor)
    try:
        before = _schema_tables(cursor, schema)
        try:
            _init_core_tables(recorder, db)
            _run_schema_migrations(recorder, db)
        finally:
            # Clears an aborted transaction so the check below can still run.
            # Uncommitted work was always discarded here anyway: the pool
            # rolls back an open transaction when the connection is returned.
            try:
                db.rollback()
            except Exception:  # nosec B110 -- nothing to roll back
                pass
            after = _schema_tables(cursor, schema)
            created = sorted((after - before) & recorder.tables)
            if created:
                app_log.warning(
                    "SEC-07 repair: tenant schema %s lacked table(s) %s -- created them. Rows this "
                    "company wrote to those tables before now are in public.<table>, mixed with the "
                    "default company's (see QA_AUDIT.md SEC-07).", schema, ", ".join(created))
        return recorder.tables, after
    finally:
        cursor.close()
        db.close()


def _record_missing_tables(results):
    """Write each tenant's still-missing tables (or NULL) to att_master.tenants."""
    from database import get_master_db
    mconn = get_master_db()
    mcur = mconn.cursor()
    for schema, missing in results.items():
        mcur.execute("UPDATE tenants SET schema_missing_tables=%s WHERE db_name=%s", (missing, schema))
    mconn.commit()
    mcur.close()
    mconn.close()


def _run_migrations_for_all_tenants():
    """Bring every already-provisioned tenant schema up to date: create any
    missing table, then re-run _run_schema_migrations(), so a tenant
    created before a given table or column was added still receives it
    (e.g. a tenant provisioned before assigned_hr_username existed would
    500 on view_employees() forever otherwise). Every statement run here is
    idempotent (IF NOT EXISTS-guarded), so re-running is safe.

    Afterwards each tenant is checked against every table the migrations
    create; a tenant still missing one is recorded in
    att_master.tenants.schema_missing_tables, and the router answers 503
    for it instead of letting its queries read the shared public table
    (QA SEC-07). The default company (DB_NAME, already migrated by
    init_db()) has no schema of its own -- its tables are public's -- so it
    has nothing to fall through to and is never flagged."""
    try:
        from database import get_master_db
        mconn = get_master_db()
        mcur = mconn.cursor(buffered=True)
        mcur.execute("SELECT db_name FROM tenants")
        schemas = [r[0] for r in mcur.fetchall()]
        mcur.close()
        mconn.close()
    except Exception as exc:
        app_log.warning("Migration: failed to list tenant schemas: %s", exc, exc_info=True)
        return

    default_schema = os.environ.get("DB_NAME", "employee_attendance")
    required, present, results = set(), {}, {}
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        existing = {s for s in schemas if _schema_exists(cursor, s)}
    finally:
        cursor.close()
        db.close()

    for schema in schemas:
        if schema not in existing:
            if schema == default_schema:
                results[schema] = None
            else:
                results[schema] = "(no schema)"
                app_log.critical("SEC-07: tenant %s has no schema -- its requests get 503 until it is "
                                 "re-provisioned", schema)
            continue
        try:
            # DDL on the owner role when DB_OWNER_USER is configured (no-op
            # otherwise), then grant the runtime role DML on what it created.
            with owner_privileges():
                created_by_migrations, present[schema] = _migrate_tenant(schema)
                from database import get_tenant_db
                db = get_tenant_db(schema)
                cursor = db.cursor(buffered=True)
                try:
                    grant_runtime_privileges(cursor)
                finally:
                    cursor.close()
                    db.close()
            required |= created_by_migrations
        except Exception as exc:
            app_log.warning("Migration: tenant schema '%s' failed: %s", schema, exc, exc_info=True)

    # Every tenant runs the same migrations, so the union of what they
    # create is the reference, even for a tenant whose run stopped early.
    for schema, tables in present.items():
        missing = sorted(required - tables)
        results[schema] = ", ".join(missing) or None
        if missing:
            app_log.critical(
                "SEC-07: tenant schema %s is missing table(s) %s after migrations -- its requests get "
                "503 until they exist (MIGRATIONS_FORCE=1 retries)", schema, ", ".join(missing))
    if required or len(results) == len(schemas):
        try:
            _record_missing_tables(results)
        except Exception as exc:
            app_log.error("Migration: failed to record tenant schema completeness: %s", exc, exc_info=True)


def init_tenant_db(schema_name: str):
    """Initialize schema in a freshly created tenant schema. seed_admin=False
    -- provision_tenant() (blueprints/org.py) inserts the real admin right
    after this using the credentials the customer entered at signup; see
    _seed_defaults_and_admin's docstring for why seeding the env-var admin
    here too was a real bug, not a redundant no-op."""
    from flask import g as _g
    _g.tenant_db = schema_name
    init_db(seed_admin=False)
