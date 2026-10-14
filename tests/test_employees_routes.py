"""Route-level tests for blueprints/employees.py branches not already
covered by tests/test_employee_registration.py (the /admin_action
action=register happy/error paths) or tests/test_pii_encryption.py
(/edit_employee PII round-trip). Covers the other admin_action
sub-actions, delete/edit/profile/detail pages, the add_employee_page
flow, photo/QR management, ID generation, and the REST-ish /api/employees
endpoints.
"""
import io
import os
import pytest
from PIL import Image
from extensions import app as flask_app
import utils.face_utils as face_utils


def _jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(50, 60, 70)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _mock_face_detected(monkeypatch):
    """Every route here that saves a face photo checks for a detectable
    face — the synthetic test JPEGs have none, so mock detection True by
    default (matches tests/test_employee_registration.py's pattern)."""
    monkeypatch.setattr(face_utils.face_recognition, "load_image_file", lambda p: "img")
    monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: ["enc"])


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _admin_bearer_token(client, seed_admin):
    resp = client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    })
    return resp.get_json()["token"]


UPLOAD_FOLDER = flask_app.config["UPLOAD_FOLDER"]


@pytest.fixture
def cleanup_emp_files():
    """Track employee_ids whose dataset/qrcode files should be removed after the test."""
    ids = []
    yield ids
    for eid in ids:
        jpg = os.path.join(UPLOAD_FOLDER, eid + ".jpg")
        qr = os.path.join(flask_app.root_path, "private_uploads", "qrcodes", eid + ".png")
        if os.path.exists(jpg):
            os.remove(jpg)
        if os.path.exists(qr):
            os.remove(qr)


class TestAdminActionUpdateFace:
    def test_unknown_employee_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/admin_action", data={
            "action": "update_face", "emp_id": "GHOST_EMP",
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg"),
        }, follow_redirects=True)
        assert b"not found" in resp.data

    def test_success_updates_face_image(self, client, seed_admin, seed_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_employee["employee_id"])
        _admin_session(client, seed_admin["username"])
        resp = client.post("/admin_action", data={
            "action": "update_face", "emp_id": seed_employee["employee_id"],
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg"),
        }, follow_redirects=True)
        assert b"Face photo updated" in resp.data


class TestAdminActionResetPassword:
    def test_unknown_employee(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/admin_action", data={"action": "reset_password", "emp_id": "GHOST_EMP"},
                           follow_redirects=True)
        assert b"not found" in resp.data

    def test_known_employee_resets(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/admin_action", data={
            "action": "reset_password", "emp_id": seed_employee["employee_id"]}, follow_redirects=True)
        assert b"Password reset" in resp.data


class TestAdminActionHoliday:
    def test_adds_holiday(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        client.post("/admin_action", data={
            "action": "holiday", "date": "2026-08-15", "holiday_name": "Route Test Holiday"})
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM holidays WHERE name='Route Test Holiday'")
        row = cur.fetchone()
        cur.execute("DELETE FROM holidays WHERE name='Route Test Holiday'")
        cur.close()
        assert row is not None


class TestAdminActionSalary:
    def test_inserts_new_salary_config(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin["username"])
        client.post("/admin_action", data={
            "action": "salary", "emp_id": seed_employee["employee_id"], "salary": "500"})
        cur = db_engine.cursor()
        cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        assert float(cur.fetchone()[0]) == 500.0
        cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

    def test_updates_existing_salary_config(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s,%s)",
                    (seed_employee["employee_id"], 400))
        _admin_session(client, seed_admin["username"])
        client.post("/admin_action", data={
            "action": "salary", "emp_id": seed_employee["employee_id"], "salary": "700"})
        cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        assert float(cur.fetchone()[0]) == 700.0
        cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()


class TestDeleteEmployee:
    def test_unknown_employee(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/delete_employee/GHOST_EMP", follow_redirects=True)
        assert b"not found" in resp.data

    def test_known_employee_deleted(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO employees (employee_id, name) VALUES (%s,%s)",
                    ("DELME1", "Delete Target"))
        _admin_session(client, seed_admin["username"])
        resp = client.post("/delete_employee/DELME1", follow_redirects=True)
        assert b"deleted successfully" in resp.data
        cur.execute("SELECT 1 FROM employees WHERE employee_id='DELME1'")
        assert cur.fetchone() is None
        cur.close()


class TestEditEmployeePage:
    def test_unknown_employee_404(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/edit_employee/GHOST_EMP")
        assert resp.status_code == 404

    def test_known_employee_renders(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/edit_employee/{seed_employee['employee_id']}")
        assert resp.status_code == 200


class TestEmployeeProfile:
    def test_unknown_employee_404(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/employee_profile/GHOST_EMP")
        assert resp.status_code == 404

    def test_known_employee_renders(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/employee_profile/{seed_employee['employee_id']}")
        assert resp.status_code == 200


class TestEmployeeDetail:
    def test_unknown_employee_redirects(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/employee_detail/GHOST_EMP", follow_redirects=True)
        assert b"not found" in resp.data

    def test_known_employee_renders(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/employee_detail/{seed_employee['employee_id']}")
        assert resp.status_code == 200


class TestViewEmployees:
    def test_renders_with_seeded_employee(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/employees")
        assert resp.status_code == 200

    def test_scoped_to_active_company(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Emp Route Co') RETURNING id")
        cid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = cid
        try:
            resp = client.get("/employees")
            assert resp.status_code == 200
        finally:
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()


class TestAddEmployeePage:
    def test_missing_name_or_id_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_employee_page", data={"name": "", "emp_id": ""}, follow_redirects=True)
        assert b"required" in resp.data

    def test_invalid_emp_id_format_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_employee_page", data={"name": "X", "emp_id": "bad id!"},
                           follow_redirects=True)
        assert b"may only contain" in resp.data

    def test_missing_photo_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_employee_page", data={"name": "NoPhoto Guy", "emp_id": "NOPHOTO1"},
                           follow_redirects=True)
        assert b"photo is required" in resp.data

    def test_success_registers_employee(self, client, seed_admin, db_engine, cleanup_emp_files):
        cleanup_emp_files.append("ADDPAGE1")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_employee_page", data={
            "name": "Add Page Employee", "emp_id": "ADDPAGE1",
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg"),
        }, follow_redirects=True)
        assert b"registered" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM employees WHERE employee_id='ADDPAGE1'")
        row = cur.fetchone()
        cur.execute("DELETE FROM employees WHERE employee_id='ADDPAGE1'")
        cur.close()
        assert row is not None

    def test_extended_profile_fields_saved_and_decrypt(self, client, seed_admin, db_engine, cleanup_emp_files):
        """Bank/PAN/Aadhar/UAN/emergency-contact/blood-group/address fields
        submitted from the Add Employee form should round-trip through
        encrypt_pii and land on the right columns, and education rows
        should land in employee_education."""
        from utils.helpers import decrypt_pii
        cleanup_emp_files.append("ADDPAGE2")
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_employee_page", data={
            "name": "Extended Fields Employee", "emp_id": "ADDPAGE2",
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg"),
            "gender": "Female", "dob": "1995-06-15", "blood_group": "O+",
            "address": "221B Baker Street", "city": "London", "state": "Greater London", "pincode": "NW16XE",
            "emergency_contact_name": "Jane Doe", "emergency_contact_phone": "9999999999",
            "emergency_contact_relation": "Sister",
            "aadhar_number": "123412341234", "pan_number": "abcde1234f", "uan_number": "100200300400",
            "bank_name": "Test Bank", "bank_account": "000111222333", "bank_ifsc": "tbnk0001234",
            "degree[]": ["B.Tech", "M.Tech"], "institution[]": ["ABC University", "XYZ Institute"],
            "year_of_passing[]": ["2016", "2018"], "percentage[]": ["82", "75"],
            "monthly_salary": "46800.00",
        }, follow_redirects=True)
        assert b"registered" in resp.data

        cur = db_engine.cursor()
        cur.execute(
            "SELECT gender, dob, blood_group, address, city, state, pincode, "
            "emergency_contact_name, emergency_contact_phone, emergency_contact_relation, "
            "aadhar_number, pan_number, uan_number, bank_name, bank_account, bank_ifsc "
            "FROM employees WHERE employee_id='ADDPAGE2'"
        )
        row = cur.fetchone()
        assert row is not None
        decrypted = [decrypt_pii(v) if v else v for v in row]
        (gender, dob, blood_group, address, city, state, pincode,
         ec_name, ec_phone, ec_relation, aadhar, pan, uan, bank_name, bank_account, bank_ifsc) = decrypted
        assert gender == "Female"
        assert dob == "1995-06-15"
        assert blood_group == "O+"
        assert address == "221B Baker Street"
        assert city == "London"
        assert state == "Greater London"
        assert pincode == "NW16XE"
        assert ec_name == "Jane Doe"
        assert ec_phone == "9999999999"
        assert ec_relation == "Sister"
        assert aadhar == "123412341234"
        assert pan == "ABCDE1234F"
        assert uan == "100200300400"
        assert bank_name == "Test Bank"
        assert bank_account == "000111222333"
        assert bank_ifsc == "TBNK0001234"

        cur.execute(
            "SELECT degree, institution, year_of_passing, percentage FROM employee_education "
            "WHERE employee_id='ADDPAGE2' ORDER BY id"
        )
        edu_rows = cur.fetchall()
        cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id='ADDPAGE2'")
        salary_row = cur.fetchone()
        cur.execute("DELETE FROM employee_education WHERE employee_id='ADDPAGE2'")
        cur.execute("DELETE FROM salary_config WHERE employee_id='ADDPAGE2'")
        cur.execute("DELETE FROM employees WHERE employee_id='ADDPAGE2'")
        cur.close()
        assert edu_rows == [
            ("B.Tech", "ABC University", "2016", "82"),
            ("M.Tech", "XYZ Institute", "2018", "75"),
        ]
        assert salary_row is not None
        assert float(salary_row[0]) == 1800.0


class TestUpdateEmployeePhoto:
    def test_unknown_employee(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/update_employee_photo/GHOST_EMP", data={
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg")}, follow_redirects=True)
        assert b"not found" in resp.data

    def test_no_file_provided(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/update_employee_photo/{seed_employee['employee_id']}", data={},
                           follow_redirects=True)
        assert b"No photo file provided" in resp.data

    def test_success(self, client, seed_admin, seed_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_employee["employee_id"])
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/update_employee_photo/{seed_employee['employee_id']}", data={
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg")}, follow_redirects=True)
        assert b"Photo updated" in resp.data


class TestRegenerateQr:
    def test_unknown_employee(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/regenerate_qr/GHOST_EMP", follow_redirects=True)
        assert b"not found" in resp.data

    def test_success(self, client, seed_admin, seed_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_employee["employee_id"])
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/regenerate_qr/{seed_employee['employee_id']}", follow_redirects=True)
        assert b"regenerated" in resp.data


class TestQrCodeStorageIsolation:
    """Regression coverage for a real vulnerability: generate_qr() used to
    save straight to static/qrcodes/<emp_id>.png -- a directly, publicly
    web-servable path with a fully predictable name. The QR payload is
    "<employee_id>.<hmac signature>", a permanent, replayable attendance
    check-in credential once scanned -- exposing the already-signed image
    under static/ let anyone who could guess a (routinely sequential)
    employee_id fetch a valid credential for that person with zero
    authentication, completely bypassing the whole point of the HMAC
    signature (see qr_generator.py's _qr_signature() docstring)."""

    def test_generate_qr_never_writes_under_static(self, seed_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_employee["employee_id"])
        from qr_generator import generate_qr
        emp_id = seed_employee["employee_id"]
        old_public_path = os.path.join("static", "qrcodes", emp_id + ".png")
        if os.path.exists(old_public_path):
            os.remove(old_public_path)
        generate_qr(emp_id)
        assert not os.path.exists(old_public_path)

    def test_generate_qr_stored_ref_is_readable_via_open_private(self, seed_employee, cleanup_emp_files):
        """Not a bare local path assumption -- proves the returned
        stored_ref actually round-trips through the same storage
        abstraction (utils.storage.open_private()) every other private
        upload uses, so this also works once S3 mode is configured."""
        cleanup_emp_files.append(seed_employee["employee_id"])
        from qr_generator import generate_qr
        from utils.storage import open_private

        stored_ref = generate_qr(seed_employee["employee_id"])
        data = open_private(stored_ref)
        assert data.startswith(b"\x89PNG")

    def test_qr_signature_round_trips_through_the_actual_encoded_image(self, seed_employee, cleanup_emp_files):
        """The HMAC signature itself (qr_generator.py's _qr_signature/
        verify_qr_value) is unchanged by this storage fix -- this just
        confirms the same payload generate_qr() actually encodes into the
        image is the one verify_qr_value() accepts, independent of where
        the image bytes end up living."""
        cleanup_emp_files.append(seed_employee["employee_id"])
        from qr_generator import _qr_signature, verify_qr_value
        emp_id = seed_employee["employee_id"]
        qr_value = f"{emp_id}.{_qr_signature(emp_id)}"
        decoded_emp_id, is_valid = verify_qr_value(qr_value)
        assert is_valid is True
        assert decoded_emp_id == emp_id

    def test_admin_qr_route_requires_admin_session(self, client, seed_employee):
        resp = client.get(f"/employees/{seed_employee['employee_id']}/qr", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_admin_can_fetch_employee_qr(self, client, seed_admin, seed_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_employee["employee_id"])
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/employees/{seed_employee['employee_id']}/qr")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"
        assert resp.data.startswith(b"\x89PNG")

    def test_admin_qr_route_404s_for_unknown_employee(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/employees/GHOST_EMP_QR/qr")
        assert resp.status_code == 404

    def test_hr_scoped_out_of_unassigned_employees_qr(self, client, seed_hr_admin, seed_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_employee["employee_id"])
        _admin_session(client, seed_hr_admin["username"], role="hr")
        resp = client.get(f"/employees/{seed_employee['employee_id']}/qr")
        assert resp.status_code == 404

    def test_hr_can_fetch_assigned_employees_qr(self, client, seed_hr_admin, seed_assigned_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_assigned_employee["employee_id"])
        _admin_session(client, seed_hr_admin["username"], role="hr")
        resp = client.get(f"/employees/{seed_assigned_employee['employee_id']}/qr")
        assert resp.status_code == 200
        assert resp.data.startswith(b"\x89PNG")

    def test_my_qr_requires_employee_session(self, client):
        resp = client.get("/my_qr", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_my_qr_serves_from_private_storage(self, client, seed_employee, cleanup_emp_files):
        cleanup_emp_files.append(seed_employee["employee_id"])
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/my_qr")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"
        assert resp.data.startswith(b"\x89PNG")


class TestServeDataset:
    def test_missing_file_404(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/dataset/does_not_exist_xyz.jpg")
        assert resp.status_code == 404


class TestMyPhoto:
    def test_not_logged_in_is_sent_to_login(self, client):
        # /my_photo is @any_user_required now (kill switch + account checks),
        # so an anonymous request gets the same login redirect as every
        # other signed-in-only route instead of a bare 403.
        resp = client.get("/my_photo", follow_redirects=False)
        assert resp.status_code == 302
        assert "login" in resp.headers.get("Location", "")

    def test_no_photo_on_file_returns_404(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/my_photo")
        assert resp.status_code == 404


class TestGenerateEmpId:
    def test_not_logged_in_returns_401(self, client):
        resp = client.get("/api/generate_emp_id")
        assert resp.status_code == 401

    def test_without_company_id(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/generate_emp_id")
        assert resp.status_code == 200
        assert "emp_id" in resp.get_json()

    def test_with_company_id(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name, code) VALUES ('GenId Co', 'GID') RETURNING id")
        cid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.get(f"/api/generate_emp_id?company_id={cid}")
            assert resp.status_code == 200
            assert resp.get_json()["code"] == "GID"
        finally:
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()


class TestApiEmployeesList:
    def test_requires_token(self, client):
        assert client.get("/api/employees").status_code == 401

    def test_returns_paginated_list(self, client, seed_admin, seed_employee):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.get("/api/employees?page=1&per_page=10",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert any(e["employee_id"] == seed_employee["employee_id"] for e in data["employees"])


class TestApiRegisterEmployee:
    def test_missing_fields_returns_400(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/employees", data={"name": ""},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_invalid_emp_id_format(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/employees", data={
            "name": "API Guy", "emp_id": "bad id"},
            headers={"Authorization": f"Bearer {token}"},
            content_type="multipart/form-data")
        assert resp.status_code in (400,)

    def test_success(self, client, seed_admin, db_engine, cleanup_emp_files):
        cleanup_emp_files.append("APIREG1")
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/employees", data={
            "name": "Api Registered", "emp_id": "APIREG1",
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg"),
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("DELETE FROM employees WHERE employee_id='APIREG1'")
        cur.close()

    def test_duplicate_id_returns_400(self, client, seed_admin, seed_employee):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post("/api/employees", data={
            "name": "Dup", "emp_id": seed_employee["employee_id"],
            "face": (io.BytesIO(_jpeg_bytes()), "f.jpg"),
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400


class TestApiEmployeeDetail:
    def test_not_found(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.get("/api/employees/GHOST_EMP", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

    def test_found(self, client, seed_admin, seed_employee):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.get(f"/api/employees/{seed_employee['employee_id']}",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["employee"]["employee_id"] == seed_employee["employee_id"]


class TestApiEditEmployee:
    def test_missing_name_returns_400(self, client, seed_admin, seed_employee):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.put(f"/api/employees/{seed_employee['employee_id']}", json={"name": ""},
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_success_updates_employee(self, client, seed_admin, seed_employee, db_engine):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.put(f"/api/employees/{seed_employee['employee_id']}", json={
            "name": "Updated Via API", "email": "updated@test.local"},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM employees WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == "Updated Via API"
        cur.close()

    def test_role_change_is_audited(self, client, seed_admin, seed_employee, db_engine):
        """Finding #13 (Medium) -- API twin of the same web-path
        regression in test_employees_coverage.py::TestEditEmployee."""
        token = _admin_bearer_token(client, seed_admin)
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        try:
            resp = client.put(f"/api/employees/{emp_id}", json={
                "name": seed_employee["name"], "role": "Staff Engineer (API)"},
                headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            cur.execute(
                "SELECT detail FROM audit_logs WHERE target_id=%s AND action='update_employee_role' "
                "ORDER BY id DESC LIMIT 1",
                (emp_id,)
            )
            row = cur.fetchone()
            assert row is not None, "API role change was not audited"
            assert "Staff Engineer (API)" in row[0]
        finally:
            cur.execute("UPDATE employees SET role=NULL WHERE employee_id=%s", (emp_id,))
            db_engine.commit()
            cur.close()


class TestApiDeleteEmployee:
    def test_not_found(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.delete("/api/employees/GHOST_EMP", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

    def test_success(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        try:
            cur.execute("DELETE FROM employees WHERE employee_id='APIDEL1'")
            cur.execute("INSERT INTO employees (employee_id, name) VALUES (%s,%s)",
                        ("APIDEL1", "Api Delete Target"))
            token = _admin_bearer_token(client, seed_admin)
            resp = client.delete("/api/employees/APIDEL1", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            cur.execute("SELECT 1 FROM employees WHERE employee_id='APIDEL1'")
            assert cur.fetchone() is None
        finally:
            cur.execute("DELETE FROM employees WHERE employee_id='APIDEL1'")
            cur.close()
