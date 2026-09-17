"""Tests for the object-level authorization guard (utils/auth.py:enforce_ownership)
and its use in the two employee-owned-resource routes that take a raw ID from the
URL/path: payroll.py's view_payslip and documents.py's download_document. These
are the BOLA/IDOR-shaped endpoints in this codebase — a valid session trying to
reach a DIFFERENT employee's payslip or uploaded document by editing an ID."""
import pytest
import utils.auth as auth_module


class TestEnforceOwnership:
    def test_admin_bypasses_ownership_check(self, client):
        with client.application.test_request_context():
            from flask import session
            session["admin_logged_in"] = True
            assert auth_module.enforce_ownership("SOMEONE_ELSE", "payslip") is True

    def test_own_resource_allowed(self, client):
        with client.application.test_request_context():
            from flask import session
            session["employee_id"] = "TST001"
            assert auth_module.enforce_ownership("TST001", "payslip") is True

    def test_cross_employee_denied(self, client):
        with client.application.test_request_context():
            from flask import session
            session["employee_id"] = "TST001"
            assert auth_module.enforce_ownership("OTHER_EMP", "payslip") is False

    def test_anonymous_denied(self, client):
        with client.application.test_request_context():
            assert auth_module.enforce_ownership("TST001", "payslip") is False

    def test_denial_logs_at_error_severity(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            auth_module, "log_security_event",
            lambda event_type, message, level="WARNING", **fields: calls.append((event_type, level, fields)),
        )
        with client.application.test_request_context():
            from flask import session
            session["employee_id"] = "TST001"
            auth_module.enforce_ownership("OTHER_EMP", "document", resource_id=42)
        assert len(calls) == 1
        event_type, level, fields = calls[0]
        assert event_type == "access.denied"
        assert level == "ERROR"
        assert fields["identifier"] == "TST001"
        assert fields["requested_owner"] == "OTHER_EMP"
        assert fields["resource_id"] == 42

    def test_allowed_access_does_not_log(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            auth_module, "log_security_event",
            lambda *a, **kw: calls.append((a, kw)),
        )
        with client.application.test_request_context():
            from flask import session
            session["employee_id"] = "TST001"
            auth_module.enforce_ownership("TST001", "payslip")
        assert calls == []


class TestPayslipOwnershipGate:
    def test_own_payslip_accessible(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get(f"/view_payslip/{seed_employee['employee_id']}/2026/1")
        assert resp.status_code == 200

    def test_other_employees_payslip_rejected(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get("/view_payslip/SOMEONE_ELSE/2026/1", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_admin_can_view_any_payslip(self, client, seed_employee):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
        resp = client.get(f"/view_payslip/{seed_employee['employee_id']}/2026/1")
        assert resp.status_code == 200


class TestDocumentOwnershipGate:
    @pytest.fixture
    def seed_document(self, db_engine, seed_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO employee_documents (employee_id, doc_type, original_name, stored_name) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (seed_employee["employee_id"], "ID Proof", "id.pdf", "id.pdf"),
        )
        doc_id = cur.fetchone()[0]
        yield doc_id
        cur.execute("DELETE FROM employee_documents WHERE id=%s", (doc_id,))
        cur.close()

    def test_other_employees_document_rejected(self, client, seed_employee, seed_document):
        with client.session_transaction() as sess:
            sess["employee_id"] = "SOME_OTHER_EMP"
        resp = client.get(f"/download_document/{seed_document}", follow_redirects=False)
        assert resp.status_code == 302
        assert "/employee_portal" in resp.headers.get("Location", "")

    def test_owning_employee_not_rejected_by_ownership_check(self, client, seed_employee, seed_document, monkeypatch):
        # Fixed: the previous assertion (`status_code != 302 or "/employee_portal"
        # not in Location`) couldn't actually distinguish "blocked by the
        # ownership check" from "let through, then redirected to the SAME
        # /employee_portal URL for an unrelated reason" -- and
        # download_document() (blueprints/documents.py) does exactly that:
        # once open_private(stored_ref) raises (no real file on disk in this
        # test environment), a non-admin session is ALSO redirected to
        # /employee_portal, just with a different flash ("Document file is
        # missing or unreadable." instead of "Access denied."). Both paths
        # produced an identical 302 to the same URL, so the old assertion
        # was true whether the gate worked or not.
        #
        # This version proves ownership enforcement directly: mock
        # open_private so the owner's request succeeds end-to-end (the
        # negative case -- a non-owner rejected -- is already covered
        # separately by test_other_employees_document_rejected above).
        monkeypatch.setattr("blueprints.documents.open_private", lambda ref: b"fake pdf bytes")
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        resp = client.get(f"/download_document/{seed_document}", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.data == b"fake pdf bytes"


class TestHrScopeHelpers:
    """Direct unit tests for utils/helpers.py's hr_scope_column/
    hr_scope_subquery/hr_scope_denied -- the shared building blocks behind
    every HR-scoping fix below (attendance/leave/tickets/performance/
    onboarding/payroll previously let any admin-side session, HR included,
    view or act on any employee's records with no ownership check at all)."""

    def test_non_hr_session_gets_empty_fragment(self, client):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import hr_scope_column, hr_scope_subquery
            session["admin_role"] = "admin"
            assert hr_scope_column() == ("", ())
            assert hr_scope_subquery() == ("", ())

    def test_anonymous_session_gets_empty_fragment(self, client):
        with client.application.test_request_context():
            from utils.helpers import hr_scope_column, hr_scope_subquery
            assert hr_scope_column() == ("", ())
            assert hr_scope_subquery() == ("", ())

    def test_hr_session_gets_scoped_fragment(self, client):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import hr_scope_column, hr_scope_subquery
            session["admin_role"] = "hr"
            session["admin_username"] = "test_hr_admin"
            col, params = hr_scope_column(alias="e")
            assert col == "AND e.assigned_hr_username=%s"
            assert params == ("test_hr_admin",)
            sub, params2 = hr_scope_subquery(alias="x")
            assert "x.employee_id IN" in sub
            assert params2 == ("test_hr_admin",)

    def test_hr_scope_denied_false_for_admin(self, client):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import hr_scope_denied
            session["admin_role"] = "admin"
            assert hr_scope_denied("ANY_EMP") is False

    def test_hr_scope_denied_true_for_unassigned_employee(self, client, seed_hr_admin, seed_employee):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import hr_scope_denied
            session["admin_role"] = "hr"
            session["admin_username"] = seed_hr_admin["username"]
            assert hr_scope_denied(seed_employee["employee_id"]) is True

    def test_hr_scope_denied_false_for_assigned_employee(self, client, seed_assigned_employee, seed_hr_admin):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import hr_scope_denied
            session["admin_role"] = "hr"
            session["admin_username"] = seed_hr_admin["username"]
            assert hr_scope_denied(seed_assigned_employee["employee_id"]) is False

    def test_hr_scope_denied_false_for_own_record(self, client, seed_hr_admin):
        # An HR session viewing its OWN employee_id (== admin_username) is
        # always allowed even with no employees row at all -- backs "My
        # Profile" (templates/admin_base.html), added earlier this session.
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import hr_scope_denied
            session["admin_role"] = "hr"
            session["admin_username"] = seed_hr_admin["username"]
            assert hr_scope_denied(seed_hr_admin["username"]) is False


class TestHrScopeRouteGuards:
    """Route-level proof that the scoping fixes actually reject an HR
    session reaching a DIFFERENT HR's (or unassigned) employee's records --
    one representative route per hardened blueprint, admin unaffected."""

    def test_attendance_detail_denied_for_unassigned_employee(self, client, seed_hr_admin, seed_employee):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = "hr"
        resp = client.get(f"/employee_attendance_detail/{seed_employee['employee_id']}/2026/1")
        assert resp.status_code == 404

    def test_attendance_detail_allowed_for_assigned_employee(self, client, seed_hr_admin, seed_assigned_employee):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = "hr"
        resp = client.get(f"/employee_attendance_detail/{seed_assigned_employee['employee_id']}/2026/1")
        assert resp.status_code == 200

    def test_admin_bypasses_attendance_detail_scoping(self, client, seed_admin, seed_employee):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        resp = client.get(f"/employee_attendance_detail/{seed_employee['employee_id']}/2026/1")
        assert resp.status_code == 200

    @pytest.fixture
    def seed_leave_request(self, db_engine, seed_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
            "VALUES (%s, CURRENT_DATE, 'test', 'Pending') RETURNING id",
            (seed_employee["employee_id"],),
        )
        lid = cur.fetchone()[0]
        yield lid
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_leave_action_denied_for_unassigned_employee(self, client, seed_hr_admin, seed_employee, seed_leave_request):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = "hr"
        resp = client.post(f"/leave_action/{seed_leave_request}", data={"action": "Approved"})
        assert resp.status_code == 403

    @pytest.fixture
    def seed_ticket(self, db_engine, seed_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO tickets (employee_id, category, subject, description, priority) "
            "VALUES (%s, 'General', 'test', 'test', 'Medium') RETURNING id",
            (seed_employee["employee_id"],),
        )
        tid = cur.fetchone()[0]
        yield tid
        cur.execute("DELETE FROM tickets WHERE id=%s", (tid,))
        cur.close()

    def test_ticket_action_denied_for_unassigned_employee(self, client, seed_hr_admin, seed_employee, seed_ticket):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = "hr"
        resp = client.post(f"/ticket_action/{seed_ticket}", data={"status": "Resolved"})
        assert resp.status_code == 403

    def test_performance_review_denied_for_unassigned_employee(self, client, seed_hr_admin, seed_employee):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = "hr"
        resp = client.get(f"/performance_review/{seed_employee['employee_id']}", follow_redirects=True)
        assert resp.status_code == 200
        assert b"not found" in resp.data.lower() or b"Employee not found" in resp.data

    @pytest.fixture
    def seed_onboarding_template(self, db_engine):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO onboarding_templates (name, description, is_active) "
            "VALUES ('Test Template', 'test', 1) RETURNING id"
        )
        tid = cur.fetchone()[0]
        yield tid
        cur.execute("DELETE FROM onboarding_templates WHERE id=%s", (tid,))
        cur.close()

    def test_onboarding_assign_denied_for_unassigned_employee(self, client, seed_hr_admin, seed_employee, seed_onboarding_template):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = "hr"
        resp = client.post("/onboarding_assign", data={
            "employee_id": seed_employee["employee_id"],
            "template_id": seed_onboarding_template,
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Employee not found" in resp.data

class TestManagerScopeHelpers:
    """Direct unit tests for utils/helpers.py's manager_scope_column/
    manager_scope_subquery/manager_scope_denied -- the manager-role twin
    of TestHrScopeHelpers above, scoping by employees.manager_id (the
    existing org-chart reporting-line column) instead of
    assigned_hr_username."""

    def test_non_manager_session_gets_empty_fragment(self, client):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import manager_scope_column, manager_scope_subquery
            session["admin_role"] = "admin"
            assert manager_scope_column() == ("", ())
            assert manager_scope_subquery() == ("", ())

    def test_anonymous_session_gets_empty_fragment(self, client):
        with client.application.test_request_context():
            from utils.helpers import manager_scope_column, manager_scope_subquery
            assert manager_scope_column() == ("", ())
            assert manager_scope_subquery() == ("", ())

    def test_manager_session_gets_scoped_fragment(self, client):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import manager_scope_column, manager_scope_subquery
            session["admin_role"] = "manager"
            session["admin_username"] = "test_manager_admin"
            col, params = manager_scope_column(alias="e")
            assert col == "AND e.manager_id=%s"
            assert params == ("test_manager_admin",)
            sub, params2 = manager_scope_subquery(alias="x")
            assert "x.employee_id IN" in sub
            assert params2 == ("test_manager_admin",)

    def test_manager_scope_denied_false_for_admin(self, client):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import manager_scope_denied
            session["admin_role"] = "admin"
            assert manager_scope_denied("ANY_EMP") is False

    def test_manager_scope_denied_true_for_non_report(self, client, seed_manager_admin, seed_employee):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import manager_scope_denied
            session["admin_role"] = "manager"
            session["admin_username"] = seed_manager_admin["username"]
            assert manager_scope_denied(seed_employee["employee_id"]) is True

    def test_manager_scope_denied_false_for_direct_report(self, client, seed_direct_report, seed_manager_admin):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import manager_scope_denied
            session["admin_role"] = "manager"
            session["admin_username"] = seed_manager_admin["username"]
            assert manager_scope_denied(seed_direct_report["employee_id"]) is False

    def test_manager_scope_denied_false_for_own_record(self, client, seed_manager_admin):
        with client.application.test_request_context():
            from flask import session
            from utils.helpers import manager_scope_denied
            session["admin_role"] = "manager"
            session["admin_username"] = seed_manager_admin["username"]
            assert manager_scope_denied(seed_manager_admin["username"]) is False


class TestManagerScopeRouteGuards:
    """Route-level proof that a manager session only sees/acts on its own
    direct reports (employees.manager_id) for leave/resignation/overtime --
    admin and HR unaffected. Mirrors TestHrScopeRouteGuards' shape. Keeps
    its own seed_leave_request fixture (rather than reusing
    TestHrScopeRouteGuards') since a class-scoped @pytest.fixture is only
    visible to tests within that same class."""

    @pytest.fixture
    def seed_leave_request(self, db_engine, seed_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
            "VALUES (%s, CURRENT_DATE, 'test', 'Pending') RETURNING id",
            (seed_employee["employee_id"],),
        )
        lid = cur.fetchone()[0]
        yield lid
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_leave_action_denied_for_non_report(self, client, seed_manager_admin, seed_employee, seed_leave_request):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_manager_admin["username"]
            sess["admin_role"] = "manager"
        resp = client.post(f"/leave_action/{seed_leave_request}", data={"action": "Approved"})
        assert resp.status_code == 403

    @pytest.fixture
    def seed_direct_report_leave_request(self, db_engine, seed_direct_report):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO leave_requests (employee_id, leave_date, reason, status) "
            "VALUES (%s, CURRENT_DATE, 'test', 'Pending') RETURNING id",
            (seed_direct_report["employee_id"],),
        )
        lid = cur.fetchone()[0]
        yield lid
        cur.execute("DELETE FROM leave_requests WHERE id=%s", (lid,))
        cur.close()

    def test_leave_action_allowed_for_direct_report(self, client, seed_manager_admin, seed_direct_report_leave_request):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_manager_admin["username"]
            sess["admin_role"] = "manager"
        resp = client.post(f"/leave_action/{seed_direct_report_leave_request}", data={"action": "Approved"})
        assert resp.status_code == 302

    def test_leave_holidays_excludes_tickets_tab_for_manager(self, client, seed_manager_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_manager_admin["username"]
            sess["admin_role"] = "manager"
        resp = client.get("/leave_holidays")
        assert resp.status_code == 200
        assert b"switchModule('tickets')" not in resp.data

    def test_leave_holidays_includes_tickets_tab_for_admin(self, client, seed_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        resp = client.get("/leave_holidays")
        assert resp.status_code == 200
        assert b"switchModule('tickets')" in resp.data

    def test_admin_bypasses_manager_scoping_on_leave_action(self, client, seed_admin, seed_employee, seed_leave_request):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        resp = client.post(f"/leave_action/{seed_leave_request}", data={"action": "Approved"})
        assert resp.status_code == 302

    # See tests/test_employee_document_verification.py's own TestHrScopeEnforcement
    # for the full upload+verify HR-scoping coverage -- this is just the one
    # representative case matching every other blueprint's entry here.
    def test_verify_employee_document_denied_for_unassigned_employee(self, client, seed_admin, seed_hr_admin, seed_employee, db_engine):
        import io
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"
        client.post("/upload_verification_document", data={
            "employee_id": seed_employee["employee_id"], "doc_kind": "id_proof",
            "document": (io.BytesIO(b"%PDF-1.4\n" + b"x" * 20), "id.pdf"),
        }, content_type="multipart/form-data")
        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM employee_documents WHERE employee_id=%s AND doc_kind='id_proof' ORDER BY uploaded_at DESC LIMIT 1",
            (seed_employee["employee_id"],),
        )
        did = cur.fetchone()[0]
        cur.close()

        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_hr_admin["username"]
            sess["admin_role"] = "hr"
        resp = client.post(f"/employees/{seed_employee['employee_id']}/documents/{did}/verify",
                           data={"action": "approve"}, follow_redirects=False)
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status FROM employee_documents WHERE id=%s", (did,))
        assert cur.fetchone()[0] == "pending", "HR must not be able to verify an unassigned employee's document"
        cur.close()
