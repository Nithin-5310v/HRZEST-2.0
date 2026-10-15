"""Tests for blueprints/disbursement.py + utils/payout_utils.py.

REQUIRE_PAYOUT_2FA defaults to False (same convention as REQUIRE_EMAIL_2FA),
so these tests exercise the routes without a TOTP step-up unless a test
explicitly flips that config flag.
"""
import datetime
from utils.payout_utils import validate_ifsc, mask_account_number


def _admin_session(client, seed_admin):
    client.post("/login", data={
        "identifier": seed_admin["username"],
        "password":   seed_admin["password"],
    })
    with client.session_transaction() as sess:
        logged_in = sess.get("admin_logged_in")
    assert logged_in, "Admin login failed for seed_admin"
    return client


class TestIfscValidation:
    def test_valid_ifsc_accepted(self):
        ok, err = validate_ifsc("HDFC0001234")
        assert ok is True
        assert err is None

    def test_lowercase_ifsc_normalized_and_accepted(self):
        ok, err = validate_ifsc("hdfc0001234")
        assert ok is True

    def test_wrong_length_rejected(self):
        ok, err = validate_ifsc("HDFC123")
        assert ok is False
        assert err

    def test_missing_zero_placeholder_rejected(self):
        # Position 5 must be literal '0' in a real IFSC.
        ok, err = validate_ifsc("HDFC1001234")
        assert ok is False

    def test_blank_rejected(self):
        ok, err = validate_ifsc("")
        assert ok is False


class TestMaskAccountNumber:
    def test_masks_all_but_last_four(self):
        assert mask_account_number("123456789012") == "********9012"

    def test_short_number_fully_masked(self):
        assert mask_account_number("123") == "***"

    def test_blank_returns_blank(self):
        assert mask_account_number("") == ""


class TestBankConfigSaveAndEncryption:
    def test_save_encrypts_account_number_and_get_returns_masked(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        resp = client.post("/api/payout/bank_config", json={
            "account_holder_name": "Test Co Pvt Ltd",
            "bank_name": "Test Bank",
            "account_number": "112233445566",
            "ifsc_code": "hdfc0001234",
            "disbursement_day_of_month": 28,
            "approval_lead_days": 3,
            "enabled": True,
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("SELECT account_number, ifsc_code FROM payout_bank_config ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        assert row is not None
        stored_account, stored_ifsc = row
        assert stored_account != "112233445566", "account number was stored in plaintext, not encrypted"
        assert stored_ifsc == "HDFC0001234"

        resp2 = client.get("/api/payout/bank_config")
        cfg = resp2.get_json()["config"]
        assert cfg["account_number_masked"] == "********5566"
        assert cfg["account_number_masked"] != "112233445566"

    def test_invalid_ifsc_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.post("/api/payout/bank_config", json={
            "account_holder_name": "Test Co", "bank_name": "Test Bank",
            "account_number": "112233445566", "ifsc_code": "BADIFSC",
            "disbursement_day_of_month": 1, "approval_lead_days": 2, "enabled": True,
        })
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_blank_account_number_keeps_previous_value(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        client.post("/api/payout/bank_config", json={
            "account_holder_name": "Test Co", "bank_name": "Test Bank",
            "account_number": "999888777666", "ifsc_code": "SBIN0001234",
            "disbursement_day_of_month": 1, "approval_lead_days": 2, "enabled": False,
        })
        cur = db_engine.cursor()
        cur.execute("SELECT account_number FROM payout_bank_config ORDER BY id DESC LIMIT 1")
        first_encrypted = cur.fetchone()[0]
        cur.close()

        # Second save with a blank account_number -- must not corrupt the
        # stored value (same "blank means unchanged" convention as
        # email_config's password field).
        client.post("/api/payout/bank_config", json={
            "account_holder_name": "Test Co Renamed", "bank_name": "Test Bank",
            "account_number": "", "ifsc_code": "SBIN0001234",
            "disbursement_day_of_month": 1, "approval_lead_days": 2, "enabled": True,
        })
        cur = db_engine.cursor()
        cur.execute("SELECT account_holder_name, account_number FROM payout_bank_config ORDER BY id DESC LIMIT 1")
        holder, second_encrypted = cur.fetchone()
        cur.close()
        assert holder == "Test Co Renamed"
        assert second_encrypted == first_encrypted


class TestFirstTimeTotpEnrollment:
    def test_setup_returns_qr_for_admin_with_no_totp_yet(self, client, seed_admin):
        _admin_session(client, seed_admin)
        resp = client.get("/api/settings/2fa/setup")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["already_enabled"] is False
        assert data["qr_code"].startswith("data:")
        assert data["secret"]

    def test_verify_2fa_rejected_before_enrollment_completes(self, client, seed_admin):
        # An admin who never confirmed enrollment has no usable code yet --
        # the plain verify endpoint must not be a backdoor around that.
        _admin_session(client, seed_admin)
        resp = client.post("/api/payout/verify-2fa", json={"code": "000000"})
        assert resp.status_code == 401
        assert resp.get_json()["ok"] is False

    def test_enroll_confirm_with_wrong_code_rejected(self, client, seed_admin):
        _admin_session(client, seed_admin)
        client.get("/api/settings/2fa/setup")  # generates+stores the secret
        resp = client.post("/api/payout/2fa/enable", json={"code": "000000"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_enroll_confirm_with_real_code_unlocks_payout_immediately(self, client, seed_admin, db_engine):
        import pyotp
        # require_payout_2fa is off by default in tests (same convention as
        # require_email_2fa) -- turn it on here so this test actually proves
        # the step-up window opened, rather than passing regardless because
        # the gate itself was bypassed the whole time.
        client.application.config["REQUIRE_PAYOUT_2FA"] = True
        try:
            _admin_session(client, seed_admin)
            setup = client.get("/api/settings/2fa/setup").get_json()
            secret = setup["secret"]
            code = pyotp.TOTP(secret).now()

            # Gate is on and step-up hasn't happened yet -- must be denied.
            denied = client.get("/api/payout/bank_config")
            assert denied.status_code == 403

            resp = client.post("/api/payout/2fa/enable", json={"code": code})
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

            # Confirming enrollment must open the PAYOUT step-up window
            # right away (not just the email one) -- the admin came here to
            # unlock bank details, not Email Settings.
            get_resp = client.get("/api/payout/bank_config")
            assert get_resp.status_code == 200
            assert get_resp.get_json()["ok"] is True

            # And the same TOTP secret now also satisfies the plain verify
            # endpoint on a later visit (enrollment is account-wide, not
            # re-done per gated area).
            code2 = pyotp.TOTP(secret).now()
            verify_resp = client.post("/api/payout/verify-2fa", json={"code": code2})
            assert verify_resp.status_code == 200
            assert verify_resp.get_json()["ok"] is True
        finally:
            client.application.config["REQUIRE_PAYOUT_2FA"] = False


class TestMissingBankDetailsFlaggedAtPrepTime:
    def test_employee_without_bank_details_flagged_before_approval(self, client, seed_admin, seed_employee, db_engine):
        """seed_employee (TST001) has no bank_account/bank_ifsc set -- its
        disbursement item must come out of preparation already marked
        missing_bank_details, visible to the admin BEFORE they approve,
        not discovered only when the run is processed."""
        from blueprints.disbursement import _prepare_one_tenant
        from utils.helpers import encrypt_pii

        _admin_session(client, seed_admin)
        today = datetime.date.today()
        client.post("/api/payout/bank_config", json={
            "account_holder_name": "Test Co", "bank_name": "Test Bank",
            "account_number": "444455556666", "ifsc_code": "UTIB0001234",
            "disbursement_day_of_month": today.day, "approval_lead_days": 5, "enabled": True,
        })

        cur = db_engine.cursor()
        # A second employee WITH complete bank details, to prove the flag is
        # per-employee, not a blanket "no one has bank details" fallback.
        cur.execute("DELETE FROM employees WHERE employee_id='TST002'")
        cur.execute(
            "INSERT INTO employees (employee_id, name, email, password, force_pin_change, "
            "bank_account, bank_name, bank_ifsc) VALUES (%s,%s,%s,%s,0,%s,%s,%s)",
            ("TST002", "Has Bank Details", "tst002@test.local", "x",
             encrypt_pii("987654321098"), "Some Bank", "HDFC0009999")
        )
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s, 1000) "
            "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=1000",
            (seed_employee["employee_id"],)
        )
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day) VALUES ('TST002', 1000) "
            "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=1000"
        )
        cur.execute("DELETE FROM salary_disbursement_runs WHERE year=%s AND month=%s", (today.year, today.month))
        db_engine.commit()
        cur.close()

        with client.application.app_context():
            _prepare_one_tenant("public")

        cur = db_engine.cursor()
        cur.execute(
            "SELECT i.employee_id, i.status FROM salary_disbursement_items i "
            "JOIN salary_disbursement_runs r ON r.id = i.run_id "
            "WHERE r.year=%s AND r.month=%s",
            (today.year, today.month)
        )
        statuses = dict(cur.fetchall())
        cur.execute("DELETE FROM employees WHERE employee_id='TST002'")
        db_engine.commit()
        cur.close()

        assert statuses.get(seed_employee["employee_id"]) == "missing_bank_details"
        assert statuses.get("TST002") == "pending"


class TestRunPreparationIdempotent:
    def test_prepare_does_not_double_create_for_same_month(self, client, seed_admin, seed_employee, db_engine):
        from blueprints.disbursement import _prepare_one_tenant

        _admin_session(client, seed_admin)
        today = datetime.date.today()
        client.post("/api/payout/bank_config", json={
            "account_holder_name": "Test Co", "bank_name": "Test Bank",
            "account_number": "111122223333", "ifsc_code": "ICIC0001234",
            "disbursement_day_of_month": today.day, "approval_lead_days": 5, "enabled": True,
        })
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s, 1000) "
            "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=1000",
            (seed_employee["employee_id"],)
        )
        cur.execute("DELETE FROM salary_disbursement_runs WHERE year=%s AND month=%s", (today.year, today.month))
        db_engine.commit()
        cur.close()

        with client.application.app_context():
            _prepare_one_tenant("public")
            _prepare_one_tenant("public")

        cur = db_engine.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM salary_disbursement_runs WHERE year=%s AND month=%s",
            (today.year, today.month)
        )
        count = cur.fetchone()[0]
        cur.close()
        assert count <= 1, "prepare_pending_disbursements created more than one run for the same month"


class TestApprovalGuards:
    def test_cannot_approve_a_non_pending_run(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_disbursement_runs WHERE year=2020 AND month=1")
        cur.execute(
            "INSERT INTO salary_disbursement_runs (year, month, status, total_amount, employee_count) "
            "VALUES (2020, 1, 'completed', 500, 1) RETURNING id"
        )
        run_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        resp = client.post(f"/disbursement/{run_id}/approve", follow_redirects=False)
        assert resp.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM salary_disbursement_runs WHERE id=%s", (run_id,))
        status = cur.fetchone()[0]
        cur.close()
        assert status == "completed", "approving a non-pending run must not change its status"

    def test_cannot_approve_the_same_run_twice(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_disbursement_runs WHERE year=2020 AND month=2")
        cur.execute(
            "INSERT INTO salary_disbursement_runs (year, month, status, total_amount, employee_count) "
            "VALUES (2020, 2, 'pending_approval', 0, 0) RETURNING id"
        )
        run_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()

        client.post(f"/disbursement/{run_id}/approve", follow_redirects=False)
        cur = db_engine.cursor()
        cur.execute("SELECT status FROM salary_disbursement_runs WHERE id=%s", (run_id,))
        status_after_first = cur.fetchone()[0]
        cur.close()
        assert status_after_first != "pending_approval"

        # Second approve attempt on the now-non-pending run must be rejected.
        resp2 = client.post(f"/disbursement/{run_id}/approve", follow_redirects=False)
        assert resp2.status_code == 302


class _RacingConnection:
    """Wraps the real connection the approve/reject route opens, so that
    right after the route's status SELECT returns (its check), a second
    "request" flips the row on the test's own connection -- the exact
    check-then-update interleaving a double-click or two admin tabs produce.
    Everything else delegates to the real connection untouched."""

    def __init__(self, real, race):
        self._real = real
        self._race = race

    def cursor(self, *args, **kwargs):
        return _RacingCursor(self._real.cursor(*args, **kwargs), self._race)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _RacingCursor:
    def __init__(self, real, race):
        self._real = real
        self._race = race
        self._fired = False

    def execute(self, sql, *args, **kwargs):
        result = self._real.execute(sql, *args, **kwargs)
        if not self._fired and sql.lstrip().upper().startswith("SELECT STATUS"):
            self._fired = True
            self._race()
        return result

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestApprovalRace:
    def _pending_run(self, db_engine, month):
        cur = db_engine.cursor()
        cur.execute("DELETE FROM salary_disbursement_runs WHERE year=2020 AND month=%s", (month,))
        cur.execute(
            "INSERT INTO salary_disbursement_runs (year, month, status, total_amount, employee_count) "
            "VALUES (2020, %s, 'pending_approval', 0, 0) RETURNING id", (month,)
        )
        run_id = cur.fetchone()[0]
        db_engine.commit()
        cur.close()
        return run_id

    def _install_race(self, monkeypatch, db_engine, run_id, winner_status):
        import blueprints.disbursement as disbursement
        real_get = disbursement.get_db_connection
        opened = {"n": 0}

        def race():
            cur = db_engine.cursor()
            cur.execute(
                "UPDATE salary_disbursement_runs SET status=%s, approved_by='winner' WHERE id=%s",
                (winner_status, run_id),
            )
            db_engine.commit()
            cur.close()

        def racing_get():
            conn = real_get()
            opened["n"] += 1
            return _RacingConnection(conn, race) if opened["n"] == 1 else conn

        monkeypatch.setattr(disbursement, "get_db_connection", racing_get)

    def test_approve_loser_of_a_race_never_processes_the_run(self, client, seed_admin, db_engine, monkeypatch):
        import blueprints.disbursement as disbursement
        _admin_session(client, seed_admin)
        run_id = self._pending_run(db_engine, 3)
        processed = []
        monkeypatch.setattr(disbursement, "_process_disbursement_run", lambda rid: processed.append(rid))
        self._install_race(monkeypatch, db_engine, run_id, "processing")

        resp = client.post(f"/disbursement/{run_id}/approve", follow_redirects=False)
        assert resp.status_code == 302

        assert processed == [], "the request that lost the race must not run the payout pass a second time"
        cur = db_engine.cursor()
        cur.execute("SELECT status, approved_by FROM salary_disbursement_runs WHERE id=%s", (run_id,))
        status, approved_by = cur.fetchone()
        cur.close()
        assert status == "processing"
        assert approved_by == "winner", "the loser must not overwrite the winner's approver"

    def test_reject_loser_of_a_race_does_not_cancel_an_approved_run(self, client, seed_admin, db_engine, monkeypatch):
        _admin_session(client, seed_admin)
        run_id = self._pending_run(db_engine, 4)
        self._install_race(monkeypatch, db_engine, run_id, "processing")

        resp = client.post(f"/disbursement/{run_id}/reject", follow_redirects=False)
        assert resp.status_code == 302

        cur = db_engine.cursor()
        cur.execute("SELECT status FROM salary_disbursement_runs WHERE id=%s", (run_id,))
        status = cur.fetchone()[0]
        cur.close()
        assert status == "processing", "a cancel that lost the race must not clobber an already-approved run"


class TestStubNeverClaimsSuccess:
    def test_unconfigured_provider_marks_items_stub_not_completed(self, client, seed_admin, seed_employee, db_engine, monkeypatch):
        monkeypatch.delenv("PAYOUT_PROVIDER", raising=False)
        from blueprints.disbursement import _process_disbursement_run
        from utils.helpers import encrypt_pii

        _admin_session(client, seed_admin)
        client.post("/api/payout/bank_config", json={
            "account_holder_name": "Test Co", "bank_name": "Test Bank",
            "account_number": "555566667777", "ifsc_code": "AXIS0001234",
            "disbursement_day_of_month": 1, "approval_lead_days": 2, "enabled": True,
        })

        cur = db_engine.cursor()
        # This test is specifically about the "no PAYOUT_PROVIDER configured"
        # path, not the separate "employee has no bank details" path (that
        # one's covered by TestMissingBankDetailsFlaggedAtPrepTime) -- give
        # the employee real bank details so execute_payout() is actually
        # reached and rejects for the right reason.
        cur.execute(
            "UPDATE employees SET bank_account=%s, bank_name=%s, bank_ifsc=%s WHERE employee_id=%s",
            (encrypt_pii("111122223333"), "Employee Bank", "SBIN0001111", seed_employee["employee_id"])
        )
        cur.execute("DELETE FROM salary_disbursement_runs WHERE year=2020 AND month=3")
        cur.execute(
            "INSERT INTO salary_disbursement_runs (year, month, status, total_amount, employee_count) "
            "VALUES (2020, 3, 'processing', 1000, 1) RETURNING id"
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO salary_disbursement_items (run_id, employee_id, amount) VALUES (%s,%s,1000)",
            (run_id, seed_employee["employee_id"])
        )
        cur.execute("DELETE FROM payroll_runs WHERE year=2020 AND month=3")
        db_engine.commit()
        cur.close()

        with client.application.app_context():
            _process_disbursement_run(run_id)

        cur = db_engine.cursor()
        cur.execute("SELECT status, email_sent FROM salary_disbursement_items WHERE run_id=%s", (run_id,))
        item_status, email_sent = cur.fetchone()
        cur.execute("SELECT status FROM salary_disbursement_runs WHERE id=%s", (run_id,))
        run_status = cur.fetchone()[0]
        cur.execute("SELECT 1 FROM payroll_runs WHERE year=2020 AND month=3")
        payroll_locked = cur.fetchone() is not None
        cur.close()

        assert item_status in ("stub_not_configured", "failed")
        assert item_status != "sent", "stub path must never mark an item as successfully sent"
        assert email_sent == 0, "no payslip should be emailed for an item that wasn't actually paid"
        assert run_status == "failed"
        assert not payroll_locked, "payroll_runs must not be locked for a run that didn't actually complete"
