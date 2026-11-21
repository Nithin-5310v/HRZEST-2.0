"""Tests for blueprints/daily_report.py -- the nightly attendance summary
email (scheduled via APScheduler at 23:59, see wsgi.py), the weekly
employee digest, and their two manual-trigger admin routes.

No SMTP is configured in this dev environment, so every test monkeypatches
the module-level send_email_async/get_email_config/get_admin_emails
bindings inside blueprints.daily_report -- the exact same "monkeypatch the
names imported into the blueprint module" pattern
tests/test_employee_welcome_email.py uses for blueprints.employees'
get_email_config/send_email_smtp, rather than opening a real SMTP
connection or depending on env-configured SMTP.
"""
import datetime

import blueprints.daily_report as daily_report_module


def _admin_session(client, seed_admin):
    resp = client.post("/login", data={
        "identifier": seed_admin["username"], "password": seed_admin["password"],
    }, follow_redirects=True)
    assert resp.status_code == 200
    return client


class _SyncThread:
    """Runs the target synchronously instead of on a real background thread
    -- same pattern tests/test_email_utils.py uses for send_email_async's
    fallback thread -- so the two manual-trigger routes below can assert on
    what got invoked without a real thread race or a sleep-and-poll loop."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


# ── _build_email_html (pure function, no DB) ─────────────────────────────────

class TestBuildEmailHtml:
    def test_zero_employees_no_division_by_zero(self):
        html = daily_report_module._build_email_html("Monday, 01 January 2026", {
            "total": 0, "present": 0, "absent": 0, "late": 0,
            "on_leave": 0, "pending_leaves": 0, "rows": [],
        })
        assert "0%" in html
        assert "Daily Attendance Report" in html
        assert "Monday, 01 January 2026" in html

    def test_all_present_shows_full_percentage_and_present_color(self):
        rows = [{"employee_id": "E1", "name": "Alice", "status": "Present",
                 "login_time": "09:00", "logout_time": "18:00"}]
        html = daily_report_module._build_email_html("Today", {
            "total": 1, "present": 1, "absent": 0, "late": 0,
            "on_leave": 0, "pending_leaves": 0, "rows": rows,
        })
        assert "100.0%" in html
        assert "Alice" in html
        assert "#16A34A" in html  # Present status color
        assert "09:00" in html and "18:00" in html

    def test_all_absent_shows_zero_percentage_and_absent_color(self):
        rows = [{"employee_id": "E1", "name": "Bob", "status": "Absent"}]
        html = daily_report_module._build_email_html("Today", {
            "total": 1, "present": 0, "absent": 1, "late": 0,
            "on_leave": 0, "pending_leaves": 0, "rows": rows,
        })
        assert "0.0%" in html
        assert "Bob" in html
        assert "#DC2626" in html  # Absent status color
        assert "--" in html  # missing login/logout time renders as "--"

    def test_pending_leave_banner_shown_with_plural_wording(self):
        html = daily_report_module._build_email_html("Today", {
            "total": 0, "present": 0, "absent": 0, "late": 0,
            "on_leave": 0, "pending_leaves": 3, "rows": [],
        })
        assert "3 leave requests pending approval" in html

    def test_pending_leave_banner_uses_singular_wording_for_one(self):
        html = daily_report_module._build_email_html("Today", {
            "total": 0, "present": 0, "absent": 0, "late": 0,
            "on_leave": 0, "pending_leaves": 1, "rows": [],
        })
        assert "1 leave request pending approval" in html
        assert "1 leave requests" not in html

    def test_no_pending_leave_banner_when_zero(self):
        html = daily_report_module._build_email_html("Today", {
            "total": 0, "present": 0, "absent": 0, "late": 0,
            "on_leave": 0, "pending_leaves": 0, "rows": [],
        })
        assert "pending approval" not in html

    def test_more_than_thirty_rows_caps_table_and_shows_truncation_notice(self):
        rows = [{"employee_id": f"E{i}", "name": f"Emp {i}", "status": "Present"} for i in range(35)]
        html = daily_report_module._build_email_html("Today", {
            "total": 35, "present": 35, "absent": 0, "late": 0,
            "on_leave": 0, "pending_leaves": 0, "rows": rows,
        })
        assert "Showing first 30 employees" in html
        assert "Emp 29" in html       # last row within the cap
        assert "Emp 30" not in html  # first row past the cap

    def test_exactly_thirty_rows_shows_no_truncation_notice(self):
        rows = [{"employee_id": f"E{i}", "name": f"Emp {i}", "status": "Present"} for i in range(30)]
        html = daily_report_module._build_email_html("Today", {
            "total": 30, "present": 30, "absent": 0, "late": 0,
            "on_leave": 0, "pending_leaves": 0, "rows": rows,
        })
        assert "Showing first 30 employees" not in html

    def test_unmapped_status_falls_back_to_default_color(self):
        rows = [{"employee_id": "E1", "name": "Carol", "status": "On Leave"}]
        html = daily_report_module._build_email_html("Today", {
            "total": 1, "present": 0, "absent": 0, "late": 0,
            "on_leave": 1, "pending_leaves": 0, "rows": rows,
        })
        assert "#64748B" in html  # default/unmapped status color


# ── generate_and_send_daily_report() / _run_report() ─────────────────────────

class TestGenerateAndSendDailyReport:
    def test_computes_correct_stats_and_sends_to_admins(self, db_engine, seed_admin, monkeypatch):
        today = datetime.date.today()
        emp_present = "DRPT_PRES"
        emp_absent = "DRPT_ABS"
        emp_leave = "DRPT_LEAVE"
        cur = db_engine.cursor()
        try:
            for eid, name in ((emp_present, "Present Guy"), (emp_absent, "Absent Guy"), (emp_leave, "Leave Guy")):
                cur.execute(
                    "INSERT INTO employees (employee_id, name) VALUES (%s,%s) "
                    "ON CONFLICT (employee_id) DO NOTHING", (eid, name),
                )
            cur.execute(
                "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (employee_id, date) DO NOTHING",
                (emp_present, today, "09:15:00", "Present"),
            )
            cur.execute(
                "INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
                "VALUES (%s,%s,%s,'Approved')", (emp_leave, today, "vacation"),
            )
            cur.execute(
                "INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
                "VALUES (%s,%s,%s,'Pending')", (emp_absent, today, "still deciding"),
            )
            # get_pending_counts() (which stats["pending_leaves"] comes from,
            # via blueprints/daily_report.py) is cached for 30s -- without
            # invalidating it here, a call from an earlier test elsewhere in
            # the suite can leave a stale count in place and this assertion
            # sees that stale value instead of the row just inserted above.
            from utils.helpers import invalidate_pending_counts_cache
            invalidate_pending_counts_cache()

            # Capture the stats dict passed to _build_email_html instead of
            # asserting on hardcoded totals -- att_test is a persistent,
            # shared-across-the-suite DB, so the *real* employee/leave
            # counts depend on whatever else is in it. Recomputing
            # "expected" with the same queries right after seeding makes
            # this robust to that, while still exercising _run_report()'s
            # real counting logic end to end.
            captured = {}
            _orig_build = daily_report_module._build_email_html

            def _capture(date_str, stats):
                captured["stats"] = stats
                return _orig_build(date_str, stats)

            monkeypatch.setattr(daily_report_module, "_build_email_html", _capture)

            sent = []
            monkeypatch.setattr(daily_report_module, "get_email_config", lambda: {"host": "smtp.test"})
            monkeypatch.setattr(daily_report_module, "send_email_async",
                                lambda to, subject, html, cfg: sent.append((to, subject, html)))

            daily_report_module.generate_and_send_daily_report()

            cur.execute("SELECT COUNT(*) FROM employees")
            expected_total = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND login_time IS NOT NULL",
                (today,),
            )
            expected_present = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM leave_requests WHERE leave_date=%s AND status='Approved'", (today,),
            )
            expected_on_leave = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM leave_requests WHERE status='Pending'")
            expected_pending = cur.fetchone()[0]

            stats = captured["stats"]
            assert stats["total"] == expected_total
            assert stats["present"] == expected_present
            assert stats["on_leave"] == expected_on_leave
            assert stats["pending_leaves"] == expected_pending
            assert stats["absent"] == max(0, expected_total - expected_present - expected_on_leave)

            rows_by_id = {r["employee_id"]: r for r in stats["rows"]}
            assert rows_by_id[emp_present]["status"] == "Present"
            assert rows_by_id[emp_leave]["status"] == "On Leave"
            assert rows_by_id[emp_absent]["status"] == "Absent"

            assert len(sent) >= 1
            assert any(to == "admin@test.local" for to, _subj, _html in sent)
            sent_html = next(html for to, _subj, html in sent if to == "admin@test.local")
            assert emp_present in sent_html
        finally:
            cur.execute("DELETE FROM leave_requests WHERE employee_id IN (%s,%s)", (emp_leave, emp_absent))
            cur.execute("DELETE FROM attendance WHERE employee_id=%s", (emp_present,))
            cur.execute("DELETE FROM employees WHERE employee_id IN (%s,%s,%s)",
                        (emp_present, emp_absent, emp_leave))
            cur.close()

    def test_no_admin_emails_skips_send_without_error(self, monkeypatch):
        monkeypatch.setattr(daily_report_module, "get_admin_emails", lambda: [])
        called = []
        monkeypatch.setattr(daily_report_module, "send_email_async", lambda *a, **k: called.append(a))
        daily_report_module.generate_and_send_daily_report()
        assert called == []

    def test_no_email_config_skips_send(self, monkeypatch):
        monkeypatch.setattr(daily_report_module, "get_admin_emails", lambda: ["someone@test.local"])
        monkeypatch.setattr(daily_report_module, "get_email_config", lambda: None)
        called = []
        monkeypatch.setattr(daily_report_module, "send_email_async", lambda *a, **k: called.append(a))
        daily_report_module.generate_and_send_daily_report()
        assert called == []

    def test_unhandled_exception_in_run_report_is_caught_and_logged(self, monkeypatch):
        # generate_and_send_daily_report() is the APScheduler-facing
        # entrypoint -- it must never let an exception from _run_report()
        # propagate and kill the scheduler's job thread.
        def _boom():
            raise RuntimeError("boom")
        monkeypatch.setattr(daily_report_module, "_run_report", _boom)
        daily_report_module.generate_and_send_daily_report()  # must not raise


# ── send_weekly_employee_digests() ───────────────────────────────────────────

class TestSendWeeklyEmployeeDigests:
    def test_sends_only_to_active_employees_with_an_email(self, db_engine, monkeypatch):
        active_with_email = "DIGEST_ACTIVE"
        inactive_with_email = "DIGEST_INACTIVE"
        active_no_email = "DIGEST_NOEMAIL"
        cur = db_engine.cursor()
        try:
            cur.execute(
                "INSERT INTO employees (employee_id, name, email, is_active) VALUES (%s,%s,%s,1) "
                "ON CONFLICT (employee_id) DO NOTHING",
                (active_with_email, "Active Digest", "digest_active@test.local"),
            )
            cur.execute(
                "INSERT INTO employees (employee_id, name, email, is_active) VALUES (%s,%s,%s,0) "
                "ON CONFLICT (employee_id) DO NOTHING",
                (inactive_with_email, "Inactive Digest", "digest_inactive@test.local"),
            )
            cur.execute(
                "INSERT INTO employees (employee_id, name, email, is_active) VALUES (%s,%s,NULL,1) "
                "ON CONFLICT (employee_id) DO NOTHING",
                (active_no_email, "No Email Digest"),
            )

            sent = []
            monkeypatch.setattr(daily_report_module, "get_email_config", lambda: {"host": "smtp.test"})
            monkeypatch.setattr(daily_report_module, "send_email_async",
                                lambda to, subject, html, cfg: sent.append((to, subject, html)))

            daily_report_module.send_weekly_employee_digests()

            recipients = [to for to, _s, _h in sent]
            assert "digest_active@test.local" in recipients
            assert "digest_inactive@test.local" not in recipients

            active_html = next(h for to, _s, h in sent if to == "digest_active@test.local")
            assert "Active Digest" in active_html
            assert active_with_email in active_html
        finally:
            cur.execute("DELETE FROM employees WHERE employee_id IN (%s,%s,%s)",
                        (active_with_email, inactive_with_email, active_no_email))
            cur.close()

    def test_no_email_config_skips_send(self, monkeypatch):
        monkeypatch.setattr(daily_report_module, "get_email_config", lambda: None)
        called = []
        monkeypatch.setattr(daily_report_module, "send_email_async", lambda *a, **k: called.append(a))
        daily_report_module.send_weekly_employee_digests()
        assert called == []

    def test_db_error_is_caught_and_logged_not_raised(self, monkeypatch):
        def _raise():
            raise RuntimeError("db down")
        monkeypatch.setattr(daily_report_module, "get_db_connection", _raise)
        daily_report_module.send_weekly_employee_digests()  # must not raise


# ── Manual trigger routes ─────────────────────────────────────────────────────

class TestTriggerDailyReportRoute:
    def test_requires_admin_session(self, client):
        resp = client.post("/api/admin/trigger_daily_report")
        assert resp.status_code == 401 and resp.get_json()["ok"] is False   # an API call gets JSON, not a redirect

    def test_admin_triggers_report_generation(self, client, seed_admin, monkeypatch):
        monkeypatch.setattr(daily_report_module.threading, "Thread", _SyncThread)
        called = []
        monkeypatch.setattr(daily_report_module, "generate_and_send_daily_report", lambda: called.append(1))
        _admin_session(client, seed_admin)
        resp = client.post("/api/admin/trigger_daily_report")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert called == [1]


class TestTriggerWeeklyDigestRoute:
    def test_requires_admin_session(self, client):
        resp = client.post("/api/admin/trigger_weekly_digest")
        assert resp.status_code == 401 and resp.get_json()["ok"] is False   # an API call gets JSON, not a redirect

    def test_admin_triggers_weekly_digest(self, client, seed_admin, monkeypatch):
        monkeypatch.setattr(daily_report_module.threading, "Thread", _SyncThread)
        called = []
        monkeypatch.setattr(daily_report_module, "send_weekly_employee_digests", lambda: called.append(1))
        _admin_session(client, seed_admin)
        resp = client.post("/api/admin/trigger_weekly_digest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert called == [1]
