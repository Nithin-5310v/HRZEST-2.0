#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-encrypt every encrypted PII value in the database as AES-256-GCM under
the current ENCRYPTION_KEY (utils/pii_crypto.py).

Two jobs in one pass: key rotation (values under an ENCRYPTION_KEY_PREVIOUS
key move to ENCRYPTION_KEY) and format upgrade (legacy Fernet / AES-128-CBC
values become AES-256-GCM). Run it once after deploying the AES-256-GCM
release even without a key change, to upgrade existing rows.

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

How it finds data: every AES-256-GCM value starts with "hz2:" and every
legacy Fernet token with the same 6 base64 chars ("gAAAAA" -- version byte +
timestamp), so any text column in any schema can be scanned without a hand-maintained column list, which would silently go
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="count only; change nothing")
    ap.add_argument("--schema", action="append", help="limit to schema(s)")
    args = ap.parse_args(argv)

    from database import get_db_connection, owner_privileges, raw_text
    from utils.pii_crypto import cipher_from_env, DecryptionError, FERNET_PREFIX, PREFIX

    if not os.environ.get("ENCRYPTION_KEY", "").strip():
        sys.exit("ENCRYPTION_KEY is not set")
    cipher = cipher_from_env()

    totals = {"scanned": 0, "rotated": 0, "already_current": 0, "undecryptable": 0}
    with owner_privileges():
        conn = get_db_connection()
        cur = raw_text(conn.cursor())       # must see stored ciphertext, not decrypted values
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
            cur.execute(f'SELECT ctid, "{column}" FROM {qt} WHERE "{column}" LIKE %s OR "{column}" LIKE %s',  # nosec B608
                        (FERNET_PREFIX + "%", PREFIX + "%"))
            for ctid, value in cur.fetchall():
                totals["scanned"] += 1
                if cipher.is_current(value):
                    totals["already_current"] += 1
                    continue
                try:
                    new = cipher.rotate(value)
                except ValueError:
                    continue            # starts with a prefix but isn't ciphertext (plain text)
                except DecryptionError:
                    totals["undecryptable"] += 1
                    print(f"  undecryptable (key not supplied?): {schema}.{table}.{column} ctid={ctid}")
                    continue
                if not args.dry_run:
                    cur.execute(f'UPDATE {qt} SET "{column}"=%s WHERE ctid=%s AND "{column}"=%s',  # nosec B608
                                (new, ctid, value))
                totals["rotated"] += 1
        if not args.dry_run:
            totals["reindexed"] = _reindex_contacts(conn, args.schema)
        cur.close()
        conn.close()

    verb = "would rotate" if args.dry_run else "rotated"
    print(f"scanned={totals['scanned']} {verb}={totals['rotated']} "
          f"already_current={totals['already_current']} undecryptable={totals['undecryptable']} "
          f"reindexed={totals.get('reindexed', 0)}")
    if totals["undecryptable"]:
        print("Some values were not decryptable with the supplied keys -- do NOT drop the old key yet "
              "(or they are corrupt / belong to a key you no longer have).")
        return 2
    return 0


def _reindex_contacts(conn, schemas=None):
    """Recompute employees.email_bidx / phone_bidx under the current key
    (blind indexes are keyed, so they change with ENCRYPTION_KEY) and
    encrypt any email/phone still in plaintext. Returns rows re-indexed."""
    from utils.pii_crypto import cipher_from_env
    from migrations.column_migrations import encrypt_plaintext_contacts
    cipher = cipher_from_env()
    cur = conn.cursor()                     # decrypting cursor: sees plaintext email/phone
    cur.execute("SELECT table_schema FROM information_schema.columns "
                "WHERE table_name='employees' AND column_name='email_bidx'")
    n = 0
    for (schema,) in cur.fetchall():
        if (schemas and schema not in schemas) or not _IDENT.match(schema):
            continue
        table = f'"{schema}".employees'
        encrypt_plaintext_contacts(cur, table)
        cur.execute(f"SELECT employee_id, email, email_bidx, phone, phone_bidx FROM {table} "  # nosec B608
                    "WHERE email_bidx IS NOT NULL OR phone_bidx IS NOT NULL")
        for emp_id, email, ebidx, phone, pbidx in cur.fetchall():
            new_e = cipher.blind_index("email", email) if email else None
            new_p = cipher.blind_index("phone", phone) if phone else None
            if (new_e, new_p) != (ebidx, pbidx):
                cur.execute(f"UPDATE {table} SET email_bidx=%s, phone_bidx=%s WHERE employee_id=%s",  # nosec B608
                            (new_e, new_p, emp_id))
                n += 1
    conn.commit()
    cur.close()
    return n


if __name__ == "__main__":
    sys.exit(main())
