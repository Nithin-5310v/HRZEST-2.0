"""Security review 2026-11-20: mobile check-in could be marked without the
verification the company requires.

1. Offline sync (/api/employee/sync_punches) ignored the company's check-in
   policy, so a token holder could post an on-time punch with no QR or face.
2. /api/employee/qr-face-checkin let an employee's own token check in a
   different employee by scanning that person's badge ("buddy punching").
3. The same route skipped the geofence when the app sent no coordinates.
"""
import datetime

from utils.helpers import invalidate_settings_cache


def _employee_token(client, seed_employee):
    return client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    }).get_json()["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _punch():
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return {"id": "p1", "punched_at": now.isoformat().replace("+00:00", "Z")}


class TestOfflineSyncFollowsPolicy:
    def test_refused_when_company_requires_qr_or_face(self, client, seed_employee, db_engine):
        # The default test company has QR and face on and app_gps_checkin unset.
        cur = db_engine.cursor()
        cur.execute("UPDATE company_settings SET app_gps_checkin=NULL")
        cur.close()
        invalidate_settings_cache()
        token = _employee_token(client, seed_employee)
        resp = client.post("/api/employee/sync_punches", json={"punches": [_punch()]}, headers=_bearer(token))
        assert resp.status_code == 403
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_allowed_when_company_allows_location_only(self, client, seed_employee, app_gps_checkin_on):
        token = _employee_token(client, seed_employee)
        resp = client.post("/api/employee/sync_punches", json={"punches": [_punch()]}, headers=_bearer(token))
        assert resp.status_code == 200


class TestQrCheckinIdentityBinding:
    def test_employee_token_cannot_check_in_someone_else(self, client, seed_employee, signed_qr):
        token = _employee_token(client, seed_employee)
        resp = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr("SOMEONE2"), "auth_combo": "qr_face",
        }, headers=_bearer(token))
        assert resp.status_code == 403
        assert "own attendance" in resp.get_json()["msg"]

    def test_employee_token_with_own_qr_passes_the_binding(self, client, seed_employee, signed_qr):
        token = _employee_token(client, seed_employee)
        resp = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]), "auth_combo": "qr_face",
        }, headers=_bearer(token))
        assert "own attendance" not in (resp.get_json() or {}).get("msg", "")


class TestQrCheckinNeedsLocation:
    def test_missing_coordinates_refused_when_location_enforced(self, client, seed_employee, signed_qr,
                                                                monkeypatch):
        monkeypatch.setenv("MOBILE_REQUIRE_LOCATION", "true")
        token = _employee_token(client, seed_employee)
        resp = client.post("/api/employee/qr-face-checkin", data={
            "employee_id": signed_qr(seed_employee["employee_id"]), "auth_combo": "qr_face",
        }, headers=_bearer(token))
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "location_required"
