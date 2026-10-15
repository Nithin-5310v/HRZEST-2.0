"""UX-01: sidebar badge counts change as soon as something is decided or raised.

get_pending_counts() caches (pending leaves, pending resignations, open
tickets) for 30 s, and nothing invalidated it after an approval, a
rejection, a new request or a ticket update, so the badges lagged.
"""
import datetime

import pytest


@pytest.fixture
def badges(client, seed_admin):
    """Read the counts the way the app does -- /api/dashboard serves the
    cached get_pending_counts() for the request's own tenant."""
    token = client.post("/api/login", json={"username": seed_admin["username"],
                                            "password": seed_admin["password"]}).get_json()["token"]

    def read():
        data = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"}).get_json()
        return data["pending_leaves"], data["pending_resignations"], data["pending_tickets"]
    return read


@pytest.fixture
def admin(client, seed_admin):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = seed_admin["username"]
        sess["admin_role"] = "admin"
    return client


def _pending_leave(db_engine, emp_id):
    cur = db_engine.cursor()
    cur.execute("INSERT INTO leave_requests (employee_id, leave_date, reason, status) VALUES (%s,%s,'QA','Pending') "
                "RETURNING id", (emp_id, datetime.date.today() + datetime.timedelta(days=20)))
    lid = cur.fetchone()[0]
    cur.close()
    return lid


def test_approving_a_leave_updates_the_badge_at_once(badges, admin, db_engine, seed_employee):
    lid = _pending_leave(db_engine, seed_employee["employee_id"])
    before = badges()[0]                                    # cached now
    admin.post(f"/leave_action/{lid}", data={"action": "Approved"})
    assert badges()[0] == before - 1


def test_a_new_ticket_updates_the_badge_at_once(badges, client, db_engine, seed_employee):
    before = badges()[2]
    with client.session_transaction() as sess:
        sess["employee_id"] = seed_employee["employee_id"]
        sess["employee_name"] = seed_employee["name"]
    client.post("/raise_ticket", data={"category": "IT", "subject": "QA", "description": "QA ticket", "priority": "Low"})
    assert badges()[2] == before + 1
