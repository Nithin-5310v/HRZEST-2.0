"""Roles & Access (utils.helpers.rbac): only Administrator and Employee are
fixed dashboards; HR, Manager and every other role is one the company creates
and assigns. A company seeded before that keeps its old 'hr'/'manager' roles
-- and the people using them -- as ordinary custom roles
(migrations/column_migrations.py's _run_default_roles_to_custom_migration)."""
import pytest

_ADMIN_ONLY = {"payroll", "reports", "admin_tools", "organization"}


def _perms(db_engine, role_key):
    cur = db_engine.cursor()
    cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (role_key,))
    rows = dict(cur.fetchall())
    cur.close()
    return rows


def _migrate(db_engine):
    from migrations.column_migrations import _run_default_roles_to_custom_migration
    _run_default_roles_to_custom_migration(db_engine.cursor(), db_engine)


def test_only_admin_and_employee_are_system_roles(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT role_key FROM roles WHERE is_system")
    assert {r[0] for r in cur.fetchall()} == {"admin", "employee"}
    cur.close()


def test_admin_and_employee_cannot_be_deleted(client, seed_admin):
    from app import app
    from utils.helpers.rbac import delete_role
    with app.test_request_context():
        for role_key in ("admin", "employee"):
            with pytest.raises(ValueError, match="System roles"):
                delete_role(role_key)


def test_old_system_hr_role_becomes_custom_and_keeps_its_people(db_engine, seed_employee):
    emp_id = seed_employee["employee_id"]
    cur = db_engine.cursor()
    cur.execute("INSERT INTO roles (role_key, display_name, base_type, is_system) "
                "VALUES ('hr','HR','hr',TRUE) ON CONFLICT (role_key) DO UPDATE SET is_system=TRUE")
    cur.execute("INSERT INTO admin_users (username, password, role, is_active) VALUES (%s,'x','hr',1)", (emp_id,))
    _migrate(db_engine)
    cur.execute("SELECT is_system FROM roles WHERE role_key='hr'")
    assert cur.fetchone()[0] is False
    cur.execute("SELECT access_role FROM employees WHERE employee_id=%s", (emp_id,))
    assert cur.fetchone()[0] == "hr"
    cur.close()


def test_switched_off_logins_are_not_given_the_role(db_engine, seed_employee):
    emp_id = seed_employee["employee_id"]
    cur = db_engine.cursor()
    cur.execute("INSERT INTO roles (role_key, display_name, base_type, is_system) "
                "VALUES ('hr','HR','hr',FALSE) ON CONFLICT (role_key) DO NOTHING")
    cur.execute("INSERT INTO admin_users (username, password, role, is_active) VALUES (%s,'x','hr',0)", (emp_id,))
    _migrate(db_engine)
    cur.execute("SELECT access_role FROM employees WHERE employee_id=%s", (emp_id,))
    assert cur.fetchone()[0] is None
    cur.close()


def test_missing_legacy_roles_are_recreated_with_their_old_access(db_engine):
    """A company with HR/Manager logins but no role rows for them must not
    lock those logins out of every module (can_access denies by default)."""
    from utils.helpers.rbac import RBAC_MODULES
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET access_role=NULL WHERE access_role IN ('hr','manager')")
    cur.execute("UPDATE admin_users SET role='admin' WHERE role IN ('hr','manager')")
    cur.execute("DELETE FROM roles WHERE role_key IN ('hr','manager')")
    cur.execute("INSERT INTO admin_users (username, password, role) VALUES ('legacy_hr','x','hr'), "
                "('legacy_mgr','x','manager')")
    _migrate(db_engine)
    cur.execute("SELECT role_key, base_type, is_system FROM roles WHERE role_key IN ('hr','manager')")
    assert {r[0]: (r[1], r[2]) for r in cur.fetchall()} == {"hr": ("hr", False), "manager": ("manager", False)}
    cur.close()
    hr, manager = _perms(db_engine, "hr"), _perms(db_engine, "manager")
    for module in RBAC_MODULES:
        assert hr[module] == ("none" if module in _ADMIN_ONLY else "edit"), module
        denied = module in _ADMIN_ONLY or module == "announcements"
        assert manager[module] == ("none" if denied else "edit"), module


def test_no_legacy_roles_are_made_for_a_company_without_those_logins(db_engine):
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET access_role=NULL WHERE access_role IN ('hr','manager')")
    cur.execute("UPDATE admin_users SET role='admin' WHERE role IN ('hr','manager')")
    cur.execute("DELETE FROM roles WHERE role_key IN ('hr','manager')")
    _migrate(db_engine)
    cur.execute("SELECT COUNT(*) FROM roles WHERE role_key IN ('hr','manager')")
    assert cur.fetchone()[0] == 0
    cur.close()
