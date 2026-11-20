#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reset a SOC console password from the server: for a sole admin who forgot it.

    python scripts/soc_reset_password.py you@example.com

The account's password is forgotten, its lockout is cleared, and every open
console session of it ends. At the next sign-in, after the emailed code, the
person sets a new password. With another admin around, use the console instead
(Users & audit -> Reset password). Running this needs a shell on the server, so
it is not a way around the sign-in. Recorded in the SOC audit trail as "cli".
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email", help="the address the person signs in with")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))     # the app's own settings, wherever this is run from
    except Exception:       # nosec B110 -- without python-dotenv, the process environment is used as is
        pass

    from utils import soc_auth
    user = soc_auth.get_user_by_email(args.email)
    if user is None:
        print("No SOC account has that address. An account is created at its first sign-in, "
              "and a new one sets its password then.", file=sys.stderr)
        return 1
    soc_auth.reset_password(user["id"])
    soc_auth.unlock(user["id"])
    soc_auth.audit("cli", "user_password_reset", target=user["username"],
                   detail="from the server; lockout cleared; all sessions ended")
    print(f"Password reset for {user['username']}. Sign in at /socperson/login: "
          "after the emailed code, set a new password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
