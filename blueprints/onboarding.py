# -*- coding: utf-8 -*-
"""Onboarding blueprint -- templates, tasks, offer letters, employee self-service."""
import datetime
import hashlib
import html as _html
import secrets
import uuid
from io import BytesIO
from flask import Blueprint, request, session, redirect, render_template, flash, jsonify, send_file
from database import get_db_connection
from werkzeug.utils import secure_filename
from utils.auth import admin_required, employee_required, api_required, employee_api_required, any_user_required
from utils.helpers import (
    tpath, get_company_settings, _safe_app_url, _db, hr_scope_column, hr_scope_denied, _validate_upload,
    company_today, company_now, _create_notification, int_param,
)
from utils.storage import save_private, open_private
from utils.email_utils import get_email_config, send_email_smtp, send_email_async
from extensions import limiter, app_log, app, log_security_event

onboarding_bp = Blueprint("onboarding", __name__)

# Same allowlist blueprints/documents.py's employee-document uploads use --
# onboarding completion docs are the same class of data (offer letters,
# certificates, ID proofs), just attached to a task instead of a doc_type.
_ONBOARDING_DOC_ALLOWED_EXT = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls', 'xlsx'}


def _save_onboarding_task_document(f, emp_id, task_id):
    """Shared by the web and API twins of the task-completion upload below.

    Used to just be `f.save(os.path.join("static", "onboarding_docs", ...))`
    -- no extension/MIME/magic-byte check, no malware scan, and a
    web-servable destination, unlike every other upload path in this app
    (blueprints/documents.py, employees.py's photos, admin_views.py's
    logos), which all go through _validate_upload()/_validate_image_file()
    and land under private_uploads/ (utils/storage.py's save_private()).
    Brought in line with that: same validation, same private storage, same
    ClamAV scan (fails closed outside development -- see
    utils/helpers.py's _scan_for_malware()).

    Returns (stored_ref_or_None, error_or_None). A returned stored_ref is
    opaque (per save_private()'s own contract) -- never build a URL from it
    directly; see download_onboarding_document() below for the only
    sanctioned way to read one back out.
    """
    ok, err = _validate_upload(f, _ONBOARDING_DOC_ALLOWED_EXT)
    if not ok:
        return None, err
    rel_path = f"onboarding_docs/{emp_id}/{task_id}/{uuid.uuid4()}_{secure_filename(f.filename)}"
    stored_ref, err = save_private(app.root_path, f, rel_path)
    if err:
        return None, err
    return stored_ref, None


@onboarding_bp.route("/onboarding_document/<int:task_id>")
@any_user_required
def download_onboarding_document(task_id):
    """Serves a completed onboarding task's attached document -- the only
    sanctioned way to read one back (templates/my_onboarding.html and
    onboarding_detail.html both used to link straight to
    /static/onboarding_docs/<name>, which no longer exists since uploads
    now land in private storage via _save_onboarding_task_document() above).

    Same admin-vs-employee split every other onboarding route in this file
    uses: an admin-tier session is scoped by hr_scope_denied() (HR limited
    to its assigned employees, admin unrestricted), an employee session may
    only ever reach its own task's document."""
    is_admin = session.get("admin_logged_in")
    emp_session = session.get("employee_id")

    with _db() as (cursor, _conn):
        cursor.execute(
            "SELECT employee_id, document_path, task_title FROM employee_onboarding_tasks WHERE id=%s",
            (task_id,)
        )
        row = cursor.fetchone()
    if not row or not row[1]:
        flash("Document not found.", "danger")
        return redirect(tpath("/employees") if is_admin else tpath("/my_onboarding"))
    doc_emp_id, stored_ref, task_title = row

    if is_admin:
        if hr_scope_denied(doc_emp_id):
            flash("Document not found.", "danger")
            return redirect(tpath("/employees"))
    elif doc_emp_id != emp_session:
        log_security_event(
            "access.denied", "Onboarding document access denied: not the owning employee",
            level="ERROR", identifier=emp_session, requested_owner=doc_emp_id, resource_id=task_id,
        )
        flash("Access denied.", "danger")
        return redirect(tpath("/my_onboarding"))

    try:
        data = open_private(stored_ref)
    except Exception:
        flash("Document file is missing or unreadable.", "danger")
        return redirect(tpath("/employees") if is_admin else tpath("/my_onboarding"))
    # stored_ref's basename is "<uuid>_<original secure_filename>" (see
    # _save_onboarding_task_document above) -- reusing it keeps the real
    # extension intact, unlike building a name from task_title alone.
    import os as _os
    download_name = f"{secure_filename(task_title or 'document')}{_os.path.splitext(stored_ref)[1]}"
    return send_file(BytesIO(data), as_attachment=True, download_name=download_name)


@onboarding_bp.route("/api/onboarding", methods=["GET"])
@api_required
def api_onboarding():
    """API endpoint to fetch real onboarding records from database."""
    with _db() as (cursor, conn):
        cursor.execute("""
            SELECT eo.id, e.employee_id, e.name, COALESCE(e.role,'Role'), COALESCE(e.department,'General'),
                   ot.name AS template_name, eo.assigned_date, eo.due_date, COALESCE(eo.status,'In Progress'),
                   COUNT(eot.id) AS total_tasks,
                   SUM(CASE WHEN eot.status='Done' THEN 1 ELSE 0 END) AS done_tasks
            FROM employee_onboarding eo
            JOIN employees e ON e.employee_id = eo.employee_id
            LEFT JOIN onboarding_templates ot ON ot.id = eo.template_id
            LEFT JOIN employee_onboarding_tasks eot ON eot.onboarding_id = eo.id
            GROUP BY eo.id, e.employee_id, e.name, e.role, e.department, ot.name, eo.assigned_date, eo.due_date, eo.status
            ORDER BY eo.assigned_date DESC
        """)
        rows = cursor.fetchall()
        records = []
        for r in rows:
            done = int(r[10]) if r[10] else 0
            total = int(r[9]) if r[9] else 0
            records.append({
                "id": str(r[0]),
                "employee_id": r[1],
                "employeeName": r[2],
                "role": r[3],
                "department": r[4],
                "template": r[5] or "Standard Onboarding",
                "startDate": str(r[6]) if r[6] else "",
                "dueDate": str(r[7]) if r[7] else "",
                "status": r[8] or "In Progress",
                "tasksCompleted": done,
                "totalTasks": total,
            })
        return jsonify({"ok": True, "onboardings": records})


@onboarding_bp.route("/api/onboarding/<int:ob_id>/tasks", methods=["GET"])
@api_required
def api_onboarding_tasks(ob_id):
    """Bearer-token twin of the task list half of onboarding_detail() below
    -- lets mobile show and update an individual onboarding's checklist,
    which api_onboarding() above (the list view) deliberately only
    summarizes as tasksCompleted/totalTasks counts."""
    with _db() as (cursor, conn):
        cursor.execute("""
            SELECT id, task_title, task_description, requires_document, due_days,
                   status, completed_at, admin_notes, employee_note
            FROM employee_onboarding_tasks WHERE onboarding_id=%s ORDER BY id
        """, (ob_id,))
        rows = cursor.fetchall()
    tasks = [
        {
            "id": r[0], "title": r[1], "description": r[2] or "",
            "requires_document": bool(r[3]), "due_days": r[4],
            "status": r[5] or "Pending",
            "completed_at": str(r[6]) if r[6] else None,
            "admin_notes": r[7] or "", "employee_note": r[8] or "",
        }
        for r in rows
    ]
    return jsonify({"ok": True, "tasks": tasks})


@onboarding_bp.route("/api/onboarding/task/<int:task_id>/update", methods=["POST"])
@api_required
def api_onboarding_task_update(task_id):
    """Bearer-token twin of onboarding_admin_task_update() below -- same
    two effects (set the task's status/notes, and auto-complete the parent
    onboarding once every task is Done) so marking a task done from mobile
    behaves identically to doing it on web. ob_id is looked up from the
    task row itself rather than required from the caller, since it's not
    something the caller needs to already know."""
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip()
    notes = (data.get("admin_notes") or "").strip()
    if new_status not in ("Pending", "Done", "Skipped"):
        return jsonify({"ok": False, "msg": "status must be 'Pending', 'Done', or 'Skipped'."}), 400

    with _db() as (cursor, conn):
        cursor.execute("SELECT onboarding_id FROM employee_onboarding_tasks WHERE id=%s", (task_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"ok": False, "msg": "Task not found."}), 404
        ob_id = row[0]

        completed = company_now().replace(tzinfo=None) if new_status == "Done" else None
        cursor.execute(
            "UPDATE employee_onboarding_tasks SET status=%s, completed_at=%s, admin_notes=%s WHERE id=%s",
            (new_status, completed, notes, task_id)
        )
        cursor.execute(
            "SELECT COUNT(*) FROM employee_onboarding_tasks WHERE onboarding_id=%s AND status!='Done'", (ob_id,)
        )
        remaining = cursor.fetchone()[0]
        onboarding_completed = remaining == 0
        if onboarding_completed:
            cursor.execute("UPDATE employee_onboarding SET status='Completed' WHERE id=%s", (ob_id,))
        conn.commit()

    return jsonify({"ok": True, "onboarding_completed": onboarding_completed})


@onboarding_bp.route("/onboarding")
@admin_required
def onboarding():
    db = get_db_connection()
    cursor = db.cursor()
    active_tab = request.args.get("tab", "active")

    # Active onboardings with progress
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT eo.id, e.employee_id, e.name, e.role, e.department,
               ot.name AS template_name, eo.assigned_date, eo.due_date, eo.status,
               COUNT(eot.id) AS total_tasks,
               SUM(CASE WHEN eot.status='Done' THEN 1 ELSE 0 END) AS done_tasks
        FROM employee_onboarding eo
        JOIN employees e ON e.employee_id = eo.employee_id {_hr}
        JOIN onboarding_templates ot ON ot.id = eo.template_id
        LEFT JOIN employee_onboarding_tasks eot ON eot.onboarding_id = eo.id
        GROUP BY eo.id, e.employee_id, e.name, e.role, e.department,
                 ot.name, eo.assigned_date, eo.due_date, eo.status
        ORDER BY eo.assigned_date DESC
    """, _hr_args)  # nosec B608
    active_onboardings = cursor.fetchall()

    # Templates with task count
    cursor.execute("""
        SELECT ot.id, ot.name, ot.description, ot.is_active,
               COUNT(tt.id) AS task_count, COALESCE(ot.role,'')
        FROM onboarding_templates ot
        LEFT JOIN onboarding_template_tasks tt ON tt.template_id = ot.id
        GROUP BY ot.id
        ORDER BY ot.created_at DESC
    """)
    templates = cursor.fetchall()

    # Employees list for assign dropdown
    _hr_noalias, _hr_noalias_args = hr_scope_column()
    cursor.execute(f"SELECT employee_id, name, role FROM employees WHERE is_active=1 {_hr_noalias} ORDER BY name", _hr_noalias_args)  # nosec B608
    emp_list = cursor.fetchall()

    # Active templates for assign dropdown (include role for JS filtering)
    cursor.execute("SELECT id, name, COALESCE(role,'') FROM onboarding_templates WHERE is_active=1 ORDER BY name")
    active_templates = cursor.fetchall()

    # Distinct employee roles for role filter dropdown
    cursor.execute("SELECT DISTINCT role FROM employees WHERE role IS NOT NULL AND role != '' ORDER BY role")
    employee_roles = [r[0] for r in cursor.fetchall()]

    today = company_today()
    total_active = sum(1 for o in active_onboardings if o[8] != 'Completed')
    total_completed = sum(1 for o in active_onboardings if o[8] == 'Completed')
    total_overdue = sum(1 for o in active_onboardings if o[7] and o[7] < today and o[8] != 'Completed')

    cursor.execute("SELECT COALESCE(default_onboarding_template_id, 0) FROM company_settings LIMIT 1")
    _dtpl = cursor.fetchone()
    default_onboarding_tpl = int(_dtpl[0]) if _dtpl and _dtpl[0] else 0

    co = get_company_settings()
    cursor.close()
    db.close()
    return render_template("onboarding.html",
                           active_onboardings=active_onboardings,
                           templates=templates,
                           emp_list=emp_list,
                           active_templates=active_templates,
                           employee_roles=employee_roles,
                           active_tab=active_tab,
                           co=co,
                           today=today,
                           total_active=total_active,
                           total_completed=total_completed,
                           total_overdue=total_overdue,
                           default_onboarding_tpl=default_onboarding_tpl,
                           pending_leaves=0, pending_resignations=0, pending_tickets=0,
                           active_nav="onboarding",
                           )


@onboarding_bp.route("/onboarding_template_save", methods=["POST"])
@admin_required
def onboarding_template_save():
    tid = request.form.get("template_id")
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip()
    role = request.form.get("role", "").strip() or None
    if not name:
        flash("Template name is required.", "error")
        return redirect(tpath("/onboarding?tab=templates"))
    # Borrowed only after validation, so the early return never holds one (QA CONN-01).
    db = get_db_connection()
    cursor = db.cursor()
    if tid:
        cursor.execute("UPDATE onboarding_templates SET name=%s, description=%s, role=%s WHERE id=%s",
                       (name, desc, role, tid))
        flash("Template updated.", "success")
    else:
        cursor.execute("INSERT INTO onboarding_templates (name, description, role) VALUES (%s,%s,%s)", (name, desc, role))
        flash("Template created.", "success")
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath("/onboarding?tab=templates"))


@onboarding_bp.route("/bulk_assign_onboarding", methods=["POST"])
@admin_required
def bulk_assign_onboarding():
    db = get_db_connection()
    cursor = db.cursor()
    tid = request.form.get("template_id")
    emp_ids = request.form.getlist("employee_ids")
    today = company_today()
    due_date = (today + datetime.timedelta(days=30)).isoformat()
    assigned = 0
    # Fetched once outside the loop -- tid is fixed for the whole bulk
    # action, so re-running these same two SELECTs per employee (as the
    # loop used to) is redundant work repeated N times for no reason.
    cursor.execute(
        "SELECT id, task_title, task_description, requires_document, due_days FROM onboarding_template_tasks "
        "WHERE template_id=%s ORDER BY sort_order, id", (tid,))
    template_tasks = cursor.fetchall()
    cursor.execute("SELECT name FROM onboarding_templates WHERE id=%s", (tid,))
    _tr = cursor.fetchone()
    for emp_id in emp_ids:
        if hr_scope_denied(emp_id):
            continue
        cursor.execute(
            "SELECT id FROM employee_onboarding WHERE employee_id=%s AND template_id=%s AND status='In Progress'",
            (emp_id, tid))
        if cursor.fetchone():
            continue
        cursor.execute(
            "INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date, status) "
            "VALUES (%s,%s,%s,%s,'In Progress') RETURNING id",
            (emp_id, tid, today, due_date))
        ob_id = cursor.fetchone()[0]
        if template_tasks:
            cursor.executemany(
                "INSERT INTO employee_onboarding_tasks (onboarding_id, template_task_id, employee_id, task_title, "
                "task_description, requires_document, due_days, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'Pending')",
                [(ob_id, tt[0], emp_id, tt[1], tt[2], tt[3], tt[4]) for tt in template_tasks]
            )
        assigned += 1
        # Email notification
        try:
            cursor.execute("SELECT name, email FROM employees WHERE employee_id=%s", (emp_id,))
            _er = cursor.fetchone()
            if _er and _er[1] and _tr:
                _ecfg = get_email_config()
                if _ecfg:
                    _html = (f"<p>Hi <strong>{_er[0]}</strong>,</p>"
                             f"<p>A new onboarding checklist <strong>'{_tr[0]}'</strong> has been assigned to you. "
                             f"Please complete all tasks by <strong>{due_date}</strong>.</p>")
                    send_email_async(_er[1], f"New Onboarding Checklist -- {_tr[0]}", _html, _ecfg)
        except Exception as exc:
            app_log.warning("Onboarding-assigned notification email failed for %s: %s", emp_id, exc, exc_info=True)
    db.commit()
    cursor.close()
    db.close()
    flash(f"Onboarding assigned to {assigned} employee(s).", "success")
    return redirect(tpath("/employees"))


@onboarding_bp.route("/export_onboarding_csv")
@admin_required
@limiter.limit("10 per minute")
def export_onboarding_csv():
    import csv
    import io
    db = get_db_connection()
    cursor = db.cursor()
    _hr, _hr_args = hr_scope_column(alias="e")
    cursor.execute(f"""
        SELECT e.employee_id, e.name, e.department, ot.name,
               eo.assigned_date, eo.due_date, eo.status,
               COUNT(eot.id) AS total_tasks,
               SUM(CASE WHEN eot.status='Done' THEN 1 ELSE 0 END) AS done_tasks
        FROM employee_onboarding eo
        JOIN employees e ON eo.employee_id = e.employee_id {_hr}
        JOIN onboarding_templates ot ON eo.template_id = ot.id
        LEFT JOIN employee_onboarding_tasks eot ON eot.onboarding_id = eo.id
        GROUP BY eo.id, e.employee_id, e.name, e.department, ot.name,
                 eo.assigned_date, eo.due_date, eo.status
        ORDER BY eo.assigned_date DESC
    """, _hr_args)  # nosec B608
    rows = cursor.fetchall()
    cursor.close()
    db.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee ID", "Name", "Department", "Template", "Assigned Date",
                    "Due Date", "Status", "Total Tasks", "Done Tasks", "Progress %"])
    for r in rows:
        pct = round(int(r[8] or 0) / int(r[7] or 1) * 100) if r[7] else 0
        writer.writerow([r[0], r[1], r[2] or "", r[3], r[4], r[5], r[6], r[7], r[8] or 0, f"{pct}%"])
    output.seek(0)
    from flask import Response
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment;filename=onboarding_export_{company_today()}.csv"})


@onboarding_bp.route("/onboarding_template_duplicate", methods=["POST"])
@admin_required
def onboarding_template_duplicate():
    db = get_db_connection()
    cursor = db.cursor()
    tid = request.form.get("template_id")
    cursor.execute("SELECT name, description FROM onboarding_templates WHERE id=%s", (tid,))
    tpl = cursor.fetchone()
    if not tpl:
        flash("Template not found.", "error")
        cursor.close()
        db.close()
        return redirect(tpath("/onboarding?tab=templates"))
    cursor.execute(
        "INSERT INTO onboarding_templates (name, description, is_active) VALUES (%s, %s, 1) RETURNING id",
        (f"Copy of {tpl[0]}", tpl[1])
    )
    new_id = cursor.fetchone()[0]
    cursor.execute(
        "SELECT task_title, task_description, requires_document, due_days, sort_order "
        "FROM onboarding_template_tasks WHERE template_id=%s ORDER BY sort_order, id", (tid,)
    )
    for task in cursor.fetchall():
        cursor.execute(
            "INSERT INTO onboarding_template_tasks "
            "(template_id, task_title, task_description, requires_document, due_days, sort_order) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (new_id, task[0], task[1], task[2], task[3], task[4])
        )
    db.commit()
    cursor.close()
    db.close()
    flash(f"Template duplicated as 'Copy of {tpl[0]}'.", "success")
    return redirect(tpath(f"/onboarding_template_detail/{new_id}"))


@onboarding_bp.route("/onboarding_template_delete", methods=["POST"])
@admin_required
def onboarding_template_delete():
    db = get_db_connection()
    cursor = db.cursor()
    tid = request.form.get("template_id")
    cursor.execute("DELETE FROM onboarding_template_tasks WHERE template_id=%s", (tid,))
    cursor.execute("DELETE FROM onboarding_templates WHERE id=%s", (tid,))
    db.commit()
    cursor.close()
    db.close()
    flash("Template deleted.", "success")
    return redirect(tpath("/onboarding?tab=templates"))


@onboarding_bp.route("/onboarding_task_save", methods=["POST"])
@admin_required
def onboarding_task_save():
    task_id = request.form.get("task_id")
    tid = request.form.get("template_id")
    title = request.form.get("task_title", "").strip()
    desc = request.form.get("task_description", "").strip()
    req_doc = 1 if request.form.get("requires_document") else 0
    due_days = int_param(request.form.get("due_days", 7))
    sort_order = int_param(request.form.get("sort_order", 0))
    if not title:
        flash("Task title is required.", "error")
        return redirect(tpath(f"/onboarding_template_detail/{tid}"))
    # Borrowed only after validation, so the early return never holds one (QA CONN-01).
    db = get_db_connection()
    cursor = db.cursor()
    if task_id:
        cursor.execute("""UPDATE onboarding_template_tasks
                          SET task_title=%s, task_description=%s, requires_document=%s,
                              due_days=%s, sort_order=%s
                          WHERE id=%s""", (title, desc, req_doc, due_days, sort_order, task_id))
        flash("Task updated.", "success")
    else:
        cursor.execute("""INSERT INTO onboarding_template_tasks
                          (template_id, task_title, task_description, requires_document, due_days, sort_order)
                          VALUES (%s,%s,%s,%s,%s,%s)""", (tid, title, desc, req_doc, due_days, sort_order))
        flash("Task added.", "success")
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath(f"/onboarding_template_detail/{tid}"))


@onboarding_bp.route("/onboarding_task_delete", methods=["POST"])
@admin_required
def onboarding_task_delete():
    db = get_db_connection()
    cursor = db.cursor()
    task_id = request.form.get("task_id")
    cursor.execute("SELECT template_id FROM onboarding_template_tasks WHERE id=%s", (task_id,))
    row = cursor.fetchone()
    tid = row[0] if row else None
    cursor.execute("DELETE FROM onboarding_template_tasks WHERE id=%s", (task_id,))
    db.commit()
    cursor.close()
    db.close()
    flash("Task deleted.", "success")
    return redirect(tpath(f"/onboarding_template_detail/{tid}"))


@onboarding_bp.route("/onboarding_template_detail/<int:tid>")
@admin_required
def onboarding_template_detail(tid):
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("SELECT id, name, description, is_active FROM onboarding_templates WHERE id=%s", (tid,))
    template = cursor.fetchone()
    cursor.execute("""SELECT id, task_title, task_description, requires_document, due_days, sort_order
                      FROM onboarding_template_tasks WHERE template_id=%s ORDER BY sort_order, id""", (tid,))
    tasks = cursor.fetchall()
    co = get_company_settings()
    cursor.close()
    db.close()
    return render_template("onboarding_template_detail.html",
                           template=template, tasks=tasks, co=co,
                           pending_leaves=0, pending_resignations=0, pending_tickets=0,
                           active_nav="onboarding",
                           )


@onboarding_bp.route("/onboarding_assign", methods=["POST"])
@admin_required
def onboarding_assign():
    db = get_db_connection()
    cursor = db.cursor()
    emp_id = request.form.get("employee_id")
    tid = request.form.get("template_id")
    due_date = request.form.get("due_date") or None
    today = company_today()

    if hr_scope_denied(emp_id):
        cursor.close()
        db.close()
        flash("Employee not found.", "error")
        return redirect(tpath("/onboarding?tab=active"))

    # Check not already assigned same template
    cursor.execute("SELECT id FROM employee_onboarding WHERE employee_id=%s AND template_id=%s AND status='In Progress'",
                   (emp_id, tid))
    if cursor.fetchone():
        flash("This employee already has this onboarding in progress.", "error")
        cursor.close()
        db.close()
        return redirect(tpath("/onboarding?tab=active"))

    cursor.execute("INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date) VALUES (%s,%s,%s,%s) RETURNING id",
                   (emp_id, tid, today, due_date))
    ob_id = cursor.fetchone()[0]

    # Copy tasks from template
    cursor.execute("""SELECT id, task_title, task_description, requires_document, due_days
                      FROM onboarding_template_tasks WHERE template_id=%s ORDER BY sort_order, id""", (tid,))
    for task in cursor.fetchall():
        cursor.execute("""INSERT INTO employee_onboarding_tasks
                          (onboarding_id, template_task_id, employee_id, task_title, task_description, requires_document, due_days)
                          VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                       (ob_id, task[0], emp_id, task[1], task[2], task[3], task[4]))
    db.commit()

    # Notification to employee
    try:
        cursor.execute("SELECT name FROM onboarding_templates WHERE id=%s", (tid,))
        tname = cursor.fetchone()[0]
        # The app's notifications table (an employee_notifications table was
        # targeted here that doesn't exist, so this always failed -- QA BUG-37).
        _create_notification('employee', 'Onboarding Started',
                             f"Your onboarding checklist '{tname}' has been assigned. Please complete all tasks.",
                             emp_id)
    except Exception as exc:
        app_log.warning("In-app onboarding-started notification failed for %s: %s", emp_id, exc, exc_info=True)

    cursor.execute("SELECT name, email FROM employees WHERE employee_id=%s", (emp_id,))
    _er = cursor.fetchone()
    emp_name = _er[0]
    emp_email = _er[1] if _er else None
    # Email employee about new onboarding assignment
    if emp_email:
        _ecfg = get_email_config()
        if _ecfg:
            try:
                _safe_name = _html.escape(emp_name or emp_id)
                _safe_tname = _html.escape(tname or "")
                _ob_html = (f"<p>Hi <strong>{_safe_name}</strong>,</p>"
                            f"<p>A new onboarding checklist <strong>'{_safe_tname}'</strong> has been assigned to you.</p>"
                            f"<p>Due date: <strong>{due_date or 'Not set'}</strong></p>"
                            f"<p>Please log in to your employee portal and complete all tasks on time.</p>")
                send_email_async(emp_email, f"New Onboarding Checklist Assigned -- {tname}", _ob_html, _ecfg)
            except Exception as exc:
                app_log.warning("Onboarding-assigned email failed for %s: %s", emp_id, exc, exc_info=True)
    cursor.close()
    db.close()
    flash(f"Onboarding assigned to {emp_name}.", "success")
    return redirect(tpath("/onboarding?tab=active"))


@onboarding_bp.route("/onboarding_detail/<int:ob_id>")
@admin_required
def onboarding_detail(ob_id):
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
        SELECT eo.id, e.employee_id, e.name, e.role, e.department,
               ot.name AS tname, eo.assigned_date, eo.due_date, eo.status
        FROM employee_onboarding eo
        JOIN employees e ON e.employee_id=eo.employee_id
        JOIN onboarding_templates ot ON ot.id=eo.template_id
        WHERE eo.id=%s
    """, (ob_id,))
    ob = cursor.fetchone()
    if not ob or hr_scope_denied(ob[1]):
        cursor.close()
        db.close()
        return "Onboarding record not found", 404
    cursor.execute("""
        SELECT id, task_title, task_description, requires_document, due_days,
               status, completed_at, document_path, admin_notes, employee_note
        FROM employee_onboarding_tasks WHERE onboarding_id=%s ORDER BY id
    """, (ob_id,))
    tasks = cursor.fetchall()
    co = get_company_settings()
    cursor.close()
    db.close()
    return render_template("onboarding_detail.html",
                           ob=ob, tasks=tasks, co=co,
                           today=company_today(),
                           pending_leaves=0, pending_resignations=0, pending_tickets=0,
                           active_nav="onboarding",
                           )


@onboarding_bp.route("/onboarding_admin_task_update", methods=["POST"])
@admin_required
def onboarding_admin_task_update():
    db = get_db_connection()
    cursor = db.cursor()
    task_id = request.form.get("task_id")
    new_status = request.form.get("status")
    notes = request.form.get("admin_notes", "")
    ob_id = request.form.get("ob_id")
    cursor.execute("SELECT employee_id FROM employee_onboarding WHERE id=%s", (ob_id,))
    _ob_owner = cursor.fetchone()
    if not _ob_owner or hr_scope_denied(_ob_owner[0]):
        cursor.close()
        db.close()
        return "Onboarding record not found", 404
    completed = company_now().replace(tzinfo=None) if new_status == "Done" else None
    cursor.execute("""UPDATE employee_onboarding_tasks
                      SET status=%s, completed_at=%s, admin_notes=%s WHERE id=%s""",
                   (new_status, completed, notes, task_id))
    # Auto-complete onboarding if all tasks done
    cursor.execute("SELECT COUNT(*) FROM employee_onboarding_tasks WHERE onboarding_id=%s AND status!='Done'", (ob_id,))
    remaining = cursor.fetchone()[0]
    if remaining == 0:
        cursor.execute("UPDATE employee_onboarding SET status='Completed' WHERE id=%s", (ob_id,))
    db.commit()
    cursor.close()
    db.close()
    flash("Task updated.", "success")
    return redirect(tpath(f"/onboarding_detail/{ob_id}"))


@onboarding_bp.route("/onboarding_close", methods=["POST"])
@admin_required
def onboarding_close():
    db = get_db_connection()
    cursor = db.cursor()
    ob_id = request.form.get("ob_id")
    cursor.execute("SELECT employee_id FROM employee_onboarding WHERE id=%s", (ob_id,))
    _ob_owner = cursor.fetchone()
    if not _ob_owner or hr_scope_denied(_ob_owner[0]):
        cursor.close()
        db.close()
        return "Onboarding record not found", 404
    cursor.execute("UPDATE employee_onboarding SET status='Completed' WHERE id=%s", (ob_id,))
    db.commit()
    cursor.close()
    db.close()
    flash("Onboarding marked as completed.", "success")
    return redirect(tpath("/onboarding?tab=active"))


@onboarding_bp.route("/offer_letter/<int:ob_id>")
@admin_required
def offer_letter(ob_id):
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
        SELECT eo.id, e.employee_id, e.name, e.role, e.department, e.email,
               eo.assigned_date, e.date_of_joining
        FROM employee_onboarding eo
        JOIN employees e ON e.employee_id = eo.employee_id
        WHERE eo.id = %s
    """, (ob_id,))
    ob = cursor.fetchone()
    if not ob:
        cursor.close()
        db.close()
        flash("Onboarding record not found.", "danger")
        return redirect(tpath("/onboarding"))
    cursor.execute(
        "SELECT COALESCE(monthly_ctc,0), COALESCE(salary_per_day,0) FROM salary_config WHERE employee_id=%s", (ob[1],))
    sal = cursor.fetchone() or (0, 0)
    monthly_ctc = float(sal[0]) or round(float(sal[1]) * 26, 2)
    cursor.execute("SELECT * FROM offer_letters WHERE onboarding_id=%s ORDER BY id DESC LIMIT 1", (ob_id,))
    existing = cursor.fetchone()
    co = get_company_settings()
    cursor.close()
    db.close()
    return render_template("offer_letter.html", ob=ob, monthly_ctc=monthly_ctc,
                           existing=existing, co=co,
                           pending_leaves=0, pending_resignations=0, pending_tickets=0,
                           active_nav="onboarding",
                           )


@onboarding_bp.route("/offer_letter_save", methods=["POST"])
@admin_required
def offer_letter_save():
    ob_id = request.form.get("ob_id")
    employee_id = request.form.get("employee_id")
    designation = request.form.get("designation", "")
    department = request.form.get("department", "")
    work_location = request.form.get("work_location", "")
    monthly_ctc = request.form.get("monthly_ctc", 0) or 0
    joining_date = request.form.get("joining_date") or None
    valid_until = request.form.get("offer_valid_until") or None
    probation = int_param(request.form.get("probation_months", 6))
    reporting_to = request.form.get("reporting_to", "")
    notes = request.form.get("additional_notes", "")
    notice_days = int_param(request.form.get("notice_period_days", 30))
    candidate_addr = request.form.get("candidate_address", "")
    db = get_db_connection()
    cursor = db.cursor()
    # notice_period_days / candidate_address are created by the boot-time
    # migrations (migrations/column_migrations.py) -- no DDL in a request.
    cursor.execute("SELECT id FROM offer_letters WHERE onboarding_id=%s", (ob_id,))
    existing = cursor.fetchone()
    if existing:
        cursor.execute("""UPDATE offer_letters SET designation=%s,department=%s,work_location=%s,
            monthly_ctc=%s,joining_date=%s,offer_valid_until=%s,probation_months=%s,
            reporting_to=%s,additional_notes=%s,notice_period_days=%s,candidate_address=%s,
            generated_at=NOW(),status='draft',sent_at=NULL
            WHERE id=%s""",
                       (designation, department, work_location, monthly_ctc, joining_date, valid_until,
                        probation, reporting_to, notes, notice_days, candidate_addr, existing[0]))
        letter_id = existing[0]
    else:
        cursor.execute("""INSERT INTO offer_letters (onboarding_id,employee_id,designation,department,
            work_location,monthly_ctc,joining_date,offer_valid_until,probation_months,
            reporting_to,additional_notes,notice_period_days,candidate_address)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (ob_id, employee_id, designation, department, work_location, monthly_ctc,
                        joining_date, valid_until, probation, reporting_to, notes, notice_days, candidate_addr))
        letter_id = cursor.fetchone()[0]
    db.commit()
    cursor.close()
    db.close()
    return redirect(tpath(f"/offer_letter_view/{letter_id}"))


@onboarding_bp.route("/offer_letter_view/<int:letter_id>")
@admin_required
def offer_letter_view(letter_id):
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
        SELECT ol.id,ol.onboarding_id,ol.employee_id,ol.designation,ol.department,
               ol.work_location,ol.monthly_ctc,ol.joining_date,ol.offer_valid_until,
               ol.probation_months,ol.reporting_to,ol.additional_notes,ol.generated_at,
               ol.sent_at,ol.status,
               COALESCE(ol.notice_period_days,30),COALESCE(ol.candidate_address,''),
               e.name, e.email
        FROM offer_letters ol
        JOIN employees e ON e.employee_id = ol.employee_id
        WHERE ol.id = %s
    """, (letter_id,))
    letter = cursor.fetchone()
    co = get_company_settings()
    cursor.close()
    db.close()
    if not letter:
        flash("Offer letter not found.", "error")
        return redirect(tpath("/onboarding"))
    return render_template("offer_letter_view.html", letter=letter, co=co)


def _generate_offer_letter_pdf(letter, co):
    """Build offer letter PDF with ReportLab and return bytes."""
    from io import BytesIO
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table,
                                    TableStyle, Spacer, HRFlowable)
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT

    BLUE = rl_colors.HexColor("#1d4ed8")
    DARK = rl_colors.HexColor("#111827")
    GRAY = rl_colors.HexColor("#6b7280")
    LIGHT = rl_colors.HexColor("#f3f4f6")

    emp_name = letter[17]
    designation = letter[3] or "the offered position"
    department = letter[4] or ""
    work_location = letter[5] or ""
    monthly_ctc = float(letter[6]) if letter[6] else 0
    joining_date = letter[7].strftime("%d %B %Y") if letter[7] else "--"
    valid_until = letter[8].strftime("%d %B %Y") if letter[8] else "7 days from date of issue"
    probation = letter[9] or 6
    reporting_to = letter[10] or "the Department Head"
    notes = letter[11] or ""
    gen_date = letter[12].strftime("%d %B %Y") if letter[12] else ""
    notice_days = letter[15] or 30
    ref_num = f"OL/{letter[2].upper()}/{letter[12].strftime('%Y') if letter[12] else ''}/{letter[0]:04d}"
    company = co.get("company_name", "Company")
    co_address = co.get("address", "")
    co_email_val = co.get("email", "")

    def ps(name, **kw):
        base = dict(fontName="Helvetica", fontSize=10, leading=14, textColor=DARK)
        base.update(kw)
        return ParagraphStyle(name, **base)

    sNormal = ps("normal")
    sSmall = ps("small", fontSize=8, textColor=GRAY)
    sLabel = ps("label", fontSize=8, fontName="Helvetica-Bold", textColor=BLUE, spaceAfter=4)
    sCenter = ps("center", alignment=TA_CENTER)
    sRight = ps("right", alignment=TA_RIGHT)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm)
    story = []

    # ── Blue top rule ──────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=4, color=BLUE, spaceAfter=8))

    # ── Letterhead row ─────────────────────────────────────────────────────
    addr_line = co_address
    if co_email_val:
        addr_line += f"  ·  {co_email_val}" if addr_line else co_email_val
    lh_data = [[
        [Paragraph(f"<b>{company}</b>", ps("co", fontSize=14, fontName="Helvetica-Bold")),
         Paragraph(addr_line, sSmall)],
        [Paragraph(f"<b>Date:</b> {gen_date}", sRight),
         Paragraph(f"<b>Ref:</b> {ref_num}", sRight)],
    ]]
    lh_tbl = Table(lh_data, colWidths=["55%", "45%"])
    lh_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(lh_tbl)
    story.append(HRFlowable(width="100%", thickness=1, color=rl_colors.HexColor("#e5e7eb"), spaceAfter=10))

    # ── To block ───────────────────────────────────────────────────────────
    story.append(Paragraph("<b>To,</b>", sNormal))
    story.append(Paragraph(emp_name, sNormal))
    story.append(Paragraph(f"Employee ID: {letter[2]}", sNormal))
    story.append(Spacer(1, 8))

    # ── Subject ────────────────────────────────────────────────────────────
    story.append(Paragraph(f"<u><b>Sub: Offer of Employment -- {designation}</b></u>", sNormal))
    story.append(Spacer(1, 10))

    # ── Salutation ─────────────────────────────────────────────────────────
    story.append(Paragraph(f"Dear <b>{emp_name}</b>,", sNormal))
    story.append(Spacer(1, 8))

    # ── Opening paragraphs ─────────────────────────────────────────────────
    dept_txt = f" in the <b>{department}</b> department" if department else ""
    loc_txt = f", located at <b>{work_location}</b>" if work_location else ""
    story.append(Paragraph(
        f"We are pleased to offer you the position of <b>{designation}</b>{dept_txt} "
        f"at <b>{company}</b>{loc_txt}. You will be reporting to <b>{reporting_to}</b>.",
        sNormal))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Your date of joining will be <b>{joining_date}</b>. Please report to the HR Department "
        f"on the joining date with your original documents for verification.",
        sNormal))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"At <b>{company}</b>, we believe in fostering a collaborative, growth-oriented environment "
        f"where every team member is empowered to make an impact. As a <b>{designation}</b>, "
        f"you will play a key role in driving our mission forward. We look forward to the "
        f"valuable perspective and expertise you will bring to the team.",
        sNormal))
    story.append(Spacer(1, 12))

    # ── Compensation ───────────────────────────────────────────────────────
    if monthly_ctc > 0:
        story.append(Paragraph("COMPENSATION DETAILS", sLabel))
        basic = round(monthly_ctc * 0.40, 2)
        hra = round(monthly_ctc * 0.20, 2)
        sa = round(monthly_ctc * 0.33, 2)
        pf = round(monthly_ctc * 0.04, 2)
        gr = round(monthly_ctc * 0.03, 2)
        def fmt(n): return f"₹{n:,.2f}"
        ctc_data = [
            ["Salary Component", "Monthly", "Annual"],
            ["Basic Salary", fmt(basic), fmt(basic * 12)],
            ["House Rent Allowance", fmt(hra), fmt(hra * 12)],
            ["Special Allowance", fmt(sa), fmt(sa * 12)],
            ["PF -- Employer (12%)", fmt(pf), fmt(pf * 12)],
            ["Gratuity (4.81%)", fmt(gr), fmt(gr * 12)],
            ["GROSS CTC", fmt(monthly_ctc), fmt(monthly_ctc * 12)],
        ]
        ctc_tbl = Table(ctc_data, colWidths=["50%", "25%", "25%"])
        ctc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("BACKGROUND", (0, -1), (-1, -1), DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), GRAY),
            ("TEXTCOLOR", (0, -1), (-1, -1), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [rl_colors.white, rl_colors.HexColor("#f9fafb")]),
            ("GRID", (0, 0), (-1, -2), 0.3, rl_colors.HexColor("#e5e7eb")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(ctc_tbl)
        story.append(Spacer(1, 10))

    # ── Notes ──────────────────────────────────────────────────────────────
    if notes:
        note_tbl = Table([[Paragraph(f"<b>Note:</b> {notes}", ps("note", fontSize=9, textColor=rl_colors.HexColor("#1e40af")))]],
                         colWidths=["100%"])
        note_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), rl_colors.HexColor("#eff6ff")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(note_tbl)
        story.append(Spacer(1, 10))

    # ── Terms & Conditions ─────────────────────────────────────────────────
    story.append(Paragraph("TERMS &amp; CONDITIONS", sLabel))
    tc_items = [
        "This offer is subject to satisfactory verification of your educational qualifications, credentials, and prior employment history.",
        f"You will serve a probationary period of <b>{probation} months</b> from the date of joining. Confirmation is subject to satisfactory performance.",
        f"Post-confirmation, either party may terminate employment by providing <b>{notice_days} days'</b> written "
        "notice or salary in lieu thereof. During probation, 7 days' notice applies.",
        "All compensation is subject to applicable statutory deductions (TDS, PF, ESI, Professional Tax) as per prevailing Indian law.",
        f"This offer is valid until <b>{valid_until}</b>. Non-acceptance by this date shall render this offer null and void.",
        "You shall maintain strict confidentiality of all proprietary and sensitive information of the Company during and after your employment.",
        "You will abide by the Company's HR policies, Code of Conduct, and all applicable rules as amended from time to time.",
        "A formal Appointment Letter will be issued upon joining. This offer letter does not constitute a contract of employment.",
    ]
    tc_data = [
        [Paragraph(f"{i+1}.&nbsp;&nbsp;{item}", ps(f"tc{i}", fontSize=9,
                   leading=13, textColor=rl_colors.HexColor("#4b5563")))]
        for i, item in enumerate(tc_items)
    ]
    tc_tbl = Table(tc_data, colWidths=["100%"])
    tc_tbl.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tc_tbl)
    story.append(Spacer(1, 12))

    story.append(Paragraph(
        f"We look forward to welcoming you to <b>{company}</b>. "
        "Please sign and return one copy of this letter to confirm your acceptance.",
        sNormal))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Warm regards,", sNormal))
    story.append(Spacer(1, 24))

    # ── Signature row ──────────────────────────────────────────────────────
    sig_data = [[
        [HRFlowable(width="80%", thickness=1, color=DARK),
         Paragraph("<b>Authorised Signatory</b>", ps("sig", fontSize=9)),
         Paragraph(company, ps("sigt", fontSize=8, textColor=GRAY)),
         Paragraph("Human Resources", ps("sigt2", fontSize=8, textColor=GRAY))],
        [Paragraph("I hereby accept this offer and agree to all terms stated above.", ps("accnote", fontSize=8, textColor=GRAY)),
         HRFlowable(width="80%", thickness=1, color=DARK),
         Paragraph(f"<b>{emp_name}</b>", ps("csig", fontSize=9)),
         Paragraph("Candidate Signature", ps("csigt", fontSize=8, textColor=GRAY)),
         Paragraph("Date: _______________", ps("cdate", fontSize=8, textColor=GRAY))],
    ]]
    sig_tbl = Table(sig_data, colWidths=["48%", "52%"])
    sig_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    story.append(sig_tbl)

    # ── Footer rule ────────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=rl_colors.HexColor("#e5e7eb")))
    foot_txt = company
    if co_address:
        foot_txt += f"  ·  {co_address}"
    story.append(Paragraph(
        f'<font size="8" color="#9ca3af">{foot_txt}&nbsp;&nbsp;&nbsp;Confidential -- For addressee only</font>', sCenter))
    story.append(HRFlowable(width="100%", thickness=4, color=DARK, spaceBefore=6))

    doc.build(story)
    return buf.getvalue()


@onboarding_bp.route("/offer_letter_send/<int:letter_id>", methods=["POST"])
@admin_required
def offer_letter_send(letter_id):
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
        SELECT ol.id,ol.onboarding_id,ol.employee_id,ol.designation,ol.department,
               ol.work_location,ol.monthly_ctc,ol.joining_date,ol.offer_valid_until,
               ol.probation_months,ol.reporting_to,ol.additional_notes,ol.generated_at,
               ol.sent_at,ol.status,
               COALESCE(ol.notice_period_days,30),COALESCE(ol.candidate_address,''),
               e.name, e.email
        FROM offer_letters ol
        JOIN employees e ON e.employee_id = ol.employee_id
        WHERE ol.id = %s
    """, (letter_id,))
    letter = cursor.fetchone()
    co = get_company_settings()
    if not letter or not letter[18]:
        flash("Employee email not found.", "error")
        cursor.close()
        db.close()
        return redirect(tpath(f"/offer_letter_view/{letter_id}"))
    cfg = get_email_config()
    if not cfg:
        flash("Email not configured. Go to Settings → Email.", "error")
        cursor.close()
        db.close()
        return redirect(tpath(f"/offer_letter_view/{letter_id}"))
    try:
        emp_name = letter[17]
        emp_email = letter[18]
        designation = letter[3] or "the offered position"
        department = letter[4] or ""
        work_location = letter[5] or ""
        monthly_ctc = float(letter[6]) if letter[6] else 0
        joining_date = letter[7].strftime("%d %B %Y") if letter[7] else "--"
        valid_until = letter[8].strftime("%d %B %Y") if letter[8] else "7 days from date of issue"
        probation = letter[9] or 6
        reporting_to = letter[10] or "the Department Head"
        notes = letter[11] or ""
        gen_date = letter[12].strftime("%d %B %Y") if letter[12] else ""
        notice_days = letter[15] or 30
        ref_num = f"OL/{letter[2].upper()}/{letter[12].strftime('%Y') if letter[12] else ''}/{letter[0]:04d}"
        company = co.get("company_name", "Company")
        co_address = co.get("address", "")
        co_email = co.get("email", "")

        # Secure one-time token (shared by accept/reject AND pdf view)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        base_url = _safe_app_url()
        accept_url = f"{base_url}/offer_letter_respond/{token}/accept"
        reject_url = f"{base_url}/offer_letter_respond/{token}/reject"
        pdf_view_url = f"{base_url}/offer_letter_pdf/{token}"
        pdf_dl_url = f"{base_url}/offer_letter_pdf/{token}?dl=1"

        # ── Salary breakdown helper ────────────────────────────────────────
        def fmt(n): return f"{n:,.2f}"
        ctc_section = ""
        if monthly_ctc > 0:
            basic = round(monthly_ctc * 0.40, 2)
            hra = round(monthly_ctc * 0.20, 2)
            sa = round(monthly_ctc * 0.33, 2)
            pf = round(monthly_ctc * 0.04, 2)
            gr = round(monthly_ctc * 0.03, 2)
            _th_style = (
                "background:#f3f4f6;color:#6b7280;font-size:10px;font-weight:700;"
                "text-transform:uppercase;padding:9px 12px;border-bottom:1px solid #e5e7eb;"
            )
            _td_style = "padding:9px 12px;border-bottom:1px solid #f3f4f6;"
            _td_num_style = _td_style + "text-align:right;font-weight:600;"

            def _ctc_row(label, monthly, annual, border=True):
                td = _td_style if border else "padding:9px 12px;"
                td_num = _td_num_style if border else "padding:9px 12px;text-align:right;font-weight:600;"
                return (
                    f'<tr><td style="{td}">{label}</td>'
                    f'<td style="{td_num}">{fmt(monthly)}</td>'
                    f'<td style="{td_num}">{fmt(annual)}</td></tr>'
                )

            ctc_section = f"""
            <p style="font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#1d4ed8;margin:20px 0 8px;">Compensation Details</p>
            <table style="width:100%;border-collapse:collapse;font-size:12.5px;margin-bottom:20px;">
              <thead><tr>
                <th style="{_th_style}text-align:left;">Salary Component</th>
                <th style="{_th_style}text-align:right;">Monthly (&#8377;)</th>
                <th style="{_th_style}text-align:right;">Annual (&#8377;)</th>
              </tr></thead>
              <tbody>
                {_ctc_row("Basic Salary", basic, basic * 12)}
                {_ctc_row("House Rent Allowance (HRA)", hra, hra * 12)}
                {_ctc_row("Special Allowance", sa, sa * 12)}
                {_ctc_row("PF -- Employer Contribution", pf, pf * 12)}
                {_ctc_row("Gratuity (4.81% of Basic)", gr, gr * 12, border=False)}
              </tbody>
              <tfoot><tr>
                <td style="padding:10px 12px;font-weight:800;background:#111827;color:#fff;">Gross CTC</td>
                <td style="padding:10px 12px;text-align:right;font-weight:800;background:#111827;color:#fff;">
                  &#8377;{fmt(monthly_ctc)}</td>
                <td style="padding:10px 12px;text-align:right;font-weight:800;background:#111827;color:#fff;">
                  &#8377;{fmt(monthly_ctc*12)}</td>
              </tr></tfoot>
            </table>"""

        notes_section = ""
        if notes:
            notes_section = f"""<div style="background:#eff6ff;border-left:3px solid #1d4ed8;padding:11px 16px;
                 font-size:12.5px;color:#1e40af;border-radius:0 6px 6px 0;margin-bottom:16px;line-height:1.7;">
              <strong>Note:</strong> {notes}</div>"""

        dept_html = f' in the <strong>{department}</strong> department' if department else ''
        loc_html = f', located at <strong>{work_location}</strong>' if work_location else ''

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<style>
  @keyframes burst {{
    0%   {{ transform:translate(var(--tx,0),0) rotate(0deg) scale(1); opacity:1; }}
    100% {{ transform:translate(var(--tx,0),var(--ty,-70px)) rotate(var(--rot,360deg)) scale(0); opacity:0; }}
  }}
  .cw {{ position:relative; display:inline-block; cursor:default; }}
  .cw:hover .cp {{ animation:burst .75s ease-out forwards; }}
  .cp {{ position:absolute; width:7px; height:7px; border-radius:2px;
         top:0; left:50%; opacity:0; pointer-events:none; }}
</style>
</head>
<body style="margin:0;padding:0;background:#e5e7eb;font-family:'Segoe UI',Arial,sans-serif;">
<div style="max-width:680px;margin:32px auto;background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.13);">

  <!-- Top accent -->
  <div style="height:4px;background:#1d4ed8;"></div>

  <!-- Hero banner -->
  <div style="background:linear-gradient(135deg,#1e3a8a 0%,#2563eb 100%);padding:40px 48px 32px;text-align:center;">
    <div class="cw">
      <span style="font-size:28px;font-weight:800;color:#fff;letter-spacing:-.5px;">
        &#127881; Congratulations, {emp_name}!
      </span>
      <!-- confetti pieces -->
      <span class="cp" style="background:#ff6b6b;--tx:-38px;--ty:-75px;--rot:240deg;animation-delay:.00s;"></span>
      <span class="cp" style="background:#ffd93d;--tx:-18px;--ty:-82px;--rot:180deg;animation-delay:.05s;"></span>
      <span class="cp" style="background:#6bcb77;--tx:  5px;--ty:-78px;--rot:300deg;animation-delay:.10s;"></span>
      <span class="cp" style="background:#4d96ff;--tx: 24px;--ty:-70px;--rot:120deg;animation-delay:.05s;"></span>
      <span class="cp" style="background:#ff6b6b;--tx: 42px;--ty:-80px;--rot: 60deg;animation-delay:.00s;"></span>
      <span class="cp" style="background:#c77dff;--tx:-50px;--ty:-60px;--rot:200deg;animation-delay:.12s;"></span>
      <span class="cp" style="background:#ffd93d;--tx: 55px;--ty:-65px;--rot:160deg;animation-delay:.08s;"></span>
      <span class="cp" style="background:#6bcb77;--tx:-25px;--ty:-90px;--rot:280deg;animation-delay:.03s;"></span>
      <span class="cp" style="background:#4d96ff;--tx: 30px;--ty:-88px;--rot:330deg;animation-delay:.15s;"></span>
      <span class="cp" style="background:#ff9f1c;--tx:  0px;--ty:-95px;--rot:  0deg;animation-delay:.07s;"></span>
    </div>
    <p style="color:#bfdbfe;font-size:14px;margin-top:10px;margin-bottom:0;">
      We are thrilled to welcome you to the <strong style="color:#fff;">{company}</strong> family!
    </p>
  </div>

  <!-- Letterhead meta -->
  <div style="padding:20px 48px 0;display:table;width:100%;box-sizing:border-box;">
    <div style="display:table-cell;vertical-align:top;">
      <div style="font-size:16px;font-weight:800;color:#111827;">{company}</div>
      <div style="font-size:11px;color:#9ca3af;margin-top:3px;">{co_address}{(' &nbsp;·&nbsp; ' + co_email) if co_email else ''}</div>
    </div>
    <div style="display:table-cell;vertical-align:top;text-align:right;font-size:11px;color:#6b7280;line-height:1.8;">
      <div><strong style="color:#111827;">Date:</strong> {gen_date}</div>
      <div><strong style="color:#111827;">Ref:</strong> {ref_num}</div>
    </div>
  </div>
  <hr style="border:none;border-top:1.5px solid #e5e7eb;margin:14px 48px 0;"/>

  <!-- Address + Subject -->
  <div style="padding:18px 48px 0;font-size:13px;color:#374151;line-height:1.8;">
    <div style="font-weight:700;">To,</div>
    <div>{emp_name}</div>
    <div>Employee ID: {letter[2]}</div>
  </div>
  <div style="padding:12px 48px 0;font-size:13px;font-weight:700;color:#111827;text-decoration:underline;">
    Sub: Offer of Employment -- {designation}
  </div>

  <!-- Letter body -->
  <div style="padding:18px 48px 32px;">

    <p style="font-size:13px;color:#374151;line-height:1.9;margin-bottom:14px;">
      Dear <strong>{emp_name}</strong>,
    </p>

    <p style="font-size:13px;color:#374151;line-height:1.9;margin-bottom:14px;">
      We are pleased to offer you the position of <strong>{designation}</strong>{dept_html}
      at <strong>{company}</strong>{loc_html}.
      You will be reporting to <strong>{reporting_to}</strong>.
    </p>

    <p style="font-size:13px;color:#374151;line-height:1.9;margin-bottom:14px;">
      Your date of joining will be <strong>{joining_date}</strong>. Please report to the HR Department
      on the joining date with your original documents for verification.
    </p>

    <!-- About the role -->
    <div style="background:#f8fafc;border-radius:8px;padding:18px 20px;margin-bottom:18px;">
      <p style="font-size:10px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#1d4ed8;margin:0 0 10px;">About Your Role</p>
      <p style="font-size:13px;color:#374151;line-height:1.85;margin:0;">
        As a <strong>{designation}</strong>{dept_html} at <strong>{company}</strong>, you will be entrusted with
        responsibilities that directly contribute to our organisational goals. You will collaborate with
        cross-functional teams, lead initiatives within your domain, and contribute to building a high-performance
        culture. We expect you to bring creativity, ownership, and a commitment to excellence to every task.
      </p>
    </div>

    <!-- What we offer -->
    <div style="background:#f0fdf4;border-radius:8px;padding:18px 20px;margin-bottom:18px;">
      <p style="font-size:10px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:#15803d;margin:0 0 10px;">What We Offer</p>
      <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
        <tr>
          <td style="padding:5px 8px;width:50%;vertical-align:top;">&#127775; Competitive CTC &amp; annual reviews</td>
          <td style="padding:5px 8px;width:50%;vertical-align:top;">&#128218; Learning &amp; development budget</td>
        </tr>
        <tr>
          <td style="padding:5px 8px;vertical-align:top;">&#127968; Flexible work environment</td>
          <td style="padding:5px 8px;vertical-align:top;">&#129303; Inclusive &amp; collaborative culture</td>
        </tr>
        <tr>
          <td style="padding:5px 8px;vertical-align:top;">&#127775; Performance bonuses &amp; incentives</td>
          <td style="padding:5px 8px;vertical-align:top;">&#128200; Clear growth &amp; promotion path</td>
        </tr>
      </table>
    </div>

    {ctc_section}
    {notes_section}

    <!-- Terms & Conditions -->
    <p style="font-size:10px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#1d4ed8;margin:20px 0 8px;">Terms &amp; Conditions</p>
    <ol style="padding-left:18px;font-size:12.5px;color:#4b5563;line-height:1.85;margin-bottom:20px;">
      <li style="margin-bottom:7px;">This offer is subject to satisfactory verification of your
        educational qualifications, credentials, and prior employment history.</li>
      <li style="margin-bottom:7px;">You will serve a probationary period of <strong>{probation} months</strong>
        from the date of joining. Confirmation is subject to satisfactory performance.</li>
      <li style="margin-bottom:7px;">Post-confirmation, either party may terminate employment by providing
        <strong>{notice_days} days'</strong> written notice or salary in lieu thereof. During probation,
        7 days' notice applies.</li>
      <li style="margin-bottom:7px;">All compensation is subject to applicable statutory deductions
        (TDS, PF, ESI, Professional Tax) as per prevailing Indian law.</li>
      <li style="margin-bottom:7px;">This offer is valid until <strong>{valid_until}</strong>.
        Non-acceptance by this date shall render this offer null and void.</li>
      <li style="margin-bottom:7px;">You shall maintain strict confidentiality of all proprietary and
        sensitive information of the Company during and after your employment.</li>
      <li style="margin-bottom:7px;">You will abide by the Company's HR policies, Code of Conduct, and
        all applicable rules as amended from time to time.</li>
      <li style="margin-bottom:7px;">A formal Appointment Letter will be issued upon joining. This offer
        letter does not constitute a contract of employment.</li>
    </ol>

    <p style="font-size:13px;color:#374151;line-height:1.9;margin-bottom:20px;">
      We look forward to welcoming you to <strong>{company}</strong>.
      Please review your complete offer letter PDF below and respond using the buttons at the bottom.
    </p>

    <!-- PDF section -->
    <div style="border:2px solid #e5e7eb;border-radius:10px;padding:20px 24px;margin-bottom:24px;background:#fafafa;">
      <div style="display:table;width:100%;">
        <div style="display:table-cell;vertical-align:middle;">
          <div style="font-size:32px;display:inline-block;vertical-align:middle;margin-right:12px;">&#128196;</div>
          <div style="display:inline-block;vertical-align:middle;">
            <div style="font-size:13px;font-weight:700;color:#111827;">Offer Letter -- {emp_name}.pdf</div>
            <div style="font-size:11px;color:#9ca3af;margin-top:2px;">Complete offer letter with salary breakdown &amp; terms</div>
          </div>
        </div>
      </div>
      <div style="margin-top:14px;display:flex;gap:10px;">
        <a href="{pdf_view_url}"
           style="display:inline-block;padding:10px 22px;background:#1d4ed8;color:#fff;font-size:12px;font-weight:700;text-decoration:none;border-radius:7px;">
          &#128065; &nbsp;View PDF
        </a>
        <a href="{pdf_dl_url}"
           style="display:inline-block;padding:10px 22px;background:#fff;color:#111827;font-size:12px;font-weight:700;
                  text-decoration:none;border-radius:7px;border:1.5px solid #d1d5db;margin-left:10px;">
          &#8681; &nbsp;Download PDF
        </a>
      </div>
    </div>

    <!-- Accept / Reject -->
    <div style="margin:0 0 16px;text-align:center;">
      <a href="{accept_url}"
         style="display:inline-block;padding:14px 40px;background:#16a34a;color:#fff;font-size:14px;font-weight:700;
                text-decoration:none;border-radius:8px;margin-right:14px;letter-spacing:.3px;">
        &#10003;&nbsp; Accept Offer
      </a>
      <a href="{reject_url}"
         style="display:inline-block;padding:14px 40px;background:#dc2626;color:#fff;font-size:14px;font-weight:700;
                text-decoration:none;border-radius:8px;letter-spacing:.3px;">
        &#10005;&nbsp; Decline Offer
      </a>
    </div>
    <p style="font-size:11px;color:#9ca3af;text-align:center;margin-bottom:24px;">
      Each response button can be used only once. Contact HR to change your response.
    </p>

    <p style="font-size:13px;color:#374151;line-height:1.9;">Warm regards,</p>
    <p style="font-size:13px;color:#374151;font-weight:700;margin-top:4px;">{company} HR Team</p>
  </div>

  <!-- Footer -->
  <div style="border-top:1px solid #e5e7eb;padding:10px 48px;font-size:10px;color:#9ca3af;display:table;width:100%;box-sizing:border-box;">
    <span style="display:table-cell;">{company}{(' · ' + co_address) if co_address else ''}</span>
    <span style="display:table-cell;text-align:right;">Confidential -- For addressee only</span>
  </div>
  <div style="height:4px;background:#111827;"></div>
</div>
</body></html>"""

        # Generate PDF
        pdf_bytes = _generate_offer_letter_pdf(letter, co)
        safe_name = emp_name.replace(" ", "_")
        send_email_smtp(
            emp_email,
            f"Offer Letter -- {company}",
            html_body,
            cfg,
            attachment_bytes=pdf_bytes,
            attachment_filename=f"Offer_Letter_{safe_name}.pdf",
        )

        token_expiry = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        cursor.execute(
            "UPDATE offer_letters SET sent_at=NOW(), status='sent', response_token=%s, "
            "response_token_expiry=%s, candidate_response=NULL, responded_at=NULL WHERE id=%s",
            (token_hash, token_expiry, letter_id)
        )
        db.commit()
        flash(f"Offer letter emailed to {emp_email}.", "success")
    except Exception as ex:
        flash(f"Email failed: {ex}", "error")
    cursor.close()
    db.close()
    return redirect(tpath(f"/offer_letter_view/{letter_id}"))


@onboarding_bp.route("/offer_letter_pdf/<token>")
def offer_letter_pdf(token):
    """Serve the offer letter PDF to the candidate (view or download) using their email token."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT ol.id,ol.onboarding_id,ol.employee_id,ol.designation,ol.department,
               ol.work_location,ol.monthly_ctc,ol.joining_date,ol.offer_valid_until,
               ol.probation_months,ol.reporting_to,ol.additional_notes,ol.generated_at,
               ol.sent_at,ol.status,
               COALESCE(ol.notice_period_days,30),COALESCE(ol.candidate_address,''),
               e.name, e.email
        FROM offer_letters ol
        JOIN employees e ON e.employee_id = ol.employee_id
        WHERE ol.response_token = %s
          AND (ol.response_token_expiry IS NULL OR ol.response_token_expiry > NOW())
    """, (hashlib.sha256(token.encode()).hexdigest(),))
    letter = cursor.fetchone()
    cursor.close()
    db.close()
    if not letter:
        return "<html><body style='font-family:Segoe UI,sans-serif;padding:60px;text-align:center;'>" \
               "<h2 style='color:#dc2626;'>Invalid or expired link.</h2>" \
               "<p>Please contact HR for a copy of your offer letter.</p></body></html>", 404
    co = get_company_settings()
    pdf_bytes = _generate_offer_letter_pdf(letter, co)
    emp_name = letter[17]
    safe_name = secure_filename(emp_name.replace(" ", "_")) or "Employee"
    dl = request.args.get("dl", "0")
    disposition = "attachment" if dl == "1" else "inline"
    from flask import Response
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'{disposition}; filename="Offer_Letter_{safe_name}.pdf"'
    return resp


@onboarding_bp.route("/offer_letter_respond/<token>/<action>")
def offer_letter_respond(token, action):
    if action not in ("accept", "reject"):
        return "Invalid action.", 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, employee_id, candidate_response, status FROM offer_letters "
        "WHERE response_token=%s AND (response_token_expiry IS NULL OR response_token_expiry > NOW())",
        (hashlib.sha256(token.encode()).hexdigest(),)
    )
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        return """<html><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:60px;color:#374151;">
          <h2 style="color:#dc2626;">Invalid or expired link.</h2>
          <p>This offer letter link is not valid. Please contact HR.</p></body></html>""", 404
    letter_id, emp_id, existing_response, status = row
    if existing_response:
        label = "accepted" if existing_response == "accept" else "declined"
        color = "#16a34a" if existing_response == "accept" else "#dc2626"
        cursor.close()
        db.close()
        return f"""<html><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:60px;color:#374151;">
          <h2 style="color:{color};">You have already {label} this offer.</h2>
          <p>Please contact HR if you wish to change your response.</p></body></html>"""
    cursor.execute(
        "UPDATE offer_letters SET candidate_response=%s, responded_at=NOW(), status=%s WHERE id=%s",
        (action, "accepted" if action == "accept" else "rejected", letter_id)
    )
    db.commit()
    cursor.close()
    db.close()
    if action == "accept":
        return """<html><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:60px;color:#374151;">
          <div style="font-size:56px;">&#127881;</div>
          <h2 style="color:#16a34a;margin-top:16px;">Offer Accepted!</h2>
          <p style="font-size:15px;margin-top:8px;">Thank you for accepting the offer. HR will reach out to you with next steps.</p>
          <p style="margin-top:24px;font-size:13px;color:#9ca3af;">You may close this window.</p></body></html>"""
    else:
        return """<html><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:60px;color:#374151;">
          <div style="font-size:56px;">&#128533;</div>
          <h2 style="color:#dc2626;margin-top:16px;">Offer Declined</h2>
          <p style="font-size:15px;margin-top:8px;">We have noted your decision. Thank you for considering us. We wish you the best.</p>
          <p style="margin-top:24px;font-size:13px;color:#9ca3af;">You may close this window.</p></body></html>"""


@onboarding_bp.route("/my_onboarding")
@employee_required
def my_onboarding():
    emp_id = session.get("employee_id")
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
        SELECT eo.id, ot.name, eo.assigned_date, eo.due_date, eo.status,
               COUNT(eot.id) AS total, SUM(CASE WHEN eot.status='Done' THEN 1 ELSE 0 END) AS done
        FROM employee_onboarding eo
        JOIN onboarding_templates ot ON ot.id=eo.template_id
        LEFT JOIN employee_onboarding_tasks eot ON eot.onboarding_id=eo.id
        WHERE eo.employee_id=%s
        GROUP BY eo.id, ot.name, eo.assigned_date, eo.due_date, eo.status ORDER BY eo.assigned_date DESC
    """, (emp_id,))
    onboardings = cursor.fetchall()

    selected_ob_id = request.args.get("ob_id")
    tasks = []
    selected_ob = None
    if not selected_ob_id and onboardings:
        selected_ob_id = onboardings[0][0]
    if selected_ob_id:
        cursor.execute("""SELECT id, task_title, task_description, requires_document,
                                 due_days, status, completed_at, document_path
                          FROM employee_onboarding_tasks
                          WHERE onboarding_id=%s AND employee_id=%s ORDER BY id""",
                       (selected_ob_id, emp_id))
        tasks = cursor.fetchall()
        for ob in onboardings:
            if ob[0] == int(selected_ob_id):
                selected_ob = ob
                break

    cursor.execute("SELECT employee_id, name, role, department, face_image FROM employees WHERE employee_id=%s", (emp_id,))
    emp = cursor.fetchone()
    cursor.close()
    db.close()
    if not emp:
        # Session outlived the employee row (e.g. deleted by an admin while
        # still logged in elsewhere) -- the template assumes emp is never
        # None, so send them back to login instead of a 500.
        session.clear()
        return redirect(tpath("/login"))
    return render_template("my_onboarding.html",
                           emp=emp, emp_id=emp_id, onboardings=onboardings, tasks=tasks,
                           selected_ob=selected_ob, selected_ob_id=int(selected_ob_id) if selected_ob_id else None,
                           today=company_today(),
                           )


@onboarding_bp.route("/my_onboarding_task_done", methods=["POST"])
@employee_required
def my_onboarding_task_done():
    emp_id = session.get("employee_id")
    db = get_db_connection()
    cursor = db.cursor()
    task_id = request.form.get("task_id")
    ob_id = request.form.get("ob_id")
    employee_note = request.form.get("employee_note", "").strip()[:500]

    cursor.execute("SELECT employee_id, requires_document FROM employee_onboarding_tasks WHERE id=%s", (task_id,))
    row = cursor.fetchone()
    if not row or row[0] != emp_id:
        flash("Not authorised.", "error")
        cursor.close()
        db.close()
        return redirect(tpath("/my_onboarding"))

    doc_path = None
    if 'document' in request.files:
        f = request.files['document']
        if f and f.filename:
            doc_path, upload_err = _save_onboarding_task_document(f, emp_id, task_id)
            if upload_err:
                cursor.close()
                db.close()
                flash(upload_err, "error")
                return redirect(tpath(f"/my_onboarding?ob_id={ob_id}"))

    if doc_path:
        cursor.execute("UPDATE employee_onboarding_tasks SET status='Done', completed_at=%s, document_path=%s, employee_note=%s WHERE id=%s",
                       (company_now().replace(tzinfo=None), doc_path, employee_note or None, task_id))
    else:
        cursor.execute("UPDATE employee_onboarding_tasks SET status='Done', completed_at=%s, employee_note=%s WHERE id=%s",
                       (company_now().replace(tzinfo=None), employee_note or None, task_id))

    # Auto-complete if all done
    cursor.execute("SELECT COUNT(*) FROM employee_onboarding_tasks WHERE onboarding_id=%s AND status!='Done'", (ob_id,))
    remaining = cursor.fetchone()[0]
    if remaining == 0:
        cursor.execute("UPDATE employee_onboarding SET status='Completed' WHERE id=%s", (ob_id,))
    db.commit()

    # Email admin about task completion
    try:
        cursor.execute("SELECT task_title FROM employee_onboarding_tasks WHERE id=%s", (task_id,))
        _tt = cursor.fetchone()
        task_title = _tt[0] if _tt else "Task"
        cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (emp_id,))
        _en = cursor.fetchone()
        emp_name_ob = _en[0] if _en else emp_id
        _ecfg = get_email_config()
        admin_email = _ecfg.get("from_email") if _ecfg else None
        if admin_email and _ecfg:
            _msg = (f"<p><strong>{emp_name_ob}</strong> has completed the onboarding task:</p>"
                    f"<p style='background:#f0fdf4;padding:10px;border-radius:8px;'><strong>{task_title}</strong></p>")
            if remaining == 0:
                _msg += "<p style='color:#16a34a;font-weight:700;'>🎉 All tasks completed -- onboarding marked as Complete!</p>"
            else:
                _msg += f"<p>{remaining} task(s) remaining.</p>"
            send_email_async(admin_email, f"Onboarding Task Done -- {emp_name_ob}", _msg, _ecfg)
    except Exception as exc:
        app_log.warning("Onboarding-task-done admin notification failed for %s: %s", emp_id, exc, exc_info=True)

    cursor.close()
    db.close()
    flash("Task marked as done!", "success")
    return redirect(tpath(f"/my_onboarding?ob_id={ob_id}"))


@onboarding_bp.route("/api/employee/onboarding", methods=["GET"])
@employee_api_required
def api_my_onboarding():
    """Bearer-token twin of my_onboarding() -- same query shape, JSON
    instead of a rendered template. Optional ?ob_id= selects a specific
    onboarding record's tasks; defaults to the most recent one, matching
    the web page's own default."""
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
        SELECT eo.id, ot.name, eo.assigned_date, eo.due_date, eo.status,
               COUNT(eot.id) AS total, SUM(CASE WHEN eot.status='Done' THEN 1 ELSE 0 END) AS done
        FROM employee_onboarding eo
        JOIN onboarding_templates ot ON ot.id=eo.template_id
        LEFT JOIN employee_onboarding_tasks eot ON eot.onboarding_id=eo.id
        WHERE eo.employee_id=%s
        GROUP BY eo.id, ot.name, eo.assigned_date, eo.due_date, eo.status ORDER BY eo.assigned_date DESC
    """, (emp_id,))
    onboardings = cursor.fetchall()

    selected_ob_id = request.args.get("ob_id")
    if not selected_ob_id and onboardings:
        selected_ob_id = onboardings[0][0]

    tasks = []
    if selected_ob_id:
        cursor.execute("""SELECT id, task_title, task_description, requires_document,
                                 due_days, status, completed_at, document_path, employee_note
                          FROM employee_onboarding_tasks
                          WHERE onboarding_id=%s AND employee_id=%s ORDER BY id""",
                       (selected_ob_id, emp_id))
        tasks = cursor.fetchall()
    cursor.close()
    db.close()

    return jsonify({
        "ok": True,
        "onboardings": [
            {"id": ob[0], "template_name": ob[1],
             "assigned_date": str(ob[2]) if ob[2] else None,
             "due_date": str(ob[3]) if ob[3] else None,
             "status": ob[4], "total_tasks": ob[5], "done_tasks": int(ob[6] or 0)}
            for ob in onboardings
        ],
        "selected_ob_id": int(selected_ob_id) if selected_ob_id else None,
        "tasks": [
            {"id": t[0], "task_title": t[1], "task_description": t[2],
             "requires_document": bool(t[3]), "due_days": t[4], "status": t[5],
             "completed_at": t[6].isoformat() if t[6] else None,
             # t[7] (document_path) is now save_private()'s opaque
             # stored_ref (an internal filesystem path or s3://... URI, per
             # its own docstring's "never build a URL from it directly")
             # -- never return it raw to a client; a boolean is all a
             # caller can actually use. (download_onboarding_document()
             # below is session-only, not Bearer-token -- there is no
             # mobile-reachable download route for this yet; nothing in
             # the mobile app currently renders this field either way.)
             "has_document": bool(t[7]), "employee_note": t[8]}
            for t in tasks
        ],
    })


@onboarding_bp.route("/api/employee/onboarding/task/<int:task_id>/done", methods=["POST"])
@employee_api_required
def api_my_onboarding_task_done(task_id):
    """Bearer-token twin of my_onboarding_task_done() -- same auto-complete
    and admin-notification-email side effects, form-data POST (so mobile
    can attach a document the same way the web form does)."""
    from flask import g as _g
    emp_id = _g.api_emp_id
    db = get_db_connection()
    cursor = db.cursor()
    ob_id = request.form.get("ob_id")
    employee_note = request.form.get("employee_note", "").strip()[:500]

    cursor.execute("SELECT employee_id, requires_document FROM employee_onboarding_tasks WHERE id=%s", (task_id,))
    row = cursor.fetchone()
    if not row or row[0] != emp_id:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Not authorised."}), 403

    doc_path = None
    if 'document' in request.files:
        f = request.files['document']
        if f and f.filename:
            doc_path, upload_err = _save_onboarding_task_document(f, emp_id, task_id)
            if upload_err:
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": upload_err}), 400

    if doc_path:
        cursor.execute("UPDATE employee_onboarding_tasks SET status='Done', completed_at=%s, document_path=%s, employee_note=%s WHERE id=%s",
                       (company_now().replace(tzinfo=None), doc_path, employee_note or None, task_id))
    else:
        cursor.execute("UPDATE employee_onboarding_tasks SET status='Done', completed_at=%s, employee_note=%s WHERE id=%s",
                       (company_now().replace(tzinfo=None), employee_note or None, task_id))

    cursor.execute("SELECT COUNT(*) FROM employee_onboarding_tasks WHERE onboarding_id=%s AND status!='Done'", (ob_id,))
    remaining = cursor.fetchone()[0]
    if remaining == 0:
        cursor.execute("UPDATE employee_onboarding SET status='Completed' WHERE id=%s", (ob_id,))
    db.commit()

    try:
        cursor.execute("SELECT task_title FROM employee_onboarding_tasks WHERE id=%s", (task_id,))
        _tt = cursor.fetchone()
        task_title = _tt[0] if _tt else "Task"
        cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (emp_id,))
        _en = cursor.fetchone()
        emp_name_ob = _en[0] if _en else emp_id
        _ecfg = get_email_config()
        admin_email = _ecfg.get("from_email") if _ecfg else None
        if admin_email and _ecfg:
            _msg = (f"<p><strong>{emp_name_ob}</strong> has completed the onboarding task (via mobile):</p>"
                    f"<p style='background:#f0fdf4;padding:10px;border-radius:8px;'><strong>{task_title}</strong></p>")
            if remaining == 0:
                _msg += "<p style='color:#16a34a;font-weight:700;'>🎉 All tasks completed -- onboarding marked as Complete!</p>"
            else:
                _msg += f"<p>{remaining} task(s) remaining.</p>"
            send_email_async(admin_email, f"Onboarding Task Done -- {emp_name_ob}", _msg, _ecfg)
    except Exception as exc:
        app_log.warning("Onboarding-task-done admin notification failed for %s: %s", emp_id, exc, exc_info=True)

    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Task marked as done.", "remaining": remaining})
