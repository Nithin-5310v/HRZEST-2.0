# -*- coding: utf-8 -*-
"""The admin-side app APIs changed with the mobile review (Nov 2026):

- GET/POST /api/bulk_mark_attendance: the day's records with status and
  HH:MM times (the app's Today list and Mark Attendance pre-fill them, so a
  marked day can be updated); no times stored for Absent/Leave/Holiday/Week
  Off; every manual mark audited under the account that made it.
- GET /api/employees: the whole staff list in one call (500 a page),
  contact details decrypted, and whether each employee has a photo.
- POST /api/employees/<id>/photo: an admin setting another employee's
  photo. The app used /api/employee/photo, which changes the token's OWN
  record -- for an owner admin with a linked profile, their own photo.
"""
import datetime
import io

import pytest
from PIL import Image

from utils.helpers.crypto import encrypt_contact

DAY = "2026-11-10"


def _token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"]}).get_json()["token"]


def _auth(client, seed_admin):
    return {"Authorization": f"Bearer {_token(client, seed_admin)}"}


@pytest.fixture
def clean_day(db_engine, seed_employee):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s", (seed_employee["employee_id"], DAY))
    yield seed_employee
    cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s", (seed_employee["employee_id"], DAY))
    cur.close()  # audit_logs is append-only: tests read only rows newer than _audit_mark()


def _audit_mark(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM audit_logs")
    mark = cur.fetchone()[0]
    cur.close()
    return mark


def _row(employees, emp_id):
    return next(e for e in employees if e["employee_id"] == emp_id)


class TestBulkMarkAttendance:
    def test_day_lists_saved_records_with_status_and_hhmm_times(self, client, db_engine, seed_admin, clean_day):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO attendance (employee_id, date, login_time, logout_time, status) "
                    "VALUES (%s,%s,'09:41:07.123456','18:12:00','Late Login')", (clean_day["employee_id"], DAY))
        cur.close()
        body = client.get(f"/api/bulk_mark_attendance?date={DAY}", headers=_auth(client, seed_admin)).get_json()
        row = _row(body["employees"], clean_day["employee_id"])
        assert (row["login_time"], row["logout_time"], row["status"], row["marked"]) == ("09:41", "18:12", "Late Login", True)

    def test_unmarked_employee_says_so(self, client, seed_admin, clean_day):
        body = client.get(f"/api/bulk_mark_attendance?date={DAY}", headers=_auth(client, seed_admin)).get_json()
        row = _row(body["employees"], clean_day["employee_id"])
        assert row["marked"] is False and row["login_time"] is None and row["status"] is None

    def test_update_overwrites_and_is_audited_as_an_update(self, client, db_engine, seed_admin, clean_day):
        auth = _auth(client, seed_admin)
        emp = clean_day["employee_id"]
        since = _audit_mark(db_engine)
        client.post("/api/bulk_mark_attendance", headers=auth, json={"date": DAY, "records": [
            {"emp_id": emp, "attendance_type": "Full Day", "login_time": "09:00", "logout_time": "18:00"}]})
        resp = client.post("/api/bulk_mark_attendance", headers=auth, json={"date": DAY, "records": [
            {"emp_id": emp, "attendance_type": "Half Day", "login_time": "09:00:00", "logout_time": "13:30"}]})
        assert resp.get_json()["saved"] == 1
        cur = db_engine.cursor()
        cur.execute("SELECT attendance_type, login_time, logout_time FROM attendance WHERE employee_id=%s AND date=%s",
                    (emp, DAY))
        assert cur.fetchone() == ("Half Day", datetime.time(9, 0), datetime.time(13, 30))
        cur.execute("SELECT actor, action FROM audit_logs WHERE target_table='attendance' AND target_id=%s "
                    "AND id > %s ORDER BY id", (emp, since))
        assert cur.fetchall() == [(seed_admin["username"], "mark_attendance"),
                                  (seed_admin["username"], "update_attendance")]
        cur.close()

    @pytest.mark.parametrize("att_type", ["Absent", "Leave", "Holiday", "Week Off"])
    def test_no_times_for_days_nobody_worked(self, client, db_engine, seed_admin, clean_day, att_type):
        client.post("/api/bulk_mark_attendance", headers=_auth(client, seed_admin), json={"date": DAY, "records": [
            {"emp_id": clean_day["employee_id"], "attendance_type": att_type,
             "login_time": "09:00", "logout_time": "18:00"}]})
        cur = db_engine.cursor()
        cur.execute("SELECT login_time, logout_time FROM attendance WHERE employee_id=%s AND date=%s",
                    (clean_day["employee_id"], DAY))
        assert cur.fetchone() == (None, None)
        cur.close()

    def test_bad_times_are_dropped_not_stored(self, client, db_engine, seed_admin, clean_day):
        client.post("/api/bulk_mark_attendance", headers=_auth(client, seed_admin), json={"date": DAY, "records": [
            {"emp_id": clean_day["employee_id"], "attendance_type": "Full Day",
             "login_time": "25:99", "logout_time": "soon"}]})
        cur = db_engine.cursor()
        cur.execute("SELECT login_time, logout_time FROM attendance WHERE employee_id=%s AND date=%s",
                    (clean_day["employee_id"], DAY))
        assert cur.fetchone() == (None, None)
        cur.close()

    def test_admin_editing_their_own_profile_is_flagged(self, client, db_engine, seed_admin, clean_day):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET linked_employee_id=%s WHERE username=%s",
                    (clean_day["employee_id"], seed_admin["username"]))
        since = _audit_mark(db_engine)
        try:
            client.post("/api/bulk_mark_attendance", headers=_auth(client, seed_admin), json={"date": DAY, "records": [
                {"emp_id": clean_day["employee_id"], "attendance_type": "Full Day", "login_time": "09:00"}]})
            cur.execute("SELECT action FROM audit_logs WHERE target_table='attendance' AND target_id=%s AND id > %s",
                        (clean_day["employee_id"], since))
            assert cur.fetchone()[0] == "mark_attendance_self"
        finally:
            cur.execute("UPDATE admin_users SET linked_employee_id=NULL WHERE username=%s", (seed_admin["username"],))
            cur.close()


class TestEmployeesList:
    def test_contact_details_are_decrypted_and_photo_flag_present(self, client, db_engine, seed_admin, seed_employee):
        enc_email, bidx = encrypt_contact("email", "real.person@example.com")
        enc_phone, pbidx = encrypt_contact("phone", "9876501234")
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=%s, email_bidx=%s, phone=%s, phone_bidx=%s, face_image=NULL "
                    "WHERE employee_id=%s", (enc_email, bidx, enc_phone, pbidx, seed_employee["employee_id"]))
        cur.close()
        body = client.get("/api/employees?per_page=500", headers=_auth(client, seed_admin)).get_json()
        row = _row(body["employees"], seed_employee["employee_id"])
        assert (row["email"], row["phone"], row["has_photo"]) == ("real.person@example.com", "9876501234", False)

    def test_up_to_500_a_page(self, client, seed_admin, seed_employee):
        body = client.get("/api/employees?per_page=5000", headers=_auth(client, seed_admin)).get_json()
        assert body["per_page"] == 500


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (210, 180, 160)).save(buf, "JPEG")
    buf.seek(0)
    return buf


class TestEmployeePhotoForAdmins:
    def test_sets_that_employees_photo_not_the_admins(self, client, db_engine, seed_admin, seed_employee, monkeypatch):
        import blueprints.employees.api as api
        monkeypatch.setattr(api, "_face_recognition_available", False)
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET face_image=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
        resp = client.post(f"/api/employees/{seed_employee['employee_id']}/photo",
                           data={"photo": (_jpeg(), "p.jpg")}, content_type="multipart/form-data",
                           headers=_auth(client, seed_admin))
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        cur.execute("SELECT face_image FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0]
        cur.close()

    def test_no_face_keeps_the_photo_on_file(self, client, db_engine, seed_admin, seed_employee, monkeypatch):
        import blueprints.employees.api as api
        monkeypatch.setattr(api, "_face_recognition_available", True)
        monkeypatch.setattr(api.face_recognition, "face_locations", lambda img: [], raising=False)
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET face_image='kept.jpg' WHERE employee_id=%s", (seed_employee["employee_id"],))
        resp = client.post(f"/api/employees/{seed_employee['employee_id']}/photo",
                           data={"photo": (_jpeg(), "p.jpg")}, content_type="multipart/form-data",
                           headers=_auth(client, seed_admin))
        assert resp.status_code == 400
        cur.execute("SELECT face_image FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == "kept.jpg"
        cur.close()

    def test_unknown_employee_404(self, client, seed_admin, monkeypatch):
        import blueprints.employees.api as api
        monkeypatch.setattr(api, "_face_recognition_available", False)
        resp = client.post("/api/employees/NOPE999/photo", data={"photo": (_jpeg(), "p.jpg")},
                           content_type="multipart/form-data", headers=_auth(client, seed_admin))
        assert resp.status_code == 404

    def test_needs_an_admin_token(self, client, seed_employee):
        resp = client.post(f"/api/employees/{seed_employee['employee_id']}/photo",
                           data={"photo": (_jpeg(), "p.jpg")}, content_type="multipart/form-data")
        assert resp.status_code == 401
