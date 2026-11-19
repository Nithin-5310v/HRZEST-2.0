"""Authorization gaps found by the route audit for the 2026-11 security
review (OBS-07: "confirm server-side authentication plus role checks").

Company used by the team-scope tests (same shape as test_role_hierarchy.py):

    Administrator
    └── Senior (team role)  SNR01
        ├── Junior (team)   JNR01 ── EMPA1
        └── Junior (team)   JNR02 ── EMPB1
"""
import time

import pytest

from extensions import app as flask_app
from utils.auth import generate_password_hash

PASSWORD = "Audit@12345"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _session(client, **values):
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(values)


def _admin_session(client):
    _session(client, admin_logged_in=True, admin_username="test_admin", admin_role="admin",
             _session_created=time.time(), _last_activity=time.time())


@pytest.fixture
def tokens(client, db_engine, seed_admin):
    """Bearer tokens for the company admin, an HR and a manager login."""
    cur = db_engine.cursor()
    for user, role in (("aud_hr", "hr"), ("aud_mgr", "manager")):
        cur.execute("DELETE FROM login_attempts WHERE identifier=%s", (user,))
        cur.execute("INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,1) "
                    "ON CONFLICT (username) DO UPDATE SET password=EXCLUDED.password, role=EXCLUDED.role",
                    (user, generate_password_hash(PASSWORD), role, f"{user}@test.local"))
    db_engine.commit()
    out = {"admin": client.post("/api/login", json={"username": "test_admin", "password": "Test@1234"}).get_json()}
    for user in ("aud_hr", "aud_mgr"):
        out[user] = client.post("/api/login", json={"username": user, "password": PASSWORD}).get_json()
    out = {k: v.get("token") for k, v in out.items()}
    assert all(out.values()), out
    yield {"admin": out["admin"], "hr": out["aud_hr"], "manager": out["aud_mgr"]}
    for user in ("aud_hr", "aud_mgr"):
        cur.execute("DELETE FROM api_tokens WHERE identity=%s", (user,))
        cur.execute("DELETE FROM admin_users WHERE username=%s", (user,))
    db_engine.commit()
    cur.close()


# ── Bearer routes that were open to every admin-side token ──────────────────
class TestApiRoleChecks:
    def test_proxy_checkin_is_company_admin_only(self, client, tokens):
        body = {"employee_id": "NOBODY-1", "lat": 1, "lon": 1}
        for role in ("hr", "manager"):
            assert client.post("/api/attendance/checkin", json=body,
                               headers=_bearer(tokens[role])).status_code == 403, role
        assert client.post("/api/attendance/checkin", json=body,
                           headers=_bearer(tokens["admin"])).status_code != 403

    def test_adding_a_holiday_needs_admin_or_hr(self, client, db_engine, tokens):
        day = {"date": "2099-12-24", "name": "Route audit test"}
        try:
            assert client.post("/api/holidays", json=day, headers=_bearer(tokens["manager"])).status_code == 403
            assert client.post("/api/holidays", json=day, headers=_bearer(tokens["hr"])).get_json()["ok"] is True
            # Listing stays open to every admin-side token.
            resp = client.get("/api/holidays", headers=_bearer(tokens["manager"]))
            assert resp.status_code == 200
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM holidays WHERE date='2099-12-24'")
            db_engine.commit()
            cur.close()


class TestOfficeNetworks:
    def test_hr_session_cannot_change_the_allow_list(self, client, tokens):
        # aud_hr is a real, active HR login (the tokens fixture creates it).
        _session(client, admin_logged_in=True, admin_username="aud_hr", admin_role="hr",
                 _session_created=time.time(), _last_activity=time.time())
        resp = client.post("/api/admin/attendance/office_networks", json={"require": False, "networks": []},
                           headers={"Accept": "application/json"})
        assert resp.status_code == 403

    def test_company_admin_can(self, client, seed_admin):
        _admin_session(client)
        assert client.get("/api/admin/attendance/office_networks").status_code == 200


class TestDeactivatedAdminSession:
    def test_session_of_a_deactivated_admin_is_refused(self, client, db_engine, seed_admin):
        """resolve_admin_identity() now runs the full session_account_ok()."""
        _admin_session(client)
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username='test_admin'")
        db_engine.commit()
        try:
            resp = client.get("/api/ai/attrition-analytics")
            assert resp.status_code in (401, 403)
        finally:
            cur.execute("UPDATE admin_users SET is_active=1 WHERE username='test_admin'")
            db_engine.commit()
            cur.close()


# ── Team scope on single-record admin routes ────────────────────────────────
@pytest.fixture
def team(client, db_engine, seed_admin):
    from utils.helpers.rbac import assign_employee_role, create_role, set_permissions
    with client.application.test_request_context():
        senior = create_role("Audit Senior", "custom", "", "", "team")
        junior = create_role("Audit Junior", "custom", senior, "", "team")
        set_permissions(senior, {"employees": "edit", "onboarding": "edit"})
        set_permissions(junior, {"employees": "edit", "onboarding": "edit"})
    cur = db_engine.cursor()
    people = [("SNR01", None), ("JNR01", "SNR01"), ("JNR02", "SNR01"), ("EMPA1", "JNR01"), ("EMPB1", "JNR02")]
    for emp_id, manager in people:
        cur.execute("INSERT INTO employees (employee_id, name, password, force_pin_change, manager_id) "
                    "VALUES (%s,%s,'x',0,%s)", (emp_id, f"Person {emp_id}", manager))
    db_engine.commit()
    with client.application.test_request_context():
        assign_employee_role("SNR01", senior)
        assign_employee_role("JNR01", junior)
        assign_employee_role("JNR02", junior)
    yield {"junior": junior}
    for emp_id, _ in people:
        cur.execute("DELETE FROM employee_onboarding_tasks WHERE employee_id=%s", (emp_id,))
        cur.execute("DELETE FROM employee_onboarding WHERE employee_id=%s", (emp_id,))
        cur.execute("DELETE FROM employee_documents WHERE employee_id=%s", (emp_id,))
        cur.execute("DELETE FROM admin_users WHERE username=%s", (emp_id,))
        cur.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM role_permissions WHERE role_key IN (%s,%s)", (junior, senior))
    cur.execute("DELETE FROM roles WHERE role_key IN (%s,%s)", (junior, senior))
    db_engine.commit()
    cur.close()


def _as_junior(client, team):
    _session(client, admin_logged_in=True, admin_username="JNR01", admin_role=team["junior"],
             admin_base_type="custom", _session_created=time.time(), _last_activity=time.time())


class TestTeamScope:
    def test_id_card_of_someone_outside_the_team_is_not_found(self, client, team):
        _as_junior(client, team)
        assert client.get("/admin_view_id_card/EMPB1").status_code == 404
        assert client.get("/admin_id_card/EMPB1").status_code == 404

    def test_photo_of_someone_outside_the_team_cannot_be_replaced(self, client, team):
        _as_junior(client, team)
        resp = client.post("/update_employee_photo/EMPB1", data={}, follow_redirects=True)
        assert b"Employee not found." in resp.data

    def test_documents_outside_the_team(self, client, db_engine, team):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name, uploaded_by) "
                    "VALUES ('EMPB1','ID','id.pdf','employee_documents/EMPB1/x_id.pdf','admin') RETURNING id")
        did = cur.fetchone()[0]
        db_engine.commit()
        _as_junior(client, team)
        resp = client.get(f"/download_document/{did}", follow_redirects=True)
        assert b"Document not found." in resp.data
        client.post(f"/delete_document/{did}")
        cur.execute("SELECT 1 FROM employee_documents WHERE id=%s", (did,))
        assert cur.fetchone(), "a team-scoped role can't delete someone else's document"
        cur.close()

    def test_onboarding_task_is_checked_by_its_own_row(self, client, db_engine, team):
        """Passing your own onboarding id with someone else's task id no longer works."""
        cur = db_engine.cursor()
        ids = {}
        for emp in ("EMPA1", "EMPB1"):
            cur.execute("INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, status) "
                        "VALUES (%s, 1, CURRENT_DATE, 'In Progress') RETURNING id", (emp,))
            ob = cur.fetchone()[0]
            cur.execute("INSERT INTO employee_onboarding_tasks (onboarding_id, template_task_id, employee_id, "
                        "task_title, status) VALUES (%s, 1, %s, 'Sign NDA', 'Pending') RETURNING id", (ob, emp))
            ids[emp] = (ob, cur.fetchone()[0])
        db_engine.commit()
        _as_junior(client, team)
        resp = client.post("/onboarding_admin_task_update",
                           data={"ob_id": ids["EMPA1"][0], "task_id": ids["EMPB1"][1], "status": "Done"})
        assert resp.status_code == 404
        cur.execute("SELECT status FROM employee_onboarding_tasks WHERE id=%s", (ids["EMPB1"][1],))
        assert cur.fetchone()[0] == "Pending"
        # Their own person's task still works.
        client.post("/onboarding_admin_task_update",
                    data={"ob_id": ids["EMPA1"][0], "task_id": ids["EMPA1"][1], "status": "Done"})
        cur.execute("SELECT status FROM employee_onboarding_tasks WHERE id=%s", (ids["EMPA1"][1],))
        assert cur.fetchone()[0] == "Done"
        cur.close()


def test_route_guard_registry_still_complete():
    """The new POST /api/holidays endpoint is decorated, not allow-listed."""
    view = flask_app.view_functions["leave.api_add_holiday"]
    assert getattr(view, "_hrz_auth_guard", None) or getattr(view, "__wrapped__", None)
