"""SEC-09: the live check-in records the server's time, not the client's.

/api/employee/checkin accepted a client-supplied punched_at up to 24 h in
the past, so an employee arriving at 11:00 could submit 08:55 and avoid
the late / half-day mark -- and the payroll deduction that follows.
"""
import datetime

import pytest
import pytz

import blueprints.employee_portal as ep
from tests.conftest import TEST_OFFICE

OFFICE = TEST_OFFICE


def _ist(hour, minute):
    tz = pytz.timezone("Asia/Kolkata")
    return tz.localize(datetime.datetime.combine(datetime.date.today(), datetime.time(hour, minute)))


@pytest.fixture
def emp_token(client, seed_employee, db_engine):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
    cur.close()
    return client.post("/api/employee/login", json={"employee_id": seed_employee["employee_id"],
                                                    "password": seed_employee["password"]}).get_json()["token"]


def test_arriving_at_11_and_claiming_08_55_is_recorded_at_11(client, emp_token, monkeypatch, db_engine, seed_employee):
    monkeypatch.setattr(ep, "company_now", lambda: _ist(11, 0))
    data = client.post("/api/employee/checkin", json={**OFFICE, "punched_at": _ist(8, 55).isoformat()},
                       headers={"Authorization": f"Bearer {emp_token}"}).get_json()
    assert data["ok"] is True
    assert data["time"] == "11:00:00"
    assert data["status"] != "Full Day Login"            # 11:00 is past the default 09:15 grace
    cur = db_engine.cursor()
    cur.execute("SELECT login_time FROM attendance WHERE employee_id=%s", (seed_employee["employee_id"],))
    assert [str(r[0]) for r in cur.fetchall()] == ["11:00:00"]
    cur.close()
