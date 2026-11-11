"""Coverage tests for blueprints/employees.py.
Targets: view_employees, api_employee_info, edit_employee_page,
employee_profile, regenerate_qr,
generate_emp_id, api_employees, delete_employee.
"""
import hashlib
import datetime
import secrets
from tests.conftest import with_local_scripts  # noqa: E402


def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    return client


def _make_admin_token(db_engine, identity="admin"):
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s,%s,'admin',%s)",
        (identity, token_hash, expiry)
    )
    cur.close()

    def cleanup():
        c = db_engine.cursor()
        c.execute("DELETE FROM api_tokens WHERE token=%s", (token_hash,))
        c.close()
    return raw, cleanup


# ── view_employees ────────────────────────────────────────────────────────────

class TestViewEmployees:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/employees")
        assert rv.status_code == 302

    def test_renders_for_admin(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/employees")
        assert rv.status_code == 200

    def test_renders_with_company_filter(self, client, seed_admin):
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 1
        rv = client.get("/employees")
        assert rv.status_code == 200

    def test_seed_employee_shown(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.get("/employees")
        assert rv.status_code == 200
        assert seed_employee["employee_id"].encode() in rv.data

    def test_schedule_modal_js_preserves_tenant_prefix(self, client, seed_admin):
        """Regression guard for a real tenant-isolation bug: the edit-shift/
        edit-break modals' JS used to overwrite the form's action with a bare
        '/edit_shift/' + id / '/update_break/' + id, discarding whatever
        tenant-slug prefix tpath() had baked into the form's initial,
        server-rendered action -- silently submitting outside the tenant's
        URL scope on a tenant-prefixed deployment. The fix rewrites only the
        trailing id segment via .replace(...) so the prefix survives.

        No JS engine runs in this suite (no Selenium/Playwright), so this
        can't execute the browser code -- it guards the source text itself:
        fails if the vulnerable bare-assignment pattern is ever reintroduced,
        or if the prefix-preserving .replace(...) fix is removed."""
        _admin_session(client, seed_admin)
        rv = client.get("/employees?tab=schedule")
        assert rv.status_code == 200
        body = with_local_scripts(rv.get_data(as_text=True))   # the JS lives in static/js now

        assert "shiftForm.action.replace(/\\/edit_shift\\/[^/?]*$/, '/edit_shift/' + id)" in body
        assert "breakForm.action.replace(/\\/update_break\\/[^/?]*$/, '/update_break/' + id)" in body

        # The vulnerable pattern this test guards against: a bare assignment
        # that drops any tenant prefix tpath() added to the form's action.
        assert "document.getElementById('editShiftForm').action = '/edit_shift/' + id" not in body
        assert "document.getElementById('editBreakForm').action = '/update_break/' + id" not in body


# ── api_employee_info ─────────────────────────────────────────────────────────

class TestApiEmployeeInfo:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/api/employee_info/TST001")
        assert rv.status_code in (302, 401)

    def test_known_employee_returns_json(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.get(f"/api/employee_info/{seed_employee['employee_id']}")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["emp_id"] == seed_employee["employee_id"]
        assert data["name"] == seed_employee["name"]

    def test_unknown_employee_returns_404(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/api/employee_info/GHOST_99999")
        assert rv.status_code == 404
        assert "error" in rv.get_json()


# ── edit_employee_page ────────────────────────────────────────────────────────

class TestEditEmployeePage:

    def test_unauthenticated_redirects(self, client, seed_employee):
        rv = client.get(f"/edit_employee/{seed_employee['employee_id']}")
        assert rv.status_code == 302

    def test_renders_for_known_employee(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.get(f"/edit_employee/{seed_employee['employee_id']}")
        assert rv.status_code == 200

    def test_unknown_employee_renders_or_404(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/edit_employee/GHOST_99999")
        assert rv.status_code in (200, 302, 404)


# ── employee_profile ──────────────────────────────────────────────────────────

class TestEmployeeProfile:

    def test_unauthenticated_redirects(self, client, seed_employee):
        rv = client.get(f"/employee_profile/{seed_employee['employee_id']}")
        assert rv.status_code == 302

    def test_renders_for_admin(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.get(f"/employee_profile/{seed_employee['employee_id']}")
        assert rv.status_code == 200


# ── regenerate_qr ─────────────────────────────────────────────────────────────

class TestRegenerateQr:

    def test_unauthenticated_redirects(self, client, seed_employee):
        rv = client.post(f"/regenerate_qr/{seed_employee['employee_id']}")
        assert rv.status_code == 302

    def test_regenerates_qr_for_known_employee(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.post(f"/regenerate_qr/{seed_employee['employee_id']}")
        assert rv.status_code == 302


# ── generate_emp_id ───────────────────────────────────────────────────────────

class TestGenerateEmpId:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/api/generate_emp_id")
        assert rv.status_code in (302, 401)

    def test_returns_json_with_emp_id(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/api/generate_emp_id")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "emp_id" in data
        assert len(data["emp_id"]) > 0


# ── api_employees ─────────────────────────────────────────────────────────────

class TestApiEmployees:

    def test_unauthenticated_returns_401(self, client):
        rv = client.get("/api/employees")
        assert rv.status_code in (302, 401)

    def test_returns_list_for_admin(self, client, db_engine, seed_admin):
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.get("/api/employees",
                            headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert "employees" in data or isinstance(data, list)
        finally:
            cleanup()


# ── delete_employee ───────────────────────────────────────────────────────────

class TestDeleteEmployee:

    def test_unauthenticated_redirects(self, client):
        rv = client.post("/delete_employee/GHOST_99")
        assert rv.status_code == 302

    def test_unknown_employee_redirects(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/delete_employee/GHOST_NEVER_EXISTS")
        assert rv.status_code == 302

    def test_deletes_employee(self, client, seed_admin, db_engine):
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password) "
            "VALUES ('DEL001','Del Test','del@test.local',%s) ON CONFLICT DO NOTHING",
            (generate_password_hash("Del@123"),)
        )
        cur.close()
        _admin_session(client, seed_admin)
        rv = client.post("/delete_employee/DEL001")
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM employees WHERE employee_id='DEL001'")
        assert cur.fetchone() is None
        cur.close()


# ── api_delete_employee (mobile/API twin of delete_employee) ──────────────────

class TestApiDeleteEmployee:
    """api_delete_employee used to perform the identical destructive
    multi-table delete as delete_employee() (web) above, but without
    wrapping it in a transaction (partial-failure risk) and without
    calling _audit() (the deletion left no trail at all when done from
    the mobile app, unlike the web path)."""

    def _seed(self, db_engine, emp_id="DELAPI001"):
        from utils.auth import generate_password_hash
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password) "
            "VALUES (%s,'Del Api Test','delapi@test.local',%s) ON CONFLICT DO NOTHING",
            (emp_id, generate_password_hash("Del@123"))
        )
        cur.close()
        db_engine.commit()

    def test_unauthenticated_returns_401(self, client):
        rv = client.delete("/api/employees/GHOST_99")
        assert rv.status_code == 401

    def test_unknown_employee_returns_404(self, client, db_engine, seed_admin):
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.delete("/api/employees/GHOST_NEVER_EXISTS",
                               headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 404
        finally:
            cleanup()

    def test_deletes_employee_and_related_rows(self, client, db_engine, seed_admin):
        emp_id = "DELAPI001"
        self._seed(db_engine, emp_id)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s)",
            (emp_id, datetime.date(2027, 2, 1), "x")
        )
        db_engine.commit()
        cur.close()

        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.delete(f"/api/employees/{emp_id}",
                               headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            assert rv.get_json()["ok"] is True

            cur = db_engine.cursor()
            cur.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone() is None
            cur.execute("SELECT 1 FROM leave_requests WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone() is None, "related leave_requests row survived the delete"
            cur.close()
        finally:
            cleanup()

    def test_delete_is_audited(self, client, db_engine, seed_admin):
        emp_id = "DELAPI002"
        self._seed(db_engine, emp_id)
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.delete(f"/api/employees/{emp_id}",
                               headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200

            cur = db_engine.cursor()
            cur.execute(
                "SELECT detail FROM audit_logs WHERE target_id=%s AND action='delete_employee' "
                "ORDER BY id DESC LIMIT 1",
                (emp_id,)
            )
            row = cur.fetchone()
            assert row is not None, "api_delete_employee's deletion was not audited"
            assert emp_id in row[0]
            cur.close()
        finally:
            cleanup()

    def test_non_admin_role_denied(self, client, db_engine, seed_admin):
        emp_id = "DELAPI003"
        self._seed(db_engine, emp_id)
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.delete(f"/api/employees/{emp_id}",
                               headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 403
            cur.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone() is not None, "employee was deleted despite the role check"
        finally:
            cleanup()
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            cur.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
            db_engine.commit()
            cur.close()


# ── edit_employee POST ────────────────────────────────────────────────────────

class TestEditEmployee:

    def test_unauthenticated_redirects(self, client):
        rv = client.post("/edit_employee", data={"emp_id": "TST001"})
        assert rv.status_code == 302

    def test_updates_employee_name(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/edit_employee", data={
            "emp_id":          seed_employee["employee_id"],
            "name":            "Updated Name",
            "role":            "Engineer",
            "email":           "emp@test.local",
            "date_of_joining": "2024-01-01",
            "work_mode":       "office",
            "department":      "Engineering",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM employees WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        name = cur.fetchone()[0]
        cur.close()
        assert name == "Updated Name"
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET name=%s WHERE employee_id=%s",
                    (seed_employee["name"], seed_employee["employee_id"]))
        cur.close()

    def test_role_change_is_audited(self, client, seed_admin, seed_employee, db_engine):
        """Finding #13 (Medium): no write path to employees.role (the
        free-text job-title field -- confirmed distinct from
        admin_users.role, the real privilege field) called _audit(), so a
        role/job-title change left no trail. A no-op edit (role
        unchanged) must NOT create a fresh audit_logs row each time --
        audit_logs is append-only, so this is checked by row count, not
        by deleting between assertions."""
        _admin_session(client, seed_admin)
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM audit_logs WHERE target_id=%s AND action='update_employee_role'", (emp_id,))
        before = cur.fetchone()[0]
        try:
            rv = client.post("/edit_employee", data={
                "emp_id": emp_id, "name": seed_employee["name"], "role": "Senior Engineer",
                "email": "emp@test.local", "date_of_joining": "2024-01-01",
                "work_mode": "office", "department": "Engineering",
            })
            assert rv.status_code == 302
            cur.execute(
                "SELECT detail FROM audit_logs WHERE target_id=%s AND action='update_employee_role' "
                "ORDER BY id DESC LIMIT 1",
                (emp_id,)
            )
            row = cur.fetchone()
            assert row is not None, "role change was not audited"
            assert "Senior Engineer" in row[0]

            # Editing again with the SAME role must not add another row.
            client.post("/edit_employee", data={
                "emp_id": emp_id, "name": seed_employee["name"], "role": "Senior Engineer",
                "email": "emp@test.local", "date_of_joining": "2024-01-01",
                "work_mode": "office", "department": "Engineering",
            })
            cur.execute("SELECT COUNT(*) FROM audit_logs WHERE target_id=%s AND action='update_employee_role'", (emp_id,))
            after_noop = cur.fetchone()[0]
            assert after_noop == before + 1, "an unchanged role must not create a second audit row"
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (emp_id,))
            db_engine.commit()
            cur.close()
