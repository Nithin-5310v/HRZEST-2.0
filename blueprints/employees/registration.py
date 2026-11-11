# -*- coding: utf-8 -*-
"""employees: add employee (web), welcome credentials email, employee-ID generation"""
import datetime
import hashlib
import os
import psycopg2
import re
import secrets
from database import get_db_connection, transaction
from extensions import app_log
from flask import flash, jsonify, redirect, request, session
from html import escape as _html_escape
from qr_generator import generate_qr
from urllib.parse import urlencode
from utils.attendance_utils import standard_working_days_per_month
from utils.auth import HR_ROLE, admin_required, generate_password_hash, session_account_ok
from utils.billing_notices import notify_employee_added
from utils.email_utils import _mask_email, get_email_config, send_email_smtp
from utils.helpers import (
    _validate_image_file,
    add_employee_seat_cap_check,
    company_today,
    company_url,
    encrypt_amount,
    encrypt_contact,
    encrypt_pii,
    get_company_settings,
    tpath,
    validate_emp_id,
    validate_employee_email_domain,
)
from utils.leave_utils import assign_leave_balances_for_employee
from utils.storage import save_upload
from utils.tenant_files import face_photo_path
from utils.webauthn_utils import _enroll_fingerprint_from_form
from . import employees_bp


def _send_welcome_credentials_email(emp_id, name, email, password=None, must_change=True):
    """The new employee's welcome email: where to sign in, their Employee
    ID and -- as the owner asked -- the temporary password the account was
    created with, plus a one-time 'set your password' link (24h, the same
    reset_token as auth.py's employee_reset_password) for anyone who'd
    rather choose their own straight away. must_change says whether the
    account makes them pick a new password at first sign-in
    (employees.force_pin_change), so the email says so only when it does.

    Returns True if the email was sent, False if no email is configured
    (the callers' silent-skip behaviour)."""
    _ecfg = get_email_config()
    if not _ecfg:
        return False
    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE employees SET reset_token=%s, reset_token_expiry=%s WHERE employee_id=%s",
                   (token_hash, expiry, emp_id))
    db.commit()
    cursor.close()
    db.close()
    set_url = company_url('/employee_reset_password/' + token)
    login_url = company_url('/login')
    company = _html_escape(get_company_settings().get("company_name") or "your company")
    safe_name, safe_id = _html_escape(name or ""), _html_escape(emp_id)
    password_rows = ""
    if password:
        password_rows = (
            f"<tr><td style=\"padding:4px 12px 4px 0;color:#475467;\">Temporary password</td>"
            f"<td style=\"padding:4px 0;\"><strong style=\"font-family:monospace;font-size:15px;\">"
            f"{_html_escape(password)}</strong></td></tr>"
        )
    change_note = ("You'll be asked to choose your own password the first time you sign in."
                   if password and must_change else
                   "You can change your password any time after signing in.")
    html_body = (
        f"<p>Hi <strong>{safe_name}</strong>,</p>"
        f"<p>Your HRzest account at <strong>{company}</strong> is ready.</p>"
        f"<table style=\"border-collapse:collapse;margin:12px 0;\">"
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#475467;\">Sign in at</td>"
        f"<td style=\"padding:4px 0;\"><a href=\"{login_url}\">{login_url}</a></td></tr>"
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#475467;\">Employee ID</td>"
        f"<td style=\"padding:4px 0;\"><strong>{safe_id}</strong></td></tr>"
        f"{password_rows}</table>"
        f"<p>{change_note} The same details work in the HRzest mobile app.</p>"
        f"<p>Prefer to set your own password now? <a href=\"{set_url}\">Set your password</a> "
        f"(link valid for 24 hours).</p>"
        f"<p style=\"color:#667085;font-size:13px;\">Keep this email private. If you weren't expecting it, "
        f"tell your HR team.</p>"
    )
    send_email_smtp(email, f"Welcome to HRzest, {name} -- your sign-in details", html_body, _ecfg)
    return True


def _max_emp_seq(cursor, prefix):
    """Highest numeric suffix among ALL employee IDs of the form <prefix><digits>
    (case-insensitive, global -- employee_id is unique across tenants). The
    prefix is LIKE-escaped so '_' / '%' in a company code can't over-match, and
    only an all-digit remainder counts (so prefix 'AB' ignores 'ABC001')."""
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cursor.execute("SELECT employee_id FROM employees WHERE employee_id ILIKE %s", (like,))
    max_seq = 0
    for (eid,) in cursor.fetchall():
        eid = eid or ""
        sfx = eid[len(prefix):]
        if eid.upper().startswith(prefix.upper()) and sfx.isdigit():
            max_seq = max(max_seq, int(sfx))
    return max_seq


def _split_emp_id(emp_id):
    """'PADW009' -> ('PADW', '009'); 'AB2-007' -> ('AB2-', '007'); 'X' -> ('X', '')."""
    m = re.match(r"^(.*?)(\d*)$", emp_id or "")
    return (m.group(1), m.group(2)) if m else (emp_id, "")


def _next_emp_id(prefix, max_seq, requested_id):
    """Next free ID after `max_seq`, zero-padded to the width of the numeric
    suffix the admin typed (PADW9 -> PADW10, EMP007 -> EMP008); 3 digits when
    the requested ID has no numeric suffix."""
    m = re.search(r"(\d+)$", requested_id or "")
    width = len(m.group(1)) if m else 3
    return f"{prefix}{max_seq + 1:0{width}d}"


def assign_default_onboarding(cursor, db, emp_id):
    """Give a new employee the company's default onboarding checklist, if
    one is set and they don't have it yet. Shared by Add Employee and the
    Excel import (bulk_import.py). Returns whether one was assigned."""
    cursor.execute("SELECT default_onboarding_template_id FROM company_settings LIMIT 1")
    _cs = cursor.fetchone()
    _default_tpl = _cs[0] if _cs and _cs[0] else None
    if not _default_tpl:
        return False
    cursor.execute("SELECT id FROM onboarding_templates WHERE id=%s AND is_active=1", (_default_tpl,))
    if not cursor.fetchone():
        return False
    cursor.execute("""
        SELECT id FROM employee_onboarding
        WHERE employee_id=%s AND template_id=%s
    """, (emp_id, _default_tpl))
    if cursor.fetchone():
        return False
    _due = (company_today() + datetime.timedelta(days=30)).isoformat()
    cursor.execute("""
        INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date, status)
        VALUES (%s, %s, %s, %s, 'In Progress') RETURNING id
    """, (emp_id, _default_tpl, company_today().isoformat(), _due))
    _ob_id = cursor.fetchone()[0]
    db.commit()
    cursor.execute("""
        SELECT id, task_title, task_description, requires_document, due_days, sort_order
        FROM onboarding_template_tasks WHERE template_id=%s ORDER BY sort_order, id
    """, (_default_tpl,))
    for _tt in cursor.fetchall():
        cursor.execute("""
            INSERT INTO employee_onboarding_tasks
            (onboarding_id, template_task_id, employee_id, task_title,
             task_description, requires_document, due_days, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'Pending')
        """, (_ob_id, _tt[0], emp_id, _tt[1], _tt[2], _tt[3], _tt[4]))
    db.commit()
    return True


@employees_bp.route("/add_employee_page", methods=["POST"])
@admin_required
def add_employee_page():
    name = request.form.get("name", "").strip()
    emp_id = request.form.get("emp_id", "").strip()
    email = request.form.get("email", "").strip() or None
    role = request.form.get("role", "").strip() or None
    # A brand-new hire always starts with no access role (plain employee
    # portal) -- Access Role is set ONLY from Settings -> Roles & Access now
    # ("Assign Someone New"), which also keeps the admin_users shadow
    # account that login routing actually reads in sync. This route no
    # longer reads an access_role field at all, so a forged one from, say,
    # an HR session's add-employee request can't grant admin-side privileges.
    access_role = None
    phone = request.form.get("phone", "").strip() or None
    date_of_joining = request.form.get("date_of_joining", "").strip() or None
    work_mode = request.form.get("work_mode", "office").strip() or "office"
    work_lat_raw = request.form.get("work_lat", "").strip()
    work_lon_raw = request.form.get("work_lon", "").strip()
    work_lat = float(work_lat_raw) if work_lat_raw else None
    work_lon = float(work_lon_raw) if work_lon_raw else None
    company_id_raw = request.form.get("company_id", "").strip()
    company_id = int(company_id_raw) if company_id_raw.isdigit() else None
    shift_id_raw = request.form.get("shift_id", "").strip()
    shift_id = int(shift_id_raw) if shift_id_raw else None

    gender = encrypt_pii(request.form.get("gender", "").strip() or None)
    dob_raw = request.form.get("dob", "").strip()
    dob = encrypt_pii(dob_raw) if dob_raw else None
    blood_group = encrypt_pii(request.form.get("blood_group", "").strip() or None)
    address = encrypt_pii(request.form.get("address", "").strip() or None)
    city = encrypt_pii(request.form.get("city", "").strip() or None)
    state = encrypt_pii(request.form.get("state", "").strip() or None)
    pincode = encrypt_pii(request.form.get("pincode", "").strip() or None)
    ec_name = encrypt_pii(request.form.get("emergency_contact_name", "").strip() or None)
    ec_phone = encrypt_pii(request.form.get("emergency_contact_phone", "").strip() or None)
    ec_relation = encrypt_pii(request.form.get("emergency_contact_relation", "").strip() or None)
    aadhar = encrypt_pii(request.form.get("aadhar_number", "").strip() or None)
    pan = encrypt_pii(request.form.get("pan_number", "").strip().upper() or None)
    bank_name = encrypt_pii(request.form.get("bank_name", "").strip() or None)
    bank_account = encrypt_pii(request.form.get("bank_account", "").strip() or None)
    bank_ifsc = encrypt_pii(request.form.get("bank_ifsc", "").strip().upper() or None)
    uan = encrypt_pii(request.form.get("uan_number", "").strip() or None)
    edu_degrees = request.form.getlist("degree[]")
    edu_institutions = request.form.getlist("institution[]")
    edu_years = request.form.getlist("year_of_passing[]")
    edu_pcts = request.form.getlist("percentage[]")
    # The Add Employee form collects a monthly figure (matches how payroll
    # is actually quoted/discussed) and converts it to salary_per_day using
    # the same standard-working-days-per-month divisor the rest of payroll
    # already assumes (see utils/attendance_utils.py's
    # standard_working_days_per_month(), derived from this company's
    # Settings -> Working Days, and utils/salary_utils.py's
    # apply_statutory_deductions doing the same conversion in reverse).
    monthly_salary_raw = request.form.get("monthly_salary", "").strip()
    monthly_salary = float(monthly_salary_raw) if monthly_salary_raw else None
    salary_per_day = (
        round(monthly_salary / standard_working_days_per_month(), 2)
        if monthly_salary is not None else None
    )

    if not name or not emp_id:
        flash("Name and Employee ID are required.", "error")
        return redirect(tpath("/employees"))
    if not validate_emp_id(emp_id):
        flash("Employee ID may only contain letters, digits, hyphens and underscores.", "error")
        return redirect(tpath("/employees"))
    # Bounded employees.* column widths -- an over-long value would otherwise
    # reach PostgreSQL and surface as an unhandled psycopg2.DataError (HTTP 500).
    for _label, _value, _max_len in (
        ("Name", name, 100), ("Employee ID", emp_id, 50), ("Email", email, 150),
        ("Role", role, 100), ("Work mode", work_mode, 20),
    ):
        if _value and len(_value) > _max_len:
            flash(f"{_label} is too long (max {_max_len} characters).", "error")
            return redirect(tpath("/employees"))
    _domain_error = validate_employee_email_domain(email)
    if _domain_error:
        flash(_domain_error, "error")
        return redirect(tpath("/employees"))
    _seat_error = add_employee_seat_cap_check()
    if _seat_error:
        flash(_seat_error, "error")
        return redirect(tpath("/employees"))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    # Which HR this new employee is managed by: any HR with dashboard access
    # (blueprints/hr_team.hr_choices), checked here rather than trusted as
    # free text. An HR adding someone may hand them to another HR, and is
    # the default (an unknown choice falls back to themselves); an admin may
    # also leave it unset (unassigned: only admins see the employee until an
    # HR is assigned). Changing it later is an admin-only action.
    from blueprints.hr_team import is_assignable_hr
    assigned_hr_username = request.form.get("assigned_hr_username", "").strip() or None
    if assigned_hr_username and not is_assignable_hr(cursor, assigned_hr_username):
        assigned_hr_username = None
    if session.get("admin_role") == HR_ROLE and not assigned_hr_username:
        assigned_hr_username = session.get("admin_username")

    # Auto-increment emp_id if already taken
    cursor.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (emp_id,))
    if cursor.fetchone():
        prefix, _digits = _split_emp_id(emp_id)
        if prefix and _digits:
            emp_id = _next_emp_id(prefix, _max_emp_seq(cursor, prefix), emp_id)

    # Photo is optional when adding an employee, and any valid image is
    # accepted (no face-detection requirement). Face attendance still needs a
    # clear face photo, which can be uploaded later from the employee page.
    file = request.files.get("face")
    filepath = None
    if file and file.filename:
        _img_ok, _img_err = _validate_image_file(file)
        if not _img_ok:
            flash(_img_err, "error")
            cursor.close()
            db.close()
            return redirect(tpath("/employees"))
        filepath = face_photo_path(emp_id)
        save_upload(file, filepath)

    auto_pass = secrets.token_urlsafe(8)
    hashed_pwd = generate_password_hash(auto_pass)

    # Retry up to 5 times in case of duplicate ID collision
    prefix, _digits = _split_emp_id(emp_id)
    if not prefix:
        prefix = emp_id
    original_filepath = filepath  # photo was already saved under initial emp_id (or None)
    registered = False
    for _attempt in range(5):
        # Keep photo file in sync with the current emp_id on each retry attempt
        if filepath:
            new_filepath = face_photo_path(emp_id)
            if new_filepath != original_filepath and os.path.exists(original_filepath):
                try:
                    os.rename(original_filepath, new_filepath)
                    original_filepath = new_filepath
                except OSError as exc:
                    # Photo stays under the pre-retry emp_id filename -- not
                    # fatal to registration, but worth knowing since it means
                    # the employee's photo path may not match their final ID.
                    app_log.warning("Could not rename employee photo %s -> %s: %s", original_filepath, new_filepath, exc)
            filepath = new_filepath
        qr_path = generate_qr(emp_id)
        try:
            # Authoritative, lock-protected recheck immediately before the
            # INSERT, wrapped in an explicit transaction() (not just the
            # same cursor) -- the pooled connection defaults to
            # autocommit=True (database.py's _borrow_connection()), so
            # without this the FOR UPDATE lock taken inside
            # add_employee_seat_cap_check() would release itself the
            # instant that one SELECT statement finished, before this
            # INSERT ever ran, and the race would still be wide open. The
            # earlier add_employee_seat_cap_check() call above (no
            # cursor, own connection) is only a cheap UX preflight and
            # does NOT prevent two concurrent requests both passing it
            # before either has inserted -- see that function's docstring.
            _seat_error = None
            with transaction(db):
                _seat_error = add_employee_seat_cap_check(cursor)
                if not _seat_error:
                    _mgr_id = request.form.get("manager_id", "").strip() or None
                    _mgr_name = request.form.get("manager_name", "").strip() or None
                    _dept = request.form.get("department", "").strip() or None
                    _enc_email, _email_bidx = encrypt_contact("email", email)
                    _enc_phone, _phone_bidx = encrypt_contact("phone", phone)
                    cursor.execute(
                        "INSERT INTO employees (name, employee_id, email, email_bidx, role, phone, phone_bidx, "
                        "face_image, qr_code, password, "
                        "date_of_joining, work_mode, work_lat, work_lon, company_id, manager_id, manager_name, department, "
                        "shift_id, gender, dob, blood_group, address, city, state, pincode, "
                        "emergency_contact_name, emergency_contact_phone, emergency_contact_relation, "
                        "aadhar_number, pan_number, bank_name, bank_account, bank_ifsc, uan_number, "
                        "assigned_hr_username, access_role, force_pin_change) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)",
                        (name, emp_id, _enc_email, _email_bidx, role, _enc_phone, _phone_bidx,
                         filepath, qr_path, hashed_pwd,
                         date_of_joining, work_mode, work_lat, work_lon, company_id,
                         _mgr_id, _mgr_name, _dept, shift_id,
                         gender, dob, blood_group, address, city, state, pincode,
                         ec_name, ec_phone, ec_relation,
                         aadhar, pan, bank_name, bank_account, bank_ifsc, uan, assigned_hr_username,
                         access_role)
                    )
            if _seat_error:
                cursor.close()
                db.close()
                if os.path.exists(filepath):
                    os.remove(filepath)
                flash(_seat_error, "error")
                return redirect(tpath("/employees"))
            _enroll_fingerprint_from_form(emp_id, cursor, db)
            assign_leave_balances_for_employee(cursor, emp_id)
            for _deg, _inst, _yr, _pct in zip(edu_degrees, edu_institutions, edu_years, edu_pcts):
                _deg, _inst = _deg.strip(), _inst.strip()
                if _deg and _inst:
                    cursor.execute(
                        "INSERT INTO employee_education (employee_id, degree, institution, year_of_passing, percentage) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (emp_id, _deg, _inst, _yr.strip() or None, _pct.strip() or None)
                    )
            if salary_per_day is not None:
                cursor.execute(
                    "INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc) VALUES (%s,%s,%s) "
                    "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=%s, monthly_ctc=%s",
                    (emp_id, encrypt_amount(salary_per_day), encrypt_amount(monthly_salary),
                     encrypt_amount(salary_per_day), encrypt_amount(monthly_salary))
                )
            db.commit()
            registered = True
            break
        except psycopg2.IntegrityError:
            db.rollback()
            # Find next available ID and retry
            emp_id = _next_emp_id(prefix, _max_emp_seq(cursor, prefix), emp_id)

    if registered:
        flash(f"Employee '{name}' registered! ID: {emp_id} | Password: {auto_pass}", "success")
        if session.get("admin_role") == HR_ROLE and assigned_hr_username != session.get("admin_username"):
            from blueprints.hr_team import hr_label
            flash(f"{name} is assigned to {hr_label(cursor, assigned_hr_username)}, who manages them from now on.",
                  "success")
        # Auto-assign default onboarding template if configured
        if assign_default_onboarding(cursor, db, emp_id):
            flash("Onboarding checklist auto-assigned.", "success")
        if email:
            try:
                if _send_welcome_credentials_email(emp_id, name, email, auto_pass):
                    flash(f"Credentials email sent to {email}", "success")
            except Exception as exc:
                app_log.warning("Welcome-credentials email failed for %s (%s): %s", emp_id, _mask_email(email), exc, exc_info=True)
    else:
        if os.path.exists(filepath):
            os.remove(filepath)
        flash("Registration failed. Please try again.", "error")
    cursor.close()
    db.close()
    if registered:
        notify_employee_added()
        # ?new= lets templates/employees.html jump to (and highlight) the new
        # row -- the table is paged 20 at a time in name order, so a new
        # hire usually lands on a later page and looked like it was missing.
        return redirect(tpath("/employees") + "?" + urlencode({"new": emp_id}))
    return redirect(tpath("/employees"))


@employees_bp.route("/api/generate_emp_id")
def generate_emp_id():
    if not session.get("admin_logged_in") or not session_account_ok():
        return jsonify({"error": "not logged in"}), 401
    company_id_raw = request.args.get("company_id", "").strip()
    if not company_id_raw.isdigit() and session.get("active_company_id"):
        company_id_raw = str(session.get("active_company_id"))
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    if company_id_raw.isdigit():
        cursor.execute("SELECT COALESCE(code,''), name FROM companies WHERE id=%s", (int(company_id_raw),))
        row = cursor.fetchone()
        code = (row[0] or "").strip().upper() if row else ""
        company_name = row[1] if row else ""
    else:
        cursor.execute("SELECT COALESCE(company_code,'') FROM company_settings LIMIT 1")
        row = cursor.fetchone()
        code = (row[0] or "").strip().upper() if row else ""
        company_name = ""

    prefix = code if code else "EMP"
    max_seq = _max_emp_seq(cursor, prefix)
    cursor.close()
    db.close()
    seq = max_seq + 1
    emp_id = f"{prefix}{seq:03d}"
    return jsonify({"emp_id": emp_id, "code": code, "seq": seq, "company_name": company_name})
