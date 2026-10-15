"""Tests for /api/employee/sync_punches — offline punch batch submission,
including the geo-fence enforcement added to match the live check-in route
(see blueprints/employee_portal.py::api_employee_sync_punches).

Office-mode geofencing is opt-in per tenant now (utils/attendance_utils.py's
is_within_office_range) rather than always-on against a process-wide
OFFICE_LAT/LON env var -- tests that exercise the "outside office, rejected"
path need an actual office location configured, or is_within_office_range
is a no-op (see TestSyncPunchesGeofence's office_geofence_configured
fixture) and every coordinate is silently accepted."""
import datetime
import pytest
from tests.conftest import TEST_OFFICE


@pytest.fixture(autouse=True)
def _clean_attendance(seed_employee, db_engine):
    """Each test punches TST001 for 'today' — without this, an earlier
    test's login+logout leaks into the next test as a false 'day already
    complete' duplicate rejection."""
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
    db_engine.commit()
    cur.close()
    yield


@pytest.fixture
def office_geofence_configured(db_engine):
    """Sets a real office_lat/office_lon (the shared TEST_OFFICE point, so
    existing test coordinates keep meaning "at the office") so office-mode
    geofence rejection actually has something to reject against, restoring
    the unconfigured (NULL) default afterward."""
    from utils.helpers import invalidate_settings_cache
    cur = db_engine.cursor()
    cur.execute(
        "UPDATE company_settings SET location_enabled=1, office_lat=%s, office_lon=%s, geo_radius=300",
        (TEST_OFFICE["lat"], TEST_OFFICE["lon"]),
    )
    cur.close()
    invalidate_settings_cache()
    yield
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET office_lat=NULL, office_lon=NULL")
    cur.close()
    invalidate_settings_cache()


def _login(client, seed_employee):
    r = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"],
        "password": seed_employee["password"],
    })
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.get_json()['token']}"}


def _now_str():
    return datetime.datetime.now().isoformat()


class TestSyncPunchesGeofence:
    def test_office_mode_punch_outside_range_rejected(self, client, seed_employee, db_engine, office_geofence_configured):
        auth = _login(client, seed_employee)
        # Far from the default office coords (~hundreds of km away)
        r = client.post("/api/employee/sync_punches", headers=auth, json={
            "punches": [{"id": "1", "punched_at": _now_str(), "lat": 28.6139, "lon": 77.2090}]
        })
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert len(results) == 1
        assert results[0]["ok"] is False
        assert "office" in results[0]["msg"].lower()

    def test_office_mode_punch_inside_range_accepted(self, client, seed_employee, db_engine, office_geofence_configured):
        auth = _login(client, seed_employee)
        r = client.post("/api/employee/sync_punches", headers=auth, json={
            "punches": [{"id": "1", "punched_at": _now_str(),
                         **TEST_OFFICE}]
        })
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert len(results) == 1
        assert results[0]["ok"] is True

    def test_punch_without_coordinates_refused_when_location_enforced(self, client, seed_employee, db_engine):
        """No lat/lon sent at all while location is enforced (this class's
        fixture sets location_enabled=1) -- refused, like the live check-in
        route. It used to skip the geofence instead (QA SEC-08)."""
        auth = _login(client, seed_employee)
        r = client.post("/api/employee/sync_punches", headers=auth, json={
            "punches": [{"id": "1", "punched_at": _now_str()}]
        })
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert len(results) == 1
        assert results[0]["ok"] is False

    def test_wfh_employee_outside_home_location_rejected(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET work_mode='wfh', work_lat=%s, work_lon=%s WHERE employee_id=%s",
            (12.9716, 77.5946, seed_employee["employee_id"]),
        )
        db_engine.commit()
        cur.close()

        auth = _login(client, seed_employee)
        r = client.post("/api/employee/sync_punches", headers=auth, json={
            "punches": [{"id": "1", "punched_at": _now_str(), "lat": 28.6139, "lon": 77.2090}]
        })
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert results[0]["ok"] is False
        assert "home" in results[0]["msg"].lower()

    def test_wfh_employee_at_home_location_accepted(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "UPDATE employees SET work_mode='wfh', work_lat=%s, work_lon=%s WHERE employee_id=%s",
            (12.9716, 77.5946, seed_employee["employee_id"]),
        )
        db_engine.commit()
        cur.close()

        auth = _login(client, seed_employee)
        r = client.post("/api/employee/sync_punches", headers=auth, json={
            "punches": [{"id": "1", "punched_at": _now_str(), "lat": 12.9716, "lon": 77.5946}]
        })
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert results[0]["ok"] is True

    def test_invalid_lat_lon_values_rejected_not_crashed(self, client, seed_employee, db_engine):
        auth = _login(client, seed_employee)
        r = client.post("/api/employee/sync_punches", headers=auth, json={
            "punches": [{"id": "1", "punched_at": _now_str(), "lat": "not-a-number", "lon": "also-bad"}]
        })
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert results[0]["ok"] is False
        assert "invalid" in results[0]["msg"].lower()

    def test_mixed_batch_rejects_only_bad_punch(self, client, seed_employee, db_engine, office_geofence_configured):
        """One punch out of range shouldn't block the others in the same batch."""
        auth = _login(client, seed_employee)
        r = client.post("/api/employee/sync_punches", headers=auth, json={
            "punches": [
                {"id": "1", "punched_at": _now_str(), "lat": 28.6139, "lon": 77.2090},
            ]
        })
        assert r.status_code == 200
        results = r.get_json()["results"]
        assert len(results) == 1
        assert results[0]["id"] == "1"
        assert results[0]["ok"] is False
