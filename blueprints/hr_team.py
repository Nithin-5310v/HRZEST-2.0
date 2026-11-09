# -*- coding: utf-8 -*-
"""Organization page's HR team tab: the company admin sees its HRs, makes an
employee an HR, and turns each HR's dashboard access on or off.

An HR is an employee whose role is exactly "HR" (blueprints/auth.py's
_finish_employee_login), backed by an admin_users row with the same
username as their employee ID. That row's is_active flag is their HR
dashboard access:

  * on  -- they sign in with their employee login and land on the HR
           dashboard, where they can add employees and manage the ones
           assigned to them;
  * off -- they sign in to their own employee portal like everyone else,
           and an HR session already open ends on its next request
           (utils/auth._reject_if_account_deactivated).

Only the company admin (role "admin") can use this tab.
"""
import secrets

from flask import Blueprint, flash, redirect, render_template, request, session

from extensions import log_security_event
from utils.auth import HR_ROLE, generate_password_hash, role_required
from utils.helpers import _db, decrypt_pii, get_company_settings, tpath

hr_team_bp = Blueprint("hr_team", __name__)

# employees.role is a free-text job title; exactly "HR" makes an HR.
_IS_HR = "LOWER(TRIM(COALESCE(e.role, ''))) = 'hr'"
_ACTIVE = "COALESCE(e.is_active, 1) = 1"


def hr_choices(cursor):
    """[(username, "Name (ID)")] of the HRs an employee can be assigned to:
    every HR with dashboard access. An HR employee who hasn't signed in yet
    (no admin_users row) has access by default and is included."""
    cursor.execute(
        "SELECT a.username, e.name FROM admin_users a LEFT JOIN employees e ON e.employee_id = a.username "
        f"WHERE a.role = %s AND COALESCE(a.is_active, 1) = 1 "  # nosec B608 -- fixed SQL fragments
        f"AND (e.employee_id IS NULL OR ({_IS_HR} AND {_ACTIVE})) "
        "UNION "
        "SELECT e.employee_id, e.name FROM employees e LEFT JOIN admin_users a ON a.username = e.employee_id "
        f"WHERE {_IS_HR} AND {_ACTIVE} AND a.username IS NULL",
        (HR_ROLE,),
    )
    rows = sorted(cursor.fetchall(), key=lambda r: (r[1] or r[0]).lower())
    return [(u, f"{n} ({u})" if n else u) for u, n in rows]


def is_assignable_hr(cursor, username):
    return bool(username) and any(u == username for u, _label in hr_choices(cursor))


def hr_label(cursor, username):
    """"Name (ID)" for an HR username, or the username itself."""
    if not username:
        return ""
    cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (username,))
    r = cursor.fetchone()
    return f"{r[0]} ({username})" if r and r[0] else username


def _team(cursor):
    cursor.execute(
        "SELECT e.employee_id, e.name, e.email, e.department, a.username IS NOT NULL, COALESCE(a.is_active, 1) "
        "FROM employees e LEFT JOIN admin_users a ON a.username = e.employee_id "
        f"WHERE {_IS_HR} AND {_ACTIVE} ORDER BY e.name",  # nosec B608 -- fixed SQL fragments
    )
    team = [{"username": r[0], "name": r[1], "email": decrypt_pii(r[2]) or "", "department": r[3] or "",
             "signed_in": bool(r[4]), "access": bool(r[5]), "employee": True} for r in cursor.fetchall()]
    # HR logins that aren't employees (made before HRs had to be employees).
    cursor.execute(
        "SELECT a.username, a.display_name, a.email, COALESCE(a.is_active, 1) FROM admin_users a "
        "WHERE a.role = %s AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.employee_id = a.username) "
        "ORDER BY a.username",
        (HR_ROLE,),
    )
    team += [{"username": r[0], "name": r[1] or r[0], "email": r[2] or "", "department": "",
              "signed_in": True, "access": bool(r[3]), "employee": False} for r in cursor.fetchall()]
    cursor.execute(
        "SELECT assigned_hr_username, COUNT(*) FROM employees "
        "WHERE assigned_hr_username IS NOT NULL AND COALESCE(is_active, 1) = 1 GROUP BY assigned_hr_username"
    )
    counts = dict(cursor.fetchall())
    for hr in team:
        hr["assigned"] = counts.get(hr["username"], 0)
    return team


@hr_team_bp.route("/profile/hr", methods=["GET"])
@role_required("admin")
def hr_team():
    with _db() as (cursor, _db_conn):
        team = _team(cursor)
        cursor.execute(
            "SELECT e.employee_id, e.name, COALESCE(e.role, ''), COALESCE(e.department, '') FROM employees e "
            f"WHERE {_ACTIVE} AND NOT {_IS_HR} ORDER BY e.name",  # nosec B608 -- fixed SQL fragments
        )
        candidates = [{"id": r[0], "name": r[1], "role": r[2], "department": r[3]} for r in cursor.fetchall()]
        cursor.execute("SELECT COUNT(*) FROM employees WHERE assigned_hr_username IS NULL AND COALESCE(is_active, 1) = 1")
        unassigned = cursor.fetchone()[0]
    return render_template(
        "profile.html", active_nav="profile", active_tab="hr", co=get_company_settings(),
        hr_team=team, hr_candidates=candidates, unassigned_count=unassigned,
    )


def _set_access(cursor, username, access, email=None):
    """Creates or updates the HR's admin_users row with this access."""
    cursor.execute("SELECT role FROM admin_users WHERE username=%s", (username,))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE admin_users SET role=%s, is_active=%s WHERE username=%s",
                       (HR_ROLE, 1 if access else 0, username))
    else:
        # Random, never-shown password: an HR signs in with their own
        # employee login (same as blueprints/auth._ensure_hr_admin_account).
        cursor.execute(
            "INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,%s)",
            (username, generate_password_hash(secrets.token_urlsafe(32)), HR_ROLE, email, 1 if access else 0),
        )


@hr_team_bp.route("/profile/hr/add", methods=["POST"])
@role_required("admin")
def hr_team_add():
    emp_id = (request.form.get("employee_id") or "").strip()
    access = request.form.get("access") == "1"
    with _db() as (cursor, db):
        cursor.execute("SELECT name, email, COALESCE(is_active, 1), role FROM employees WHERE employee_id=%s",
                       (emp_id,))
        emp = cursor.fetchone()
        if not emp or not emp[2]:
            flash("Choose an active employee to make an HR.", "error")
            return redirect(tpath("/profile/hr"))
        name, enc_email, _active, previous_role = emp
        cursor.execute("SELECT role FROM admin_users WHERE username=%s", (emp_id,))
        login = cursor.fetchone()
        if login and (login[0] or "admin") not in (HR_ROLE, "manager"):
            # Same name as an admin login: making it an HR would change that login.
            flash(f"{emp_id} is also the name of an admin login, so it can't be made an HR account.", "error")
            return redirect(tpath("/profile/hr"))
        cursor.execute("UPDATE employees SET role='HR' WHERE employee_id=%s", (emp_id,))
        _set_access(cursor, emp_id, access, decrypt_pii(enc_email) or None)
        db.commit()
    log_security_event(
        "admin.hr_added", f"Employee '{emp_id}' made an HR (role was {previous_role!r}), "
        f"dashboard access {'on' if access else 'off'}",
        level="WARNING", identifier=session.get("admin_username"), employee_id=emp_id,
    )
    flash(f"{name} is now an HR. " + (
        "They sign in with their usual employee login and land on the HR dashboard." if access
        else "Turn on their dashboard access when they should start using it."), "success")
    return redirect(tpath("/profile/hr"))


@hr_team_bp.route("/profile/hr/<username>/access", methods=["POST"])
@role_required("admin")
def hr_team_access(username):
    access = request.form.get("access") == "1"
    with _db() as (cursor, db):
        cursor.execute(
            f"SELECT e.name, e.email FROM employees e WHERE e.employee_id=%s AND {_IS_HR}",  # nosec B608 -- fixed SQL fragment
            (username,),
        )
        emp = cursor.fetchone()
        cursor.execute("SELECT role FROM admin_users WHERE username=%s", (username,))
        login = cursor.fetchone()
        is_standalone_hr = bool(login) and login[0] == HR_ROLE
        if not emp and not is_standalone_hr:
            flash("That person isn't on your HR team.", "error")
            return redirect(tpath("/profile/hr"))
        if emp and login and (login[0] or "admin") not in (HR_ROLE, "manager"):
            flash(f"{username} is also the name of an admin login, so its access can't be changed here.", "error")
            return redirect(tpath("/profile/hr"))
        _set_access(cursor, username, access, decrypt_pii(emp[1]) if emp else None)
        db.commit()
    name = emp[0] if emp else username
    log_security_event(
        "admin.hr_access_changed", f"HR dashboard access for '{username}' turned {'on' if access else 'off'}",
        level="WARNING", identifier=session.get("admin_username"), hr_username=username, access=access,
    )
    flash(f"HR dashboard access turned on for {name}." if access else
          f"HR dashboard access turned off for {name}. They now sign in to their own employee portal; "
          "the employees assigned to them stay assigned.", "success")
    return redirect(tpath("/profile/hr"))
