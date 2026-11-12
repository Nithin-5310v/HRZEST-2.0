"""A manager-based role gets exactly the modules Settings -> Roles & Access
grants it -- no hardcoded "manager" rule on top. Tickets in particular: with
Tickets access a manager opens the module and sees (and acts on) only their
direct reports' tickets; without it they can't act on any ticket."""
import pytest

MGR = "MGRT01"


def _login(client, role="manager"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = MGR
        sess["admin_role"] = role
        sess["admin_base_type"] = "manager"


def _set_access(cur, role_key, module, access):
    cur.execute("INSERT INTO roles (role_key, display_name, base_type, is_system) "
                "VALUES (%s,%s,'manager',FALSE) ON CONFLICT (role_key) DO NOTHING", (role_key, role_key.title()))
    cur.execute("INSERT INTO role_permissions (role_key, module, access) VALUES (%s,%s,%s) "
                "ON CONFLICT (role_key, module) DO UPDATE SET access=EXCLUDED.access", (role_key, module, access))


@pytest.fixture
def team(db_engine, seed_admin):
    """A manager, one direct report and one employee on another team, each
    report with an open ticket. Returns {employee_id: ticket_id}."""
    cur = db_engine.cursor()
    for emp_id, manager in ((MGR, None), ("MGRT02", MGR), ("MGRT03", None)):
        cur.execute("INSERT INTO employees (employee_id, name, password, force_pin_change, manager_id) "
                    "VALUES (%s,%s,'x',0,%s)", (emp_id, f"Person {emp_id}", manager))
    cur.execute("INSERT INTO admin_users (username, password, role, is_active) VALUES (%s,'x','manager',1)", (MGR,))
    tickets = {}
    for emp_id in ("MGRT02", "MGRT03"):
        cur.execute("INSERT INTO tickets (employee_id, category, subject, description) "
                    "VALUES (%s,'IT',%s,'d') RETURNING id", (emp_id, f"Laptop issue {emp_id}"))
        tickets[emp_id] = cur.fetchone()[0]
    _set_access(cur, "manager", "tickets", "edit")
    db_engine.commit()
    yield tickets
    cur.close()


def test_manager_with_tickets_access_opens_tickets(client, team):
    _login(client)
    resp = client.get("/tickets")
    assert resp.status_code == 200


def test_manager_sees_only_direct_reports_tickets(client, team):
    _login(client)
    page = client.get("/tickets").data
    assert b"Laptop issue MGRT02" in page
    assert b"Laptop issue MGRT03" not in page


def test_manager_updates_a_direct_reports_ticket(client, db_engine, team):
    _login(client)
    resp = client.post(f"/ticket_action/{team['MGRT02']}", data={"status": "Resolved"},
                       headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    cur = db_engine.cursor()
    cur.execute("SELECT status FROM tickets WHERE id=%s", (team["MGRT02"],))
    assert cur.fetchone()[0] == "Resolved"
    cur.close()


def test_manager_cannot_update_another_teams_ticket(client, db_engine, team):
    _login(client)
    resp = client.post(f"/ticket_action/{team['MGRT03']}", data={"status": "Resolved"},
                       headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 404
    cur = db_engine.cursor()
    cur.execute("SELECT status FROM tickets WHERE id=%s", (team["MGRT03"],))
    assert cur.fetchone()[0] == "Open"
    cur.close()


def test_role_without_ticket_edit_access_cannot_update_tickets(client, db_engine, team):
    cur = db_engine.cursor()
    _set_access(cur, "mgr_viewer", "tickets", "view")
    db_engine.commit()
    cur.close()
    _login(client, role="mgr_viewer")
    resp = client.post(f"/ticket_action/{team['MGRT02']}", data={"status": "Resolved"},
                       headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 403


def test_dashboard_announcements_card_follows_access(client, db_engine, team):
    cur = db_engine.cursor()
    _set_access(cur, "manager", "announcements", "edit")
    db_engine.commit()
    _login(client)
    assert b'class="u-tdec-none" href="/announcements"' in client.get("/admin").data
    _set_access(cur, "manager", "announcements", "none")
    db_engine.commit()
    cur.close()
    assert b'class="u-tdec-none" href="/announcements"' not in client.get("/admin").data


class TestDashboardShowsOnlyGrantedModules:
    """The main Dashboard (/admin) shows a role-holder only the stats,
    module cards and quick actions of modules their role can use."""

    @pytest.fixture
    def leaves_only(self, db_engine, team):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO roles (role_key, display_name, base_type, is_system) "
                    "VALUES ('leave_desk','Leave Desk','manager',FALSE) ON CONFLICT (role_key) DO NOTHING")
        from utils.helpers.rbac import RBAC_MODULES
        cur.executemany("INSERT INTO role_permissions (role_key, module, access) VALUES ('leave_desk',%s,%s) "
                        "ON CONFLICT (role_key, module) DO UPDATE SET access=EXCLUDED.access",
                        [(m, "edit" if m == "leaves" else "none") for m in RBAC_MODULES])
        db_engine.commit()
        cur.close()

    def test_role_sees_only_its_modules(self, client, leaves_only):
        _login(client, role="leave_desk")
        page = client.get("/admin").data
        assert b"Your Modules" in page and b"Tickets \xc2\xb7 Leaves \xc2\xb7 Resign." in page
        for hidden in (b"Total Employees", b"Present Today", b"OT Pending", b"Onboarding Overview",
                       b"Employee Management", b"Attendance Tracking", b"Shifts &amp; Breaks",
                       b"Salary &amp; Payslips", b"Export Month", b"sendAbsenteeReport()\" class",
                       b"Set Daily Salary", b"Register Employee", b"Attendance Trend"):
            assert hidden not in page, hidden
        # Edit access to Leaves is what puts "Add Holiday" on their dashboard.
        assert b"Add Holiday" in page

    def test_quick_actions_follow_module_access(self, client, db_engine, leaves_only):
        """The holiday and daily-salary actions need Leaves / Salary edit
        access -- on the dashboard and at /admin_action itself."""
        _login(client, role="leave_desk")
        resp = client.post("/admin_action", data={"action": "salary", "emp_id": "MGRT02", "salary": "999"})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin")
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM salary_config WHERE employee_id='MGRT02'")
        assert cur.fetchone() is None, "salary was written without Salary access"
        client.post("/admin_action", data={"action": "holiday", "date": "2026-12-25", "holiday_name": "Xmas"})
        cur.execute("SELECT name FROM holidays WHERE date='2026-12-25'")
        assert (cur.fetchone() or [None])[0] == "Xmas"
        cur.close()

    def test_owner_admin_sees_everything(self, client, seed_admin):
        with client.session_transaction() as sess:
            sess.clear()
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        page = client.get("/admin").data
        for shown in (b"Admin Modules", b"Total Employees", b"Export Month", b"Email Absentees",
                      b"Shifts &amp; Breaks", b"Add Holiday", b"Set Daily Salary", b"Register Employee"):
            assert shown in page, shown

    @pytest.mark.parametrize("path", ["/today_present", "/today_absent", "/today_late",
                                      "/monthly_report_export", "/api/dashboard_live",
                                      "/api/attendance_chart_data"])
    def test_attendance_pages_need_attendance_access(self, client, leaves_only, path):
        _login(client, role="leave_desk")
        resp = client.get(path, headers={"Accept": "application/json"})
        assert resp.status_code == 403

    def test_emailing_absentees_needs_attendance_edit(self, client, leaves_only):
        _login(client, role="leave_desk")
        resp = client.post("/send_absentee_report", headers={"Accept": "application/json"})
        assert resp.status_code == 403
