"""Admin "Reset Employee Password" (blueprints/employees.py admin_action,
action=reset_password).

It used to set the password to the employee ID -- a value anyone who knows
the ID (printed on ID cards, payslips, QR badges) could log in with. It
must instead set a random temporary password, show it to the admin once,
revoke the employee's existing sessions/tokens, and make the employee
replace it at their next login, web or mobile.
"""
import re

from extensions import app as flask_app
from utils.auth import check_password_hash

_TEMP_RE = re.compile(rb"Temporary password: ([A-Za-z0-9_-]{12,})")
NEW_PW = "Brand-New-Pass-77"


def _admin_login(client, seed_admin):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})


def _reset(client, emp_id):
    resp = client.post("/admin_action", data={"action": "reset_password", "emp_id": emp_id, "next": "settings"},
                       follow_redirects=True)
    match = _TEMP_RE.search(resp.data)
    assert match, "temporary password was not shown to the admin"
    return match.group(1).decode()


def _employee_row(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("SELECT password, must_change_password FROM employees WHERE employee_id=%s", (emp_id,))
    row = cur.fetchone()
    cur.close()
    return row


class TestAdminResetPassword:
    def test_password_is_not_the_employee_id(self, client, seed_admin, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        _admin_login(client, seed_admin)
        _reset(client, emp_id)
        assert not check_password_hash(_employee_row(db_engine, emp_id)[0], emp_id)
        with flask_app.test_client() as other:
            resp = other.post("/api/employee/login", json={"employee_id": emp_id, "password": emp_id})
            assert resp.status_code == 401

    def test_random_temp_password_shown_once(self, client, seed_admin, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        _admin_login(client, seed_admin)
        temp = _reset(client, emp_id)
        assert check_password_hash(_employee_row(db_engine, emp_id)[0], temp)
        assert temp.encode() not in client.get("/settings").data
        assert _reset(client, emp_id) != temp

    def test_sets_must_change_password_flag(self, client, seed_admin, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        assert _employee_row(db_engine, emp_id)[1] == 0
        _admin_login(client, seed_admin)
        _reset(client, emp_id)
        assert _employee_row(db_engine, emp_id)[1] == 1

    def test_reset_revokes_existing_session_and_token(self, client, seed_admin, seed_employee):
        emp_id = seed_employee["employee_id"]
        token = client.post("/api/employee/login", json={
            "employee_id": emp_id, "password": seed_employee["password"]}).get_json()["token"]
        # Not a `with` block: two clients that both preserve request
        # context can't interleave requests.
        device = flask_app.test_client()
        device.post("/login", data={"identifier": emp_id, "password": seed_employee["password"]})
        assert device.get("/employee_portal", follow_redirects=False).status_code == 200

        _admin_login(client, seed_admin)
        _reset(client, emp_id)

        assert device.get("/employee_portal", follow_redirects=False).status_code == 302
        resp = device.get("/api/employee/portal", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_web_login_forces_change_then_clears_flag(self, client, seed_admin, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        _admin_login(client, seed_admin)
        temp = _reset(client, emp_id)

        device = flask_app.test_client()
        resp = device.post("/login", data={"identifier": emp_id, "password": temp})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/force_change_pin")
        resp = device.get("/employee_portal", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/force_change_pin")

        resp = device.post("/force_change_pin", data={"new_password": NEW_PW, "confirm_password": NEW_PW})
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/employee_portal")
        assert device.get("/employee_portal", follow_redirects=False).status_code == 200
        assert _employee_row(db_engine, emp_id)[1] == 0

    def test_mobile_login_refused_until_changed(self, client, seed_admin, seed_employee):
        emp_id = seed_employee["employee_id"]
        _admin_login(client, seed_admin)
        temp = _reset(client, emp_id)

        device = flask_app.test_client()
        resp = device.post("/api/employee/login", json={"employee_id": emp_id, "password": temp})
        assert resp.status_code == 403
        body = resp.get_json()
        assert body["ok"] is False and body["password_change_required"] is True and "token" not in body

        device.post("/login", data={"identifier": emp_id, "password": temp})
        device.post("/force_change_pin", data={"new_password": NEW_PW, "confirm_password": NEW_PW})
        resp = device.post("/api/employee/login", json={"employee_id": emp_id, "password": NEW_PW})
        assert resp.status_code == 200 and resp.get_json()["token"]

    def test_wrong_password_still_generic_while_flag_set(self, client, seed_admin, seed_employee):
        # The 403 above must only ever follow a correct password -- a wrong
        # one gets the same 401 as before, so the flag isn't enumerable.
        emp_id = seed_employee["employee_id"]
        _admin_login(client, seed_admin)
        _reset(client, emp_id)
        with flask_app.test_client() as other:
            resp = other.post("/api/employee/login", json={"employee_id": emp_id, "password": "Wrong-Pass-1"})
            assert resp.status_code == 401
            assert resp.get_json() == {"ok": False, "msg": "Invalid credentials"}

    def test_reset_link_clears_flag(self, client, seed_admin, seed_employee, db_engine):
        import datetime
        import hashlib
        import secrets
        emp_id = seed_employee["employee_id"]
        _admin_login(client, seed_admin)
        _reset(client, emp_id)

        raw = secrets.token_hex(32)
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET reset_token=%s, reset_token_expiry=%s WHERE employee_id=%s",
                    (hashlib.sha256(raw.encode()).hexdigest(),
                     datetime.datetime.utcnow() + datetime.timedelta(hours=1), emp_id))
        cur.close()
        with flask_app.test_client() as other:
            other.post(f"/employee_reset_password/{raw}", data={"new_password": NEW_PW, "confirm_password": NEW_PW})
        assert _employee_row(db_engine, emp_id)[1] == 0
        with flask_app.test_client() as other:
            resp = other.post("/api/employee/login", json={"employee_id": emp_id, "password": NEW_PW})
            assert resp.status_code == 200
