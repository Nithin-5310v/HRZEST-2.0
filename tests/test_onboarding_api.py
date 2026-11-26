"""The app's onboarding APIs (blueprints/onboarding.py): GET /api/onboarding,
GET /api/onboarding/<ob_id>/tasks and POST /api/onboarding/task/<task_id>/update.
Admin token, gated by the role's Onboarding access, and -- like the web
pages -- limited to the caller's own team for a team-scoped role."""
import hashlib
import secrets

import pytest


def _bearer(db_engine, identity, token_type="admin"):
    raw = secrets.token_hex(32)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s, %s, %s, NOW() + INTERVAL '1 hour')",
        (identity, hashlib.sha256(raw.encode()).hexdigest(), token_type),
    )
    cur.close()
    return {"Authorization": f"Bearer {raw}"}


def _custom_role(db_engine, role_key, scope, onboarding_access):
    from utils.helpers.rbac import RBAC_MODULES
    cur = db_engine.cursor()
    cur.execute("INSERT INTO roles (role_key, display_name, base_type, is_system, data_scope) "
                "VALUES (%s, %s, 'custom', FALSE, %s)", (role_key, role_key.title(), scope))
    cur.executemany("INSERT INTO role_permissions (role_key, module, access) VALUES (%s, %s, %s)",
                    [(role_key, m, onboarding_access if m == "onboarding" else "none") for m in RBAC_MODULES])
    cur.close()


def _admin_user(db_engine, username, role):
    from utils.auth import generate_password_hash
    cur = db_engine.cursor()
    cur.execute("INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,1)",
                (username, generate_password_hash("Test@1234"), role, f"{username.lower()}@test.local"))
    cur.close()


def _onboarding(db_engine, employee_id, task_statuses):
    """An onboarding for employee_id with one task per status; returns (ob_id, [task ids])."""
    cur = db_engine.cursor()
    cur.execute("INSERT INTO onboarding_templates (name, description, is_active) VALUES (%s, '', 1) RETURNING id",
                (f"Route Template {employee_id}",))
    template_id = cur.fetchone()[0]
    cur.execute("INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date, status) "
                "VALUES (%s, %s, NOW(), NOW() + INTERVAL '30 days', 'In Progress') RETURNING id",
                (employee_id, template_id))
    ob_id = cur.fetchone()[0]
    task_ids = []
    for n, status in enumerate(task_statuses, 1):
        cur.execute("INSERT INTO employee_onboarding_tasks (onboarding_id, template_task_id, employee_id, task_title, "
                    "task_description, status) VALUES (%s, 0, %s, %s, 'Do it', %s) RETURNING id",
                    (ob_id, employee_id, f"Route task {n}", status))
        task_ids.append(cur.fetchone()[0])
    cur.close()
    return ob_id, task_ids


def _task(db_engine, task_id):
    cur = db_engine.cursor()
    cur.execute("SELECT status, admin_notes, completed_at FROM employee_onboarding_tasks WHERE id=%s", (task_id,))
    row = cur.fetchone()
    cur.close()
    return row


def _ob_status(db_engine, ob_id):
    cur = db_engine.cursor()
    cur.execute("SELECT status FROM employee_onboarding WHERE id=%s", (ob_id,))
    row = cur.fetchone()
    cur.close()
    return row[0]


@pytest.fixture
def team(db_engine, seed_employee):
    """TEAMLEAD1 holds a team-scoped role with Onboarding edit access;
    RTEAM01 reports to them, TST001 does not."""
    _custom_role(db_engine, "route_team_onb", "team", "edit")
    _admin_user(db_engine, "TEAMLEAD1", "route_team_onb")
    cur = db_engine.cursor()
    cur.execute("INSERT INTO employees (employee_id, name, email, password, manager_id, is_active) "
                "VALUES ('RTEAM01', 'Route Team Member', 'rteam01@test.local', 'x', 'TEAMLEAD1', 1)")
    cur.close()
    inside = _onboarding(db_engine, "RTEAM01", ["Pending"])
    outside = _onboarding(db_engine, "TST001", ["Pending", "Done"])
    return {"headers": _bearer(db_engine, "TEAMLEAD1"), "inside": inside, "outside": outside}


URLS = [("get", "/api/onboarding"), ("get", "/api/onboarding/1/tasks"),
        ("post", "/api/onboarding/task/1/update")]


class TestGuards:
    @pytest.mark.parametrize("method,url", URLS)
    def test_needs_an_admin_token(self, client, db_engine, seed_employee, method, url):
        assert getattr(client, method)(url, json={}).status_code == 401
        emp = _bearer(db_engine, seed_employee["employee_id"], "employee")
        assert getattr(client, method)(url, json={}, headers=emp).status_code == 401

    @pytest.mark.parametrize("method,url", URLS)
    def test_a_role_without_onboarding_access_is_refused(self, client, db_engine, method, url):
        _custom_role(db_engine, "route_no_onb", "company", "none")
        _admin_user(db_engine, "NOONB1", "route_no_onb")
        resp = getattr(client, method)(url, json={"status": "Done"}, headers=_bearer(db_engine, "NOONB1"))
        assert resp.status_code == 403

    def test_view_access_cannot_update(self, client, db_engine, seed_employee):
        _custom_role(db_engine, "route_view_onb", "company", "view")
        _admin_user(db_engine, "VIEWONB1", "route_view_onb")
        ob_id, [task_id] = _onboarding(db_engine, "TST001", ["Pending"])
        headers = _bearer(db_engine, "VIEWONB1")
        assert client.get(f"/api/onboarding/{ob_id}/tasks", headers=headers).status_code == 200
        resp = client.post(f"/api/onboarding/task/{task_id}/update", json={"status": "Done"}, headers=headers)
        assert resp.status_code == 403
        assert _task(db_engine, task_id)[0] == "Pending"


class TestOwnerAdmin:
    def test_list_and_tasks(self, client, db_engine, seed_admin, seed_employee):
        ob_id, task_ids = _onboarding(db_engine, "TST001", ["Done", "Pending"])
        headers = _bearer(db_engine, seed_admin["username"])
        records = {r["id"]: r for r in client.get("/api/onboarding", headers=headers).get_json()["onboardings"]}
        mine = records[str(ob_id)]
        assert (mine["employee_id"], mine["tasksCompleted"], mine["totalTasks"], mine["status"]) == (
            "TST001", 1, 2, "In Progress")
        tasks = client.get(f"/api/onboarding/{ob_id}/tasks", headers=headers).get_json()["tasks"]
        assert [(t["id"], t["title"], t["status"]) for t in tasks] == [
            (task_ids[0], "Route task 1", "Done"), (task_ids[1], "Route task 2", "Pending")]

    def test_updating_the_last_task_completes_the_onboarding(self, client, db_engine, seed_admin, seed_employee):
        ob_id, (first, second) = _onboarding(db_engine, "TST001", ["Pending", "Pending"])
        headers = _bearer(db_engine, seed_admin["username"])
        resp = client.post(f"/api/onboarding/task/{first}/update", json={"status": "Done", "admin_notes": " ok "},
                           headers=headers)
        assert resp.get_json() == {"ok": True, "onboarding_completed": False}
        status, notes, completed_at = _task(db_engine, first)
        assert (status, notes) == ("Done", "ok") and completed_at is not None
        assert _ob_status(db_engine, ob_id) == "In Progress"

        resp = client.post(f"/api/onboarding/task/{second}/update", json={"status": "Done"}, headers=headers)
        assert resp.get_json() == {"ok": True, "onboarding_completed": True}
        assert _ob_status(db_engine, ob_id) == "Completed"

    def test_setting_a_task_back_clears_its_completion_time(self, client, db_engine, seed_admin, seed_employee):
        _ob_id, [task_id] = _onboarding(db_engine, "TST001", ["Pending"])
        headers = _bearer(db_engine, seed_admin["username"])
        client.post(f"/api/onboarding/task/{task_id}/update", json={"status": "Done"}, headers=headers)
        client.post(f"/api/onboarding/task/{task_id}/update", json={"status": "Pending"}, headers=headers)
        assert _task(db_engine, task_id)[0] == "Pending" and _task(db_engine, task_id)[2] is None

    @pytest.mark.parametrize("status", ["", "done", "Completed", None])
    def test_only_known_statuses(self, client, db_engine, seed_admin, seed_employee, status):
        _ob_id, [task_id] = _onboarding(db_engine, "TST001", ["Pending"])
        resp = client.post(f"/api/onboarding/task/{task_id}/update", json={"status": status},
                           headers=_bearer(db_engine, seed_admin["username"]))
        assert resp.status_code == 400
        assert _task(db_engine, task_id)[0] == "Pending"

    def test_unknown_task(self, client, db_engine, seed_admin):
        resp = client.post("/api/onboarding/task/999999999/update", json={"status": "Done"},
                           headers=_bearer(db_engine, seed_admin["username"]))
        assert resp.status_code == 404


class TestTeamScope:
    def test_list_shows_only_the_team(self, client, team):
        employees = {r["employee_id"] for r in
                     client.get("/api/onboarding", headers=team["headers"]).get_json()["onboardings"]}
        assert employees == {"RTEAM01"}

    def test_own_team_tasks_can_be_read_and_updated(self, client, db_engine, team):
        ob_id, [task_id] = team["inside"]
        assert len(client.get(f"/api/onboarding/{ob_id}/tasks", headers=team["headers"]).get_json()["tasks"]) == 1
        resp = client.post(f"/api/onboarding/task/{task_id}/update", json={"status": "Done"}, headers=team["headers"])
        assert resp.status_code == 200 and _task(db_engine, task_id)[0] == "Done"

    # Regression (IDOR): api_onboarding_tasks had no hr_scope_denied() check, so a team-scoped
    # token read the checklist of any onboarding by id. The web twin (onboarding_detail) 404s.
    def test_cannot_read_another_teams_tasks(self, client, team):
        ob_id, _tasks = team["outside"]
        assert client.get(f"/api/onboarding/{ob_id}/tasks", headers=team["headers"]).status_code == 404

    # Regression (IDOR): api_onboarding_task_update had no hr_scope_denied() check on the task's
    # employee, so a team-scoped token could mark another team's task Done (and complete their
    # onboarding). The web twin (onboarding_admin_task_update) got this check in the 2026-11 audit.
    def test_cannot_update_another_teams_task(self, client, db_engine, team):
        _ob_id, (pending, _done) = team["outside"]
        resp = client.post(f"/api/onboarding/task/{pending}/update", json={"status": "Done"},
                           headers=team["headers"])
        assert resp.status_code == 404
        assert _task(db_engine, pending)[0] == "Pending"
