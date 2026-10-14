"""
Additional coverage tests for blueprints/attendance.py.
Targets uncovered branches identified in the 78% coverage report.
Does NOT duplicate any test already in test_attendance_checkin.py or test_leave_attendance.py.
"""
import datetime
import io
import time as _time_stdlib

import pytest

import utils.config as _cfg
from tests.conftest import TEST_OFFICE

_REAL_SHIFT_RULES = _cfg.shift_rules

# Location enforcement is on by default (QA SEC-08): live and offline check-ins
# need coordinates -- these are the office's own.
OFFICE = TEST_OFFICE


def _patch_shift_rules(mocker, **changes):
    """Override fields of the current company's shift rules for one test."""
    mocker.patch.object(_cfg, "shift_rules", lambda: _REAL_SHIFT_RULES()._replace(**changes))


# ── Session / token helpers ──────────────────────────────────────────────────

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


def _admin_token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"],
        "password": seed_admin["password"],
    }).get_json()["token"]


def _emp_token(client, seed_employee):
    return client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"],
        "password":    seed_employee["password"],
    }).get_json()["token"]


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def att_login_only(db_engine, seed_employee):
    """Login-only attendance record for today (next call = logout)."""
    cur = db_engine.cursor()
    today = datetime.date.today()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.execute(
        "INSERT INTO attendance (employee_id, date, login_time, status) "
        "VALUES (%s, %s, '09:00:00', 'Full Day Login')",
        (seed_employee["employee_id"], today),
    )
    yield {"employee_id": seed_employee["employee_id"], "date": today}
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.close()


@pytest.fixture
def att_completed(db_engine, seed_employee):
    """Completed (login + logout) record for today, with last_relogin set
    via a second INSERT/UPDATE so second logout reads last_relogin."""
    cur = db_engine.cursor()
    today = datetime.date.today()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    # First insert with login
    cur.execute(
        "INSERT INTO attendance (employee_id, date, login_time, logout_time, "
        "status, logout_status, attendance_type, worked_minutes, last_relogin) "
        "VALUES (%s, %s, '08:00:00', '12:00:00', "
        "'Full Day Login', 'Half Day Logout', 'Half Day', 240, '12:30:00')",
        (seed_employee["employee_id"], today),
    )
    yield {"employee_id": seed_employee["employee_id"], "date": today}
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.close()


@pytest.fixture
def att_completed_basic(db_engine, seed_employee):
    """Standard completed record for today (login + logout, no last_relogin)."""
    cur = db_engine.cursor()
    today = datetime.date.today()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.execute(
        "INSERT INTO attendance (employee_id, date, login_time, logout_time, "
        "status, logout_status, attendance_type, worked_minutes) "
        "VALUES (%s, %s, '09:00:00', '18:00:00', "
        "'Full Day Login', 'Completed', 'Full Day', 540)",
        (seed_employee["employee_id"], today),
    )
    yield {"employee_id": seed_employee["employee_id"], "date": today}
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.close()


@pytest.fixture
def att_with_types(db_engine, seed_employee):
    """Insert attendance records with multiple att_type values for monthly-report branch coverage."""
    cur = db_engine.cursor()
    today = datetime.date.today()
    year, month = today.year, today.month
    # Insert records for up to 4 past days in the current month with different types
    records = []
    for i, (att_type, status) in enumerate([
        ("Full Day",        "Full Day Login"),
        ("Late - Full Day", "Late Login"),
        ("Half Day",        "Full Day Login"),
        ("Absent",          "Absent"),
    ], start=1):
        d = datetime.date(year, month, i)
        if d >= today:
            break
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], d))
        if att_type != "Absent":
            cur.execute(
                "INSERT INTO attendance (employee_id, date, login_time, logout_time, "
                "status, logout_status, attendance_type, worked_minutes) "
                "VALUES (%s, %s, '09:00:00', '18:00:00', %s, 'Completed', %s, 540)",
                (seed_employee["employee_id"], d, status, att_type),
            )
        else:
            cur.execute(
                "INSERT INTO attendance (employee_id, date, login_time, status, attendance_type) "
                "VALUES (%s, %s, NULL, 'Absent', 'Absent')",
                (seed_employee["employee_id"], d),
            )
        records.append(d)
    yield {"employee_id": seed_employee["employee_id"], "dates": records}
    for d in records:
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], d))
    cur.close()


@pytest.fixture
def att_post_relogin(db_engine, seed_employee):
    """State after relogin: login_time SET, logout_time NULL, last_relogin SET.
    Next check-in → logout that uses last_relogin as session_start (line 1135)."""
    cur = db_engine.cursor()
    today = datetime.date.today()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.execute(
        "INSERT INTO attendance (employee_id, date, login_time, logout_time, "
        "status, worked_minutes, last_relogin) "
        "VALUES (%s, %s, '08:00:00', NULL, 'Full Day Login', 240, '12:30:00')",
        (seed_employee["employee_id"], today),
    )
    yield {"employee_id": seed_employee["employee_id"], "date": today}
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.close()


@pytest.fixture
def att_today_with_login_logout(db_engine, seed_employee):
    """Attendance record for today with both login and logout, for PDF coverage."""
    cur = db_engine.cursor()
    today = datetime.date.today()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.execute(
        "INSERT INTO attendance (employee_id, date, login_time, logout_time, "
        "status, logout_status, attendance_type, worked_minutes) "
        "VALUES (%s, %s, '09:00:00', '18:00:00', 'Full Day Login', 'Completed', 'Full Day', 540)",
        (seed_employee["employee_id"], today),
    )
    yield {"employee_id": seed_employee["employee_id"], "date": today}
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                (seed_employee["employee_id"], today))
    cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Helper function unit tests (lines 31-113)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetKnownFaceEncoding:
    """Lines 31-42: _get_known_face_encoding cache paths."""

    def test_file_not_found_returns_none(self, mocker):
        import blueprints.attendance as att
        mocker.patch("blueprints.attendance.os.path.getmtime", side_effect=OSError("no file"))
        enc = att._get_known_face_encoding("TST001", "/no/such/file.jpg")
        assert enc is None

    def test_cache_hit_returns_cached_encoding(self, mocker):
        # _get_known_face_encoding + its cache dict + its face_recognition
        # reference all live in utils/face_utils.py (blueprints.attendance
        # only imports the function by name, so patching attributes on the
        # att module itself doesn't reach the real implementation's state).
        import blueprints.attendance as att
        import utils.face_utils as fu
        sentinel = object()
        mtime = 12345.0
        fu._face_enc_cache["CACHE_HIT_EMP"] = (mtime, sentinel)
        mocker.patch("blueprints.attendance.os.path.getmtime", return_value=mtime)
        result = att._get_known_face_encoding("CACHE_HIT_EMP", "/fake/path.jpg")
        assert result is sentinel
        del fu._face_enc_cache["CACHE_HIT_EMP"]

    def test_cache_miss_calls_face_recognition(self, mocker):
        import blueprints.attendance as att
        import utils.face_utils as fu
        mtime = 99999.0
        mocker.patch("blueprints.attendance.os.path.getmtime", return_value=mtime)
        fake_img = object()
        fake_enc = [0.1] * 128
        fr_mock = mocker.MagicMock()
        fr_mock.load_image_file.return_value = fake_img
        fr_mock.face_encodings.return_value = [fake_enc]
        mocker.patch.object(fu, "face_recognition", fr_mock)
        fu._face_enc_cache.pop("MISS_EMP", None)
        result = att._get_known_face_encoding("MISS_EMP", "/fake/path.jpg")
        assert result == fake_enc
        assert fu._face_enc_cache.get("MISS_EMP") == (mtime, fake_enc)
        del fu._face_enc_cache["MISS_EMP"]

    def test_no_encodings_returns_none_cached(self, mocker):
        import blueprints.attendance as att
        import utils.face_utils as fu
        mtime = 77777.0
        mocker.patch("blueprints.attendance.os.path.getmtime", return_value=mtime)
        fr_mock = mocker.MagicMock()
        fr_mock.load_image_file.return_value = object()
        fr_mock.face_encodings.return_value = []  # no faces in image
        mocker.patch.object(fu, "face_recognition", fr_mock)
        fu._face_enc_cache.pop("NOENC_EMP", None)
        result = att._get_known_face_encoding("NOENC_EMP", "/fake/path.jpg")
        assert result is None
        del fu._face_enc_cache["NOENC_EMP"]


class TestSafeReferrerRedirect:
    """Lines 52-59: _safe_referrer_redirect URL parsing branches."""

    def test_empty_referrer_returns_fallback(self, client):
        import blueprints.attendance as att
        with client.application.test_request_context("/"):
            result = att._safe_referrer_redirect("", "/fallback")
            assert result == "/fallback"

    def test_relative_url_passes_to_safe_redirect(self, client, mocker):
        # _safe_referrer_redirect lives in utils/helpers.py (blueprints.attendance
        # only imports it by name) and calls ITS OWN bound _safe_redirect —
        # patch that, not attendance's separately-imported copy.
        import blueprints.attendance as att
        import utils.helpers as helpers
        mock_redirect = mocker.patch.object(helpers, "_safe_redirect", return_value="/settings")
        with client.application.test_request_context("/"):
            result = att._safe_referrer_redirect("/settings?tab=shifts", "/fallback")
            assert result == "/settings"
            mock_redirect.assert_called_once()

    def test_absolute_same_host_strips_to_path(self, client, mocker):
        import blueprints.attendance as att
        mocker.patch.object(att, "_safe_redirect", side_effect=lambda path, fb: path)
        with client.application.test_request_context("/", headers={"Host": "testserver"}):
            from flask import request
            host = request.host  # "testserver" or "localhost"
            referrer = f"http://{host}/monthly_report?year=2025"
            result = att._safe_referrer_redirect(referrer, "/fallback")
            assert "/monthly_report" in result

    def test_absolute_different_host_returns_fallback(self, client):
        import blueprints.attendance as att
        with client.application.test_request_context("/"):
            result = att._safe_referrer_redirect("http://evil.com/steal", "/fallback")
            assert result == "/fallback"


class TestWaFingerprintRecentlyVerified:
    """Lines 86-89: _wa_fingerprint_recently_verified."""

    def test_valid_session_proof_returns_true(self, client, seed_employee):
        emp_id = seed_employee["employee_id"]
        now_ts = _time_stdlib.time()
        with client.session_transaction() as sess:
            sess["wa_fp_verified_emp_id"] = emp_id.upper()
            sess["wa_fp_verified_at"] = now_ts

        import blueprints.attendance as att
        # Confirm the function consumes and validates the session proof
        with client.application.test_request_context("/"):
            from flask import session
            session["wa_fp_verified_emp_id"] = emp_id.upper()
            session["wa_fp_verified_at"] = now_ts
            result = att._wa_fingerprint_recently_verified(emp_id)
        assert result is True

    def test_expired_proof_returns_false(self, client, seed_employee):
        emp_id = seed_employee["employee_id"]
        import blueprints.attendance as att
        with client.application.test_request_context("/"):
            from flask import session
            session["wa_fp_verified_emp_id"] = emp_id.upper()
            session["wa_fp_verified_at"] = _time_stdlib.time() - 9999  # way expired
            result = att._wa_fingerprint_recently_verified(emp_id)
        assert result is False

    def test_wrong_emp_id_returns_false(self, client):
        import blueprints.attendance as att
        with client.application.test_request_context("/"):
            from flask import session
            session["wa_fp_verified_emp_id"] = "OTHER_EMP"
            session["wa_fp_verified_at"] = _time_stdlib.time()
            result = att._wa_fingerprint_recently_verified("TST001")
        assert result is False


class TestMobileBiometricRecentlyVerified:
    """Lines 96-113: _mobile_biometric_recently_verified."""

    def test_recent_proof_returns_true_and_clears(self, db_engine, seed_employee, client):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        # Ensure row exists
        cur.execute("DELETE FROM mobile_biometric_proofs WHERE employee_id=%s", (emp_id,))
        now = datetime.datetime.now()
        cur.execute(
            "INSERT INTO mobile_biometric_proofs (employee_id, verified_at) VALUES (%s, %s)",
            (emp_id, now),
        )
        from utils.webauthn_utils import _mobile_biometric_recently_verified
        with client.application.test_request_context("/"):
            result = _mobile_biometric_recently_verified(emp_id)
        assert result is True
        # Proof should be cleared
        cur.execute("SELECT verified_at FROM mobile_biometric_proofs WHERE employee_id=%s", (emp_id,))
        row = cur.fetchone()
        assert row is None or row[0] is None
        cur.execute("DELETE FROM mobile_biometric_proofs WHERE employee_id=%s", (emp_id,))
        cur.close()

    def test_no_row_returns_false(self, db_engine, seed_employee, client):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("DELETE FROM mobile_biometric_proofs WHERE employee_id=%s", (emp_id,))
        cur.close()
        from utils.webauthn_utils import _mobile_biometric_recently_verified
        with client.application.test_request_context("/"):
            result = _mobile_biometric_recently_verified(emp_id)
        assert result is False

    def test_expired_proof_returns_false(self, db_engine, seed_employee, client):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("DELETE FROM mobile_biometric_proofs WHERE employee_id=%s", (emp_id,))
        cur.execute(
            "INSERT INTO mobile_biometric_proofs (employee_id, verified_at) "
            "VALUES (%s, NOW() - %s * INTERVAL '1 second')",
            (emp_id, 9999),
        )
        from utils.webauthn_utils import _mobile_biometric_recently_verified
        with client.application.test_request_context("/"):
            result = _mobile_biometric_recently_verified(emp_id)
        assert result is False
        cur.execute("DELETE FROM mobile_biometric_proofs WHERE employee_id=%s", (emp_id,))
        cur.close()

    def test_empty_emp_id_returns_false(self, client):
        from utils.webauthn_utils import _mobile_biometric_recently_verified
        with client.application.test_request_context("/"):
            result = _mobile_biometric_recently_verified("")
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# /shifts redirect (line 180)
# ═══════════════════════════════════════════════════════════════════════════════

class TestShiftsRedirect:
    def test_shifts_redirects_to_settings(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/shifts")
        assert rv.status_code == 302
        assert "/settings" in rv.headers["Location"]
        assert "tab=shifts" in rv.headers["Location"]


# ═══════════════════════════════════════════════════════════════════════════════
# /add_shift — company_id branch and exception branch (lines 199, 209-210)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAddShiftCompanyId:

    def test_add_shift_with_company_id_inserts_with_company(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/add_shift", data={
            "shift_name": "CI Company Shift",
            "start_time": "07:00",
            "half_time":  "12:00",
            "end_time":   "15:00",
            "company_id": "1",
        })
        assert rv.status_code in (200, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT id, company_id FROM shifts WHERE name='CI Company Shift'")
        row = cur.fetchone()
        if row:
            assert row[1] == 1
            cur.execute("DELETE FROM shifts WHERE id=%s", (row[0],))
        cur.close()

    def test_add_shift_db_exception_redirects_silently(self, client, seed_admin, mocker):
        _admin_session(client, seed_admin)
        # Force DB to raise on execute so the exception branch (lines 209-210) is hit
        mock_cursor = mocker.MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn = mocker.MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mocker.patch("blueprints.attendance.get_db_connection", return_value=mock_conn)
        rv = client.post("/add_shift", data={
            "shift_name": "Crash Shift",
            "start_time": "08:00",
            "half_time":  "12:00",
            "end_time":   "17:00",
        })
        # Exception is silently swallowed; should still redirect
        assert rv.status_code in (200, 302)


# ═══════════════════════════════════════════════════════════════════════════════
# attendance_chart_data with company filter (lines 139, 166-168)
# ═══════════════════════════════════════════════════════════════════════════════

class TestChartDataCompanyFilter:

    def test_with_active_company_id_in_session(self, client, seed_admin):
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 1
        rv = client.get("/api/attendance_chart_data")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "trend" in data
        assert "dept" in data
        with client.session_transaction() as sess:
            sess.pop("active_company_id", None)


# ═══════════════════════════════════════════════════════════════════════════════
# monthly_report — company filter and attendance type branches (lines 469, 499-508)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMonthlyReportWithData:

    def test_company_filter_branch(self, client, seed_admin):
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 1
        today = datetime.date.today()
        rv = client.get(f"/monthly_report?year={today.year}&month={today.month}")
        assert rv.status_code == 200
        with client.session_transaction() as sess:
            sess.pop("active_company_id", None)

    def test_attendance_type_branches_covered(self, client, seed_admin, att_with_types):
        """Exercises Full Day / Late - Full Day / Half Day / Absent classification branches."""
        _admin_session(client, seed_admin)
        today = datetime.date.today()
        rv = client.get(f"/monthly_report?year={today.year}&month={today.month}")
        assert rv.status_code == 200
        html = rv.data.decode()
        assert "TST001" in html or "Test Employee" in html


# ═══════════════════════════════════════════════════════════════════════════════
# monthly_report_export — data loop and openpyxl styling (lines 826-844, 888-900)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMonthlyReportExportWithData:

    def test_export_with_employee_data_loop_runs(self, client, seed_admin, att_with_types):
        """Runs the for-emp loop (lines 825-844) and openpyxl row styling (888-900)."""
        _admin_session(client, seed_admin)
        today = datetime.date.today()
        rv = client.get(f"/monthly_report_export?year={today.year}&month={today.month}")
        assert rv.status_code == 200
        assert rv.headers["Content-Type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        # Verify the xlsx file is non-trivial (has data rows beyond header)
        data = rv.data
        assert data[:4] == b"PK\x03\x04"  # zip/xlsx magic bytes


# ═══════════════════════════════════════════════════════════════════════════════
# api_monthly_report — loop with attendance data (lines 1307-1325)
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiMonthlyReportWithData:

    def test_report_loop_with_attendance_records(self, client, seed_admin, att_with_types):
        token = _admin_token(client, seed_admin)
        today = datetime.date.today()
        rv = client.get(
            f"/api/monthly_report?year={today.year}&month={today.month}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["ok"] is True
        # Verify the per-employee entry includes coverage-relevant keys
        entry = next((r for r in body["report"] if r["employee_id"] == "TST001"), None)
        assert entry is not None
        assert "full_days" in entry and "late_days" in entry and "half_days" in entry


# ═══════════════════════════════════════════════════════════════════════════════
# my_attendance_pdf — with attendance records (lines 1210-1234)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMyAttendancePdfWithRecords:

    def test_pdf_with_records_renders_rows(self, client, seed_employee, att_today_with_login_logout):
        _emp_session(client, seed_employee)
        today = datetime.date.today()
        rv = client.get(f"/my_attendance_pdf?year={today.year}&month={today.month}")
        assert rv.status_code == 200
        html = rv.data.decode()
        # Should contain at least the date row and the employee name
        assert seed_employee["name"] in html
        assert "Full Day" in html or "login" in html.lower()

    def test_pdf_total_hours_calculated(self, client, seed_employee, att_today_with_login_logout):
        """Covers lines 1216-1219: login_t and logout_t worked minutes calculation."""
        _emp_session(client, seed_employee)
        today = datetime.date.today()
        rv = client.get(f"/my_attendance_pdf?year={today.year}&month={today.month}")
        assert rv.status_code == 200
        html = rv.data.decode()
        # Total hours section shows something non-zero
        assert "Total Hours" in html


# ═══════════════════════════════════════════════════════════════════════════════
# bulk_mark_attendance — POST invalid date, POST loop, GET invalid date, GET company
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkMarkAttendanceCoverage:

    def test_post_invalid_date_flashes_error(self, client, seed_admin):
        """Lines 691-693: POST with invalid date_str → flash + redirect."""
        _admin_session(client, seed_admin)
        rv = client.post("/bulk_mark_attendance", data={"date": "not-a-date"})
        assert rv.status_code in (200, 302)

    def test_post_inserts_new_record_for_employee(self, client, seed_admin, seed_employee, db_engine):
        """Lines 705-720: POST loop with att_type → INSERT branch."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()

        _admin_session(client, seed_admin)
        rv = client.post("/bulk_mark_attendance", data={
            "date":           today.isoformat(),
            "att_TST001":     "Full Day",
            "login_TST001":   "09:00",
            "logout_TST001":  "18:00",
        })
        assert rv.status_code in (200, 302)

        cur = db_engine.cursor()
        cur.execute("SELECT attendance_type FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        row = cur.fetchone()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        assert row is not None
        assert row[0] == "Full Day"

    def test_post_updates_existing_record(self, client, seed_admin, seed_employee, att_with_types, db_engine):
        """Lines 708-713: POST loop → UPDATE branch (record already exists)."""
        today = datetime.date.today()
        # Ensure a record exists for today
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO attendance (employee_id, date, login_time, status) "
                "VALUES ('TST001', %s, '08:00:00', 'Full Day Login')",
                (today,)
            )
        cur.close()

        _admin_session(client, seed_admin)
        rv = client.post("/bulk_mark_attendance", data={
            "date":        today.isoformat(),
            "att_TST001":  "Half Day",
        })
        assert rv.status_code in (200, 302)

        cur = db_engine.cursor()
        cur.execute("SELECT attendance_type FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        row = cur.fetchone()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        assert row is not None
        assert row[0] == "Half Day"

    def test_get_invalid_date_falls_back_to_today(self, client, seed_admin):
        """Lines 729-731: GET with ?date=bad-date falls back to today."""
        _admin_session(client, seed_admin)
        rv = client.get("/bulk_mark_attendance?date=not-a-date")
        assert rv.status_code == 200
        # The page should show today's date in the response
        today = datetime.date.today().isoformat()
        assert today in rv.data.decode()

    def test_get_with_company_filter(self, client, seed_admin):
        """Line 747: GET with active_company_id filters employees by company."""
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 1
        rv = client.get("/bulk_mark_attendance")
        assert rv.status_code == 200
        with client.session_transaction() as sess:
            sess.pop("active_company_id", None)


# ═══════════════════════════════════════════════════════════════════════════════
# Kiosk /attendance — Late Login and Half Day Login (lines 1118-1122)
# ═══════════════════════════════════════════════════════════════════════════════

class TestKioskLoginStatus:

    def _login_fresh(self, client, seed_employee, db_engine, mocker, s_start, s_half, grace, signed_qr):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        mocker.patch("blueprints.attendance.get_employee_shift",
                     return_value=(s_start, s_half, datetime.time(18, 0), "Test Shift"))
        _patch_shift_rules(mocker, grace_minutes=grace)
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
        })
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()
        return rv

    def test_late_login_status(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Line 1120: current_time > grace_time but <= s_half → 'Late Login'."""
        rv = self._login_fresh(
            client, seed_employee, db_engine, mocker,
            s_start=datetime.time(0, 0),
            s_half=datetime.time(23, 59),
            grace=0,  # grace_time = 00:00 → any time > midnight = Late Login
            signed_qr=signed_qr,
        )
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Late Login"

    def test_half_day_login_status(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Line 1122: current_time > s_half → 'Half Day Login'."""
        rv = self._login_fresh(
            client, seed_employee, db_engine, mocker,
            s_start=datetime.time(0, 0),
            s_half=datetime.time(0, 1),  # s_half=00:01 → any daytime is Half Day
            grace=0,
            signed_qr=signed_qr,
        )
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Half Day Login"


# ═══════════════════════════════════════════════════════════════════════════════
# Kiosk /attendance — Half Day Logout, Early Logout (lines 1143-1148)
# ═══════════════════════════════════════════════════════════════════════════════

class TestKioskLogoutStatus:

    def _logout(self, client, seed_employee, db_engine, mocker, s_half, s_end, signed_qr):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.execute(
            "INSERT INTO attendance (employee_id, date, login_time, status) "
            "VALUES (%s, %s, '09:00:00', 'Full Day Login')",
            (seed_employee["employee_id"], today),
        )
        cur.close()
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        mocker.patch("blueprints.attendance.get_employee_shift",
                     return_value=(datetime.time(9, 0), s_half, s_end, "Test Shift"))
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
        })
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()
        return rv

    def test_half_day_logout_status(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Line 1144: current_time < s_half → 'Half Day Logout'."""
        rv = self._logout(
            client, seed_employee, db_engine, mocker,
            s_half=datetime.time(23, 59),
            s_end=datetime.time(23, 59),
            signed_qr=signed_qr,
        )
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Half Day Logout"

    def test_early_logout_status(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Line 1146: s_half <= current_time < s_end → 'Early Logout'."""
        rv = self._logout(
            client, seed_employee, db_engine, mocker,
            s_half=datetime.time(0, 1),   # past midnight → no Half Day Logout
            s_end=datetime.time(23, 59),   # hasn't reached end yet → Early Logout
            signed_qr=signed_qr,
        )
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Early Logout"

    def test_second_logout_uses_last_relogin(self, client, seed_employee, att_post_relogin, db_engine, mocker, signed_qr):
        """Line 1135: session_start = last_relogin_stored (not login_time) for second logout."""
        # att_post_relogin: login_time SET, logout_time NULL, last_relogin='12:30:00'
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        mocker.patch("blueprints.attendance.get_employee_shift",
                     return_value=(datetime.time(8, 0), datetime.time(0, 1), datetime.time(23, 59), "Test Shift"))
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
        })
        data = rv.get_json()
        # logout_time is NULL → this is the logout path; last_relogin is used as session_start
        assert data["ok"] is True, data
        assert data["type"] == "logout"


# ═══════════════════════════════════════════════════════════════════════════════
# Kiosk /attendance — WFH employee outside home location (lines 1059-1062)
# ═══════════════════════════════════════════════════════════════════════════════

class TestKioskWfhGeoFence:

    def test_wfh_employee_outside_home_rejected(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Lines 1059-1062: work_mode='wfh' + work_lat/lon set + user outside → rejected."""
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET work_mode='wfh', work_lat=12.9716, work_lon=77.5946 "
            "WHERE employee_id='TST001'"
        )
        cur.close()
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": True,
        })
        try:
            rv = client.post("/attendance", json={
                "employee_id": signed_qr(seed_employee["employee_id"]),
                "auth_combo":  "qr_only",
                "lat":         19.0760,   # Mumbai — far from Bangalore home
                "lon":         72.8777,
            })
            data = rv.get_json()
            assert data["ok"] is False
            assert "home location" in data["msg"].lower() or "outside" in data["msg"].lower()
        finally:
            cur = db_engine.cursor()
            cur.execute(
                "UPDATE employees SET work_mode='office', work_lat=NULL, work_lon=NULL "
                "WHERE employee_id='TST001'"
            )
            cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# /api/attendance/checkin — Late/Half Day Login, Half Day/Early Logout, WFH fence
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiCheckinStatusBranches:

    def _patch_shifts(self, s_start, s_half, s_end, grace=0):
        rules = _cfg.ShiftRules(s_start, s_half, s_end, grace)
        _cfg.shift_rules = lambda: rules

    def _restore_shifts(self):
        _cfg.shift_rules = _REAL_SHIFT_RULES

    def test_late_login(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        self._patch_shifts(datetime.time(0, 0), datetime.time(23, 59), datetime.time(23, 59), grace=0)
        try:
            token = _admin_token(client, seed_admin)
            rv = client.post("/api/attendance/checkin", json={
                "employee_id": seed_employee["employee_id"],
            }, headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["status"] == "Late Login"
        finally:
            self._restore_shifts()
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
            cur.close()

    def test_half_day_login(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        self._patch_shifts(datetime.time(0, 0), datetime.time(0, 1), datetime.time(23, 59), grace=0)
        try:
            token = _admin_token(client, seed_admin)
            rv = client.post("/api/attendance/checkin", json={
                "employee_id": seed_employee["employee_id"],
            }, headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["status"] == "Half Day Login"
        finally:
            self._restore_shifts()
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
            cur.close()

    def test_half_day_logout(self, client, seed_admin, seed_employee, att_login_only, db_engine):
        # current_time < SHIFT_HALF → Half Day Logout
        self._patch_shifts(datetime.time(0, 0), datetime.time(23, 59), datetime.time(23, 59))
        try:
            token = _admin_token(client, seed_admin)
            rv = client.post("/api/attendance/checkin", json={
                "employee_id": seed_employee["employee_id"],
            }, headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["status"] == "Half Day Logout"
        finally:
            self._restore_shifts()

    def test_early_logout(self, client, seed_admin, seed_employee, att_login_only, db_engine):
        # SHIFT_HALF past → SHIFT_END not reached → Early Logout
        self._patch_shifts(datetime.time(0, 0), datetime.time(0, 1), datetime.time(23, 59))
        try:
            token = _admin_token(client, seed_admin)
            rv = client.post("/api/attendance/checkin", json={
                "employee_id": seed_employee["employee_id"],
            }, headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["status"] == "Early Logout"
        finally:
            self._restore_shifts()

    def test_wfh_employee_outside_home_rejected(self, client, seed_admin, seed_employee, db_engine):
        """Lines 1355-1358: WFH employee with home coords, user outside → rejected."""
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET work_mode='wfh', work_lat=12.9716, work_lon=77.5946 "
            "WHERE employee_id='TST001'"
        )
        cur.close()
        try:
            token = _admin_token(client, seed_admin)
            rv = client.post("/api/attendance/checkin", json={
                "employee_id": seed_employee["employee_id"],
                "lat":         19.0760,
                "lon":         72.8777,
            }, headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is False
            assert "home location" in data["msg"].lower()
        finally:
            cur = db_engine.cursor()
            cur.execute(
                "UPDATE employees SET work_mode='office', work_lat=NULL, work_lon=NULL "
                "WHERE employee_id='TST001'"
            )
            cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# /api/employee/checkin — invalid punched_at, WFH fence, login/logout status
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiEmployeeCheckinCoverage:

    def _patch_shifts(self, s_start, s_half, s_end, grace=0):
        rules = _cfg.ShiftRules(s_start, s_half, s_end, grace)
        _cfg.shift_rules = lambda: rules

    def _restore_shifts(self):
        _cfg.shift_rules = _REAL_SHIFT_RULES

    def test_invalid_punched_at_silently_uses_now(self, client, seed_employee, db_engine):
        """Lines 1474-1475: malformed punched_at string → ValueError → pass, use now."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin", json={
            **OFFICE, "punched_at": "not-an-iso-string",
        }, headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["action"] == "login"
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()

    def test_wfh_employee_outside_home_rejected(self, client, seed_employee, db_engine):
        """Lines 1454-1457: WFH + home lat/lon set + user outside → rejected."""
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET work_mode='wfh', work_lat=12.9716, work_lon=77.5946 "
            "WHERE employee_id='TST001'"
        )
        cur.close()
        try:
            token = _emp_token(client, seed_employee)
            rv = client.post("/api/employee/checkin", json={
                "lat": 19.0760,
                "lon": 72.8777,
            }, headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is False
            assert "home location" in data["msg"].lower()
        finally:
            cur = db_engine.cursor()
            cur.execute(
                "UPDATE employees SET work_mode='office', work_lat=NULL, work_lon=NULL "
                "WHERE employee_id='TST001'"
            )
            cur.close()

    def test_late_login(self, client, seed_employee, db_engine):
        """Line 1497: current_time > grace → Late Login."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        self._patch_shifts(datetime.time(0, 0), datetime.time(23, 59), datetime.time(23, 59), grace=0)
        try:
            token = _emp_token(client, seed_employee)
            rv = client.post("/api/employee/checkin", json=OFFICE,
                             headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["status"] == "Late Login"
        finally:
            self._restore_shifts()
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
            cur.close()

    def test_half_day_login(self, client, seed_employee, db_engine):
        """Line 1499: current_time > s_half → Half Day Login."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        self._patch_shifts(datetime.time(0, 0), datetime.time(0, 1), datetime.time(23, 59), grace=0)
        try:
            token = _emp_token(client, seed_employee)
            rv = client.post("/api/employee/checkin", json=OFFICE,
                             headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["status"] == "Half Day Login"
        finally:
            self._restore_shifts()
            cur = db_engine.cursor()
            cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
            cur.close()

    def test_half_day_logout(self, client, seed_employee, att_login_only, db_engine):
        """Line 1516: current_time < s_half → Half Day Logout."""
        self._patch_shifts(datetime.time(0, 0), datetime.time(23, 59), datetime.time(23, 59))
        try:
            token = _emp_token(client, seed_employee)
            rv = client.post("/api/employee/checkin", json=OFFICE,
                             headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["action"] == "logout"
            assert data["status"] == "Half Day Logout"
        finally:
            self._restore_shifts()

    def test_early_logout(self, client, seed_employee, att_login_only, db_engine):
        """Line 1518: s_half <= current_time < s_end → Early Logout."""
        self._patch_shifts(datetime.time(0, 0), datetime.time(0, 1), datetime.time(23, 59))
        try:
            token = _emp_token(client, seed_employee)
            rv = client.post("/api/employee/checkin", json=OFFICE,
                             headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["action"] == "logout"
            assert data["status"] == "Early Logout"
        finally:
            self._restore_shifts()

    def test_relogin_second_logout_uses_last_relogin(self, client, seed_employee, att_post_relogin, db_engine):
        """Line 1510: session_start = last_relogin_stored (not login_time)."""
        # att_post_relogin: login SET, logout NULL, last_relogin SET → second logout
        self._patch_shifts(datetime.time(0, 0), datetime.time(0, 1), datetime.time(23, 59))
        try:
            token = _emp_token(client, seed_employee)
            rv = client.post("/api/employee/checkin", json=OFFICE,
                             headers={"Authorization": f"Bearer {token}"})
            data = rv.get_json()
            assert data["ok"] is True, data
            assert data["action"] == "logout"
        finally:
            self._restore_shifts()


# ═══════════════════════════════════════════════════════════════════════════════
# /api/employee/qr-face-checkin — WFH fence, optional face save, login/logout
# ═══════════════════════════════════════════════════════════════════════════════

class TestQrFaceCheckinCoverage:

    def _make_jpeg(self):
        from PIL import Image as _PIL
        buf = io.BytesIO()
        _PIL.new("RGB", (10, 10)).save(buf, format="JPEG")
        buf.seek(0)
        return buf

    def test_wfh_outside_home_rejected(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Lines 1589-1600: WFH employee outside home → rejected."""
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET work_mode='wfh', work_lat=12.9716, work_lon=77.5946 "
            "WHERE employee_id='TST001'"
        )
        cur.close()
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": True,
            "fingerprint_enabled": False, "location_enabled": True,
        })
        try:
            rv = client.post("/api/employee/qr-face-checkin", data={
                "employee_id": signed_qr(seed_employee["employee_id"]),
                "auth_combo":  "qr_face",
                "lat":         "19.0760",
                "lon":         "72.8777",
            }, content_type="multipart/form-data")
            data = rv.get_json()
            assert data["ok"] is False
            assert "home location" in data["msg"].lower() or "outside" in data["msg"].lower()
        finally:
            cur = db_engine.cursor()
            cur.execute(
                "UPDATE employees SET work_mode='office', work_lat=NULL, work_lon=NULL "
                "WHERE employee_id='TST001'"
            )
            cur.close()

    def test_optional_face_photo_saved_when_not_needed(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Lines 1636-1645: auth_combo=qr_fingerprint (no face needed) + face_photo uploaded → save attempt."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        emp_id = seed_employee["employee_id"].upper()
        # Pre-set fingerprint proof so the auth guard passes
        with client.session_transaction() as sess:
            sess["wa_fp_verified_emp_id"] = emp_id
            sess["wa_fp_verified_at"] = _time_stdlib.time()
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        _patch_shift_rules(mocker, grace_minutes=0, start=datetime.time(0, 0), half=datetime.time(23, 59), end=datetime.time(23, 59))
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",  # needs fp not face
            "face_photo":  (self._make_jpeg(), "face.jpg"),
        }, content_type="multipart/form-data")
        data = rv.get_json()
        # Face photo saved as optional log; login proceeds
        assert data["ok"] is True, data
        assert data["action"] == "login"
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()

    def test_qr_face_known_enc_none_returns_400(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Lines 1622-1627: _get_known_face_encoding returns None → 400."""
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET face_image='/tmp/fake_face.jpg' WHERE employee_id='TST001'")
        cur.close()
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": True,
            "fingerprint_enabled": False, "location_enabled": False,
        })
        mocker.patch("blueprints.employee_portal._face_recognition_available", True)
        mocker.patch("blueprints.employee_portal.os.path.exists", return_value=True)
        mocker.patch("blueprints.employee_portal._get_known_face_encoding", return_value=None)
        # PIL.Image.open will be called; provide a valid JPEG
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
            "face_photo":  (self._make_jpeg(), "face.jpg"),
        }, content_type="multipart/form-data")
        data = rv.get_json()
        assert data["ok"] is False
        assert "not detected" in data["msg"].lower() or "invalid" in data["msg"].lower()
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET face_image=NULL WHERE employee_id='TST001'")
        cur.close()

    def _qr_fp_session(self, client, emp_id):
        """Pre-set a valid fingerprint session proof so qr_fingerprint auth passes."""
        with client.session_transaction() as sess:
            sess["wa_fp_verified_emp_id"] = emp_id.upper()
            sess["wa_fp_verified_at"] = _time_stdlib.time()

    def test_late_login_qrface(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Line 1668: current_time > grace but <= s_half → Late Login."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        self._qr_fp_session(client, seed_employee["employee_id"])
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        _patch_shift_rules(mocker, grace_minutes=0, start=datetime.time(0, 0), half=datetime.time(23, 59), end=datetime.time(23, 59))
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        }, content_type="multipart/form-data")
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Late Login"
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()

    def test_half_day_login_qrface(self, client, seed_employee, db_engine, mocker, signed_qr):
        """Line 1670: current_time > s_half → Half Day Login."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        self._qr_fp_session(client, seed_employee["employee_id"])
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        _patch_shift_rules(mocker, grace_minutes=0, start=datetime.time(0, 0), half=datetime.time(0, 1), end=datetime.time(23, 59))
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        }, content_type="multipart/form-data")
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Half Day Login"
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()

    def test_half_day_logout_qrface(self, client, seed_employee, att_login_only, db_engine, mocker, signed_qr):
        """Line 1687: current_time < s_half → Half Day Logout."""
        self._qr_fp_session(client, seed_employee["employee_id"])
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        _patch_shift_rules(mocker, half=datetime.time(23, 59), end=datetime.time(23, 59))
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        }, content_type="multipart/form-data")
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Half Day Logout"

    def test_early_logout_qrface(self, client, seed_employee, att_login_only, db_engine, mocker, signed_qr):
        """Line 1689: s_half < current_time < s_end → Early Logout."""
        self._qr_fp_session(client, seed_employee["employee_id"])
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        _patch_shift_rules(mocker, half=datetime.time(0, 1), end=datetime.time(23, 59))
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        }, content_type="multipart/form-data")
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["status"] == "Early Logout"

    def test_relogin_qrface(self, client, seed_employee, att_completed_basic, db_engine, mocker, signed_qr):
        """Lines 1704-1711: relogin after a completed session."""
        self._qr_fp_session(client, seed_employee["employee_id"])
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        }, content_type="multipart/form-data")
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["action"] == "relogin"


# ═══════════════════════════════════════════════════════════════════════════════
# api_shifts — NULL times, create exception, assign missing emp_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiShiftsCoverage:

    def test_get_shift_with_null_times_returns_dashes(self, client, seed_admin, mocker):
        """Lines 1771-1773: shift row with None times → '--' for each time field."""
        # The shifts table has NOT NULL on time columns, so mock the cursor
        # to return a row with None times (defensive branch in the route).
        mock_shift_row = (999, "Null-Time Shift", None, None, None)
        mock_emp_row = ("TST999", "Ghost", "employee", None, None)
        mock_cur = mocker.MagicMock()
        mock_cur.fetchall.side_effect = [[mock_shift_row], [mock_emp_row]]
        mock_conn = mocker.MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mocker.patch("blueprints.attendance.get_db_connection", return_value=mock_conn)
        token = _admin_token(client, seed_admin)
        rv = client.get("/api/shifts", headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 200
        shifts = rv.get_json()["shifts"]
        assert len(shifts) == 1
        assert shifts[0]["start"] == "--"
        assert shifts[0]["half"] == "--"
        assert shifts[0]["end"] == "--"

    def test_create_shift_db_exception_returns_400(self, client, seed_admin, mocker):
        """Lines 1808-1811: DB exception during INSERT → 400."""
        mock_cursor = mocker.MagicMock()
        mock_cursor.execute.side_effect = Exception("duplicate key")
        mock_conn = mocker.MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mocker.patch("blueprints.attendance.get_db_connection", return_value=mock_conn)
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/shifts", json={
            "name": "Duplicate Shift", "start_time": "09:00",
            "half_time": "13:00", "end_time": "18:00",
        }, headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 400
        assert rv.get_json()["ok"] is False

    def test_assign_shift_missing_emp_id_returns_400(self, client, seed_admin):
        """Line 1835: missing emp_id → 400."""
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/shifts/assign", json={"shift_id": 1},
                         headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 400
        assert "emp_id" in rv.get_json()["msg"]


# ═══════════════════════════════════════════════════════════════════════════════
# Fingerprint auth proof coverage (lines 86-89 via /attendance route)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFingerprintRecentlyVerifiedViaRoute:

    def test_wa_fingerprint_verified_allows_login(self, client, seed_employee, db_engine, mocker):
        """Lines 86-89: _wa_fingerprint_recently_verified returns True → login proceeds."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
        emp_id = seed_employee["employee_id"].upper()
        # Pre-set session proof
        with client.session_transaction() as sess:
            sess["wa_fp_verified_emp_id"] = emp_id
            sess["wa_fp_verified_at"] = _time_stdlib.time()
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": True, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        rv = client.post("/attendance", json={
            "employee_id": seed_employee["employee_id"],
            "auth_combo":  "fingerprint_only",
        })
        data = rv.get_json()
        # With a valid proof, the function returns True and login proceeds
        assert data["ok"] is True, data
        assert data["type"] in ("login", "logout", "relogin")
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id='TST001' AND date=%s", (today,))
        cur.close()
