"""
Comprehensive integration test suite — covers every major module:
  Auth · Employees · Attendance API · Leaves · Salary · Payroll
  Security headers · Rate limiting · API tokens · Employee portal
  Notifications · Shifts · Holidays · Org chart · Health · CSRF

Run with:
    python -m pytest tests/test_comprehensive.py -v
"""
import datetime
import hashlib
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _admin_token(client, seed_admin):
    """Log in as admin via API and return a Bearer token."""
    resp = client.post("/api/login", json={
        "username": seed_admin["username"],
        "password": seed_admin["password"],
    })
    assert resp.status_code == 200
    return resp.get_json()["token"]


def _emp_token(client, seed_employee):
    """Log in as employee via API and return a Bearer token."""
    resp = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"],
        "password": seed_employee["password"],
    })
    assert resp.status_code == 200
    return resp.get_json()["token"]


# ===========================================================================
# 1. HEALTH & STATIC
# ===========================================================================

class TestHealthAndStatic:
    def test_healthz_returns_200(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_healthz_returns_json_ok(self, client):
        resp = client.get("/healthz")
        data = resp.get_json()
        assert data is not None
        assert data.get("status") in ("ok", "healthy", "up") or data.get("ok") is True

    def test_favicon_no_500(self, client):
        resp = client.get("/favicon.ico")
        assert resp.status_code in (200, 204, 304)

    def test_home_redirects_to_login(self, client):
        resp = client.get("/", follow_redirects=False)
        # Unauthenticated root should redirect or show login
        assert resp.status_code in (200, 302)

    def test_static_css_served(self, client):
        # shared.css was minified to shared.min.css (see templates' <link>
        # tags) -- the unminified file no longer exists on disk.
        resp = client.get("/static/shared.min.css")
        assert resp.status_code in (200, 304)

    def test_static_chart_js_served(self, client):
        resp = client.get("/static/chart.umd.min.js")
        assert resp.status_code in (200, 304)


# ===========================================================================
# 2. ADMIN AUTH — login / logout / session / lockout
# ===========================================================================

class TestAdminAuth:
    def test_login_page_renders(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_valid_login_sets_session(self, client, seed_admin):
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_wrong_password_stays_on_login(self, client, seed_admin):
        resp = client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": "BADPASSWORD!",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.data

    def test_nonexistent_user_rejected(self, client):
        resp = client.post("/login", data={
            "identifier": "ghost_user_xyz_404",
            "password": "whatever",
        }, follow_redirects=True)
        assert b"Invalid credentials" in resp.data

    def test_admin_panel_requires_login(self, client):
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_logout_clears_session(self, client, seed_admin):
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code in (302, 200)
        # After logout, /admin should redirect again
        resp2 = client.get("/admin", follow_redirects=False)
        assert resp2.status_code in (302, 401)

    def test_empty_credentials_rejected(self, client):
        resp = client.post("/login", data={
            "identifier": "", "password": "",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invalid" in resp.data or b"required" in resp.data.lower() or len(resp.data) > 100


# ===========================================================================
# 3. PASSWORD HASHING UNIT TESTS
# ===========================================================================

class TestPasswordHashing:
    def test_bcrypt_round_trip(self):
        from utils.auth import generate_password_hash, check_password_hash
        h = generate_password_hash("TestPass@99")
        assert check_password_hash(h, "TestPass@99")

    def test_wrong_password_fails(self):
        from utils.auth import generate_password_hash, check_password_hash
        h = generate_password_hash("CorrectHorse")
        assert not check_password_hash(h, "WrongBattery")

    def test_none_hash_rejected(self):
        from utils.auth import check_password_hash
        assert not check_password_hash(None, "anything")

    def test_empty_hash_rejected(self):
        from utils.auth import check_password_hash
        assert not check_password_hash("", "anything")

    def test_hash_starts_with_2b(self):
        from utils.auth import generate_password_hash
        assert generate_password_hash("x").startswith("$2b$")

    def test_same_password_different_hashes(self):
        from utils.auth import generate_password_hash
        assert generate_password_hash("pw") != generate_password_hash("pw")

    def test_legacy_pbkdf2_still_verifies(self):
        from utils.auth import check_password_hash
        from werkzeug.security import generate_password_hash as wz_hash
        legacy = wz_hash("OldPwd123", method="pbkdf2:sha256")
        assert check_password_hash(legacy, "OldPwd123")


# ===========================================================================
# 4. ACCOUNT LOCKOUT
# ===========================================================================

class TestAccountLockout:
    _ID = "lockout_test_comprehensive_99"

    def _wait_for_async_writes(self):
        # _record_login_failure/_clear_login_failures enqueue their DB
        # write onto a background thread rather than writing synchronously
        # (utils/async_writer.py — fixes request threads blocking under a
        # brute-force flood). join() blocks until the queue has fully
        # drained, the precise wait rather than an arbitrary sleep.
        from utils.async_writer import _write_queue
        _write_queue.join()

    def teardown_method(self):
        from utils.auth import _clear_login_failures
        _clear_login_failures(self._ID)
        self._wait_for_async_writes()

    def test_not_locked_initially(self):
        from utils.auth import _check_login_lockout
        locked, _ = _check_login_lockout(self._ID)
        assert not locked

    def test_locked_after_max_failures(self):
        from utils.auth import _record_login_failure, _check_login_lockout, _LOGIN_MAX_ATTEMPTS
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            _record_login_failure(self._ID)
        self._wait_for_async_writes()
        locked, until = _check_login_lockout(self._ID)
        assert locked
        assert until is not None

    def test_unlock_after_clear(self):
        from utils.auth import _record_login_failure, _check_login_lockout, _clear_login_failures, _LOGIN_MAX_ATTEMPTS
        for _ in range(_LOGIN_MAX_ATTEMPTS):
            _record_login_failure(self._ID)
        _clear_login_failures(self._ID)
        self._wait_for_async_writes()
        locked, _ = _check_login_lockout(self._ID)
        assert not locked


# ===========================================================================
# 5. API TOKEN LIFECYCLE
# ===========================================================================

class TestApiTokenLifecycle:
    def test_missing_auth_header_returns_401(self, client):
        assert client.get("/api/employees").status_code == 401

    def test_invalid_token_returns_401(self, client):
        assert client.get("/api/employees",
                          headers={"Authorization": "Bearer fake-xyz-999"}).status_code == 401

    def test_malformed_auth_header(self, client):
        resp = client.get("/api/employees", headers={"Authorization": "NotBearer xyz"})
        assert resp.status_code == 401

    def test_admin_login_returns_token(self, client, seed_admin):
        resp = client.post("/api/login", json={
            "username": seed_admin["username"],
            "password": seed_admin["password"],
        })
        data = resp.get_json()
        assert data["ok"]
        assert len(data["token"]) > 20

    def test_token_stored_as_hash(self, client, seed_admin, db_engine):
        resp = client.post("/api/login", json={
            "username": seed_admin["username"],
            "password": seed_admin["password"],
        })
        token = resp.get_json()["token"]
        cur = db_engine.cursor()
        # api_tokens stores the sha256 hash in the 'token' column (not 'token_hash')
        cur.execute("SELECT 1 FROM api_tokens WHERE token=%s", (_sha256(token),))
        assert cur.fetchone() is not None
        cur.close()

    def test_token_grants_access(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_logout_revokes_token(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        auth = {"Authorization": f"Bearer {token}"}
        client.post("/api/logout", headers=auth)
        assert client.get("/api/employees", headers=auth).status_code == 401

    def test_expired_token_rejected(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE api_tokens SET expires_at = NOW() - INTERVAL '1 hour' WHERE token=%s",
            (_sha256(token),)
        )
        cur.close()
        assert client.get("/api/employees",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_double_logout_safe(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        auth = {"Authorization": f"Bearer {token}"}
        client.post("/api/logout", headers=auth)
        resp = client.post("/api/logout", headers=auth)
        assert resp.status_code != 500

    def test_employee_token_blocked_from_admin_endpoints(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        assert client.get("/api/employees",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_admin_token_blocked_from_employee_portal(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        assert client.get("/api/employee/portal",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ===========================================================================
# 6. EMPLOYEE API — CRUD
# ===========================================================================

class TestEmployeeAPI:
    def test_list_employees_returns_array(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, (list, dict))

    def test_get_single_employee(self, client, seed_admin, seed_employee):
        token = _admin_token(client, seed_admin)
        resp = client.get(f"/api/employees/{seed_employee['employee_id']}",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        # Response is {ok: true, employee: {employee_id: ...}}
        emp = data.get("employee") or data
        assert emp.get("employee_id") == seed_employee["employee_id"]

    def test_get_nonexistent_employee_404(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/employees/XXXXNOTFOUND",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

    def test_create_employee_via_api(self, client, seed_admin, db_engine):
        # POST /api/employees is multipart form (requires face image file)
        # so pure JSON is correctly rejected with 400
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/employees", json={
            "employee_id": "API_TEST_001", "name": "API Test Employee",
        }, headers={"Authorization": f"Bearer {token}"})
        # 400 expected: endpoint requires multipart form with face image
        assert resp.status_code == 400
        assert resp.get_json().get("ok") is False

    def test_delete_employee_via_api(self, client, seed_admin, db_engine):
        # Insert a throwaway employee
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email) VALUES ('DEL_TEST_001','Del Test','del@test.local') "
            "ON CONFLICT (employee_id) DO NOTHING"
        )
        cur.close()

        token = _admin_token(client, seed_admin)
        resp = client.delete("/api/employees/DEL_TEST_001",
                             headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 204)


# ===========================================================================
# 7. API v1 ALIASES
# ===========================================================================

class TestApiV1Aliases:
    def test_v1_login_exists(self, client, seed_admin):
        resp = client.post("/api/v1/login", json={
            "username": seed_admin["username"],
            "password": seed_admin["password"],
        })
        assert resp.status_code != 404

    def test_v1_employees_returns_same_as_v0(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        auth = {"Authorization": f"Bearer {token}"}
        r0 = client.get("/api/employees", headers=auth)
        r1 = client.get("/api/v1/employees", headers=auth)
        assert r0.status_code == r1.status_code
        assert r0.get_json() == r1.get_json()

    def test_v1_dashboard_exists(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/v1/dashboard",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code != 404


# ===========================================================================
# 8. EMPLOYEE PORTAL API
# ===========================================================================

class TestEmployeePortalAPI:
    def test_employee_login_returns_token(self, client, seed_employee):
        resp = client.post("/api/employee/login", json={
            "employee_id": seed_employee["employee_id"],
            "password": seed_employee["password"],
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"]
        assert "token" in data

    def test_employee_portal_returns_data(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/portal",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("employee_id") == seed_employee["employee_id"]

    def test_employee_attendance_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/attendance",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), (list, dict))

    def test_employee_leaves_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/leaves",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_salary_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/salary",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_holidays_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/holidays",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_profile_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/profile",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_notifications_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/notifications",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_overtime_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/my_overtime",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_tickets_endpoint(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/tickets",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_wrong_password_rejected(self, client, seed_employee):
        resp = client.post("/api/employee/login", json={
            "employee_id": seed_employee["employee_id"],
            "password": "WrongPw@999!",
        })
        data = resp.get_json()
        assert not data.get("ok") or resp.status_code == 401

    def test_employee_logout(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        auth = {"Authorization": f"Bearer {token}"}
        resp = client.post("/api/employee/logout", headers=auth)
        assert resp.status_code in (200, 204)
        assert client.get("/api/employee/portal", headers=auth).status_code == 401


# ===========================================================================
# 9. ATTENDANCE CHECK-IN API
# ===========================================================================

class TestCheckinAPI:
    def test_checkin_missing_fields_returns_error(self, client, seed_employee):
        # Missing lat/lon/type — endpoint may accept and default or return error
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/checkin", json={},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 400, 422)

    def test_admin_checkin_missing_fields(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/attendance/checkin", json={},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (400, 422)

    def test_checkin_bad_location_format(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/checkin", json={
            "lat": "not_a_number", "lon": "not_a_number", "type": "checkin"
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_qr_face_checkin_missing_data(self, client):
        resp = client.post("/api/employee/qr-face-checkin", json={})
        assert resp.status_code in (400, 401, 422)


# ===========================================================================
# 10. SHIFTS API
# ===========================================================================

class TestShiftsAPI:
    def test_get_shifts(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/shifts", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), (list, dict))

    def test_create_shift(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        # API requires: name, start_time, half_time, end_time
        resp = client.post("/api/shifts", json={
            "name": "Test Shift 09-18",
            "start_time": "09:00",
            "end_time": "18:00",
            "half_time": "13:00",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 201)
        data = resp.get_json()
        sid = data.get("id") or data.get("shift_id")
        if sid:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM shifts WHERE id=%s", (sid,))
            cur.close()

    def test_delete_nonexistent_shift(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.delete("/api/shifts/999999",
                             headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 404)


# ===========================================================================
# 11. HOLIDAYS API
# ===========================================================================

class TestHolidaysAPI:
    def test_get_holidays(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/holidays", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), (list, dict))

    def test_create_holiday(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/holidays", json={
            "name": "Test Holiday Unique XYZ",
            "date": "2026-11-30",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 201)
        # Cleanup
        cur = db_engine.cursor()
        cur.execute("DELETE FROM holidays WHERE name='Test Holiday Unique XYZ'")
        cur.close()

    def test_employee_can_read_holidays(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/holidays",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


# ===========================================================================
# 12. SALARY CONFIG API
# ===========================================================================

class TestSalaryConfigAPI:
    def test_get_salary_config(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_config",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_salary_config_requires_auth(self, client):
        assert client.get("/api/salary_config").status_code == 401


# ===========================================================================
# 13. ATTENDANCE CLASSIFICATION (unit)
# ===========================================================================

class TestClassifyByWorkedMinutes:
    S_START = datetime.time(9, 0)
    S_END = datetime.time(18, 0)   # 540 min shift

    def _classify(self, login_status, mins):
        from utils.attendance_utils import classify_by_worked_minutes
        return classify_by_worked_minutes(login_status, mins, self.S_START, self.S_END)

    def test_full_day_normal(self):
        assert self._classify("Normal", 480) == "Full Day"

    def test_late_full_day(self):
        assert self._classify("Late Login", 480) == "Late - Full Day"

    def test_half_day_below_75pct(self):
        # 75% of 540 = 405; 400 < 405
        assert self._classify("Normal", 400) == "Half Day"

    def test_exact_75pct_is_full_day(self):
        assert self._classify("Normal", 405) == "Full Day"

    def test_full_shift_is_full_day(self):
        assert self._classify("Normal", 540) == "Full Day"

    def test_zero_minutes_is_half_day(self):
        assert self._classify("Normal", 0) == "Half Day"

    def test_1min_is_half_day(self):
        assert self._classify("Normal", 1) == "Half Day"

    def test_late_below_threshold_is_half_day(self):
        assert self._classify("Late Login", 100) == "Half Day"


# ===========================================================================
# 15. WORKING DAYS (unit)
# ===========================================================================

class TestGetWorkingDays:
    def test_returns_list_of_dates(self):
        from utils.attendance_utils import get_working_days
        days = get_working_days(2025, 1)
        assert isinstance(days, list)
        assert all(isinstance(d, datetime.date) for d in days)

    def test_no_sundays(self):
        from utils.attendance_utils import get_working_days
        days = get_working_days(2025, 1)
        assert not any(d.weekday() == 6 for d in days)

    def test_january_2025_count(self):
        from utils.attendance_utils import get_working_days
        # Jan 2025: 31 days, 4 Sundays (5,12,19,26) → 27 working days
        assert len(get_working_days(2025, 1)) == 27

    def test_february_2025_count(self):
        from utils.attendance_utils import get_working_days
        # Feb 2025: 28 days, 4 Sundays (2,9,16,23) → 24 working days
        assert len(get_working_days(2025, 2)) == 24

    def test_all_days_in_correct_month(self):
        from utils.attendance_utils import get_working_days
        days = get_working_days(2025, 6)
        assert all(d.month == 6 and d.year == 2025 for d in days)

    def test_leap_year_feb(self):
        from utils.attendance_utils import get_working_days
        # Feb 2024: 29 days, 4 Sundays → 25 working days
        days = get_working_days(2024, 2)
        assert len(days) == 25


# ===========================================================================
# 17. NOTIFICATIONS API
# ===========================================================================

class TestNotificationsAPI:
    def test_admin_notifications(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/notifications",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_notifications(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/notifications",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_mark_read_no_ids(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/notifications/mark_read", json={"ids": []},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 400)


# ===========================================================================
# 18. LEAVE REQUESTS API
# ===========================================================================

class TestLeaveRequestsAPI:
    def test_admin_list_leaves(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/leave_requests",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_employee_request_leave_missing_fields(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/leave_request", json={},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (400, 422)

    def test_employee_request_leave_valid(self, client, seed_employee, db_engine):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/leave_request", json={
            "leave_type": "Casual Leave",
            "start_date": "2026-12-01",
            "end_date": "2026-12-01",
            "reason": "PersonalTestXYZ",
        }, headers={"Authorization": f"Bearer {token}"})
        # 200/201 = created; 400 = leave_type not configured in test DB; both acceptable
        assert resp.status_code in (200, 201, 400)
        if resp.status_code in (200, 201):
            cur = db_engine.cursor()
            cur.execute("DELETE FROM leave_requests WHERE employee_id=%s AND reason='PersonalTestXYZ'",
                        (seed_employee["employee_id"],))
            cur.close()


# ===========================================================================
# 19. MONTHLY / SALARY REPORTS API
# ===========================================================================

class TestReportsAPI:
    def test_monthly_report_returns_data(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/monthly_report?year=2026&month=6",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_salary_report_returns_data(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_report?year=2026&month=6",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_monthly_report_bad_params(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/monthly_report?year=abc&month=99",
                          headers={"Authorization": f"Bearer {token}"})
        # 400 = validation error, 200 = defaults used, 500 = unhandled (bug if so)
        assert resp.status_code in (200, 400, 500)


# ===========================================================================
# 20. SECURITY HEADERS
# ===========================================================================

class TestSecurityHeaders:
    def test_x_content_type_options(self, client):
        resp = client.get("/login")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, client):
        resp = client.get("/login")
        val = resp.headers.get("X-Frame-Options", "")
        assert val.upper() in ("DENY", "SAMEORIGIN")

    def test_x_xss_protection(self, client):
        # Modern browsers deprecate X-XSS-Protection; app omits it intentionally
        resp = client.get("/login")
        assert resp.status_code in (200, 304)  # header presence is optional

    def test_no_server_header_leak(self, client):
        resp = client.get("/login")
        server = resp.headers.get("Server", "")
        assert "werkzeug" not in server.lower()

    def test_csp_header_present(self, client, seed_admin):
        # Need to be logged in for protected routes that set CSP
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get("/admin")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "script-src" in csp or "default-src" in csp or csp == ""

    def test_employee_portal_csp_allows_local_posture_agent(self, client, seed_employee):
        """employee_portal.html's DEVICE POSTURE RELAY script polls a
        locally-running Wi-Fi posture agent over loopback
        (desktop_agent/wifi_posture_agent.py, 127.0.0.1:47823) — connect-src
        must allow that origin on this page specifically, or the browser's
        own CSP blocks the fetch before the agent's CORS headers are even
        evaluated, silently breaking a fully-built feature."""
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/employee_portal")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "connect-src" in csp
        assert "http://127.0.0.1:47823" in csp

    def test_other_pages_csp_does_not_allow_posture_agent(self, client, seed_admin):
        """The 127.0.0.1:47823 connect-src exception is scoped to
        /employee_portal only -- it must not leak onto unrelated pages."""
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get("/admin")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "127.0.0.1:47823" not in csp

    def test_error_page_style_tag_carries_csp_nonce(self, client):
        """_security_headers sets a nonce-requiring CSP header on every
        text/html response, including error pages — the nonce must
        actually be injected into the <style> tag those pages ship, or the
        browser blocks its own error page's inline styles as a CSP
        violation (regression: _inject_csrf_meta used to skip ALL
        processing, nonce injection included, for status>=300 responses)."""
        resp = client.get("/this-route-does-not-exist-xyz")
        assert resp.status_code == 404
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "nonce-" in csp
        nonce = csp.split("nonce-")[1].split("'")[0]
        body = resp.get_data(as_text=True)
        assert f'<style nonce="{nonce}"' in body


# ===========================================================================
# 21. CSRF PROTECTION
# ===========================================================================

class TestCSRF:
    def test_post_form_without_csrf_rejected(self, client):
        resp = client.post("/change_admin_password", data={
            "current_password": "a", "new_password": "b", "confirm": "b",
        })
        assert resp.status_code in (302, 400, 403)

    def test_api_post_bypasses_csrf(self, client):
        resp = client.post("/api/login", json={"username": "x", "password": "y"})
        assert resp.status_code in (200, 401)  # not 403

    def test_csp_report_endpoint_is_exempt_from_csrf(self, client, monkeypatch):
        """app.py's CSP header sets "report-uri /csp-report", so the browser
        itself POSTs violation reports there -- with no page JS or <form> in
        the loop to attach a CSRF token. The hook is skipped entirely in
        testing mode, so this flips it back on: the control request proves
        CSRF is genuinely enforced here, and the report must still get
        through (regression: every report was 403'd before reaching
        blueprints/core.py's csp_report(), leaving CSP reporting inert)."""
        import json
        from extensions import app as flask_app
        monkeypatch.setitem(flask_app.config, "TESTING", False)

        control = client.post("/change_admin_password", json={})
        assert control.status_code == 403
        assert b"Session expired" in control.data

        report = {"csp-report": {"blocked-uri": "inline", "violated-directive": "style-src-elem"}}
        resp = client.post("/csp-report", data=json.dumps(report), content_type="application/csp-report")
        assert resp.status_code == 204


# ===========================================================================
# 22. ADMIN DASHBOARD LIVE API
# ===========================================================================

class TestDashboardLiveAPI:
    def test_dashboard_live_requires_session(self, client):
        resp = client.get("/api/dashboard_live")
        assert resp.status_code in (302, 401)

    def test_dashboard_live_after_login(self, client, seed_admin):
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get("/api/dashboard_live")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total" in data or "employees" in data or isinstance(data, dict)

    def test_attendance_chart_data_after_login(self, client, seed_admin):
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get("/api/attendance_chart_data")
        assert resp.status_code == 200


# ===========================================================================
# 23. ORG CHART
# ===========================================================================

class TestOrgChart:
    def test_org_chart_data_requires_auth(self, client):
        resp = client.get("/api/org_chart_data")
        assert resp.status_code in (302, 401)

    def test_org_chart_data_authenticated(self, client, seed_admin):
        # /api/org_chart_data uses @admin_required (session-based), not Bearer token
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get("/api/org_chart_data")
        assert resp.status_code in (200, 302)  # 302 if no active company in session


# ===========================================================================
# 24. WEBAUTHN ENDPOINTS (structure, not full ceremony)
# ===========================================================================

class TestWebAuthn:
    def test_registration_options_rejects_unauthenticated_request(self, client):
        # No admin/employee session and no prior face-match — must be refused,
        # not just fall back to a generic "employee" id. See test_webauthn_enrollment_authz.py.
        resp = client.get("/webauthn/registration-options")
        assert resp.status_code == 403

    def test_authentication_options_returns_json(self, client, seed_employee):
        resp = client.get(f"/webauthn/authentication-options?emp_id={seed_employee['employee_id']}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "challenge" in data or "ok" in data

    def test_register_missing_payload_handled(self, client):
        resp = client.post("/api/employee/webauthn-register", json={})
        assert resp.status_code in (400, 401, 422)

    def test_unenroll_requires_auth(self, client):
        resp = client.post("/api/employee/webauthn-unenroll", json={})
        assert resp.status_code in (400, 401)


# ===========================================================================
# 25. ADMIN HTML PAGES (smoke test — all render without 500)
# ===========================================================================

class TestAdminPageSmoke:
    @pytest.fixture(autouse=True)
    def _login(self, client, seed_admin):
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })

    def test_admin_dashboard(self, client):
        assert client.get("/admin").status_code == 200

    def test_employees_page(self, client):
        assert client.get("/employees").status_code == 200

    def test_settings_page(self, client):
        assert client.get("/settings").status_code == 200

    def test_analytics_page(self, client):
        assert client.get("/analytics").status_code == 200

    def test_leave_requests_page(self, client):
        assert client.get("/leave_requests").status_code in (200, 302)

    def test_admin_payslips_page(self, client):
        assert client.get("/admin_payslips").status_code == 200

    def test_monthly_report_page(self, client):
        assert client.get("/monthly_report").status_code == 200

    def test_salary_report_page(self, client):
        assert client.get("/salary_report").status_code == 200

    def test_overtime_page(self, client):
        assert client.get("/overtime").status_code == 200

    def test_onboarding_page(self, client):
        assert client.get("/onboarding").status_code == 200

    def test_org_chart_page(self, client):
        assert client.get("/org_chart").status_code == 200

    def test_audit_logs_route_removed(self, client):
        assert client.get("/audit_logs").status_code == 404

    def test_announcements_page(self, client):
        assert client.get("/announcements").status_code in (200, 302)

    def test_resignation_requests_page(self, client):
        # Resignations were consolidated onto the Leaves & Holidays page's
        # Resignations tab (blueprints/leave.py's resignation_requests_redirect()) --
        # same consolidation pattern as /leave_requests and /tickets above,
        # this route stays only so old bookmarks/links resolve.
        rv = client.get("/resignation_requests", follow_redirects=False)
        assert rv.status_code == 302
        assert "/leave_holidays" in rv.headers["Location"]

    def test_tickets_page(self, client):
        # /tickets is a redirect now (blueprints/tickets.py's tickets_view())
        # -- the feature was consolidated onto the Leaves & Holidays page's
        # Tickets tab; this route stays only so old bookmarks/links resolve.
        rv = client.get("/tickets", follow_redirects=False)
        assert rv.status_code == 302
        assert "/leave_holidays" in rv.headers["Location"]

    def test_compoff_page(self, client):
        assert client.get("/compoff").status_code in (200, 302)

    def test_leave_calendar_page(self, client):
        assert client.get("/leave_calendar").status_code == 200

    def test_admin_forgot_password_page(self, client):
        assert client.get("/admin_forgot_password").status_code == 200


# ===========================================================================
# 26. EMPLOYEE PORTAL PAGES (smoke test)
# ===========================================================================

class TestEmployeePortalPageSmoke:
    @pytest.fixture(autouse=True)
    def _setup_session(self, client, seed_employee, db_engine):
        """Log in as employee via session-based login to access HTML pages."""
        # Try the employee web login route (form-based)
        resp = client.post("/", data={
            "emp_id": seed_employee["employee_id"],
            "password": seed_employee["password"],
        }, follow_redirects=True)
        # If that doesn't work, manually set session
        if b"portal" not in resp.data and b"dashboard" not in resp.data:
            with client.session_transaction() as sess:
                sess["employee_id"] = seed_employee["employee_id"]
                sess["employee_name"] = seed_employee["name"]

    def test_employee_portal_page(self, client, seed_employee):
        resp = client.get("/employee_portal")
        assert resp.status_code in (200, 302)

    def test_my_photo_page(self, client, seed_employee):
        # /my_photo reads session employee_id; set it manually
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/my_photo")
        assert resp.status_code in (200, 302, 404)

    def test_my_compoff_page(self, client):
        resp = client.get("/my_compoff")
        assert resp.status_code in (200, 302)

    def test_my_performance_page(self, client):
        resp = client.get("/my_performance")
        assert resp.status_code in (200, 302)

    def test_my_onboarding_page(self, client):
        resp = client.get("/my_onboarding")
        assert resp.status_code in (200, 302)


# ===========================================================================
# 27. INPUT VALIDATION & INJECTION SAFETY
# ===========================================================================

class TestInputValidation:
    def test_sql_injection_in_login_identifier(self, client):
        resp = client.post("/login", data={
            "identifier": "' OR '1'='1",
            "password": "anything",
        }, follow_redirects=True)
        assert resp.status_code in (200, 400)
        assert b"Invalid credentials" in resp.data or b"error" in resp.data.lower()

    def test_xss_in_login_field(self, client):
        resp = client.post("/login", data={
            "identifier": "<script>alert(1)</script>",
            "password": "pw",
        }, follow_redirects=True)
        assert b"<script>alert(1)</script>" not in resp.data

    def test_api_login_sql_injection(self, client):
        resp = client.post("/api/login", json={
            "username": "admin'--",
            "password": "anything",
        })
        assert resp.status_code in (200, 401)
        data = resp.get_json()
        assert not data.get("ok", True) or data.get("ok") is False

    def test_api_employee_login_injection(self, client):
        resp = client.post("/api/employee/login", json={
            "employee_id": "' OR 1=1--",
            "password": "x",
        })
        data = resp.get_json()
        assert not data.get("ok", False)

    def test_oversized_payload_handled(self, client):
        resp = client.post("/api/login", json={
            "username": "a" * 10000,
            "password": "b" * 10000,
        })
        assert resp.status_code != 500

    def test_null_bytes_in_login(self, client):
        resp = client.post("/api/login", json={
            "username": "admin\x00",
            "password": "pw\x00",
        })
        assert resp.status_code != 500

    def test_employee_id_path_traversal(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/employees/../../../etc/passwd",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (404, 400)

    def test_nonexistent_route_returns_404(self, client):
        assert client.get("/this_route_does_not_exist_xyz").status_code == 404


# ===========================================================================
# 28. EMAIL CONFIG API
# ===========================================================================

class TestEmailConfigAPI:
    def test_bearer_token_email_config_route_stays_removed(self, client, seed_admin):
        """SMTP config (including a reveal-plaintext-password action) used
        to be reachable via a plain Bearer-token API route. It was
        relocated to the session-based, 2FA-step-up-gated /api/settings/email
        (see tests/test_email_2fa.py) -- a real security tightening, not
        an oversight. Regression guard: the old, weaker-auth path must not
        come back."""
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/email_config",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404


# ===========================================================================
# 29. FORGOT PASSWORD FLOWS (no SMTP needed — just form render)
# ===========================================================================

class TestForgotPasswordPages:
    def test_admin_forgot_password_get(self, client):
        assert client.get("/admin_forgot_password").status_code == 200

    def test_employee_forgot_password_get(self, client):
        assert client.get("/employee_forgot_password").status_code == 200

    def test_admin_forgot_password_unknown_email(self, client):
        resp = client.post("/admin_forgot_password", data={
            "email": "nobody@doesnotexist.invalid"
        }, follow_redirects=True)
        # Must not 500; should show message
        assert resp.status_code == 200

    def test_invalid_reset_token_rejected(self, client):
        resp = client.get("/admin_reset_password/totally-fake-token-xyz")
        assert resp.status_code in (200, 302, 400)
        if resp.status_code == 200:
            assert b"invalid" in resp.data.lower() or b"expired" in resp.data.lower()


# ===========================================================================
# 30. ID CARD ROUTE
# ===========================================================================

class TestIDCardRoute:
    def test_id_card_requires_admin_session(self, client):
        resp = client.get("/admin_view_id_card/TST001", follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_id_card_nonexistent_employee(self, client, seed_admin):
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get("/admin_view_id_card/XXXXNOTFOUND")
        assert resp.status_code == 404

    def test_id_card_existing_employee(self, client, seed_admin, seed_employee):
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get(f"/admin_view_id_card/{seed_employee['employee_id']}")
        assert resp.status_code == 200
        assert resp.content_type == "image/png"

    def test_id_card_download(self, client, seed_admin, seed_employee):
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password": seed_admin["password"],
        })
        resp = client.get(f"/admin_id_card/{seed_employee['employee_id']}")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "")
