"""Tests for blueprints/hr_dashboard.py -- the consolidated single-page HR
dashboard (see the approved plan: it replaces the old ?view=dashboard stub
on /employees). Every route is HR-only and scoped to the session's
assigned employees via utils/helpers.py's hr_scope_column()/
hr_scope_subquery()."""
from utils.helpers import encrypt_amount as _pay  # pay columns only accept ciphertext
from utils.auth import HR_ROLE


def test_unauthenticated_redirects_to_login(client):
    resp = client.get("/hr_dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "login" in resp.headers.get("Location", "")


def test_admin_role_gets_403_not_the_hr_dashboard(client, seed_admin):
    # /hr_dashboard is HR-only -- admin keeps using /admin, unchanged.
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    resp = client.get("/hr_dashboard")
    assert resp.status_code == 403


def test_hr_session_sees_overview(client, seed_hr_admin, seed_assigned_employee):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    resp = client.get("/hr_dashboard")
    assert resp.status_code == 200
    assert b"HR Dashboard" in resp.data
    assert b"Total Employees" in resp.data


def test_employees_api_scoped_to_assigned_only(client, seed_hr_admin, seed_employee, seed_assigned_employee):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    resp = client.get("/api/hr_dashboard/employees")
    assert resp.status_code == 200
    data = resp.get_json()
    ids = [e["employee_id"] for e in data["employees"]]
    assert seed_assigned_employee["employee_id"] in ids
    assert seed_employee["employee_id"] not in ids


def test_attendance_today_api_scoped_to_assigned_only(client, seed_hr_admin, seed_employee, seed_assigned_employee):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    resp = client.get("/api/hr_dashboard/attendance/today")
    assert resp.status_code == 200
    data = resp.get_json()
    names = [r["employee_id"] for r in data["rows"]]
    assert seed_assigned_employee["employee_id"] in names
    assert seed_employee["employee_id"] not in names


def test_leave_pending_api_scoped_to_assigned_only(client, db_engine, seed_hr_admin, seed_employee, seed_assigned_employee):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
        "VALUES (%s, CURRENT_DATE, 'unassigned', 'Pending') RETURNING id",
        (seed_employee["employee_id"],),
    )
    lid1 = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
        "VALUES (%s, CURRENT_DATE, 'assigned', 'Pending') RETURNING id",
        (seed_assigned_employee["employee_id"],),
    )
    lid2 = cur.fetchone()[0]
    cur.close()

    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    resp = client.get("/api/hr_dashboard/leave/pending")
    assert resp.status_code == 200
    data = resp.get_json()
    reasons = [leave["reason"] for leave in data["leaves"]]
    assert "assigned" in reasons
    assert "unassigned" not in reasons

    cur = db_engine.cursor()
    cur.execute("DELETE FROM leave_requests WHERE id IN (%s, %s)", (lid1, lid2))
    cur.close()


def test_dashboard_link_points_to_hr_dashboard_for_hr_role(client, seed_hr_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    resp = client.get("/employees")
    assert resp.status_code == 200
    assert b'href="' + b'/hr_dashboard"' in resp.data or b"/hr_dashboard" in resp.data


def test_old_view_dashboard_query_param_redirects(client, seed_hr_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_hr_admin["username"]
        sess["admin_role"] = HR_ROLE
    resp = client.get("/employees?view=dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/hr_dashboard" in resp.headers.get("Location", "")


class TestHrDashboardPayroll:
    """Phase 3 -- the payroll decision made explicitly for this feature:
    HR gets full unmasked salary/PAN/bank detail for employees assigned to
    them, via NEW routes (payroll.py's own admin-only /view_payslip stays
    exactly as it was, still blocking HR -- see TestPayslipOwnershipGate in
    tests/test_bola_ownership.py, unaffected by any of this)."""

    def _hr_session(self, client, seed_hr_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = HR_ROLE

    def test_payroll_report_scoped_to_assigned_only(self, client, db_engine, seed_hr_admin, seed_employee, seed_assigned_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc, basic_pct) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=EXCLUDED.salary_per_day",
            (seed_assigned_employee["employee_id"], _pay(1000), _pay(26000), _pay(50)),
        )
        cur.close()
        self._hr_session(client, seed_hr_admin)
        resp = client.get("/api/hr_dashboard/payroll/report")
        assert resp.status_code == 200
        data = resp.get_json()
        ids = [r["employee_id"] for r in data["rows"]]
        assert seed_assigned_employee["employee_id"] in ids
        assert seed_employee["employee_id"] not in ids
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (seed_assigned_employee["employee_id"],))
        cur.close()

    def test_hr_payslip_allowed_for_assigned_employee(self, client, seed_hr_admin, seed_assigned_employee):
        self._hr_session(client, seed_hr_admin)
        today = __import__("datetime").date.today()
        resp = client.get(f"/api/hr_dashboard/payroll/payslip/{seed_assigned_employee['employee_id']}/{today.year}/{today.month}")
        assert resp.status_code == 200
        assert b"pan" in resp.data.lower() or b"payslip" in resp.data.lower() or seed_assigned_employee["name"].encode() in resp.data

    def test_hr_payslip_denied_for_unassigned_employee(self, client, seed_hr_admin, seed_employee):
        self._hr_session(client, seed_hr_admin)
        today = __import__("datetime").date.today()
        resp = client.get(f"/api/hr_dashboard/payroll/payslip/{seed_employee['employee_id']}/{today.year}/{today.month}")
        assert resp.status_code == 404

    def test_existing_admin_only_view_payslip_route_still_blocks_hr(self, client, seed_hr_admin, seed_assigned_employee):
        # Regression guard: payroll.py's own /view_payslip must stay
        # admin-only exactly as before -- the new HR access path is the
        # separate /api/hr_dashboard/payroll/payslip/... route only.
        self._hr_session(client, seed_hr_admin)
        today = __import__("datetime").date.today()
        resp = client.get(f"/view_payslip/{seed_assigned_employee['employee_id']}/{today.year}/{today.month}", follow_redirects=False)
        assert resp.status_code == 302

    def test_overtime_list_scoped_to_assigned_only(self, client, db_engine, seed_hr_admin, seed_employee, seed_assigned_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s, CURRENT_DATE, '18:00:00', '19:00:00', 60, %s, 'Pending') RETURNING id",
            (seed_employee["employee_id"], _pay(100)),
        )
        oid1 = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s, CURRENT_DATE, '18:00:00', '19:30:00', 90, %s, 'Pending') RETURNING id",
            (seed_assigned_employee["employee_id"], _pay(150)),
        )
        oid2 = cur.fetchone()[0]
        cur.close()

        self._hr_session(client, seed_hr_admin)
        resp = client.get("/api/hr_dashboard/payroll/overtime")
        assert resp.status_code == 200
        data = resp.get_json()
        ids = [r["employee_id"] for r in data["rows"]]
        assert seed_assigned_employee["employee_id"] in ids
        assert seed_employee["employee_id"] not in ids

        cur = db_engine.cursor()
        cur.execute("DELETE FROM overtime_records WHERE id IN (%s, %s)", (oid1, oid2))
        cur.close()


class TestHrDashboardReports:
    """Phase 4 -- scoped analytics, a separate route from admin_views.py's
    own unscoped /analytics (never modified by this work)."""

    def test_reports_scoped_to_assigned_employees_only(self, client, seed_hr_admin, seed_employee, seed_assigned_employee):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = HR_ROLE
        resp = client.get("/api/hr_dashboard/reports")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        # Both employees exist; only the assigned one should count toward
        # this HR's total_employees (seed_employee is unassigned).
        assert data["total_employees"] == 1
        assert len(data["attendance_trend"]) == 6
        assert len(data["headcount_trend"]) == 6
        assert isinstance(data["dept_data"], list)

    def test_admin_analytics_route_unaffected(self, client, seed_admin):
        # Regression guard: admin_views.py's /analytics stays completely
        # untouched -- still admin-only, still fully unscoped.
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        resp = client.get("/analytics")
        assert resp.status_code == 200

    def test_hr_role_still_blocked_from_admin_analytics(self, client, seed_hr_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = HR_ROLE
        resp = client.get("/analytics")
        assert resp.status_code == 403


class TestHrDashboardPhase5:
    """Phase 5 -- thin read-only glance endpoints for Onboarding,
    Performance, and Tickets; Phase 1 already scoped/guarded the
    underlying routes these link out to for actions."""

    def _hr_session(self, client, seed_hr_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = HR_ROLE

    def test_onboarding_scoped_to_assigned_only(self, client, db_engine, seed_hr_admin, seed_employee, seed_assigned_employee):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO onboarding_templates (name, description, is_active) VALUES ('T5', 'test', 1) RETURNING id")
        tid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, status) "
            "VALUES (%s, %s, CURRENT_DATE, 'In Progress') RETURNING id",
            (seed_employee["employee_id"], tid),
        )
        ob1 = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, status) "
            "VALUES (%s, %s, CURRENT_DATE, 'In Progress') RETURNING id",
            (seed_assigned_employee["employee_id"], tid),
        )
        ob2 = cur.fetchone()[0]
        cur.close()

        self._hr_session(client, seed_hr_admin)
        resp = client.get("/api/hr_dashboard/onboarding")
        assert resp.status_code == 200
        data = resp.get_json()
        ids = [r["employee_id"] for r in data["rows"]]
        assert seed_assigned_employee["employee_id"] in ids
        assert seed_employee["employee_id"] not in ids

        cur = db_engine.cursor()
        cur.execute("DELETE FROM employee_onboarding WHERE id IN (%s, %s)", (ob1, ob2))
        cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (tid,))
        cur.close()

    def test_performance_scoped_to_assigned_only(self, client, seed_hr_admin, seed_employee, seed_assigned_employee):
        self._hr_session(client, seed_hr_admin)
        resp = client.get("/api/hr_dashboard/performance")
        assert resp.status_code == 200
        data = resp.get_json()
        ids = [r["employee_id"] for r in data["rows"]]
        assert seed_assigned_employee["employee_id"] in ids
        assert seed_employee["employee_id"] not in ids

    def test_tickets_scoped_to_assigned_only(self, client, db_engine, seed_hr_admin, seed_employee, seed_assigned_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO tickets (employee_id, category, subject, description, priority, status) "
            "VALUES (%s, 'General', 'unassigned ticket', 'test', 'Medium', 'Open') RETURNING id",
            (seed_employee["employee_id"],),
        )
        tid1 = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO tickets (employee_id, category, subject, description, priority, status) "
            "VALUES (%s, 'General', 'assigned ticket', 'test', 'Medium', 'Open') RETURNING id",
            (seed_assigned_employee["employee_id"],),
        )
        tid2 = cur.fetchone()[0]
        cur.close()

        self._hr_session(client, seed_hr_admin)
        resp = client.get("/api/hr_dashboard/tickets")
        assert resp.status_code == 200
        data = resp.get_json()
        subjects = [r["subject"] for r in data["rows"]]
        assert "assigned ticket" in subjects
        assert "unassigned ticket" not in subjects

        cur = db_engine.cursor()
        cur.execute("DELETE FROM tickets WHERE id IN (%s, %s)", (tid1, tid2))
        cur.close()


class TestEmployeePortalHrDashboardButton:
    """templates/employee_portal.html's top-right button used to be a bare
    <a href="/employees"> shown whenever the viewed employee's role=='HR' --
    dead once reached via switch_to_my_employee_portal(), since that flow
    intentionally clears admin_logged_in, and /employees requires it. Now a
    POST form to switch_back_to_hr_panel, gated on _hr_return_username
    (only ever set by that same switch), so it always leads somewhere that
    works instead of a 302 to login."""

    def test_button_shown_and_works_after_switching_from_hr_dashboard(self, client, db_engine, seed_hr_admin):
        # switch_to_my_employee_portal() requires an employees row whose
        # employee_id equals the HR admin's own username -- seed one.
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, role, password, force_pin_change) "
            "VALUES (%s, 'Test HR Self', 'HR', 'x', 0) ON CONFLICT (employee_id) DO NOTHING",
            (seed_hr_admin["username"],),
        )
        cur.close()
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = HR_ROLE
        resp = client.post("/switch_to_my_employee_portal", follow_redirects=False)
        assert resp.status_code == 302
        assert "/employee_portal" in resp.headers.get("Location", "")

        resp = client.get("/employee_portal")
        assert resp.status_code == 200
        assert b'action="/switch_back_to_hr_panel"' in resp.data
        assert b'href="/employees"' not in resp.data

        resp = client.post("/switch_back_to_hr_panel", follow_redirects=False)
        assert resp.status_code == 302
        assert "/hr_dashboard" in resp.headers.get("Location", "")

        cur = db_engine.cursor()
        cur.execute("DELETE FROM employees WHERE employee_id=%s", (seed_hr_admin["username"],))
        cur.close()

    def test_button_not_shown_for_a_normal_employee(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/employee_portal")
        assert resp.status_code == 200
        assert b'action="/switch_back_to_hr_panel"' not in resp.data
