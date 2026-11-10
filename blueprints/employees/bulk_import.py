# -*- coding: utf-8 -*-
"""employees: register many employees at once from an Excel sheet.

  GET  /employees/import/template   the .xlsx to fill in
  POST /api/employees/import/preview  check an uploaded sheet: which rows are
                                    ready, which have problems (and why), and
                                    what has to be paid first
  POST /api/employees/import        register the ready rows

A company with a seat limit pays one month (the per-employee rate) for each
employee beyond the paid seats it has free, before they're registered --
same rule and same payment as Add Employee (blueprints/seats.py,
static/js/seat-pay.js); from the next billing date its monthly bill /
Auto-Debit includes them. Rows with problems are skipped, not blocking: the
rest are registered and the bad ones listed to fix and upload again.

Nothing is stored between the preview and the import: the browser sends the
same file again and it's checked again, so a sheet can't be changed in
between. Each employee goes through the same steps as Add Employee
(registration.py): the seat check under the company_settings lock, an
automatic Employee ID when none is given, leave balances, salary, the
default onboarding checklist and the welcome email with sign-in details.
"""
import datetime
import io
import re
import secrets
import threading

import psycopg2
from flask import copy_current_request_context, current_app, g, jsonify, request, send_file, session

from database import get_db_connection, transaction
from extensions import app_log, limiter, log_security_event
from qr_generator import generate_qr
from utils.auth import HR_ROLE, admin_required, generate_password_hash
from utils.billing_notices import notify_employee_added
from utils.email_utils import _mask_email, get_email_config
from utils.helpers import (
    add_employee_seat_cap_check,
    encrypt_amount,
    encrypt_contact,
    encrypt_pii,
    validate_emp_id,
    validate_employee_email_domain,
)
from utils.leave_utils import assign_leave_balances_for_employee
from . import employees_bp
from .registration import _max_emp_seq, _send_welcome_credentials_email, assign_default_onboarding

MAX_ROWS = 1000
MAX_BYTES = 2 * 1024 * 1024

# (key, header in the template, column width)
COLUMNS = (
    ("name", "Full Name *", 26),
    ("emp_id", "Employee ID (leave blank for automatic)", 22),
    ("email", "Email", 30),
    ("phone", "Phone", 16),
    ("role", "Role / Designation", 22),
    ("department", "Department", 18),
    ("date_of_joining", "Date of Joining (YYYY-MM-DD)", 18),
    ("work_mode", "Work Mode (office / wfh)", 16),
    ("gender", "Gender (Male / Female / Other)", 16),
    ("dob", "Date of Birth (YYYY-MM-DD)", 18),
    ("monthly_salary", "Monthly Salary", 16),
)
_LIMITS = {"name": 100, "emp_id": 50, "email": 150, "phone": 20, "role": 100, "department": 100}
_GENDERS = {"male": "Male", "female": "Female", "other": "Other", "m": "Male", "f": "Female"}
_WORK_MODES = {"office": "office", "wfh": "wfh", "work from home": "wfh", "remote": "wfh", "home": "wfh"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm(header):
    """'Date of Joining (YYYY-MM-DD)' -> 'dateofjoining'."""
    return re.sub(r"[^a-z]", "", str(header or "").split("(")[0].lower())


_HEADER_KEYS = {_norm(h): k for k, h, _ in COLUMNS}
_HEADER_KEYS.update({
    "name": "name", "employeename": "name", "employeeid": "emp_id", "empid": "emp_id", "id": "emp_id",
    "emailid": "email", "emailaddress": "email", "mobile": "phone", "mobilenumber": "phone",
    "phonenumber": "phone", "designation": "role", "role": "role", "doj": "date_of_joining",
    "joiningdate": "date_of_joining", "workmode": "work_mode", "dateofbirth": "dob", "salary": "monthly_salary",
    "monthlyctc": "monthly_salary", "ctc": "monthly_salary",
})


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # a phone or ID typed as a number
    return str(value).strip()


def _date(value):
    """(YYYY-MM-DD or None, error or None) from a date cell or text."""
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime.datetime):
        return value.date().isoformat(), None
    if isinstance(value, datetime.date):
        return value.isoformat(), None
    text = _text(value)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat(), None
        except ValueError:
            continue
    return None, f"'{text}' isn't a date (use YYYY-MM-DD)"


def _read_sheet(file_storage):
    """[(row number, {key: raw value})] from the first sheet, or an error."""
    if not file_storage or not file_storage.filename:
        return None, "Choose an Excel file (.xlsx) to upload."
    if not file_storage.filename.lower().endswith(".xlsx"):
        return None, "Upload an Excel .xlsx file -- download the template to start from."
    data = file_storage.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return None, "That file is over 2 MB. Split it into smaller sheets."
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None) or ()
        keys = [_HEADER_KEYS.get(_norm(h)) for h in header]
        if "name" not in keys:
            wb.close()
            return None, "The first row must have the column headings from the template (at least 'Full Name')."
        out = []
        for number, values in enumerate(rows, start=2):
            record = {k: v for k, v in zip(keys, values) if k}
            if not any(_text(v) for v in record.values()):
                continue  # blank row
            if len(out) >= MAX_ROWS:
                wb.close()
                return None, f"A sheet can have up to {MAX_ROWS} employees. Split it into smaller sheets."
            out.append((number, record))
        wb.close()
    except Exception as exc:
        app_log.info("employee import: unreadable sheet: %s", exc)
        return None, "That file couldn't be read as an Excel sheet. Save it as .xlsx and try again."
    if not out:
        return None, "The sheet has no employees in it."
    return out, None


def _check_rows(cursor, rows):
    """(ready rows, problems). A ready row is a dict of clean values; each
    problem is {"row", "name", "msg"} with every reason for that row."""
    ready, problems = [], []
    seen_ids, seen_emails = {}, {}
    for number, raw in rows:
        r = {k: _text(raw.get(k)) for k, _, _ in COLUMNS if k not in ("date_of_joining", "dob")}
        errors = []
        if not r["name"]:
            errors.append("Full Name is missing")
        for key, limit in _LIMITS.items():
            if r[key] and len(r[key]) > limit:
                errors.append(f"{key.replace('_', ' ')} is longer than {limit} characters")
        if r["emp_id"]:
            if not validate_emp_id(r["emp_id"]):
                errors.append("Employee ID may only have letters, digits, - and _")
            elif r["emp_id"].upper() in seen_ids:
                errors.append(f"Employee ID {r['emp_id']} is also on row {seen_ids[r['emp_id'].upper()]}")
            else:
                cursor.execute("SELECT 1 FROM employees WHERE UPPER(employee_id)=UPPER(%s)", (r["emp_id"],))
                if cursor.fetchone():
                    errors.append(f"Employee ID {r['emp_id']} is already taken")
                seen_ids[r["emp_id"].upper()] = number
        if r["email"]:
            r["email"] = r["email"].lower()
            if not _EMAIL_RE.match(r["email"]):
                errors.append(f"'{r['email']}' isn't an email address")
            else:
                domain_error = validate_employee_email_domain(r["email"])
                if domain_error:
                    errors.append(domain_error)
                elif r["email"] in seen_emails:
                    errors.append(f"{r['email']} is also on row {seen_emails[r['email']]}")
                else:
                    cursor.execute("SELECT 1 FROM employees WHERE email_bidx=%s",
                                   (encrypt_contact("email", r["email"])[1],))
                    if cursor.fetchone():
                        errors.append(f"{r['email']} already belongs to an employee")
                    seen_emails[r["email"]] = number
        if r["phone"] and not re.fullmatch(r"\+?[\d\s-]{6,20}", r["phone"]):
            errors.append(f"'{r['phone']}' isn't a phone number")
        for key, label in (("date_of_joining", "Date of Joining"), ("dob", "Date of Birth")):
            r[key], err = _date(raw.get(key))
            if err:
                errors.append(f"{label}: {err}")
        if r["work_mode"]:
            mode = _WORK_MODES.get(r["work_mode"].lower())
            if not mode:
                errors.append("Work Mode must be office or wfh")
            r["work_mode"] = mode or "office"
        else:
            r["work_mode"] = "office"
        if r["gender"]:
            gender = _GENDERS.get(r["gender"].lower())
            if not gender:
                errors.append("Gender must be Male, Female or Other")
            r["gender"] = gender
        if r["monthly_salary"]:
            try:
                r["monthly_salary"] = float(r["monthly_salary"].replace(",", ""))
                if r["monthly_salary"] < 0:
                    raise ValueError
            except ValueError:
                errors.append("Monthly Salary must be a number")
        else:
            r["monthly_salary"] = None
        if errors:
            problems.append({"row": number, "name": r["name"], "msg": "; ".join(errors)})
        else:
            r["row"] = number
            ready.append(r)
    return ready, problems


def _payment_plan(ready_count):
    """What has to be paid before `ready_count` employees can be added."""
    from blueprints.seats import seat_payment_info
    from utils.plan_limits import format_price_inr
    info = seat_payment_info()
    if not info.get("limited"):
        return {"to_pay": 0, "free": None}
    to_pay = max(0, ready_count - info["free"])
    return {"to_pay": to_pay, "free": info["free"], "rate_display": info["rate_display"],
            "amount_display": format_price_inr(to_pay * info["rate_paise"]), "renews_on": info["renews_on"]}


@employees_bp.route("/employees/import/template", methods=["GET"])
@admin_required
def employee_import_template():
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Employees"
    ws.append([h for _, h, _ in COLUMNS])
    for i, (_, _, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1E3A8A")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[cell.column_letter].width = width
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"
    ws.append(["Priya Sharma", "", "priya@example.com", "9876543210", "Accountant", "Finance",
               "2026-11-15", "office", "Female", "1995-04-21", 32000])
    ws.cell(row=2, column=1).font = Font(italic=True, color="64748B")
    notes = wb.create_sheet("How to fill")
    for line in (
        "One employee per row on the Employees sheet. Delete the example row (Priya Sharma) first.",
        "Only Full Name is required. Leave Employee ID blank to have one made for you.",
        "Dates: YYYY-MM-DD, e.g. 2026-11-15. Work Mode: office or wfh. Gender: Male, Female or Other.",
        "Each employee gets a welcome email with their sign-in details when an Email is given.",
        f"Up to {MAX_ROWS} employees per file. Rows with problems are skipped and listed so you can fix them.",
        "If your paid seats are all in use, you pay for the extra employees (one month each) before they're added.",
    ):
        notes.append([line])
    notes.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="hrzest-employee-import.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@employees_bp.route("/api/employees/import/preview", methods=["POST"])
@admin_required
@limiter.limit("30 per minute")
def employee_import_preview():
    rows, error = _read_sheet(request.files.get("file"))
    if error:
        return jsonify({"ok": False, "msg": error}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        ready, problems = _check_rows(cursor, rows)
    finally:
        cursor.close()
        db.close()
    return jsonify({
        "ok": True,
        "ready": [{"row": r["row"], "name": r["name"], "emp_id": r["emp_id"] or None, "email": r["email"] or None}
                  for r in ready],
        "problems": problems,
        "payment": _payment_plan(len(ready)),
    })


def _insert_employee(cursor, db, r, emp_id, password, company_id, assigned_hr):
    """One employee row (and its leave balances / salary), inside the seat
    check's lock -- the same as Add Employee. Returns the seat-limit message
    if the company ran out of paid seats, else None."""
    qr_path = generate_qr(emp_id)
    with transaction(db):
        seat_error = add_employee_seat_cap_check(cursor)
        if seat_error:
            return seat_error
        enc_email, email_bidx = encrypt_contact("email", r["email"] or None)
        enc_phone, phone_bidx = encrypt_contact("phone", r["phone"] or None)
        cursor.execute(
            "INSERT INTO employees (name, employee_id, email, email_bidx, role, phone, phone_bidx, qr_code, "
            "password, date_of_joining, work_mode, company_id, department, gender, dob, "
            "assigned_hr_username, force_pin_change) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)",
            (r["name"], emp_id, enc_email, email_bidx, r["role"] or None, enc_phone, phone_bidx, qr_path,
             generate_password_hash(password), r["date_of_joining"], r["work_mode"], company_id,
             r["department"] or None, encrypt_pii(r["gender"] or None), encrypt_pii(r["dob"]) if r["dob"] else None,
             assigned_hr)
        )
        assign_leave_balances_for_employee(cursor, emp_id)
        if r["monthly_salary"] is not None:
            per_day = round(r["monthly_salary"] / 26, 2)  # same divisor as Add Employee
            cursor.execute(
                "INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc) VALUES (%s,%s,%s) "
                "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=%s, monthly_ctc=%s",
                (emp_id, encrypt_amount(per_day), encrypt_amount(r["monthly_salary"]),
                 encrypt_amount(per_day), encrypt_amount(r["monthly_salary"]))
            )
    return None


@employees_bp.route("/api/employees/import", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def employee_import():
    rows, error = _read_sheet(request.files.get("file"))
    if error:
        return jsonify({"ok": False, "msg": error}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    try:
        ready, problems = _check_rows(cursor, rows)
        if not ready:
            return jsonify({"ok": False, "msg": "No rows are ready to register -- fix the problems listed and "
                                                "upload again.", "problems": problems}), 400
        plan = _payment_plan(len(ready))
        if plan["to_pay"]:
            return jsonify({"ok": False, "payment": plan,
                            "msg": f"Pay for {plan['to_pay']} more employee seat"
                                   f"{'s' if plan['to_pay'] != 1 else ''} first."}), 402

        # An HR adding people manages them; an admin's imports start unassigned
        # (Add Employee's default for each).
        assigned_hr = session.get("admin_username") if session.get("admin_role") == HR_ROLE else None
        company_id = session.get("active_company_id")
        company_id = int(company_id) if str(company_id or "").isdigit() else None
        cursor.execute("SELECT COALESCE(company_code,'') FROM company_settings LIMIT 1")
        code_row = cursor.fetchone()
        prefix = ((code_row[0] if code_row else "") or "").strip().upper() or "EMP"
        next_seq = _max_emp_seq(cursor, prefix) + 1
        typed_ids = {r["emp_id"].upper() for r in ready if r["emp_id"]}  # never hand these out automatically

        created = []
        for r in ready:
            password = secrets.token_urlsafe(8)
            emp_id, outcome = r["emp_id"], None
            for _attempt in range(5):
                if not r["emp_id"]:
                    while f"{prefix}{next_seq:03d}".upper() in typed_ids:
                        next_seq += 1
                    emp_id = f"{prefix}{next_seq:03d}"
                    next_seq += 1
                try:
                    outcome = _insert_employee(cursor, db, r, emp_id, password, company_id, assigned_hr) or "ok"
                    break
                except psycopg2.IntegrityError:
                    if r["emp_id"]:  # taken since the check -- the sheet's own ID can't change
                        outcome = f"Employee ID {emp_id} was taken just now"
                        break
            if outcome != "ok":
                problems.append({"row": r["row"], "name": r["name"],
                                 "msg": outcome or "Couldn't register -- please try again"})
                if outcome and "paid employee seat" in outcome:
                    # Out of paid seats (someone else just used one): the rest can't fit either.
                    for later in ready[ready.index(r) + 1:]:
                        problems.append({"row": later["row"], "name": later["name"], "msg": outcome})
                    break
                continue
            assign_default_onboarding(cursor, db, emp_id)
            created.append({"row": r["row"], "name": r["name"], "emp_id": emp_id, "email": r["email"] or None,
                            "password": password})
    finally:
        cursor.close()
        db.close()

    to_email = [c for c in created if c["email"]] if get_email_config() else []
    if to_email:
        _send_welcome_emails(to_email)
    if created:
        notify_employee_added(added=len(created))
        log_security_event("employees.imported", f"{len(created)} employee(s) registered from an Excel sheet",
                           level="INFO", count=len(created), skipped=len(problems))
    problems.sort(key=lambda p: p["row"])
    return jsonify({"ok": True, "created": created, "problems": problems, "emailing": len(to_email)})


def _send_welcome_emails(employees):
    """Each new employee's welcome email with their sign-in details, sent
    one by one after the response -- a big sheet would otherwise keep the
    page waiting on every email. Runs with this request's context (the
    sign-in link is built from it) and this company's schema set
    explicitly, so a reset link is never written to another company."""
    tenant_db = getattr(g, "tenant_db", None)

    @copy_current_request_context
    def _send_all():
        if tenant_db:
            g.tenant_db = tenant_db
        for c in employees:
            try:
                _send_welcome_credentials_email(c["emp_id"], c["name"], c["email"], c["password"])
            except Exception as exc:
                app_log.warning("employee import: welcome email failed for %s (%s): %s",
                                c["emp_id"], _mask_email(c["email"]), exc)

    if current_app.testing:
        _send_all()  # tests check the emails as soon as the call returns
    else:
        threading.Thread(target=_send_all, name="employee-import-emails", daemon=True).start()
