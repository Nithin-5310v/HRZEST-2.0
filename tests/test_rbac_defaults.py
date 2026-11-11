"""Roles & Access (utils.helpers.rbac) seeds the built-in HR and Manager roles
with the access they had before per-module permissions existed. Without these
rows can_access() denies every module, and every existing HR/Manager account
(admin_users.role 'hr'/'manager') loses its pages the moment it deploys."""
import pytest

_ADMIN_ONLY = {"payroll", "reports", "admin_tools", "organization"}


def _perms(db_engine, role_key):
    cur = db_engine.cursor()
    cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (role_key,))
    rows = dict(cur.fetchall())
    cur.close()
    return rows


def test_hr_and_manager_are_seeded_system_roles(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT role_key, base_type, is_system FROM roles WHERE role_key IN ('hr','manager')")
    rows = {rk: (bt, sysrow) for rk, bt, sysrow in cur.fetchall()}
    cur.close()
    assert rows == {"hr": ("hr", True), "manager": ("manager", True)}


def test_hr_keeps_its_modules_and_stays_out_of_admin_only_ones(db_engine):
    from utils.helpers.rbac import RBAC_MODULES
    perms = _perms(db_engine, "hr")
    assert set(perms) == set(RBAC_MODULES)
    for module in RBAC_MODULES:
        assert perms[module] == ("none" if module in _ADMIN_ONLY else "edit"), module


def test_manager_keeps_its_modules_but_not_announcements(db_engine):
    from utils.helpers.rbac import RBAC_MODULES
    perms = _perms(db_engine, "manager")
    assert set(perms) == set(RBAC_MODULES)
    for module in RBAC_MODULES:
        denied = module in _ADMIN_ONLY or module == "announcements"
        assert perms[module] == ("none" if denied else "edit"), module


def test_built_in_hr_and_manager_cannot_be_deleted(client, seed_admin):
    from app import app
    from utils.helpers.rbac import delete_role
    with app.test_request_context():
        for role_key in ("hr", "manager"):
            with pytest.raises(ValueError, match="System roles"):
                delete_role(role_key)
