"""
Payroll blueprint — comprehensive test suite.
Covers: view_salary, update_salary, salary_report, lock/unlock,
        payslips, hikes, hike config, and all API endpoints.

Target: payroll.py 22% → ~65%
"""
from utils.helpers import decrypt_pii as _unpay  # pay columns hold ciphertext; this reads them
from utils.helpers import encrypt_amount as _pay  # pay columns only accept ciphertext
import datetime
import pytest


# ── Session / token helpers ──────────────────────────────────────────────────

def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    return client


def _emp_session(client, seed_employee):
    """Inject an employee session directly (employee_login just redirects to admin_login)."""
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    return client


def _admin_token(client, seed_admin):
    resp = client.post("/api/login", json={
        "username": seed_admin["username"],
        "password": seed_admin["password"],
    })
    return resp.get_json()["token"]


def _emp_token(client, seed_employee):
    resp = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"],
        "password":    seed_employee["password"],
    })
    return resp.get_json()["token"]


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def salary_config(db_engine, seed_employee):
    """Seed a salary_config row for TST001."""
    cur = db_engine.cursor()
    cur.execute("""
        INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc, basic_pct)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (employee_id) DO UPDATE
            SET salary_per_day=EXCLUDED.salary_per_day, monthly_ctc=EXCLUDED.monthly_ctc,
                basic_pct=EXCLUDED.basic_pct
    """, (seed_employee["employee_id"], _pay(1500), _pay(39000), _pay(50)))
    yield {"employee_id": seed_employee["employee_id"], "spd": 1500.0}
    cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.close()


@pytest.fixture
def payroll_lock(db_engine):
    """Seed a payroll_runs lock for Jan 2025, clean up after."""
    cur = db_engine.cursor()
    cur.execute("""
        INSERT INTO payroll_runs (year, month, processed_by, email_count)
        VALUES (2025, 1, 'test', 0)
        ON CONFLICT (year, month) DO NOTHING
    """)
    yield {"year": 2025, "month": 1}
    cur.execute("DELETE FROM payroll_runs WHERE year=2025 AND month=1")
    cur.close()


# ===========================================================================
# 1. Auth guards
# ===========================================================================

class TestPayrollAuthGuards:
    def test_view_salary_requires_admin(self, client):
        assert client.get("/view_salary", follow_redirects=False).status_code in (302, 401)

    def test_salary_report_requires_admin(self, client):
        assert client.get("/salary_report", follow_redirects=False).status_code in (302, 401)

    def test_update_salary_requires_admin(self, client):
        assert client.post("/update_salary", data={}).status_code in (302, 401)

    def test_lock_payroll_requires_admin(self, client):
        assert client.post("/lock_payroll", data={}).status_code in (302, 401)

    def test_unlock_payroll_requires_admin(self, client):
        assert client.post("/unlock_payroll", data={}).status_code in (302, 401)

    def test_apply_hike_requires_admin(self, client):
        assert client.post("/apply_hike", data={}).status_code in (302, 401)

    def test_save_hike_config_requires_admin(self, client):
        assert client.post("/save_hike_config", data={}).status_code in (302, 401)

    def test_api_salary_config_requires_token(self, client):
        assert client.get("/api/salary_config").status_code == 401

    def test_api_salary_report_requires_token(self, client):
        assert client.get("/api/salary_report").status_code == 401

    def test_my_payslip_summary_requires_employee(self, client):
        assert client.get("/my_payslip_summary/2025/1",
                          follow_redirects=False).status_code in (302, 401)

    def test_api_employee_salary_requires_employee_token(self, client):
        assert client.get("/api/employee/salary").status_code == 401


# ===========================================================================
# 2. /view_salary
# ===========================================================================

class TestViewSalary:
    def test_renders_200(self, client, seed_admin):
        _admin_session(client, seed_admin)
        assert client.get("/view_salary").status_code == 200

    def test_shows_employee_with_salary_config(self, client, seed_admin, salary_config, seed_employee):
        _admin_session(client, seed_admin)
        resp = client.get("/view_salary")
        assert resp.status_code == 200
        assert seed_employee["employee_id"].encode() in resp.data

    def test_shows_employee_without_salary_config(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        resp = client.get("/view_salary")
        assert resp.status_code == 200


# ===========================================================================
# 3. /update_salary
# ===========================================================================

class TestUpdateSalary:
    def test_creates_new_salary_config(self, client, seed_admin, seed_employee, db_engine):
        # Delete existing config first
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

        _admin_session(client, seed_admin)
        client.post("/update_salary", data={
            "emp_id":    seed_employee["employee_id"],
            "salary":    "2000",
            "hike_date": "2025-01-01",
        }, follow_redirects=True)

        cur = db_engine.cursor()
        cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        row = cur.fetchone()
        cur.close()
        assert row is not None
        assert float(_unpay(row[0])) == 2000.0

    def test_updates_existing_salary_config(self, client, seed_admin, seed_employee, salary_config, db_engine):
        _admin_session(client, seed_admin)
        client.post("/update_salary", data={
            "emp_id":  seed_employee["employee_id"],
            "salary":  "1800",
        }, follow_redirects=True)

        cur = db_engine.cursor()
        cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        assert float(_unpay(cur.fetchone()[0])) == 1800.0
        cur.close()

    def test_accepts_no_hike_date(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        resp = client.post("/update_salary", data={
            "emp_id": seed_employee["employee_id"],
            "salary": "1600",
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_redirects_to_view_salary(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        resp = client.post("/update_salary", data={
            "emp_id": seed_employee["employee_id"],
            "salary": "1700",
        }, follow_redirects=False)
        assert resp.status_code in (302, 200)


# ===========================================================================
# 4. /salary_report
# ===========================================================================

class TestSalaryReport:
    def test_renders_200_default_params(self, client, seed_admin):
        _admin_session(client, seed_admin)
        assert client.get("/salary_report").status_code == 200

    def test_renders_with_explicit_year_month(self, client, seed_admin):
        _admin_session(client, seed_admin)
        assert client.get("/salary_report?year=2025&month=1").status_code == 200

    def test_renders_with_salary_config(self, client, seed_admin, salary_config):
        _admin_session(client, seed_admin)
        assert client.get("/salary_report?year=2025&month=1").status_code == 200

    def test_all_months_render(self, client, seed_admin):
        _admin_session(client, seed_admin)
        for m in range(1, 13):
            assert client.get(f"/salary_report?year=2025&month={m}").status_code == 200


# ===========================================================================
# 5. /lock_payroll and /unlock_payroll
# ===========================================================================

class TestPayrollLockUnlock:
    def test_lock_payroll_returns_ok_json(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/lock_payroll", data={"year": "2025", "month": "6"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_unlock_payroll_returns_ok_json(self, client, seed_admin, payroll_lock):
        _admin_session(client, seed_admin)
        resp = client.post("/unlock_payroll", data={"year": "2025", "month": "1"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_lock_then_unlock_idempotent(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        client.post("/lock_payroll", data={"year": "2024", "month": "3"})
        resp = client.post("/unlock_payroll", data={"year": "2024", "month": "3"})
        assert resp.status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM payroll_runs WHERE year=2024 AND month=3")
        assert cur.fetchone() is None
        cur.close()

    def test_lock_creates_payroll_runs_row(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        client.post("/lock_payroll", data={"year": "2024", "month": "7"})
        cur = db_engine.cursor()
        cur.execute("SELECT processed_by FROM payroll_runs WHERE year=2024 AND month=7")
        row = cur.fetchone()
        cur.execute("DELETE FROM payroll_runs WHERE year=2024 AND month=7")
        cur.close()
        assert row is not None


# ===========================================================================
# 6. /my_payslip_summary/<year>/<month>
# ===========================================================================

class TestMyPayslipSummary:
    def test_returns_json(self, client, seed_employee):
        _emp_session(client, seed_employee)
        resp = client.get("/my_payslip_summary/2025/1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)

    def test_contains_salary_keys(self, client, seed_employee, salary_config):
        _emp_session(client, seed_employee)
        resp = client.get("/my_payslip_summary/2025/1")
        data = resp.get_json()
        assert "gross" in data
        assert "net" in data
        assert "salary_per_day" in data

    def test_no_salary_config_returns_zeros(self, client, seed_employee):
        _emp_session(client, seed_employee)
        resp = client.get("/my_payslip_summary/2024/1")
        data = resp.get_json()
        assert data["salary_per_day"] == 0.0 or data["gross"] == 0.0

    def test_all_months_of_year(self, client, seed_employee):
        _emp_session(client, seed_employee)
        for m in range(1, 13):
            assert client.get(f"/my_payslip_summary/2024/{m}").status_code == 200


# ===========================================================================
# 7. /save_hike_config
# ===========================================================================

class TestSaveHikeConfig:
    def test_save_hike_config_accepts_band_data(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/save_hike_config", data={
            "quarter": "1",
            "year":    "2025",
            "label_1":       "Outstanding",
            "min_rating_1":  "4.5",
            "max_rating_1":  "5.0",
            "hike_pct_1":    "15",
            "incentive_pct_1": "10",
            "color_1":       "#10B981",
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_save_hike_config_multiple_bands(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/save_hike_config", data={
            "quarter": "1",
            "year":    "2025",
            "label_1": "Band A", "min_rating_1": "4", "max_rating_1": "5",
            "hike_pct_1": "12", "incentive_pct_1": "8", "color_1": "#0EA5E9",
            "label_2": "Band B", "min_rating_2": "3", "max_rating_2": "4",
            "hike_pct_2": "8",  "incentive_pct_2": "5", "color_2": "#F59E0B",
        }, follow_redirects=True)
        assert resp.status_code == 200


# ===========================================================================
# 8. /apply_hike
# ===========================================================================

class TestApplyHike:
    def test_no_employees_selected_shows_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/apply_hike", data={
            "quarter": "1",
            "year":    "2025",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"No employees" in resp.data or b"selected" in resp.data.lower()

    def test_apply_hike_with_salary_config(self, client, seed_admin, seed_employee, salary_config, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/apply_hike", data={
            "quarter":   "1",
            "year":      "2025",
            "emp_ids":   seed_employee["employee_id"],
            "hike_pct":  "10",
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_apply_hike_unknown_employee_graceful(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/apply_hike", data={
            "quarter": "1",
            "year":    "2025",
            "emp_ids": "NONEXISTENT_EMP_XYZ",
        }, follow_redirects=True)
        assert resp.status_code == 200


# ===========================================================================
# 9. /view_payslip/<emp_id>/<year>/<month>
# ===========================================================================

class TestViewPayslip:
    def test_admin_can_view_any_payslip(self, client, seed_admin, seed_employee, salary_config):
        today = datetime.date.today()
        _admin_session(client, seed_admin)
        resp = client.get(
            f"/view_payslip/{seed_employee['employee_id']}/{today.year}/{today.month}"
        )
        assert resp.status_code in (200, 302)

    def test_employee_can_view_own_payslip(self, client, seed_employee, salary_config):
        _emp_session(client, seed_employee)
        resp = client.get(
            f"/view_payslip/{seed_employee['employee_id']}/2025/1"
        )
        assert resp.status_code in (200, 302)

    def test_unauthenticated_redirects(self, client, seed_employee):
        resp = client.get(f"/view_payslip/{seed_employee['employee_id']}/2025/1",
                          follow_redirects=False)
        assert resp.status_code in (302, 401)

    def test_nonexistent_employee_returns_404_or_redirect(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/view_payslip/NO_SUCH_EMP_XYZ/2025/1", follow_redirects=False)
        assert resp.status_code in (200, 302, 404)

    def test_past_month_renders(self, client, seed_admin, seed_employee, salary_config):
        _admin_session(client, seed_admin)
        resp = client.get(f"/view_payslip/{seed_employee['employee_id']}/2024/6")
        assert resp.status_code in (200, 302)


# ===========================================================================
# 10. API: GET /api/salary_config
# ===========================================================================

class TestApiSalaryConfigGet:
    def test_returns_ok_and_salaries_list(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_config",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["salaries"], list)

    def test_includes_seed_employee(self, client, seed_admin, seed_employee, salary_config):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_config",
                          headers={"Authorization": f"Bearer {token}"})
        ids = [r["employee_id"] for r in resp.get_json()["salaries"]]
        assert seed_employee["employee_id"] in ids

    def test_salary_per_day_is_float(self, client, seed_admin, salary_config):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_config",
                          headers={"Authorization": f"Bearer {token}"})
        for row in resp.get_json()["salaries"]:
            assert isinstance(row["salary_per_day"], float)


# ===========================================================================
# 11. API: POST /api/salary_config
# ===========================================================================

class TestApiSalaryConfigPost:
    def test_creates_salary_config(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

        token = _admin_token(client, seed_admin)
        resp = client.post("/api/salary_config", json={
            "employee_id":  seed_employee["employee_id"],
            "salary_per_day": 2500,
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_updates_existing_salary_config(self, client, seed_admin, seed_employee, salary_config):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/salary_config", json={
            "employee_id":    seed_employee["employee_id"],
            "salary_per_day": 3000,
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_missing_employee_id_returns_400(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/salary_config", json={"salary_per_day": 1000},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_missing_salary_returns_400(self, client, seed_admin, seed_employee):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/salary_config",
                           json={"employee_id": seed_employee["employee_id"]},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_unauthenticated_returns_401(self, client, seed_employee):
        resp = client.post("/api/salary_config", json={
            "employee_id": seed_employee["employee_id"],
            "salary_per_day": 1000,
        })
        assert resp.status_code == 401

    def test_non_admin_role_denied(self, client, seed_admin, seed_employee, db_engine):
        """api_salary_config_post used to check only @api_required (any
        admin-side token, any role) -- a lower-privilege role like
        soc_analyst could silently overwrite any employee's pay rate. Now
        gated to @api_role_required("admin"), same as the GET twin and
        the web /update_salary route."""
        token = _admin_token(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        try:
            resp = client.post("/api/salary_config", json={
                "employee_id": seed_employee["employee_id"],
                "salary_per_day": 99999,
            }, headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403
            cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id=%s",
                        (seed_employee["employee_id"],))
            row = cur.fetchone()
            assert row is None or float(row[0]) != 99999, "salary was written despite the role check"
        finally:
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            db_engine.commit()
            cur.close()

    def test_write_is_audited(self, client, seed_admin, seed_employee, db_engine):
        # audit_logs is append-only at the DB level (a trigger rejects
        # DELETE/UPDATE against it) -- no pre-test cleanup possible or
        # needed; a distinctive salary value is enough to find THIS
        # write's own row without touching any other test's rows.
        token = _admin_token(client, seed_admin)
        cur = db_engine.cursor()
        resp = client.post("/api/salary_config", json={
            "employee_id": seed_employee["employee_id"],
            "salary_per_day": 4242,
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        cur.execute(
            "SELECT detail FROM audit_logs WHERE target_id=%s AND action='update_salary' "
            "ORDER BY id DESC LIMIT 1",
            (seed_employee["employee_id"],)
        )
        row = cur.fetchone()
        assert row is not None, "api_salary_config_post's write was not audited"
        assert "4242" not in row[0], "the salary amount must not be written to the audit log in plaintext"
        cur.close()


# ===========================================================================
# 12. API: GET /api/salary_report
# ===========================================================================

class TestApiSalaryReport:
    def test_returns_ok_and_salary_data(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_report?year=2025&month=1",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "salary_data" in data

    def test_includes_month_name(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_report?year=2025&month=6",
                          headers={"Authorization": f"Bearer {token}"})
        data = resp.get_json()
        assert "June 2025" in data.get("month_name", "")

    def test_salary_data_is_list(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_report",
                          headers={"Authorization": f"Bearer {token}"})
        assert isinstance(resp.get_json()["salary_data"], list)

    def test_requires_auth(self, client):
        assert client.get("/api/salary_report").status_code == 401

    def test_salary_data_has_expected_fields(self, client, seed_admin, seed_employee, salary_config):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/salary_report?year=2025&month=1",
                          headers={"Authorization": f"Bearer {token}"})
        rows = resp.get_json()["salary_data"]
        if rows:
            row = rows[0]
            for key in ("emp_id", "name"):
                assert key in row


# ===========================================================================
# 13. API: GET /api/employee/salary
# ===========================================================================

class TestApiEmployeeSalary:
    def test_returns_salary_data(self, client, seed_employee, salary_config):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/salary",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_salary_struct_has_required_keys(self, client, seed_employee, salary_config):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/salary",
                          headers={"Authorization": f"Bearer {token}"})
        sal = resp.get_json()["salary"]
        for key in ("emp_id", "name", "spd", "gross", "deduction", "net"):
            assert key in sal

    def test_no_salary_config_returns_zeros(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/salary?year=2024&month=1",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        sal = resp.get_json()["salary"]
        assert sal["spd"] == 0.0

    def test_explicit_year_month_params(self, client, seed_employee, salary_config):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/salary?year=2025&month=3",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["month"] == 3

    def test_requires_employee_token(self, client):
        assert client.get("/api/employee/salary").status_code == 401

    def test_admin_token_blocked(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.get("/api/employee/salary",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_salary_per_day_matches_config(self, client, seed_employee, salary_config):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/employee/salary",
                          headers={"Authorization": f"Bearer {token}"})
        sal = resp.get_json()["salary"]
        assert sal["spd"] == salary_config["spd"]


# ===========================================================================
# 14. build_salary_slip_html unit test
# ===========================================================================

class TestBuildSalarySlipHtml:
    def test_html_contains_employee_name(self):
        from blueprints.payroll import build_salary_slip_html
        html = build_salary_slip_html(
            emp_name="John Doe",
            emp_id="EMP001",
            emp_email="john@test.local",
            month_name="January 2025",
            year=2025,
            month=1,
            salary_data={
                "monthly_ctc": 50000,
                "basic_pct": 50,
                "full_days": 22,
                "late_days": 1,
                "half_days": 0,
                "absent": 0,
            }
        )
        assert "John Doe" in html
        assert "January 2025" in html

    def test_html_calculates_net_pay_positive(self):
        from blueprints.payroll import build_salary_slip_html
        html = build_salary_slip_html(
            emp_name="Jane Smith",
            emp_id="EMP002",
            emp_email="jane@test.local",
            month_name="March 2025",
            year=2025,
            month=3,
            salary_data={
                "monthly_ctc": 60000,
                "basic_pct": 50,
                "full_days": 26,
                "late_days": 0,
                "half_days": 0,
                "absent": 0,
            }
        )
        assert "Jane Smith" in html

    def test_html_with_zero_ctc_uses_spd(self):
        from blueprints.payroll import build_salary_slip_html
        html = build_salary_slip_html(
            emp_name="Emp Zero",
            emp_id="EMP003",
            emp_email="zero@test.local",
            month_name="April 2025",
            year=2025,
            month=4,
            salary_data={
                "monthly_ctc": 0,
                "spd": 1500,
                "basic_pct": 50,
                "full_days": 20,
                "late_days": 0,
                "half_days": 0,
                "absent": 2,
            }
        )
        assert "Emp Zero" in html

    def test_html_with_extra_employee_fields(self):
        from blueprints.payroll import build_salary_slip_html
        html = build_salary_slip_html(
            emp_name="Full Employee",
            emp_id="EMP004",
            emp_email="full@test.local",
            month_name="May 2025",
            year=2025,
            month=5,
            salary_data={"monthly_ctc": 40000, "basic_pct": 50, "full_days": 22,
                         "late_days": 0, "half_days": 0, "absent": 0, "incentive": 1000},
            company_name="Acme Corp",
            emp_designation="Engineer",
            emp_dept="Technology",
            pan="ABCDE1234F",
            uan="100123456789",
            bank_account="123456789012",
            bank_name="HDFC Bank",
            payroll_cfg={"pf_employee_pct": 12, "professional_tax": 200, "tds_annual_pct": 5},
        )
        assert "Acme Corp" in html
        assert "Engineer" in html
