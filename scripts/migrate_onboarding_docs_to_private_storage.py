#!/usr/bin/env python3
"""One-time cleanup for the onboarding-document-upload security fix.

Before that fix, blueprints/onboarding.py's task-completion upload saved
files straight to static/onboarding_docs/ -- a directly web-servable
directory -- with no extension/MIME/magic-byte validation and no malware
scan, and stored just the bare filename ("EMP001_42_resume.pdf") in
employee_onboarding_tasks.document_path. New uploads now go through the
same _validate_upload()/save_private() path every other document upload in
this app uses (see blueprints/onboarding.py's
_save_onboarding_task_document()), landing in private storage instead.

This script finds every task row from BEFORE that fix (detected by
document_path having no path separator -- a stored_ref from save_private()
always has one, either a private_uploads/... local path or an s3://... URL)
across every tenant schema, and:
  1. Reads the file from its old static/onboarding_docs/<name> location.
  2. Runs it through the exact same ClamAV scan _validate_upload() does --
     an existing file that was NEVER scanned when it was accepted the first
     time around gets one retroactively here. A positive hit is logged and
     left in place rather than migrated (see --quarantine below), since
     silently relocating a detected-malicious file is worse than leaving
     it exactly where an operator can find and remove it by hand.
  3. Re-saves it via save_private() (respects whatever storage backend --
     local disk or S3 -- is configured today, not whatever was configured
     when the file was first uploaded) and updates document_path to the
     new stored_ref.
  4. Deletes the original static/onboarding_docs/<name> file.

Every step is independently try/except-guarded and logged -- one bad row
never aborts the run. Safe to re-run: an already-migrated row's
document_path already contains a path separator and is skipped.

Usage:
    python scripts/migrate_onboarding_docs_to_private_storage.py [--dry-run] [--quarantine]

    --dry-run     Report what would move, without touching any file or row.
    --quarantine  Rename a malware-scan-positive file in place (append
                  ".quarantined") instead of just logging and leaving it
                  under its original, still-web-reachable name.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.datastructures import FileStorage  # noqa: E402

from database import get_master_db, get_tenant_db  # noqa: E402
from extensions import app, app_log  # noqa: E402
from utils.helpers import _scan_for_malware  # noqa: E402
from utils.storage import save_private  # noqa: E402

OLD_UPLOAD_DIR = os.path.join(app.root_path, "static", "onboarding_docs")


def _is_legacy_path(document_path: str) -> bool:
    return bool(document_path) and "/" not in document_path and "\\" not in document_path


def _migrate_schema(schema, dry_run, quarantine):
    db = get_tenant_db(schema)
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT id, employee_id, document_path FROM employee_onboarding_tasks WHERE document_path IS NOT NULL")
    rows = cursor.fetchall()

    migrated, skipped, scan_hits, missing = 0, 0, 0, 0
    for task_id, emp_id, document_path in rows:
        if not _is_legacy_path(document_path):
            skipped += 1
            continue

        old_path = os.path.join(OLD_UPLOAD_DIR, document_path)
        if not os.path.isfile(old_path):
            missing += 1
            app_log.warning("[%s] task %s: document_path=%r not found at %s", schema, task_id, document_path, old_path)
            continue

        if dry_run:
            app_log.info("[%s] DRY RUN: would migrate task %s (%s)", schema, task_id, document_path)
            migrated += 1
            continue

        with open(old_path, "rb") as fh:
            fs = FileStorage(stream=fh, filename=document_path)
            clean, scan_err = _scan_for_malware(fs)
            if not clean:
                scan_hits += 1
                app_log.critical(
                    "[%s] task %s: MALWARE SCAN POSITIVE on legacy file %s (%s) -- left in place, not migrated.",
                    schema, task_id, old_path, scan_err,
                )
                if quarantine:
                    os.rename(old_path, old_path + ".quarantined")
                continue
            fh.seek(0)
            rel_path = f"onboarding_docs/{emp_id}/{task_id}/{document_path}"
            stored_ref, err = save_private(app.root_path, fs, rel_path)
        if err:
            app_log.warning("[%s] task %s: save_private failed: %s", schema, task_id, err)
            continue

        cursor.execute("UPDATE employee_onboarding_tasks SET document_path=%s WHERE id=%s", (stored_ref, task_id))
        db.commit()
        try:
            os.remove(old_path)
        except OSError as exc:
            app_log.warning("[%s] task %s: migrated but could not remove old file %s: %s", schema, task_id, old_path, exc)
        migrated += 1

    cursor.close()
    db.close()
    return migrated, skipped, scan_hits, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report only, don't move anything.")
    parser.add_argument("--quarantine", action="store_true", help="Rename malware-positive files in place.")
    args = parser.parse_args()

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT db_name FROM tenants")
    schemas = [r[0] for r in mcur.fetchall()]
    mcur.close()
    mconn.close()

    totals = {"migrated": 0, "skipped": 0, "scan_hits": 0, "missing": 0}
    for schema in schemas:
        try:
            migrated, skipped, scan_hits, missing = _migrate_schema(schema, args.dry_run, args.quarantine)
        except Exception as exc:
            app_log.warning("Schema '%s' failed entirely: %s", schema, exc, exc_info=True)
            continue
        totals["migrated"] += migrated
        totals["skipped"] += skipped
        totals["scan_hits"] += scan_hits
        totals["missing"] += missing
        print(f"[{schema}] migrated={migrated} already_ok={skipped} malware_hits={scan_hits} file_missing={missing}")

    print(f"\nTotal: migrated={totals['migrated']} already_ok={totals['skipped']} "
          f"malware_hits={totals['scan_hits']} file_missing={totals['missing']}")
    if totals["scan_hits"]:
        print(f"\n⚠ {totals['scan_hits']} file(s) failed a retroactive malware scan and were left in place "
              f"under {OLD_UPLOAD_DIR} -- review and remove them by hand.")


if __name__ == "__main__":
    main()
