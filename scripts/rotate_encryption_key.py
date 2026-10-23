#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-encrypt every Fernet-encrypted value in the database under the current
ENCRYPTION_KEY.

Procedure (no downtime -- decryption accepts old keys while this runs):

  1. Generate a new key:
         python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  2. In the environment: ENCRYPTION_KEY=<new key>
                         ENCRYPTION_KEY_PREVIOUS=<old key>[,<older key>...]
     and deploy/restart the app.
  3. Dry run, then for real:
         python scripts/rotate_encryption_key.py --dry-run
         python scripts/rotate_encryption_key.py
  4. When it reports 0 values still on an old key, remove
     ENCRYPTION_KEY_PREVIOUS and redeploy. Keep the old key in the secret
     manager until your backups taken under it have aged out.

How it finds data: every Fernet token starts with the same 6 base64 chars
("gAAAAA" -- version byte + timestamp), so any text column in any schema can
be scanned without a hand-maintained column list, which would silently go
stale as columns are added. Audit tables are skipped (append-only; they hold
no encrypted values). Runs on the owner role when DB_OWNER_USER is set.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")
_SKIP_TABLES = {"audit_logs", "security_events", "audit_chain_head"}
_TOKEN_PREFIX = "gAAAAA"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="count only; change nothing")
    ap.add_argument("--schema", action="append", help="limit to schema(s)")
    args = ap.parse_args(argv)

    from cryptography.fernet import Fernet, InvalidToken
    from database import get_db_connection, owner_privileges

    primary = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not primary:
        sys.exit("ENCRYPTION_KEY is not set")
    previous = [k.strip() for k in os.environ.get("ENCRYPTION_KEY_PREVIOUS", "").split(",") if k.strip()]
    from cryptography.fernet import MultiFernet
    multi = MultiFernet([Fernet(k.encode()) for k in [primary] + previous])
    primary_only = Fernet(primary.encode())

    totals = {"scanned": 0, "rotated": 0, "already_current": 0, "undecryptable": 0}
    with owner_privileges():
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT table_schema, table_name, column_name FROM information_schema.columns c "
            "WHERE data_type IN ('text','character varying') "
            "AND table_schema NOT IN ('pg_catalog','information_schema') "
            "AND table_name IN (SELECT table_name FROM information_schema.tables "
            "                   WHERE table_type='BASE TABLE' AND table_schema=c.table_schema) "
            "ORDER BY 1,2,3")
        targets = cur.fetchall()
        for schema, table, column in targets:
            if table in _SKIP_TABLES or (args.schema and schema not in args.schema):
                continue
            if not all(_IDENT.match(x) for x in (schema, table, column)):
                continue
            qt = f'"{schema}"."{table}"'
            cur.execute(f'SELECT ctid, "{column}" FROM {qt} WHERE "{column}" LIKE %s', (_TOKEN_PREFIX + "%",))  # nosec B608
            for ctid, value in cur.fetchall():
                totals["scanned"] += 1
                try:
                    primary_only.decrypt(value.encode())
                    totals["already_current"] += 1
                    continue
                except InvalidToken:
                    pass
                try:
                    new = multi.rotate(value.encode()).decode()
                except InvalidToken:
                    totals["undecryptable"] += 1
                    print(f"  undecryptable (key not supplied?): {schema}.{table}.{column} ctid={ctid}")
                    continue
                if not args.dry_run:
                    cur.execute(f'UPDATE {qt} SET "{column}"=%s WHERE ctid=%s AND "{column}"=%s',  # nosec B608
                                (new, ctid, value))
                totals["rotated"] += 1
        cur.close()
        conn.close()

    verb = "would rotate" if args.dry_run else "rotated"
    print(f"scanned={totals['scanned']} {verb}={totals['rotated']} "
          f"already_current={totals['already_current']} undecryptable={totals['undecryptable']}")
    if totals["undecryptable"]:
        print("Some values were not decryptable with the supplied keys -- do NOT drop the old key yet "
              "(or they are corrupt / belong to a key you no longer have).")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
