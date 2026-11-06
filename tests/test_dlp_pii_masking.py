"""Tests for the DLP field-masking layer (utils/dlp.py) applied to
employee_detail/employee_profile (blueprints/employees.py) and the
/api/employees* role gap fix (utils/auth.py's api_role_required).

PII clearance is mapped onto the existing role model: admin_role=="admin"
sees unmasked aadhar/pan/bank_account/uan/salary; manager and soc_analyst
see the record with those fields masked — mirrors the restriction
payroll.py's view_payslip already enforces for payslips specifically."""
from utils.helpers import encrypt_amount as _pay  # pay columns only accept ciphertext
import pytest
from utils.helpers import encrypt_pii


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def pii_employee(db_engine):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO employees (employee_id, name, email) VALUES (%s,%s,%s) "
        "ON CONFLICT (employee_id) DO NOTHING",
        ("DLP001", "PII Test Employee", "dlp@test.local"),
    )
    cur.execute(
        "UPDATE employees SET aadhar_number=%s, pan_number=%s, bank_name=%s, "
        "bank_account=%s, bank_ifsc=%s, uan_number=%s WHERE employee_id=%s",
        (encrypt_pii("123456789012"), encrypt_pii("ABCDE1234F"), encrypt_pii("Test Bank"),
         encrypt_pii("000111222333"), encrypt_pii("TEST0001234"), encrypt_pii("UAN123456789"),
         "DLP001"),
    )
    cur.execute(
        "INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s, %s) "
        "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=EXCLUDED.salary_per_day",
        ("DLP001", _pay(2000.00)),
    )
    db_engine.commit()
    cur.close()
    yield "DLP001"
    cur = db_engine.cursor()
    cur.execute("DELETE FROM salary_config WHERE employee_id='DLP001'")
    cur.execute("DELETE FROM employees WHERE employee_id='DLP001'")
    db_engine.commit()
    cur.close()


@pytest.fixture
def soc_role_admin(seed_admin, db_engine):
    """Promotes the seeded admin to soc_analyst for the duration of the test."""
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
    db_engine.commit()
    cur.close()
    yield seed_admin
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
    db_engine.commit()
    cur.close()


class TestEmployeeDetailMasking:
    def test_admin_sees_unmasked_pii(self, client, seed_admin, pii_employee):
        _admin_session(client, seed_admin["username"], role="admin")
        resp = client.get(f"/employee_detail/{pii_employee}")
        assert resp.status_code == 200
        assert b"123456789012" in resp.data
        assert b"ABCDE1234F" in resp.data
        assert b"2000" in resp.data

    def test_manager_sees_masked_pii(self, client, seed_admin, db_engine, pii_employee):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='manager' WHERE username=%s", (seed_admin["username"],))
        db_engine.commit()
        cur.close()
        try:
            _admin_session(client, seed_admin["username"], role="manager")
            resp = client.get(f"/employee_detail/{pii_employee}")
            assert resp.status_code == 200
            assert b"123456789012" not in resp.data
            assert b"ABCDE1234F" not in resp.data
            assert b"9012" in resp.data  # last 4 digits still shown
            assert b"Restricted" in resp.data  # salary hidden message
        finally:
            # Not wrapping this in try/finally used to mean any assertion
            # failure above left 'test_admin' permanently stuck on role
            # 'manager' for the rest of the test session -- every later
            # test assuming a plain admin login (seed_admin + a normal
            # /login POST) would then silently get a degraded session
            # instead of failing loudly at the point of corruption.
            cur = db_engine.cursor()
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()
            cur.close()

    def test_soc_analyst_sees_masked_pii(self, client, soc_role_admin, pii_employee):
        _admin_session(client, soc_role_admin["username"], role="soc_analyst")
        resp = client.get(f"/employee_detail/{pii_employee}")
        assert resp.status_code == 200
        assert b"123456789012" not in resp.data
        assert b"ABCDE1234F" not in resp.data


class TestEmployeeProfileMasking:
    """employee_profile.html doesn't render aadhar/pan/bank fields at all
    today (only employee_detail.html does) — but the route still decrypts
    them into the template context, so the masking is genuine
    defense-in-depth there. Salary is the one field this template actually
    renders, so it's what's observable end-to-end."""

    def test_admin_sees_unmasked_salary(self, client, seed_admin, pii_employee):
        _admin_session(client, seed_admin["username"], role="admin")
        resp = client.get(f"/employee_profile/{pii_employee}")
        assert resp.status_code == 200
        assert b"2000" in resp.data
        assert b"Restricted" not in resp.data

    def test_manager_sees_masked_salary(self, client, seed_admin, db_engine, pii_employee):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='manager' WHERE username=%s", (seed_admin["username"],))
        db_engine.commit()
        cur.close()
        try:
            _admin_session(client, seed_admin["username"], role="manager")
            resp = client.get(f"/employee_profile/{pii_employee}")
            assert resp.status_code == 200
            assert b"Restricted" in resp.data
        finally:
            cur = db_engine.cursor()
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()
            cur.close()


class TestPayrollBulkRoutesAdminOnly:
    """view_salary/salary_report/salary_report_export tightened from
    admin_required (any admin-tier role) to role_required("admin") — the
    same restriction their sibling update_salary already had."""

    def test_manager_denied_view_salary(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='manager' WHERE username=%s", (seed_admin["username"],))
        db_engine.commit()
        cur.close()
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.get("/view_salary")
        assert resp.status_code == 403
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_admin_allowed_view_salary(self, client, seed_admin):
        _admin_session(client, seed_admin["username"], role="admin")
        resp = client.get("/view_salary")
        assert resp.status_code == 200


class TestApiRoleGapFix:
    """/api/employees, /api/employees/<id>, /api/salary_config,
    /api/salary_report previously accepted any valid admin Bearer token
    regardless of role — api_role_required("admin") closes that gap."""

    def test_manager_token_denied_bulk_employees_api(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='manager' WHERE username=%s", (seed_admin["username"],))
        db_engine.commit()
        cur.close()
        resp = client.post("/api/login", json={
            "username": seed_admin["username"], "password": seed_admin["password"],
        })
        token = resp.get_json()["token"]
        resp = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_admin_token_allowed_bulk_employees_api(self, client, seed_admin):
        resp = client.post("/api/login", json={
            "username": seed_admin["username"], "password": seed_admin["password"],
        })
        token = resp.get_json()["token"]
        resp = client.get("/api/employees", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
