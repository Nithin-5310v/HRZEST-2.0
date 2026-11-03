"""Coverage tests for blueprints/tickets.py.
Targets: raise_ticket, tickets_view, ticket_action, api routes.
"""
import hashlib
import datetime
import secrets


def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    return client


def _emp_session(client, seed_employee):
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    return client


def _make_employee_token(db_engine, emp_id):
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
        "VALUES (%s,%s,'employee',%s)",
        (emp_id, token_hash, expiry)
    )
    cur.close()

    def cleanup():
        c = db_engine.cursor()
        c.execute("DELETE FROM api_tokens WHERE token=%s", (token_hash,))
        c.close()
    return raw, cleanup


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


def _seed_ticket(db_engine, emp_id, subject="CI Test Ticket"):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO tickets (employee_id, category, subject, description, priority, status) "
        "VALUES (%s,'IT',%s,'Test description','Medium','Open') RETURNING id",
        (emp_id, subject)
    )
    tid = cur.fetchone()[0]
    cur.close()
    return tid


# ── raise_ticket ──────────────────────────────────────────────────────────────

class TestRaiseTicket:

    def test_unauthenticated_redirects(self, client):
        rv = client.post("/raise_ticket", data={
            "category": "IT", "subject": "Test", "description": "desc"
        })
        assert rv.status_code == 302

    def test_missing_fields_redirects_back(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/raise_ticket", data={
            "category": "", "subject": "", "description": ""
        })
        assert rv.status_code == 302
        assert "employee_portal" in rv.headers["Location"]

    def test_valid_ticket_inserts_and_redirects(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        rv = client.post("/raise_ticket", data={
            "category":    "IT",
            "subject":     "CI Raise Ticket Test",
            "description": "This is a test ticket",
            "priority":    "High",
        })
        assert rv.status_code == 302
        assert "ticket_sent=1" in rv.headers["Location"]
        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE subject='CI Raise Ticket Test'")
        cur.close()

    def test_missing_category_redirects(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.post("/raise_ticket", data={
            "category": "", "subject": "sub", "description": "desc"
        })
        assert rv.status_code == 302


# ── tickets_view ──────────────────────────────────────────────────────────────

class TestTicketsView:

    def test_unauthenticated_redirects(self, client):
        rv = client.get("/tickets")
        assert rv.status_code == 302

    def test_renders_for_admin(self, client, seed_admin):
        # /tickets is its own module page (blueprints/tickets.py's tickets_view()).
        _admin_session(client, seed_admin)
        rv = client.get("/tickets", follow_redirects=False)
        assert rv.status_code == 200
        assert b"panel-tickets" in rv.data

    def test_renders_with_ticket_data(self, client, seed_admin, seed_employee, db_engine):
        tid = _seed_ticket(db_engine, seed_employee["employee_id"])
        _admin_session(client, seed_admin)
        rv = client.get("/tickets", follow_redirects=False)
        assert rv.status_code == 200
        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
        cur.close()


# ── ticket_action ─────────────────────────────────────────────────────────────

class TestTicketAction:

    def test_invalid_status_returns_redirect(self, client, seed_admin, seed_employee, db_engine):
        tid = _seed_ticket(db_engine, seed_employee["employee_id"])
        _admin_session(client, seed_admin)
        rv = client.post(f"/ticket_action/{tid}", data={"status": "InvalidStatus"})
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
        cur.close()

    def test_invalid_status_ajax_returns_400(self, client, seed_admin, seed_employee, db_engine):
        tid = _seed_ticket(db_engine, seed_employee["employee_id"])
        _admin_session(client, seed_admin)
        rv = client.post(f"/ticket_action/{tid}",
                         data={"status": "BadStatus"},
                         headers={"X-Requested-With": "XMLHttpRequest"})
        assert rv.status_code == 400
        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
        cur.close()

    def test_valid_status_update_redirects(self, client, seed_admin, seed_employee, db_engine):
        tid = _seed_ticket(db_engine, seed_employee["employee_id"])
        _admin_session(client, seed_admin)
        rv = client.post(f"/ticket_action/{tid}", data={
            "status":         "In Progress",
            "admin_response": "",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
        cur.close()

    def test_resolve_with_response(self, client, seed_admin, seed_employee, db_engine):
        tid = _seed_ticket(db_engine, seed_employee["employee_id"])
        _admin_session(client, seed_admin)
        rv = client.post(f"/ticket_action/{tid}", data={
            "status":         "Resolved",
            "admin_response": "Fixed your issue.",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
        cur.close()

    def test_close_ticket(self, client, seed_admin, seed_employee, db_engine):
        tid = _seed_ticket(db_engine, seed_employee["employee_id"])
        _admin_session(client, seed_admin)
        rv = client.post(f"/ticket_action/{tid}", data={"status": "Closed"})
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
        cur.close()


# ── api_employee_tickets ──────────────────────────────────────────────────────

class TestApiEmployeeTickets:

    def test_unauthenticated_returns_401(self, client):
        rv = client.get("/api/employee/tickets")
        assert rv.status_code == 401

    def test_returns_list_with_token(self, client, seed_employee, db_engine):
        token, cleanup = _make_employee_token(db_engine, seed_employee["employee_id"])
        try:
            rv = client.get("/api/employee/tickets",
                            headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert "tickets" in data
        finally:
            cleanup()


# ── api_employee_raise_ticket ─────────────────────────────────────────────────

class TestApiEmployeeRaiseTicket:

    def test_unauthenticated_returns_401(self, client):
        rv = client.post("/api/employee/raise_ticket", json={})
        assert rv.status_code == 401

    def test_missing_fields_returns_400(self, client, seed_employee, db_engine):
        token, cleanup = _make_employee_token(db_engine, seed_employee["employee_id"])
        try:
            rv = client.post("/api/employee/raise_ticket",
                             json={"category": "", "subject": "", "description": ""},
                             headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code in (400, 422)
        finally:
            cleanup()

    def test_valid_raises_ticket(self, client, seed_employee, db_engine):
        token, cleanup = _make_employee_token(db_engine, seed_employee["employee_id"])
        try:
            rv = client.post("/api/employee/raise_ticket",
                             json={
                                 "category":    "IT",
                                 "subject":     "CI API Ticket Test",
                                 "description": "API test ticket",
                                 "priority":    "Low",
                             },
                             headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            assert rv.get_json().get("ok") is True
        finally:
            cleanup()
            cur = db_engine.cursor()
            cur.execute("DELETE FROM tickets WHERE subject='CI API Ticket Test'")
            cur.close()


# ── api_tickets (admin) ───────────────────────────────────────────────────────

class TestApiTickets:

    def test_unauthenticated_returns_401(self, client):
        rv = client.get("/api/tickets")
        assert rv.status_code in (302, 401)

    def test_returns_list_for_admin(self, client, db_engine, seed_admin):
        # api_tickets is now role-gated (@api_role_required("admin","hr")) --
        # needs a real admin_users row for the token's identity to resolve
        # a role from (COALESCE(role,'admin') finds nothing for a bare
        # "admin" identity with no matching row, same fix already applied
        # to test_payroll_coverage.py's equivalent synthetic-token tests).
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.get("/api/tickets",
                            headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert "tickets" in data
        finally:
            cleanup()

    def test_non_admin_role_denied(self, client, db_engine, seed_admin):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.get("/api/tickets", headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 403
        finally:
            cleanup()
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()
            cur.close()

    def test_hr_role_allowed(self, client, db_engine, seed_admin):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='hr' WHERE username=%s", (seed_admin["username"],))
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.get("/api/tickets", headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
        finally:
            cleanup()
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()
            cur.close()


# ── api_ticket_action (admin) ────────────────────────────────────────────────

class TestApiTicketAction:

    def _make_ticket(self, db_engine, emp_id):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO tickets (employee_id, category, subject, description, priority) "
            "VALUES (%s,'IT','Role Gate Test','desc','Medium') RETURNING id",
            (emp_id,)
        )
        tid = cur.fetchone()[0]
        cur.close()
        db_engine.commit()
        return tid

    def test_unauthenticated_returns_401(self, client):
        rv = client.post("/api/tickets/1/action", json={"status": "Resolved"})
        assert rv.status_code == 401

    def test_admin_can_act(self, client, db_engine, seed_admin, seed_employee):
        tid = self._make_ticket(db_engine, seed_employee["employee_id"])
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.post(f"/api/tickets/{tid}/action",
                             json={"status": "Resolved", "admin_response": "done"},
                             headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 200
            cur = db_engine.cursor()
            cur.execute("SELECT status FROM tickets WHERE id=%s", (tid,))
            assert cur.fetchone()[0] == "Resolved"
            cur.close()
        finally:
            cleanup()
            cur = db_engine.cursor()
            cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
            db_engine.commit()
            cur.close()

    def test_non_admin_role_denied(self, client, db_engine, seed_admin, seed_employee):
        """api_ticket_action used to check only @api_required -- any
        admin-side role could close/edit any employee's ticket. Now
        gated to @api_role_required("admin","hr")."""
        tid = self._make_ticket(db_engine, seed_employee["employee_id"])
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        token, cleanup = _make_admin_token(db_engine, identity=seed_admin["username"])
        try:
            rv = client.post(f"/api/tickets/{tid}/action",
                             json={"status": "Resolved"},
                             headers={"Authorization": f"Bearer {token}"})
            assert rv.status_code == 403
            cur.execute("SELECT status FROM tickets WHERE id=%s", (tid,))
            assert cur.fetchone()[0] != "Resolved", "ticket was mutated despite the role check"
        finally:
            cleanup()
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
            db_engine.commit()
            cur.close()
