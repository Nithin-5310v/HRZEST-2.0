"""Leavers keep their records but lose access (utils/auth.py's
employment_ended_on and _employment_ended_sql): from the day after the last
working day of an ACCEPTED resignation, an employee can't sign in on the
website or in the app, and already-open sessions and app tokens stop too.
Nothing is deleted; declining the resignation gives access back."""
import datetime

import pytest

from extensions import app as flask_app
from utils.helpers import company_today


def _resign(db_engine, emp_id, last_day, status="Accepted"):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO resignation_requests (employee_id, last_working_day, reason, status) "
        "VALUES (%s,%s,%s,%s) RETURNING id", (emp_id, last_day, "Moving on", status))
    rid = cur.fetchone()[0]
    cur.close()
    return rid


def _set_status(db_engine, rid, status):
    cur = db_engine.cursor()
    cur.execute("UPDATE resignation_requests SET status=%s WHERE id=%s", (status, rid))
    cur.close()


@pytest.fixture
def today():
    with flask_app.app_context():
        return company_today()


@pytest.fixture(autouse=True)
def _clean_resignations(db_engine):
    yield
    cur = db_engine.cursor()
    cur.execute("DELETE FROM resignation_requests WHERE employee_id IN ('TST001', 'test_admin')")
    cur.close()


def _web_login(client, ident, password):
    return client.post("/login", data={"identifier": ident, "password": password}, follow_redirects=False)


def _api_login(ident, password):
    with flask_app.test_client() as c:
        return c.post("/api/employee/login", json={"employee_id": ident, "password": password})


class TestSignInAfterLastWorkingDay:
    def test_web_login_refused_with_a_clear_message(self, client, seed_employee, db_engine, today):
        _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=1))
        resp = _web_login(client, seed_employee["employee_id"], seed_employee["password"])
        assert resp.status_code == 200  # the login page again, not a redirect into the portal
        assert b"Your access ended after your last working day" in resp.data
        with client.session_transaction() as sess:
            assert not sess.get("employee_id")

    def test_app_login_refused_with_access_ended(self, seed_employee, db_engine, today):
        _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=3))
        resp = _api_login(seed_employee["employee_id"], seed_employee["password"])
        body = resp.get_json()
        assert resp.status_code == 403
        assert body["access_ended"] is True and "token" not in body
        assert "last working day" in body["msg"]

    def test_one_box_signin_refused_too(self, seed_employee, db_engine, today):
        _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=1))
        with flask_app.test_client() as c:
            resp = c.post("/api/signin", json={"identifier": seed_employee["employee_id"],
                                               "password": seed_employee["password"]})
        assert resp.status_code == 403
        assert resp.get_json()["access_ended"] is True

    def test_wrong_password_still_gets_the_generic_answer(self, seed_employee, db_engine, today):
        # The leaver message only follows a correct password -- it reveals nothing to a guesser.
        _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=1))
        resp = _api_login(seed_employee["employee_id"], "Wrong-Pass-1")
        assert resp.status_code == 401
        assert resp.get_json() == {"ok": False, "msg": "Invalid credentials"}

    def test_the_last_working_day_itself_still_works(self, seed_employee, db_engine, today):
        _resign(db_engine, seed_employee["employee_id"], today)
        resp = _api_login(seed_employee["employee_id"], seed_employee["password"])
        assert resp.status_code == 200 and resp.get_json()["token"]

    @pytest.mark.parametrize("status", ["Pending", "Declined"])
    def test_only_an_accepted_resignation_ends_access(self, seed_employee, db_engine, today, status):
        _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=5), status=status)
        resp = _api_login(seed_employee["employee_id"], seed_employee["password"])
        assert resp.status_code == 200 and resp.get_json()["token"]

    def test_declining_the_resignation_gives_access_back(self, seed_employee, db_engine, today):
        rid = _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=1))
        assert _api_login(seed_employee["employee_id"], seed_employee["password"]).status_code == 403
        _set_status(db_engine, rid, "Declined")
        assert _api_login(seed_employee["employee_id"], seed_employee["password"]).status_code == 200


class TestOpenSessionsStop:
    def test_open_web_session_is_signed_out(self, client, seed_employee, db_engine, today):
        resp = _web_login(client, seed_employee["employee_id"], seed_employee["password"])
        assert resp.status_code == 302
        assert client.get("/employee_portal", follow_redirects=False).status_code == 200
        _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=1))
        resp = client.get("/employee_portal", follow_redirects=False)
        assert resp.status_code == 302 and "login" in resp.headers["Location"]
        with client.session_transaction() as sess:
            assert not sess.get("employee_id")

    def test_app_token_stops_working(self, seed_employee, db_engine, today):
        token = _api_login(seed_employee["employee_id"], seed_employee["password"]).get_json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        with flask_app.test_client() as device:
            assert device.get("/api/employee/profile", headers=headers).status_code == 200
            _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=1))
            resp = device.get("/api/employee/profile", headers=headers)
            assert resp.status_code == 401
            assert resp.get_json()["access_ended"] is True
            # The dashboard endpoint that reads the token itself.
            assert device.get("/api/employee/portal", headers=headers).status_code == 401

    def test_hr_or_manager_admin_account_named_after_the_employee_stops(self, client, seed_admin, db_engine, today):
        # An HR/manager employee's admin account is named after their employee ID.
        cur = db_engine.cursor()
        cur.execute("INSERT INTO employees (employee_id, name, email, password) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (employee_id) DO NOTHING",
                    (seed_admin["username"], "Test HR", "hr@test.local", "unused"))
        try:
            token = client.post("/api/login", json={"username": seed_admin["username"],
                                                    "password": seed_admin["password"]}).get_json()["token"]
            _resign(db_engine, seed_admin["username"], today - datetime.timedelta(days=1))
            with flask_app.test_client() as device:
                resp = device.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
                assert resp.status_code == 401 and resp.get_json()["access_ended"] is True
                resp = device.post("/api/login", json={"username": seed_admin["username"],
                                                       "password": seed_admin["password"]})
                assert resp.status_code == 403 and resp.get_json()["access_ended"] is True
        finally:
            cur.execute("DELETE FROM resignation_requests WHERE employee_id=%s", (seed_admin["username"],))
            cur.execute("DELETE FROM employees WHERE employee_id=%s", (seed_admin["username"],))
            cur.close()


class TestRecordsAreKept:
    def test_the_employee_and_resignation_stay_in_the_database(self, client, seed_employee, db_engine, today):
        _resign(db_engine, seed_employee["employee_id"], today - datetime.timedelta(days=1))
        _api_login(seed_employee["employee_id"], seed_employee["password"])
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == seed_employee["name"]
        cur.execute("SELECT status FROM resignation_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == "Accepted"
        cur.close()
