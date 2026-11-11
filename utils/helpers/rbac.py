# -*- coding: utf-8 -*-
"""helpers: role-based access (RBAC) for admin-side feature modules.

A company defines its own roles -- custom names on top of one of the four
base types (admin/hr/manager/employee) -- in Settings -> Roles & Access, and
sets each role's access per module to one of 'none' / 'view' / 'edit'. The
four system roles are seeded in migrations/core_tables.py; this module reads
the role_permissions table those seeds (and the Settings screen) populate.

superadmin is never configurable and is never stored in the roles table: it
always has full access, matching utils/auth.py's role_required(), where
superadmin already bypasses every role gate. The owner/superadmin can never be
locked out by a permission change.
"""
import re

from flask import session, g
from database import get_db_connection

# Feature areas the Roles & Access grid governs. Keep in sync with the seed in
# migrations/core_tables.py and the Settings UI.
RBAC_MODULES = (
    "employees", "attendance", "payroll", "leaves",
    "announcements", "tickets", "overtime", "performance",
    "onboarding", "resignations", "reports", "admin_tools",
    "organization",
)

# Ordered so a higher level satisfies a lower one (edit implies view).
_ACCESS_RANK = {"none": 0, "view": 1, "edit": 2}


def get_role_permissions(role):
    """Return {module: access} for one role_key in the current tenant schema.
    A role with no rows yet is treated as no access (empty dict)."""
    try:
        db = get_db_connection()
        cur = db.cursor()
        try:
            cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (role,))
            return {module: access for module, access in cur.fetchall()}
        finally:
            cur.close()
    except Exception:
        # Table not provisioned yet (pre-migration) or a transient read error:
        # return nothing so a page never 500s. Deny-by-default for everyone
        # except the always-full admin/superadmin handled in can_access().
        return {}


def _cached_permissions(role):
    """Per-request cache so menu/template checks don't re-query for every
    module. Falls back to a direct read outside a request context (e.g. CLI)."""
    try:
        cache = g._rbac_perms  # noqa: SLF001 -- request-local scratch space
    except (RuntimeError, AttributeError):
        return get_role_permissions(role)
    if role not in cache:
        cache[role] = get_role_permissions(role)
    return cache[role]


def _ensure_cache():
    try:
        if not hasattr(g, "_rbac_perms"):
            g._rbac_perms = {}
    except RuntimeError:
        pass  # no request context -- _cached_permissions reads directly


def can_access(module, action="view", role=None):
    """True if `role` (default: the logged-in admin-side role in the session)
    may use `module` at `action` level -- 'view' or 'edit'. 'edit' implies
    'view'. superadmin always passes; an unknown role or module means no
    access (deny by default)."""
    role = role or session.get("admin_role") or "admin"
    # The base 'admin' role and superadmin always have full access and are never
    # restrictable (LOCKED_ROLE_KEYS) -- only *custom* admin-tier roles (a
    # different role_key) are configurable, so no company can lock itself out.
    if role in ("superadmin", "admin"):
        return True
    _ensure_cache()
    access = _cached_permissions(role).get(module, "none")
    return _ACCESS_RANK.get(access, 0) >= _ACCESS_RANK.get(action, 1)


# ── Management (read + write) used by the Settings "Roles & Access" screen ────
VALID_ACCESS = ("none", "view", "edit")
VALID_BASE_TYPES = ("admin", "hr", "manager", "employee")
# System roles whose identity/base can't be changed or deleted. Their per-module
# access is still editable EXCEPT 'admin', which stays full so a company can
# never lock every admin out (superadmin, separate and never in this table, is
# the ultimate backstop).
SYSTEM_ROLE_KEYS = ("admin", "employee")
LOCKED_ROLE_KEYS = ("admin",)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name):
    """A safe, unique-ish role_key from a display name: 'Senior HR' -> 'senior_hr'.
    Only [a-z0-9_]; role_key lands in admin_users.role / session['admin_role']."""
    s = _SLUG_RE.sub("_", (name or "").strip().lower()).strip("_")
    return s or "role"


def list_roles():
    """Every role in this tenant: system roles first, then custom by name."""
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute(
            "SELECT role_key, display_name, base_type, is_system "
            "FROM roles ORDER BY is_system DESC, display_name"
        )
        return [
            {"role_key": rk, "display_name": dn, "base_type": bt,
             "is_system": bool(sysrow),
             "locked": rk in LOCKED_ROLE_KEYS}
            for rk, dn, bt, sysrow in cur.fetchall()
        ]
    finally:
        cur.close()


def all_role_permissions():
    """{role_key: {module: access}} for every role, every module filled in."""
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute("SELECT role_key, module, access FROM role_permissions")
        out = {}
        for role_key, module, access in cur.fetchall():
            out.setdefault(role_key, {})[module] = access
        return out
    finally:
        cur.close()


def defined_role_base_type(role_key):
    """base_type ('admin'|'hr'|'manager'|'employee') if role_key is a role that
    actually exists in Settings -> Roles & Access (the roles table), else None.
    Unlike base_type_for_role(), an unknown/free-text value returns None -- so a
    plain job title like "Developer" routes to the normal employee portal, and
    only a real assigned role sends an employee to the admin-side dashboard.
    Used by blueprints/auth.py's login routing."""
    role_key = (role_key or "").strip()
    if not role_key:
        return None
    try:
        db = get_db_connection()
        cur = db.cursor()
        try:
            cur.execute("SELECT base_type FROM roles WHERE role_key=%s", (role_key,))
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            cur.close()
    except Exception:
        return None


def employees_by_role():
    """{role_key: [{'employee_id':.., 'name':..}]} -- which employees are assigned
    each access role, so the Roles & Access screen can show who uses each role's
    dashboard (e.g. Senior HR -> Harini Aindla). Empty dict on any read error."""
    try:
        db = get_db_connection()
        cur = db.cursor()
        try:
            cur.execute(
                "SELECT access_role, employee_id, name FROM employees "
                "WHERE access_role IS NOT NULL AND access_role <> '' ORDER BY name"
            )
            out = {}
            for role_key, emp_id, name in cur.fetchall():
                out.setdefault(role_key, []).append({"employee_id": emp_id, "name": name})
            return out
        finally:
            cur.close()
    except Exception:
        return {}


def list_employees_with_roles():
    """Every employee with their current access role (role_key, '' if none) --
    feeds the "Employee Access Assignments" table on Settings -> Roles &
    Access, the one place to put a new hire on a dashboard, promote them to a
    different role, or take their admin access away."""
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute(
            "SELECT employee_id, name, department, COALESCE(access_role,'') "
            "FROM employees ORDER BY name"
        )
        return [
            {"employee_id": eid, "name": name, "department": dept or "", "access_role": role}
            for eid, name, dept, role in cur.fetchall()
        ]
    finally:
        cur.close()


def assign_employee_role(employee_id, role_key):
    """Assign, change (promote/demote), or clear employee_id's access role --
    the one action behind "give a new hire a dashboard", "Junior HR just
    became Senior HR", and "take their admin access away". role_key='' clears
    it, sending them back to the plain employee portal and immediately
    revoking any live admin session (not just at their next login). A real
    role_key must be an existing CUSTOM role -- 'admin' and the system
    'employee' row aren't assignable this way (the Administrator is set up
    elsewhere and can't be restricted; "no role" already means "employee").
    Returns (saved_role_key, employee_name); saved_role_key is '' when
    cleared. Raises ValueError on bad input."""
    import secrets
    from utils.auth import generate_password_hash
    employee_id = (employee_id or "").strip()
    if not employee_id:
        raise ValueError("Choose an employee.")
    role_key = (role_key or "").strip()
    if role_key == "employee":
        role_key = ""  # "no access role" is stored as NULL, not the system row
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute("SELECT name, email FROM employees WHERE employee_id=%s", (employee_id,))
        emp_row = cur.fetchone()
        if not emp_row:
            raise ValueError("Employee not found.")
        emp_name, emp_email = emp_row
        if role_key:
            if role_key in LOCKED_ROLE_KEYS:
                raise ValueError("That role can't be assigned here.")
            cur.execute("SELECT is_system FROM roles WHERE role_key=%s", (role_key,))
            role_row = cur.fetchone()
            if not role_row or role_row[0]:
                raise ValueError("Role not found.")
        cur.execute("UPDATE employees SET access_role=%s WHERE employee_id=%s",
                    (role_key or None, employee_id))
        if role_key:
            # Keep the admin_users shadow account (what actually carries the
            # session's role/base_type -- see blueprints/auth.py's
            # _ensure_role_admin_account) in sync RIGHT NOW, so the change is
            # live immediately rather than only from their next login.
            cur.execute("SELECT role FROM admin_users WHERE username=%s", (employee_id,))
            au_row = cur.fetchone()
            if au_row is None:
                cur.execute(
                    "INSERT INTO admin_users (username, password, role, email, is_active) "
                    "VALUES (%s,%s,%s,%s,1)",
                    (employee_id, generate_password_hash(secrets.token_urlsafe(32)), role_key, emp_email),
                )
            else:
                cur.execute("UPDATE admin_users SET role=%s, is_active=1 WHERE username=%s",
                            (role_key, employee_id))
        else:
            # Cleared: kill any live admin session on its next per-request
            # check instead of leaving it valid with a stale role.
            cur.execute("UPDATE admin_users SET is_active=0 WHERE username=%s", (employee_id,))
        db.commit()
        return role_key, emp_name
    finally:
        cur.close()


def base_type_for_role(role_key):
    """The base_type ('admin'|'hr'|'manager'|'employee') a role maps to, which
    decides the admin panel it lands on. Unknown/legacy role -> 'admin' so an
    existing account is never downgraded by a missing row. superadmin stays
    'admin'-tier here but bypasses every gate elsewhere."""
    if role_key in ("admin", "superadmin"):
        return "admin"
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute("SELECT base_type FROM roles WHERE role_key=%s", (role_key,))
        row = cur.fetchone()
        return row[0] if row else "admin"
    finally:
        cur.close()


def create_role(display_name, base_type):
    """Create a custom role from a display name + base type. Returns its
    role_key. Raises ValueError on bad input or a name that collides."""
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Role name is required.")
    if base_type not in VALID_BASE_TYPES:
        raise ValueError("Invalid base type.")
    key = _slug(display_name)
    db = get_db_connection()
    cur = db.cursor()
    try:
        # Keep role_key unique even if two names slug to the same value.
        base_key, n = key, 2
        while True:
            cur.execute("SELECT 1 FROM roles WHERE role_key=%s", (key,))
            if not cur.fetchone():
                break
            key = f"{base_key}_{n}"
            n += 1
        cur.execute(
            "INSERT INTO roles (role_key, display_name, base_type, is_system) "
            "VALUES (%s,%s,%s,FALSE)",
            (key, display_name, base_type),
        )
        # Start a new role with no access to anything (deny by default).
        cur.executemany(
            "INSERT INTO role_permissions (role_key, module, access) VALUES (%s,%s,'none') "
            "ON CONFLICT (role_key, module) DO NOTHING",
            [(key, m) for m in RBAC_MODULES],
        )
        db.commit()
        return key
    finally:
        cur.close()


def rename_role(role_key, display_name):
    """Rename any role (system roles can be renamed, not re-keyed)."""
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Role name is required.")
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute("UPDATE roles SET display_name=%s WHERE role_key=%s",
                    (display_name, role_key))
        db.commit()
    finally:
        cur.close()


def delete_role(role_key):
    """Delete a custom role (never a system role). role_permissions rows cascade.
    Raises ValueError for a system role or one still assigned to someone."""
    if role_key in SYSTEM_ROLE_KEYS:
        raise ValueError("System roles can't be deleted.")
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute("SELECT 1 FROM roles WHERE role_key=%s AND is_system=FALSE", (role_key,))
        if not cur.fetchone():
            raise ValueError("Role not found.")
        cur.execute("SELECT COUNT(*) FROM admin_users WHERE role=%s", (role_key,))
        in_use = cur.fetchone()[0]
        # access_role (the RBAC assignment), not role (the free-text job
        # title) -- those are two separate columns; checking the wrong one
        # let a still-assigned role be deleted, leaving employees.access_role
        # pointing at nothing.
        cur.execute("SELECT COUNT(*) FROM employees WHERE access_role=%s", (role_key,))
        in_use += cur.fetchone()[0]
        if in_use:
            raise ValueError("This role is still assigned to %d account(s); "
                             "reassign them first." % in_use)
        cur.execute("DELETE FROM roles WHERE role_key=%s", (role_key,))
        db.commit()
    finally:
        cur.close()


def set_permissions(role_key, perms):
    """Upsert a role's whole access grid. `perms` is {module: access}; unknown
    modules are ignored, missing ones default to 'none'. The locked 'admin'
    role is forced to full access (never restrictable)."""
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute("SELECT 1 FROM roles WHERE role_key=%s", (role_key,))
        if not cur.fetchone():
            raise ValueError("Role not found.")
        rows = []
        for module in RBAC_MODULES:
            access = "edit" if role_key in LOCKED_ROLE_KEYS else perms.get(module, "none")
            if access not in VALID_ACCESS:
                access = "none"
            rows.append((role_key, module, access))
        cur.executemany(
            "INSERT INTO role_permissions (role_key, module, access) VALUES (%s,%s,%s) "
            "ON CONFLICT (role_key, module) DO UPDATE SET access=EXCLUDED.access",
            rows,
        )
        db.commit()
    finally:
        cur.close()
