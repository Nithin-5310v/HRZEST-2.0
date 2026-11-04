#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Encrypt every file under private_uploads/ with AES-256-GCM (in place).

utils/storage.py encrypts new uploads automatically; this upgrades files
written before that (plaintext) and re-encrypts files still under an
ENCRYPTION_KEY_PREVIOUS key after a rotation. Safe to re-run: files already
on the current key are skipped. Each rewrite is atomic (temp file + rename).

    python scripts/encrypt_private_uploads.py --dry-run
    python scripts/encrypt_private_uploads.py

Run it after deploying the release that added upload encryption, and again
after every key rotation (alongside scripts/rotate_encryption_key.py).
Exit code 2 if any file could not be decrypted with the supplied keys.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="count only; change nothing")
    ap.add_argument("--root", default=os.path.join(ROOT, "private_uploads"), help="directory to process")
    args = ap.parse_args(argv)

    # Bare-metal hosts keep ENCRYPTION_KEY in .env (containers get it via
    # env_file). override=False: real environment variables still win.
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    if not os.environ.get("ENCRYPTION_KEY", "").strip():
        sys.exit("ENCRYPTION_KEY is not set")
    from utils.pii_crypto import cipher_from_env, DecryptionError
    from utils.storage import write_encrypted
    cipher = cipher_from_env()

    totals = {"scanned": 0, "encrypted": 0, "already_current": 0, "undecryptable": 0}
    for dirpath, _dirs, files in os.walk(args.root):
        for name in files:
            if name.startswith(".tmp-"):
                continue
            path = os.path.join(dirpath, name)
            totals["scanned"] += 1
            with open(path, "rb") as f:
                blob = f.read()
            if cipher.file_is_current(blob):
                totals["already_current"] += 1
                continue
            try:
                plain = cipher.decrypt_bytes(blob)
            except DecryptionError:
                totals["undecryptable"] += 1
                print(f"  undecryptable (key not supplied?): {path}")
                continue
            if not args.dry_run:
                write_encrypted(path, plain)
            totals["encrypted"] += 1

    verb = "would encrypt" if args.dry_run else "encrypted"
    print(f"scanned={totals['scanned']} {verb}={totals['encrypted']} "
          f"already_current={totals['already_current']} undecryptable={totals['undecryptable']}")
    return 2 if totals["undecryptable"] else 0


if __name__ == "__main__":
    sys.exit(main())
