"""Create a Platform Super Admin account, or reset one's password.

platform_admins (att_master schema, blueprints/platform_admin/) has no seed
path in the app: init_master_db() creates the table but never adds a row.
Run this once per deployment to create the first operator:

    python scripts/seed_platform_admin.py --username ops --email ops@hrzest.com

The password is asked for twice at a hidden prompt. For automation, pipe it
in with --password-stdin. Don't put it on the command line: shell history
and the server's process list would keep it.

An existing account is never changed by accident. Running this again for a
username that already exists stops without changing anything, unless
--reset is given. --reset sets a new password (and a new email, if --email
is given), signs out every console session opened with the old password,
and cancels any sign-in code already sent.

Each create and reset is recorded as a security event
(auth.platform_admin_created / auth.platform_admin_password_reset), shown in
the console's audit log.
"""
import argparse
import getpass
import os
import re
import sys

# Run from anywhere: work relative to the repo root (this file lives in scripts/).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import database as db  # noqa: E402
from extensions import log_security_event  # noqa: E402
from utils.auth import generate_password_hash, validate_new_password  # noqa: E402

# Fits platform_admins.username VARCHAR(100). Plain ASCII only: the sign-in
# form strips spaces, and look-alike Unicode letters would allow two names
# that read the same.
_USERNAME_RE = re.compile(r"[A-Za-z0-9._@+-]{3,100}")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_EMAIL_MAX = 200  # platform_admins.email VARCHAR(200)


class SeedError(Exception):
    """A refusal meant for the operator. Nothing has been changed."""


def clean_username(raw):
    username = (raw or "").strip()
    if not _USERNAME_RE.fullmatch(username):
        raise SeedError("Username must be 3-100 characters long and use only letters, digits, "
                        "dots, underscores, @, + or -.")
    return username


def clean_email(raw):
    email = (raw or "").strip().lower()
    if len(email) > _EMAIL_MAX or not _EMAIL_RE.fullmatch(email):
        raise SeedError("Email must be one address, like ops@example.com. The sign-in code is sent there.")
    return email


def check_password(password, username, email):
    # The sign-in form strips spaces from both ends of what is typed, so a
    # password that starts or ends with one could never be entered.
    if password != password.strip():
        raise SeedError("The password can't start or end with a space.")
    ok, err = validate_new_password(password, (username, email))
    if not ok:
        raise SeedError(err)


def read_password(args, stdin=None):
    if args.password is not None:
        print("Warning: --password leaves the password in shell history and the process list. "
              "Use the prompt or --password-stdin next time.", file=sys.stderr)
        return args.password
    if args.password_stdin:
        return (stdin or sys.stdin).readline().rstrip("\r\n")
    first = getpass.getpass("New password: ")
    if getpass.getpass("Repeat it: ") != first:
        raise SeedError("The two passwords didn't match.")
    return first


def existing_email(username):
    """The account's email, or None if there is no such account."""
    conn = db.get_master_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT email FROM platform_admins WHERE username=%s", (username,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return row[0] if row else None


def seed(username, password, email=None, reset=False):
    """Create the account, or with reset=True give an existing one a new
    password (and email, when one is given). Returns "created" or "reset".
    Raises SeedError, changing nothing, if the account exists and reset is
    False, or if it must be created and no email was given. The checks are
    made again here, inside the transaction, so they hold even if someone
    else changed the table after main() looked."""
    conn = db.get_master_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM platform_admins WHERE username=%s FOR UPDATE", (username,))
        exists = cur.fetchone() is not None
        if exists and not reset:
            raise SeedError(f"Platform admin '{username}' already exists. "
                            "Run again with --reset to give it a new password.")
        if not exists and not email:
            raise SeedError("--email is required to create an account: the sign-in code is sent there.")
        hashed = generate_password_hash(password)
        if exists:
            # Moving password_changed_at ends every session opened with the
            # old password (blueprints/platform_admin's per-request check).
            # Deleting the pending code stops a sign-in already past the
            # old password from finishing.
            cur.execute(
                "UPDATE platform_admins SET password=%s, email=COALESCE(%s, email), "
                "password_changed_at=NOW() WHERE username=%s",
                (hashed, email, username),
            )
            cur.execute("DELETE FROM login_otp_challenges WHERE kind=%s AND username=%s",
                        ("platform_admin", username))
        else:
            cur.execute("INSERT INTO platform_admins (username, password, email) VALUES (%s, %s, %s)",
                        (username, hashed, email))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return "reset" if exists else "created"


def _record(outcome, username, email_changed):
    try:
        run_by = getpass.getuser()
    except Exception:
        run_by = "unknown"
    if outcome == "created":
        log_security_event("auth.platform_admin_created",
                           "Platform admin account created with scripts/seed_platform_admin.py",
                           level="WARNING", identifier=username, run_by=run_by)
    else:
        log_security_event("auth.platform_admin_password_reset",
                           "Platform admin password reset with scripts/seed_platform_admin.py; "
                           "older console sessions are signed out",
                           level="WARNING", identifier=username, run_by=run_by,
                           email_changed=email_changed)


def main(argv=None, stdin=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", required=True)
    ap.add_argument("--email", help="Required for a new account. With --reset, changes where sign-in codes go.")
    ap.add_argument("--reset", action="store_true",
                    help="Give an existing account a new password and sign out its sessions.")
    ap.add_argument("--password-stdin", action="store_true",
                    help="Read the password from the first line of standard input instead of prompting.")
    ap.add_argument("--password", help=argparse.SUPPRESS)  # kept for old runbooks; warns
    args = ap.parse_args(argv)

    try:
        username = clean_username(args.username)
        email = clean_email(args.email) if args.email is not None else None
        current_email = existing_email(username)
        # Fail before asking for a password that can't be used.
        if current_email is not None and not args.reset:
            raise SeedError(f"Platform admin '{username}' already exists. "
                            "Run again with --reset to give it a new password.")
        if current_email is None and email is None:
            raise SeedError("--email is required to create an account: the sign-in code is sent there.")
        password = read_password(args, stdin)
        check_password(password, username, email or current_email)
        outcome = seed(username, password, email=email, reset=args.reset)
    except SeedError as exc:
        print(f"{exc} Nothing was changed.", file=sys.stderr)
        return 1

    email_changed = outcome == "reset" and email is not None and email != current_email
    # A one-off command has no request threads to keep fast: write the
    # security event now, so it isn't lost when the process exits.
    from utils.async_writer import set_synchronous_mode
    set_synchronous_mode(True)
    _record(outcome, username, email_changed)

    if outcome == "created":
        print(f"Platform admin '{username}' created. Sign in at /super_admin/login; "
              f"the sign-in code is sent to {email}.")
    else:
        print(f"Password for '{username}' reset. Console sessions opened with the old password are signed out.")
        if email_changed:
            print(f"Sign-in codes now go to {email} instead of {current_email}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
