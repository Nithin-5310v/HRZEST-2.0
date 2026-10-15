#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-time provisioning of the split database roles (see database.py,
"Optional privileged owner role").

Before: one role (usually the Postgres superuser) owns every object and is
what the web tier logs in as -- so SQL injection / a leaked .env is also
DROP TRIGGER, DROP SCHEMA and "rewrite the audit log".

After:
    hrzest_owner  NOSUPERUSER  owns every schema/table; used only by boot
                               migrations, tenant provisioning/deletion and
                               the audit verifier (DB_OWNER_USER/DB_OWNER_PASS)
    hrzest_app    NOSUPERUSER  SELECT/INSERT/UPDATE/DELETE on tenant data;
                               INSERT+SELECT only on audit_logs/
                               security_events; no DDL, no chain-head access
                               (DB_USER/DB_PASS)

Usage (run once, from a host that can reach Postgres, as a role that may
CREATE ROLE and own the objects -- typically the current superuser):

    PG_ADMIN_USER=postgres PG_ADMIN_PASS=... \\
    DB_NAME=employee_attendance DB_HOST=127.0.0.1 \\
    HRZEST_OWNER_ROLE=hrzest_owner HRZEST_OWNER_PASS=... \\
    HRZEST_APP_ROLE=hrzest_app     HRZEST_APP_PASS=... \\
        python scripts/provision_db_roles.py [--dry-run]

Then in the app's .env:
    DB_USER=hrzest_app        DB_PASS=<HRZEST_APP_PASS>
    DB_OWNER_USER=hrzest_owner  DB_OWNER_PASS=<HRZEST_OWNER_PASS>

Idempotent: safe to re-run (also after adding tenants) -- it re-asserts
ownership and grants. `--dry-run` prints the statements without executing.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def _need(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"missing required environment variable {name}")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    import psycopg2
    from psycopg2 import sql
    from database import runtime_grant_statements

    owner = _need("HRZEST_OWNER_ROLE")
    app_role = _need("HRZEST_APP_ROLE")
    owner_pw = _need("HRZEST_OWNER_PASS")
    app_pw = _need("HRZEST_APP_PASS")
    for r in (owner, app_role):
        if not _IDENT.match(r):
            sys.exit(f"unsafe role name {r!r} (letters, digits, underscore only)")
    if owner == app_role:
        sys.exit("owner and app roles must be different")

    dbname = os.environ.get("DB_NAME", "employee_attendance")
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"), port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ.get("PG_ADMIN_USER") or os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("PG_ADMIN_PASS") or os.environ.get("DB_PASS", ""),
        dbname=dbname, sslmode=os.environ.get("DB_SSLMODE", "prefer"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    def run(stmt, label=None):
        text = stmt.as_string(conn) if hasattr(stmt, "as_string") else stmt
        shown = re.sub(r"PASSWORD\s+'[^']*'", "PASSWORD '***'", text)
        print(("[dry-run] " if args.dry_run else "") + (label or shown))
        if not args.dry_run:
            cur.execute(stmt)

    def role_exists(name):
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,))
        return cur.fetchone() is not None

    # 1) roles -- neither is a superuser, neither can create roles/databases.
    for name, pw in ((owner, owner_pw), (app_role, app_pw)):
        verb = "ALTER" if role_exists(name) else "CREATE"
        run(sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}")
            .format(sql.Identifier(name), sql.Literal(pw)))
    # The owner needs to create tenant schemas; the app role only connects.
    run(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(app_role)))
    run(sql.SQL("GRANT CONNECT, CREATE ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(owner)))
    run(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    run(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname)))
    run(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(app_role)))
    run(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(owner)))

    # 2) hand every application object to the owner role.
    cur.execute("""
        SELECT nspname FROM pg_namespace
        WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'
        ORDER BY nspname
    """)
    schemas = [r[0] for r in cur.fetchall()]
    for schema in schemas:
        if not _IDENT.match(schema):
            print(f"skipping schema with unexpected name {schema!r}")
            continue
        run(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(owner)))
        cur.execute(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind IN ('r','p','S','v','m') "
            # sequences owned by a column follow their table
            "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=c.oid AND d.deptype IN ('a','i') AND c.relkind='S')",
            (schema,))
        for relname, relkind in cur.fetchall():
            kind = {"r": "TABLE", "p": "TABLE", "S": "SEQUENCE", "v": "VIEW", "m": "MATERIALIZED VIEW"}[relkind]
            run(sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                sql.SQL(kind), sql.Identifier(schema), sql.Identifier(relname), sql.Identifier(owner)))
        cur.execute(
            "SELECT p.proname, pg_get_function_identity_arguments(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s AND p.prokind='f'", (schema,))
        for proname, args_sig in cur.fetchall():
            run(sql.SQL("ALTER FUNCTION {}.{}({}) OWNER TO {}").format(
                sql.Identifier(schema), sql.Identifier(proname), sql.SQL(args_sig), sql.Identifier(owner)))

    # 3) runtime grants for the app role in every schema (the app re-applies
    #    these per schema at boot too; doing it here makes the very first
    #    boot after the switch work without a privileged round-trip).
    for schema in schemas:
        stmts = runtime_grant_statements(schema, app_role)
        if not stmts:
            continue
        # ALTER DEFAULT PRIVILEGES applies to the role running it; run it
        # as the owner so tables the owner creates later are covered.
        run(f"SET ROLE {owner}")
        for stmt in stmts:
            run(stmt)
        run("RESET ROLE")

    cur.close()
    conn.close()
    print("done." if not args.dry_run else "dry run complete -- nothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
