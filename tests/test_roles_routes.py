"""Roles & Access form actions (blueprints/admin_views/settings.py):
POST /roles/update, /roles/rename, /roles/delete and /roles/permissions.
Owner-admin only; tests/test_role_hierarchy.py covers create/assign/team
and the pages themselves."""
import pytest

from utils.helpers.rbac import RBAC_MODULES

ROUTES = ("/roles/update", "/roles/rename", "/roles/delete", "/roles/permissions")


def _session(client, username="test_admin", role="admin"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _role(db_engine, role_key):
    cur = db_engine.cursor()
    cur.execute("SELECT display_name, COALESCE(parent_role_key,''), COALESCE(description,''), data_scope "
                "FROM roles WHERE role_key=%s", (role_key,))
    row = cur.fetchone()
    cur.close()
    return row


def _perms(db_engine, role_key):
    cur = db_engine.cursor()
    cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (role_key,))
    out = dict(cur.fetchall())
    cur.close()
    return out


@pytest.fixture
def roles(client, seed_admin):
    """Route Lead (top level, employees=edit, payroll=view) > Route Member."""
    from utils.helpers.rbac import create_role, set_permissions
    with client.application.test_request_context():
        lead = create_role("Route Lead", "custom", "", "", "company")
        member = create_role("Route Member", "custom", lead, "", "team")
        set_permissions(lead, {"employees": "edit", "payroll": "view"})
        set_permissions(member, {"employees": "edit", "payroll": "view"})
    return {"lead": lead, "member": member}


class TestGuards:
    @pytest.mark.parametrize("path", ROUTES)
    def test_signed_out_is_sent_to_login(self, client, path):
        resp = client.post(path, data={"role_key": "x"})
        assert resp.status_code == 302 and "login" in resp.headers["Location"]

    @pytest.mark.parametrize("path", ROUTES)
    def test_a_non_owner_role_changes_nothing(self, client, db_engine, seed_hr_admin, roles, path):
        _session(client, seed_hr_admin["username"], "hr")
        before = (_role(db_engine, roles["lead"]), _perms(db_engine, roles["lead"]))
        resp = client.post(path, data={"role_key": roles["lead"], "display_name": "Hijacked",
                                       "perm_payroll": "edit", "data_scope": "company"})
        assert resp.status_code in (302, 403)
        assert (_role(db_engine, roles["lead"]), _perms(db_engine, roles["lead"])) == before


class TestUpdateRole:
    def test_saves_name_description_and_scope(self, client, db_engine, roles):
        _session(client)
        resp = client.post("/roles/update", data={
            "role_key": roles["lead"], "display_name": "Route Lead Renamed", "parent_role_key": "",
            "description": "Leads the route team", "data_scope": "team"})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/settings/roles/role/{roles['lead']}")
        assert _role(db_engine, roles["lead"]) == ("Route Lead Renamed", "", "Leads the route team", "team")

    def test_moving_under_a_parent_lowers_access_to_fit(self, client, db_engine, roles):
        """Route Member taken out from under Route Lead, then put under a new
        role with less access, drops to that role's level."""
        from utils.helpers.rbac import create_role, set_permissions
        with client.application.test_request_context():
            narrow = create_role("Route Narrow", "custom", "", "", "company")
            set_permissions(narrow, {"employees": "view"})
        _session(client)
        client.post("/roles/update", data={"role_key": roles["member"], "display_name": "Route Member",
                                           "parent_role_key": narrow, "data_scope": "team"})
        assert _role(db_engine, roles["member"])[1] == narrow
        perms = _perms(db_engine, roles["member"])
        assert perms["employees"] == "view" and perms["payroll"] == "none"

    @pytest.mark.parametrize("data", [
        {"role_key": "admin", "display_name": "Boss", "data_scope": "company"},          # built-in
        {"role_key": "no_such_role", "display_name": "Ghost", "data_scope": "company"},
        {"role_key": "LEAD", "display_name": "", "data_scope": "company"},                # name required
        {"role_key": "LEAD", "display_name": "Route Lead", "data_scope": "everyone"},     # bad scope
    ])
    def test_refusals_change_nothing(self, client, db_engine, roles, data):
        data = dict(data, role_key=roles["lead"] if data["role_key"] == "LEAD" else data["role_key"])
        before = {k: _role(db_engine, k) for k in ("admin", roles["lead"])}
        _session(client)
        resp = client.post("/roles/update", data=data, follow_redirects=True)
        assert resp.status_code == 200
        assert {k: _role(db_engine, k) for k in ("admin", roles["lead"])} == before
        assert _role(db_engine, "no_such_role") is None


class TestRenameRole:
    def test_renames_a_custom_role(self, client, db_engine, roles):
        _session(client)
        resp = client.post("/roles/rename", data={"role_key": roles["member"], "display_name": "Route Crew"})
        assert resp.status_code == 302
        assert _role(db_engine, roles["member"])[0] == "Route Crew"

    def test_a_built_in_role_can_be_renamed(self, client, db_engine, seed_admin):
        _session(client)
        client.post("/roles/rename", data={"role_key": "employee", "display_name": "Team Member"})
        assert _role(db_engine, "employee")[0] == "Team Member"

    def test_a_blank_name_is_refused(self, client, db_engine, roles):
        _session(client)
        resp = client.post("/roles/rename", data={"role_key": roles["member"], "display_name": "   "},
                           follow_redirects=True)
        assert b"Role name is required" in resp.data
        assert _role(db_engine, roles["member"])[0] == "Route Member"


class TestDeleteRole:
    def test_deletes_an_unassigned_role_and_moves_its_children_up(self, client, db_engine, roles):
        _session(client)
        resp = client.post("/roles/delete", data={"role_key": roles["lead"]})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/roles/list")
        assert _role(db_engine, roles["lead"]) is None
        assert _perms(db_engine, roles["lead"]) == {}
        assert _role(db_engine, roles["member"])[1] == ""      # now directly under the Administrator

    def test_a_role_still_assigned_is_kept(self, client, db_engine, roles, seed_employee):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET access_role=%s WHERE employee_id='TST001'", (roles["member"],))
        cur.close()
        _session(client)
        resp = client.post("/roles/delete", data={"role_key": roles["member"]}, follow_redirects=True)
        assert b"still assigned" in resp.data
        assert _role(db_engine, roles["member"]) is not None

    @pytest.mark.parametrize("role_key", ["admin", "employee"])
    def test_built_in_roles_are_kept(self, client, db_engine, seed_admin, role_key):
        _session(client)
        resp = client.post("/roles/delete", data={"role_key": role_key}, follow_redirects=True)
        assert b"System roles can" in resp.data
        assert _role(db_engine, role_key) is not None

    def test_an_unknown_role_is_reported(self, client, seed_admin):
        _session(client)
        resp = client.post("/roles/delete", data={"role_key": "no_such_role"}, follow_redirects=True)
        assert b"Role not found" in resp.data


class TestSavePermissions:
    def test_saves_the_grid(self, client, db_engine, roles):
        _session(client)
        form = {"role_key": roles["lead"], "perm_employees": "view", "perm_leaves": "edit",
                "perm_not_a_module": "edit"}
        resp = client.post("/roles/permissions", data=form)
        assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/settings/roles/role/{roles['lead']}")
        perms = _perms(db_engine, roles["lead"])
        assert set(perms) == set(RBAC_MODULES)
        assert (perms["employees"], perms["leaves"], perms["payroll"]) == ("view", "edit", "none")

    def test_an_invalid_level_counts_as_none(self, client, db_engine, roles):
        _session(client)
        client.post("/roles/permissions", data={"role_key": roles["lead"], "perm_employees": "superuser"})
        assert _perms(db_engine, roles["lead"])["employees"] == "none"

    def test_a_child_is_capped_at_its_parent(self, client, db_engine, roles):
        _session(client)
        resp = client.post("/roles/permissions", data={"role_key": roles["member"], "perm_payroll": "edit",
                                                       "perm_employees": "edit"}, follow_redirects=True)
        assert b"most the role it reports to allows" in resp.data
        perms = _perms(db_engine, roles["member"])
        assert (perms["payroll"], perms["employees"]) == ("view", "edit")

    def test_lowering_a_parent_lowers_its_children(self, client, db_engine, roles):
        _session(client)
        resp = client.post("/roles/permissions", data={"role_key": roles["lead"], "perm_employees": "view"},
                           follow_redirects=True)
        assert b"Roles below it were lowered" in resp.data
        perms = _perms(db_engine, roles["member"])
        assert (perms["employees"], perms["payroll"]) == ("view", "none")

    def test_the_owner_admin_role_stays_full_access(self, client, db_engine, seed_admin):
        _session(client)
        client.post("/roles/permissions", data={"role_key": "admin"})       # every module left at "none"
        assert set(_perms(db_engine, "admin").values()) == {"edit"}

    def test_an_unknown_role_is_reported(self, client, seed_admin):
        _session(client)
        resp = client.post("/roles/permissions", data={"role_key": "no_such_role"}, follow_redirects=True)
        assert b"Role not found" in resp.data
