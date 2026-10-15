"""NV-18: the mobile app's location-only check-in ignored the company's
check-in policy.

/api/employee/checkin marks attendance with only the employee's login
token (plus GPS). The kiosk and /api/employee/qr-face-checkin only accept
the QR/face/fingerprint combinations allowed_checkin_combos() permits, but
this endpoint never asked, so it skipped the face or QR check a company
requires.

Owner decision (QA_AUDIT.md §0.1): it follows the company policy. A
company setting (company_settings.app_gps_checkin) decides; left unset,
location-only check-in is allowed only when the company has no QR, face
or fingerprint method enabled. The current app's check-in buttons all go
through the QR/face scanner, so they are unaffected.
"""
import pytest

from tests.conftest import TEST_OFFICE
from tests.test_admin_views_routes import _admin_session
from utils.helpers import invalidate_settings_cache


@pytest.fixture
def emp_token(client, seed_employee, db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.close()
    return client.post("/api/employee/login", json={"employee_id": seed_employee["employee_id"],
                                                    "password": seed_employee["password"]}).get_json()["token"]


def _settings(db_engine, **cols):
    cur = db_engine.cursor()
    for col, val in cols.items():
        cur.execute(f"UPDATE company_settings SET {col}=%s", (val,))  # nosec B608 -- test column names
    cur.close()
    invalidate_settings_cache()


def _checkin(client, token):
    return client.post("/api/employee/checkin", json=TEST_OFFICE, headers={"Authorization": f"Bearer {token}"})


def _attendance_rows(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=%s", (emp_id,))
    n = cur.fetchone()[0]
    cur.close()
    return n


def test_refused_by_default_when_the_company_requires_qr_or_face(client, emp_token, db_engine, seed_employee):
    _settings(db_engine, app_gps_checkin=None, qr_enabled=1, face_enabled=1)
    resp = _checkin(client, emp_token)
    assert resp.status_code == 403
    assert resp.get_json()["ok"] is False
    assert _attendance_rows(db_engine, seed_employee["employee_id"]) == 0


def test_refused_by_default_when_the_company_requires_fingerprint(client, emp_token, db_engine):
    _settings(db_engine, app_gps_checkin=None, qr_enabled=0, face_enabled=0, fingerprint_enabled=1)
    assert _checkin(client, emp_token).status_code == 403


def test_allowed_when_the_admin_turns_it_on(client, emp_token, db_engine):
    _settings(db_engine, app_gps_checkin=1, qr_enabled=1, face_enabled=1)
    resp = _checkin(client, emp_token)
    assert resp.status_code == 200 and resp.get_json()["ok"] is True


def test_allowed_by_default_when_no_verified_method_is_enabled(client, emp_token, db_engine):
    _settings(db_engine, app_gps_checkin=None, qr_enabled=0, face_enabled=0, fingerprint_enabled=0)
    assert _checkin(client, emp_token).get_json()["ok"] is True


def test_admin_can_turn_it_off_explicitly(client, emp_token, db_engine):
    _settings(db_engine, app_gps_checkin=0, qr_enabled=0, face_enabled=0, fingerprint_enabled=0)
    assert _checkin(client, emp_token).status_code == 403


def test_settings_toggle_saves_the_choice(client, seed_admin, db_engine):
    _admin_session(client, seed_admin["username"])
    for enabled, expected in (("1", 1), ("0", 0)):
        resp = client.post("/toggle_auth_method", data={"method": "app_gps", "enabled": enabled})
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT app_gps_checkin FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == expected
        cur.close()


def test_settings_toggle_takes_effect_at_once(client, seed_admin, emp_token, db_engine):
    """The toggle used to leave the cached check-in policy in place, so a
    change only applied once the settings cache expired."""
    _settings(db_engine, app_gps_checkin=None, qr_enabled=1, face_enabled=1)
    assert _checkin(client, emp_token).status_code == 403  # policy now cached
    _admin_session(client, seed_admin["username"])
    client.post("/toggle_auth_method", data={"method": "app_gps", "enabled": "1"})
    assert _checkin(client, emp_token).status_code == 200
