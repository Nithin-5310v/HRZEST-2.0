"""Which PostgreSQL server, database and role will the test suite use -- and
will its per-test restore work there?

Roadmap HR-07/HR-08/HR-09 (21 Nov 2026): teardown failed with "permission
denied to set parameter session_replication_role", the postgres password
failed over TCP and the expected att_test database was missing, because the
server, database and credentials pytest actually used had never been pinned
down. Run this first:

    python scripts/test_db_doctor.py                 # reads ./.env
    python scripts/test_db_doctor.py --env /path/.env

It never prints passwords or tokens. Exit code 0 = ready, 1 = something to fix.
"""
import argparse
import os
import sys


def _load_env(path):
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("python-dotenv not installed; using the current environment only")
        return
    if path and os.path.isfile(path):
        load_dotenv(path, override=False)
        print(f"env file: {path}")
    else:
        print(f"env file: not found at {path!r}; using the current environment only")


def _test_db_name():
    base = os.environ.get("HRZEST_TEST_DB", "att_test")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    return f"{base}_{worker}" if worker else base


def _check_role(label, host, port, user, password, dbname):
    import psycopg2
    ok = True
    print(f"\n[{label}] role={user!r} host={host!r} port={port} database={dbname!r}")
    try:
        conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname,
                                connect_timeout=5)
    except psycopg2.OperationalError as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        # Never echo the password; psycopg2's message doesn't contain it.
        print(f"  connect: FAILED ({reason})")
        return False
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT current_setting('server_version'), (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)")
    version, is_super = cur.fetchone()
    print(f"  connect: ok   server_version={version}   superuser={bool(is_super)}")
    try:
        cur.execute("SET session_replication_role = replica")
        cur.execute("SET session_replication_role = DEFAULT")
        print("  per-test restore (SET session_replication_role): allowed")
    except psycopg2.errors.InsufficientPrivilege:
        ok = False
        print("  per-test restore (SET session_replication_role): NOT allowed -- see the fix below")
    cur.close()
    conn.close()
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default=os.path.join(os.getcwd(), ".env"), help="path to the .env file")
    args = ap.parse_args()
    _load_env(args.env)

    host = os.environ.get("DB_HOST", "localhost")
    port = int(os.environ.get("DB_PORT", "5432"))
    user = os.environ.get("DB_USER", "postgres")
    owner = os.environ.get("DB_OWNER_USER", "").strip()
    test_db = _test_db_name()
    print(f"\ntest database pytest will use: {test_db!r}  (HRZEST_TEST_DB, + xdist worker suffix)")
    print(f"server: {host}:{port}   runtime role DB_USER={user!r}   harness role DB_OWNER_USER={owner or '(not set: uses DB_USER)'}")
    problems = []
    if "test" not in test_db:
        problems.append("HRZEST_TEST_DB must contain 'test'")
    if host.strip().lower() not in {"localhost", "127.0.0.1", "::1", ""} and host.strip().lower() not in {
            h.strip().lower() for h in os.environ.get("HRZEST_TEST_ALLOWED_DB_HOSTS", "").split(",") if h.strip()}:
        problems.append(f"DB_HOST {host!r} is not local (allow a disposable container via HRZEST_TEST_ALLOWED_DB_HOSTS)")

    import psycopg2
    try:
        conn = psycopg2.connect(host=host, port=port, user=user, password=os.environ.get("DB_PASS", ""),
                                dbname="postgres", connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (test_db,))
        exists = cur.fetchone() is not None
        conn.close()
        print(f"database {test_db!r} exists: {exists}  (the suite creates it if missing and DB_USER may CREATE DATABASE)")
    except psycopg2.OperationalError as exc:
        problems.append(f"cannot reach the server as DB_USER: {str(exc).strip().splitlines()[0] if str(exc).strip() else exc}")
        exists = False

    harness_user = owner or user
    harness_pass = os.environ.get("DB_OWNER_PASS", "") if owner else os.environ.get("DB_PASS", "")
    if exists and not _check_role("harness", host, port, harness_user, harness_pass, test_db):
        problems.append("the harness role may not SET session_replication_role")

    if problems:
        print("\nTO FIX:")
        for p in problems:
            print(f"  - {p}")
        print("  Least-privilege setup (test server only, never superuser for the app runtime role):\n"
              "    python scripts/provision_db_roles.py            # creates hrzest_owner / hrzest_app\n"
              "    GRANT SET ON PARAMETER session_replication_role TO hrzest_owner;   -- as postgres, PG 15+\n"
              "    then run pytest with DB_USER=hrzest_app DB_OWNER_USER=hrzest_owner DB_OWNER_PASS=...")
        return 1
    print("\nREADY: the test suite can create, use and reset its database here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
