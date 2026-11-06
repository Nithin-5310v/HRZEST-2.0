# -*- coding: utf-8 -*-
"""One-off: move employee face photos from the old shared flat folder
(dataset/<employee_id>.jpg) into per-company folders
(dataset/tenants/<tenant>/<employee_id>.jpg -- see utils/tenant_files.py).

Every company used to write its reference photos into the same folder, keyed
only by employee ID. Employee IDs repeat across companies (EMP001, ...), so
when two companies both have an EMP001 the one shared file belongs to
whichever uploaded last, and nothing records which. Those IDs are AMBIGUOUS:
the file may be another company's employee -- or a face a hostile company
deliberately uploaded to impersonate one. They are never copied; they are
listed so each company can re-enroll those employees, and --clear-ambiguous
unsets their face_image so the stale photo can no longer be matched against.

An employee ID that exists in only one company is UNAMBIGUOUS: its flat file
is copied into that company's folder and its face_image is repointed.

Usage:
    python scripts/migrate_face_photos_per_tenant.py --dry-run
    python scripts/migrate_face_photos_per_tenant.py [--clear-ambiguous] [--delete-legacy]

    --dry-run          Report only; change no file or row.
    --clear-ambiguous  Set face_image=NULL for ambiguous employees (they must
                       re-enroll a photo before face check-in works again).
    --delete-legacy    After copying, delete flat files that were unambiguous.
                       Ambiguous flat files are always kept for review.
"""
import argparse
import collections
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def plan(rows_by_tenant, legacy_path_for):
    """Pure planner, unit-tested.

    rows_by_tenant: {tenant: [(employee_id, face_image), ...]} -- EVERY
    employee in every company (needed to see cross-company ID reuse).
    legacy_path_for(emp_id) -> the old flat path for that ID.

    Returns {"copy": [(tenant, emp_id, src)], "ambiguous": [(tenant, emp_id, src)]}
    for employees whose face_image points at the old flat file. Rows already
    pointing elsewhere (the new per-company folder) are left alone."""
    owners = collections.Counter()
    for rows in rows_by_tenant.values():
        for emp_id, _ in rows:
            owners[(emp_id or "").upper()] += 1
    out = {"copy": [], "ambiguous": []}
    for tenant, rows in sorted(rows_by_tenant.items()):
        for emp_id, face_image in rows:
            if not emp_id or not face_image:
                continue
            src = legacy_path_for(emp_id)
            if os.path.normcase(os.path.abspath(face_image)) != os.path.normcase(os.path.abspath(src)):
                continue
            bucket = "ambiguous" if owners[emp_id.upper()] > 1 else "copy"
            out[bucket].append((tenant, emp_id, src))
    return out


def _tenants(get_master_db):
    m = get_master_db()
    cur = m.cursor()
    cur.execute("SELECT db_name FROM tenants")
    names = [r[0] for r in cur.fetchall()]
    cur.close()
    m.close()
    return ["public"] + [n for n in names if n and n != "public"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clear-ambiguous", action="store_true")
    ap.add_argument("--delete-legacy", action="store_true")
    args = ap.parse_args()

    from database import get_master_db, get_tenant_db
    from extensions import app, app_log
    from utils import tenant_files

    rows_by_tenant = {}
    with app.app_context():
        for tenant in _tenants(get_master_db):
            try:
                db = get_tenant_db(tenant)
                cur = db.cursor()
                cur.execute("SELECT to_regclass('employees')")
                if cur.fetchone()[0] is None:
                    cur.close()
                    db.close()
                    continue
                cur.execute("SELECT employee_id, face_image FROM employees")
                rows_by_tenant[tenant] = cur.fetchall()
                cur.close()
                db.close()
            except Exception as exc:
                app_log.warning("[%s] could not read employees: %s", tenant, exc)

        actions = plan(rows_by_tenant, tenant_files.legacy_face_photo_path)
        for tenant, emp_id, src in actions["ambiguous"]:
            print(f"AMBIGUOUS  {tenant:30} {emp_id:15} {src}  -> re-enroll this employee's photo")
        for tenant, emp_id, src in actions["copy"]:
            print(f"COPY       {tenant:30} {emp_id:15} {src}")
        print(f"\n{len(actions['copy'])} to copy, {len(actions['ambiguous'])} ambiguous.")
        if args.dry_run:
            print("Dry run: nothing changed.")
            return 0

        copied = []
        for tenant, emp_id, src in actions["copy"]:
            if not os.path.isfile(src):
                app_log.warning("[%s] %s: flat file %s missing, skipped", tenant, emp_id, src)
                continue
            dst = tenant_files.face_photo_path(emp_id, tenant)
            shutil.copy2(src, dst)
            db = get_tenant_db(tenant)
            cur = db.cursor()
            cur.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s AND face_image=%s",
                        (dst, emp_id, src))
            db.commit()
            cur.close()
            db.close()
            copied.append(src)

        if args.clear_ambiguous:
            for tenant, emp_id, src in actions["ambiguous"]:
                db = get_tenant_db(tenant)
                cur = db.cursor()
                cur.execute("UPDATE employees SET face_image=NULL WHERE employee_id=%s AND face_image=%s",
                            (emp_id, src))
                db.commit()
                cur.close()
                db.close()

        if args.delete_legacy:
            for src in copied:
                try:
                    os.remove(src)
                except OSError as exc:
                    app_log.warning("Could not delete flat file %s: %s", src, exc)

        print(f"Copied {len(copied)}."
              + (f" Cleared {len(actions['ambiguous'])} ambiguous." if args.clear_ambiguous else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
