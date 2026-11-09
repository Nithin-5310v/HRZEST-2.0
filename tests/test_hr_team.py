"""The Organization page's HR team tab (blueprints/hr_team.py): the company
admin makes employees HRs and turns their HR dashboard access on and off;
an HR without access signs in as a plain employee; an HR adding an employee
can assign them to another HR; the admin reassigns an employee's HR from
their profile."""
import io

import pytest

from tests.test_employee_welcome_email import _fake_jpeg_bytes, _mock_face_detected
from utils.auth import generate_password_hash

PASSWORD = "HrTeam@1234"


def _admin(client, username="test_admin", role="admin"):
    with client.session_transaction() as sess:
        sess.clear()
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def people(db_engine, seed_admin):
    """Makes employees on demand: people("HRT01", role="HR")."""
    made = []
    cur = db_engine.cursor()

    def make(emp_id, name=None, role=None, assigned_hr=None, login=None):
        """login: None -- no HR login row yet (never signed in); 1/0 -- one with access on/off."""
        cur.execute("DELETE FROM login_attempts WHERE identifier=%s", (emp_id,))
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password, force_pin_change, role, assigned_hr_username) "
            "VALUES (%s,%s,%s,%s,0,%s,%s)",
            (emp_id, name or f"Person {emp_id}", f"{emp_id.lower()}@test.local", generate_password_hash(PASSWORD),
             role, assigned_hr),
        )
        if login is not None:
            cur.execute("INSERT INTO admin_users (username, password, role, is_active) VALUES (%s,'x','hr',%s)",
                        (emp_id, login))
        db_engine.commit()
        made.append(emp_id)
        return emp_id

    yield make
    for emp_id in made + ["HRTNEW1", "HRTNEW2"]:
        cur.execute("DELETE FROM api_tokens WHERE identity=%s", (emp_id,))
        cur.execute("DELETE FROM admin_users WHERE username=%s", (emp_id,))
        cur.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
    db_engine.commit()
    cur.close()


def _login_row(db_engine, username):
    cur = db_engine.cursor()
    cur.execute("SELECT role, is_active FROM admin_users WHERE username=%s", (username,))
    row = cur.fetchone()
    cur.close()
    return row


def _employee(db_engine, emp_id, col):
    cur = db_engine.cursor()
    cur.execute(f"SELECT {col} FROM employees WHERE employee_id=%s", (emp_id,))  # nosec B608 -- test literal
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


class TestHrTeamTab:
    def test_lists_hrs_and_who_can_be_added(self, client, people):
        people("HRT01", "Asha HR", role="HR")
        people("HRT02", "Ravi Dev", role="Developer", assigned_hr="HRT01")
        _admin(client)
        page = client.get("/profile/hr").get_data(as_text=True)
        assert "HR team" in page and "Asha HR" in page
        assert "Access on" in page and "Hasn't signed in yet" in page
        assert '<option value="HRT02">Ravi Dev (HRT02) -- Developer</option>' in page
        assert '<option value="HRT01">' not in page, "already an HR"

    def test_only_the_company_admin(self, client, db_engine, people):
        people("HRT01", role="HR", login=1)
        people("HRT04", role="HR")
        _admin(client, "HRT01", role="hr")
        assert client.get("/profile/hr").status_code in (302, 403)
        assert client.post("/profile/hr/HRT04/access", data={"access": "0"}).status_code in (302, 403)
        assert _login_row(db_engine, "HRT04") is None, "an HR can't switch another HR off"

    def test_add_hr_with_access(self, client, db_engine, people):
        people("HRT02", "Ravi Dev", role="Developer")
        _admin(client)
        resp = client.post("/profile/hr/add", data={"employee_id": "HRT02", "access": "1"}, follow_redirects=True)
        assert b"Ravi Dev is now an HR." in resp.data
        assert _employee(db_engine, "HRT02", "role") == "HR"
        assert _login_row(db_engine, "HRT02") == ("hr", 1)

    def test_add_hr_without_access_yet(self, client, db_engine, people):
        people("HRT02", role="Developer")
        _admin(client)
        client.post("/profile/hr/add", data={"employee_id": "HRT02"})
        assert _login_row(db_engine, "HRT02") == ("hr", 0)

    def test_add_refuses_an_admin_login_name(self, client, db_engine, people):
        people("HRT03", role="Developer")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO admin_users (username, password, role) VALUES ('HRT03', 'x', 'admin')")
        db_engine.commit()
        cur.close()
        _admin(client)
        resp = client.post("/profile/hr/add", data={"employee_id": "HRT03", "access": "1"}, follow_redirects=True)
        assert b"also the name of an admin login" in resp.data
        assert _login_row(db_engine, "HRT03") == ("admin", 1)
        assert _employee(db_engine, "HRT03", "role") == "Developer"

    def test_add_refuses_unknown_employee(self, client, seed_admin):
        _admin(client)
        resp = client.post("/profile/hr/add", data={"employee_id": "NOPE999", "access": "1"}, follow_redirects=True)
        assert b"Choose an active employee" in resp.data

    def test_stop_and_give_access(self, client, db_engine, people):
        people("HRT01", "Asha HR", role="HR")
        _admin(client)
        resp = client.post("/profile/hr/HRT01/access", data={"access": "0"}, follow_redirects=True)
        assert b"HR dashboard access turned off for Asha HR." in resp.data
        assert _login_row(db_engine, "HRT01") == ("hr", 0), "a row is made even before they ever signed in"
        assert b"Access off" in resp.data
        client.post("/profile/hr/HRT01/access", data={"access": "1"})
        assert _login_row(db_engine, "HRT01") == ("hr", 1)

    def test_access_only_for_hrs(self, client, db_engine, people):
        people("HRT02", role="Developer")
        _admin(client)
        resp = client.post("/profile/hr/HRT02/access", data={"access": "1"}, follow_redirects=True)
        assert b"isn&#39;t on your HR team" in resp.data
        assert _login_row(db_engine, "HRT02") is None


class TestSigningInWithoutAccess:
    def test_hr_without_access_gets_the_employee_portal(self, client, db_engine, people):
        people("HRT01", role="HR")
        _admin(client)
        client.post("/profile/hr/HRT01/access", data={"access": "0"})
        client.post("/logout")
        resp = client.post("/login", data={"identifier": "HRT01", "password": PASSWORD})
        assert "/hr_dashboard" not in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert sess.get("employee_id") == "HRT01" and not sess.get("admin_logged_in")

    def test_hr_with_access_gets_the_hr_dashboard(self, client, people):
        people("HRT01", role="HR")
        resp = client.post("/login", data={"identifier": "HRT01", "password": PASSWORD})
        assert resp.headers["Location"].endswith("/hr_dashboard")

    def test_turning_access_off_ends_an_open_hr_session(self, client, db_engine, people):
        people("HRT01", role="HR")
        client.post("/login", data={"identifier": "HRT01", "password": PASSWORD})
        assert client.get("/hr_dashboard").status_code == 200
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username='HRT01'")
        db_engine.commit()
        cur.close()
        assert client.get("/hr_dashboard").status_code in (301, 302)

    def test_app_signs_them_in_as_an_employee(self, client, db_engine, people):
        people("HRT01", role="HR")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO admin_users (username, password, role, is_active) VALUES ('HRT01', 'x', 'hr', 0)")
        db_engine.commit()
        cur.close()
        body = client.post("/api/signin", json={"identifier": "HRT01", "password": PASSWORD}).get_json()
        assert body["account"] == "employee" and body["token"]


class TestAssigningEmployees:
    def _add(self, client, emp_id, **extra):
        data = {"name": f"New {emp_id}", "emp_id": emp_id, "email": f"{emp_id.lower()}@test.local",
                "role": "Developer", "face": (io.BytesIO(_fake_jpeg_bytes()), "face.jpg")}
        data.update(extra)
        return client.post("/add_employee_page", data=data, follow_redirects=True)

    def test_hr_assigns_a_new_employee_to_another_hr(self, client, db_engine, people, monkeypatch):
        _mock_face_detected(monkeypatch)
        people("HRT01", "Asha HR", role="HR", login=1)
        people("HRT04", "Bala HR", role="HR")
        _admin(client, "HRT01", role="hr")
        resp = self._add(client, "HRTNEW1", assigned_hr_username="HRT04")
        assert _employee(db_engine, "HRTNEW1", "assigned_hr_username") == "HRT04"
        assert b"assigned to Bala HR (HRT04)" in resp.data

    def test_hr_defaults_to_themselves(self, client, db_engine, people, monkeypatch):
        _mock_face_detected(monkeypatch)
        people("HRT01", role="HR", login=1)
        _admin(client, "HRT01", role="hr")
        self._add(client, "HRTNEW1")
        assert _employee(db_engine, "HRTNEW1", "assigned_hr_username") == "HRT01"

    def test_hr_without_access_cannot_be_chosen(self, client, db_engine, people, monkeypatch):
        _mock_face_detected(monkeypatch)
        people("HRT01", role="HR", login=1)
        people("HRT04", role="HR", login=0)
        _admin(client, "HRT01", role="hr")
        self._add(client, "HRTNEW1", assigned_hr_username="HRT04")
        assert _employee(db_engine, "HRTNEW1", "assigned_hr_username") == "HRT01"

    def test_the_picker_shows_names(self, client, people):
        people("HRT01", "Asha HR", role="HR", login=1)
        _admin(client, "HRT01", role="hr")
        page = client.get("/employees").get_data(as_text=True)
        assert 'value="HRT01" selected>Asha HR (HRT01) — you' in page

    def test_admin_reassigns_from_the_profile(self, client, db_engine, people):
        people("HRT01", "Asha HR", role="HR")
        people("HRT04", "Bala HR", role="HR")
        people("HRT02", "Ravi Dev", role="Developer", assigned_hr="HRT01")
        _admin(client)
        page = client.get("/employee_profile/HRT02").get_data(as_text=True)
        assert "Assigned HR" in page and 'value="HRT01" selected' in page
        resp = client.post("/employee_profile/HRT02/assigned_hr", data={"assigned_hr_username": "HRT04"},
                           follow_redirects=True)
        assert b"Ravi Dev is now managed by Bala HR (HRT04)." in resp.data
        assert _employee(db_engine, "HRT02", "assigned_hr_username") == "HRT04"
        client.post("/employee_profile/HRT02/assigned_hr", data={"assigned_hr_username": ""})
        assert _employee(db_engine, "HRT02", "assigned_hr_username") is None

    def test_reassign_refuses_an_hr_without_access(self, client, db_engine, people):
        people("HRT04", role="HR")
        people("HRT02", role="Developer")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO admin_users (username, password, role, is_active) VALUES ('HRT04', 'x', 'hr', 0)")
        db_engine.commit()
        cur.close()
        _admin(client)
        resp = client.post("/employee_profile/HRT02/assigned_hr", data={"assigned_hr_username": "HRT04"},
                           follow_redirects=True)
        assert b"Choose an HR who has HR dashboard access." in resp.data
        assert _employee(db_engine, "HRT02", "assigned_hr_username") is None

    def test_an_hr_cannot_reassign(self, client, db_engine, people):
        people("HRT01", role="HR", login=1)
        people("HRT04", role="HR")
        people("HRT02", role="Developer", assigned_hr="HRT01")
        _admin(client, "HRT01", role="hr")
        client.post("/employee_profile/HRT02/assigned_hr", data={"assigned_hr_username": "HRT04"})
        assert _employee(db_engine, "HRT02", "assigned_hr_username") == "HRT01"
        page = client.get("/employee_profile/HRT02").get_data(as_text=True)
        assert "/assigned_hr" not in page
