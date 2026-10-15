"""SEC-08: when the company enforces location, a check-in needs coordinates.

/api/employee/checkin and /api/employee/sync_punches ran the geofence only
`if lat and lon`, so a request that simply left the coordinates out was
accepted -- an employee could mark attendance from anywhere. The kiosk
(/attendance) already refuses that when location is enabled.
"""
import datetime

import pytest

import blueprints.employee_portal as ep
from tests.conftest import TEST_OFFICE
from utils.helpers import get_auth_config

# These tests exercise the location-only app check-in (QA NV-18).
pytestmark = pytest.mark.usefixtures("app_gps_checkin_on")

OFFICE = TEST_OFFICE


@pytest.fixture
def location(monkeypatch):
    def _set(enabled):
        real = get_auth_config()
        monkeypatch.setattr(ep, "get_auth_config", lambda: dict(real, location_enabled=enabled))
    return _set


@pytest.fixture
def emp_token(client, seed_employee, db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.close()
    return client.post("/api/employee/login", json={"employee_id": seed_employee["employee_id"],
                                                    "password": seed_employee["password"]}).get_json()["token"]


def _checkin(client, token, body):
    return client.post("/api/employee/checkin", json=body, headers={"Authorization": f"Bearer {token}"})


def _attendance_rows(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=%s", (emp_id,))
    n = cur.fetchone()[0]
    cur.close()
    return n


def test_checkin_without_coordinates_is_refused_when_location_is_enforced(client, emp_token, location, db_engine, seed_employee):
    location(True)
    resp = _checkin(client, emp_token, {})
    assert resp.status_code == 400 and resp.get_json()["ok"] is False
    assert _attendance_rows(db_engine, seed_employee["employee_id"]) == 0


def test_checkin_with_office_coordinates_is_accepted(client, emp_token, location):
    location(True)
    resp = _checkin(client, emp_token, OFFICE)
    assert resp.status_code == 200 and resp.get_json()["ok"] is True


def test_checkin_without_coordinates_is_fine_when_location_is_off(client, emp_token, location):
    location(False)
    assert _checkin(client, emp_token, {}).get_json()["ok"] is True


def test_offline_punch_without_coordinates_is_refused_when_location_is_enforced(client, emp_token, location, db_engine, seed_employee):
    location(True)
    punched = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
    data = client.post("/api/employee/sync_punches", json={"punches": [{"id": "p1", "punched_at": punched}]},
                       headers={"Authorization": f"Bearer {emp_token}"}).get_json()
    assert data["results"][0]["ok"] is False
    assert _attendance_rows(db_engine, seed_employee["employee_id"]) == 0
