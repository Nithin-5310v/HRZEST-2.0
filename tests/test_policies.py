"""Tests for blueprints/policies.py -- the brand-new company_policies CRUD
feature (previously just hardcoded static text in
templates/employee_portal.html). Company-wide, not per-assigned-employee
(matching the existing holidays table's own precedent) -- both 'admin' and
HR-role sessions can manage it."""
from utils.auth import HR_ROLE


def _admin_session(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"


def _hr_session(client, seed_hr_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE


def _cleanup_policy(db_engine, title):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM company_policies WHERE title=%s", (title,))
    cur.close()


class TestPolicyMigration:
    def test_company_policies_table_exists(self, db_engine):
        # No starter rows are seeded -- each company writes its own.
        cur = db_engine.cursor()
        cur.execute("SELECT to_regclass('company_policies')")
        assert cur.fetchone()[0] is not None
        cur.close()


class TestPolicyCrudAdmin:
    def test_admin_can_add_policy(self, client, db_engine, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/policies/add", data={
            "category": "terms", "title": "Test Admin Policy", "body": "Body text",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT category, body, is_published FROM company_policies WHERE title=%s", ("Test Admin Policy",))
        row = cur.fetchone()
        cur.close()
        assert row == ("terms", "Body text", 1)
        _cleanup_policy(db_engine, "Test Admin Policy")

    def test_admin_can_edit_publish_unpublish_delete(self, client, db_engine, seed_admin):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO company_policies (category, title, body) VALUES ('rules','Editable Policy','orig') RETURNING id"
        )
        pid = cur.fetchone()[0]
        cur.close()

        _admin_session(client, seed_admin)
        resp = client.post(f"/policies/{pid}/edit", data={
            "category": "rules", "title": "Editable Policy (edited)", "body": "new body",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT title, body FROM company_policies WHERE id=%s", (pid,))
        assert cur.fetchone() == ("Editable Policy (edited)", "new body")
        cur.close()

        resp = client.post(f"/policies/{pid}/unpublish", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT is_published FROM company_policies WHERE id=%s", (pid,))
        assert cur.fetchone()[0] == 0
        cur.close()

        resp = client.post(f"/policies/{pid}/publish", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT is_published FROM company_policies WHERE id=%s", (pid,))
        assert cur.fetchone()[0] == 1
        cur.close()

        resp = client.post(f"/policies/{pid}/delete", follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM company_policies WHERE id=%s", (pid,))
        assert cur.fetchone() is None
        cur.close()


class TestPolicyCrudHr:
    def test_hr_can_add_and_manage_policies(self, client, db_engine, seed_hr_admin):
        _hr_session(client, seed_hr_admin)
        resp = client.post("/policies/add", data={
            "category": "posh", "title": "Test HR Policy", "body": "Body text",
        }, follow_redirects=True)
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM company_policies WHERE title=%s", ("Test HR Policy",))
        assert cur.fetchone() is not None
        cur.close()
        _cleanup_policy(db_engine, "Test HR Policy")


class TestPolicyAccessControl:
    def test_plain_employee_session_blocked_from_mutation_routes(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.post("/policies/add", data={"category": "terms", "title": "x", "body": "y"})
        assert resp.status_code in (302, 403)
        if resp.status_code == 302:
            assert "login" in resp.headers.get("Location", "")

    def test_unauthenticated_blocked_from_mutation_routes(self, client):
        resp = client.post("/policies/add", data={"category": "terms", "title": "x", "body": "y"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "login" in resp.headers.get("Location", "")


class TestEmployeePoliciesApi:
    def test_returns_only_published_policies(self, client, db_engine, seed_employee):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO company_policies (category, title, body, is_published) VALUES ('rules','Published One','x',1) RETURNING id")
        pub_id = cur.fetchone()[0]
        cur.execute("INSERT INTO company_policies (category, title, body, is_published) VALUES ('rules','Draft One','x',0) RETURNING id")
        draft_id = cur.fetchone()[0]
        cur.close()

        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/api/employee/policies")
        assert resp.status_code == 200
        data = resp.get_json()
        titles = [p["title"] for p in data["policies"]]
        assert "Published One" in titles
        assert "Draft One" not in titles

        cur = db_engine.cursor()
        cur.execute("DELETE FROM company_policies WHERE id IN (%s, %s)", (pub_id, draft_id))
        cur.close()


class TestHrDashboardPoliciesAndHolidaysTabs:
    def test_hr_dashboard_policies_endpoint_lists_all_including_drafts(self, client, db_engine, seed_hr_admin):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO company_policies (category, title, body, is_published) VALUES ('rules','Draft Visible To HR','x',0) RETURNING id")
        pid = cur.fetchone()[0]
        cur.close()

        _hr_session(client, seed_hr_admin)
        resp = client.get("/api/hr_dashboard/policies")
        assert resp.status_code == 200
        titles = [p["title"] for p in resp.get_json()["rows"]]
        assert "Draft Visible To HR" in titles

        cur = db_engine.cursor()
        cur.execute("DELETE FROM company_policies WHERE id=%s", (pid,))
        cur.close()

    def test_hr_dashboard_holidays_endpoint(self, client, seed_hr_admin):
        _hr_session(client, seed_hr_admin)
        resp = client.get("/api/hr_dashboard/holidays")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
