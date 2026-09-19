"""Tests for utils/document_verification.py's automated company-KYC
review pipeline, and blueprints/auth.py's new login gate that requires
doc_review_status='approved' before an admin session can complete.

extract_kyc_fields() (the actual Claude vision call) is always mocked here
-- these tests exercise run_automated_verification()'s branching (approve /
reject-on-duplicate / inconclusive-falls-back-to-manual-queue) and the
login gate, never make a real API call.
"""
from extensions import app as flask_app
from utils.auth import generate_password_hash


def _insert_tenant(db_engine, company_name, subdomain, admin_email="owner@test.local",
                   gst=None, pan=None, cin=None, status="active"):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.tenants (company_name, subdomain, db_name, admin_email, gst_number, pan, cin, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (company_name, subdomain, "att_" + subdomain.replace("-", "_"), admin_email, gst, pan, cin, status)
    )
    tenant_id = cur.fetchone()[0]
    cur.close()
    return tenant_id


def _insert_application(db_engine, tenant_id, company_name, admin_username, admin_email,
                        gst=None, doc_review_status="pending_review"):
    cur = db_engine.cursor()
    cur.execute(
        "INSERT INTO att_master.tenant_applications "
        "(company_name, subdomain, admin_username, admin_email, admin_password_hash, access_token_hash, "
        "gst_number, status, doc_review_status, tenant_id, "
        "doc_registration_cert, doc_address_proof, doc_visiting_card, doc_name_board_photo) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'provisioned',%s,%s,'x','x','x','x') RETURNING id",
        (company_name, admin_username, admin_username, admin_email, generate_password_hash("x"), "tokhash",
         gst, doc_review_status, tenant_id)
    )
    app_id = cur.fetchone()[0]
    cur.close()
    return app_id


def _cleanup(db_engine, tenant_ids, application_ids):
    cur = db_engine.cursor()
    for aid in application_ids:
        cur.execute("DELETE FROM att_master.tenant_applications WHERE id=%s", (aid,))
    for tid in tenant_ids:
        cur.execute("DELETE FROM att_master.tenants WHERE id=%s", (tid,))
    cur.close()


class TestRunAutomatedVerification:
    def test_confident_pass_approves_and_backfills_pan_cin(self, db_engine, monkeypatch):
        import utils.document_verification as dv
        tenant_id = _insert_tenant(db_engine, "Auto Pass Co", "auto-pass-co", gst="27AAAAA0000A1Z5")
        app_id = _insert_application(db_engine, tenant_id, "Auto Pass Co", "auto_pass_admin",
                                     "autopass@test.local", gst="27AAAAA0000A1Z5")
        try:
            monkeypatch.setattr(dv, "extract_kyc_fields", lambda application: (
                {"gst_number": "27AAAAA0000A1Z5", "pan": "AAAAA1234A", "cin": "U12345MH2020PTC123456",
                 "company_name": "Auto Pass Co", "address": "123 Test St", "legible": True,
                 "confidence": "high", "notes": ""},
                None,
            ))
            outcome = dv.run_automated_verification(app_id)
            assert "auto-approved" in outcome

            cur = db_engine.cursor()
            cur.execute("SELECT doc_review_status, reviewed_by FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            row = cur.fetchone()
            assert row[0] == "approved"
            assert row[1] == "system:automated_verification"
            cur.execute("SELECT status, pan, cin FROM att_master.tenants WHERE id=%s", (tenant_id,))
            trow = cur.fetchone()
            assert trow[0] == "active"
            assert trow[1] == "AAAAA1234A"
            assert trow[2] == "U12345MH2020PTC123456"
            cur.close()
        finally:
            _cleanup(db_engine, [tenant_id], [app_id])

    def test_confident_gst_duplicate_rejects_and_suspends(self, db_engine, monkeypatch):
        import utils.document_verification as dv
        existing_tenant_id = _insert_tenant(db_engine, "Original Owner Co", "original-owner-co", gst="27BBBBB0000B1Z5")
        new_tenant_id = _insert_tenant(db_engine, "Copycat Co", "copycat-co")
        app_id = _insert_application(db_engine, new_tenant_id, "Copycat Co", "copycat_admin", "copycat@test.local")
        try:
            monkeypatch.setattr(dv, "extract_kyc_fields", lambda application: (
                {"gst_number": "27BBBBB0000B1Z5", "pan": None, "cin": None,
                 "company_name": "Copycat Co", "address": "456 Test Ave", "legible": True,
                 "confidence": "high", "notes": ""},
                None,
            ))
            outcome = dv.run_automated_verification(app_id)
            assert "auto-rejected" in outcome
            assert "duplicate gst" in outcome

            cur = db_engine.cursor()
            cur.execute("SELECT doc_review_status, rejection_reason FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            row = cur.fetchone()
            assert row[0] == "rejected"
            assert "duplicate" in row[1].lower()
            cur.execute("SELECT status FROM att_master.tenants WHERE id=%s", (new_tenant_id,))
            assert cur.fetchone()[0] == "suspended"
            # The ORIGINAL tenant (the real owner of that GST) must be untouched.
            cur.execute("SELECT status FROM att_master.tenants WHERE id=%s", (existing_tenant_id,))
            assert cur.fetchone()[0] == "active"
            cur.close()
        finally:
            _cleanup(db_engine, [existing_tenant_id, new_tenant_id], [app_id])

    def test_own_tenants_gst_is_not_treated_as_a_duplicate_of_itself(self, db_engine, monkeypatch):
        """Once approved once, gst_number is already on this SAME tenant's
        own row -- a re-run (e.g. a retried job) must not treat that as a
        conflict against itself."""
        import utils.document_verification as dv
        tenant_id = _insert_tenant(db_engine, "Self Match Co", "self-match-co", gst="27CCCCC0000C1Z5")
        app_id = _insert_application(db_engine, tenant_id, "Self Match Co", "selfmatch_admin",
                                     "selfmatch@test.local", gst="27CCCCC0000C1Z5")
        try:
            monkeypatch.setattr(dv, "extract_kyc_fields", lambda application: (
                {"gst_number": "27CCCCC0000C1Z5", "pan": None, "cin": None,
                 "company_name": "Self Match Co", "address": "789 Test Rd", "legible": True,
                 "confidence": "high", "notes": ""},
                None,
            ))
            outcome = dv.run_automated_verification(app_id)
            assert "auto-approved" in outcome
            cur = db_engine.cursor()
            cur.execute("SELECT status FROM att_master.tenants WHERE id=%s", (tenant_id,))
            assert cur.fetchone()[0] == "active"
            cur.close()
        finally:
            _cleanup(db_engine, [tenant_id], [app_id])

    def test_illegible_extraction_leaves_pending_review_for_manual_queue(self, db_engine, monkeypatch):
        import utils.document_verification as dv
        tenant_id = _insert_tenant(db_engine, "Blurry Scan Co", "blurry-scan-co")
        app_id = _insert_application(db_engine, tenant_id, "Blurry Scan Co", "blurry_admin", "blurry@test.local")
        try:
            monkeypatch.setattr(dv, "extract_kyc_fields", lambda application: (
                {"gst_number": None, "pan": None, "cin": None, "company_name": None, "address": None,
                 "legible": False, "confidence": "low", "notes": "scans too dark to read"},
                None,
            ))
            outcome = dv.run_automated_verification(app_id)
            assert "inconclusive" in outcome
            cur = db_engine.cursor()
            cur.execute("SELECT doc_review_status FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            # Untouched -- still exactly what the existing manual queue
            # (blueprints/platform_admin.py's platform_admin_applications_queue())
            # already filters on, zero changes needed there.
            assert cur.fetchone()[0] == "pending_review"
            cur.close()
        finally:
            _cleanup(db_engine, [tenant_id], [app_id])

    def test_extraction_failure_leaves_pending_review(self, db_engine, monkeypatch):
        import utils.document_verification as dv
        tenant_id = _insert_tenant(db_engine, "Api Down Co", "api-down-co")
        app_id = _insert_application(db_engine, tenant_id, "Api Down Co", "apidown_admin", "apidown@test.local")
        try:
            monkeypatch.setattr(dv, "extract_kyc_fields", lambda application: (None, "network timeout"))
            outcome = dv.run_automated_verification(app_id)
            assert "inconclusive" in outcome
            cur = db_engine.cursor()
            cur.execute("SELECT doc_review_status FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            assert cur.fetchone()[0] == "pending_review"
            cur.close()
        finally:
            _cleanup(db_engine, [tenant_id], [app_id])

    def test_typed_vs_extracted_gst_mismatch_is_inconclusive_not_autoreject(self, db_engine, monkeypatch):
        """A typed-vs-document mismatch is a signal for a human, never an
        auto-reject -- a real applicant's own typo shouldn't be treated
        the same as a confirmed duplicate."""
        import utils.document_verification as dv
        tenant_id = _insert_tenant(db_engine, "Typo Co", "typo-co")
        app_id = _insert_application(db_engine, tenant_id, "Typo Co", "typo_admin", "typo@test.local",
                                     gst="27DDDDD0000D1Z5")
        try:
            monkeypatch.setattr(dv, "extract_kyc_fields", lambda application: (
                {"gst_number": "27EEEEE0000E1Z5", "pan": None, "cin": None,  # different from typed
                 "company_name": "Typo Co", "address": "1 Test Ln", "legible": True,
                 "confidence": "high", "notes": ""},
                None,
            ))
            outcome = dv.run_automated_verification(app_id)
            assert "inconclusive" in outcome
            cur = db_engine.cursor()
            cur.execute("SELECT doc_review_status FROM att_master.tenant_applications WHERE id=%s", (app_id,))
            assert cur.fetchone()[0] == "pending_review"
            cur.close()
        finally:
            _cleanup(db_engine, [tenant_id], [app_id])

    def test_not_awaiting_review_is_skipped(self, db_engine, monkeypatch):
        import utils.document_verification as dv
        tenant_id = _insert_tenant(db_engine, "Already Approved Co", "already-approved-co")
        app_id = _insert_application(db_engine, tenant_id, "Already Approved Co", "aa_admin", "aa@test.local",
                                     doc_review_status="approved")
        try:
            called = []
            monkeypatch.setattr(dv, "extract_kyc_fields", lambda application: called.append(1) or (None, "should not be called"))
            outcome = dv.run_automated_verification(app_id)
            assert outcome == "not awaiting review, skipped"
            assert not called
        finally:
            _cleanup(db_engine, [tenant_id], [app_id])


class TestLoginGateBlocksUntilApproved:
    def _admin_session_login(self, client, subdomain, username, password):
        return client.post(f"/{subdomain}/login", data={"identifier": username, "password": password},
                           follow_redirects=False)

    def test_login_blocked_while_pending_review(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        subdomain = "kyc-gate-pending"
        schema = "att_" + subdomain.replace("-", "_")
        cur = db_engine.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
        cur.close()
        try:
            from blueprints.org import provision_tenant
            from utils.auth import generate_password_hash as _gph
            with flask_app.app_context():
                ok, error, portal_url, checkin_url = provision_tenant(
                    "KYC Gate Pending Co", subdomain, "kycgate_admin",
                    _gph("Test@1234"), "kycgate@test.local", subscription_status="trialing",
                )
            assert ok, error
            cur = db_engine.cursor()
            cur.execute("SELECT id FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            tenant_id = cur.fetchone()[0]
            cur.close()
            app_id = _insert_application(db_engine, tenant_id, "KYC Gate Pending Co", "kycgate_admin",
                                         "kycgate@test.local")

            resp = self._admin_session_login(client, subdomain, "kycgate_admin", "Test@1234")
            assert resp.status_code == 200
            assert b"still being verified" in resp.data
            with client.session_transaction() as sess:
                assert not sess.get("admin_logged_in")
            _cleanup(db_engine, [], [app_id])
        finally:
            cur = db_engine.cursor()
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            cur.close()

    def test_login_succeeds_once_approved(self, client, db_engine, monkeypatch):
        from app import init_master_db
        init_master_db()
        subdomain = "kyc-gate-approved"
        schema = "att_" + subdomain.replace("-", "_")
        cur = db_engine.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
        cur.close()
        try:
            from blueprints.org import provision_tenant
            from utils.auth import generate_password_hash as _gph
            with flask_app.app_context():
                ok, error, portal_url, checkin_url = provision_tenant(
                    "KYC Gate Approved Co", subdomain, "kycgateok_admin",
                    _gph("Test@1234"), "kycgateok@test.local", subscription_status="trialing",
                )
            assert ok, error
            cur = db_engine.cursor()
            cur.execute("SELECT id FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            tenant_id = cur.fetchone()[0]
            cur.close()
            app_id = _insert_application(db_engine, tenant_id, "KYC Gate Approved Co", "kycgateok_admin",
                                         "kycgateok@test.local", doc_review_status="approved")

            resp = self._admin_session_login(client, subdomain, "kycgateok_admin", "Test@1234")
            assert resp.status_code in (301, 302)
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
            _cleanup(db_engine, [], [app_id])
        finally:
            cur = db_engine.cursor()
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            cur.close()

    def test_no_application_row_at_all_is_not_blocked(self, client, db_engine, monkeypatch):
        """Platform Admin's own always-free direct-create tenants (and any
        legacy pre-KYC tenant) never get a tenant_applications row at all
        -- must never be blocked by this gate."""
        from app import init_master_db
        init_master_db()
        subdomain = "kyc-gate-no-application"
        schema = "att_" + subdomain.replace("-", "_")
        cur = db_engine.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
        cur.close()
        try:
            from blueprints.org import provision_tenant
            from utils.auth import generate_password_hash as _gph
            with flask_app.app_context():
                ok, error, portal_url, checkin_url = provision_tenant(
                    "No Application Co", subdomain, "noapp_admin",
                    _gph("Test@1234"), "noapp@test.local",
                )
            assert ok, error
            resp = self._admin_session_login(client, subdomain, "noapp_admin", "Test@1234")
            assert resp.status_code in (301, 302)
            with client.session_transaction() as sess:
                assert sess.get("admin_logged_in") is True
        finally:
            cur = db_engine.cursor()
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute("DELETE FROM att_master.tenants WHERE subdomain=%s", (subdomain,))
            cur.close()
