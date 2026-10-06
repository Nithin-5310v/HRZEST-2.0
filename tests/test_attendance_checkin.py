"""
Attendance blueprint — comprehensive test suite for check-in/out flows.

Covers (all gaps NOT already in test_leave_attendance.py):
  /location, /attendance (kiosk), /api/attendance/checkin,
  /api/employee/checkin, /api/employee/qr-face-checkin,
  /api/employee/attendance, shift swap lifecycle,
  /monthly_report_export, /send_absentee_report, /my_attendance_pdf,
  helper functions, correct_attendance edge cases,
  bulk_assign_shift edge cases, assign_shift, edit_shift edge cases,
  delete_shift_form, employee_attendance_detail edge cases,
  update_default_shift NameError bug.

"""
import base64
import datetime
import io
import pytest


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
def shift_a(db_engine):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO shifts (name, start_time, half_time, end_time) "
        "VALUES ('CI Morning Shift', '09:00', '13:00', '18:00') RETURNING id"
    )
    sid = cur.fetchone()[0]
    yield {"id": sid, "name": "CI Morning Shift"}
    cur.execute("UPDATE employees SET shift_id=NULL WHERE shift_id=%s", (sid,))
    cur.execute("UPDATE break_config SET shift_id=NULL WHERE shift_id=%s", (sid,))
    cur.execute("DELETE FROM shifts WHERE id=%s", (sid,))
    cur.close()


@pytest.fixture
def shift_b(db_engine):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO shifts (name, start_time, half_time, end_time) "
        "VALUES ('CI Evening Shift', '14:00', '17:00', '22:00') RETURNING id"
    )
    sid = cur.fetchone()[0]
    yield {"id": sid, "name": "CI Evening Shift"}
    cur.execute("UPDATE employees SET shift_id=NULL WHERE shift_id=%s", (sid,))
    cur.execute("UPDATE break_config SET shift_id=NULL WHERE shift_id=%s", (sid,))
    cur.execute("DELETE FROM shifts WHERE id=%s", (sid,))
    cur.close()


@pytest.fixture
def attendance_today(db_engine, seed_employee):
    """Seed a login-only record for today — next checkin becomes a logout."""
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
def attendance_completed(db_engine, seed_employee):
    """Seed a completed (login + logout) record — next checkin becomes a relogin."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# Helper function unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsWithinRange:
    """Unit tests for the haversine geo-fence helper."""

    def test_same_point_is_within(self):
        from blueprints.attendance import is_within_range
        assert is_within_range(12.9716, 77.5946, 12.9716, 77.5946) is True

    def test_very_close_distance(self):
        from blueprints.attendance import is_within_range
        # ~1 m apart — should be within any reasonable office radius
        assert is_within_range(12.971600, 77.594600, 12.971609, 77.594600) is True

    def test_different_cities_out_of_range(self):
        from blueprints.attendance import is_within_range
        # Bangalore vs Mumbai (~980 km)
        assert is_within_range(12.9716, 77.5946, 19.0760, 72.8777) is False


# _fmt_t was only ever local to master's now-removed duplicate
# api_employee_attendance route in blueprints/attendance.py — that route
# (and its own _fmt_t helper) live solely in blueprints/employee_portal.py
# after the podman-migration/master merge consolidated the duplicate away.


# ═══════════════════════════════════════════════════════════════════════════════
# /attendance (kiosk check-in, no auth, POST JSON)
# ═══════════════════════════════════════════════════════════════════════════════

class TestKioskAttendance:

    def test_invalid_auth_combo(self, client, seed_employee):
        rv = client.post("/attendance", json={
            "employee_id": seed_employee["employee_id"],
            "auth_combo":  "bad_combo",
        })
        data = rv.get_json()
        assert data["ok"] is False
        assert "Invalid auth combination" in data["msg"]

    def test_missing_emp_id_qr_only(self, client):
        rv = client.post("/attendance", json={"auth_combo": "qr_only", "employee_id": ""})
        assert rv.get_json()["ok"] is False

    def test_missing_emp_id_fingerprint_only(self, client):
        rv = client.post("/attendance", json={"auth_combo": "fingerprint_only", "employee_id": ""})
        data = rv.get_json()
        assert data["ok"] is False
        assert "Employee ID is required" in data["msg"]

    def test_unknown_employee(self, client, signed_qr, mocker):
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        rv = client.post("/attendance", json={
            "employee_id": signed_qr("GHOST99"), "auth_combo": "qr_only",
        })
        data = rv.get_json()
        assert data["ok"] is False
        assert "not found" in data["msg"].lower()

    def test_qr_only_login_creates_attendance_record(self, client, seed_employee, db_engine, mocker, signed_qr):
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
        })
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["type"] == "login"
        assert data["status"] in ("Full Day Login", "Late Login", "Half Day Login")

        cur = db_engine.cursor()
        cur.execute("SELECT login_time, logout_time FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        row = cur.fetchone()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        assert row is not None
        assert row[0] is not None   # login_time set
        assert row[1] is None       # logout_time still NULL

    def test_qr_only_logout_sets_logout_time(self, client, seed_employee, attendance_today, db_engine, mocker, signed_qr):
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
        })
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["type"] == "logout"
        assert data["status"] in ("Half Day Logout", "Early Logout", "Completed")

        cur = db_engine.cursor()
        cur.execute("SELECT logout_time FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], attendance_today["date"]))
        assert cur.fetchone()[0] is not None
        cur.close()

    def test_qr_only_relogin_clears_logout_time(self, client, seed_employee, attendance_completed, db_engine, mocker, signed_qr):
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": False, "location_enabled": False,
        })
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
        })
        data = rv.get_json()
        assert data["ok"] is True, data
        assert data["type"] == "relogin"

        cur = db_engine.cursor()
        cur.execute("SELECT logout_time FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], attendance_completed["date"]))
        assert cur.fetchone()[0] is None
        cur.close()

    def test_qr_face_empty_face_image(self, client, seed_employee, signed_qr):
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
            "face_image":  "",
        })
        data = rv.get_json()
        assert data["ok"] is False
        assert "Face photo not captured" in data["msg"]

    def test_qr_face_invalid_base64(self, client, seed_employee, signed_qr):
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
            "face_image":  "!!!not_valid_base64!!!",
        })
        data = rv.get_json()
        assert data["ok"] is False
        assert "Invalid face image data" in data["msg"]

    def test_qr_face_recognition_unavailable(self, client, seed_employee, mocker, signed_qr):
        from PIL import Image as _PIL
        buf = io.BytesIO()
        _PIL.new("RGB", (100, 100)).save(buf, format="JPEG")
        face_b64 = base64.b64encode(buf.getvalue()).decode()

        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "qr_enabled": True,
            "face_enabled": True, "location_enabled": False,
        })
        mocker.patch("blueprints.attendance._face_recognition_available", False)
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
            "face_image":  face_b64,
        })
        data = rv.get_json()
        assert data["ok"] is False
        assert "unavailable" in data["msg"].lower()

    def test_fingerprint_not_enabled_returns_403(self, client, seed_employee, mocker):
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "location_enabled": False,
            "face_enabled": True, "qr_enabled": True,
        })
        rv = client.post("/attendance", json={
            "employee_id": seed_employee["employee_id"],
            "auth_combo":  "fingerprint_only",
        })
        assert rv.status_code == 403
        assert rv.get_json()["ok"] is False

    def test_fingerprint_not_verified_returns_401(self, client, seed_employee, mocker):
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": True, "location_enabled": False,
            "face_enabled": False, "qr_enabled": True,
        })
        mocker.patch("blueprints.attendance._wa_fingerprint_recently_verified", return_value=False)
        rv = client.post("/attendance", json={
            "employee_id": seed_employee["employee_id"],
            "auth_combo":  "fingerprint_only",
        })
        assert rv.status_code == 401
        assert rv.get_json()["ok"] is False

    def test_location_required_but_missing(self, client, seed_employee, mocker, signed_qr):
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "location_enabled": True,
            "face_enabled": False, "qr_enabled": True,
        })
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
        })
        data = rv.get_json()
        assert data["ok"] is False
        assert "Location not captured" in data["msg"]

    def test_location_outside_office_rejected(self, client, seed_employee, mocker, signed_qr):
        mocker.patch("blueprints.attendance.get_auth_config", return_value={
            "fingerprint_enabled": False, "location_enabled": True,
            "face_enabled": False, "qr_enabled": True,
        })
        mocker.patch("blueprints.attendance.is_within_office_range", return_value=False)
        rv = client.post("/attendance", json={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_only",
            "lat": 19.0760, "lon": 72.8777,
        })
        data = rv.get_json()
        assert data["ok"] is False
        assert "outside" in data["msg"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# /api/attendance/checkin (admin Bearer token, POST JSON)
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiAdminCheckin:

    def test_requires_admin_token(self, client):
        rv = client.post("/api/attendance/checkin", json={"employee_id": "TST001"})
        assert rv.status_code == 401

    def test_missing_employee_id_returns_400(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/attendance/checkin", json={},
                         headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 400
        assert rv.get_json()["ok"] is False

    def test_unknown_employee_returns_error(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/attendance/checkin", json={"employee_id": "GHOST99"},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is False
        assert "not found" in data["msg"].lower()

    def test_login_creates_record(self, client, seed_admin, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        token = _admin_token(client, seed_admin)
        rv = client.post("/api/attendance/checkin",
                         json={"employee_id": seed_employee["employee_id"]},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["type"] == "login"
        assert data["status"] in ("Full Day Login", "Late Login", "Half Day Login")

        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

    def test_logout_updates_record(self, client, seed_admin, seed_employee, attendance_today):
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/attendance/checkin",
                         json={"employee_id": seed_employee["employee_id"]},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["type"] == "logout"
        assert "att_type" in data

    def test_relogin_clears_logout_time(self, client, seed_admin, seed_employee, attendance_completed):
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/attendance/checkin",
                         json={"employee_id": seed_employee["employee_id"]},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["type"] == "relogin"

    def test_geo_fence_rejection(self, client, seed_admin, seed_employee, db_engine, mocker):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        mocker.patch("utils.attendance_utils.is_within_office_range", return_value=False)
        token = _admin_token(client, seed_admin)
        rv = client.post("/api/attendance/checkin",
                         json={"employee_id": seed_employee["employee_id"],
                               "lat": 19.0760, "lon": 72.8777},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is False
        assert "outside" in data["msg"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# /api/employee/checkin (employee Bearer token, POST JSON)
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiEmployeeCheckin:

    def test_requires_employee_token(self, client):
        rv = client.post("/api/employee/checkin", json={})
        assert rv.status_code == 401

    def test_login_creates_attendance(self, client, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin", json={},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["action"] == "login"
        assert data["status"] in ("Full Day Login", "Late Login", "Half Day Login")

        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

    def test_logout_updates_existing_login(self, client, seed_employee, attendance_today):
        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin", json={},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["action"] == "logout"
        assert "att_type" in data

    def test_relogin_after_completed(self, client, seed_employee, attendance_completed):
        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin", json={},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["action"] == "relogin"

    def test_offline_punch_within_24h_accepted(self, client, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        two_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=2)
        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin",
                         json={"punched_at": two_hours_ago.isoformat()},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["action"] == "login"

        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

    def test_offline_punch_over_24h_rejected(self, client, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        two_days_ago = datetime.datetime.now() - datetime.timedelta(days=2)
        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin",
                         json={"punched_at": two_days_ago.isoformat()},
                         headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 400
        data = rv.get_json()
        assert data["ok"] is False
        assert "24" in data["msg"]

    def test_invalid_lat_lon_returns_400(self, client, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin",
                         json={"lat": "not_a_float", "lon": "also_bad"},
                         headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 400
        assert rv.get_json()["ok"] is False

    def test_geo_fence_rejection(self, client, seed_employee, db_engine, mocker):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        mocker.patch("blueprints.employee_portal.is_within_office_range", return_value=False)
        token = _emp_token(client, seed_employee)
        rv = client.post("/api/employee/checkin",
                         json={"lat": 19.0760, "lon": 72.8777},
                         headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is False
        assert "outside" in data["msg"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# /api/employee/qr-face-checkin (public kiosk, multipart/form-data)
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiQrFaceCheckin:

    def test_invalid_auth_combo_returns_400(self, client, seed_employee):
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": seed_employee["employee_id"],
            "auth_combo":  "unknown_combo",
        })
        assert rv.status_code == 400
        assert rv.get_json()["ok"] is False

    def test_missing_employee_id_returns_400(self, client):
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": "",
            "auth_combo":  "qr_face",
        })
        assert rv.status_code == 400

    def test_qr_disabled_returns_403(self, client, seed_employee, mocker, signed_qr):
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": False, "face_enabled": True,
            "fingerprint_enabled": False, "location_enabled": False,
        })
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
        })
        assert rv.status_code == 403

    def test_face_disabled_returns_403(self, client, seed_employee, mocker, signed_qr):
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": False, "location_enabled": False,
        })
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
        })
        assert rv.status_code == 403

    def test_fingerprint_disabled_returns_403(self, client, seed_employee, mocker, signed_qr):
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": True,
            "fingerprint_enabled": False, "location_enabled": False,
        })
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        })
        assert rv.status_code == 403

    def test_fingerprint_not_verified_returns_401(self, client, seed_employee, mocker, signed_qr):
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        mocker.patch("blueprints.employee_portal._wa_fingerprint_recently_verified", return_value=False)
        mocker.patch("blueprints.employee_portal._mobile_biometric_recently_verified", return_value=False)
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        })
        assert rv.status_code == 401

    def test_unknown_employee_returns_404(self, client, mocker, signed_qr):
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": True,
            "fingerprint_enabled": False, "location_enabled": False,
        })
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr("GHOST99"),
            "auth_combo":  "qr_face",
        })
        assert rv.status_code == 404

    def test_qr_fingerprint_login_with_verified_fingerprint(
            self, client, seed_employee, db_engine, mocker, signed_qr):
        """qr_fingerprint with a verified fingerprint should create a login record."""
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        mocker.patch("blueprints.employee_portal._wa_fingerprint_recently_verified", return_value=True)

        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        })
        data = rv.get_json()
        assert data["ok"] is True
        assert data["action"] == "login"

        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

    def test_qr_face_without_photo_returns_400(self, client, seed_employee, mocker, signed_qr):
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": True,
            "fingerprint_enabled": False, "location_enabled": False,
        })
        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
        })
        assert rv.status_code == 400
        assert "face photo required" in rv.get_json()["msg"].lower()

    def test_qr_face_with_face_recognition_unavailable(self, client, seed_employee, mocker, signed_qr):
        from PIL import Image as _PIL
        buf = io.BytesIO()
        _PIL.new("RGB", (100, 100)).save(buf, format="JPEG")
        buf.seek(0)

        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": True,
            "fingerprint_enabled": False, "location_enabled": False,
        })
        mocker.patch("blueprints.employee_portal._face_recognition_available", False)

        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_face",
            "face_photo":  (buf, "face.jpg", "image/jpeg"),
        }, content_type="multipart/form-data")
        assert rv.status_code == 503
        assert "unavailable" in rv.get_json()["msg"].lower()

    def test_qr_fingerprint_relogin_after_completed(
            self, client, seed_employee, attendance_completed, mocker, signed_qr):
        # Face off: non-face check-in methods are only permitted then (allowed_checkin_combos).
        mocker.patch("blueprints.employee_portal.get_auth_config", return_value={
            "qr_enabled": True, "face_enabled": False,
            "fingerprint_enabled": True, "location_enabled": False,
        })
        mocker.patch("blueprints.employee_portal._wa_fingerprint_recently_verified", return_value=True)

        rv = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]),
            "auth_combo":  "qr_fingerprint",
        })
        data = rv.get_json()
        assert data["ok"] is True
        assert data["action"] == "relogin"


# ═══════════════════════════════════════════════════════════════════════════════
# /api/employee/attendance (employee Bearer token, GET)
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiEmployeeAttendanceGet:

    def test_requires_employee_token(self, client):
        rv = client.get("/api/employee/attendance")
        assert rv.status_code == 401

    def test_returns_required_json_keys(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        rv = client.get("/api/employee/attendance",
                        headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert "records" in data
        assert "summary" in data
        assert "year" in data
        assert "month" in data
        assert "month_name" in data

    def test_custom_year_month(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        rv = client.get("/api/employee/attendance?year=2025&month=3",
                        headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        assert data["year"] == 2025
        assert data["month"] == 3
        assert "March 2025" in data["month_name"]

    def test_invalid_year_returns_400(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        rv = client.get("/api/employee/attendance?year=notanumber",
                        headers={"Authorization": f"Bearer {token}"})
        assert rv.status_code == 400
        assert rv.get_json()["ok"] is False

    def test_records_contain_expected_fields(self, client, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.execute(
            "INSERT INTO attendance (employee_id, date, login_time, logout_time, "
            "status, logout_status, attendance_type, worked_minutes) "
            "VALUES (%s,%s,'09:00:00','18:00:00','Full Day Login','Completed','Full Day',540)",
            (seed_employee["employee_id"], today),
        )
        cur.close()

        token = _emp_token(client, seed_employee)
        rv = client.get(f"/api/employee/attendance?year={today.year}&month={today.month}",
                        headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["ok"] is True
        rec = next((r for r in data["records"] if r["date"] == str(today)), None)
        assert rec is not None
        assert rec["attendance_type"] == "Full Day"
        assert rec["login_time"] == "09:00:00"
        assert "logout_time" in rec
        assert "worked_minutes" in rec

        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()

    def test_summary_counts_full_and_late_days(self, client, seed_employee, db_engine):
        today = datetime.date.today()
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.execute(
            "INSERT INTO attendance (employee_id, date, login_time, attendance_type) "
            "VALUES (%s,%s,'09:00:00','Full Day')",
            (seed_employee["employee_id"], today),
        )
        cur.close()

        token = _emp_token(client, seed_employee)
        rv = client.get(f"/api/employee/attendance?year={today.year}&month={today.month}",
                        headers={"Authorization": f"Bearer {token}"})
        data = rv.get_json()
        assert data["summary"]["full_days"] >= 1

        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], today))
        cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# /monthly_report_export (admin, GET → xlsx download)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMonthlyReportExport:

    def test_requires_admin(self, client):
        rv = client.get("/monthly_report_export")
        assert rv.status_code == 302

    def test_returns_xlsx_content_type(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/monthly_report_export?year=2025&month=1")
        assert rv.status_code == 200
        ct = rv.headers.get("Content-Type", "")
        assert "openxmlformats" in ct or "spreadsheet" in ct

    def test_filename_in_content_disposition(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/monthly_report_export?year=2025&month=3")
        cd = rv.headers.get("Content-Disposition", "")
        assert "attendance_2025_03.xlsx" in cd

    def test_response_has_xlsx_magic_bytes(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/monthly_report_export?year=2025&month=1")
        # XLSX is a ZIP archive — starts with PK magic bytes
        assert rv.data[:2] == b"PK"

    def test_default_params_uses_current_month(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/monthly_report_export")
        assert rv.status_code == 200
        assert rv.data[:2] == b"PK"


# ═══════════════════════════════════════════════════════════════════════════════
# /send_absentee_report (admin, POST → JSON)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSendAbsenteeReport:

    def test_requires_admin(self, client):
        rv = client.post("/send_absentee_report")
        assert rv.status_code == 302

    def test_no_email_config_returns_error_json(self, client, seed_admin, mocker):
        mocker.patch("blueprints.attendance.get_email_config", return_value=None)
        _admin_session(client, seed_admin)
        rv = client.post("/send_absentee_report")
        data = rv.get_json()
        assert data["ok"] is False
        assert "Email not configured" in data["msg"]

    def test_send_success_returns_ok(self, client, seed_admin, mocker):
        mocker.patch("blueprints.attendance.get_email_config", return_value={
            "user": "smtp@test.com",
            "from_email": "smtp@test.com",
            "password": "pass",
            "host": "smtp.test.com",
            "port": 587,
        })
        mock_send = mocker.patch("blueprints.attendance.send_email_smtp", return_value=None)
        _admin_session(client, seed_admin)
        rv = client.post("/send_absentee_report")
        data = rv.get_json()
        assert data["ok"] is True
        assert "Report sent" in data["msg"]
        mock_send.assert_called_once()

    def test_smtp_failure_returns_error_json(self, client, seed_admin, mocker):
        mocker.patch("blueprints.attendance.get_email_config", return_value={
            "user": "smtp@test.com", "from_email": "smtp@test.com",
        })
        mocker.patch("blueprints.attendance.send_email_smtp",
                     side_effect=Exception("SMTP connection refused"))
        _admin_session(client, seed_admin)
        rv = client.post("/send_absentee_report")
        data = rv.get_json()
        assert data["ok"] is False
        assert "Failed" in data["msg"]

    def test_email_sent_to_from_email_field(self, client, seed_admin, mocker):
        """send_email_smtp is called with the from_email address as recipient."""
        mocker.patch("blueprints.attendance.get_email_config", return_value={
            "user": "user@smtp.com",
            "from_email": "reports@smtp.com",
        })
        mock_send = mocker.patch("blueprints.attendance.send_email_smtp", return_value=None)
        _admin_session(client, seed_admin)
        client.post("/send_absentee_report")
        called_to = mock_send.call_args[0][0]
        assert called_to == "reports@smtp.com"


# ═══════════════════════════════════════════════════════════════════════════════
# /my_attendance_pdf (employee, GET → HTML page)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMyAttendancePdf:

    def test_requires_employee_session(self, client):
        rv = client.get("/my_attendance_pdf")
        assert rv.status_code == 302

    def test_returns_html_200(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_attendance_pdf")
        assert rv.status_code == 200
        assert b"Attendance Report" in rv.data

    def test_employee_name_appears_in_page(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_attendance_pdf")
        assert seed_employee["name"].encode() in rv.data

    def test_custom_year_month_in_page(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_attendance_pdf?year=2025&month=1")
        assert rv.status_code == 200
        assert b"January 2025" in rv.data

    def test_print_button_present(self, client, seed_employee):
        _emp_session(client, seed_employee)
        rv = client.get("/my_attendance_pdf")
        assert b"window.print()" in rv.data


# ═══════════════════════════════════════════════════════════════════════════════
# correct_attendance — edge cases beyond basic auth guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestCorrectAttendanceEdgeCases:

    def test_missing_emp_id_redirects(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/correct_attendance", data={
            "emp_id": "", "date": "2025-03-10", "attendance_type": "Full Day",
        })
        assert rv.status_code == 302

    def test_missing_attendance_type_redirects(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.post("/correct_attendance", data={
            "emp_id": seed_employee["employee_id"],
            "date":   "2025-03-10",
            "attendance_type": "",
        })
        assert rv.status_code == 302

    def test_invalid_date_format_redirects(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.post("/correct_attendance", data={
            "emp_id":          seed_employee["employee_id"],
            "date":            "not-a-date",
            "attendance_type": "Full Day",
        })
        assert rv.status_code == 302

    def test_insert_when_no_existing_record(self, client, seed_admin, seed_employee, db_engine):
        date_str = "2025-01-15"
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], date_str))
        cur.close()

        _admin_session(client, seed_admin)
        rv = client.post("/correct_attendance", data={
            "emp_id":          seed_employee["employee_id"],
            "date":            date_str,
            "login_time":      "09:00",
            "logout_time":     "18:00",
            "attendance_type": "Full Day",
            "year":            "2025",
            "month":           "1",
        })
        assert rv.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT attendance_type, status FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], date_str))
        row = cur.fetchone()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], date_str))
        cur.close()

        assert row is not None
        assert row[0] == "Full Day"
        assert row[1] == "Manual"

    def test_update_when_existing_record(self, client, seed_admin, seed_employee, db_engine):
        date_str = "2025-01-15"
        cur = db_engine.cursor()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], date_str))
        cur.execute(
            "INSERT INTO attendance (employee_id, date, login_time, attendance_type, status) "
            "VALUES (%s,%s,'08:00:00','Half Day','Late Login')",
            (seed_employee["employee_id"], date_str),
        )
        cur.close()

        _admin_session(client, seed_admin)
        rv = client.post("/correct_attendance", data={
            "emp_id":          seed_employee["employee_id"],
            "date":            date_str,
            "login_time":      "09:00",
            "logout_time":     "18:00",
            "attendance_type": "Full Day",
            "year":            "2025",
            "month":           "1",
        })
        assert rv.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT attendance_type, status FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], date_str))
        row = cur.fetchone()
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], date_str))
        cur.close()

        assert row[0] == "Full Day"
        assert row[1] == "Manual"


# ═══════════════════════════════════════════════════════════════════════════════
# bulk_assign_shift — edge cases beyond basic happy path
# ═══════════════════════════════════════════════════════════════════════════════

class TestBulkAssignShiftEdgeCases:

    def test_no_emp_ids_no_dept_updates_all_employees(
            self, client, seed_admin, seed_employee, shift_a, db_engine):
        """Known behavior: no emp_ids + no dept_filter → UPDATE employees SET shift_id=X (no WHERE)."""
        _admin_session(client, seed_admin)
        rv = client.post("/bulk_assign_shift", data={"shift_id": str(shift_a["id"])})
        assert rv.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        row = cur.fetchone()
        cur.execute("UPDATE employees SET shift_id=NULL WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        cur.close()

        assert row[0] == shift_a["id"]

    def test_dept_filter_updates_only_that_department(
            self, client, seed_admin, seed_employee, shift_a, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET department='Engineering' WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        cur.close()

        _admin_session(client, seed_admin)
        rv = client.post("/bulk_assign_shift", data={
            "shift_id":    str(shift_a["id"]),
            "dept_filter": "Engineering",
        })
        assert rv.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        row = cur.fetchone()
        cur.execute("UPDATE employees SET shift_id=NULL, department=NULL WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        cur.close()

        assert row[0] == shift_a["id"]

    def test_emp_ids_list_updates_specific_employees(
            self, client, seed_admin, seed_employee, shift_a, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/bulk_assign_shift", data={
            "shift_id": str(shift_a["id"]),
            "emp_ids":  [seed_employee["employee_id"]],
        })
        assert rv.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        row = cur.fetchone()
        cur.execute("UPDATE employees SET shift_id=NULL WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        cur.close()

        assert row[0] == shift_a["id"]


# ═══════════════════════════════════════════════════════════════════════════════
# assign_shift — JSON response and NULL assignment
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssignShift:

    def test_returns_json_ok(self, client, seed_admin, seed_employee, shift_a, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/assign_shift", data={
            "emp_id":   seed_employee["employee_id"],
            "shift_id": str(shift_a["id"]),
        })
        assert rv.status_code == 200
        assert rv.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET shift_id=NULL WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        cur.close()

    def test_empty_shift_id_clears_assignment(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/assign_shift", data={
            "emp_id":   seed_employee["employee_id"],
            "shift_id": "",
        })
        assert rv.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        assert cur.fetchone()[0] is None
        cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# edit_shift — exception path when sid cannot be parsed
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditShiftEdgeCases:

    def test_unparseable_sid_in_url_404s(self, client, seed_admin):
        """/edit_shift/<int:sid> -- the id lives in the URL path (Flask's
        int converter), not a form field, so a non-numeric id never even
        reaches the view; it 404s at the routing layer."""
        _admin_session(client, seed_admin)
        rv = client.post("/edit_shift/not_an_int", data={
            "shift_name": "Morning",
            "start_time": "09:00",
            "half_time":  "13:00",
            "end_time":   "18:00",
        })
        assert rv.status_code == 404

    def test_missing_name_skips_db_update(self, client, seed_admin, shift_a, db_engine):
        """Empty name causes early redirect without updating the DB."""
        _admin_session(client, seed_admin)
        rv = client.post(f"/edit_shift/{shift_a['id']}", data={
            "shift_name": "",
            "start_time": "08:00",
            "half_time":  "12:00",
            "end_time":   "17:00",
        })
        assert rv.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT name FROM shifts WHERE id=%s", (shift_a["id"],))
        assert cur.fetchone()[0] == shift_a["name"]  # unchanged
        cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# delete_shift — POST /delete_shift/<int:sid>
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteShiftForm:

    def test_bare_path_with_no_id_404s(self, client, seed_admin):
        """/delete_shift/<int:sid> requires the id in the URL -- a bare
        POST with no id segment at all never matches the route."""
        _admin_session(client, seed_admin)
        rv = client.post("/delete_shift", data={"shift_id": ""})
        assert rv.status_code == 404

    def test_deletes_shift_and_clears_employee_assignments(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO shifts (name, start_time, half_time, end_time) "
            "VALUES ('Temp Delete Shift','10:00','14:00','19:00') RETURNING id"
        )
        sid = cur.fetchone()[0]
        cur.execute("UPDATE employees SET shift_id=%s WHERE employee_id=%s", (sid, seed_employee["employee_id"]))
        cur.close()

        _admin_session(client, seed_admin)
        rv = client.post(f"/delete_shift/{sid}")
        assert rv.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT id FROM shifts WHERE id=%s", (sid,))
        assert cur.fetchone() is None
        cur.execute("SELECT shift_id FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] is None
        cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# employee_attendance_detail — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmployeeAttendanceDetailEdgeCases:

    def test_unknown_employee_id_returns_404(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/employee_attendance_detail/NOTEXIST/2025/3")
        assert rv.status_code == 404

    def test_valid_employee_returns_200_with_name(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.get(f"/employee_attendance_detail/{seed_employee['employee_id']}/2025/3")
        assert rv.status_code == 200
        assert seed_employee["name"].encode() in rv.data

    def test_month_name_in_response(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.get(f"/employee_attendance_detail/{seed_employee['employee_id']}/2025/6")
        assert rv.status_code == 200
        assert b"June 2025" in rv.data


# ═══════════════════════════════════════════════════════════════════════════════
# update_default_shift — missing fields guard and known NameError bug
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateDefaultShift:

    def test_missing_fields_redirects_with_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/update_default_shift", data={
            "shift_start": "", "shift_half": "", "shift_end": "",
        })
        assert rv.status_code == 302
        assert "error" in rv.headers["Location"]

    # The NameError bug this class used to regression-test (load_default_shift
    # referenced without being imported) doesn't apply post-merge: HEAD's
    # attendance.py uses module-qualified cfg.load_default_shift() throughout
    # (import utils.config as cfg), so there's no bare name to go missing.

    def test_valid_fields_with_mocked_load_fn_redirects(self, client, seed_admin, mocker):
        """When load_default_shift is mocked (bug fixed), the route redirects."""
        import blueprints.attendance as att_mod
        mocker.patch.object(att_mod, "load_default_shift", create=True, return_value=None)
        _admin_session(client, seed_admin)
        rv = client.post("/update_default_shift", data={
            "shift_start": "09:00",
            "shift_half":  "13:00",
            "shift_end":   "18:00",
        })
        assert rv.status_code == 302
        assert "default_saved=1" in rv.headers["Location"]
