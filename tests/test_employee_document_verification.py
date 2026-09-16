"""Tests for the employee document verification workflow (Company
Onboarding -> Trial -> Document Verification -> Subscription Activation) --
blueprints/documents.py's upload_verification_document()/
verify_employee_document(). A separate concept from the pre-existing
free-text employee_documents upload (doc_type) -- these rows carry a
normalized doc_kind and a real approve/reject workflow, ending in exactly
one admin notification once every required kind is approved for a given
employee.
"""
import io
import pytest


_FAKE_PDF = b"%PDF-1.4\n" + b"x" * 20


def _admin_session(client, username, role="admin"):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username
        sess["admin_role"] = role


def _upload(client, emp_id, doc_kind, filename="doc.pdf"):
    return client.post(
        "/upload_verification_document",
        data={
            "employee_id": emp_id, "doc_kind": doc_kind,
            "document": (io.BytesIO(_FAKE_PDF), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def _latest_doc_id(db_engine, emp_id, doc_kind):
    cur = db_engine.cursor()
    cur.execute(
        "SELECT id FROM employee_documents WHERE employee_id=%s AND doc_kind=%s "
        "ORDER BY uploaded_at DESC LIMIT 1",
        (emp_id, doc_kind),
    )
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def _verify(client, emp_id, did, action, reason=None):
    data = {"action": action}
    if reason is not None:
        data["reason"] = reason
    return client.post(f"/employees/{emp_id}/documents/{did}/verify", data=data, follow_redirects=False)


class TestUploadVerificationDocument:
    def test_requires_admin(self, client, seed_employee):
        resp = _upload(client, seed_employee["employee_id"], "id_proof")
        assert resp.status_code in (301, 302)
        assert "/admin-login" in resp.headers["Location"] or "/login" in resp.headers["Location"]

    def test_invalid_doc_kind_rejected(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = _upload(client, seed_employee["employee_id"], "passport_photo")
        assert resp.status_code in (301, 302)

    def test_valid_upload_creates_pending_row(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = _upload(client, seed_employee["employee_id"], "id_proof")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute(
            "SELECT verification_status, doc_kind FROM employee_documents WHERE employee_id=%s AND doc_kind='id_proof'",
            (seed_employee["employee_id"],),
        )
        row = cur.fetchone()
        cur.close()
        assert row is not None
        assert row[0] == "pending"

class TestVerifyEmployeeDocument:
    def test_requires_admin(self, client, seed_employee):
        resp = _verify(client, seed_employee["employee_id"], 1, "approve")
        assert resp.status_code in (301, 302)
        assert "/admin-login" in resp.headers["Location"] or "/login" in resp.headers["Location"]

    def test_reject_requires_reason(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin["username"])
        _upload(client, seed_employee["employee_id"], "id_proof")
        did = _latest_doc_id(db_engine, seed_employee["employee_id"], "id_proof")
        resp = _verify(client, seed_employee["employee_id"], did, "reject")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status FROM employee_documents WHERE id=%s", (did,))
        assert cur.fetchone()[0] == "pending", "rejection with no reason must not apply"
        cur.close()

    def test_invalid_action_rejected(self, client, seed_admin, seed_employee, db_engine):
        _admin_session(client, seed_admin["username"])
        _upload(client, seed_employee["employee_id"], "id_proof")
        did = _latest_doc_id(db_engine, seed_employee["employee_id"], "id_proof")
        resp = _verify(client, seed_employee["employee_id"], did, "delete_everything")
        assert resp.status_code in (301, 302)

    def test_reject_sets_status_and_reason_then_reupload_creates_new_pending_row(
        self, client, seed_admin, seed_employee, db_engine
    ):
        _admin_session(client, seed_admin["username"])
        emp_id = seed_employee["employee_id"]
        _upload(client, emp_id, "id_proof")
        did = _latest_doc_id(db_engine, emp_id, "id_proof")

        resp = _verify(client, emp_id, did, "reject", reason="Blurry photo.")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status, rejection_reason, verified_by FROM employee_documents WHERE id=%s", (did,))
        row = cur.fetchone()
        assert row[0] == "rejected"
        assert row[1] == "Blurry photo."
        assert row[2] == seed_admin["username"]
        cur.close()

        # Re-upload is a NEW row, not an in-place update -- history kept.
        _upload(client, emp_id, "id_proof", filename="doc2.pdf")
        new_did = _latest_doc_id(db_engine, emp_id, "id_proof")
        assert new_did != did
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status FROM employee_documents WHERE id=%s", (new_did,))
        assert cur.fetchone()[0] == "pending"
        cur.close()

    def test_verify_unknown_document_id_rejected(self, client, seed_admin, seed_employee):
        _admin_session(client, seed_admin["username"])
        resp = _verify(client, seed_employee["employee_id"], 9999999, "approve")
        assert resp.status_code in (301, 302)

    def test_verify_document_belonging_to_different_employee_rejected(
        self, client, seed_admin, seed_employee, seed_assigned_employee, db_engine
    ):
        _admin_session(client, seed_admin["username"])
        _upload(client, seed_employee["employee_id"], "id_proof")
        did = _latest_doc_id(db_engine, seed_employee["employee_id"], "id_proof")
        # Same document id, wrong employee_id in the URL.
        resp = _verify(client, seed_assigned_employee["employee_id"], did, "approve")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status FROM employee_documents WHERE id=%s", (did,))
        assert cur.fetchone()[0] == "pending"
        cur.close()

    def test_free_text_document_cannot_be_verified(self, client, seed_admin, seed_employee, db_engine):
        """A document uploaded via the ORIGINAL free-text /upload_document
        flow (doc_kind IS NULL) has no verification workflow -- the verify
        route must refuse it rather than silently "approving" something
        with no doc_kind to track."""
        _admin_session(client, seed_admin["username"])
        emp_id = seed_employee["employee_id"]
        client.post(
            "/upload_document",
            data={"employee_id": emp_id, "doc_type": "PAN Card", "document": (io.BytesIO(_FAKE_PDF), "pan.pdf")},
            content_type="multipart/form-data",
        )
        cur = db_engine.cursor()
        cur.execute(
            "SELECT id FROM employee_documents WHERE employee_id=%s AND doc_kind IS NULL ORDER BY uploaded_at DESC LIMIT 1",
            (emp_id,),
        )
        did = cur.fetchone()[0]
        cur.close()
        resp = _verify(client, emp_id, did, "approve")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status FROM employee_documents WHERE id=%s", (did,))
        assert cur.fetchone()[0] is None
        cur.close()


class TestNotificationFiresExactlyOnceOnFullVerification:
    def test_notification_and_flag_fire_only_once_both_kinds_approved(
        self, client, seed_admin, seed_employee, db_engine, monkeypatch
    ):
        import blueprints.documents as documents_module
        notified = []
        monkeypatch.setattr(
            documents_module, "_notify_admin_employee_verified",
            lambda emp_id, name: notified.append((emp_id, name))
        )
        _admin_session(client, seed_admin["username"])
        emp_id = seed_employee["employee_id"]

        _upload(client, emp_id, "id_proof")
        _upload(client, emp_id, "address_proof")
        id_did = _latest_doc_id(db_engine, emp_id, "id_proof")
        addr_did = _latest_doc_id(db_engine, emp_id, "address_proof")

        resp = _verify(client, emp_id, id_did, "approve")
        assert resp.status_code in (301, 302)
        assert notified == [], "must not notify until EVERY required kind is approved"
        cur = db_engine.cursor()
        cur.execute("SELECT doc_verification_notified_at FROM employees WHERE employee_id=%s", (emp_id,))
        assert cur.fetchone()[0] is None
        cur.close()

        resp = _verify(client, emp_id, addr_did, "approve")
        assert resp.status_code in (301, 302)
        assert len(notified) == 1
        assert notified[0][0] == emp_id
        cur = db_engine.cursor()
        cur.execute("SELECT doc_verification_notified_at FROM employees WHERE employee_id=%s", (emp_id,))
        assert cur.fetchone()[0] is not None
        cur.close()

        # Idempotency: re-approving (e.g. a duplicate/retried request)
        # must not fire a second notification -- the guarded UPDATE's
        # rowcount is 0 the second time since doc_verification_notified_at
        # is already set.
        resp = _verify(client, emp_id, addr_did, "approve")
        assert resp.status_code in (301, 302)
        assert len(notified) == 1, "re-approval must not double-notify"

    def test_rejecting_one_kind_after_both_approved_does_not_re_notify(
        self, client, seed_admin, seed_employee, db_engine, monkeypatch
    ):
        import blueprints.documents as documents_module
        notified = []
        monkeypatch.setattr(
            documents_module, "_notify_admin_employee_verified",
            lambda emp_id, name: notified.append((emp_id, name))
        )
        _admin_session(client, seed_admin["username"])
        emp_id = seed_employee["employee_id"]
        _upload(client, emp_id, "id_proof")
        _upload(client, emp_id, "address_proof")
        id_did = _latest_doc_id(db_engine, emp_id, "id_proof")
        addr_did = _latest_doc_id(db_engine, emp_id, "address_proof")
        _verify(client, emp_id, id_did, "approve")
        _verify(client, emp_id, addr_did, "approve")
        assert len(notified) == 1

        # Reject a freshly re-uploaded id_proof -- doc_verification_notified_at
        # stays set (the employee WAS fully verified once; rejecting a new
        # doc later doesn't retroactively un-notify or crash).
        _upload(client, emp_id, "id_proof", filename="reupload.pdf")
        new_id_did = _latest_doc_id(db_engine, emp_id, "id_proof")
        resp = _verify(client, emp_id, new_id_did, "reject", reason="Expired.")
        assert resp.status_code in (301, 302)
        assert len(notified) == 1


class TestHrScopeEnforcement:
    """Mirrors the HR-ownership pattern already established for every other
    single-record admin action route (leave_action, ticket_action, ...) --
    an HR session must only be able to upload/verify documents for
    employees assigned to it."""

    def test_hr_cannot_upload_for_unassigned_employee(self, client, seed_hr_admin, seed_employee):
        _admin_session(client, seed_hr_admin["username"], role="hr")
        resp = _upload(client, seed_employee["employee_id"], "id_proof")
        assert resp.status_code in (301, 302)
        assert resp.headers["Location"].endswith("/employees")

    def test_hr_can_upload_for_assigned_employee(self, client, seed_hr_admin, seed_assigned_employee, db_engine):
        _admin_session(client, seed_hr_admin["username"], role="hr")
        resp = _upload(client, seed_assigned_employee["employee_id"], "id_proof")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute(
            "SELECT 1 FROM employee_documents WHERE employee_id=%s AND doc_kind='id_proof'",
            (seed_assigned_employee["employee_id"],),
        )
        assert cur.fetchone() is not None
        cur.close()

    def test_hr_cannot_verify_document_of_unassigned_employee(
        self, client, seed_admin, seed_hr_admin, seed_employee, db_engine
    ):
        # Admin uploads it first (HR has no upload access to this employee either).
        _admin_session(client, seed_admin["username"])
        _upload(client, seed_employee["employee_id"], "id_proof")
        did = _latest_doc_id(db_engine, seed_employee["employee_id"], "id_proof")

        _admin_session(client, seed_hr_admin["username"], role="hr")
        resp = _verify(client, seed_employee["employee_id"], did, "approve")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status FROM employee_documents WHERE id=%s", (did,))
        assert cur.fetchone()[0] == "pending"
        cur.close()

    def test_hr_can_verify_document_of_assigned_employee(
        self, client, seed_hr_admin, seed_assigned_employee, db_engine
    ):
        _admin_session(client, seed_hr_admin["username"], role="hr")
        emp_id = seed_assigned_employee["employee_id"]
        _upload(client, emp_id, "id_proof")
        did = _latest_doc_id(db_engine, emp_id, "id_proof")
        resp = _verify(client, emp_id, did, "approve")
        assert resp.status_code in (301, 302)
        cur = db_engine.cursor()
        cur.execute("SELECT verification_status, verified_by FROM employee_documents WHERE id=%s", (did,))
        row = cur.fetchone()
        assert row[0] == "approved"
        assert row[1] == seed_hr_admin["username"]
        cur.close()
