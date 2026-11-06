"""Route-level tests for the settings/company-management/misc portion of
blueprints/admin_views.py that isn't already covered by
tests/test_soc_gate.py, tests/test_security_settings_hub.py,
tests/test_email_2fa.py, tests/test_org.py, or tests/test_admin_search.py
(2FA, SOC dashboard, security-settings hub, org provisioning, search).
"""
import secrets
import pytest
from tests.conftest import patch_package  # noqa: E402


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


@pytest.fixture
def temp_company(db_engine):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO companies (name, code) VALUES ('Route Test Co', 'RTC') RETURNING id")
    cid = cur.fetchone()[0]
    try:
        yield cid
    finally:
        cur.execute("DELETE FROM company_feature_settings WHERE company_id=%s", (cid,))
        cur.execute("DELETE FROM shifts WHERE company_id=%s", (cid,))
        cur.execute("DELETE FROM break_config WHERE company_id=%s", (cid,))
        cur.execute("DELETE FROM employees WHERE company_id=%s", (cid,))
        cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
        cur.close()


class TestAdminDashboardWithActiveCompany:
    def test_dashboard_scoped_to_active_company(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = temp_company
        resp = client.get("/admin")
        assert resp.status_code == 200


class TestActivityLogPage:
    """New Activity Log view (Finding #5) -- a browsable page over
    audit_logs, which previously had no reviewer anywhere in the product.
    audit_logs is append-only at the DB level (DELETE is rejected by a
    trigger), so these tests use a distinctive marker string to find only
    their own rows rather than cleaning up afterward."""

    def _seed_entry(self, db_engine, action, actor, detail, target_id=None):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO audit_logs (actor, actor_type, action, target_table, target_id, detail) "
            "VALUES (%s,'admin',%s,'employees',%s,%s)",
            (actor, action, target_id, detail)
        )
        cur.close()
        db_engine.commit()

    def test_unauthenticated_redirects(self, client):
        resp = client.get("/activity_log", follow_redirects=False)
        assert resp.status_code in (302, 401, 403)

    def test_non_admin_role_denied(self, client, seed_admin):
        _admin_session(client, seed_admin["username"], role="hr")
        resp = client.get("/activity_log")
        assert resp.status_code == 403

    def test_admin_sees_page(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/activity_log")
        assert resp.status_code == 200
        assert b"Activity Log" in resp.data

    def test_search_finds_seeded_entry(self, client, seed_admin, db_engine):
        marker = "ACTLOGMARKER_" + secrets.token_hex(6)
        self._seed_entry(db_engine, "update_salary", seed_admin["username"],
                         f"salary_per_day set to 5000 ({marker})")
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/activity_log?q={marker}")
        assert resp.status_code == 200
        assert marker.encode() in resp.data

    def test_action_filter_excludes_non_matching_entries(self, client, seed_admin, db_engine):
        # No `q` here deliberately -- the search box always echoes back
        # whatever `q` was submitted (value="{{ q }}"), so asserting a
        # marker's absence while also passing it as `q` would trivially
        # fail regardless of whether the *table* actually excluded it.
        # Filtering by `action` alone isolates that check correctly.
        marker = "ACTLOGMARKER_" + secrets.token_hex(6)
        self._seed_entry(db_engine, "delete_employee", seed_admin["username"], f"deleted ({marker})")
        _admin_session(client, seed_admin["username"])
        resp = client.get("/activity_log?action=update_salary")
        assert resp.status_code == 200
        assert marker.encode() not in resp.data

    def test_action_filter_includes_matching_entry(self, client, seed_admin, db_engine):
        marker = "ACTLOGMARKER_" + secrets.token_hex(6)
        self._seed_entry(db_engine, "delete_employee", seed_admin["username"], f"deleted ({marker})")
        _admin_session(client, seed_admin["username"])
        resp = client.get(f"/activity_log?q={marker}&action=delete_employee")
        assert resp.status_code == 200
        assert marker.encode() in resp.data


class TestSaveDefaultOnboardingTemplate:
    def test_saves_and_redirects(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_default_onboarding_template",
                           data={"default_onboarding_template_id": ""}, follow_redirects=False)
        assert resp.status_code == 302
        assert "/onboarding" in resp.headers["Location"]


class TestSaveSalaryRules:
    def test_invalid_values_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_salary_rules", data={
            "late_deduction_pct": "not-a-number",
        }, follow_redirects=True)
        assert b"Invalid values" in resp.data

    def test_valid_values_saved_globally(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_salary_rules", data={
            "late_deduction_pct": "15", "half_day_deduction_pct": "60",
            "grace_minutes": "10", "holiday_pay": "unpaid", "leave_pay": "absent",
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT holiday_pay, leave_pay FROM company_settings LIMIT 1")
        assert cur.fetchone() == ("unpaid", "absent")
        cur.close()
        # restore defaults so later tests relying on default config aren't affected
        cur = db_engine.cursor()
        cur.execute("UPDATE company_settings SET late_deduction_pct=10, half_day_deduction_pct=50, "
                    "grace_minutes=15, holiday_pay='paid', leave_pay='exclude'")
        cur.close()

    def test_valid_values_saved_scoped_to_company(self, client, seed_admin, temp_company, db_engine):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = temp_company
        resp = client.post("/save_salary_rules", data={
            "late_deduction_pct": "20", "half_day_deduction_pct": "40",
            "grace_minutes": "5", "holiday_pay": "paid", "leave_pay": "exclude",
            "shift_start": "09:00", "shift_half": "13:00", "shift_end": "18:00",
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT grace_minutes FROM company_feature_settings WHERE company_id=%s", (temp_company,))
        assert cur.fetchone()[0] == 5
        cur.close()


class TestToggleAuthMethod:
    def test_invalid_method_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_auth_method", data={"method": "bogus", "enabled": "1"},
                           follow_redirects=True)
        assert b"Invalid authentication method" in resp.data

    def test_valid_method_toggled_globally(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_auth_method", data={"method": "qr", "enabled": "0"},
                           follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT qr_enabled FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == 0
        cur.execute("UPDATE company_settings SET qr_enabled=1")
        cur.close()

    def test_valid_method_toggled_scoped_to_company(self, client, seed_admin, temp_company, db_engine):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = temp_company
        resp = client.post("/toggle_auth_method", data={"method": "face", "enabled": "0"},
                           follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT face_auth_enabled FROM company_feature_settings WHERE company_id=%s", (temp_company,))
        assert cur.fetchone()[0] == 0
        cur.close()


class TestToggleFingerprint:
    def test_toggled_globally(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_fingerprint", data={"enabled": "1"}, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT fingerprint_enabled FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == 1
        cur.execute("UPDATE company_settings SET fingerprint_enabled=0")
        cur.close()


class TestFeatureTogglesAreUnmetered:
    """Tiered plans (Basic/Medium/Prime/Starter/Growth/Enterprise) were
    retired in favor of a single flat per-employee rate -- see
    utils/plan_limits.py's module docstring: "every feature is available
    to every tenant regardless of headcount". blueprints/admin_views.py's
    toggle_auth_method/toggle_fingerprint/toggle_feature no longer contain
    any plan check at all (confirmed by reading the current source), so
    this now regression-guards the opposite of what it used to: a
    starter-plan tenant can enable every feature, including the ones a
    since-removed gate used to block. See tests/test_plan_limits.py for
    the pricing-math unit tests."""

    @pytest.fixture
    def as_starter(self, db_engine):
        import os
        schema = os.environ.get("DB_NAME")
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.tenants SET plan='starter' WHERE db_name=%s", (schema,))
        cur.close()
        yield
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.tenants SET plan='enterprise' WHERE db_name=%s", (schema,))
        cur.close()

    def test_starter_can_enable_face_via_toggle_auth_method(self, client, seed_admin, as_starter, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_auth_method", data={"method": "face", "enabled": "1"},
                           follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT face_enabled FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == 1
        cur.execute("UPDATE company_settings SET face_enabled=0")
        cur.close()

    def test_starter_allows_enabling_qr_via_toggle_auth_method(self, client, seed_admin, as_starter, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_auth_method", data={"method": "qr", "enabled": "1"},
                           follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT qr_enabled FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == 1
        cur.close()

    def test_starter_allows_disabling_face(self, client, seed_admin, as_starter):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_auth_method", data={"method": "face", "enabled": "0"},
                           follow_redirects=False)
        assert resp.status_code == 302

    def test_starter_can_enable_fingerprint_route(self, client, seed_admin, as_starter, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_fingerprint", data={"enabled": "1"}, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT fingerprint_enabled FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == 1
        cur.execute("UPDATE company_settings SET fingerprint_enabled=0")
        cur.close()

    def test_starter_can_enable_biometric_via_toggle_feature(self, client, seed_admin, as_starter, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_feature", json={"feature": "biometric_enabled", "value": True})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("UPDATE company_settings SET biometric_enabled=0")
        cur.close()


class TestSaveCompanyCode:
    def test_saves_uppercased_code(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_company_code", data={"company_code": "acme"}, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT company_code FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == "ACME"
        cur.execute("UPDATE company_settings SET company_code=''")
        cur.close()


class TestSaveCompanyInfo:
    def test_invalid_timezone_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_company_info", data={
            "company_name": "Acme", "timezone": "Not/ARealZone"}, follow_redirects=True)
        assert b"Invalid timezone" in resp.data

    def test_invalid_working_days_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_company_info", data={
            "company_name": "Acme", "timezone": "Asia/Kolkata", "working_days": "Notaday"},
            follow_redirects=True)
        assert b"Invalid working days" in resp.data

    def test_valid_info_saved(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_company_info", data={
            "company_name": "Acme Corp", "timezone": "Asia/Kolkata", "working_days": ["Mon", "Tue"]},
            follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT company_name, working_days FROM company_settings LIMIT 1")
        assert cur.fetchone() == ("Acme Corp", "Mon,Tue")
        cur.execute("UPDATE company_settings SET company_name='My Company', working_days='Mon,Tue,Wed,Thu,Fri'")
        cur.close()


class TestToggleFeature:
    def test_unknown_feature_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_feature", json={"feature": "not_a_real_feature", "value": True})
        assert resp.status_code == 400

    def test_valid_feature_toggled(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/toggle_feature", json={"feature": "notify_leave", "value": False})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("SELECT notify_leave FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == 0
        cur.execute("UPDATE company_settings SET notify_leave=1")
        cur.close()


class TestSaveGeoRadius:
    def test_out_of_range_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_geo_radius", data={"geo_radius": "99999"}, follow_redirects=True)
        assert b"between 50 and 5000" in resp.data

    def test_valid_radius_saved(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/save_geo_radius", data={"geo_radius": "250"}, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT geo_radius FROM company_settings LIMIT 1")
        assert cur.fetchone()[0] == 250
        cur.execute("UPDATE company_settings SET geo_radius=300")
        cur.close()


class TestSwitchCompany:
    def test_empty_cid_clears_active_company(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = 999
        resp = client.post("/switch_company", data={"company_id": ""}, follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "active_company_id" not in sess

    def test_non_numeric_cid_ignored(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/switch_company", data={"company_id": "abc"}, follow_redirects=False)
        assert resp.status_code == 302

    def test_unknown_cid_shows_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/switch_company", data={"company_id": "99999999"}, follow_redirects=True)
        assert b"Company not found" in resp.data

    def test_switch_without_pin_succeeds(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/switch_company", data={"company_id": str(temp_company)}, follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess["active_company_id"] == temp_company

    def test_switch_with_wrong_pin_rejected(self, client, seed_admin, temp_company, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE companies SET pin='1234' WHERE id=%s", (temp_company,))
        cur.close()
        _admin_session(client, seed_admin["username"])
        resp = client.post("/switch_company", data={"company_id": str(temp_company), "pin": "0000"},
                           follow_redirects=True)
        assert b"Incorrect PIN" in resp.data

    def test_switch_with_correct_pin_succeeds(self, client, seed_admin, temp_company, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE companies SET pin='1234' WHERE id=%s", (temp_company,))
        cur.close()
        _admin_session(client, seed_admin["username"])
        resp = client.post("/switch_company", data={"company_id": str(temp_company), "pin": "1234"},
                           follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess["active_company_id"] == temp_company


class TestClearCompany:
    def test_clears_active_company(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = 5
        resp = client.post("/clear_company", follow_redirects=False)
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "active_company_id" not in sess


class TestSetCompanyPin:
    def test_missing_cid_shows_error(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/set_company_pin", data={"company_id": ""}, follow_redirects=True)
        assert b"Invalid request" in resp.data

    def test_sets_pin(self, client, seed_admin, temp_company, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/set_company_pin", data={"company_id": str(temp_company), "pin": "5678"},
                           follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT pin FROM companies WHERE id=%s", (temp_company,))
        assert cur.fetchone()[0] == "5678"
        cur.close()

    def test_removes_pin(self, client, seed_admin, temp_company, db_engine):
        cur = db_engine.cursor()
        cur.execute("UPDATE companies SET pin='5678' WHERE id=%s", (temp_company,))
        cur.close()
        _admin_session(client, seed_admin["username"])
        resp = client.post("/set_company_pin", data={"company_id": str(temp_company), "pin": ""},
                           follow_redirects=True)
        assert b"removed" in resp.data


class TestViewCompaniesRedirect:
    def test_redirects_to_settings(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/companies", follow_redirects=False)
        assert resp.status_code == 302
        assert "tab=company" in resp.headers["Location"]


class TestAddCompany:
    def test_missing_name_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/companies/add", data={"name": ""}, follow_redirects=True)
        assert b"Company name is required" in resp.data

    def test_full_success_with_shifts_and_breaks(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/companies/add", data={
            "name": "New Test Co", "code": "ntc",
            "working_days": ["Mon", "Tue", "Wed"],
            "shift_name[]": ["Morning"], "shift_start[]": ["09:00"],
            "shift_half[]": ["13:00"], "shift_end[]": ["18:00"],
            "break_name[]": ["Lunch"], "break_time[]": ["13:00"], "break_duration[]": ["30"],
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM companies WHERE name='New Test Co'")
        cid = cur.fetchone()[0]
        try:
            cur.execute("SELECT COUNT(*) FROM shifts WHERE company_id=%s", (cid,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT COUNT(*) FROM break_config WHERE company_id=%s", (cid,))
            assert cur.fetchone()[0] == 1
        finally:
            cur.execute("DELETE FROM shifts WHERE company_id=%s", (cid,))
            cur.execute("DELETE FROM break_config WHERE company_id=%s", (cid,))
            cur.execute("DELETE FROM companies WHERE id=%s", (cid,))
            cur.close()


class TestEditCompany:
    def test_missing_name_rejected(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/edit", data={"name": ""}, follow_redirects=True)
        assert b"Company name is required" in resp.data

    def test_rename_without_code_change(self, client, seed_admin, temp_company, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{temp_company}/edit", data={
            "name": "Renamed Co", "code": "RTC"}, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM companies WHERE id=%s", (temp_company,))
        assert cur.fetchone()[0] == "Renamed Co"
        cur.close()

    def test_code_change_renames_matching_employee_ids(self, client, seed_admin, temp_company, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, company_id) VALUES (%s,%s,%s)",
            ("RTC001", "Rename Target", temp_company),
        )
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.post(f"/companies/{temp_company}/edit", data={
                "name": "Route Test Co", "code": "NEWCODE"}, follow_redirects=True)
            assert b"employee ID" in resp.data
            cur.execute("SELECT employee_id FROM employees WHERE name='Rename Target'")
            assert cur.fetchone()[0] == "NEWCODE001"
        finally:
            cur.execute("DELETE FROM employees WHERE name='Rename Target'")
            cur.close()


class TestDeleteCompany:
    def test_blocked_when_employees_assigned(self, client, seed_admin, temp_company, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, company_id) VALUES (%s,%s,%s)",
            ("RTCDEL1", "Blocker", temp_company),
        )
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.post(f"/companies/{temp_company}/delete", follow_redirects=True)
            assert b"Cannot delete" in resp.data
        finally:
            cur.execute("DELETE FROM employees WHERE employee_id='RTCDEL1'")
            cur.close()

    def test_succeeds_when_no_employees(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO companies (name) VALUES ('Deletable Co') RETURNING id")
        cid = cur.fetchone()[0]
        cur.close()
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/companies/{cid}/delete", follow_redirects=True)
        assert b"Company deleted" in resp.data


class TestAnnouncementsAdmin:
    def test_get_renders_own_page(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/announcements", follow_redirects=False)
        assert resp.status_code == 200
        assert b"panel-announcements" in resp.data

    def test_split_modules_have_own_routes(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        for url, panel in (("/tickets", b"panel-tickets"), ("/resignations", b"panel-resignations"),
                           ("/leave_holidays", b"panel-leaves")):
            resp = client.get(url, follow_redirects=False)
            assert resp.status_code == 200, url
            assert panel in resp.data, url
        # old tab links forward to the new routes
        for tab, url in (("announcements", "/announcements"), ("tickets", "/tickets"),
                         ("resignations", "/resignations")):
            resp = client.get(f"/leave_holidays?tab={tab}", follow_redirects=False)
            assert resp.status_code == 302 and resp.headers["Location"].endswith(url)

    def test_private_without_target_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/announcements", data={
            "action": "add", "visibility": "private", "title": "x", "content": "y"},
            follow_redirects=True)
        # Exact wording from admin_views.py's announcements_admin():
        # "Please select at least one employee for a private announcement."
        assert b"select at least one employee" in resp.data

    def test_public_announcement_posted_and_deleted(self, client, seed_admin, db_engine, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/announcements", data={
            "action": "add", "visibility": "public", "title": "Route Test Ann", "content": "hello everyone"},
            follow_redirects=True)
        assert b"Announcement posted" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM announcements WHERE title='Route Test Ann'")
        ann_id = cur.fetchone()[0]
        del_resp = client.post("/announcements", data={"action": "delete", "ann_id": str(ann_id)},
                               follow_redirects=True)
        assert b"Announcement deleted" in del_resp.data
        cur.execute("DELETE FROM notifications WHERE title LIKE '%Route Test Ann%'")
        cur.close()

    def test_private_announcement_targets_one_employee(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin["username"])
        # The real form (templates/leave_holidays.html) is a multi-select
        # <select name="target_employee_ids" multiple> -- the handler reads
        # it via request.form.getlist("target_employee_ids") (plural), one
        # announcement row created per selected target. A single-value
        # "target_employee_id" (as this test used to send) matches nothing,
        # so the request was silently treated as having zero targets.
        resp = client.post("/announcements", data={
            "action": "add", "visibility": "private", "title": "Private Ann",
            "target_employee_ids": [seed_employee["employee_id"]], "content": "just for you"},
            follow_redirects=True)
        assert b"Announcement posted" in resp.data
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM announcements WHERE title='Private Ann'")
        ann_id = cur.fetchone()[0]
        cur.execute("DELETE FROM announcements WHERE id=%s", (ann_id,))
        cur.execute("DELETE FROM notifications WHERE title LIKE '%Private Ann%'")
        cur.close()


class TestTestEmail:
    def test_no_config_returns_ok_false(self, client, seed_admin, monkeypatch):
        patch_package(monkeypatch, "blueprints.admin_views", "get_email_config", lambda: None)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/test_email", data={"test_to": "x@y.com"})
        assert resp.get_json()["ok"] is False

    def test_missing_recipient_returns_ok_false(self, client, seed_admin, monkeypatch):
        patch_package(monkeypatch, "blueprints.admin_views", "get_email_config", lambda: {"host": "x"})
        _admin_session(client, seed_admin["username"])
        resp = client.post("/test_email", data={"test_to": ""})
        assert resp.get_json()["ok"] is False

    def test_success(self, client, seed_admin, monkeypatch):
        patch_package(monkeypatch, "blueprints.admin_views", "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})
        patch_package(monkeypatch, "blueprints.admin_views", "send_email_smtp", lambda *a, **k: None)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/test_email", data={"test_to": "recipient@test.local"})
        assert resp.get_json()["ok"] is True

    def test_send_failure_returns_ok_false(self, client, seed_admin, monkeypatch):
        patch_package(monkeypatch, "blueprints.admin_views", "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})

        def _raise(*a, **k):
            raise RuntimeError("smtp down")
        patch_package(monkeypatch, "blueprints.admin_views", "send_email_smtp", _raise)
        _admin_session(client, seed_admin["username"])
        resp = client.post("/test_email", data={"test_to": "recipient@test.local"})
        assert resp.get_json()["ok"] is False


class TestApiExpiringDocuments:
    def test_returns_documents(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/admin/expiring_documents?days=30")
        assert resp.status_code == 200
        assert "documents" in resp.get_json()


class TestAdminTools:
    def test_default_tab(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/admin_tools")
        assert resp.status_code == 200

    def test_unknown_tab_param_still_renders(self, client, seed_admin):
        # Audit Logs tab was removed entirely (moved off the admin
        # dashboard); an old bookmarked URL with tab=audit_logs must not
        # error, it just shows the Org Chart tab like any other unknown
        # value would.
        _admin_session(client, seed_admin["username"])
        resp = client.get("/admin_tools?tab=audit_logs&actor=test&action=login&date=2026-01-01&page=1")
        assert resp.status_code == 200
        assert b"Audit Logs" not in resp.data

    def test_scoped_to_active_company(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = temp_company
        resp = client.get("/admin_tools")
        assert resp.status_code == 200

    def test_audit_logs_route_removed(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/audit_logs", follow_redirects=False)
        assert resp.status_code == 404


class TestDashboardLive:
    def test_returns_live_snapshot(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/dashboard_live")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "present" in data and "rows" in data

    def test_scoped_to_active_company(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = temp_company
        resp = client.get("/api/dashboard_live")
        assert resp.status_code == 200


class TestAttendanceChartData:
    def test_returns_chart_data(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/attendance_chart_data")
        assert resp.status_code == 200


class TestAnalyticsPage:
    def test_renders(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/analytics")
        assert resp.status_code == 200


class TestOrgChartPage:
    def test_renders(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/org_chart")
        assert resp.status_code == 200

    def test_scoped_to_active_company(self, client, seed_admin, temp_company):
        _admin_session(client, seed_admin["username"])
        with client.session_transaction() as sess:
            sess["active_company_id"] = temp_company
        resp = client.get("/org_chart")
        assert resp.status_code == 200


class TestApiAdminSearch:
    def test_short_query_returns_empty(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/admin/search?q=a")
        assert resp.get_json()["results"] == []

    def test_finds_ticket_and_leave_matches(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin["username"])
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO tickets (employee_id, subject, category, status, description) VALUES (%s,%s,%s,%s,%s)",
            (seed_employee["employee_id"], "UniqueSearchSubjectXYZ", "General", "Open", "test description"),
        )
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) VALUES (%s,%s,%s,%s)",
            (seed_employee["employee_id"], "2026-05-01", "UniqueSearchReasonXYZ", "Pending"),
        )
        try:
            resp = client.get("/api/admin/search?q=UniqueSearchSubjectXYZ")
            types = [r["type"] for r in resp.get_json()["results"]]
            assert "ticket" in types

            resp2 = client.get("/api/admin/search?q=UniqueSearchReasonXYZ")
            types2 = [r["type"] for r in resp2.get_json()["results"]]
            assert "leave" in types2
        finally:
            cur.execute("DELETE FROM tickets WHERE subject='UniqueSearchSubjectXYZ'")
            cur.execute("DELETE FROM leave_requests WHERE reason='UniqueSearchReasonXYZ'")
            cur.close()


class TestApiOrgChartData:
    def test_builds_tree_with_manager_hierarchy(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, role, department, is_active) VALUES (%s,%s,%s,%s,1)",
            ("ORGMGR1", "Manager One", "Manager", "Sales"),
        )
        cur.execute(
            "INSERT INTO employees (employee_id, name, role, department, manager_id, is_active) "
            "VALUES (%s,%s,%s,%s,%s,1)",
            ("ORGRPT1", "Report One", "Rep", "Sales", "ORGMGR1"),
        )
        _admin_session(client, seed_admin["username"])
        try:
            resp = client.get("/api/org_chart_data")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "tree" in data
        finally:
            cur.execute("DELETE FROM employees WHERE employee_id IN ('ORGRPT1','ORGMGR1')")
            cur.close()

    def test_filters_by_department(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/org_chart_data?dept=NoSuchDept")
        assert resp.status_code == 200


class TestNotificationBellClear:
    def test_admin_clear_deletes_admin_feed_only(self, client, seed_admin, db_engine, seed_employee):
        _admin_session(client, seed_admin["username"])
        conn = db_engine
        cur = conn.cursor()
        cur.execute("INSERT INTO notifications (recipient_type, title, message) VALUES ('admin','clr-a','x')")
        cur.execute("INSERT INTO notifications (recipient_type, employee_id, title, message) "
                    "VALUES ('employee', %s, 'clr-e', 'x')", (seed_employee["employee_id"],))
        conn.commit()
        resp = client.post("/web/admin/notifications/clear")
        assert resp.status_code == 200 and resp.get_json()["ok"] is True
        cur.execute("SELECT COUNT(*) FROM notifications WHERE recipient_type='admin' AND title='clr-a'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM notifications WHERE recipient_type='employee' AND title='clr-e'")
        assert cur.fetchone()[0] == 1
        cur.execute("DELETE FROM notifications WHERE title='clr-e'")
        conn.commit()
