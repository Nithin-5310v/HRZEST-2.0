"""Leave approvals go through one decision path (QA BUG-31/32/33/36, SEC-11).

The web, bulk and mobile approvals each had their own copy: the web one
didn't check the request was still Pending (approving twice deducted the
balance twice), the mobile one only flipped the status (no balance
deduction, no attendance mark, 'Declined' instead of 'Rejected'), and the
bulk one skipped the balance and the self-approval guard.
"""
import datetime
import secrets

import pytest

LEAVE_DAY = datetime.date.today() + datetime.timedelta(days=10)   # any future day


@pytest.fixture
def leave_type(db_engine):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO leave_types (name, annual_quota) VALUES (%s, 12) RETURNING id",
                ("QA Leave " + secrets.token_hex(3),))
    lt = cur.fetchone()[0]
    cur.close()
    return lt


@pytest.fixture
def request_leave(db_engine, leave_type):
    def _make(emp_id, day=LEAVE_DAY, half=False, status="Pending"):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO leave_requests (employee_id, leave_type_id, leave_date, reason, status, is_half_day) "
                    "VALUES (%s,%s,%s,'QA',%s,%s) RETURNING id", (emp_id, leave_type, day, status, 1 if half else 0))
        lid = cur.fetchone()[0]
        cur.close()
        return lid
    return _make


def _used_days(db_engine, emp_id, leave_type):
    cur = db_engine.cursor()
    cur.execute("SELECT COALESCE(SUM(used_days), 0) FROM leave_balances WHERE employee_id=%s AND leave_type_id=%s",
                (emp_id, leave_type))
    used = float(cur.fetchone()[0])
    cur.close()
    return used


def _status(db_engine, lid):
    cur = db_engine.cursor()
    cur.execute("SELECT status FROM leave_requests WHERE id=%s", (lid,))
    status = cur.fetchone()[0]
    cur.close()
    return status


def _attendance_type(db_engine, emp_id, day=LEAVE_DAY):
    cur = db_engine.cursor()
    cur.execute("SELECT attendance_type FROM attendance WHERE employee_id=%s AND date=%s", (emp_id, day))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _admin_token(client, seed_admin):
    creds = {"username": seed_admin["username"], "password": seed_admin["password"]}
    return client.post("/api/login", json=creds).get_json()["token"]


# ── BUG-31: the web approval only decides a Pending request ──────────────────

def test_approving_twice_deducts_the_balance_once(client, db_engine, seed_admin, seed_employee, leave_type, request_leave):
    emp = seed_employee["employee_id"]
    lid = request_leave(emp)
    _admin_session(client, seed_admin["username"])
    client.post(f"/leave_action/{lid}", data={"action": "Approved"})
    client.post(f"/leave_action/{lid}", data={"action": "Approved"})
    assert _used_days(db_engine, emp, leave_type) == 1.0


def test_a_decided_leave_cannot_be_flipped(client, db_engine, seed_admin, seed_employee, leave_type, request_leave):
    emp = seed_employee["employee_id"]
    lid = request_leave(emp)
    _admin_session(client, seed_admin["username"])
    client.post(f"/leave_action/{lid}", data={"action": "Approved"})
    client.post(f"/leave_action/{lid}", data={"action": "Rejected"})
    assert _status(db_engine, lid) == "Approved"
    assert _used_days(db_engine, emp, leave_type) == 1.0


# ── BUG-32: the mobile approval does what the web approval does ─────────────

def test_mobile_approval_deducts_the_balance_and_marks_the_day(client, db_engine, seed_admin, seed_employee, leave_type, request_leave):
    emp = seed_employee["employee_id"]
    lid = request_leave(emp)
    token = _admin_token(client, seed_admin)
    resp = client.post(f"/api/leave_requests/{lid}/action", json={"action": "Approved"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.get_json()["ok"] is True
    assert _used_days(db_engine, emp, leave_type) == 1.0
    assert _attendance_type(db_engine, emp) == "Approved Leave"


def test_mobile_approval_of_a_decided_leave_is_refused(client, db_engine, seed_admin, seed_employee, leave_type, request_leave):
    emp = seed_employee["employee_id"]
    lid = request_leave(emp)
    token = _admin_token(client, seed_admin)
    headers = {"Authorization": f"Bearer {token}"}
    client.post(f"/api/leave_requests/{lid}/action", json={"action": "Approved"}, headers=headers)
    resp = client.post(f"/api/leave_requests/{lid}/action", json={"action": "Approved"}, headers=headers)
    assert resp.status_code == 409
    assert _used_days(db_engine, emp, leave_type) == 1.0
