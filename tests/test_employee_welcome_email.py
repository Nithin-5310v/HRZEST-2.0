"""New-employee welcome emails link the recipient somewhere useful rather
than listing bare credentials with nowhere to use them. Covers all 3 live
employee-creation entry points: /add_employee_page, the Bearer-token
/api/employees, and the app's /api/employee/signup. (A 4th used to
exist -- POST /admin_action action="register" -- but that branch was
removed from blueprints/employees.py; see tests/test_employee_registration.py's
docstring. /add_employee_page's coverage below is the same welcome-email
behavior, just through the route that actually creates employees now.)

Every path sends the same welcome (blueprints/employees.py's
_send_welcome_credentials_email), as the owner asked on 3 Nov 2026: the
sign-in link, the Employee ID, the temporary password the account was made
with, and a one-time "set your password" link
(/employee_reset_password/<token>). /add_employee_page and /api/employees
make the employee change that password at first sign-in, so their email
says so; /api/employee/signup takes the password the admin typed in the
app and doesn't.

get_email_config()/send_email_smtp() are monkeypatched on
blueprints.employees (where the shared welcome lives) so these tests don't
depend on real email being configured.
"""
import io
import os
from PIL import Image

import utils.face_utils as face_utils
from extensions import app as flask_app
from utils.tenant_files import face_photo_path

UPLOAD_FOLDER = flask_app.config["UPLOAD_FOLDER"]
QR_FOLDER = os.path.join(flask_app.root_path, "private_uploads", "qrcodes")


def _fake_jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(80, 90, 100)).save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


def _mock_face_detected(monkeypatch):
    monkeypatch.setattr(face_utils.face_recognition, "load_image_file", lambda p: "img")
    monkeypatch.setattr(face_utils.face_recognition, "face_encodings", lambda img: ["enc"])


def _admin_session(client, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"], "password": seed_admin["password"],
    }, follow_redirects=True)
    assert resp.status_code == 200


def _cleanup_employee(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM leave_balances WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
    cur.close()
    for path in (os.path.join(UPLOAD_FOLDER, emp_id + ".jpg"), face_photo_path(emp_id, "public"),
                 os.path.join(QR_FOLDER, emp_id + ".png")):
        if os.path.exists(path):
            os.remove(path)


class TestEmployeeLoginUrlHelper:
    def test_no_session_falls_back_to_bare_login(self, client):
        with flask_app.test_request_context("/"):
            from utils.helpers import employee_login_url
            url = employee_login_url()
            assert url.endswith("/login")
            assert url.startswith("http")

    def test_tenant_slug_in_session_is_included(self, client):
        with flask_app.test_request_context("/"):
            from flask import session
            session["tenant_slug"] = "acme"
            from utils.helpers import employee_login_url
            url = employee_login_url()
            assert url.endswith("/acme/login")


class TestAddEmployeePageWelcomeEmail:
    def test_add_employee_sends_email_with_set_password_link(self, client, seed_admin, db_engine, monkeypatch):
        _mock_face_detected(monkeypatch)
        import blueprints.employees.registration as employees_module   # welcome email lives here
        monkeypatch.setattr(employees_module, "get_email_config", lambda: {"host": "smtp.test"})
        sent = []
        monkeypatch.setattr(employees_module, "send_email_smtp",
                            lambda to, subject, html, cfg: sent.append((to, subject, html)))

        emp_id = "WELMAIL002"
        try:
            _admin_session(client, seed_admin)
            resp = client.post("/add_employee_page", data={
                "name": "Welcome Mail Test 2", "emp_id": emp_id,
                "email": "welmail002@test.local", "role": "Developer",
                "face": (io.BytesIO(_fake_jpeg_bytes()), "face.jpg"),
            }, follow_redirects=True)
            assert resp.status_code == 200

            assert len(sent) == 1
            to, subject, html = sent[0]
            assert to == "welmail002@test.local" and "sign-in details" in subject
            assert "/employee_reset_password/" in html and "/login" in html
            assert emp_id in html and "Temporary password" in html
            assert "first time you sign in" in html  # this account must change it
        finally:
            _cleanup_employee(db_engine, emp_id)


class TestApiRegisterEmployeeWelcomeEmail:
    def test_api_register_sends_email_with_set_password_link(self, client, seed_admin, db_engine, monkeypatch):
        _mock_face_detected(monkeypatch)
        import blueprints.employees.registration as employees_module   # welcome email lives here
        monkeypatch.setattr(employees_module, "get_email_config", lambda: {"host": "smtp.test"})
        sent = []
        monkeypatch.setattr(employees_module, "send_email_smtp",
                            lambda to, subject, html, cfg: sent.append((to, subject, html)))

        emp_id = "WELMAIL003"
        try:
            resp = client.post("/api/login", json={
                "username": seed_admin["username"], "password": seed_admin["password"],
            })
            token = resp.get_json()["token"]
            resp = client.post(
                "/api/employees",
                data={
                    "name": "Welcome Mail Test 3", "emp_id": emp_id,
                    "email": "welmail003@test.local",
                    "face": (io.BytesIO(_fake_jpeg_bytes()), "face.jpg"),
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

            assert len(sent) == 1
            to, subject, html = sent[0]
            assert to == "welmail003@test.local"
            assert "/employee_reset_password/" in html and "/login" in html
            # No password given: the account's temporary password is the Employee ID.
            assert html.count(emp_id) >= 2 and "Temporary password" in html
        finally:
            _cleanup_employee(db_engine, emp_id)


class TestApiEmployeeSignupWelcomeEmail:
    def test_signup_sends_the_same_welcome_with_the_typed_password(self, client, seed_admin, db_engine, monkeypatch):
        import blueprints.employees.registration as employees_module   # welcome email lives here
        monkeypatch.setattr(employees_module, "get_email_config", lambda: {"host": "smtp.test"})
        sent = []
        monkeypatch.setattr(employees_module, "send_email_smtp",
                            lambda to, subject, html, cfg: sent.append((to, subject, html)))

        emp_id = "WELMAIL004"
        try:
            token = client.post("/api/login", json={
                "username": seed_admin["username"], "password": seed_admin["password"],
            }).get_json()["token"]
            resp = client.post("/api/employee/signup", json={
                "employee_id": emp_id, "name": "Welcome Mail Test 4",
                "email": "welmail004@test.local", "password": "SelfChosen@1",
            }, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200

            assert len(sent) == 1
            to, subject, html = sent[0]
            assert to == "welmail004@test.local"
            assert "/login" in html and "/employee_reset_password/" in html
            assert "SelfChosen@1" in html  # the owner wants the credentials in the email
            assert "first time you sign in" not in html  # this account isn't forced to change it
        finally:
            _cleanup_employee(db_engine, emp_id)

    def test_signup_needs_an_admin_token(self, client, db_engine):
        """Open, this let anyone create an employee in any company -- with
        role "HR", an admin-panel login (auth.py's _finish_employee_login)."""
        emp_id = "WELMAIL005"
        try:
            resp = client.post("/api/employee/signup", json={
                "employee_id": emp_id, "name": "Not Staff", "role": "HR",
                "email": "welmail005@test.local", "password": "SelfChosen@1",
            })
            assert resp.status_code == 401
            cur = db_engine.cursor()
            cur.execute("SELECT 1 FROM employees WHERE employee_id=%s", (emp_id,))
            assert cur.fetchone() is None
        finally:
            _cleanup_employee(db_engine, emp_id)


class TestWelcomeEmailEscaping:
    def test_name_and_password_are_escaped(self, client, seed_employee, monkeypatch):
        import blueprints.employees.registration as employees_module   # welcome email lives here
        monkeypatch.setattr(employees_module, "get_email_config", lambda: {"host": "smtp.test"})
        sent = []
        monkeypatch.setattr(employees_module, "send_email_smtp",
                            lambda to, subject, html, cfg: sent.append(html))
        with flask_app.test_request_context("/"):
            employees_module._send_welcome_credentials_email(
                seed_employee["employee_id"], "<b>Asha</b>", "asha@test.local", "p<w>&d")
        html, = sent
        assert "<b>Asha</b>" not in html and "&lt;b&gt;Asha&lt;/b&gt;" in html
        assert "p&lt;w&gt;&amp;d" in html
