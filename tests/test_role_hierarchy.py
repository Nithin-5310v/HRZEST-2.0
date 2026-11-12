"""Hierarchical Roles & Access (utils.helpers.rbac, utils.helpers.scoping,
blueprints/admin_views/settings.py).

The company in these tests:

    Administrator
    └── Senior Manager  (role, team)      SNR01
        ├── Junior Manager (role, team)   JNR01 ── EMPA1, EMPA2
        └── Junior Manager (role, team)   JNR02 ── EMPB1
    EMPZ1 (attached to no one)

A role never has more access than the role it reports to; a 'team' role sees
only the people under that person (any depth); the Administrator sees all.
"""
import pytest

from utils.helpers.rbac import RBAC_MODULES


def _admin(client, username="test_admin"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = "admin"


def _as(client, username, role, base="manager"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role
        sess["admin_base_type"] = base


def _perms(db_engine, role_key):
    cur = db_engine.cursor()
    cur.execute("SELECT module, access FROM role_permissions WHERE role_key=%s", (role_key,))
    out = dict(cur.fetchall())
    cur.close()
    return out


def _parent(db_engine, role_key):
    cur = db_engine.cursor()
    cur.execute("SELECT parent_role_key FROM roles WHERE role_key=%s", (role_key,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else "missing"


@pytest.fixture
def roles(client, seed_admin):
    """Senior Manager (top level) > Junior Manager, both 'team'."""
    from utils.helpers.rbac import create_role, set_permissions
    with client.application.test_request_context():
        senior = create_role("Senior Manager", "manager", "", "Leads the managers", "team")
        junior = create_role("Junior Manager", "manager", senior, "", "team")
        set_permissions(senior, {"employees": "edit", "leaves": "edit", "payroll": "view"})
        set_permissions(junior, {"employees": "view", "leaves": "edit"})
    return {"senior": senior, "junior": junior}


@pytest.fixture
def company(db_engine, client, roles):
    """The people above. Returns their employee_ids."""
    from utils.helpers.rbac import assign_employee_role
    cur = db_engine.cursor()
    people = [("SNR01", None), ("JNR01", "SNR01"), ("JNR02", "SNR01"),
              ("EMPA1", "JNR01"), ("EMPA2", "JNR01"), ("EMPB1", "JNR02"), ("EMPZ1", None)]
    for emp_id, manager in people:
        cur.execute("INSERT INTO employees (employee_id, name, password, force_pin_change, manager_id) "
                    "VALUES (%s,%s,'x',0,%s)", (emp_id, f"Person {emp_id}", manager))
    db_engine.commit()
    cur.close()
    with client.application.test_request_context():
        assign_employee_role("SNR01", roles["senior"])
        assign_employee_role("JNR01", roles["junior"])
        assign_employee_role("JNR02", roles["junior"])
    return [p[0] for p in people]


# ── The access ceiling ──────────────────────────────────────────────────────
class TestCeiling:
    def test_child_is_capped_at_its_parent(self, client, db_engine, roles):
        from utils.helpers.rbac import set_permissions
        with client.application.test_request_context():
            capped, _ = set_permissions(roles["junior"], {"payroll": "edit", "admin_tools": "edit", "leaves": "edit"})
        perms = _perms(db_engine, roles["junior"])
        assert perms["payroll"] == "view"       # Senior has View
        assert perms["admin_tools"] == "none"   # Senior has none
        assert perms["leaves"] == "edit"
        assert set(capped) == {"payroll", "admin_tools"}

    def test_lowering_a_parent_lowers_everything_below(self, client, db_engine, roles):
        from utils.helpers.rbac import set_permissions
        with client.application.test_request_context():
            _, lowered = set_permissions(roles["senior"], {"employees": "view"})
        assert _perms(db_engine, roles["senior"])["leaves"] == "none"
        junior = _perms(db_engine, roles["junior"])
        assert junior["leaves"] == "none" and junior["employees"] == "view"
        assert lowered == ["Junior Manager"]

    def test_top_level_role_can_get_anything(self, client, db_engine, roles):
        from utils.helpers.rbac import set_permissions
        with client.application.test_request_context():
            capped, _ = set_permissions(roles["senior"], {m: "edit" for m in RBAC_MODULES})
        assert capped == [] and set(_perms(db_engine, roles["senior"]).values()) == {"edit"}

    def test_moving_under_a_weaker_parent_lowers_the_role(self, client, db_engine, roles):
        from utils.helpers.rbac import create_role, update_role
        with client.application.test_request_context():
            weak = create_role("Viewer Lead", "manager", "", "", "company")   # no access at all
            lowered = update_role(roles["junior"], "Junior Manager", weak, "", "team")
        assert set(_perms(db_engine, roles["junior"]).values()) == {"none"}
        assert "Junior Manager" in lowered

    def test_matrix_saves_parents_first_and_caps_children(self, client, db_engine, roles):
        _admin(client)
        form = {f"row_{roles['senior']}": "1", f"row_{roles['junior']}": "1"}
        form.update({f"cell_{roles['senior']}__{m}": "none" for m in RBAC_MODULES})
        form[f"cell_{roles['senior']}__leaves"] = "view"
        form.update({f"cell_{roles['junior']}__{m}": "none" for m in RBAC_MODULES})
        form[f"cell_{roles['junior']}__leaves"] = "edit"          # more than the new parent
        resp = client.post("/roles/matrix", data=form)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/roles/access")
        assert _perms(db_engine, roles["senior"])["leaves"] == "view"
        assert _perms(db_engine, roles["junior"])["leaves"] == "view"


# ── The role tree ───────────────────────────────────────────────────────────
class TestTree:
    def test_role_tree_orders_parents_first(self, client, roles):
        from utils.helpers.rbac import role_tree
        with client.application.test_request_context():
            tree = [r for r in role_tree() if r["role_key"] in roles.values()]
        assert [(r["role_key"], r["depth"]) for r in tree] == [(roles["senior"], 0), (roles["junior"], 1)]
        assert tree[1]["ancestors"] == [roles["senior"]]

    @pytest.mark.parametrize("bad_parent", ["self", "child"])
    def test_no_loops(self, client, roles, bad_parent):
        from utils.helpers.rbac import update_role
        parent = roles["senior"] if bad_parent == "self" else roles["junior"]
        with client.application.test_request_context(), pytest.raises(ValueError, match="itself or to a role below"):
            update_role(roles["senior"], "Senior Manager", parent, "", "team")

    def test_deleting_a_role_moves_its_children_up(self, client, db_engine, roles):
        from utils.helpers.rbac import create_role, delete_role
        with client.application.test_request_context():
            middle = create_role("Middle", "manager", roles["senior"], "", "team")
            leaf = create_role("Leaf", "manager", middle, "", "team")
            delete_role(middle)
        assert _parent(db_engine, middle) == "missing"
        assert _parent(db_engine, leaf) == roles["senior"]

    def test_a_role_in_use_cannot_be_deleted(self, client, company, roles):
        from utils.helpers.rbac import delete_role
        with client.application.test_request_context(), pytest.raises(ValueError, match="still assigned"):
            delete_role(roles["junior"])


# ── Who sees whom (data scope) ──────────────────────────────────────────────
class TestTeamScope:
    def _denied(self, client, me, role, emp_id):
        from utils.helpers import hr_scope_denied
        with client.application.test_request_context():
            from flask import session
            session["admin_role"] = role
            session["admin_username"] = me
            return hr_scope_denied(emp_id)

    def test_senior_sees_juniors_and_everyone_under_them(self, client, company, roles):
        for emp in ("JNR01", "JNR02", "EMPA1", "EMPA2", "EMPB1"):
            assert self._denied(client, "SNR01", roles["senior"], emp) is False, emp
        assert self._denied(client, "SNR01", roles["senior"], "EMPZ1") is True

    def test_junior_sees_only_their_assigned_employees(self, client, company, roles):
        assert self._denied(client, "JNR01", roles["junior"], "EMPA1") is False
        assert self._denied(client, "JNR01", roles["junior"], "EMPA2") is False
        for emp in ("EMPB1", "JNR02", "SNR01", "EMPZ1"):
            assert self._denied(client, "JNR01", roles["junior"], emp) is True, emp

    def test_whole_company_role_sees_everyone(self, client, company, roles, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE roles SET data_scope='company' WHERE role_key=%s", (roles["junior"],))
        db_engine.commit()
        cur.close()
        for emp in ("EMPB1", "EMPZ1", "SNR01"):
            assert self._denied(client, "JNR01", roles["junior"], emp) is False, emp

    def test_admin_sees_everyone(self, client, company):
        assert self._denied(client, "test_admin", "admin", "EMPZ1") is False

    def test_a_real_page_follows_the_scope(self, client, company, roles):
        _as(client, "JNR01", roles["junior"])
        assert client.get("/employee_detail/EMPA1").status_code == 200
        assert client.get("/employee_detail/EMPB1").status_code == 404

    def test_people_hierarchy_matches_the_scope(self, client, company, roles):
        from utils.helpers.rbac import people_hierarchy
        with client.application.test_request_context():
            tree = people_hierarchy()
        senior = next(n for n in tree["roots"] if n["employee_id"] == "SNR01")
        assert sorted(c["employee_id"] for c in senior["children"]) == ["JNR01", "JNR02"]
        jnr01 = next(c for c in senior["children"] if c["employee_id"] == "JNR01")
        assert sorted(m["employee_id"] for m in jnr01["members"]) == ["EMPA1", "EMPA2"]
        assert senior["team_size"] == 5
        assert "EMPZ1" in [e["employee_id"] for e in tree["unattached"]]


# ── The pages and forms ─────────────────────────────────────────────────────
class TestPages:
    @pytest.mark.parametrize("path", ["/settings/roles", "/settings/roles/list", "/settings/roles/access",
                                      "/settings/roles/assignments"])
    def test_pages_open_for_the_admin(self, client, company, path):
        _admin(client)
        resp = client.get(path)
        assert resp.status_code == 200 and b"Roles &amp; Access" in resp.data

    def test_hierarchy_page_shows_people_and_their_employees(self, client, company):
        _admin(client)
        html = client.get("/settings/roles").get_data(as_text=True)
        for text in ("Senior Manager", "Junior Manager", "Person SNR01", "Person JNR01", "Person EMPA1"):
            assert text in html, text

    def test_role_page_locks_options_above_the_parent(self, client, company, roles):
        _admin(client)
        html = client.get(f"/settings/roles/role/{roles['junior']}").get_data(as_text=True)
        assert "Limited by <b>Senior Manager</b>" in html
        # Senior Manager has only View on Payroll, so Edit is locked for Junior.
        import re
        assert re.search(r'id="p_payroll_edit"[^>]*disabled', html)
        assert not re.search(r'id="p_leaves_edit"[^>]*disabled', html)

    def test_team_page(self, client, company):
        _admin(client)
        html = client.get("/settings/roles/team/JNR01").get_data(as_text=True)
        assert "Person EMPA1" in html and "Person EMPB1" not in html.split("Add to")[0]

    @pytest.mark.parametrize("path", ["/settings/roles", "/settings/roles/list", "/settings/roles/access",
                                      "/settings/roles/assignments", "/settings/roles/team/JNR01"])
    def test_role_holders_cannot_open_them(self, client, company, roles, path):
        _as(client, "SNR01", roles["senior"])
        assert client.get(path).status_code in (302, 403)

    def test_create_role_with_parent_and_scope(self, client, db_engine, roles):
        _admin(client)
        resp = client.post("/roles/create", data={
            "display_name": "Team Lead", "base_type": "manager",
            "parent_role_key": roles["junior"], "data_scope": "team"})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/roles/role/team_lead")
        assert _parent(db_engine, "team_lead") == roles["junior"]

    def test_assign_with_reports_to(self, client, db_engine, company, roles):
        _admin(client)
        client.post("/roles/assign", data={"employee_id": "EMPZ1", "role_key": roles["junior"],
                                           "reports_to": "SNR01"})
        cur = db_engine.cursor()
        cur.execute("SELECT access_role, manager_id FROM employees WHERE employee_id='EMPZ1'")
        assert cur.fetchone() == (roles["junior"], "SNR01")
        cur.close()

    def test_attach_and_detach(self, client, db_engine, company):
        _admin(client)
        client.post("/roles/team/attach", data={"holder_id": "JNR02", "employee_id": "EMPZ1"})
        cur = db_engine.cursor()
        cur.execute("SELECT manager_id FROM employees WHERE employee_id='EMPZ1'")
        assert cur.fetchone()[0] == "JNR02"
        client.post("/roles/team/detach", data={"holder_id": "JNR02", "employee_id": "EMPZ1"})
        cur.execute("SELECT manager_id FROM employees WHERE employee_id='EMPZ1'")
        assert cur.fetchone()[0] is None
        cur.close()

    def test_attach_refuses_a_loop(self, client, db_engine, company):
        _admin(client)
        resp = client.post("/roles/team/attach", data={"holder_id": "JNR01", "employee_id": "SNR01"},
                           follow_redirects=True)
        assert b"above them in the hierarchy" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT manager_id FROM employees WHERE employee_id='SNR01'")
        assert cur.fetchone()[0] is None
        cur.close()
