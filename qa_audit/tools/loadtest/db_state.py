"""Seed and clean up the QA load-test accounts in the LOCAL att_test database.

    python qa_audit/tools/loadtest/db_state.py seed --employees 200
    python qa_audit/tools/loadtest/db_state.py cleanup

`seed` first records a high-water mark (max id of every table in the
public and att_master schemas) in the system temp dir, then creates one
admin (`lt_admin`) and N employees (`LT_0001`...). `cleanup` deletes every
row added since that mark, plus rows keyed by the LT_ / lt_admin names in
tables without an integer id, so att_test is left exactly as it was before
the run (it is shared with the pytest suite, whose per-session snapshot
must not pick up load-test rows). Refuses to run against anything but
att_test.
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import _env  # noqa: E402,F401  -- process-local env; must precede app/db imports

import bcrypt  # noqa: E402
import psycopg2  # noqa: E402

MARK_FILE = os.path.join(tempfile.gettempdir(), "hrzest_loadtest_watermark.json")
SCHEMAS = ("public", "att_master")


def connect():
    if os.environ["DB_NAME"] != "att_test":
        sys.exit("Refusing to run: DB_NAME is not att_test")
    conn = psycopg2.connect(host=os.environ.get("DB_HOST", "localhost"), port=int(os.environ.get("DB_PORT", "5432")),
                            user=os.environ.get("DB_USER", "postgres"), password=os.environ.get("DB_PASS", ""),
                            dbname="att_test")
    conn.autocommit = True
    return conn


def id_tables(cur):
    cur.execute(
        "SELECT c.table_schema, c.table_name FROM information_schema.columns c "
        "JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name "
        "WHERE c.table_schema = ANY(%s) AND c.column_name='id' AND t.table_type='BASE TABLE' "
        "AND c.data_type IN ('integer','bigint')",
        (list(SCHEMAS),),
    )
    return cur.fetchall()


def seed(n):
    conn = connect()
    cur = conn.cursor()
    mark = {}
    for schema, table in id_tables(cur):
        cur.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{schema}"."{table}"')  # nosec B608 -- names from information_schema
        mark[f"{schema}.{table}"] = cur.fetchone()[0]
    with open(MARK_FILE, "w", encoding="utf-8") as fh:
        json.dump(mark, fh)
    pw_hash = bcrypt.hashpw(_env.LT_PASSWORD.encode(), bcrypt.gensalt(rounds=12)).decode()
    cur.execute("INSERT INTO admin_users (username, password, email, role) VALUES (%s,%s,%s,'admin') "
                "ON CONFLICT (username) DO NOTHING", (_env.LT_ADMIN, pw_hash, "lt_admin@example.invalid"))
    for i in range(1, n + 1):
        emp = f"{_env.LT_PREFIX}{i:04d}"
        cur.execute("INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
                    "VALUES (%s,%s,%s,%s,0) ON CONFLICT (employee_id) DO NOTHING",
                    (emp, f"Load Test {i:04d}", f"{emp.lower()}@example.invalid", pw_hash))
        cur.execute("INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s, 1000) "
                    "ON CONFLICT (employee_id) DO NOTHING", (emp,))
    print(f"seeded 1 admin + {n} employees; watermark saved ({len(mark)} tables)")
    conn.close()


def cleanup():
    conn = connect()
    cur = conn.cursor()
    try:
        with open(MARK_FILE, encoding="utf-8") as fh:
            mark = json.load(fh)
    except FileNotFoundError:
        sys.exit("No watermark file -- run `seed` first (nothing to clean).")
    # Name-keyed rows in tables without an integer id.
    for sql in ("DELETE FROM api_tokens WHERE identity LIKE 'LT\\_%%' OR identity=%s",
                "DELETE FROM mobile_bridge_tokens WHERE admin_username=%s",
                "DELETE FROM login_otp_challenges WHERE username LIKE 'LT\\_%%' OR username=%s"):
        try:
            cur.execute(sql, (_env.LT_ADMIN,))
        except psycopg2.Error:
            pass  # table absent in this schema version
    # Rows added since the watermark; parents (employees/admin_users) last, retried for FK order.
    pending = sorted(mark.items(), key=lambda kv: kv[0].endswith((".employees", ".admin_users")))
    removed = 0
    for _ in range(3):
        failed = []
        for qualified, max_id in pending:
            schema, table = qualified.split(".", 1)
            try:
                cur.execute(f'DELETE FROM "{schema}"."{table}" WHERE id > %s', (max_id,))  # nosec B608
                removed += cur.rowcount
            except psycopg2.Error:
                failed.append((qualified, max_id))
        if not failed:
            break
        pending = failed
    leftovers = []
    for table, col in (("employees", "employee_id"), ("admin_users", "username"), ("attendance", "employee_id")):
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE 'LT\\_%%' OR {col}=%s", (_env.LT_ADMIN,))  # nosec B608
        if cur.fetchone()[0]:
            leftovers.append(table)
    os.remove(MARK_FILE)
    print(f"removed {removed} row(s) added during the load test"
          + (f"; LEFTOVERS in {leftovers}" if leftovers else "; no load-test rows remain"))
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["seed", "cleanup"])
    ap.add_argument("--employees", type=int, default=200)
    a = ap.parse_args()
    seed(a.employees) if a.action == "seed" else cleanup()
