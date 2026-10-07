"""READ-ONLY impact check for QA findings BUG-01 and BUG-30.

BUG-01  Shift times and payroll rules were process-wide globals, so one
        company's settings could be used for another company's check-ins.
BUG-30  Four of the five check-in endpoints ignored the employee's assigned
        shift and used the company default instead.

Both bugs can leave WRONG VALUES STORED in `attendance` (status, logout_status,
attendance_type), and payroll is computed from those stored values. This
script recomputes what each stored value should have been, using:
  * the company's OWN company_settings (shift_start/half/end, grace_minutes)
  * the employee's assigned shift (employees.shift_id -> shifts), if any
and reports every row whose stored value differs.

It never writes: every connection is opened with
default_transaction_read_only=on, so even a bug in this script cannot change
data. Run it where the app's DB_* environment variables are set (for example
inside the app container). Nothing secret is printed.

    python qa_audit/tools/bug01_impact_check.py --since 2026-07-01
    python qa_audit/tools/bug01_impact_check.py --since 2026-07-01 --schema acme --samples 10

Caveats -- a mismatch is a lead to review, not proof of a wrong payslip:
  * Rows are compared against the company's CURRENT settings; if a company
    changed its shift times, older rows may differ legitimately.
  * Rows an admin corrected by hand (bulk mark / correct attendance) can
    differ legitimately.
"""
import argparse
import datetime
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import psycopg2  # noqa: E402

from database import _DB_CONFIG  # noqa: E402  -- the app's own env-driven connection settings
from utils.attendance_utils import classify_by_worked_minutes  # noqa: E402  -- the app's own rule

LOGIN_STATUSES = ("Full Day Login", "Late Login", "Half Day Login")
LOGOUT_STATUSES = ("Half Day Logout", "Early Logout", "Completed")
ATT_TYPES = ("Full Day", "Late - Full Day", "Half Day")
DEFAULTS = {"shift_start": datetime.time(9, 0), "shift_half": datetime.time(13, 0),
            "shift_end": datetime.time(18, 0), "grace_minutes": 15}


def connect():
    cfg = dict(_DB_CONFIG)
    cfg["options"] = (cfg.get("options", "") + " -c default_transaction_read_only=on").strip()
    conn = psycopg2.connect(**cfg)
    conn.autocommit = True
    return conn


def as_time(v):
    if v is None or isinstance(v, datetime.time):
        return v
    if isinstance(v, datetime.timedelta):
        total = int(v.total_seconds())
        return datetime.time((total // 3600) % 24, (total % 3600) // 60, total % 60)
    return datetime.datetime.strptime(str(v)[:8], "%H:%M:%S").time()


def schemas(cur, only):
    cur.execute("SELECT subdomain, db_name FROM att_master.tenants ORDER BY id")
    rows = cur.fetchall()
    default = _DB_CONFIG["dbname"]
    found = [(r[0], r[1]) for r in rows]
    if default not in {r[1] for r in found}:
        found.insert(0, ("(default schema)", default))
    if only:
        found = [r for r in found if only in (r[0], r[1])]
    return found


def table_in(cur, schema, table):
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (schema, table))
    return cur.fetchone() is not None


def resolve(cur, schema, table, fallbacks):
    """Qualified name the APP would actually hit: the app sets
    `search_path TO "<schema>", public`, so a table missing from the tenant
    schema silently resolves to public.<table>. Records such fallbacks."""
    if table_in(cur, schema, table):
        return f'"{schema}".{table}'
    if table_in(cur, "public", table):
        fallbacks.append(table)
        return f"public.{table}"
    return None


def tables_missing_vs_public(cur, schema):
    """Every public table this tenant schema lacks -- for each one, the app
    would read/write the shared public table instead (tenant-isolation risk)."""
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' "
        "EXCEPT SELECT table_name FROM information_schema.tables WHERE table_schema=%s ORDER BY 1",
        (schema,),
    )
    return [r[0] for r in cur.fetchall()]


def settings_for(cur, schema, fallbacks):
    tbl = resolve(cur, schema, "company_settings", fallbacks)
    if tbl is None:
        return None
    cur.execute(
        f'SELECT shift_start, shift_half, shift_end, grace_minutes, late_deduction_pct, '  # nosec B608 -- names from information_schema, quoted
        f'half_day_deduction_pct, holiday_pay, leave_pay FROM {tbl} LIMIT 1'
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "shift_start": as_time(row[0]) or DEFAULTS["shift_start"],
        "shift_half": as_time(row[1]) or DEFAULTS["shift_half"],
        "shift_end": as_time(row[2]) or DEFAULTS["shift_end"],
        "grace_minutes": int(row[3] if row[3] is not None else DEFAULTS["grace_minutes"]),
        "late_deduction_pct": float(row[4]) if row[4] is not None else 10.0,
        "half_day_deduction_pct": float(row[5]) if row[5] is not None else 50.0,
        "holiday_pay": row[6] or "paid",
        "leave_pay": row[7] or "exclude",
    }


def expected_login_status(login_t, start, half, grace):
    grace_t = (datetime.datetime.combine(datetime.date(2000, 1, 1), start)
               + datetime.timedelta(minutes=grace)).time()
    if login_t <= grace_t:
        return "Full Day Login"
    if login_t <= half:
        return "Late Login"
    return "Half Day Login"


def expected_logout_status(logout_t, half, end):
    if logout_t < half:
        return "Half Day Logout"
    if logout_t < end:
        return "Early Logout"
    return "Completed"


def check_schema(cur, schema, since, samples):
    fallbacks = []
    st = settings_for(cur, schema, fallbacks)
    if st is None:
        return None, None, fallbacks
    employees = resolve(cur, schema, "employees", fallbacks)
    shifts = resolve(cur, schema, "shifts", fallbacks)
    attendance = resolve(cur, schema, "attendance", fallbacks)
    cur.execute(
        f'SELECT e.employee_id, s.start_time, s.half_time, s.end_time '  # nosec B608 -- names from information_schema, quoted
        f'FROM {employees} e LEFT JOIN {shifts} s ON s.id = e.shift_id'
    )
    emp_shift = {r[0]: (as_time(r[1]), as_time(r[2]), as_time(r[3])) if r[1] else None for r in cur.fetchall()}
    cur.execute(
        f'SELECT employee_id, date, login_time, logout_time, status, logout_status, attendance_type, '  # nosec B608
        f'worked_minutes FROM {attendance} WHERE date >= %s AND login_time IS NOT NULL ORDER BY date',
        (since,),
    )
    counts = Counter()
    per_month = defaultdict(Counter)
    examples = []
    for emp_id, day, login_t, logout_t, status, logout_status, att_type, worked in cur.fetchall():
        counts["rows"] += 1
        shift = emp_shift.get(emp_id) or (st["shift_start"], st["shift_half"], st["shift_end"])
        start, half, end = shift
        login_t, logout_t = as_time(login_t), as_time(logout_t)
        exp_login = expected_login_status(login_t, start, half, st["grace_minutes"])
        diffs = []
        if status in LOGIN_STATUSES and status != exp_login:
            diffs.append(f"status {status!r} -> {exp_login!r}")
        if logout_t and logout_status in LOGOUT_STATUSES:
            exp_out = expected_logout_status(logout_t, half, end)
            if logout_status != exp_out:
                diffs.append(f"logout_status {logout_status!r} -> {exp_out!r}")
        if att_type in ATT_TYPES and worked is not None:
            exp_type = classify_by_worked_minutes(exp_login, int(worked), start, end)
            if att_type != exp_type:
                diffs.append(f"attendance_type {att_type!r} -> {exp_type!r}")
        if diffs:
            counts["mismatched_rows"] += 1
            per_month[day.strftime("%Y-%m")]["mismatched"] += 1
            if len(examples) < samples:
                examples.append(f"    {day} {emp_id}: " + "; ".join(diffs))
        per_month[day.strftime("%Y-%m")]["rows"] += 1
    return st, (counts, per_month, examples), fallbacks


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", required=True, type=datetime.date.fromisoformat,
                    help="first attendance date to check (YYYY-MM-DD)")
    ap.add_argument("--schema", help="only this tenant (subdomain or schema name)")
    ap.add_argument("--samples", type=int, default=5, help="example rows to print per tenant")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    tenants = schemas(cur, args.schema)
    results = []
    isolation = []
    for subdomain, schema in tenants:
        cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (schema,))
        schema_exists = cur.fetchone() is not None
        if schema_exists and schema != "public":
            missing = tables_missing_vs_public(cur, schema)
            if missing:
                isolation.append((subdomain, schema, missing))
        try:
            st, res, _fallbacks = check_schema(cur, schema, args.since, args.samples)
        except psycopg2.Error as exc:
            print(f"[{subdomain}] skipped: {type(exc).__name__}: {exc.pgerror or exc}".strip())
            continue
        if st is None:
            print(f"[{subdomain}] skipped: no company_settings table/row")
            continue
        results.append((subdomain, schema, st, res))

    print("\n== Tenant isolation: tables a tenant schema LACKS (the app falls back to public.<table>) ==")
    if not isolation:
        print("  None -- every tenant schema has its own copy of every public table.")
    for subdomain, schema, missing in isolation:
        print(f"  [{subdomain}] schema {schema!r} lacks {len(missing)}: {', '.join(missing)}")
    if isolation:
        print("  -> Requests for these tenants read/write the SHARED public tables listed above.")

    print("\n== Company settings (BUG-01: these were shared across companies) ==")
    distinct = {(r[2]["shift_start"], r[2]["shift_half"], r[2]["shift_end"], r[2]["grace_minutes"],
                 r[2]["late_deduction_pct"], r[2]["half_day_deduction_pct"], r[2]["holiday_pay"], r[2]["leave_pay"])
                for r in results}
    for subdomain, _schema, st, _res in results:
        print(f"  {subdomain:24s} shift {st['shift_start']:%H:%M}/{st['shift_half']:%H:%M}/{st['shift_end']:%H:%M} "
              f"grace {st['grace_minutes']:>3}m  late {st['late_deduction_pct']:.0f}%  half {st['half_day_deduction_pct']:.0f}%  "
              f"holiday={st['holiday_pay']} leave={st['leave_pay']}")
    if len(distinct) <= 1:
        print("  -> All companies use identical settings: BUG-01 could not have mixed values between them.")
    else:
        print(f"  -> {len(distinct)} different setting combinations: BUG-01 may have applied one company's "
              "rules to another. Check the mismatches below.")

    print(f"\n== Stored attendance values vs the company's own rules, since {args.since} ==")
    total_rows = total_bad = 0
    for subdomain, _schema, _st, (counts, per_month, examples) in results:
        total_rows += counts["rows"]
        total_bad += counts["mismatched_rows"]
        print(f"[{subdomain}] {counts['mismatched_rows']} of {counts['rows']} rows differ")
        for month in sorted(per_month):
            m = per_month[month]
            if m["mismatched"]:
                print(f"    {month}: {m['mismatched']} of {m['rows']}")
        if examples:
            print("  examples:")
            print("\n".join(examples))
    print(f"\nTOTAL: {total_bad} of {total_rows} checked rows differ from the company's own rules.")
    print("Read-only check finished; no data was changed.")
    conn.close()


if __name__ == "__main__":
    main()
