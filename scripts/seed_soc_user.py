"""Create a SOC console account (run once per analyst; the first one must be an admin).

  python scripts/seed_soc_user.py --username alice --role admin --email alice@example.com

The password is prompted for (not echoed, not in shell history) unless --password
is given. The script prints a ONE-TIME SETUP CODE: the new user needs the password
AND this code on first sign-in to enrol their authenticator app, so someone who
learns only the password cannot claim the second factor first. Hand the two over
through different channels. The code is stored hashed and cannot be shown again
(an admin can issue a new one from the console: Admin -> Reset MFA).

There are no default credentials. The console lives at /soc.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from migrations.soc_tables import SOC_ROLES   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", required=True)
    ap.add_argument("--role", choices=SOC_ROLES, default="viewer")
    ap.add_argument("--email", default=None)
    ap.add_argument("--password", help="Omit to be prompted interactively")
    args = ap.parse_args()

    password = args.password or getpass.getpass("SOC password (min 12 characters): ")
    if not args.password and password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")

    from database import get_master_db
    from migrations.soc_tables import ensure_soc_tables
    db = get_master_db()
    cur = db.cursor()
    ensure_soc_tables(cur)      # idempotent; lets this script run before the first app boot
    db.commit()
    cur.close()
    db.close()

    from utils import soc_auth
    ok, msg, setup_code = soc_auth.create_user(args.username, password, args.role, args.email, created_by="cli")
    if not ok:
        raise SystemExit(msg)
    soc_auth.audit("cli", "user_created", target=args.username.lower(), detail=f"role={args.role}")
    print(f"SOC {args.role} '{args.username.lower()}' created. Sign in at /soc/login")
    print(f"ONE-TIME SETUP CODE (shown once): {setup_code}")


if __name__ == "__main__":
    main()
