# -*- coding: utf-8 -*-
"""helpers: role-based access (RBAC) for admin-side feature modules.

Only two dashboards are fixed: Administrator and Employee (the two system
roles seeded in migrations/core_tables.py). Every other role -- HR, Manager,
Senior HR Manager, ... -- a company defines itself in Settings -> Roles &
Access, on one of the base types (admin/hr/manager/employee), with each
module's access set to 'none' / 'view' / 'edit', and assigns to employees.
An employee with no assigned role gets only the employee dashboard.

Roles form a hierarchy: each role reports to a parent role (parent_role_key;
NULL = directly under the Administrator), and can never have more access
than its parent -- set_permissions() clamps a role to its parent's access
and passes any reduction down to every role below it. Each role also has a
data scope: 'company' (sees every employee) or 'team' (sees only the people
attached to that person -- by Reporting manager or Assigned HR -- at any
depth; see utils/helpers/scoping.py).

superadmin is never configurable and is never stored in the roles table: it
always has full access, matching utils/auth.py's role_required(), where
superadmin already bypasses every role gate. The owner/superadmin can never be
locked out by a permission change.
"""
import re

from flask import session, g
from database import get_db_connection
from .db import _db

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
# The two fixed dashboards, whose identity/base can't be changed or deleted.
# 'admin' stays full access so a company can never lock every admin out
# (superadmin, separate and never in this table, is the ultimate backstop).
SYSTEM_ROLE_KEYS = ("admin", "employee")
LOCKED_ROLE_KEYS = ("admin",)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name):
    """A safe, unique-ish role_key from a display name: 'Senior HR' -> 'senior_hr'.
    Only [a-z0-9_]; role_key lands in admin_users.role / session['admin_role']."""
    s = _SLUG_RE.sub("_", (name or "").strip().lower()).strip("_")
    return s or "role"


VALID_DATA_SCOPES = ("company", "team")


def list_roles():
    """Every role in this tenant: system roles first, then custom by name.
    Each carries its hierarchy fields: parent_role_key ('' = directly under
    the Administrator), description and data_scope."""
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute(
            "SELECT role_key, display_name, base_type, is_system, COALESCE(parent_role_key,''), "
            "COALESCE(description,''), COALESCE(data_scope,'company') "
            "FROM roles ORDER BY is_system DESC, display_name"
        )
        return [
            {"role_key": rk, "display_name": dn, "base_type": bt,
             "is_system": bool(sysrow),
             "locked": rk in LOCKED_ROLE_KEYS,
             "parent_role_key": parent, "description": desc, "data_scope": scope}
            for rk, dn, bt, sysrow, parent, desc, scope in cur.fetchall()
        ]
    finally:
        cur.close()


def role_tree(roles=None):
    """The custom roles in hierarchy order (each parent before its children,
    siblings by name), each a list_roles() dict plus 'depth' (0 = directly
    under the Administrator), 'children' (role_keys) and 'ancestors'
    (role_keys from the top down). A role whose parent no longer exists is
    treated as top-level, so nothing is ever hidden."""
    roles = [r for r in (roles if roles is not None else list_roles()) if not r["is_system"]]
    by_key = {r["role_key"]: dict(r, children=[]) for r in roles}
    roots = []
    for r in sorted(by_key.values(), key=lambda r: r["display_name"].lower()):
        parent = r["parent_role_key"]
        if parent in by_key and parent != r["role_key"]:
            by_key[parent]["children"].append(r["role_key"])
        else:
            roots.append(r["role_key"])
    out, seen = [], set()

    def walk(key, depth, ancestors):
        if key in seen:      # a cycle in old data: show each role once
            return
        seen.add(key)
        node = by_key[key]
        out.append(dict(node, depth=depth, ancestors=list(ancestors)))
        for child in node["children"]:
            walk(child, depth + 1, ancestors + [key])

    for key in roots:
        walk(key, 0, [])
    for key in by_key:       # anything only reachable through a cycle
        walk(key, 0, [])
    return out


def _descendant_keys(cur, role_key):
    """Every role below role_key, at any depth (not including itself)."""
    cur.execute(
        "WITH RECURSIVE d(k) AS (SELECT role_key FROM roles WHERE parent_role_key=%s "
        "UNION SELECT r.role_key FROM roles r JOIN d ON r.parent_role_key = d.k) SELECT k FROM d",
        (role_key,),
    )
    return {r[0] for r in cur.fetchall()}


def _ceiling(cur, role_key):
    """The most access role_key may have, per module: its parent's access,
    or full access for a role directly under the Administrator."""
    cur.execute("SELECT COALESCE(parent_role_key,'') FROM roles WHERE role_key=%s", (role_key,))
    row = cur.fetchone()
    parent = row[0] if row else ""
    if not parent or parent in LOCKED_ROLE_KEYS:
        return {m: "edit" for m in RBAC_MODULES}
    cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (parent,))
    got = dict(cur.fetchall())
    return {m: got.get(m, "none") for m in RBAC_MODULES}


def role_ceilings():
    """{role_key: {module: max access}} for every custom role -- what the
    role pages show as locked (above the parent's access)."""
    roles = list_roles()
    perms = all_role_permissions()
    full = {m: "edit" for m in RBAC_MODULES}
    out = {}
    for r in roles:
        parent = r["parent_role_key"]
        out[r["role_key"]] = (full if not parent or parent in LOCKED_ROLE_KEYS
                              else {m: perms.get(parent, {}).get(m, "none") for m in RBAC_MODULES})
    return out


def _clamp(access, ceiling):
    return access if _ACCESS_RANK.get(access, 0) <= _ACCESS_RANK.get(ceiling, 0) else ceiling


def _cascade_down(cur, role_key):
    """After role_key's access changed, lower every role below it that now
    has more than its own parent. Returns the display names of roles that
    were lowered."""
    lowered = []
    frontier = [role_key]
    seen = {role_key}
    while frontier:
        parent = frontier.pop(0)
        cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (parent,))
        parent_perms = dict(cur.fetchall())
        cur.execute("SELECT role_key, display_name FROM roles WHERE parent_role_key=%s", (parent,))
        for child, child_name in cur.fetchall():
            if child in seen:
                continue
            seen.add(child)
            cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (child,))
            child_perms = dict(cur.fetchall())
            changed = False
            for m in RBAC_MODULES:
                new = _clamp(child_perms.get(m, "none"), parent_perms.get(m, "none"))
                if new != child_perms.get(m, "none"):
                    cur.execute(
                        "INSERT INTO role_permissions (role_key, module, access) VALUES (%s,%s,%s) "
                        "ON CONFLICT (role_key, module) DO UPDATE SET access=EXCLUDED.access",
                        (child, m, new),
                    )
                    changed = True
            if changed:
                lowered.append(child_name)
            frontier.append(child)
    return lowered


def _validate_parent(cur, role_key, parent):
    """'' (directly under the Administrator) or an existing custom role that
    isn't role_key itself or below it. Raises ValueError otherwise."""
    parent = (parent or "").strip()
    if not parent or parent in LOCKED_ROLE_KEYS:
        return ""
    cur.execute("SELECT is_system FROM roles WHERE role_key=%s", (parent,))
    row = cur.fetchone()
    if not row or row[0]:
        raise ValueError("Choose a valid role for “Reports to”.")
    if role_key and (parent == role_key or parent in _descendant_keys(cur, role_key)):
        raise ValueError("A role can't report to itself or to a role below it.")
    return parent


def role_data_scope(role_key):
    """'company' or 'team' for a role_key, cached per request. The owner
    'admin' and superadmin always see the whole company. A role with no row
    (or a failed read) falls back to the old behaviour: the built-in 'hr'
    and 'manager' were limited to their own people."""
    if role_key in ("admin", "superadmin"):
        return "company"
    try:
        cache = g._rbac_scope
    except (RuntimeError, AttributeError):
        cache = None
    if cache is not None and role_key in cache:
        return cache[role_key]
    scope = None
    try:
        db = get_db_connection()
        cur = db.cursor()
        try:
            cur.execute("SELECT COALESCE(data_scope,'company') FROM roles WHERE role_key=%s", (role_key,))
            row = cur.fetchone()
            scope = row[0] if row else None
        finally:
            cur.close()
    except Exception:
        scope = None
    if scope not in VALID_DATA_SCOPES:
        scope = "team" if role_key in ("hr", "manager") else "company"
    try:
        if not hasattr(g, "_rbac_scope"):
            g._rbac_scope = {}
        g._rbac_scope[role_key] = scope
    except RuntimeError:
        pass
    return scope


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


def create_role(display_name, base_type, parent_role_key="", description="", data_scope="company"):
    """Create a custom role from a display name + base type, reporting to
    parent_role_key ('' = directly under the Administrator). Returns its
    role_key. Raises ValueError on bad input or a name that collides."""
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Role name is required.")
    if base_type not in VALID_BASE_TYPES:
        raise ValueError("Invalid base type.")
    if data_scope not in VALID_DATA_SCOPES:
        raise ValueError("Choose what this role can see.")
    description = (description or "").strip()[:255]
    key = _slug(display_name)
    db = get_db_connection()
    cur = db.cursor()
    try:
        parent = _validate_parent(cur, "", parent_role_key)
        # Keep role_key unique even if two names slug to the same value.
        base_key, n = key, 2
        while True:
            cur.execute("SELECT 1 FROM roles WHERE role_key=%s", (key,))
            if not cur.fetchone():
                break
            key = f"{base_key}_{n}"
            n += 1
        cur.execute(
            "INSERT INTO roles (role_key, display_name, base_type, is_system, parent_role_key, "
            "description, data_scope) VALUES (%s,%s,%s,FALSE,%s,%s,%s)",
            (key, display_name, base_type, parent or None, description or None, data_scope),
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


def update_role(role_key, display_name, parent_role_key="", description="", data_scope="company"):
    """Edit a custom role's name, Reports to, description and data scope.
    Moving it under a different parent lowers its access (and everything
    below it) to fit the new parent. Returns the display names of roles
    whose access was lowered. Raises ValueError on bad input."""
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Role name is required.")
    if data_scope not in VALID_DATA_SCOPES:
        raise ValueError("Choose what this role can see.")
    db = get_db_connection()
    cur = db.cursor()
    try:
        cur.execute("SELECT is_system FROM roles WHERE role_key=%s", (role_key,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Role not found.")
        if row[0]:
            raise ValueError("Built-in roles can only be renamed.")
        parent = _validate_parent(cur, role_key, parent_role_key)
        cur.execute(
            "UPDATE roles SET display_name=%s, parent_role_key=%s, description=%s, data_scope=%s "
            "WHERE role_key=%s",
            (display_name, parent or None, (description or "").strip()[:255] or None, data_scope, role_key),
        )
        ceiling = _ceiling(cur, role_key)
        cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (role_key,))
        current = dict(cur.fetchall())
        lowered = []
        if any(_clamp(current.get(m, "none"), ceiling[m]) != current.get(m, "none") for m in RBAC_MODULES):
            cur.executemany(
                "INSERT INTO role_permissions (role_key, module, access) VALUES (%s,%s,%s) "
                "ON CONFLICT (role_key, module) DO UPDATE SET access=EXCLUDED.access",
                [(role_key, m, _clamp(current.get(m, "none"), ceiling[m])) for m in RBAC_MODULES],
            )
            lowered.append(display_name)
        lowered += _cascade_down(cur, role_key)
        db.commit()
        return lowered
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
        cur.execute("SELECT COALESCE(parent_role_key,'') FROM roles WHERE role_key=%s AND is_system=FALSE",
                    (role_key,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Role not found.")
        grandparent = row[0]
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
        # Roles below it move up to its own parent; they already fit within
        # that parent's access (theirs never exceeded this role's).
        cur.execute("UPDATE roles SET parent_role_key=%s WHERE parent_role_key=%s",
                    (grandparent or None, role_key))
        cur.execute("DELETE FROM roles WHERE role_key=%s", (role_key,))
        db.commit()
    finally:
        cur.close()


def set_permissions(role_key, perms, commit=True, cur=None):
    """Upsert a role's whole access grid. `perms` is {module: access}; unknown
    modules are ignored, missing ones default to 'none'. The locked 'admin'
    role is forced to full access (never restrictable). A role can't have
    more than its parent: anything above the parent's access is saved at the
    parent's level, and roles below it are lowered to fit. Returns
    (capped, lowered): the modules that were capped at the parent's level,
    and the display names of roles below whose access was lowered."""
    own = cur is None
    db = get_db_connection() if own else None
    cur = db.cursor() if own else cur
    try:
        cur.execute("SELECT 1 FROM roles WHERE role_key=%s", (role_key,))
        if not cur.fetchone():
            raise ValueError("Role not found.")
        ceiling = _ceiling(cur, role_key)
        rows, capped = [], []
        for module in RBAC_MODULES:
            access = "edit" if role_key in LOCKED_ROLE_KEYS else perms.get(module, "none")
            if access not in VALID_ACCESS:
                access = "none"
            allowed = _clamp(access, ceiling[module])
            if allowed != access:
                capped.append(module)
            rows.append((role_key, module, allowed))
        cur.executemany(
            "INSERT INTO role_permissions (role_key, module, access) VALUES (%s,%s,%s) "
            "ON CONFLICT (role_key, module) DO UPDATE SET access=EXCLUDED.access",
            rows,
        )
        lowered = _cascade_down(cur, role_key)
        if own and commit:
            db.commit()
        return capped, lowered
    finally:
        if own:
            cur.close()


def set_all_permissions(grid):
    """Save the whole access matrix ({role_key: {module: access}}) in one
    transaction, parents before children so each role is capped by its
    parent's new access. Returns (capped, lowered) like set_permissions(),
    with capped as [(role display name, module)]."""
    order = [r for r in role_tree() if r["role_key"] in grid]
    db = get_db_connection()
    cur = db.cursor()
    capped_all, lowered_all = [], []
    try:
        for r in order:
            capped, lowered = set_permissions(r["role_key"], grid[r["role_key"]], cur=cur)
            capped_all += [(r["display_name"], m) for m in capped]
            lowered_all += [n for n in lowered if n not in lowered_all]
        db.commit()
        return capped_all, lowered_all
    finally:
        cur.close()


# ── People hierarchy (Settings -> Roles & Access -> Hierarchy) ────────────────
def _up_links(emp):
    """The people an employee is attached to: Reporting manager first, then
    Assigned HR -- the same two links the 'team' data scope follows."""
    return [p for p in (emp["manager_id"], emp["assigned_hr"]) if p and p != emp["employee_id"]]


def people_hierarchy():
    """Who holds which role and who is under them, for the Hierarchy page.

    Returns {"roots": [node], "unattached": [employee], "holders": n,
    "total": active employees} where a
    node is a role-holder: their employee fields + role_key, role_name,
    data_scope, 'children' (role-holder nodes whose nearest role-holder up
    the line is this person) and 'members' (plain employees whose nearest
    role-holder is this person). An employee's nearest role-holder is found
    by walking up their Reporting manager / Assigned HR links (any depth,
    shortest path, Reporting manager first) -- exactly the people a 'team'
    role sees. Role-holders with nobody above them are roots (directly under
    the Administrator); plain employees with nobody above them are listed as
    unattached (only whole-company roles see them)."""
    roles = {r["role_key"]: r for r in list_roles() if not r["is_system"]}
    with _db() as (cur, conn):
        cur.execute(
            "SELECT employee_id, name, COALESCE(department,''), COALESCE(role,''), COALESCE(access_role,''), "
            "manager_id, assigned_hr_username FROM employees WHERE COALESCE(is_active,1)=1 ORDER BY name"
        )
        emps = {
            r[0]: {"employee_id": r[0], "name": r[1], "department": r[2], "title": r[3],
                   "access_role": r[4], "manager_id": r[5], "assigned_hr": r[6]}
            for r in cur.fetchall()
        }
    holders = {eid for eid, e in emps.items() if e["access_role"] in roles}

    def nearest_holder(eid):
        frontier, seen = list(_up_links(emps[eid])), {eid}
        while frontier:
            nxt = []
            for p in frontier:
                if p in seen or p not in emps:
                    continue
                seen.add(p)
                if p in holders:
                    return p
                nxt += _up_links(emps[p])
            frontier = nxt
        return None

    nodes = {}
    for eid in holders:
        e = emps[eid]
        role = roles[e["access_role"]]
        nodes[eid] = dict(e, role_key=role["role_key"], role_name=role["display_name"],
                          data_scope=role["data_scope"], children=[], members=[])
    roots, unattached = [], []
    for eid, e in emps.items():
        boss = nearest_holder(eid)
        if eid in holders:
            (nodes[boss]["children"] if boss else roots).append(nodes[eid])
        elif boss:
            nodes[boss]["members"].append(e)
        else:
            unattached.append(e)

    def total(node):
        node["team_size"] = len(node["members"]) + sum(1 + total(c) for c in node["children"])
        return node["team_size"]

    for n in roots:
        total(n)
    return {"roots": roots, "unattached": unattached, "holders": len(holders), "total": len(emps)}


def team_of(employee_id):
    """employee_ids attached to employee_id (Reporting manager or Assigned
    HR) at any depth, not including themself."""
    from .scoping import TEAM_SUBQUERY
    with _db() as (cur, conn):
        cur.execute(f"SELECT id FROM ({TEAM_SUBQUERY}) t", (employee_id, employee_id))  # nosec B608 -- fixed SQL
        return {r[0] for r in cur.fetchall()} - {employee_id}


def attach_to(holder_id, employee_id):
    """Put employee_id in holder_id's team by making holder_id their Reporting
    manager. Refuses anyone above holder_id (that would loop). Returns the
    employee's name. Raises ValueError on bad input."""
    if not holder_id or not employee_id or holder_id == employee_id:
        raise ValueError("Choose an employee.")
    if holder_id in team_of(employee_id):
        raise ValueError("That person is above them in the hierarchy, so they can't also be in their team.")
    with _db() as (cur, conn):
        cur.execute("SELECT name FROM employees WHERE employee_id=%s", (employee_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Employee not found.")
        cur.execute("UPDATE employees SET manager_id=%s WHERE employee_id=%s", (holder_id, employee_id))
        conn.commit()
        return row[0]


def detach_from(holder_id, employee_id):
    """Remove employee_id from holder_id's team: clears their Reporting
    manager if it is holder_id. (An Assigned HR link is managed on the
    Organization page's HR team tab.) Returns the employee's name."""
    with _db() as (cur, conn):
        cur.execute("SELECT name FROM employees WHERE employee_id=%s AND manager_id=%s", (employee_id, holder_id))
        row = cur.fetchone()
        if not row:
            raise ValueError("They don't report to this person.")
        cur.execute("UPDATE employees SET manager_id=NULL WHERE employee_id=%s", (employee_id,))
        conn.commit()
        return row[0]
