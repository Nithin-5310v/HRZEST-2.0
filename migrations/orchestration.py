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


def _run_migrations_for_all_tenants():
    """Re-run _run_schema_migrations() against every already-provisioned
    tenant schema, not just whichever one happens to be active when
    init_db() is called (the "public"/default schema at startup, or a
    brand-new tenant's own schema via init_tenant_db()). Without this, an
    existing tenant created before a given migration was added to
    _run_column_migrations()/etc. never receives it -- e.g. a tenant
    provisioned before assigned_hr_username existed would 500 on
    blueprints/employees.py's view_employees() forever, since nothing else
    ever re-applies startup migrations to its schema. Every statement run
    here is already idempotent (IF NOT EXISTS-guarded) and independently
    try/except-guarded, so re-running them on every boot is safe -- same
    pattern as utils/email_utils.py's _active_tenant_schemas()."""
    try:
        from database import get_master_db, get_tenant_db
        mconn = get_master_db()
        mcur = mconn.cursor(buffered=True)
        mcur.execute("SELECT db_name FROM tenants")
        schemas = [r[0] for r in mcur.fetchall()]
        mcur.close()
        mconn.close()
    except Exception as exc:
        app_log.warning("Migration: failed to list tenant schemas: %s", exc, exc_info=True)
        return

    for schema in schemas:
        try:
            with owner_privileges():
                db = get_tenant_db(schema)
                cursor = db.cursor(buffered=True)
                _run_schema_migrations(cursor, db)
                grant_runtime_privileges(cursor)
                cursor.close()
                db.close()
        except Exception as exc:
            app_log.warning("Migration: tenant schema '%s' failed: %s", schema, exc, exc_info=True)


def init_tenant_db(schema_name: str):
    """Initialize schema in a freshly created tenant schema. seed_admin=False
    -- provision_tenant() (blueprints/org.py) inserts the real admin right
    after this using the credentials the customer entered at signup; see
    _seed_defaults_and_admin's docstring for why seeding the env-var admin
    here too was a real bug, not a redundant no-op."""
    from flask import g as _g
    _g.tenant_db = schema_name
    init_db(seed_admin=False)
