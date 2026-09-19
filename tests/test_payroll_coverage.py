"""
Coverage tests for blueprints/payroll.py.
Targets uncovered lines: _upsert_co_features, save_salary_rules branches,
view_salary company filter, send_salary_email, send_all_salary_emails,
lock/unlock_payroll, my_payslip_summary, apply_hike, award_performance_bonus,
save_hike_config, api_salary_config GET/POST, api_salary_report.
"""
import datetime
import hashlib
import secrets


# ── helpers ───────────────────────────────────────────────────────────────────

def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    with client.session_transaction() as sess:
        logged_in = sess.get("admin_logged_in")
    if not logged_in:
        # A one-off transient failure here (rather than a real credential
        # problem -- seed_admin just created this row) used to surface many
        # steps later as a confusing, unrelated assertion failure deep in
        # the test body. Retry once before giving up loudly.
        client.post("/login", data={
            "identifier": seed_admin["username"],
            "password":   seed_admin["password"],
        })
        with client.session_transaction() as sess:
            logged_in = sess.get("admin_logged_in")
    assert logged_in, f"Admin login failed for {seed_admin['username']!r} -- check for stale admin_users.role state from another test."
    return client


def _emp_session(client, seed_employee):
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    return client


def _make_api_token(db_engine, token_type="admin", identity="test_admin"):
    """Insert a real admin API token; returns (raw_token, cleanup_fn)."""
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO api_tokens (identity, token, token_type, expires_at) VALUES (%s,%s,%s,%s)",
        (identity, token_hash, token_type, expiry)
    )
    cur.close()

    def cleanup():
        c = db_engine.cursor()
        c.execute("DELETE FROM api_tokens WHERE token=%s", (token_hash,))
        c.close()

    return raw, cleanup


# ── _upsert_co_features ────────────────────────────────────────────────────────

class TestUpsertCoFeatures:

    def test_no_company_id_returns_immediately(self):
        from utils.helpers import _upsert_co_features
        # Should silently return without touching DB
        _upsert_co_features(None, {"geo_enabled": True})

    def test_empty_fields_dict_returns_immediately(self):
        from utils.helpers import _upsert_co_features
        _upsert_co_features(1, {})

    def test_unknown_column_logs_error_and_returns(self, mocker):
        # _upsert_co_features now lives in utils/helpers.py (moved during the
        # podman-migration/master merge) — patch its own bound app_log, not payroll's.
        from utils.helpers import _upsert_co_features
        mock_log = mocker.patch("utils.helpers.app_log")
        _upsert_co_features(1, {"bad_col_xyz": True})
        mock_log.error.assert_called_once()

    def test_valid_fields_upsert_succeeds(self, mocker):
        from utils.helpers import _upsert_co_features
        mock_conn = mocker.MagicMock()
        mock_cur = mocker.MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mocker.patch("utils.helpers.get_db_connection", return_value=mock_conn)
        _upsert_co_features(99, {"geo_enabled": True, "qr_enabled": False})
        assert mock_cur.execute.called
        assert mock_conn.commit.called


# ── save_salary_rules ──────────────────────────────────────────────────────────

class TestSaveSalaryRules:

    def test_without_company_id_updates_company_settings(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/save_salary_rules", data={
            "late_deduction_pct": "10",
            "half_day_deduction_pct": "50",
            "grace_minutes": "15",
            "holiday_pay": "paid",
            "leave_pay": "exclude",
        })
        assert rv.status_code == 302
        assert "settings" in rv.headers["Location"]

    def test_with_active_company_id_calls_upsert(self, client, seed_admin, mocker):
        # save_salary_rules now lives in blueprints/admin_views.py (moved during
        # the podman-migration/master merge), which imports _upsert_co_features
        # by name from utils.helpers — patch admin_views' own bound reference.
        mock_upsert = mocker.patch("blueprints.admin_views._upsert_co_features")
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 99
        rv = client.post("/save_salary_rules", data={
            "late_deduction_pct": "15",
            "half_day_deduction_pct": "50",
            "grace_minutes": "10",
            "holiday_pay": "paid",
            "leave_pay": "exclude",
        })
        assert rv.status_code == 302
        mock_upsert.assert_called_once()

    def test_with_shift_times_updates_shifts(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/save_salary_rules", data={
            "late_deduction_pct": "10",
            "half_day_deduction_pct": "50",
            "grace_minutes": "15",
            "holiday_pay": "paid",
            "leave_pay": "exclude",
            "shift_start": "09:00",
            "shift_half":  "13:00",
            "shift_end":   "18:00",
        })
        assert rv.status_code == 302

    def test_invalid_values_flash_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/save_salary_rules", data={
            "late_deduction_pct": "not_a_number",
        })
        assert rv.status_code == 302
        assert "salary" in rv.headers["Location"]


# ── view_salary ────────────────────────────────────────────────────────────────

class TestViewSalary:

    def test_without_company_filter(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.get("/view_salary")
        assert rv.status_code == 200

    def test_with_active_company_id(self, client, seed_admin):
        """Line 394: company filter branch."""
        _admin_session(client, seed_admin)
        with client.session_transaction() as sess:
            sess["active_company_id"] = 1
        rv = client.get("/view_salary")
        assert rv.status_code == 200


# ── update_salary ─────────────────────────────────────────────────────────────

class TestUpdateSalary:

    def test_insert_salary_config(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_config WHERE employee_id='TST001'")
        cur.close()
        rv = client.post("/update_salary", data={
            "emp_id": seed_employee["employee_id"],
            "salary": "1000",
            "hike_date": "",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id='TST001'")
        row = cur.fetchone()
        cur.close()
        assert row is not None

    def test_update_existing_salary_config(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day) VALUES ('TST001', 500)"
            " ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=500"
        )
        cur.close()
        rv = client.post("/update_salary", data={
            "emp_id": seed_employee["employee_id"],
            "salary": "1500",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT salary_per_day FROM salary_config WHERE employee_id='TST001'")
        row = cur.fetchone()
        cur.close()
        assert float(row[0]) == 1500.0


# ── send_salary_email ─────────────────────────────────────────────────────────

class TestSendSalaryEmail:

    def test_no_email_config_returns_error_json(self, client, seed_admin, seed_employee, mocker):
        mocker.patch("blueprints.payroll.get_email_config", return_value=None)
        _admin_session(client, seed_admin)
        rv = client.post("/send_salary_email", data={
            "emp_id": seed_employee["employee_id"],
            "year": "2025",
            "month": "1",
        })
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is False
        assert "Email not configured" in data["msg"]

    def test_unknown_employee_returns_error_json(self, client, seed_admin, mocker):
        mocker.patch("blueprints.payroll.get_email_config", return_value={"host": "smtp.test"})
        _admin_session(client, seed_admin)
        rv = client.post("/send_salary_email", data={
            "emp_id": "GHOST999",
            "year": "2025",
            "month": "1",
        })
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is False
        assert "not found" in data["msg"].lower()

    def test_employee_no_email_returns_error_json(self, client, seed_admin, seed_employee, db_engine, mocker):
        mocker.patch("blueprints.payroll.get_email_config", return_value={"host": "smtp.test"})
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=NULL WHERE employee_id='TST001'")
        cur.close()
        try:
            rv = client.post("/send_salary_email", data={
                "emp_id": seed_employee["employee_id"],
                "year": "2025",
                "month": "1",
            })
            data = rv.get_json()
            assert data["ok"] is False
            assert "email" in data["msg"].lower()
        finally:
            cur = db_engine.cursor()
            cur.execute("UPDATE employees SET email='emp@test.local' WHERE employee_id='TST001'")
            cur.close()


# ── send_all_salary_emails ────────────────────────────────────────────────────

class TestSendAllSalaryEmails:

    def test_no_email_config_returns_error_json(self, client, seed_admin, mocker):
        mocker.patch("blueprints.payroll.get_email_config", return_value=None)
        _admin_session(client, seed_admin)
        rv = client.post("/send_all_salary_emails", data={"year": "2025", "month": "1"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is False

    def test_with_no_email_employees_skips_all(self, client, seed_admin, seed_employee, db_engine, mocker):
        mock_config = {"host": "smtp.test", "user": "u", "pass": "p", "port": 587, "from": "f@t.local"}
        mocker.patch("blueprints.payroll.get_email_config", return_value=mock_config)
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email=NULL WHERE employee_id='TST001'")
        cur.close()
        try:
            rv = client.post("/send_all_salary_emails", data={"year": "2025", "month": "1"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert "Skipped" in data["msg"]
        finally:
            cur = db_engine.cursor()
            cur.execute("UPDATE employees SET email='emp@test.local' WHERE employee_id='TST001'")
            cur.close()


# ── lock_payroll / unlock_payroll ─────────────────────────────────────────────

class TestLockUnlockPayroll:

    def test_lock_payroll_returns_ok(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        rv = client.post("/lock_payroll", data={"year": "2099", "month": "12"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        cur = db_engine.cursor()
        cur.execute("DELETE FROM payroll_runs WHERE year=2099 AND month=12")
        cur.close()

    def test_unlock_payroll_returns_ok(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO payroll_runs (year, month, processed_by, email_count) VALUES (2098, 11, 'test', 0)"
            " ON CONFLICT (year, month) DO NOTHING"
        )
        cur.close()
        rv = client.post("/unlock_payroll", data={"year": "2098", "month": "11"})
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True


# ── my_payslip_summary ─────────────────────────────────────────────────────────

class TestMyPayslipSummary:

    def test_returns_json_for_employee(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day) VALUES ('TST001', 1000)"
            " ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=1000"
        )
        cur.close()
        today = datetime.date.today()
        rv = client.get(f"/my_payslip_summary/{today.year}/{today.month}")
        assert rv.status_code == 200
        import json
        data = json.loads(rv.data)
        assert "salary_per_day" in data
        assert "net" in data

    def test_returns_zero_for_employee_with_no_salary(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_config WHERE employee_id='TST001'")
        cur.close()
        today = datetime.date.today()
        rv = client.get(f"/my_payslip_summary/{today.year}/{today.month}")
        assert rv.status_code == 200
        import json
        data = json.loads(rv.data)
        assert data["salary_per_day"] == 0.0


# ── apply_hike ────────────────────────────────────────────────────────────────

class TestApplyHike:

    def test_no_employees_selected_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/apply_hike", data={"quarter": "1", "year": "2025"})
        assert rv.status_code == 302
        assert "performance" in rv.headers["Location"]

    def test_with_employees_redirect_to_performance(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.post("/apply_hike", data={
            "quarter": "1",
            "year": "2025",
            "emp_ids": seed_employee["employee_id"],
        })
        assert rv.status_code == 302
        assert "performance" in rv.headers["Location"]


# ── award_performance_bonus ───────────────────────────────────────────────────

class TestAwardPerformanceBonus:

    def test_no_employees_selected_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/award_performance_bonus", data={"quarter": "1", "year": "2025"})
        assert rv.status_code == 302
        assert "performance" in rv.headers["Location"]

    def test_with_employees_processes_and_redirects(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin)
        rv = client.post("/award_performance_bonus", data={
            "quarter": "1",
            "year": "2025",
            "emp_ids": seed_employee["employee_id"],
        })
        assert rv.status_code == 302


# ── save_hike_config ──────────────────────────────────────────────────────────

class TestSaveHikeConfig:

    def test_no_band_data_flashes_error(self, client, seed_admin):
        _admin_session(client, seed_admin)
        rv = client.post("/save_hike_config", data={"quarter": "1", "year": "2025"})
        assert rv.status_code == 302
        assert "performance" in rv.headers["Location"]

    def test_with_band_data_updates_and_redirects(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO hike_config (label, min_rating, max_rating, hike_pct, incentive_pct)"
            " VALUES ('Top', 4.0, 5.0, 10.0, 5.0) RETURNING id"
        )
        hike_id = cur.fetchone()[0]
        cur.close()
        rv = client.post("/save_hike_config", data={
            "quarter": "1", "year": "2025",
            "band_id":     str(hike_id),
            "band_label":  "Top Performer",
            "band_min":    "4.0",
            "band_max":    "5.0",
            "band_hike":   "12.0",
            "band_inc":    "6.0",
        })
        assert rv.status_code == 302
        cur = db_engine.cursor()
        cur.execute("DELETE FROM hike_config WHERE id=%s", (hike_id,))
        cur.close()


# ── api_salary_config GET/POST ────────────────────────────────────────────────

class TestApiSalaryConfig:

    def test_get_returns_salary_list(self, client, db_engine, seed_admin):
        # api_role_required looks up admin_users.role for the token's identity
        # ("test_admin", _make_api_token's default) — needs seed_admin's row to exist.
        raw, cleanup = _make_api_token(db_engine)
        try:
            rv = client.get("/api/salary_config",
                            headers={"Authorization": f"Bearer {raw}"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert data["ok"] is True
            assert isinstance(data["salaries"], list)
        finally:
            cleanup()

    def test_post_missing_fields_returns_400(self, client, db_engine, seed_admin):
        # api_salary_config_post is now role-gated (@api_role_required("admin"))
        # same as its GET twin above -- needs seed_admin's row to exist for
        # "test_admin" to resolve to a real admin_users.role.
        raw, cleanup = _make_api_token(db_engine)
        try:
            rv = client.post("/api/salary_config",
                             json={},
                             headers={"Authorization": f"Bearer {raw}",
                                      "Content-Type": "application/json"})
            assert rv.status_code == 400
        finally:
            cleanup()

    def test_post_insert_salary(self, client, db_engine, seed_employee, seed_admin):
        raw, cleanup = _make_api_token(db_engine)
        try:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM salary_config WHERE employee_id='TST001'")
            cur.close()
            rv = client.post("/api/salary_config",
                             json={"employee_id": "TST001", "salary_per_day": 2000},
                             headers={"Authorization": f"Bearer {raw}",
                                      "Content-Type": "application/json"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert data["ok"] is True
        finally:
            cleanup()

    def test_post_update_existing_salary(self, client, db_engine, seed_employee, seed_admin):
        raw, cleanup = _make_api_token(db_engine)
        try:
            cur = db_engine.cursor()
            cur.execute(
                "INSERT INTO salary_config (employee_id, salary_per_day) VALUES ('TST001', 800)"
                " ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=800"
            )
            cur.close()
            rv = client.post("/api/salary_config",
                             json={"employee_id": "TST001", "salary_per_day": 2500},
                             headers={"Authorization": f"Bearer {raw}",
                                      "Content-Type": "application/json"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert data["ok"] is True
        finally:
            cleanup()

    def test_unauthenticated_returns_401(self, client):
        rv = client.get("/api/salary_config")
        assert rv.status_code == 401


# ── api_salary_report ─────────────────────────────────────────────────────────

class TestApiSalaryReport:

    def test_returns_salary_data(self, client, db_engine, seed_admin):
        # api_role_required looks up admin_users.role for the token's identity
        # ("test_admin", _make_api_token's default) — needs seed_admin's row to exist.
        raw, cleanup = _make_api_token(db_engine)
        try:
            today = datetime.date.today()
            rv = client.get(f"/api/salary_report?year={today.year}&month={today.month}",
                            headers={"Authorization": f"Bearer {raw}"})
            assert rv.status_code == 200
            data = rv.get_json()
            assert data["ok"] is True
            assert "salary_data" in data
            assert "month_name" in data
        finally:
            cleanup()

    def test_unauthenticated_returns_401(self, client):
        rv = client.get("/api/salary_report")
        assert rv.status_code == 401


# ── build_salary_slip_html (direct call) ──────────────────────────────────────

class TestBuildSalarySlipHtml:

    def test_basic_slip_renders_html(self):
        from blueprints.payroll import build_salary_slip_html
        salary_data = {
            "monthly_ctc": 50000,
            "basic_pct": 50,
            "full_days": 22,
            "late_days": 1,
            "half_days": 0,
            "absent": 1,
            "incentive": 0,
        }
        html = build_salary_slip_html(
            "Test Emp", "TST001", "emp@test.local",
            "January 2025", 2025, 1, salary_data,
            company_name="Acme Corp",
        )
        assert "Test Emp" in html
        assert "Acme Corp" in html
        assert "January 2025" in html

    def test_slip_with_extra_fields(self):
        from blueprints.payroll import build_salary_slip_html
        salary_data = {
            "monthly_ctc": 60000,
            "basic_pct": 40,
            "full_days": 20,
            "late_days": 2,
            "half_days": 1,
            "absent": 0,
            "incentive": 500,
            "spd": 0,
        }
        html = build_salary_slip_html(
            "Jane Doe", "EMP002", "jane@test.local",
            "March 2025", 2025, 3, salary_data,
            emp_designation="Engineer",
            emp_dept="Tech",
            pan="ABCDE1234F",
            uan="123456789012",
            bank_account="00001234567890",
            bank_name="State Bank",
            payroll_cfg={
                "pf_employee_pct": 12, "pf_employer_pct": 12,
                "professional_tax": 200, "tds_annual_pct": 5,
                "pf_basic_cap": 15000,
            },
        )
        assert "Jane Doe" in html
        assert "Engineer" in html
        assert "Tech" in html
        assert "ABCDE1234F" in html

    def test_slip_with_zero_ctc_uses_spd(self):
        from blueprints.payroll import build_salary_slip_html
        salary_data = {
            "monthly_ctc": 0,
            "basic_pct": 50,
            "full_days": 20,
            "late_days": 0,
            "half_days": 0,
            "absent": 2,
            "incentive": 0,
            "spd": 2000,
        }
        html = build_salary_slip_html(
            "Bob", "BOB001", "bob@t.local",
            "February 2025", 2025, 2, salary_data,
        )
        assert "Bob" in html


# ── HTML documents built from employee data must escape it ────────────────────

class TestEmployeeDataIsEscapedInGeneratedHtml:
    """my_attendance_pdf and download_tax_statement assemble whole HTML pages
    from f-strings. HR/admin can edit an employee's name/role, and
    download_tax_statement's emp_id comes straight from the URL, so any of
    them reaching the page unescaped is script injection -- and app.py's
    after_request adds the response's CSP nonce to EVERY <script tag in the
    body, so an injected script would carry a valid nonce and execute."""

    PAYLOAD = "<script>alert(1)</script>"
    ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"

    def _set_name_and_role(self, db_engine, emp_id):
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET name=%s, role=%s WHERE employee_id=%s",
                    (self.PAYLOAD, self.PAYLOAD, emp_id))
        cur.close()

    def test_attendance_report_escapes_name_and_role(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        self._set_name_and_role(db_engine, seed_employee["employee_id"])
        rv = client.get("/my_attendance_pdf")
        assert rv.status_code == 200
        assert self.PAYLOAD.encode() not in rv.data
        assert self.ESCAPED.encode() in rv.data

    def test_tax_statement_escapes_employee_name(self, client, seed_employee, db_engine):
        _emp_session(client, seed_employee)
        self._set_name_and_role(db_engine, seed_employee["employee_id"])
        rv = client.get(f"/download_tax_statement/{seed_employee['employee_id']}/2025")
        assert rv.status_code == 200
        assert self.PAYLOAD.encode() not in rv.data
        assert self.ESCAPED.encode() in rv.data

    def test_tax_statement_escapes_an_employee_id_containing_markup(self, client, db_engine):
        """The statement prints the id from the URL next to the name. A
        session for an id with no employee row is rejected by
        employee_required before the view runs, so the reachable case is an
        employee whose own id contains markup. The payload is inert markup
        with no "/" (a slash would split the URL path and 404) that the WAF
        lets through -- utils/waf.py blocks classic script/onerror=
        signatures in path params, but a signature list is no substitute
        for escaping, so this proves the view itself escapes."""
        from utils.auth import generate_password_hash
        hostile_id = "<u>injected"
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
            "VALUES (%s,%s,%s,%s,0) ON CONFLICT (employee_id) DO NOTHING",
            (hostile_id, "Markup Id Employee", "markup@test.local", generate_password_hash("EmpPass@1")),
        )
        try:
            with client.session_transaction() as sess:
                sess["employee_id"] = hostile_id
            rv = client.get(f"/download_tax_statement/{hostile_id}/2025")
            assert rv.status_code == 200
            assert hostile_id.encode() not in rv.data
            assert b"&lt;u&gt;injected" in rv.data
        finally:
            cur.execute("DELETE FROM employees WHERE employee_id=%s", (hostile_id,))
            cur.close()
