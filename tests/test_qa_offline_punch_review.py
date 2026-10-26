"""SEC-09 (rest): queued offline punches -- shorter window and admin review.

/api/employee/sync_punches accepted a queued punch with a client-supplied
time up to 24 hours old, and nothing flagged it: an employee could stay
"offline", queue an 08:55 punch at 11:00 and sync it later to avoid the
late mark. The live check-in already ignores the client's time.

Owner decision (QA_AUDIT.md §0.1): punches older than 12 hours are refused
(a per-company setting, company_settings.offline_punch_max_hours), and every
synced punch is flagged for review. An admin approves or rejects it on
/offline_punches; rejecting removes the punch's effect on the attendance
row (and so on salary). A synced check-out earlier than the recorded
check-in is refused too.
"""
import datetime

import pytest

from tests.test_admin_views_routes import _admin_session
from utils.helpers import invalidate_settings_cache


@pytest.fixture
def emp(client, seed_employee, db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.execute("UPDATE company_settings SET location_enabled=0")
    cur.close()
    invalidate_settings_cache()
    token = client.post("/api/employee/login", json={"employee_id": seed_employee["employee_id"],
                                                     "password": seed_employee["password"]}).get_json()["token"]
    return seed_employee["employee_id"], token


def _ago(hours=0, minutes=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours, minutes=minutes)).isoformat()


def _sync(client, token, *punched_at):
    resp = client.post("/api/employee/sync_punches", headers={"Authorization": f"Bearer {token}"},
                       json={"punches": [{"id": str(i), "punched_at": t} for i, t in enumerate(punched_at)]})
    assert resp.status_code == 200
    return resp.get_json()["results"]


def _rows(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("SELECT id, login_time, logout_time, attendance_type, offline_parts, offline_review "
                "FROM attendance WHERE employee_id=%s ORDER BY date", (emp_id,))
    rows = cur.fetchall()
    cur.close()
    return rows


class TestWindow:
    def test_punch_older_than_the_company_window_is_refused(self, client, emp, db_engine):
        emp_id, token = emp
        result = _sync(client, token, _ago(hours=13))[0]
        assert result["ok"] is False and "12" in result["msg"]
        assert _rows(db_engine, emp_id) == []

    def test_window_is_a_company_setting(self, client, emp, db_engine):
        emp_id, token = emp
        cur = db_engine.cursor()
        cur.execute("UPDATE company_settings SET offline_punch_max_hours=24")
        cur.close()
        invalidate_settings_cache()
        assert _sync(client, token, _ago(hours=13))[0]["ok"] is True

    def test_offline_checkout_before_the_checkin_is_refused(self, client, emp, db_engine):
        emp_id, token = emp
        login, logout = _sync(client, token, _ago(minutes=30), _ago(minutes=50))
        assert login["ok"] is True
        assert logout["ok"] is False
        assert _rows(db_engine, emp_id)[0][2] is None   # no logout recorded


class TestReviewFlag:
    def test_synced_punches_are_flagged_for_review(self, client, emp, db_engine):
        emp_id, token = emp
        _sync(client, token, _ago(minutes=50), _ago(minutes=10))
        (_id, login, logout, _type, parts, review), = _rows(db_engine, emp_id)
        assert login and logout
        assert parts == "login,logout" and review == "pending"

    def test_review_page_lists_pending_punches(self, client, emp, seed_admin, db_engine):
        emp_id, token = emp
        _sync(client, token, _ago(minutes=50))
        _admin_session(client, seed_admin["username"])
        html = client.get("/offline_punches").get_data(as_text=True)
        assert emp_id in html

    def test_monthly_report_shows_how_many_await_review(self, client, emp, seed_admin, db_engine):
        emp_id, token = emp
        _sync(client, token, _ago(minutes=50))
        _admin_session(client, seed_admin["username"])
        html = client.get("/monthly_report").get_data(as_text=True)
        assert "offline punch" in html.lower() and "/offline_punches" in html

    def test_approve(self, client, emp, seed_admin, db_engine):
        emp_id, token = emp
        _sync(client, token, _ago(minutes=50))
        att_id = _rows(db_engine, emp_id)[0][0]
        _admin_session(client, seed_admin["username"])
        assert client.post(f"/offline_punches/{att_id}/decide", data={"decision": "approve"}).status_code == 302
        row = _rows(db_engine, emp_id)[0]
        assert row[1] is not None and row[5] == "approved"

    def test_rejecting_an_offline_checkin_removes_the_day(self, client, emp, seed_admin, db_engine):
        emp_id, token = emp
        _sync(client, token, _ago(minutes=50), _ago(minutes=10))
        att_id = _rows(db_engine, emp_id)[0][0]
        _admin_session(client, seed_admin["username"])
        client.post(f"/offline_punches/{att_id}/decide", data={"decision": "reject"})
        assert _rows(db_engine, emp_id) == []

    def test_rejecting_an_offline_checkout_keeps_the_live_checkin(self, client, emp, seed_admin, db_engine):
        emp_id, token = emp
        today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
        cur = db_engine.cursor()
        cur.execute("INSERT INTO attendance (employee_id, date, login_time, status) "
                    "VALUES (%s, %s, '00:01', 'Full Day Login')", (emp_id, today))
        cur.close()
        assert _sync(client, token, _ago(minutes=1))[0]["ok"] is True
        att_id, login, logout, _type, parts, review = _rows(db_engine, emp_id)[0]
        assert logout is not None and parts == "logout" and review == "pending"
        _admin_session(client, seed_admin["username"])
        client.post(f"/offline_punches/{att_id}/decide", data={"decision": "reject"})
        _id, login, logout, att_type, _parts, review = _rows(db_engine, emp_id)[0]
        assert login is not None and logout is None and att_type is None and review == "rejected"

    def test_hr_cannot_decide_for_an_employee_not_assigned_to_them(self, client, emp, seed_hr_admin, db_engine):
        emp_id, token = emp
        _sync(client, token, _ago(minutes=50))
        att_id = _rows(db_engine, emp_id)[0][0]
        _admin_session(client, seed_hr_admin["username"], role="hr")
        client.post(f"/offline_punches/{att_id}/decide", data={"decision": "approve"})
        assert _rows(db_engine, emp_id)[0][5] == "pending"


class TestSetting:
    def test_admin_saves_the_window(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        for value, expected in (("24", 24), ("0", 24), ("100", 24), ("x", 24), ("6", 6)):
            client.post("/save_offline_punch_window", data={"offline_punch_max_hours": value})
            cur = db_engine.cursor()
            cur.execute("SELECT offline_punch_max_hours FROM company_settings LIMIT 1")
            assert cur.fetchone()[0] == expected, value
            cur.close()
