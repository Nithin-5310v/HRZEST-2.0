"""
Tests for the Bearer-token routes added to close out the "pending/incomplete
work" inventory: employee self-service (profile/bank details/experience/
education), employee-facing onboarding + performance views, and admin
payroll lock/unlock + holiday delete.
"""
import datetime
import pytest


def _admin_session(client, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"], "password": seed_admin["password"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("admin_logged_in")
    return resp


def _admin_token(client, seed_admin):
    return client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"],
    }).get_json()["token"]


def _emp_token(client, seed_employee):
    return client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"], "password": seed_employee["password"],
    }).get_json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def other_employee(db_engine):
    """A second real employee row -- employee_experience/employee_onboarding/
    performance_reviews all carry a real FK to employees.employee_id now, so
    an "ownership" test needs a genuine other row, not just an arbitrary
    string."""
    from utils.auth import generate_password_hash
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
        "VALUES ('OTHR001','Other Employee','other@test.local',%s,0) ON CONFLICT (employee_id) DO NOTHING",
        (generate_password_hash("Other@1234"),),
    )
    cur.close()
    yield "OTHR001"
    cur = db_engine.cursor()
    cur.execute("DELETE FROM employees WHERE employee_id='OTHR001'")
    cur.close()


# ── Employee self-service ────────────────────────────────────────────────────

class TestEmployeeProfileApi:
    def test_update_profile_round_trips_and_preserves_other_fields(self, client, seed_employee, db_engine):
        token = _emp_token(client, seed_employee)
        # Seed a bank field first -- the profile update must not wipe it.
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET bank_name='Preserve Bank' WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

        resp = client.post("/api/employee/profile", json={
            "phone": "9998887777", "gender": "Female", "dob": "1995-05-05",
            "blood_group": "O+", "address": "123 Test St", "city": "Chennai",
            "state": "TN", "pincode": "600001",
            "emergency_contact_name": "Jane Doe", "emergency_contact_phone": "9991112222",
            "emergency_contact_relation": "Sister", "about_me": "Hello",
        }, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        prof = client.get("/api/employee/profile", headers=_auth(token)).get_json()["profile"]
        assert prof["phone"] == "9998887777"
        assert prof["city"] == "Chennai"
        assert prof["emergency_contact_relation"] == "Sister"
        assert prof["bank_name"] == "Preserve Bank"  # untouched by the profile update

    def test_update_bank_details(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/bank_details", json={
            "aadhar_number": "123456789012", "pan_number": "abcde1234f",
            "bank_name": "Test Bank", "bank_account": "000111222", "bank_ifsc": "test0001", "uan_number": "UAN123",
        }, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        prof = client.get("/api/employee/profile", headers=_auth(token)).get_json()["profile"]
        assert prof["bank_name"] == "Test Bank"
        assert prof["pan_number"] == "ABCDE1234F"  # uppercased
        assert prof["uan_number"] == "UAN123"

    def test_requires_bearer_token(self, client):
        resp = client.get("/api/employee/experience")
        assert resp.status_code == 401


class TestEmployeeExperienceApi:
    def test_add_list_delete_round_trip(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        add = client.post("/api/employee/experience", json={
            "company": "Acme Corp", "designation": "Engineer",
            "from_year": "2019", "to_year": "2022", "is_current": False, "description": "Built things",
        }, headers=_auth(token))
        assert add.status_code == 200 and add.get_json()["ok"] is True

        listing = client.get("/api/employee/experience", headers=_auth(token)).get_json()
        assert listing["ok"] is True
        assert len(listing["experience"]) == 1
        entry = listing["experience"][0]
        assert entry["company"] == "Acme Corp"

        delete = client.delete(f"/api/employee/experience/{entry['id']}", headers=_auth(token))
        assert delete.status_code == 200 and delete.get_json()["ok"] is True

        listing2 = client.get("/api/employee/experience", headers=_auth(token)).get_json()
        assert listing2["experience"] == []

    def test_add_requires_fields(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/experience", json={"company": "Acme"}, headers=_auth(token))
        assert resp.status_code == 400

    def test_delete_someone_elses_entry_is_404(self, client, seed_employee, other_employee, db_engine):
        token = _emp_token(client, seed_employee)
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employee_experience (employee_id, company, designation, from_year) "
            "VALUES (%s,'X','Y','2020') RETURNING id",
            (other_employee,),
        )
        other_id = cur.fetchone()[0]
        cur.close()
        try:
            resp = client.delete(f"/api/employee/experience/{other_id}", headers=_auth(token))
            assert resp.status_code == 404
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM employee_experience WHERE id=%s", (other_id,))
            cur.close()


class TestEmployeeEducationApi:
    def test_add_list_delete_round_trip(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        add = client.post("/api/employee/education", json={
            "degree": "B.Tech", "institution": "Test University", "year_of_passing": "2018", "percentage": "82.5",
        }, headers=_auth(token))
        assert add.status_code == 200 and add.get_json()["ok"] is True

        listing = client.get("/api/employee/education", headers=_auth(token)).get_json()
        assert len(listing["education"]) == 1
        entry = listing["education"][0]

        delete = client.delete(f"/api/employee/education/{entry['id']}", headers=_auth(token))
        assert delete.status_code == 200

        listing2 = client.get("/api/employee/education", headers=_auth(token)).get_json()
        assert listing2["education"] == []


# ── Employee onboarding / performance views ──────────────────────────────────

def _make_template(db_engine, name="Pending-Work Template"):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO onboarding_templates (name, description, is_active) VALUES (%s,%s,1) RETURNING id",
        (name, "test"),
    )
    tid = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO onboarding_template_tasks (template_id, task_title, task_description, requires_document, due_days, sort_order) "
        "VALUES (%s,%s,%s,0,7,0) RETURNING id",
        (tid, "Sign NDA", ""),
    )
    task_id = cur.fetchone()[0]
    cur.close()
    return tid, task_id


def _assign_onboarding(db_engine, emp_id, template_id, task_id):
    cur = db_engine.cursor()
    today = datetime.date.today()
    cur.execute(
        "INSERT INTO employee_onboarding (employee_id, template_id, assigned_date, due_date, status) "
        "VALUES (%s,%s,%s,%s,'In Progress') RETURNING id",
        (emp_id, template_id, today, today + datetime.timedelta(days=30)),
    )
    ob_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO employee_onboarding_tasks (onboarding_id, template_task_id, employee_id, "
        "task_title, task_description, requires_document, due_days, status) "
        "VALUES (%s,%s,%s,'Sign NDA','',0,7,'Pending') RETURNING id",
        (ob_id, task_id, emp_id),
    )
    task_row_id = cur.fetchone()[0]
    cur.close()
    return ob_id, task_row_id


def _cleanup_onboarding(db_engine, template_id, ob_id):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM employee_onboarding_tasks WHERE onboarding_id=%s", (ob_id,))
    cur.execute("DELETE FROM employee_onboarding WHERE id=%s", (ob_id,))
    cur.execute("DELETE FROM onboarding_template_tasks WHERE template_id=%s", (template_id,))
    cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (template_id,))
    cur.close()


class TestEmployeeOnboardingApi:
    def test_fetch_and_complete_task(self, client, seed_employee, db_engine):
        template_id, task_id = _make_template(db_engine)
        ob_id, task_row_id = _assign_onboarding(db_engine, seed_employee["employee_id"], template_id, task_id)
        try:
            token = _emp_token(client, seed_employee)
            res = client.get("/api/employee/onboarding", headers=_auth(token)).get_json()
            assert res["ok"] is True
            assert res["selected_ob_id"] == ob_id
            assert len(res["tasks"]) == 1
            assert res["tasks"][0]["status"] == "Pending"

            done = client.post(
                f"/api/employee/onboarding/task/{task_row_id}/done",
                data={"ob_id": str(ob_id), "employee_note": "done via test"},
                headers=_auth(token),
            )
            assert done.status_code == 200
            body = done.get_json()
            assert body["ok"] is True
            assert body["remaining"] == 0

            res2 = client.get("/api/employee/onboarding", headers=_auth(token)).get_json()
            assert res2["tasks"][0]["status"] == "Done"

            cur = db_engine.cursor()
            cur.execute("SELECT status FROM employee_onboarding WHERE id=%s", (ob_id,))
            assert cur.fetchone()[0] == "Completed"
            cur.close()
        finally:
            _cleanup_onboarding(db_engine, template_id, ob_id)

    def test_complete_with_valid_document_stores_private_ref(self, client, seed_employee, db_engine):
        """API twin of the same security fix covered in
        tests/test_onboarding.py's TestMyOnboardingTaskDocumentUpload --
        document_path must be a save_private() stored_ref, never the old
        bare-filename-under-static/ format."""
        import io
        template_id, task_id = _make_template(db_engine, "Doc Upload Template")
        ob_id, task_row_id = _assign_onboarding(db_engine, seed_employee["employee_id"], template_id, task_id)
        try:
            token = _emp_token(client, seed_employee)
            resp = client.post(
                f"/api/employee/onboarding/task/{task_row_id}/done",
                data={"ob_id": str(ob_id), "document": (io.BytesIO(b"%PDF-1.4 fake pdf content"), "cert.pdf")},
                content_type="multipart/form-data",
                headers=_auth(token),
            )
            assert resp.status_code == 200
            cur = db_engine.cursor()
            cur.execute("SELECT document_path FROM employee_onboarding_tasks WHERE id=%s", (task_row_id,))
            document_path = cur.fetchone()[0]
            cur.close()
            assert document_path is not None
            assert "static" not in document_path.replace("\\", "/").split("/")
        finally:
            _cleanup_onboarding(db_engine, template_id, ob_id)

    def test_complete_with_disallowed_file_type_rejected(self, client, seed_employee, db_engine):
        import io
        template_id, task_id = _make_template(db_engine, "Doc Reject Template")
        ob_id, task_row_id = _assign_onboarding(db_engine, seed_employee["employee_id"], template_id, task_id)
        try:
            token = _emp_token(client, seed_employee)
            resp = client.post(
                f"/api/employee/onboarding/task/{task_row_id}/done",
                data={"ob_id": str(ob_id), "document": (io.BytesIO(b"MZ\x90\x00fake exe"), "payload.exe")},
                content_type="multipart/form-data",
                headers=_auth(token),
            )
            assert resp.status_code == 400
            cur = db_engine.cursor()
            cur.execute("SELECT status FROM employee_onboarding_tasks WHERE id=%s", (task_row_id,))
            assert cur.fetchone()[0] == "Pending"
            cur.close()
        finally:
            _cleanup_onboarding(db_engine, template_id, ob_id)

    def test_complete_someone_elses_task_is_forbidden(self, client, seed_employee, other_employee, db_engine):
        template_id, task_id = _make_template(db_engine, "Other Emp Template")
        ob_id, task_row_id = _assign_onboarding(db_engine, other_employee, template_id, task_id)
        try:
            token = _emp_token(client, seed_employee)
            resp = client.post(
                f"/api/employee/onboarding/task/{task_row_id}/done",
                data={"ob_id": str(ob_id)},
                headers=_auth(token),
            )
            assert resp.status_code == 403
        finally:
            _cleanup_onboarding(db_engine, template_id, ob_id)


class TestEmployeePerformanceApi:
    def test_fetch_own_reviews_and_submit_comment(self, client, seed_employee, db_engine):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO performance_reviews (employee_id, quarter, year, overall_rating, reviewer_feedback, status) "
            "VALUES (%s,1,2026,4,'Great work','Finalized') RETURNING id",
            (emp_id,),
        )
        rev_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO performance_kpis (review_id, kpi_title, target, achievement, weight, rating) "
            "VALUES (%s,'Delivery','10','12',1.0,5)",
            (rev_id,),
        )
        cur.close()
        try:
            token = _emp_token(client, seed_employee)
            res = client.get("/api/employee/performance", headers=_auth(token)).get_json()
            assert res["ok"] is True
            assert len(res["reviews"]) == 1
            review = res["reviews"][0]
            assert review["overall_rating_label"] == "Exceeds Expectations"
            assert len(review["kpis"]) == 1

            comment = client.post("/api/employee/performance/comment", json={
                "review_id": rev_id, "comment": "Thanks!",
            }, headers=_auth(token))
            assert comment.status_code == 200 and comment.get_json()["ok"] is True

            cur = db_engine.cursor()
            cur.execute("SELECT employee_comment FROM performance_reviews WHERE id=%s", (rev_id,))
            assert cur.fetchone()[0] == "Thanks!"
            cur.close()
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM performance_kpis WHERE review_id=%s", (rev_id,))
            cur.execute("DELETE FROM performance_reviews WHERE id=%s", (rev_id,))
            cur.close()

    def test_cannot_comment_on_someone_elses_review(self, client, seed_employee, other_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO performance_reviews (employee_id, quarter, year, status) "
            "VALUES (%s,1,2026,'Finalized') RETURNING id",
            (other_employee,),
        )
        rev_id = cur.fetchone()[0]
        cur.close()
        try:
            token = _emp_token(client, seed_employee)
            resp = client.post("/api/employee/performance/comment", json={
                "review_id": rev_id, "comment": "should not apply",
            }, headers=_auth(token))
            assert resp.status_code == 404
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM performance_reviews WHERE id=%s", (rev_id,))
            cur.close()


# ── Admin: payroll lock/unlock, holiday delete ───────────────────────────────

class TestPayrollLockApi:
    def test_lock_status_unlock_round_trip(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        year, month = 2031, 6  # a month no other test touches
        try:
            status = client.get(f"/api/payroll/status?year={year}&month={month}", headers=_auth(token))
            assert status.get_json()["locked"] is False

            lock = client.post("/api/payroll/lock", json={"year": year, "month": month}, headers=_auth(token))
            assert lock.status_code == 200 and lock.get_json()["ok"] is True

            status2 = client.get(f"/api/payroll/status?year={year}&month={month}", headers=_auth(token))
            assert status2.get_json()["locked"] is True

            unlock = client.post("/api/payroll/unlock", json={"year": year, "month": month}, headers=_auth(token))
            assert unlock.status_code == 200 and unlock.get_json()["ok"] is True

            status3 = client.get(f"/api/payroll/status?year={year}&month={month}", headers=_auth(token))
            assert status3.get_json()["locked"] is False
        finally:
            cur = db_engine.cursor()
            cur.execute("DELETE FROM payroll_runs WHERE year=%s AND month=%s", (year, month))
            cur.close()

    def test_lock_requires_year_and_month(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/payroll/lock", json={}, headers=_auth(token))
        assert resp.status_code == 400


class TestHolidayDeleteApi:
    def test_add_then_delete_holiday(self, client, seed_admin, db_engine):
        # Self-healing: if a previous run of this same test crashed or was
        # interrupted between the add and delete steps below, the
        # '2031-03-03' row is left behind -- and since the add step
        # asserts success before the delete step ever runs, that stale row
        # would otherwise fail this exact assertion on every subsequent
        # run forever (the test could never reach its own cleanup step).
        cur = db_engine.cursor()
        cur.execute("DELETE FROM holidays WHERE date='2031-03-03'")
        cur.close()

        token = _admin_token(client, seed_admin)
        add = client.post("/api/holidays", json={"date": "2031-03-03", "name": "Pending-Work Test Holiday"}, headers=_auth(token))
        assert add.status_code == 200 and add.get_json()["ok"] is True

        listing = client.get("/api/holidays", headers=_auth(token)).get_json()
        entry = next((h for h in listing["holidays"] if h["date"] == "2031-03-03"), None)
        assert entry is not None
        assert "id" in entry

        delete = client.delete(f"/api/holidays/{entry['id']}", headers=_auth(token))
        assert delete.status_code == 200 and delete.get_json()["ok"] is True

        listing2 = client.get("/api/holidays", headers=_auth(token)).get_json()
        assert all(h["date"] != "2031-03-03" for h in listing2["holidays"])

    def test_delete_unknown_holiday_is_404(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.delete("/api/holidays/999999999", headers=_auth(token))
        assert resp.status_code == 404


class TestSalaryRulesApi:
    def test_save_salary_rules_round_trips(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute(
            "SELECT late_deduction_pct, half_day_deduction_pct, grace_minutes, holiday_pay, leave_pay "
            "FROM company_settings LIMIT 1"
        )
        original = cur.fetchone()
        cur.close()
        try:
            resp = client.post("/api/settings/salary_rules", json={
                "late_deduction_pct": 15, "half_day_deduction_pct": 60,
                "grace_minutes": 20, "holiday_pay": "unpaid", "leave_pay": "absent",
            }, headers=_auth(token))
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

            settings = client.get("/api/settings", headers=_auth(token)).get_json()["settings"]
            assert float(settings["late_deduction_pct"]) == 15
            assert float(settings["half_day_deduction_pct"]) == 60
            assert int(settings["grace_minutes"]) == 20
            assert settings["holiday_pay"] == "unpaid"
            assert settings["leave_pay"] == "absent"
        finally:
            if original:
                cur = db_engine.cursor()
                cur.execute(
                    "UPDATE company_settings SET late_deduction_pct=%s, half_day_deduction_pct=%s, "
                    "grace_minutes=%s, holiday_pay=%s, leave_pay=%s",
                    original,
                )
                cur.close()

    def test_save_salary_rules_clamps_out_of_range_percentages(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute(
            "SELECT late_deduction_pct, half_day_deduction_pct, grace_minutes, holiday_pay, leave_pay "
            "FROM company_settings LIMIT 1"
        )
        original = cur.fetchone()
        cur.close()
        try:
            resp = client.post("/api/settings/salary_rules", json={
                "late_deduction_pct": 500, "half_day_deduction_pct": -10,
                "grace_minutes": 999, "holiday_pay": "bogus", "leave_pay": "bogus",
            }, headers=_auth(token))
            assert resp.status_code == 200
            settings = client.get("/api/settings", headers=_auth(token)).get_json()["settings"]
            assert float(settings["late_deduction_pct"]) == 100
            assert float(settings["half_day_deduction_pct"]) == 0
            assert int(settings["grace_minutes"]) == 120
            assert settings["holiday_pay"] == "paid"
            assert settings["leave_pay"] == "exclude"
        finally:
            if original:
                cur = db_engine.cursor()
                cur.execute(
                    "UPDATE company_settings SET late_deduction_pct=%s, half_day_deduction_pct=%s, "
                    "grace_minutes=%s, holiday_pay=%s, leave_pay=%s",
                    original,
                )
                cur.close()

    def test_save_salary_rules_requires_admin_token(self, client, seed_employee):
        # An employee Bearer token isn't a valid admin token at all here
        # (api_required, not employee_api_required) -- rejected at 401
        # before role is even checked, same as the other admin-only routes.
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/settings/salary_rules", json={
            "late_deduction_pct": 15, "half_day_deduction_pct": 60,
            "grace_minutes": 20, "holiday_pay": "unpaid", "leave_pay": "absent",
        }, headers=_auth(token))
        assert resp.status_code == 401


class TestSalaryReportExportApi:
    def test_export_returns_base64_xlsx(self, client, seed_admin):
        import base64
        import datetime as _dt
        token = _admin_token(client, seed_admin)
        today = _dt.date.today()
        resp = client.get(
            f"/api/payroll/salary_report_export?year={today.year}&month={today.month}",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["filename"].endswith(".xlsx")
        assert data["mime_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        raw = base64.b64decode(data["content_base64"])
        assert raw[:2] == b"PK"  # xlsx is a zip archive

    def test_export_requires_admin_token(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.get("/api/payroll/salary_report_export", headers=_auth(token))
        assert resp.status_code == 401


class TestEmailBlastApi:
    def test_blast_to_all_via_bearer_token(self, client, seed_admin, seed_employee, db_engine):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/admin/email-blast", json={
            "target_type": "all", "subject": "Pending-Work Test Blast", "body": "Hello everyone",
        }, headers=_auth(token))
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["ok"] is True
        assert data["queued_count"] >= 1

        cur = db_engine.cursor()
        cur.execute("SELECT sender_username FROM broadcast_emails WHERE subject=%s ORDER BY id DESC LIMIT 1",
                    ("Pending-Work Test Blast",))
        row = cur.fetchone()
        cur.close()
        assert row is not None
        assert row[0] == seed_admin["username"]

    def test_blast_requires_auth(self, client):
        resp = client.post("/api/admin/email-blast", json={
            "target_type": "all", "subject": "x", "body": "y",
        })
        assert resp.status_code == 401

    def test_blast_rejects_employee_token(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/admin/email-blast", json={
            "target_type": "all", "subject": "x", "body": "y",
        }, headers=_auth(token))
        assert resp.status_code == 401

    def test_blast_missing_fields_rejected(self, client, seed_admin):
        token = _admin_token(client, seed_admin)
        resp = client.post("/api/admin/email-blast", json={"target_type": "all"}, headers=_auth(token))
        assert resp.status_code == 400


class TestNotificationPreferencesApi:
    def test_round_trips_and_reflects_in_profile(self, client, seed_employee):
        token = _emp_token(client, seed_employee)
        resp = client.post("/api/employee/notification_preferences", json={"email_alerts_enabled": False},
                           headers=_auth(token))
        assert resp.status_code == 200
        assert resp.get_json()["email_alerts_enabled"] is False

        prof = client.get("/api/employee/profile", headers=_auth(token)).get_json()["profile"]
        assert prof["email_alerts_enabled"] is False

        resp2 = client.post("/api/employee/notification_preferences", json={"email_alerts_enabled": True},
                            headers=_auth(token))
        assert resp2.get_json()["email_alerts_enabled"] is True

    def test_leave_approval_skips_email_when_disabled(self, client, seed_admin, seed_employee, db_engine, monkeypatch):
        import blueprints.leave as leave_mod
        monkeypatch.setattr(leave_mod, "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})

        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email_alerts_enabled=0, email='alerts-off@test.local' WHERE employee_id=%s", (emp_id,))
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) VALUES (%s, '2031-04-01', 'test', 'Pending') RETURNING id",
            (emp_id,))
        lid = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"

        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM email_queue WHERE to_email='alerts-off@test.local'")
        before = cur.fetchone()[0]
        cur.close()

        resp = client.post(f"/leave_action/{lid}", data={"action": "Approved"}, follow_redirects=True)
        assert resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM email_queue WHERE to_email='alerts-off@test.local'")
        after = cur.fetchone()[0]
        cur.close()
        assert after == before  # no new email queued -- alerts disabled

    def test_leave_approval_sends_email_when_enabled(self, client, seed_admin, seed_employee, db_engine, monkeypatch):
        import blueprints.leave as leave_mod
        monkeypatch.setattr(leave_mod, "get_email_config", lambda: {
            "host": "x", "port": 587, "user": "u", "password": "p", "from_name": "N", "from_email": "u@x.com"})

        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET email_alerts_enabled=1, email='alerts-on@test.local' WHERE employee_id=%s", (emp_id,))
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) VALUES (%s, '2031-04-02', 'test', 'Pending') RETURNING id",
            (emp_id,))
        lid = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"

        resp = client.post(f"/leave_action/{lid}", data={"action": "Approved"}, follow_redirects=True)
        assert resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM email_queue WHERE to_email='alerts-on@test.local'")
        count = cur.fetchone()[0]
        cur.close()
        assert count >= 1
