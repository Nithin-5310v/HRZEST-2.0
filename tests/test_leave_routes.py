"""Route-level tests for blueprints/leave.py: holidays, leave types, leave
requests/approval, resignation, bulk actions, the /api/* JSON endpoints,
overtime, and comp-off. This blueprint previously had almost no dedicated
route-level coverage (only incidentally touched by tests/test_comprehensive.py),
so these tests cover each route's success path plus its most important
branch (validation failure, not-found, already-processed, permission gate).
"""
import datetime

from utils.async_writer import _write_queue


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _employee_session(client, seed_employee):
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]


def _employee_bearer_token(client, seed_employee):
    resp = client.post("/api/employee/login", json={
        "employee_id": seed_employee["employee_id"],
        "password": seed_employee["password"],
    })
    return resp.get_json()["token"]


def _admin_bearer_token(client, seed_admin):
    resp = client.post("/api/login", json={
        "username": seed_admin["username"], "password": seed_admin["password"]})
    return resp.get_json()["token"]


def _wait_for_async_writes():
    _write_queue.join()


class TestAddHoliday:
    def test_adds_new_holiday(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/add_holiday", data={
            "date": "2026-08-15", "holiday_name": "Independence Day",
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM holidays WHERE date='2026-08-15'")
        row = cur.fetchone()
        assert row is not None and row[0] == "Independence Day"
        cur.execute("DELETE FROM holidays WHERE date='2026-08-15'")
        cur.close()

    def test_duplicate_date_silently_ignored(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        client.post("/add_holiday", data={"date": "2026-08-16", "holiday_name": "First"})
        resp = client.post("/add_holiday", data={"date": "2026-08-16", "holiday_name": "Second"},
                           follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM holidays WHERE date='2026-08-16'")
        row = cur.fetchone()
        assert row[0] == "First"  # second insert was a no-op
        cur.execute("DELETE FROM holidays WHERE date='2026-08-16'")
        cur.close()

    def test_leave_type_prefixes_name(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        client.post("/add_holiday", data={
            "date": "2026-08-17", "holiday_name": "Optional Day", "type": "Leave",
        })
        cur = db_engine.cursor()
        cur.execute("SELECT name FROM holidays WHERE date='2026-08-17'")
        row = cur.fetchone()
        assert row[0] == "Leave:Optional Day"
        cur.execute("DELETE FROM holidays WHERE date='2026-08-17'")
        cur.close()


class TestDeleteHoliday:
    def test_deletes_existing_holiday(self, client, seed_admin, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO holidays (date, name) VALUES ('2026-08-18','Temp') RETURNING id")
        hid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/delete_holiday/{hid}", data={"year": "2026"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT * FROM holidays WHERE id=%s", (hid,))
        assert cur.fetchone() is None
        cur.close()


class TestImportIndianHolidays:
    def test_import_runs_without_error(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM holidays WHERE date BETWEEN '2026-01-01' AND '2026-12-31'")
        existing_ids = {r[0] for r in cur.fetchall()}
        resp = client.post("/import_indian_holidays", data={"year": "2026"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT id FROM holidays WHERE date BETWEEN '2026-01-01' AND '2026-12-31'")
        new_ids = [r[0] for r in cur.fetchall() if r[0] not in existing_ids]
        if new_ids:
            cur.execute("DELETE FROM holidays WHERE id = ANY(%s)", (new_ids,))
        cur.close()


class TestRequestLeave:
    def test_single_day_leave_creates_one_row(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee)
        resp = client.post("/request_leave", data={
            "leave_date_start": "2026-09-01", "reason": "Personal work",
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM leave_requests WHERE employee_id=%s AND leave_date='2026-09-01'",
            (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == 1
        cur.execute("DELETE FROM leave_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()
        _wait_for_async_writes()

    def test_multi_day_leave_creates_one_row_per_working_day(self, client, seed_employee, db_engine):
        # Sat 5 - Mon 7 Sep 2026: the Sunday gets no row (QA BUG-07).
        _employee_session(client, seed_employee)
        client.post("/request_leave", data={
            "leave_date_start": "2026-09-05", "leave_date_end": "2026-09-07", "reason": "Trip",
        })
        cur = db_engine.cursor()
        cur.execute(
            "SELECT leave_date FROM leave_requests WHERE employee_id=%s AND leave_date BETWEEN '2026-09-05' AND '2026-09-07' "
            "ORDER BY leave_date",
            (seed_employee["employee_id"],))
        assert [str(r[0]) for r in cur.fetchall()] == ["2026-09-05", "2026-09-07"]
        cur.execute("DELETE FROM leave_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()
        _wait_for_async_writes()

    def test_half_day_leave_ignores_end_date(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee)
        client.post("/request_leave", data={
            "leave_date_start": "2026-09-10", "leave_date_end": "2026-09-15",
            "reason": "Doctor visit", "is_half_day": "on", "half_day_session": "Afternoon",
        })
        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*), COALESCE(BOOL_AND(is_half_day=1),FALSE) FROM leave_requests "
            "WHERE employee_id=%s AND leave_date='2026-09-10'",
            (seed_employee["employee_id"],))
        count, all_half = cur.fetchone()
        assert count == 1 and all_half
        cur.execute("DELETE FROM leave_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()
        _wait_for_async_writes()

    def test_missing_reason_is_a_noop(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee)
        resp = client.post("/request_leave", data={"leave_date_start": "2026-09-20"},
                           follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM leave_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == 0
        cur.close()


class TestLeaveBalance:
    def test_renders_for_admin(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/leave_balance")
        assert resp.status_code == 200


class TestSetLeaveBalance:
    def test_sets_and_updates_balance(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM leave_types WHERE is_active=1 LIMIT 1")
        lt_id = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        year = datetime.date.today().year
        resp = client.post("/set_leave_balance", data={
            "employee_id": seed_employee["employee_id"], "leave_type_id": str(lt_id),
            "total_days": "10", "year": str(year),
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute(
            "SELECT total_days FROM leave_balances WHERE employee_id=%s AND leave_type_id=%s AND year=%s",
            (seed_employee["employee_id"], lt_id, year))
        assert cur.fetchone()[0] == 10
        cur.execute("DELETE FROM leave_balances WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()


class TestLeaveHolidays:
    def test_leaves_tab_renders(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/leave_holidays?tab=leaves")
        assert resp.status_code == 200

    def test_holidays_tab_renders(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/leave_holidays?tab=holidays")
        assert resp.status_code == 200

    def test_redirect_alias(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/leave_requests", follow_redirects=False)
        assert resp.status_code == 302
        assert "tab=leaves" in resp.headers["Location"]


class TestLeaveAction:
    def test_approve_creates_attendance_and_deducts_balance(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute("SELECT id FROM leave_types WHERE name='Casual Leave' LIMIT 1")
        lt_id = cur.fetchone()[0]
        year = datetime.date.today().year
        leave_date = datetime.date(year, 10, 5)
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, leave_type_id) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], leave_date, "Test", lt_id))
        lid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO leave_balances (employee_id, leave_type_id, year, total_days, used_days) "
            "VALUES (%s,%s,%s,12,0) ON CONFLICT (employee_id, leave_type_id, year) DO UPDATE SET used_days=0",
            (seed_employee["employee_id"], lt_id, year))

        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/leave_action/{lid}", data={"action": "Approved"}, follow_redirects=False)
        assert resp.status_code == 302

        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Approved"
        cur.execute(
            "SELECT attendance_type FROM attendance WHERE employee_id=%s AND date=%s",
            (seed_employee["employee_id"], leave_date))
        assert cur.fetchone()[0] == "Approved Leave"
        cur.execute(
            "SELECT used_days FROM leave_balances WHERE employee_id=%s AND leave_type_id=%s AND year=%s",
            (seed_employee["employee_id"], lt_id, year))
        assert float(cur.fetchone()[0]) == 1.0

        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s", (seed_employee["employee_id"], leave_date))
        cur.execute("DELETE FROM leave_balances WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()
        _wait_for_async_writes()

    def test_reject_updates_status_only(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=5), "Test"))
        lid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        client.post(f"/leave_action/{lid}", data={"action": "Rejected"})
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Rejected"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()
        _wait_for_async_writes()

    def test_invalid_action_is_a_noop(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=6), "Test"))
        lid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/leave_action/{lid}", data={"action": "Bogus"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()


class TestLeaveCalendar:
    def test_renders(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/leave_calendar")
        assert resp.status_code == 200

    def test_month_rollover_params(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/leave_calendar?year=2026&month=0")
        assert resp.status_code == 200
        resp2 = client.get("/leave_calendar?year=2026&month=13")
        assert resp2.status_code == 200


class TestRequestResignation:
    def test_success(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee)
        future = (datetime.date.today() + datetime.timedelta(days=45)).isoformat()
        resp = client.post("/request_resignation", data={
            "last_working_day": future, "resign_reason": "Career move",
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT reason FROM resignation_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        row = cur.fetchone()
        assert row is not None and row[0] == "Career move"
        cur.execute("DELETE FROM resignation_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

    def test_less_than_30_days_notice_rejected(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee)
        soon = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        client.post("/request_resignation", data={"last_working_day": soon, "resign_reason": "x"})
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM resignation_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == 0
        cur.close()

    def test_invalid_date_format_rejected(self, client, seed_employee, db_engine):
        _employee_session(client, seed_employee)
        resp = client.post("/request_resignation", data={
            "last_working_day": "not-a-date", "resign_reason": "x",
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT COUNT(*) FROM resignation_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == 0
        cur.close()


class TestResignationAction:
    def test_accept(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=40), "x"))
        rid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/resignation_action/{rid}", data={"action": "Accepted"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM resignation_requests WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == "Accepted"
        cur.execute("DELETE FROM resignation_requests WHERE id=%s", (rid,))
        cur.close()

    def test_decline(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=40), "x"))
        rid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        client.post(f"/resignation_action/{rid}", data={"action": "Declined"})
        cur.execute("SELECT status FROM resignation_requests WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == "Declined"
        cur.execute("DELETE FROM resignation_requests WHERE id=%s", (rid,))
        cur.close()

    def test_invalid_action_redirects_without_change(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=40), "x"))
        rid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/resignation_action/{rid}", data={"action": "Bogus"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM resignation_requests WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM resignation_requests WHERE id=%s", (rid,))
        cur.close()


class TestBulkLeaveAction:
    def test_approves_multiple_pending_requests(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        ids = []
        for d in (11, 12):
            cur.execute(
                "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
                (seed_employee["employee_id"], datetime.date(2026, 11, d), "bulk test"))
            ids.append(cur.fetchone()[0])

        _admin_session(client, seed_admin["username"])
        resp = client.post("/bulk_leave_action", data={
            "action": "Approved", "leave_ids": [str(i) for i in ids],
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM leave_requests WHERE id = ANY(%s)", (ids,))
        statuses = [r[0] for r in cur.fetchall()]
        assert statuses == ["Approved", "Approved"]
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date IN (%s,%s)",
                    (seed_employee["employee_id"], datetime.date(2026, 11, 11), datetime.date(2026, 11, 12)))
        cur.execute("DELETE FROM leave_requests WHERE id = ANY(%s)", (ids,))
        cur.close()

    def test_no_ids_is_a_noop(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/bulk_leave_action", data={"action": "Approved"}, follow_redirects=False)
        assert resp.status_code == 302

    def test_invalid_action_is_a_noop(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2026, 11, 20), "x"))
        lid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        client.post("/bulk_leave_action", data={"action": "Bogus", "leave_ids": [str(lid)]})
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()


class TestLeaveApprovalRoleGating:
    """leave_action/resignation_action/bulk_leave_action/overtime_action and
    their /api/* twins used to only check @admin_required/@api_required --
    any authenticated admin-side session or token, regardless of role,
    could read and approve/reject every employee's leave/resignation/
    overtime data. They're now role-gated to admin/hr/manager (see
    blueprints/leave.py's _LEAVE_APPROVER_ROLES) -- a lower-privilege role
    like soc_analyst must be rejected, while manager (previously
    indistinguishable from admin here) must still work for its own direct
    reports (employees.manager_id -- see utils/helpers.py's
    manager_scope_denied, added once manager accounts became a real,
    creatable role rather than just a role-gate check)."""

    def test_soc_analyst_denied_web_leave_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2027, 1, 5), "x"))
        lid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"], role="soc_analyst")
        resp = client.post(f"/leave_action/{lid}", data={"action": "Approved"}, follow_redirects=False)
        assert resp.status_code == 403
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_manager_role_allowed_web_leave_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2027, 1, 6), "x"))
        lid = cur.fetchone()[0]
        # manager_scope_denied() requires seed_employee to actually be
        # this manager's direct report -- without this, the role-gate
        # would pass but the ownership check now correctly rejects it.
        cur.execute("UPDATE employees SET manager_id=%s WHERE employee_id=%s",
                    (seed_admin["username"], seed_employee["employee_id"]))
        _admin_session(client, seed_admin["username"], role="manager")
        resp = client.post(f"/leave_action/{lid}", data={"action": "Approved"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Approved"
        cur.execute("UPDATE employees SET manager_id=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.execute("DELETE FROM attendance WHERE employee_id=%s AND date=%s",
                    (seed_employee["employee_id"], datetime.date(2027, 1, 6)))
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_soc_analyst_denied_web_resignation_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=40), "x"))
        rid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"], role="soc_analyst")
        resp = client.post(f"/resignation_action/{rid}", data={"action": "Accepted"}, follow_redirects=False)
        assert resp.status_code == 403
        cur.execute("SELECT status FROM resignation_requests WHERE id=%s", (rid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM resignation_requests WHERE id=%s", (rid,))
        cur.close()

    def test_soc_analyst_denied_web_bulk_leave_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2027, 1, 7), "x"))
        lid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"], role="soc_analyst")
        resp = client.post("/bulk_leave_action", data={
            "action": "Approved", "leave_ids": [str(lid)],
        }, follow_redirects=False)
        assert resp.status_code == 403
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_soc_analyst_denied_web_overtime_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s,%s,'18:00:00','19:00:00',60,0,'Pending') RETURNING id",
            (seed_employee["employee_id"], datetime.date.today()))
        oid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"], role="soc_analyst")
        resp = client.post(f"/overtime_action/{oid}", data={"action": "approve"}, follow_redirects=False)
        assert resp.status_code == 403
        cur.execute("SELECT status FROM overtime_records WHERE id=%s", (oid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM overtime_records WHERE id=%s", (oid,))
        cur.close()

    def test_soc_analyst_denied_api_leave_list(self, client, seed_admin, db_engine):
        token = _admin_bearer_token(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        try:
            resp = client.get("/api/leave_requests", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403
        finally:
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            cur.close()

    def test_soc_analyst_denied_api_leave_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2027, 1, 8), "x"))
        lid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        try:
            resp = client.post(f"/api/leave_requests/{lid}/action", json={"action": "Approved"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403
            cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
            assert cur.fetchone()[0] == "Pending"
        finally:
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
            cur.close()

    def test_manager_role_allowed_api_leave_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2027, 1, 9), "x"))
        lid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        cur.execute("UPDATE admin_users SET role='manager' WHERE username=%s", (seed_admin["username"],))
        # _api_manager_scope_denied() requires seed_employee to actually be
        # this manager's direct report -- see the web equivalent above.
        cur.execute("UPDATE employees SET manager_id=%s WHERE employee_id=%s",
                    (seed_admin["username"], seed_employee["employee_id"]))
        try:
            resp = client.post(f"/api/leave_requests/{lid}/action", json={"action": "Approved"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "Approved"
        finally:
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            cur.execute("UPDATE employees SET manager_id=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
            cur.close()

    def test_soc_analyst_denied_api_resignation_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=40), "x"))
        rid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        try:
            resp = client.post(f"/api/resignation_requests/{rid}/action", json={"action": "Accepted"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403
        finally:
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            cur.execute("DELETE FROM resignation_requests WHERE id=%s", (rid,))
            cur.close()

    def test_soc_analyst_denied_api_overtime_action(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s,%s,'18:00:00','19:00:00',60,0,'Pending') RETURNING id",
            (seed_employee["employee_id"], datetime.date.today()))
        oid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        cur.execute("UPDATE admin_users SET role='soc_analyst' WHERE username=%s", (seed_admin["username"],))
        try:
            resp = client.post(f"/api/overtime/{oid}/action", json={"action": "approve"},
                               headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403
        finally:
            cur.execute("UPDATE admin_users SET role='admin' WHERE username=%s", (seed_admin["username"],))
            cur.execute("DELETE FROM overtime_records WHERE id=%s", (oid,))
            cur.close()


class TestApiHolidaysAndLeaveRequests:
    def test_api_holidays(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.get("/api/holidays", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_api_leave_requests(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.get("/api/leave_requests", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "leaves" in resp.get_json()

    def test_api_leave_action_valid(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2026, 12, 1), "x"))
        lid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post(f"/api/leave_requests/{lid}/action", json={"action": "Approved"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Approved"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_api_leave_action_invalid_action_rejected(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date(2026, 12, 2), "x"))
        lid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post(f"/api/leave_requests/{lid}/action", json={"action": "Bogus"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()


class TestApiResignationRequests:
    def test_list(self, client, seed_admin):
        token = _admin_bearer_token(client, seed_admin)
        resp = client.get("/api/resignation_requests", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "resignations" in resp.get_json()

    def test_action_valid(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=40), "x"))
        rid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post(f"/api/resignation_requests/{rid}/action", json={"action": "Accepted"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Accepted"
        cur.execute("DELETE FROM resignation_requests WHERE id=%s", (rid,))
        cur.close()

    def test_action_invalid_rejected(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO resignation_requests (employee_id, last_working_day, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=40), "x"))
        rid = cur.fetchone()[0]
        token = _admin_bearer_token(client, seed_admin)
        resp = client.post(f"/api/resignation_requests/{rid}/action", json={"action": "Bogus"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400
        cur.execute("DELETE FROM resignation_requests WHERE id=%s", (rid,))
        cur.close()


class TestApiEmployeeLeaveRequest:
    def test_success(self, client, seed_employee, db_engine):
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post("/api/employee/leave_request", json={
            "leave_date": "2026-12-10", "reason": "Family event",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("DELETE FROM leave_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

    def test_missing_fields_rejected(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post("/api/employee/leave_request", json={"leave_date": "2026-12-10"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400


class TestApiEmployeeResign:
    def test_success(self, client, seed_employee, db_engine):
        token = _employee_bearer_token(client, seed_employee)
        future = (datetime.date.today() + datetime.timedelta(days=45)).isoformat()
        resp = client.post("/api/employee/resign", json={
            "last_working_day": future, "reason": "Relocating",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("DELETE FROM resignation_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

    def test_invalid_date_format(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post("/api/employee/resign", json={
            "last_working_day": "31-12-2026", "reason": "x",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_too_soon_rejected(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        soon = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        resp = client.post("/api/employee/resign", json={
            "last_working_day": soon, "reason": "x",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400


class TestApiEmployeeLeaves:
    def test_summary_counts(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) VALUES "
            "(%s,%s,%s,'Approved'), (%s,%s,%s,'Pending')",
            (seed_employee["employee_id"], datetime.date(2026, 12, 15), "a",
             seed_employee["employee_id"], datetime.date(2026, 12, 16), "b"))
        token = _employee_bearer_token(client, seed_employee)
        resp = client.get("/api/employee/leaves", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        summary = resp.get_json()["summary"]
        assert summary["approved"] >= 1 and summary["pending"] >= 1
        cur.execute("DELETE FROM leave_requests WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()


class TestApiEmployeeCancelLeave:
    def test_cancels_own_pending_future_leave(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        future = datetime.date.today() + datetime.timedelta(days=10)
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], future, "x"))
        lid = cur.fetchone()[0]
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post(f"/api/employee/cancel_leave/{lid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Cancelled"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_not_found_for_wrong_owner(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
            "VALUES (%s,%s,%s,%s,0) ON CONFLICT (employee_id) DO NOTHING",
            ("TST002", "Other Employee", "emp2@test.local", "x"),
        )
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            ("TST002", datetime.date.today() + datetime.timedelta(days=10), "x"))
        lid = cur.fetchone()[0]
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post(f"/api/employee/cancel_leave/{lid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.execute("DELETE FROM employees WHERE employee_id='TST002'")
        cur.close()

    def test_already_approved_cannot_be_cancelled(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) VALUES (%s,%s,%s,'Approved') RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() + datetime.timedelta(days=10), "x"))
        lid = cur.fetchone()[0]
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post(f"/api/employee/cancel_leave/{lid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_past_date_cannot_be_cancelled(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], datetime.date.today() - datetime.timedelta(days=1), "x"))
        lid = cur.fetchone()[0]
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post(f"/api/employee/cancel_leave/{lid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()


class TestCancelLeaveWeb:
    def test_cancels_own_pending_future_leave(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        future = datetime.date.today() + datetime.timedelta(days=10)
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason) VALUES (%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], future, "x"))
        lid = cur.fetchone()[0]
        _employee_session(client, seed_employee)
        resp = client.post(f"/cancel_leave/{lid}", follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
        assert cur.fetchone()[0] == "Cancelled"
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_not_found_flashes_error(self, client, seed_employee):
        _employee_session(client, seed_employee)
        resp = client.post("/cancel_leave/999999999", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/employee_portal?tab=leave#leave-history")
        with client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("not found" in msg.lower() for _, msg in flashes)


class TestApiEmployeeOvertime:
    def test_request_overtime_success(self, client, seed_employee, db_engine):
        token = _employee_bearer_token(client, seed_employee)
        today = str(datetime.date.today())
        resp = client.post("/api/employee/request_overtime", json={
            "date": today, "reason": "Server migration",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("DELETE FROM overtime_records WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

    def test_missing_reason_rejected(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        resp = client.post("/api/employee/request_overtime", json={"date": str(datetime.date.today())},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_past_date_rejected(self, client, seed_employee):
        token = _employee_bearer_token(client, seed_employee)
        past = str(datetime.date.today() - datetime.timedelta(days=1))
        resp = client.post("/api/employee/request_overtime", json={"date": past, "reason": "x"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400

    def test_duplicate_date_rejected(self, client, seed_employee, db_engine):
        token = _employee_bearer_token(client, seed_employee)
        today = str(datetime.date.today())
        client.post("/api/employee/request_overtime", json={"date": today, "reason": "First"},
                    headers={"Authorization": f"Bearer {token}"})
        resp = client.post("/api/employee/request_overtime", json={"date": today, "reason": "Second"},
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400
        cur = db_engine.cursor()
        cur.execute("DELETE FROM overtime_records WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

    def test_my_overtime_lists_records(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s,%s,'18:00:00','19:00:00',60,100,'Pending')",
            (seed_employee["employee_id"], datetime.date.today()))
        token = _employee_bearer_token(client, seed_employee)
        resp = client.get("/api/employee/my_overtime", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert len(resp.get_json()["records"]) >= 1
        cur.execute("DELETE FROM overtime_records WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.close()

    def test_employee_holidays_marks_passed(self, client, seed_employee, db_engine):
        cur = db_engine.cursor()
        past = datetime.date.today() - datetime.timedelta(days=2)
        cur.execute("INSERT INTO holidays (date, name) VALUES (%s,'Past Holiday')", (past,))
        token = _employee_bearer_token(client, seed_employee)
        resp = client.get("/api/employee/holidays", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        entries = [h for h in resp.get_json()["holidays"] if h["name"] == "Past Holiday"]
        assert entries and entries[0]["passed"] is True
        cur.execute("DELETE FROM holidays WHERE date=%s", (past,))
        cur.close()


class TestOvertimePage:
    def test_renders(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/overtime")
        assert resp.status_code == 200


class TestOvertimeAction:
    def test_approve_above_threshold_credits_compoff(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s,%s,'18:00:00','20:30:00',150,0,'Pending') RETURNING id",
            (seed_employee["employee_id"], datetime.date.today()))
        oid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/overtime_action/{oid}", data={"action": "approve"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM overtime_records WHERE id=%s", (oid,))
        assert cur.fetchone()[0] == "Approved"
        cur.execute("SELECT earned_minutes FROM compoff_balance WHERE employee_id=%s", (seed_employee["employee_id"],))
        row = cur.fetchone()
        assert row is not None and row[0] >= 150
        cur.execute("DELETE FROM compoff_balance WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.execute("DELETE FROM overtime_records WHERE id=%s", (oid,))
        cur.close()

    def test_reject_reverses_previously_approved_compoff(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s,%s,'18:00:00','20:30:00',150,0,'Approved') RETURNING id",
            (seed_employee["employee_id"], datetime.date.today()))
        oid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO compoff_balance (employee_id, earned_minutes, used_minutes) VALUES (%s,150,0)",
            (seed_employee["employee_id"],))
        _admin_session(client, seed_admin["username"])
        client.post(f"/overtime_action/{oid}", data={"action": "reject"})
        cur.execute("SELECT earned_minutes FROM compoff_balance WHERE employee_id=%s", (seed_employee["employee_id"],))
        assert cur.fetchone()[0] == 0
        cur.execute("DELETE FROM compoff_balance WHERE employee_id=%s", (seed_employee["employee_id"],))
        cur.execute("DELETE FROM overtime_records WHERE id=%s", (oid,))
        cur.close()

    def test_invalid_action_flashes_error(self, client, seed_admin, seed_employee, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO overtime_records (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status) "
            "VALUES (%s,%s,'18:00:00','19:00:00',60,0,'Pending') RETURNING id",
            (seed_employee["employee_id"], datetime.date.today()))
        oid = cur.fetchone()[0]
        _admin_session(client, seed_admin["username"])
        resp = client.post(f"/overtime_action/{oid}", data={"action": "bogus"}, follow_redirects=False)
        assert resp.status_code == 302
        cur.execute("SELECT status FROM overtime_records WHERE id=%s", (oid,))
        assert cur.fetchone()[0] == "Pending"
        cur.execute("DELETE FROM overtime_records WHERE id=%s", (oid,))
        cur.close()


class TestCompoffRedirectAndSettings:
    def test_compoff_redirects_to_overtime_tab(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/compoff", follow_redirects=False)
        assert resp.status_code == 302
        assert "tab=compoff" in resp.headers["Location"]

    def test_compoff_settings_saves(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.post("/compoff_settings", data={
            "min_ot_minutes": "90", "minutes_per_day": "440",
        }, follow_redirects=False)
        assert resp.status_code == 302
        cur = db_engine.cursor()
        cur.execute("SELECT compoff_min_ot_minutes, compoff_minutes_per_day FROM company_settings LIMIT 1")
        min_ot, mpd = cur.fetchone()
        assert min_ot == 90 and mpd == 440
        # restore defaults so other tests relying on the 120/480 default aren't affected
        cur.execute("UPDATE company_settings SET compoff_min_ot_minutes=120, compoff_minutes_per_day=480")
        cur.close()


class TestMyCompoff:
    def test_renders(self, client, seed_employee):
        _employee_session(client, seed_employee)
        resp = client.get("/my_compoff")
        assert resp.status_code == 200
