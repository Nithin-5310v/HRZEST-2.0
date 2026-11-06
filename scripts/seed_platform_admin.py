"""One-time bootstrap for the very first Platform Super Admin account.

platform_admins (att_master schema, blueprints/platform_admin.py) has no
seed path anywhere in the app -- init_master_db() creates the table but
never inserts a row into it, unlike the tenant-side admin_users bootstrap
in app.py's init_db(). Without this script the only way in is a hand-written
SQL insert with a manually bcrypt-hashed password. Run this once per
deployment; safe to re-run (it upserts by username).

Usage:
    python scripts/seed_platform_admin.py --username ops --email ops@hrzest.com --password "..."

Omit --password to be prompted for it interactively (not echoed, not put in
shell history).
"""
import argparse
import getpass
import os
import sys

# Run from anywhere: work relative to the repo root (this file lives in scripts/).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import database as db  # noqa: E402
from utils.auth import generate_password_hash, validate_new_password  # noqa: E402


def seed(username: str, email: str, password: str) -> None:
    conn = db.get_master_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO platform_admins (username, password, email)
        VALUES (%s, %s, %s)
        ON CONFLICT (username) DO UPDATE
            SET password = EXCLUDED.password, email = EXCLUDED.email
        """,
        (username, generate_password_hash(password), email),
    )
    conn.commit()
    cur.close()
    conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", help="Omit to be prompted interactively")
    args = ap.parse_args()

    password = args.password or getpass.getpass("Platform admin password: ")
    _pw_ok, _pw_err = validate_new_password(password)
    if not _pw_ok:
        raise SystemExit(_pw_err)

    seed(args.username, args.email, password)
    print(f"Platform admin '{args.username}' is ready. Log in at /super_admin/login")


if __name__ == "__main__":
    main()
