"""BUG-30: every check-in path classifies against the employee's assigned shift.

The login/logout classification was copy-pasted into five endpoints and
only the web kiosk (/attendance) looked up the employee's assigned shift;
the mobile, device and offline-sync paths always used the company default.
The same employee was stored as "Full Day" or "Half Day" depending on which
device they used -- and payroll reads those marks.

The employee here works an early shift (06:00 / half 07:00 / end 15:00);
the company default stays 09:00 / 13:00 / 18:00. Punching in at 08:00 is a
half-day login on the early shift (a full-day login on the default), and
punching out at 15:30 completes the early shift (an early logout on the
default).
"""
import datetime
import time

import pytest
import pytz

from tests.conftest import TEST_OFFICE

EMP = "TST001"
OFFICE = TEST_OFFICE   # location is enforced by default (SEC-08)
TODAY = datetime.date.today()
AUTH_QR_FP = {"qr_enabled": True, "face_enabled": False,
              "fingerprint_enabled": True, "location_enabled": False}
AUTH_QR = {"qr_enabled": True, "face_enabled": False,
           "fingerprint_enabled": False, "location_enabled": False}


def _ist(hour, minute):
    tz = pytz.timezone("Asia/Kolkata")
    return tz.localize(datetime.datetime.combine(TODAY, datetime.time(hour, minute)))


@pytest.fixture
def early_shift(db_engine, seed_employee):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO shifts (name, start_time, half_time, end_time) "
                "VALUES ('Early QA', '06:00', '07:00', '15:00') RETURNING id")
    shift_id = cur.fetchone()[0]
    cur.execute("UPDATE employees SET shift_id=%s WHERE employee_id=%s", (shift_id, EMP))
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s", (EMP, TODAY))
    yield
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s", (EMP, TODAY))
    cur.execute("DELETE FROM overtime_records WHERE employee_id=%s AND date=%s", (EMP, TODAY))
    cur.execute("UPDATE employees SET shift_id=NULL WHERE employee_id=%s", (EMP,))
    cur.execute("DELETE FROM shifts WHERE id=%s", (shift_id,))
    cur.close()


@pytest.fixture
def logged_in_at_six(db_engine, early_shift):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO attendance (employee_id, date, login_time, status) "
                "VALUES (%s, %s, '06:00:00', 'Full Day Login')", (EMP, TODAY))
    cur.close()


def _at(mocker, module, hour, minute):
    mocker.patch(f"{module}.company_now", return_value=_ist(hour, minute))


def _emp_token(client, seed_employee):
    return client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    }).get_json()["token"]


def _admin_token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    }).get_json()["token"]


# ── one call per endpoint; each returns the response JSON ───────────────────

def _employee_checkin(client, seed_employee, mocker, hour, minute):
    _at(mocker, "blueprints.employee_portal", hour, minute)
    token = _emp_token(client, seed_employee)
    return client.post("/api/employee/checkin", json=OFFICE,
                       headers={"Authorization": f"Bearer {token}"}).get_json()


def _offline_sync(client, seed_employee, mocker, hour, minute):
    _at(mocker, "blueprints.employee_portal", hour, minute + 5)
    token = _emp_token(client, seed_employee)
    data = client.post("/api/employee/sync_punches", json={"punches": [
        {"id": "p1", "punched_at": _ist(hour, minute).isoformat(), **OFFICE},
    ]}, headers={"Authorization": f"Bearer {token}"}).get_json()
    assert data["ok"] is True, data
    return data["results"][0]


def _qr_fingerprint_checkin(client, seed_employee, mocker, hour, minute, signed_qr):
    _at(mocker, "blueprints.employee_portal", hour, minute)
    mocker.patch("blueprints.employee_portal.get_auth_config", return_value=AUTH_QR_FP)
    with client.session_transaction() as sess:
        sess["wa_fp_verified_emp_id"] = EMP
        sess["wa_fp_verified_at"] = time.time()
    return client.post("/api/employee/qr-face-checkin", data={
        "employee_id": signed_qr(EMP), "auth_combo": "qr_fingerprint",
    }, content_type="multipart/form-data").get_json()


def _device_checkin(client, seed_admin, mocker, hour, minute):
    _at(mocker, "blueprints.attendance", hour, minute)
    token = _admin_token(client, seed_admin)
    return client.post("/api/attendance/checkin", json={"employee_id": EMP},
                       headers={"Authorization": f"Bearer {token}"}).get_json()


def _kiosk_checkin(client, mocker, hour, minute, signed_qr):
    _at(mocker, "blueprints.attendance", hour, minute)
    mocker.patch("blueprints.attendance.get_auth_config", return_value=AUTH_QR)
    return client.post("/attendance", json={
        "employee_id": signed_qr(EMP), "auth_combo": "qr_only",
    }).get_json()


# ── login at 08:00 ───────────────────────────────────────────────────────────

def test_employee_app_login_uses_assigned_shift(client, seed_employee, early_shift, mocker):
    assert _employee_checkin(client, seed_employee, mocker, 8, 0)["status"] == "Half Day Login"


def test_offline_sync_login_uses_assigned_shift(client, seed_employee, early_shift, mocker):
    assert _offline_sync(client, seed_employee, mocker, 8, 0)["status"] == "Half Day Login"


def test_qr_fingerprint_login_uses_assigned_shift(client, seed_employee, early_shift, mocker, signed_qr):
    assert _qr_fingerprint_checkin(client, seed_employee, mocker, 8, 0, signed_qr)["status"] == "Half Day Login"


def test_device_api_login_uses_assigned_shift(client, seed_admin, seed_employee, early_shift, mocker):
    assert _device_checkin(client, seed_admin, mocker, 8, 0)["status"] == "Half Day Login"


def test_kiosk_login_uses_assigned_shift(client, seed_employee, early_shift, mocker, signed_qr):
    assert _kiosk_checkin(client, mocker, 8, 0, signed_qr)["status"] == "Half Day Login"


# ── logout at 15:30 after logging in at 06:00 ────────────────────────────────

def test_employee_app_logout_uses_assigned_shift(client, seed_employee, logged_in_at_six, mocker):
    data = _employee_checkin(client, seed_employee, mocker, 15, 30)
    assert (data["action"], data["status"]) == ("logout", "Completed")


def test_offline_sync_logout_uses_assigned_shift(client, seed_employee, logged_in_at_six, mocker):
    data = _offline_sync(client, seed_employee, mocker, 15, 30)
    assert (data["action"], data["status"]) == ("logout", "Completed")


def test_qr_fingerprint_logout_uses_assigned_shift(client, seed_employee, logged_in_at_six, mocker, signed_qr):
    data = _qr_fingerprint_checkin(client, seed_employee, mocker, 15, 30, signed_qr)
    assert (data["action"], data["status"]) == ("logout", "Completed")


def test_device_api_logout_uses_assigned_shift(client, seed_admin, seed_employee, logged_in_at_six, mocker):
    data = _device_checkin(client, seed_admin, mocker, 15, 30)
    assert (data["type"], data["status"]) == ("logout", "Completed")


def test_kiosk_logout_uses_assigned_shift(client, seed_employee, logged_in_at_six, mocker, signed_qr):
    data = _kiosk_checkin(client, mocker, 15, 30, signed_qr)
    assert (data["type"], data["status"]) == ("logout", "Completed")


# ── the shared classifiers ───────────────────────────────────────────────────

@pytest.mark.parametrize("punch, grace, expected", [
    (datetime.time(6, 15), 15, "Full Day Login"),   # last minute of grace
    (datetime.time(6, 16), 15, "Late Login"),
    (datetime.time(7, 0), 15, "Late Login"),        # half-time itself is still late
    (datetime.time(7, 1), 15, "Half Day Login"),
    (datetime.time(6, 1), 0, "Late Login"),
])
def test_classify_login(punch, grace, expected, mocker):
    import utils.attendance_utils as au
    mocker.patch.object(au.cfg, "shift_rules",
                        return_value=au.cfg.ShiftRules(datetime.time(9), datetime.time(13), datetime.time(18), grace))
    assert au.classify_login(punch, datetime.time(6, 0), datetime.time(7, 0)) == expected


@pytest.mark.parametrize("punch, expected", [
    (datetime.time(6, 59), "Half Day Logout"),
    (datetime.time(7, 0), "Early Logout"),
    (datetime.time(14, 59), "Early Logout"),
    (datetime.time(15, 0), "Completed"),
])
def test_classify_logout(punch, expected):
    from utils.attendance_utils import classify_logout
    assert classify_logout(punch, datetime.time(7, 0), datetime.time(15, 0)) == expected
