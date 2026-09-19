"""Route-level tests for blueprints/attendance.py branches not already
covered by tests/test_attendance.py, tests/test_sync_punches.py, or
tests/test_device_risk.py — mostly shift/break management success paths,
shift-swap workflow, reporting pages, and the /api/shifts + /api/attendance
REST endpoints.
"""
import datetime
import hashlib
import secrets
import pytest


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _employee_session(client, employee_id):
    with client.session_transaction() as sess:
        sess["employee_id"] = employee_id


def _make_token(db_engine, identity, token_type="employee"):
    """Mints a real, live api_tokens row and returns the raw bearer value --
    same pattern tests/test_employees_coverage.py's _make_admin_token uses.
    Caller is responsible for cleanup (the row is left for the per-test DB
    snapshot/restore fixture to clear)."""
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) VALUES (%s,%s,%s,%s)",
        (identity, token_hash, token_type, expiry)
    )
    cur.close()
    return raw


def _admin_bearer_token(client, seed_admin):
    resp = client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"]})
    return resp.get_json()["token"]


@pytest.fixture
def temp_shift(db_engine):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO shifts (name, start_time, half_time, end_time) VALUES (%s,%s,%s,%s) RETURNING id",
        ("Route Test Shift", "09:00:00", "13:00:00", "18:00:00"),
    )
    sid = cur.fetchone()[0]
    try:
        yield sid
    finally:
        cur.execute("UPDATE employees SET shift_id=NULL WHERE shift_id=%s", (sid,))
        cur.execute("UPDATE break_config SET shift_id=NULL WHERE shift_id=%s", (sid,))
        cur.execute("DELETE FROM shifts WHERE id=%s", (sid,))
        cur.close()


class TestAddShift:
    def test_missing_fields_no_op(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_shift", data={"shift_name": ""}, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_creates_shift(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_shift", data={
            "shift_name": "New Route Shift", "start_time": "08:00", "half_time": "12:00",
            "end_time": "17:00"}, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM shifts WHERE name='New Route Shift'")
        row = cur.fetchone()
        assert row is not None
        cur.execute("DELETE FROM shifts WHERE name='New Route Shift'")
        cur.close()


class TestDeleteShiftForm:
    def test_success(self, client, seed_admin, temp_shift, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/delete_shift/{temp_shift}", follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT 1 FROM shifts WHERE id=%s", (temp_shift,))
        assert cur.fetchone() is None
        cur.close()


class TestEditShift:
    def test_success_via_url_id(self, client, seed_admin, temp_shift, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/edit_shift/{temp_shift}", data={
            "shift_name": "Renamed Shift", "start_time": "09:30", "half_time": "13:30", "end_time": "18:30"},
            follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM shifts WHERE id=%s", (temp_shift,))
        assert cur.fetchone()[0] == "Renamed Shift"
        cur.close()

    def test_unparseable_id_in_url_404s(self, client, seed_admin):
        """/edit_shift/<int:sid> -- the id lives in the URL path, so a
        non-numeric id 404s at the routing layer rather than reaching the
        view at all."""
        _admin_session(client, seed_admin["username"])
        resp = client.post("/edit_shift/not-an-int", data={"shift_name": "x"}, follow_redirects=False)
        assert resp.status_code == 404


class TestBulkAssignShift:
    def test_assign_by_emp_ids(self, client, seed_admin, seed_employee, temp_shift, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/bulk_assign_shift", data={
            "shift_id": str(temp_shift), "emp_ids": [seed_employee["employee_id"]]}, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == temp_shift
        cur.close()

    def test_assign_by_department(self, client, seed_admin, seed_employee, temp_shift, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET department='RouteDept' WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        _admin_session(client, seed_admin["username"])
        resp = client.post("/bulk_assign_shift", data={
            "shift_id": str(temp_shift), "dept_filter": "RouteDept"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == temp_shift
        cur.execute("UPDATE employees SET department=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()


class TestUpdateDefaultShift:
    def test_missing_fields_redirects_with_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/update_default_shift", data={"shift_start": ""}, follow_redirects=False)
        assert resp.status_code == 302
        assert "error" in resp.headers["Location"]

    def test_success(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/update_default_shift", data={
            "shift_start": "09:00", "shift_half": "13:00", "shift_end": "18:00"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "default_saved=1" in resp.headers["Location"]


class TestAssignShift:
    def test_success(self, client, seed_admin, seed_employee, temp_shift, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/assign_shift", data={
            "emp_id": seed_employee["employee_id"], "shift_id": str(temp_shift)})
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == temp_shift
        cur.close()


class TestApiBreaks:
    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/breaks")
        assert resp.status_code == 401

    def test_employee_session_allowed(self, client, seed_employee):
        _employee_session(client, seed_employee["employee_id"])
        resp = client.get("/api/breaks")
        assert resp.status_code == 200

    def test_garbage_bearer_token_returns_401(self, client):
        """Regression test: this route used to accept ANY string starting
        with "Bearer " with no lookup against api_tokens at all -- a
        garbage token got the same 200 a real session got."""
        resp = client.get("/api/breaks", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_valid_employee_bearer_token_allowed(self, client, db_engine, seed_employee):
        token = _make_token(db_engine, seed_employee["employee_id"], "employee")
        resp = client.get("/api/breaks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_valid_admin_bearer_token_allowed(self, client, db_engine, seed_admin):
        token = _make_token(db_engine, seed_admin["username"], "admin")
        resp = client.get("/api/breaks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_expired_bearer_token_returns_401(self, client, db_engine, seed_employee):
        token_hash = hashlib.sha256(b"expired-token-value").hexdigest()
        cur = db_engine.cursor()
        # expires_at computed via the DB's own NOW() (not Python's local-tz
        # datetime.now()) -- api_tokens.expires_at is TIMESTAMP WITHOUT TIME
        # ZONE, compared against NOW() in the DB session's timezone. A
        # Python-side "now() - 1h" in a local timezone ahead of the DB
        # session's (e.g. IST, UTC+5:30) can still land in the DB's future,
        # making an intentionally-expired fixture row look unexpired.
        cur.execute(
            "INSERT INTO api_tokens (identity, token, token_type, expires_at) "
            "VALUES (%s,%s,'employee',NOW() - INTERVAL '1 hour')",
            (seed_employee["employee_id"], token_hash),
        )
        cur.close()
        resp = client.get("/api/breaks", headers={"Authorization": "Bearer expired-token-value"})
        assert resp.status_code == 401


class TestAddBreak:
    def test_missing_fields_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_break", data={"break_name": ""}, follow_redirects=True)
        assert b"required" in resp.data

    def test_success(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_break", data={
            "break_name": "Route Lunch", "break_time": "13:00", "duration_minutes": "30"},
            follow_redirects=True)
        assert b"added successfully" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM break_config WHERE break_name='Route Lunch'")
        bid = cur.fetchone()[0]
        cur.execute("DELETE FROM break_config WHERE id=%s", (bid,))
        cur.close()


class TestUpdateBreak:
    def test_unparseable_id_in_url_404s(self, client, seed_admin):
        """/update_break/<int:bid> -- the id lives in the URL path, so a
        non-numeric id 404s at the routing layer."""
        _admin_session(client, seed_admin["username"])
        resp = client.post("/update_break/bad", data={"break_name": "x"}, follow_redirects=False)
        assert resp.status_code == 404

    def test_success_via_url_id(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO break_config (break_name, break_time, duration_minutes) "
                    "VALUES ('Old Break','12:00',15) RETURNING id")
        bid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.post(f"/update_break/{bid}", data={
                "break_name": "Updated Break", "break_time": "12:30", "duration_minutes": "20"},
                follow_redirects=True)
            assert b"Break updated" in resp.data
            cur.execute("SELECT break_name FROM break_config WHERE id=%s", (bid,))
            assert cur.fetchone()[0] == "Updated Break"
        finally:
            cur.execute("DELETE FROM break_config WHERE id=%s", (bid,))
            cur.close()


class TestDeleteBreak:
    def test_success_via_url_id(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO break_config (break_name, break_time, duration_minutes) "
                    "VALUES ('ToDelete','12:00',10) RETURNING id")
        bid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/delete_break/{bid}", follow_redirects=True)
        assert b"Break deleted" in resp.data
        cur.execute("SELECT 1 FROM break_config WHERE id=%s", (bid,))
        assert cur.fetchone() is None
        cur.close()

    def test_unparseable_id_in_url_404s(self, client, seed_admin):
        """/delete_break/<int:bid> -- the id lives in the URL path, so a
        non-numeric id 404s at the routing layer."""
        _admin_session(client, seed_admin["username"])
        resp = client.post("/delete_break/bad", follow_redirects=False)
        assert resp.status_code == 404


class TestMonthlyReport:
    def test_renders_with_data(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO attendance (employee_id, date, login_time, logout_time, attendance_type) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (employee_id, date) DO NOTHING",
            (seed_employee["employee_id"], today, "09:00:00", "18:00:00", "Full Day"),
        )
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.get(f"/monthly_report?year={today.year}&month={today.month}")
            assert resp.status_code == 200
        finally:
            cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            cur.close()

    def test_scoped_to_active_company(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Att Route Co') RETURNING id")
        cid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = cid
        try:
            resp = client.get("/monthly_report")
            assert resp.status_code == 200
        finally:
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()


class TestEmployeeAttendanceDetail:
    def test_unknown_employee_404(self, client, seed_admin):
        today = datetime.date.today()
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/employee_attendance_detail/GHOST_EMP/{today.year}/{today.month}")
        assert resp.status_code == 404

    def test_known_employee_renders(self, client, seed_admin, seed_employee):
        today = datetime.date.today()
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/employee_attendance_detail/{seed_employee['employee_id']}/{today.year}/{today.month}")
        assert resp.status_code == 200


class TestCorrectAttendance:
    def test_missing_fields_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/correct_attendance", data={"emp_id": ""}, follow_redirects=False)
        assert resp.status_code == 302

    def test_invalid_date_flashes_error(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/correct_attendance", data={
            "emp_id": seed_employee["employee_id"], "date": "not-a-date", "attendance_type": "Full Day"},
            follow_redirects=False)
        assert resp.status_code == 302

    def test_creates_new_record(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        _admin_session(client, seed_admin["username"])
        resp = client.post("/correct_attendance", data={
            "emp_id": seed_employee["employee_id"], "date": today.isoformat(),
            "attendance_type": "Full Day", "year": str(today.year), "month": str(today.month),
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT attendance_type FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        assert cur.fetchone()[0] == "Full Day"
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

    def test_updates_existing_record(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO attendance (employee_id, date, attendance_type) VALUES (%s,%s,%s) "
            "ON CONFLICT (employee_id, date) DO UPDATE SET attendance_type=EXCLUDED.attendance_type",
            (seed_employee["employee_id"], today, "Absent"),
        )
        _admin_session(client, seed_admin["username"])
        try:
            client.post("/correct_attendance", data={
                "emp_id": seed_employee["employee_id"], "date": today.isoformat(),
                "attendance_type": "Half Day", "year": str(today.year), "month": str(today.month),
            })
            cur.execute("SELECT attendance_type, status FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            assert cur.fetchone() == ("Half Day", "Manual")
        finally:
            cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            cur.close()


class TestBulkMarkAttendance:
    def test_get_renders(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/bulk_mark_attendance")
        assert resp.status_code == 200

    def test_get_with_invalid_date_falls_back_to_today(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/bulk_mark_attendance?date=not-a-date")
        assert resp.status_code == 200

    def test_post_invalid_date_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/bulk_mark_attendance", data={"date": "bad"}, follow_redirects=True)
        assert b"Invalid date" in resp.data

    def test_post_marks_attendance_for_active_employees(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.post("/bulk_mark_attendance", data={
                "date": today.isoformat(),
                f"att_{seed_employee['employee_id']}": "Full Day",
            }, follow_redirects=True)
            assert b"Attendance saved" in resp.data
            cur = db_engine.cursor()
            cur.execute("SELECT attendance_type FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            assert cur.fetchone()[0] == "Full Day"
            cur.close()
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            cur.close()


class TestMonthlyReportExport:
    def test_returns_xlsx_file(self, client, seed_admin):
        today = datetime.date.today()
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/monthly_report_export?year={today.year}&month={today.month}")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class TestSendAbsenteeReport:
    def test_no_config_returns_ok_false(self, client, seed_admin, monkeypatch):
        import blueprints.attendance as attendance_module
        monkeypatch.setattr(attendance_module, "get_email_config", lambda: None)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/send_absentee_report")
        assert resp.get_json()["ok"] is False

    def test_success(self, client, seed_admin, seed_employee, monkeypatch):
        import blueprints.attendance as attendance_module
        monkeypatch.setattr(attendance_module, "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})
        monkeypatch.setattr(attendance_module, "send_email_smtp", lambda *a, **k: None)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/send_absentee_report")
        assert resp.get_json()["ok"] is True

    def test_send_failure(self, client, seed_admin, monkeypatch):
        import blueprints.attendance as attendance_module
        monkeypatch.setattr(attendance_module, "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})

        def _raise(*a, **k):
            raise RuntimeError("smtp down")
        monkeypatch.setattr(attendance_module, "send_email_smtp", _raise)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/send_absentee_report")
        assert resp.get_json()["ok"] is False


class TestApiCheckin:
    def test_missing_emp_id_returns_400(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/attendance/checkin", json={},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_unknown_employee(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/attendance/checkin", json={"employee_id": "GHOST_EMP"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.get_json()["ok"] is False

    def test_first_call_of_day_logs_in(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        token = _admin_bearer_token(client, seed_admin)
        try:
            resp = client.post("/api/attendance/checkin", json={"employee_id": seed_employee["employee_id"]},
                               headers={"Authorization": f"Bearer {token}"})
            data = resp.get_json()
            assert data["ok"] is True
            assert data["type"] == "login"
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            cur.close()

    def test_second_call_logs_out(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        token = _admin_bearer_token(client, seed_admin)
        try:
            client.post("/api/attendance/checkin", json={"employee_id": seed_employee["employee_id"]},
                        headers={"Authorization": f"Bearer {token}"})
            resp = client.post("/api/attendance/checkin", json={"employee_id": seed_employee["employee_id"]},
                               headers={"Authorization": f"Bearer {token}"})
            data = resp.get_json()
            assert data["ok"] is True
            assert data["type"] == "logout"
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            cur.close()

    def test_third_call_is_relogin(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        token = _admin_bearer_token(client, seed_admin)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            client.post("/api/attendance/checkin", json={"employee_id": seed_employee["employee_id"]}, headers=headers)
            client.post("/api/attendance/checkin", json={"employee_id": seed_employee["employee_id"]}, headers=headers)
            resp = client.post("/api/attendance/checkin", json={"employee_id": seed_employee["employee_id"]}, headers=headers)
            assert resp.get_json()["type"] == "relogin"
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                        (seed_employee["employee_id"], today))
            cur.close()

    def test_wfh_outside_registered_location_rejected(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET work_mode='wfh', work_lat=12.9, work_lon=77.6 WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        token = _admin_bearer_token(client, seed_admin)
        try:
            resp = client.post("/api/attendance/checkin", json={
                "employee_id": seed_employee["employee_id"], "lat": 40.0, "lon": -70.0},
                headers={"Authorization": f"Bearer {token}"})
            assert resp.get_json()["ok"] is False
        finally:
            cur.execute("UPDATE employees SET work_mode='office', work_lat=NULL, work_lon=NULL WHERE employee_id=%s",
                        (seed_employee["employee_id"],))
            cur.close()


class TestApiShifts:
    def test_get_list(self, client, seed_admin, temp_shift):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.get("/api/shifts", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert any(s["id"] == temp_shift for s in resp.get_json()["shifts"])

    def test_create_missing_fields_returns_400(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/shifts", json={"name": ""}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_create_success(self, client, seed_admin, db_engine):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/shifts", json={
            "name": "Api Created Shift", "start_time": "09:00", "half_time": "13:00", "end_time": "18:00"},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        sid = resp.get_json()["id"]
        cur = db_engine.cursor()
        cur.execute("DELETE FROM shifts WHERE id=%s", (sid,))
        cur.close()

    def test_delete(self, client, seed_admin, temp_shift):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.delete(f"/api/shifts/{temp_shift}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_assign_missing_emp_id_returns_400(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/shifts/assign", json={}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_assign_success(self, client, seed_admin, seed_employee, temp_shift, db_engine):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/shifts/assign", json={
            "emp_id": seed_employee["employee_id"], "shift_id": temp_shift},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == temp_shift
        cur.close()
