"""Factory-reset this deployment back to a blank, never-used state.

Wipes every tenant's data (employees, admin accounts, attendance, payroll,
leave, tickets, documents, security/session state, company branding) plus
uploaded files (photos, QR codes, employee documents) so the next visitor
hits the /setup wizard and creates a brand-new company + admin account from
scratch, exactly like a first-time install.

Deliberately schema-generic: discovers every non-system Postgres schema
(not just "public") so it also cleans att_master (the tenant registry) and
any self-registered org schemas from blueprints/org.py's create_org(), on
whichever database DB_HOST/DB_NAME in the environment point to — run this
script on the machine/host whose .env you want reset (locally, or over SSH
on the EC2 host against RDS), it uses the exact same env vars as the app.

Safety: dry-run by default. Nothing is deleted until you pass --yes.

Usage:
    python factory_reset.py                 # dry run — prints what would be wiped
    python factory_reset.py --yes           # actually wipe it
    python factory_reset.py --yes --schema public   # limit to one schema
"""
import argparse
import os
import shutil

import database as db

# Tables holding data that belongs to a specific buyer/tenant and must not
# survive a resale — order matters where foreign keys would otherwise block
# a delete (children before parents; TRUNCATE ... CASCADE makes the manual
# ordering a belt-and-suspenders concern rather than a strict requirement).
_WIPE_TABLES = [
    # Security / session / identity state — tied to the old owner's admin
    # accounts and devices; MUST be cleared, not just employees/attendance.
    # (TOTP secrets and WebAuthn/fingerprint public keys are columns on
    # admin_users/employees respectively, not separate tables — wiping
    # those two rows covers them.)
    "security_events", "login_attempts", "api_tokens", "known_login_ips",
    "audit_logs", "session_risk", "mobile_biometric_proofs",
    # Hash-chain head pointer for the two audit tables above -- must be
    # reset with them or the next audit row would chain from a head
    # that no longer exists (reported as tampering by verify_audit_chain).
    "audit_chain_head",
    # Core people data
    "attendance", "employees", "admin_users",
    "compoff_balance",
    # Payroll
    "salary_config", "payroll_config", "payroll_runs", "employee_incentives",
    "incentive_goals", "hike_config", "overtime_records",
    # Leave / resignation
    "leave_requests", "leave_balances", "leave_types", "resignation_requests",
    # Tickets / notifications / announcements
    "tickets", "notifications", "announcements", "email_queue",
    # Documents / onboarding
    "employee_documents", "employee_onboarding_tasks", "employee_onboarding",
    "onboarding_templates", "onboarding_template_tasks", "offer_letters",
    "employee_experience", "employee_education",
    # Performance
    "performance_reviews", "performance_kpis",
    # Scheduling / company config
    "shifts", "break_config", "holidays", "companies", "company_feature_settings",
    "id_card_templates",
    # Email / alerting (old owner's SMTP + webhook secrets)
    "email_config",
    # Multi-tenant registry (att_master only, but harmless no-op elsewhere)
    "tenants",
]

# Never touch these — schema bookkeeping, not tenant data.
_PRESERVE_TABLES = {"_applied_migrations"}

_UPLOAD_DIRS = [
    "dataset",
    # QR codes and employee documents both moved to private storage
    # (utils/storage.py's save_private()) -- qr_generator.py's
    # generate_qr() (QR images are a bearer attendance credential, never
    # safe under static/) and blueprints/documents.py (employee documents,
    # pre-existing). The old static/ paths are listed too in case this
    # runs against a database that predates either migration and still
    # has files sitting there.
    os.path.join("private_uploads", "qrcodes"),
    os.path.join("private_uploads", "employee_documents"),
    os.path.join("static", "qrcodes"),
    os.path.join("static", "employee_docs"),
]


def _discover_schemas(cur):
    cur.execute(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')"
    )
    return [r[0] for r in cur.fetchall()]


def _table_exists(cur, schema, table):
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
        (schema, table),
    )
    return cur.fetchone() is not None


def _row_count(cur, schema, table):
    cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
    return cur.fetchone()[0]


def plan(schemas):
    """Read-only pass: returns {schema: {table: row_count}} for reporting."""
    conn = db.get_db_connection()
    cur = conn.cursor()
    report = {}
    for schema in schemas:
        report[schema] = {}
        for table in _WIPE_TABLES:
            if table in _PRESERVE_TABLES:
                continue
            if _table_exists(cur, schema, table):
                report[schema][table] = _row_count(cur, schema, table)
    cur.close()
    conn.close()
    return report


def execute(schemas):
    conn = db.get_db_connection()
    cur = conn.cursor()
    for schema in schemas:
        for table in _WIPE_TABLES:
            if table in _PRESERVE_TABLES:
                continue
            if _table_exists(cur, schema, table):
                cur.execute(f'TRUNCATE TABLE "{schema}"."{table}" CASCADE')
                print(f"  wiped {schema}.{table}")
        # company_settings gets one fresh default row (setup_done=0) rather
        # than being left empty, matching what init_db() does on first boot.
        if _table_exists(cur, schema, "company_settings"):
            cur.execute(f'TRUNCATE TABLE "{schema}"."company_settings" CASCADE')
            cur.execute(f'INSERT INTO "{schema}"."company_settings" (setup_done) VALUES (0)')
            print(f"  reset {schema}.company_settings -> setup_done=0")
    # Startup migrations and default seeding run only when the migration
    # code changes (migrations/runner.py); forget the recorded version so
    # the next start re-runs them and re-seeds the defaults wiped above.
    if _table_exists(cur, "att_master", "schema_migrations"):
        cur.execute('TRUNCATE TABLE "att_master"."schema_migrations"')
        print("  cleared att_master.schema_migrations -> next start re-runs migrations")
    conn.commit()
    cur.close()
    conn.close()


def wipe_uploads(dry_run):
    root = os.path.dirname(os.path.abspath(__file__))
    for rel in _UPLOAD_DIRS:
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            path = os.path.join(d, name)
            if dry_run:
                print(f"  would remove {os.path.join(rel, name)}")
                continue
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                print(f"  removed {os.path.join(rel, name)}")
            else:
                try:
                    os.remove(path)
                    print(f"  removed {os.path.join(rel, name)}")
                except OSError as exc:
                    # This unconditionally printed "removed" before, even
                    # on failure -- a bad look for a script whose entire
                    # job is convincing an operator the wipe actually
                    # happened.
                    print(f"  FAILED to remove {os.path.join(rel, name)}: {exc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="Actually perform the wipe (default: dry run)")
    ap.add_argument("--schema", action="append", help="Limit to this schema (repeatable). Default: all discovered.")
    args = ap.parse_args()

    conn = db.get_db_connection()
    cur = conn.cursor()
    schemas = args.schema or _discover_schemas(cur)
    cur.close()
    conn.close()

    print(f"Target schemas: {schemas}")
    report = plan(schemas)
    total = 0
    for schema, tables in report.items():
        for table, count in tables.items():
            if count:
                print(f"  {schema}.{table}: {count} row(s)")
                total += count
    print(f"\nTotal rows that would be wiped: {total}")
    print("\nUpload directories:")
    wipe_uploads(dry_run=True)

    if not args.yes:
        print("\nDRY RUN ONLY — nothing was changed. Re-run with --yes to actually wipe.")
        return

    print("\nWiping now...")
    execute(schemas)
    wipe_uploads(dry_run=False)
    print("\nDone. Remember to also unset ADMIN_PASSWORD/ADMIN_USERNAME in .env on this "
          "host before the app next starts, or init_db() will silently reseed an admin "
          "account before the buyer ever sees the /setup wizard.")


if __name__ == "__main__":
    main()
