"""The audit trail: what an edit changed (old -> new), who read or exported
salary and other employees' data, and the request ID that ties a response,
its log lines and its security events together.

audit_logs is append-only (a trigger refuses DELETE/UPDATE), so each test
reads only the rows written after its own starting mark.
"""
import json

import pytest
from flask import g

from extensions import _log_handler, app_log, current_request_id, log_security_event
from utils import detections
from utils.helpers import describe_audit_detail
from utils.helpers import encrypt_amount as _pay
from utils.helpers.db import _audit_actor

EMP = "TST001"


def _admin_session(client, seed_admin):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})
    return client


def _admin_token(client, seed_admin):
    resp = client.post("/api/login", json={"username": seed_admin["username"], "password": seed_admin["password"]})
    return resp.get_json()["token"]


def _mark(db_engine):
    cur = db_engine.cursor()
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM audit_logs")
    mark = cur.fetchone()[0]
    cur.close()
    return mark


def _rows(db_engine, mark, action):
    cur = db_engine.cursor()
    cur.execute("SELECT actor, actor_type, target_id, detail FROM audit_logs WHERE id > %s AND action=%s ORDER BY id",
                (mark, action))
    rows = [dict(zip(("actor", "actor_type", "target_id", "detail"), r)) for r in cur.fetchall()]
    cur.close()
    return rows


def _changes(row):
    return json.loads(row["detail"])["changes"]


@pytest.fixture
def salary_config(db_engine, seed_employee):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc, basic_pct) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=EXCLUDED.salary_per_day, "
        "monthly_ctc=EXCLUDED.monthly_ctc, basic_pct=EXCLUDED.basic_pct",
        (EMP, _pay(1500), _pay(39000), _pay(50)))
    yield
    cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (EMP,))
    cur.close()


@pytest.fixture
def reset_employee(db_engine, seed_employee):
    yield seed_employee
    cur = db_engine.cursor()
    cur.execute("UPDATE employees SET role=NULL, department=NULL, address=NULL, work_mode=NULL WHERE employee_id=%s",
                (EMP,))
    db_engine.commit()
    cur.close()


EDIT_FORM = {"emp_id": EMP, "name": "Test Employee", "email": "emp@test.local", "work_mode": "office"}


class TestEditsRecordOldAndNewValues:
    def test_edit_records_each_changed_field(self, client, seed_admin, reset_employee, db_engine):
        _admin_session(client, seed_admin)
        mark = _mark(db_engine)
        resp = client.post("/edit_employee", data={**EDIT_FORM, "name": "Test Employee Renamed",
                                                   "department": "Finance", "address": "12 Secret Street"})
        assert resp.status_code == 302
        [row] = _rows(db_engine, mark, "update_employee")
        changes = _changes(row)
        assert row["actor"] == seed_admin["username"] and row["target_id"] == EMP
        assert changes["name"] == {"from": "Test Employee", "to": "Test Employee Renamed"}
        assert changes["department"] == {"from": None, "to": "Finance"}
        # encrypted at rest: recorded as changed, never the value
        assert changes["address"] == {"changed": True}
        assert "Secret Street" not in row["detail"]
        assert "email" not in changes

    def test_saving_the_same_values_again_records_nothing(self, client, seed_admin, reset_employee, db_engine):
        _admin_session(client, seed_admin)
        form = {**EDIT_FORM, "department": "Finance", "address": "12 Secret Street"}
        client.post("/edit_employee", data=form)
        mark = _mark(db_engine)
        # the address is now ciphertext in the table, and re-encrypting it gives
        # different ciphertext: the comparison has to use the decrypted value
        client.post("/edit_employee", data=form)
        assert _rows(db_engine, mark, "update_employee") == []

    def test_role_change_keeps_its_own_row_with_old_and_new(self, client, seed_admin, reset_employee, db_engine):
        _admin_session(client, seed_admin)
        mark = _mark(db_engine)
        client.post("/edit_employee", data={**EDIT_FORM, "role": "Engineer"})
        [row] = _rows(db_engine, mark, "update_employee_role")
        assert _changes(row) == {"role": {"from": None, "to": "Engineer"}}
        for other in _rows(db_engine, mark, "update_employee"):
            assert "role" not in _changes(other)

    def test_salary_change_is_recorded_without_the_amount(self, client, seed_admin, salary_config, db_engine):
        _admin_session(client, seed_admin)
        mark = _mark(db_engine)
        client.post("/update_salary", data={"emp_id": EMP, "salary": "1777", "hike_date": "2026-01-15"})
        [row] = _rows(db_engine, mark, "update_salary")
        changes = _changes(row)
        assert changes["salary_per_day"] == {"changed": True}
        assert changes["last_revised"]["to"] == "2026-01-15"
        assert "1777" not in row["detail"] and "1500" not in row["detail"]

    def test_a_hike_is_recorded_without_the_amounts(self, client, seed_admin, salary_config, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO performance_reviews (employee_id, quarter, year, overall_rating) "
                    "VALUES (%s, 1, 2025, 4.8) ON CONFLICT (employee_id, quarter, year) "
                    "DO UPDATE SET overall_rating=EXCLUDED.overall_rating", (EMP,))
        cur.close()
        _admin_session(client, seed_admin)
        mark = _mark(db_engine)
        client.post("/apply_hike", data={"quarter": "1", "year": "2025", "emp_ids": EMP})
        [row] = _rows(db_engine, mark, "apply_hike")
        detail = json.loads(row["detail"])
        assert detail == {"changes": {"monthly_ctc": {"changed": True}}, "hike_pct": 20.0, "quarter": "Q1 2025"}
        assert "39000" not in row["detail"] and "46800" not in row["detail"]


class TestSensitiveReadsAreRecorded:
    def test_api_export_names_the_api_user_not_system(self, client, seed_admin, db_engine):
        token = _admin_token(client, seed_admin)
        mark = _mark(db_engine)
        resp = client.get("/api/payroll/salary_report_export?year=2025&month=1",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        [row] = _rows(db_engine, mark, "export_salary_report")
        assert (row["actor"], row["actor_type"], row["target_id"]) == (seed_admin["username"], "admin", "2025-01")
        assert json.loads(row["detail"]) == {"format": "xlsx", "via": "api"}

    def test_repeated_salary_exports_raise_the_mass_export_detection(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        for month in range(1, 6):
            assert client.get(f"/salary_report_export?year=2025&month={month}").status_code == 200
        events = []
        cur = db_engine.cursor()
        findings = detections.run_tenant(cur, "acme", lambda event_type, message, **fields: events.append(fields))
        cur.close()
        assert "mass_export" in [f["rule"] for f in findings]
        assert seed_admin["username"] in [e.get("identifier") for e in events]

    def test_admin_reading_a_payslip_is_recorded_but_the_employee_reading_their_own_is_not(
            self, client, seed_admin, seed_employee, salary_config, db_engine):
        mark = _mark(db_engine)
        with client.session_transaction() as sess:
            sess["employee_id"] = EMP
            sess["employee_name"] = seed_employee["name"]
        assert client.get(f"/view_payslip/{EMP}/2025/1").status_code == 200
        assert _rows(db_engine, mark, "view_payslip") == []

        with client.session_transaction() as sess:
            sess.clear()
        _admin_session(client, seed_admin)
        assert client.get(f"/view_payslip/{EMP}/2025/1").status_code == 200
        assert client.get(f"/download_payslip/{EMP}/2025/1").status_code == 200
        assert [r["target_id"] for r in _rows(db_engine, mark, "view_payslip")] == [f"{EMP}:2025-01"]
        assert [r["target_id"] for r in _rows(db_engine, mark, "download_payslip")] == [f"{EMP}:2025-01"]

    def test_activity_log_spells_out_the_changes(self, client, seed_admin, reset_employee, db_engine):
        _admin_session(client, seed_admin)
        client.post("/edit_employee", data={**EDIT_FORM, "department": "Finance", "address": "12 Secret Street"})
        page = client.get("/activity_log?action=update_employee").get_data(as_text=True)
        assert "department: (empty) → Finance" in page
        assert "address changed (value not recorded)" in page
        assert "Secret Street" not in page


class TestDescribeAuditDetail:
    def test_spells_out_changes_and_facts(self):
        detail = json.dumps({"changes": {"department": {"from": "Ops", "to": None}, "dob": {"changed": True}},
                             "via": "api"})
        assert describe_audit_detail(detail) == "department: Ops → (empty); dob changed (value not recorded); via: api"

    @pytest.mark.parametrize("detail", [None, "", "role changed from 'A' to 'B'", "{not json", "[1, 2]"])
    def test_leaves_other_details_as_they_are(self, detail):
        assert describe_audit_detail(detail) == detail

    def test_outside_a_request_the_actor_is_system(self):
        assert _audit_actor() == ("system", "system", "")


class TestRequestId:
    def test_every_response_carries_one(self, client):
        rid = client.get("/healthz").headers.get("X-Request-ID", "")
        assert len(rid) == 32 and int(rid, 16) >= 0

    def test_a_well_formed_incoming_id_is_kept(self, client):
        assert client.get("/healthz", headers={"X-Request-ID": "nginx-abc123def456"}).headers["X-Request-ID"] \
            == "nginx-abc123def456"

    @pytest.mark.parametrize("bad", ["short", "has space in it", "x" * 65, "<script>alert(1)</script>", "aébcdefgh"])
    def test_a_malformed_incoming_id_is_replaced(self, client, bad):
        rid = client.get("/healthz", headers={"X-Request-ID": bad}).headers["X-Request-ID"]
        assert rid != bad and len(rid) == 32

    def test_security_events_record_the_request_id(self, client, db_engine):
        rid = "audit-trail-test-0001"
        assert client.get("/api/employees", headers={"X-Request-ID": rid}).status_code == 401
        cur = db_engine.cursor()
        cur.execute("SELECT event_type, extra_json FROM security_events WHERE extra_json LIKE %s", (f"%{rid}%",))
        rows = cur.fetchall()
        cur.close()
        assert [r[0] for r in rows] == ["access.denied"]
        assert json.loads(rows[0][1])["request_id"] == rid

    def test_log_lines_carry_the_request_id(self, client):
        with client.application.test_request_context("/somewhere"):
            g.request_id = "log-line-test-0002"
            assert current_request_id() == "log-line-test-0002"
            record = app_log.makeRecord(app_log.name, 20, __file__, 1, "hello", (), None)
            assert all(f.filter(record) for f in _log_handler.filters)
            assert json.loads(_log_handler.format(record))["request_id"] == "log-line-test-0002"
        record = app_log.makeRecord(app_log.name, 20, __file__, 1, "outside a request", (), None)
        assert all(f.filter(record) for f in _log_handler.filters)
        assert "request_id" not in json.loads(_log_handler.format(record))

    def test_an_explicit_request_id_field_is_not_overwritten(self, client, db_engine):
        with client.application.test_request_context("/x"):
            g.request_id = "from-the-request-0003"
            log_security_event("test.audit_trail", "explicit request id", level="INFO",
                               identifier="AUDIT_TRAIL_RID", request_id="caller-supplied-0003")
        cur = db_engine.cursor()
        cur.execute("SELECT extra_json FROM security_events WHERE identifier='AUDIT_TRAIL_RID' ORDER BY id DESC LIMIT 1")
        assert json.loads(cur.fetchone()[0])["request_id"] == "caller-supplied-0003"
        cur.close()
