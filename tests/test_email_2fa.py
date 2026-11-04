"""Tests for the Email Settings 2FA step-up gate: utils/totp.py (TOTP
enrollment/verification) and utils/auth.py's require_email_2fa, plus the
/api/settings/2fa/*, /api/settings/email, and /api/settings/email/
reveal-password routes in blueprints/admin_views.py."""
import time
import pyotp
import pytest
import utils.auth as auth_module
import utils.totp as totp_module


def _admin_session(client, username):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = username


class TestTotpUtil:
    def test_new_secret_generated_when_none_exists(self, seed_admin, db_engine):
        secret, enabled = totp_module.get_or_create_admin_totp_secret(seed_admin["username"])
        assert secret and len(secret) >= 16
        assert enabled is False
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_existing_secret_reused_not_rotated(self, seed_admin, db_engine):
        secret1, _ = totp_module.get_or_create_admin_totp_secret(seed_admin["username"])
        secret2, _ = totp_module.get_or_create_admin_totp_secret(seed_admin["username"])
        assert secret1 == secret2
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_verify_rejects_before_enabled(self, seed_admin, db_engine):
        secret, _ = totp_module.get_or_create_admin_totp_secret(seed_admin["username"])
        code = pyotp.TOTP(secret).now()
        assert totp_module.verify_totp_code(seed_admin["username"], code, require_enabled=True) is False
        assert totp_module.verify_totp_code(seed_admin["username"], code, require_enabled=False) is True
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_verify_rejects_wrong_code(self, seed_admin, db_engine):
        totp_module.get_or_create_admin_totp_secret(seed_admin["username"])
        totp_module.mark_totp_enabled(seed_admin["username"])
        assert totp_module.verify_totp_code(seed_admin["username"], "000000") is False
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_verify_rejects_malformed_code(self, seed_admin):
        assert totp_module.verify_totp_code(seed_admin["username"], "12345") is False
        assert totp_module.verify_totp_code(seed_admin["username"], "abcdef") is False
        assert totp_module.verify_totp_code(seed_admin["username"], "") is False

    def test_qr_data_uri_shape(self):
        uri = totp_module.totp_qr_data_uri("test_admin", pyotp.random_base32())
        assert uri.startswith("data:image/png;base64,")


class TestStepUpSession:
    def test_no_flag_means_invalid(self, client):
        with client.application.test_request_context():
            assert auth_module.email_settings_step_up_valid() is False

    def test_fresh_refresh_is_valid(self, client):
        with client.application.test_request_context():
            auth_module.email_settings_step_up_refresh()
            assert auth_module.email_settings_step_up_valid() is True

    def test_expired_flag_is_invalid(self, client):
        from flask import session
        with client.application.test_request_context():
            session["email_2fa_verified_at"] = time.time() - auth_module.EMAIL_2FA_WINDOW_SEC - 1
            assert auth_module.email_settings_step_up_valid() is False

    def test_clear_removes_flag(self, client):
        with client.application.test_request_context():
            auth_module.email_settings_step_up_refresh()
            auth_module.email_settings_step_up_clear()
            assert auth_module.email_settings_step_up_valid() is False


@pytest.fixture
def enrolled_admin(seed_admin, db_engine):
    """A seeded admin with TOTP already enrolled+enabled; returns (username, secret)."""
    secret, _ = totp_module.get_or_create_admin_totp_secret(seed_admin["username"])
    totp_module.mark_totp_enabled(seed_admin["username"])
    yield seed_admin["username"], secret
    cur = db_engine.cursor()
    cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                (seed_admin["username"],))
    db_engine.commit()
    cur.close()


@pytest.fixture
def seeded_email_config(db_engine):
    cur = db_engine.cursor()
    from utils.helpers import encrypt_pii
    cur.execute("DELETE FROM email_config")
    cur.execute(
        "INSERT INTO email_config (smtp_host, smtp_port, smtp_user, smtp_pass, from_name, from_email) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        ("smtp.example.com", 587, "bot@example.com", encrypt_pii("RealPassw0rd!"), "HR Team", "bot@example.com"),
    )
    db_engine.commit()
    cur.close()
    yield
    cur = db_engine.cursor()
    cur.execute("DELETE FROM email_config")
    db_engine.commit()
    cur.close()


class TestEmailSettingsRoutes:
    def test_get_email_without_stepup_is_403(self, client, seed_admin, seeded_email_config):
        # require_email_2fa is off by default in tests (same convention as
        # require_payout_2fa -- see tests/test_disbursement.py) -- turn it on
        # here so this actually proves the gate blocks, rather than passing
        # for the wrong reason (gate bypassed the whole time).
        client.application.config["REQUIRE_EMAIL_2FA"] = True
        try:
            _admin_session(client, seed_admin["username"])
            resp = client.get("/api/settings/email")
            assert resp.status_code == 403
            assert "2FA" in resp.get_json()["msg"]
        finally:
            client.application.config["REQUIRE_EMAIL_2FA"] = False

    def test_get_email_requires_admin_first(self, client, seeded_email_config):
        resp = client.get("/api/settings/email")
        assert resp.status_code in (302, 401)

    def test_2fa_setup_returns_qr_when_not_enrolled(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/api/settings/2fa/setup")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["already_enabled"] is False
        assert data["qr_code"].startswith("data:image/png;base64,")
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_2fa_setup_reports_already_enabled(self, client, enrolled_admin):
        username, _ = enrolled_admin
        _admin_session(client, username)
        resp = client.get("/api/settings/2fa/setup")
        assert resp.get_json() == {"ok": True, "already_enabled": True}

    def test_enable_with_wrong_code_rejected(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        client.get("/api/settings/2fa/setup")
        resp = client.post("/api/settings/2fa/enable", json={"code": "000000"})
        assert resp.status_code == 400
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_enable_with_correct_code_succeeds(self, client, seed_admin, db_engine):
        _admin_session(client, seed_admin["username"])
        setup = client.get("/api/settings/2fa/setup").get_json()
        code = pyotp.TOTP(setup["secret"]).now()
        resp = client.post("/api/settings/2fa/enable", json={"code": code})
        assert resp.get_json()["ok"] is True
        cur = db_engine.cursor()
        cur.execute("SELECT totp_enabled FROM admin_users WHERE username=%s", (seed_admin["username"],))
        assert cur.fetchone()[0] == 1
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_enable_unlocks_immediately_without_separate_verify_call(self, client, seed_admin, seeded_email_config, db_engine):
        # Regression test: confirming enrollment must itself open the 2FA
        # step-up session. Previously it didn't, so the very next request
        # (fetching the SMTP config right after enabling) 403'd and the
        # frontend fell back to re-showing the enrollment screen — which
        # looked exactly like the entered code kept being rejected.
        _admin_session(client, seed_admin["username"])
        setup = client.get("/api/settings/2fa/setup").get_json()
        code = pyotp.TOTP(setup["secret"]).now()
        enable_resp = client.post("/api/settings/2fa/enable", json={"code": code})
        assert enable_resp.get_json()["ok"] is True

        get_resp = client.get("/api/settings/email")
        assert get_resp.status_code == 200

        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=NULL, totp_enabled=0 WHERE username=%s",
                    (seed_admin["username"],))
        db_engine.commit()
        cur.close()

    def test_reset_requires_correct_password(self, client, enrolled_admin):
        username, _ = enrolled_admin
        _admin_session(client, username)
        resp = client.post("/api/settings/2fa/reset", json={"password": "wrong"})
        assert resp.status_code == 401
        assert resp.get_json()["ok"] is False

    def test_reset_with_correct_password_issues_fresh_secret(self, client, enrolled_admin, seed_admin, db_engine):
        username, old_secret = enrolled_admin
        _admin_session(client, username)
        resp = client.post("/api/settings/2fa/reset", json={"password": seed_admin["password"]})
        assert resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("SELECT totp_secret, totp_enabled FROM admin_users WHERE username=%s", (username,))
        secret_enc, enabled = cur.fetchone()
        cur.close()
        assert secret_enc is None
        assert enabled == 0

        setup = client.get("/api/settings/2fa/setup").get_json()
        assert setup["already_enabled"] is False
        assert setup["secret"] != old_secret

    def test_verify_2fa_wrong_code_denied(self, client, enrolled_admin):
        client.application.config["REQUIRE_EMAIL_2FA"] = True
        try:
            username, _ = enrolled_admin
            _admin_session(client, username)
            resp = client.post("/api/settings/verify-2fa", json={"code": "111111"})
            assert resp.status_code == 401
            get_resp = client.get("/api/settings/email")
            assert get_resp.status_code == 403
        finally:
            client.application.config["REQUIRE_EMAIL_2FA"] = False

    def test_verify_2fa_correct_code_unlocks(self, client, enrolled_admin, seeded_email_config):
        username, secret = enrolled_admin
        _admin_session(client, username)
        code = pyotp.TOTP(secret).now()
        resp = client.post("/api/settings/verify-2fa", json={"code": code})
        assert resp.get_json()["ok"] is True

        get_resp = client.get("/api/settings/email")
        assert get_resp.status_code == 200
        cfg = get_resp.get_json()["config"]
        assert cfg["password"] == "********"
        assert cfg["host"] == "smtp.example.com"

    def test_reveal_password_returns_real_value(self, client, enrolled_admin, seeded_email_config):
        username, secret = enrolled_admin
        _admin_session(client, username)
        code = pyotp.TOTP(secret).now()
        client.post("/api/settings/verify-2fa", json={"code": code})
        resp = client.post("/api/settings/email/reveal-password")
        assert resp.get_json() == {"ok": True, "password": "RealPassw0rd!"}

    def test_reveal_password_requires_own_stepup(self, client, enrolled_admin, seeded_email_config):
        client.application.config["REQUIRE_EMAIL_2FA"] = True
        try:
            username, _ = enrolled_admin
            _admin_session(client, username)
            resp = client.post("/api/settings/email/reveal-password")
            assert resp.status_code == 403
        finally:
            client.application.config["REQUIRE_EMAIL_2FA"] = False

    def test_lock_reasserts_gate(self, client, enrolled_admin, seeded_email_config):
        client.application.config["REQUIRE_EMAIL_2FA"] = True
        try:
            username, secret = enrolled_admin
            _admin_session(client, username)
            code = pyotp.TOTP(secret).now()
            client.post("/api/settings/verify-2fa", json={"code": code})
            assert client.get("/api/settings/email").status_code == 200

            client.post("/api/settings/2fa/lock")
            assert client.get("/api/settings/email").status_code == 403
        finally:
            client.application.config["REQUIRE_EMAIL_2FA"] = False

    def test_save_with_masked_password_keeps_existing(self, client, enrolled_admin, seeded_email_config, db_engine):
        from utils.helpers import decrypt_pii
        username, secret = enrolled_admin
        _admin_session(client, username)
        code = pyotp.TOTP(secret).now()
        client.post("/api/settings/verify-2fa", json={"code": code})

        resp = client.post("/api/settings/email", json={
            "host": "smtp.newhost.com", "port": 465, "user": "bot@example.com",
            "password": "********", "from_name": "HR", "from_email": "bot@example.com",
        })
        assert resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("SELECT smtp_host, smtp_pass FROM email_config ORDER BY id DESC LIMIT 1")
        host, enc_pass = cur.fetchone()
        cur.close()
        assert host == "smtp.newhost.com"
        assert enc_pass.startswith("hz2:"), "kept password must stay encrypted at rest, not be re-saved as plaintext"
        assert decrypt_pii(enc_pass) == "RealPassw0rd!"   # unchanged, not corrupted

    def test_save_with_new_password_encrypts_it(self, client, enrolled_admin, seeded_email_config, db_engine):
        from utils.helpers import decrypt_pii
        username, secret = enrolled_admin
        _admin_session(client, username)
        code = pyotp.TOTP(secret).now()
        client.post("/api/settings/verify-2fa", json={"code": code})

        resp = client.post("/api/settings/email", json={
            "host": "smtp.example.com", "port": 587, "user": "bot@example.com",
            "password": "BrandNewPassw0rd!", "from_name": "HR", "from_email": "bot@example.com",
        })
        assert resp.get_json()["ok"] is True

        cur = db_engine.cursor()
        cur.execute("SELECT smtp_pass FROM email_config ORDER BY id DESC LIMIT 1")
        enc_pass = cur.fetchone()[0]
        cur.close()
        assert decrypt_pii(enc_pass) == "BrandNewPassw0rd!"

    def test_legacy_email_config_get_redirects_to_gated_tab(self, client, seed_admin):
        _admin_session(client, seed_admin["username"])
        resp = client.get("/email_config", follow_redirects=False)
        assert resp.status_code == 302
        assert "/settings" in resp.headers.get("Location", "")

    def test_legacy_email_config_post_cannot_bypass_stepup_gate(self, client, seed_admin, db_engine):
        """Regression test: POST /email_config (blueprints/payroll.py) writes
        the same smtp_pass column as the gated /api/settings/email but, until
        this fix, had no step-up check at all -- a plain admin session could
        silently rewrite the outbound SMTP credentials (including setting a
        brand-new password) without ever passing 2FA, entirely bypassing the
        gate this whole test file is about. No template posts to this route
        anymore (only GETs it), so this was a live-but-UI-unreachable hole,
        not a used flow -- see blueprints/payroll.py's email_config()."""
        client.application.config["REQUIRE_EMAIL_2FA"] = True
        try:
            _admin_session(client, seed_admin["username"])
            resp = client.post("/email_config", data={
                "smtp_host": "evil.example.com", "smtp_port": "587",
                "smtp_user": "attacker@evil.example.com", "smtp_pass": "hijacked",
            })
            assert resp.status_code == 403
            assert "2FA" in resp.get_json()["msg"]

            cur = db_engine.cursor()
            cur.execute("SELECT smtp_host FROM email_config ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            cur.close()
            assert row is None or row[0] != "evil.example.com"
        finally:
            client.application.config["REQUIRE_EMAIL_2FA"] = False
            cur = db_engine.cursor()
            cur.execute("DELETE FROM email_config WHERE smtp_host='evil.example.com'")
            db_engine.commit()
            cur.close()

    def test_legacy_email_config_post_still_works_after_stepup(self, client, enrolled_admin, db_engine):
        """The fix must not break the (now unguarded-only-via-2FA, not
        unreachable) legacy route for an admin who actually completed the
        step-up -- same gate, same shared session key as /api/settings/email."""
        import pyotp
        client.application.config["REQUIRE_EMAIL_2FA"] = True
        try:
            username, secret = enrolled_admin
            _admin_session(client, username)
            code = pyotp.TOTP(secret).now()
            verify_resp = client.post("/api/settings/verify-2fa", json={"code": code})
            assert verify_resp.get_json()["ok"] is True

            resp = client.post("/email_config", data={
                "smtp_host": "legit.example.com", "smtp_port": "587",
                "smtp_user": "bot@legit.example.com", "smtp_pass": "SomePassw0rd!",
            }, follow_redirects=False)
            assert resp.status_code == 302

            cur = db_engine.cursor()
            cur.execute("SELECT smtp_host FROM email_config ORDER BY id DESC LIMIT 1")
            assert cur.fetchone()[0] == "legit.example.com"
            cur.close()
        finally:
            client.application.config["REQUIRE_EMAIL_2FA"] = False
            cur = db_engine.cursor()
            cur.execute("DELETE FROM email_config WHERE smtp_host='legit.example.com'")
            db_engine.commit()
            cur.close()
