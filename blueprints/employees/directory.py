# -*- coding: utf-8 -*-
"""employees: employee list, departments, employee detail page"""
import datetime
from database import get_db_connection
from extensions import log_security_event
from flask import flash, jsonify, redirect, render_template, request, session
from utils.attendance_utils import _td_to_time
from utils.auth import HR_ROLE, admin_required, api_required, require_access
from utils.dlp import has_pii_clearance, mask_tail
from utils.helpers import (
    _db,
    amount,
    decrypt_pii,
    decrypt_pii_date,
    get_company_settings,
    get_pending_counts,
    hr_scope_denied as _hr_scope_denied,
    tpath,
)
from . import employees_bp


@employees_bp.route("/employees")
@admin_required
@require_access("employees")
def view_employees():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    active_cid = session.get("active_company_id")

    # HR-role sessions (a real admin_users role='hr' account, or an
    # employee auto-routed here because their own employees.role is
    # exactly "HR" -- see blueprints/auth.py's _finish_employee_login) see
    # ONLY the employees explicitly assigned to them, not the whole
    # company -- 'admin' (and any other admin-side role reaching this
    # @admin_required page) keeps the unscoped, full-company view exactly
    # as before. An employee with no assigned_hr_username is invisible to
    # every HR session until an admin assigns one; it never silently
    # defaults to "visible to all HR."
    is_hr_scoped = session.get("admin_role") == HR_ROLE
    current_hr_username = session.get("admin_username")
    _hr_filter = " AND e.assigned_hr_username = %s" if is_hr_scoped else ""

    if active_cid:
        _params = (active_cid, current_hr_username) if is_hr_scoped else (active_cid,)
        cursor.execute(f"""
            SELECT e.employee_id, e.name, e.role, e.email, e.date_of_joining,
                   COUNT(a.date)  AS total_days,
                   MAX(a.date)    AS last_seen,
                   e.work_mode, e.work_lat, e.work_lon,
                   e.face_image, e.qr_code,
                   e.department, e.phone, e.gender,
                   s.name AS shift_name, e.shift_id, e.assigned_hr_username
            FROM employees e
            LEFT JOIN attendance a ON e.employee_id = a.employee_id
            LEFT JOIN shifts     s ON e.shift_id = s.id
            WHERE e.company_id = %s {_hr_filter}
            GROUP BY e.employee_id, e.name, e.role, e.email, e.date_of_joining,
                     e.work_mode, e.work_lat, e.work_lon, e.face_image, e.qr_code,
                     e.department, e.phone, e.gender, s.name, e.shift_id, e.assigned_hr_username
            ORDER BY e.name
            LIMIT 500
        """, _params)  # nosec B608 -- _hr_filter is a hardcoded literal chosen by is_hr_scoped, never user input
    else:
        _params = (current_hr_username,) if is_hr_scoped else ()
        # WHERE 1=1 (not a bare WHERE) so _hr_filter's leading " AND" is
        # always valid whether or not the company-scoping branch above ran.
        cursor.execute(f"""
            SELECT e.employee_id, e.name, e.role, e.email, e.date_of_joining,
                   COUNT(a.date)  AS total_days,
                   MAX(a.date)    AS last_seen,
                   e.work_mode, e.work_lat, e.work_lon,
                   e.face_image, e.qr_code,
                   e.department, e.phone, e.gender,
                   s.name AS shift_name, e.shift_id, e.assigned_hr_username
            FROM employees e
            LEFT JOIN attendance a ON e.employee_id = a.employee_id
            LEFT JOIN shifts     s ON e.shift_id = s.id
            WHERE 1=1 {_hr_filter}
            GROUP BY e.employee_id, e.name, e.role, e.email, e.date_of_joining,
                     e.work_mode, e.work_lat, e.work_lon, e.face_image, e.qr_code,
                     e.department, e.phone, e.gender, s.name, e.shift_id, e.assigned_hr_username
            ORDER BY e.name
            LIMIT 500
        """, _params)  # nosec B608 -- _hr_filter is a hardcoded literal chosen by is_hr_scoped, never user input
    employees_raw = cursor.fetchall()

    cursor.execute("SELECT DISTINCT employee_id FROM resignation_requests WHERE status='Accepted'")
    resigned_set = {r[0] for r in cursor.fetchall()}
    cursor.execute(
        "SELECT DISTINCT employee_id FROM leave_requests "
        "WHERE status='Approved' AND leave_date=CURRENT_DATE"
    )
    on_leave_set = {r[0] for r in cursor.fetchall()}

    employees = []
    for row in employees_raw:
        eid = row[0]
        if eid in resigned_set:
            emp_status = "Resigned"
        elif eid in on_leave_set:
            emp_status = "On Leave"
        else:
            emp_status = "Active"
        # Postgres returns date_of_joining (index 4) as a real date object;
        # the SQLite dev fallback has no DATE type and hands back the raw
        # "YYYY-MM-DD" string instead, which templates/employees.html's
        # doj.strftime(...) can't call -- normalize here so both backends
        # reach the template as the same type.
        _doj = row[4]
        if isinstance(_doj, str):
            try:
                _doj = datetime.datetime.strptime(_doj[:10], "%Y-%m-%d").date()
            except ValueError:
                _doj = None
        row = row[:4] + (_doj,) + row[5:14] + (decrypt_pii(row[14]),) + row[15:]  # [14]=gender
        employees.append(row + (emp_status,))

    total = len(employees)
    active_count = sum(1 for e in employees if e[-1] == "Active")
    on_leave_count = sum(1 for e in employees if e[-1] == "On Leave")
    resigned_count = sum(1 for e in employees if e[-1] == "Resigned")

    # Full shift details for Schedule tab
    cursor.execute("SELECT id, name, start_time, half_time, end_time FROM shifts ORDER BY start_time")
    shift_full = []
    for sid, sname, st, ht, et in cursor.fetchall():
        shift_full.append({
            "id": sid, "name": sname,
            "start": _td_to_time(st).strftime("%H:%M") if st else "--",
            "half": _td_to_time(ht).strftime("%H:%M") if ht else "--",
            "end": _td_to_time(et).strftime("%H:%M") if et else "--",
        })
    # Simple (id, name) list for dropdowns
    cursor.execute("SELECT id, name FROM shifts ORDER BY name")
    shifts = cursor.fetchall()

    # Breaks list
    cursor.execute("SELECT id, break_name, break_time, duration_minutes, is_active FROM break_config ORDER BY break_time")
    breaks_raw = cursor.fetchall()
    breaks_list = []
    for bid, bname, bt, dur, bactive in breaks_raw:
        def _fmt_bt(v):
            if v is None:
                return "--"
            if isinstance(v, datetime.timedelta):
                h, m = divmod(int(v.total_seconds()) // 60, 60)
                return "%02d:%02d" % (h, m)
            return str(v)[:5]
        breaks_list.append({"id": bid, "name": bname, "time": _fmt_bt(bt), "duration": dur, "active": bactive})

    cursor.execute(
        "SELECT department FROM employees "
        "WHERE department IS NOT NULL AND department != '' "
        "GROUP BY department ORDER BY MIN(id) ASC"
    )
    departments = [r[0] for r in cursor.fetchall()]

    if is_hr_scoped:
        # get_pending_counts() (below, for the unscoped case) is a shared,
        # cached, company-wide helper reused by other pages (e.g. /admin) --
        # deliberately NOT reused/modified here, since scoping IT to one
        # HR's assignees would silently corrupt the cache for every other
        # caller that wants the real company-wide count. This runs its own
        # always-fresh, assignment-scoped queries instead.
        _assignee_sub = "employee_id IN (SELECT employee_id FROM employees WHERE assigned_hr_username=%s)"
        # nosec B608 below: _assignee_sub is a hardcoded literal, not user input.
        cursor.execute(f"SELECT COUNT(*) FROM leave_requests WHERE status='Pending' AND {_assignee_sub}",  # nosec B608
                       (current_hr_username,))
        pending_leaves = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM resignation_requests WHERE status='Pending' AND {_assignee_sub}",  # nosec B608
                       (current_hr_username,))
        pending_resignations = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM tickets WHERE status IN ('Open','In Progress') AND {_assignee_sub}",  # nosec B608
                       (current_hr_username,))
        pending_tickets = cursor.fetchone()[0]
    else:
        pending_leaves, pending_resignations, pending_tickets = get_pending_counts()
    cursor.execute("SELECT id, name FROM companies ORDER BY name")
    companies = cursor.fetchall()
    cursor.execute("SELECT id, name FROM onboarding_templates WHERE is_active=1 ORDER BY name")
    onboarding_templates = cursor.fetchall()
    from blueprints.hr_team import hr_choices
    hr_accounts = hr_choices(cursor)  # [(username, "Name (ID)")]

    # Onboarding summary for this page's stats row -- this page is where
    # HR-role sessions land (see blueprints/auth.py's _finish_employee_login
    # and app.py's _is_hr), so it doubles as their dashboard; same query
    # shape as blueprints/admin_views.py's /admin dashboard, scoped to
    # assigned employees only for an HR-role viewer.
    try:
        _ob_filter = "AND employee_id IN (SELECT employee_id FROM employees WHERE assigned_hr_username=%s)" if is_hr_scoped else ""
        _ob_params = (current_hr_username,) if is_hr_scoped else ()
        cursor.execute(f"""
            SELECT
              SUM(CASE WHEN status != 'Completed' THEN 1 ELSE 0 END),
              SUM(CASE WHEN status != 'Completed' AND due_date < CURRENT_DATE THEN 1 ELSE 0 END)
            FROM employee_onboarding
            WHERE 1=1 {_ob_filter}
        """, _ob_params)  # nosec B608 -- _ob_filter is a hardcoded literal chosen by is_hr_scoped, never user input
        _ob = cursor.fetchone()
        ob_active = int(_ob[0] or 0)
        ob_overdue = int(_ob[1] or 0)
    except Exception:
        ob_active = ob_overdue = 0

    cursor.close()
    db.close()

    # ?view=dashboard used to render a lightweight inline stub here --
    # superseded by blueprints/hr_dashboard.py's real /hr_dashboard route
    # (see templates/admin_base.html's sidebar "Dashboard" link, updated to
    # point straight there). Kept as a redirect, not deleted, so any stale
    # bookmark/link to the old ?view=dashboard URL still resolves.
    if request.args.get("view") == "dashboard":
        return redirect(tpath("/hr_dashboard"))

    from blueprints.seats import seat_payment_info
    return render_template("employees.html",
                           seat_pay=seat_payment_info(),
                           ob_active=ob_active,
                           ob_overdue=ob_overdue,
                           is_hr_scoped=is_hr_scoped,
                           hr_accounts=hr_accounts, hr_names=dict(hr_accounts),
                           employees=employees,
                           shifts=shifts,
                           shift_full=shift_full,
                           breaks_list=breaks_list,
                           departments=departments,
                           companies=companies,
                           onboarding_templates=onboarding_templates,
                           total=total,
                           active_count=active_count,
                           on_leave_count=on_leave_count,
                           resigned_count=resigned_count,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           active_nav="employees",
                           email_domain=get_company_settings().get("email_domain"),
                           )


@employees_bp.route("/api/departments", methods=["GET"])
@api_required
def api_departments():
    """API endpoint to fetch real departments and headcounts from database."""
    with _db() as (cursor, conn):
        cursor.execute("""
            SELECT department, COUNT(*) AS count
            FROM employees
            WHERE department IS NOT NULL AND department != '' AND is_active = 1
            GROUP BY department
            ORDER BY count DESC
        """)
        rows = cursor.fetchall()
        depts = []
        for r in rows:
            depts.append({
                "name": r[0],
                "count": r[1]
            })
        return jsonify({"ok": True, "departments": depts})


@employees_bp.route("/employee_detail/<emp_id>")
@admin_required
def employee_detail(emp_id):
    if _hr_scope_denied(emp_id):
        return "Employee not found", 404
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT e.employee_id, e.name, e.role, e.email, e.date_of_joining,
               e.work_mode, e.work_lat, e.work_lon,
               e.face_image, e.qr_code,
               e.department, e.phone, e.gender, e.dob, e.blood_group,
               e.shift_id, e.manager_name,
               e.address, e.city, e.state, e.pincode,
               e.emergency_contact_name, e.emergency_contact_phone, e.emergency_contact_relation,
               e.aadhar_number, e.pan_number,
               e.bank_name, e.bank_account, e.bank_ifsc, e.uan_number,
               s.name AS shift_name,
               COUNT(a.date)  AS total_days,
               MAX(a.date)    AS last_seen,
               SUM(CASE WHEN a.attendance_type IN ('Present','Full Day','Approved Leave')
                        OR (a.login_time IS NOT NULL AND a.attendance_type IS NULL) THEN 1 ELSE 0 END) AS full_days,
               SUM(CASE WHEN a.attendance_type='Half Day' THEN 1 ELSE 0 END) AS half_days,
               SUM(CASE WHEN a.attendance_type LIKE 'Late%%' OR a.status='Late Login' THEN 1 ELSE 0 END) AS late_days,
               sc.salary_per_day AS salary_per_day,
               e.about_me
        FROM employees e
        LEFT JOIN shifts s ON e.shift_id = s.id
        LEFT JOIN attendance a ON e.employee_id = a.employee_id
        LEFT JOIN salary_config sc ON e.employee_id = sc.employee_id
        WHERE e.employee_id = %s
        GROUP BY e.employee_id, e.name, e.role, e.email, e.date_of_joining,
                 e.work_mode, e.work_lat, e.work_lon,
                 e.face_image, e.qr_code,
                 e.department, e.phone, e.gender, e.dob, e.blood_group,
                 e.shift_id, e.manager_name,
                 e.address, e.city, e.state, e.pincode,
                 e.emergency_contact_name, e.emergency_contact_phone, e.emergency_contact_relation,
                 e.aadhar_number, e.pan_number,
                 e.bank_name, e.bank_account, e.bank_ifsc, e.uan_number,
                 s.name, sc.salary_per_day, e.about_me
    """, (emp_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        flash("Employee not found.", "error")
        return redirect(tpath("/employees"))

    # Decrypt PII fields: [12]=gender [14]=blood_group [17]=address [18]=city
    # [19]=state [20]=pincode [21]=ec_name [22]=ec_phone [23]=ec_relation
    # [24]=aadhar_number [25]=pan_number [26]=bank_name [27]=bank_account
    # [28]=bank_ifsc [29]=uan_number. [13]=dob handled separately (see
    # decrypt_pii_date). NOTE: this replaces an existing off-by-one bug that
    # decrypted the wrong indices (23,24,26,27,28) and left pan_number/
    # uan_number displayed as raw ciphertext on this page.
    row = list(row)
    for _pii_idx in (12, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29):
        if _pii_idx < len(row):
            row[_pii_idx] = decrypt_pii(row[_pii_idx])
    row[13] = decrypt_pii_date(row[13])
    row[36] = amount(row[36])            # salary_per_day (encrypted; NULL -> 0)

    # DLP: aadhar/pan/bank_account/uan (indices 24,25,27,29) and salary
    # (index 36) are only shown unmasked to the finance/HR-clearance tier
    # (admin_role=="admin") -- mirrors the restriction payroll.py's
    # view_payslip already enforces for payslips. manager/soc_analyst still
    # get the rest of the record.
    salary_hidden = False
    if not has_pii_clearance():
        row[24] = mask_tail(row[24])
        row[25] = mask_tail(row[25])
        row[27] = mask_tail(row[27])
        row[29] = mask_tail(row[29])
        if row[36]:
            salary_hidden = True
            row[36] = None
    else:
        log_security_event("data.reveal", "Admin viewed unmasked PII on employee detail",
                           level="WARNING", identifier=session.get("admin_username"),
                           resource_type="employee_detail", resource_id=emp_id)

    cursor.execute("SELECT COUNT(*) FROM resignation_requests WHERE employee_id=%s AND status='Accepted'", (emp_id,))
    is_resigned = cursor.fetchone()[0] > 0
    cursor.execute(
        "SELECT COUNT(*) FROM leave_requests WHERE employee_id=%s AND status='Approved' AND leave_date=CURRENT_DATE", (emp_id,))
    is_on_leave = cursor.fetchone()[0] > 0

    if is_resigned:
        emp_status = "Resigned"
    elif is_on_leave:
        emp_status = "On Leave"
    else:
        emp_status = "Active"

    # Recent attendance (last 30 records)
    cursor.execute("""
        SELECT date, login_time, logout_time, attendance_type, status
        FROM attendance WHERE employee_id=%s
        ORDER BY date DESC LIMIT 30
    """, (emp_id,))
    raw_att = cursor.fetchall()

    def _fmt_time(t):
        if t is None:
            return None
        if isinstance(t, datetime.timedelta):
            total = int(t.total_seconds())
            h, rem = divmod(total, 3600)
            m = rem // 60
            suffix = "AM" if h < 12 else "PM"
            h12 = h % 12 or 12
            return f"{h12:02d}:{m:02d} {suffix}"
        if hasattr(t, 'strftime'):
            return t.strftime('%I:%M %p')
        return str(t)

    recent_attendance = [
        (date, _fmt_time(lt), _fmt_time(lot), att_type, status)
        for date, lt, lot, att_type, status in raw_att
    ]

    # Work experience
    cursor.execute("""
        SELECT company, designation, from_year, to_year, is_current, description
        FROM employee_experience WHERE employee_id=%s ORDER BY from_year DESC
    """, (emp_id,))
    experience = cursor.fetchall()

    # Education
    cursor.execute("""
        SELECT degree, institution, year_of_passing, percentage
        FROM employee_education WHERE employee_id=%s ORDER BY year_of_passing DESC
    """, (emp_id,))
    education = cursor.fetchall()

    pending_leaves, pending_resignations, pending_tickets = get_pending_counts()

    # Documents for this employee -- excludes doc_kind IS NOT NULL rows,
    # which are the separate verification-document workflow below (kept
    # out of this general list so the same row never shows twice).
    cursor.execute(
        "SELECT id, doc_type, original_name, uploaded_by, uploaded_at FROM employee_documents "
        "WHERE employee_id=%s AND doc_kind IS NULL ORDER BY uploaded_at DESC",
        (emp_id,)
    )
    emp_docs = cursor.fetchall()

    # Employee verification documents (Company Onboarding -> Trial ->
    # Document Verification -> Subscription Activation, blueprints/
    # documents.py). DISTINCT ON doc_kind -- a rejected document is
    # re-uploaded as a NEW row (no uniqueness constraint, keeps history),
    # so only the latest row per kind reflects current status. Local
    # import (not top-of-file) since blueprints/documents.py doesn't need
    # to know about employees.py at all -- no reason to risk a circular
    # import for two constants only this one view uses.
    from blueprints.documents import _EMPLOYEE_VERIFICATION_DOC_KINDS, _EMPLOYEE_VERIFICATION_DOC_LABELS
    cursor.execute(
        "SELECT DISTINCT ON (doc_kind) id, doc_kind, original_name, verification_status, verified_by, "
        "verified_at, rejection_reason, uploaded_at FROM employee_documents "
        "WHERE employee_id=%s AND doc_kind IS NOT NULL ORDER BY doc_kind, uploaded_at DESC",
        (emp_id,)
    )
    verification_docs = cursor.fetchall()

    cursor.close()
    db.close()
    return render_template("employee_detail.html",
                           emp=row,
                           emp_id=row[0], name=row[1], role=row[2], email=row[3],
                           doj=row[4], work_mode=row[5], work_lat=row[6], work_lon=row[7],
                           face_image=row[8], qr_code=row[9], department=row[10], phone=row[11],
                           gender=row[12], dob=row[13], blood_group=row[14], shift_id=row[15],
                           manager_name=row[16], address=row[17], city=row[18], state=row[19],
                           pincode=row[20], ec_name=row[21], ec_phone=row[22], ec_rel=row[23],
                           aadhar=row[24], pan=row[25], bank_name=row[26], bank_account=row[27],
                           bank_ifsc=row[28], uan=row[29], shift_name=row[30], total_days=row[31],
                           last_seen=row[32], full_days=row[33], half_days=row[34], late_days=row[35],
                           salary_pd=row[36], about_me=row[37],
                           emp_status=emp_status,
                           recent_attendance=recent_attendance,
                           experience=experience,
                           education=education,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           emp_docs=emp_docs,
                           verification_docs=verification_docs,
                           verification_doc_kinds=_EMPLOYEE_VERIFICATION_DOC_KINDS,
                           verification_doc_labels=_EMPLOYEE_VERIFICATION_DOC_LABELS,
                           salary_hidden=salary_hidden,
                           active_nav="employees",
                           )
