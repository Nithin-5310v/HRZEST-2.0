"""
Company email-domain gate on employee registration.

utils/helpers.py's validate_employee_email_domain() enforces "new
employee's email must match the company's configured domain" -- but only
once a company has actually set one (company_settings.email_domain). This
covers both states: the default/no-domain-set schema (backward compatible
-- email stays optional, matching every pre-existing registration test)
and a domain explicitly configured.

Exercised through POST /add_employee_page (blueprints/employees.py) --
the actual, current employee-registration route. This used to go through
POST /admin_action (action="register"), which had its own registration
branch once; that branch was removed and the feature lives solely on
/add_employee_page now (see tests/test_employee_registration.py's
docstring for the full story).

Mutates company_settings.email_domain on the shared att_test default
schema for the "domain configured" tests -- always restored to NULL in a
finally block, since that column being unset is the baseline every other
test file's employee-registration tests assume.
"""
import io
import os
import datetime
from PIL import Image

import utils.face_utils as face_utils
from extensions import app as flask_app

UPLOAD_FOLDER = flask_app.config["UPLOAD_FOLDER"]
QR_FOLDER = os.path.join(flask_app.root_path, "private_uploads", "qrcodes")


def _fake_jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(120, 120, 120)).save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


def _mock_face_detected(monkeypatch):
    monkeypatch.setattr(face_utils.face_recognition, "load_image_file", lambda p: "img")
    monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: ["enc"])


def _admin_session(client, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"],
        "password": seed_admin["password"],
    }, follow_redirects=True)
    assert resp.status_code == 200


def _registration_payload(emp_id, email, **overrides):
    payload = {
        "name": "Domain Gate Test Employee",
        "emp_id": emp_id,
        "email": email,
        "role": "Developer",
        "date_of_joining": datetime.date.today().isoformat(),
        "work_mode": "office",
        "face": (io.BytesIO(_fake_jpeg_bytes()), "face.jpg"),
    }
    payload.update(overrides)
    return payload


def _cleanup_employee(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM leave_balances WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
    cur.close()
    for path in (os.path.join(UPLOAD_FOLDER, emp_id + ".jpg"),
                 os.path.join(QR_FOLDER, emp_id + ".png")):
        if os.path.exists(path):
            os.remove(path)


def _set_domain(db_engine, domain):
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET email_domain=%s WHERE id=1", (domain,))
    cur.close()
    from utils.helpers import invalidate_settings_cache
    invalidate_settings_cache()


class TestNoDomainConfigured:
    def test_registration_without_email_still_works(self, client, seed_admin, db_engine, monkeypatch):
        """Baseline every other registration test in the suite assumes:
        a company that never configured a domain keeps today's behavior."""
        _mock_face_detected(monkeypatch)
        emp_id = "DOMGATE001"
        _set_domain(db_engine, None)
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/add_employee_page", data=_registration_payload(emp_id, ""), follow_redirects=True)
            assert resp.status_code == 200

            cur = db_engine.cursor()
            cur.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone() is not None
            cur.close()
        finally:
            _cleanup_employee(db_engine, emp_id)
            _set_domain(db_engine, None)


class TestDomainConfigured:
    def test_mismatched_email_domain_rejected(self, client, seed_admin, db_engine, monkeypatch):
        _mock_face_detected(monkeypatch)
        emp_id = "DOMGATE002"
        _set_domain(db_engine, "acme.com")
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/add_employee_page", data=_registration_payload(emp_id, "someone@other.com"),
                               follow_redirects=True)
            assert resp.status_code == 200
            assert b"must be a @acme.com address" in resp.data

            cur = db_engine.cursor()
            cur.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone() is None, "employee with a non-matching domain must not be created"
            cur.close()
        finally:
            _cleanup_employee(db_engine, emp_id)
            _set_domain(db_engine, None)

    def test_missing_email_rejected_when_domain_required(self, client, seed_admin, db_engine, monkeypatch):
        _mock_face_detected(monkeypatch)
        emp_id = "DOMGATE003"
        _set_domain(db_engine, "acme.com")
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/add_employee_page", data=_registration_payload(emp_id, ""), follow_redirects=True)
            assert resp.status_code == 200
            assert b"Employee email is required" in resp.data

            cur = db_engine.cursor()
            cur.execute("SELECT employee_id FROM employees WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone() is None
            cur.close()
        finally:
            _cleanup_employee(db_engine, emp_id)
            _set_domain(db_engine, None)

    def test_matching_email_domain_accepted(self, client, seed_admin, db_engine, monkeypatch):
        _mock_face_detected(monkeypatch)
        emp_id = "DOMGATE004"
        _set_domain(db_engine, "acme.com")
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/add_employee_page", data=_registration_payload(emp_id, "new.hire@acme.com"),
                               follow_redirects=True)
            assert resp.status_code == 200

            cur = db_engine.cursor()
            cur.execute("SELECT email FROM employees WHERE employee_id=%s", (emp_id,))
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "new.hire@acme.com"
            cur.close()
        finally:
            _cleanup_employee(db_engine, emp_id)
            _set_domain(db_engine, None)
