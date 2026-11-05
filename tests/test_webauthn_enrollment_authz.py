"""Tests for the WebAuthn kiosk-enrollment identity gate (blueprints/auth.py).

Regression coverage for a real vulnerability: /webauthn/registration-options
and /api/employee/webauthn-register-kiosk had no authorization check at all —
anyone could enroll their own biometric against any employee_id (sourced
from an unverified QR scan or typed input) and then check in as that
employee indefinitely. Fixed via _wa_authorize_enrollment: only an admin
session, a matching employee session, or a fresh server-verified face match
(api_kiosk_enroll_face_verify) may obtain enrollment options for a given
employee_id.
"""
import io
import pytest
from PIL import Image
import utils.face_utils as face_utils


def _fake_jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(120, 120, 120)).save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


@pytest.fixture
def registered_face_file(tmp_path):
    p = tmp_path / "face.jpg"
    p.write_bytes(_fake_jpeg_bytes())
    return str(p)


@pytest.fixture
def seed_employee_with_face(seed_employee, db_engine, registered_face_file):
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET face_image=%s WHERE employee_id=%s",
                (registered_face_file, seed_employee["employee_id"]))
    db_engine.commit()
    cur.close()
    yield seed_employee


@pytest.fixture(autouse=True)
def _clear_face_cache():
    face_utils._face_enc_cache.clear()
    yield
    face_utils._face_enc_cache.clear()


def _mock_face_match(monkeypatch, result):
    monkeypatch.setattr(face_utils.face_recognition, "load_image_file", lambda p: "img")
    monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: ["enc"])
    monkeypatch.setattr(face_utils.face_recognition, "compare_faces",
                        lambda known, test, tolerance=0.5: [result])


class TestRegistrationOptionsAuthorization:
    def test_anonymous_request_is_rejected(self, client):
        resp = client.get("/webauthn/registration-options?emp_id=SOMEONE_ELSE")
        assert resp.status_code == 403

    def test_admin_session_can_enroll_any_employee(self, client, seed_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        resp = client.get("/webauthn/registration-options?emp_id=ANY_EMP_ID")
        assert resp.status_code == 200

    def test_manager_session_cannot_enroll_on_behalf_of_employee(self, client, seed_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "manager"
        resp = client.get("/webauthn/registration-options?emp_id=ANY_EMP_ID")
        assert resp.status_code == 403

    def test_employee_session_can_enroll_own_id(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get(f"/webauthn/registration-options?emp_id={seed_employee['employee_id']}")
        assert resp.status_code == 200

    def test_employee_session_cannot_enroll_someone_else(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/webauthn/registration-options?emp_id=SOMEONE_ELSE")
        assert resp.status_code == 403

    def test_internal_error_does_not_leak_exception_text(self, client, seed_admin, monkeypatch):
        """Finding #17 (Low): this route used to echo the raw exception
        string back to the client on any failure, inconsistent with
        every other route's error handling in this codebase (generic
        message to the client, full traceback server-side only)."""
        import blueprints.auth as auth_bp_module
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"

        def _raise(*a, **k):
            raise RuntimeError("some internal library detail nobody outside should see")

        monkeypatch.setattr(auth_bp_module.webauthn, "generate_registration_options", _raise)
        resp = client.get("/webauthn/registration-options?emp_id=ANY_EMP_ID")
        assert resp.status_code == 500
        assert b"internal library detail" not in resp.data
        assert resp.get_json()["error"] == "Could not start WebAuthn registration. Please try again."


class TestKioskFaceVerifyGate:
    def test_missing_fields_rejected(self, client):
        resp = client.post("/api/employee/kiosk-enroll-face-verify", data={})
        assert resp.status_code == 400

    def test_unknown_employee_rejected(self, client):
        resp = client.post("/api/employee/kiosk-enroll-face-verify", data={
            "employee_id": "NOPE_NOT_REAL",
            "face_photo": (io.BytesIO(_fake_jpeg_bytes()), "f.jpg"),
        })
        assert resp.status_code == 404

    def test_face_mismatch_rejected(self, client, seed_employee_with_face, monkeypatch):
        _mock_face_match(monkeypatch, False)
        resp = client.post("/api/employee/kiosk-enroll-face-verify", data={
            "employee_id": seed_employee_with_face["employee_id"],
            "face_photo": (io.BytesIO(_fake_jpeg_bytes()), "f.jpg"),
        })
        assert resp.status_code == 401

    def test_face_match_authorizes_registration_options(self, client, seed_employee_with_face, monkeypatch):
        _mock_face_match(monkeypatch, True)
        emp_id = seed_employee_with_face["employee_id"]

        verify_resp = client.post("/api/employee/kiosk-enroll-face-verify", data={
            "employee_id": emp_id,
            "face_photo": (io.BytesIO(_fake_jpeg_bytes()), "f.jpg"),
        })
        assert verify_resp.get_json()["ok"] is True

        opts_resp = client.get(f"/webauthn/registration-options?emp_id={emp_id}")
        assert opts_resp.status_code == 200

    def test_face_match_cannot_replace_an_existing_passkey(self, client, seed_employee_with_face,
                                                           db_engine, monkeypatch):
        """A kiosk face match may enroll a first passkey, never overwrite
        one -- that would let a fooled face check lock out the real
        employee and enroll the attacker's device."""
        _mock_face_match(monkeypatch, True)
        emp_id = seed_employee_with_face["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET fingerprint_credential_id='existing-cred' WHERE employee_id=%s", (emp_id,))
        cur.close()
        client.post("/api/employee/kiosk-enroll-face-verify", data={
            "employee_id": emp_id,
            "face_photo": (io.BytesIO(_fake_jpeg_bytes()), "f.jpg"),
        })
        resp = client.get(f"/webauthn/registration-options?emp_id={emp_id}")
        assert resp.status_code == 409

    def test_face_match_is_single_use(self, client, seed_employee_with_face, monkeypatch):
        _mock_face_match(monkeypatch, True)
        emp_id = seed_employee_with_face["employee_id"]

        client.post("/api/employee/kiosk-enroll-face-verify", data={
            "employee_id": emp_id,
            "face_photo": (io.BytesIO(_fake_jpeg_bytes()), "f.jpg"),
        })
        first = client.get(f"/webauthn/registration-options?emp_id={emp_id}")
        assert first.status_code == 200

        second = client.get(f"/webauthn/registration-options?emp_id={emp_id}")
        assert second.status_code == 403

    def test_face_match_does_not_authorize_a_different_employee_id(self, client, seed_employee_with_face, monkeypatch):
        _mock_face_match(monkeypatch, True)
        emp_id = seed_employee_with_face["employee_id"]

        client.post("/api/employee/kiosk-enroll-face-verify", data={
            "employee_id": emp_id,
            "face_photo": (io.BytesIO(_fake_jpeg_bytes()), "f.jpg"),
        })
        resp = client.get("/webauthn/registration-options?emp_id=SOMEONE_ELSE_ENTIRELY")
        assert resp.status_code == 403
