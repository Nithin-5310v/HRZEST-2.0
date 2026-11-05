# -*- coding: utf-8 -*-
"""Open-redirect and step-up MFA hardening (security review, 2026-11)."""
import pyotp
import pytest

from extensions import app as flask_app
from utils.helpers import _safe_redirect


class TestSafeRedirect:
    @pytest.mark.parametrize("dest", ["/\evil.com", "/\/evil.com", "/\t/evil.com", "/\n/evil.com",
                                      "//evil.com", "https://evil.com", "evil.com", ""])
    def test_offsite_or_ambiguous_targets_fall_back(self, dest):
        with flask_app.test_request_context("/"):
            assert _safe_redirect(dest, "/admin") == "/admin"

    @pytest.mark.parametrize("dest", ["/admin", "/employees?tab=all", "/a/b#c"])
    def test_same_site_paths_kept(self, dest):
        with flask_app.test_request_context("/"):
            assert _safe_redirect(dest, "/admin") == dest


class TestStepUpMfa:
    def _set_totp(self, db_engine, username, enabled):
        from utils.helpers import encrypt_pii
        secret = pyotp.random_base32()
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET totp_secret=%s, totp_enabled=%s WHERE username=%s",
                    (encrypt_pii(secret), 1 if enabled else 0, username))
        cur.close()
        return secret

    def _login(self, client, seed_admin):
        with client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_username"] = seed_admin["username"]
            sess["admin_role"] = "admin"

    def test_unconfirmed_totp_secret_does_not_pass_step_up(self, client, seed_admin, db_engine):
        secret = self._set_totp(db_engine, seed_admin["username"], enabled=False)
        self._login(client, seed_admin)
        resp = client.post("/admin/step-up-mfa", data={"totp_code": pyotp.TOTP(secret).now()})
        assert resp.status_code == 200          # re-rendered with an error, not redirected
        with client.session_transaction() as sess:
            assert "_step_up_mfa_verified_at" not in sess

    def test_enabled_totp_passes_and_redirect_stays_on_site(self, client, seed_admin, db_engine):
        secret = self._set_totp(db_engine, seed_admin["username"], enabled=True)
        self._login(client, seed_admin)
        with client.session_transaction() as sess:
            sess["_step_up_target_url"] = "/\evil.com"
        resp = client.post("/admin/step-up-mfa", data={"totp_code": pyotp.TOTP(secret).now()})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/admin")
