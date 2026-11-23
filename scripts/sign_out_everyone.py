#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sign out everyone, from the server: for when the SOC console is out of reach.

    python scripts/sign_out_everyone.py --yes

Every browser session and API token (web and mobile app) issued before now, in
every company, is refused on its next request; people sign in again as usual.
SOC console sessions are not affected. The same as Response -> Signed in ->
Sign out everyone in the console (utils/session_cutoff.py). Running this needs
a shell on the server. Recorded in the SOC audit trail as "cli".
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="confirm: sign out every person in every company")
    args = ap.parse_args(argv)
    if not args.yes:
        print("This signs out every person in every company. Run again with --yes to do it.", file=sys.stderr)
        return 2
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))     # the app's own settings, wherever this is run from
    except Exception:       # nosec B110 -- without python-dotenv, the process environment is used as is
        pass

    from utils import soc_actions
    ok, msg = soc_actions.sign_out_everyone("cli", None)
    print(msg, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
