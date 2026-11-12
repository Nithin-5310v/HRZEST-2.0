"""One click from the admin side to your own employee dashboard, and one
click back (blueprints/auth.py switch_to_my_employee_portal /
switch_back_to_hr_panel) for an HR or manager whose login is their
employee ID, and for the company admin, whose own record is the profile
linked to their login (utils/own_profile.py). The sidebar no longer has a
"My Employee Dashboard" item; the Organization page's own-profile card
keeps its "Open my employee dashboard" button."""
import pytest

from utils.auth import HR_ROLE

SWITCH = b'action="/switch_to_my_employee_portal"'
BACK = b'action="/switch_back_to_hr_panel"'
WORKSPACE = b'href="/my_workspace"'


def _login(client, username, role):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def hr_with_record(db_engine, seed_hr_admin):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO employees (employee_id, name, role, password, force_pin_change) "
                "VALUES (%s, 'Hema HR', 'HR', 'x', 0) ON CONFLICT (employee_id) DO NOTHING",
                (seed_hr_admin["username"],))
    db_engine.commit()
    yield seed_hr_admin
    cur.execute("DELETE FROM employees WHERE employee_id=%s", (seed_hr_admin["username"],))
    db_engine.commit()
    cur.close()


@pytest.fixture
def admin_profile(db_engine, seed_admin):
    """Links an employee record to the company admin's login."""
    cur = db_engine.cursor()
    cur.execute("INSERT INTO employees (employee_id, name, password, force_pin_change) "
                "VALUES ('OWNSW01', 'Owner Admin', 'x', 0) ON CONFLICT (employee_id) DO NOTHING")
    cur.execute("UPDATE admin_users SET linked_employee_id='OWNSW01' WHERE username=%s", (seed_admin["username"],))
    db_engine.commit()
    yield "OWNSW01"
    cur.execute("UPDATE admin_users SET linked_employee_id=NULL WHERE username=%s", (seed_admin["username"],))
    cur.execute("DELETE FROM employees WHERE employee_id='OWNSW01'")
    db_engine.commit()
    cur.close()


class TestHr:
    def test_sidebar_has_no_employee_dashboard_item(self, client, hr_with_record):
        _login(client, hr_with_record["username"], HR_ROLE)
        page = client.get("/employees").data
        assert SWITCH not in page and b"My Employee Dashboard" not in page

    def test_switches_and_comes_back_to_the_hr_dashboard(self, client, hr_with_record):
        _login(client, hr_with_record["username"], HR_ROLE)
        resp = client.post("/switch_to_my_employee_portal")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/employee_portal")
        page = client.get("/employee_portal").data
        assert BACK in page and b"Back to HR Dashboard" in page
        resp = client.post("/switch_back_to_hr_panel")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin")
        with client.session_transaction() as sess:
            assert sess["admin_username"] == hr_with_record["username"] and sess["admin_role"] == HR_ROLE
            assert "employee_id" not in sess

    def test_no_button_without_a_record_of_their_own(self, client, seed_hr_admin):
        _login(client, seed_hr_admin["username"], HR_ROLE)
        assert SWITCH not in client.get("/employees").data


class TestMyWorkspace:
    """A role-holder's own employee details are a module of their role
    dashboard (blueprints/core.py's my_workspace), not a separate one."""

    def test_sidebar_links_to_my_workspace(self, client, hr_with_record):
        _login(client, hr_with_record["username"], HR_ROLE)
        page = client.get("/employees").data
        assert WORKSPACE in page and b"My Workspace" in page

    def test_opens_inside_the_role_dashboard(self, client, hr_with_record):
        _login(client, hr_with_record["username"], HR_ROLE)
        resp = client.get("/my_workspace")
        assert resp.status_code == 200 and b"Hema HR" in resp.data
        with client.session_transaction() as sess:
            assert sess["admin_logged_in"] and "employee_id" not in sess

    def test_not_shown_without_a_record_of_their_own(self, client, seed_hr_admin):
        _login(client, seed_hr_admin["username"], HR_ROLE)
        assert WORKSPACE not in client.get("/employees").data

    def test_company_admin_with_a_profile_opens_it(self, client, seed_admin, admin_profile):
        _login(client, seed_admin["username"], "admin")
        assert WORKSPACE in client.get("/admin").data
        resp = client.get("/my_workspace")
        assert resp.status_code == 200 and b"Owner Admin" in resp.data

    def test_company_admin_without_a_profile_is_led_to_create_one(self, client, seed_admin):
        _login(client, seed_admin["username"], "admin")
        assert WORKSPACE in client.get("/admin").data
        resp = client.get("/my_workspace")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/profile#my-employee-profile")


class TestCompanyAdmin:
    def test_sidebar_has_no_employee_dashboard_item(self, client, seed_admin):
        _login(client, seed_admin["username"], "admin")
        assert SWITCH not in client.get("/admin").data

    def test_without_a_profile_it_leads_to_creating_one(self, client, seed_admin):
        _login(client, seed_admin["username"], "admin")
        resp = client.post("/switch_to_my_employee_portal")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/profile#my-employee-profile")
        with client.session_transaction() as sess:
            assert sess["admin_logged_in"] and "employee_id" not in sess

    def test_switches_into_the_linked_profile_and_back(self, client, seed_admin, admin_profile):
        _login(client, seed_admin["username"], "admin")
        resp = client.post("/switch_to_my_employee_portal")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/employee_portal")
        with client.session_transaction() as sess:
            assert sess["employee_id"] == admin_profile and "admin_logged_in" not in sess
        page = client.get("/employee_portal").data
        assert b"Back to Admin Dashboard" in page
        resp = client.post("/switch_back_to_hr_panel")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess["admin_logged_in"] and sess["admin_username"] == seed_admin["username"]
            assert sess["admin_role"] == "admin" and "employee_id" not in sess

    def test_a_deactivated_profile_is_not_switched_into(self, client, db_engine, seed_admin, admin_profile):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET is_active=0 WHERE employee_id=%s", (admin_profile,))
        db_engine.commit()
        cur.close()
        _login(client, seed_admin["username"], "admin")
        resp = client.post("/switch_to_my_employee_portal")
        assert "/profile" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert "employee_id" not in sess


class TestSwitchBackGuard:
    def test_back_never_restores_a_different_login(self, client, seed_employee):
        # A plain employee session with forged return keys for another record.
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
            sess["_hr_return_username"] = "test_admin"
            sess["_hr_return_employee_id"] = "SOMEONE_ELSE"
            sess["_hr_return_role"] = "admin"
        resp = client.post("/switch_back_to_hr_panel")
        assert resp.headers["Location"].endswith("/employee_portal")
        with client.session_transaction() as sess:
            assert not sess.get("admin_logged_in")
