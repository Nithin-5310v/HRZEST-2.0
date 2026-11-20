"""SOC console password step (utils/soc_auth.py): after the emailed code.

Why: with the emailed code alone, whoever controls a listed inbox controls the
console. The password adds something only the person knows. The first sign-in
has none yet, so the person sets it right after the code -- and that "set" must
never be able to replace a password that exists, or the inbox alone would be
enough again. These tests walk the real sign-in routes.
"""
import time

import pytest

from extensions import app as flask_app
from tests import test_soc_console as _console
from tests.test_soc_console import TEST_PASSWORD, audit_actions, enter_code, make_user, sign_in
from utils import soc_auth
from utils.soc_db import read_cursor, write_cursor

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches).
_fresh_soc_state = _console._fresh_soc_state

NEW_PASSWORD = "a different long passphrase"


@pytest.fixture(autouse=True)
def _testing_config(_fresh_soc_state, client):
    # `client` for its TESTING config (CSRF off, as for the rest of the suite):
    # without it, a test here that only makes its own test clients fails with a
    # CSRF 403 whenever random ordering runs it first.
    yield


def code_step(c, email):
    r = enter_code(c, email)
    assert r.status_code == 303 and r.headers["Location"].endswith("/socperson/login"), r.get_data(as_text=True)


def page(c):
    return c.get("/socperson/login").get_data(as_text=True)


def set_pw(c, password, confirm=None):
    return c.post("/socperson/login", data={"action": "set_password", "password": password,
                                            "confirm": password if confirm is None else confirm})


def enter_pw(c, password):
    return c.post("/socperson/login", data={"action": "password", "password": password})


def signed_in(username, role="admin"):
    email = make_user(username, role)
    c = flask_app.test_client()
    r = sign_in(c, email)
    assert r.status_code == 303 and r.headers["Location"].endswith("/socperson"), r.get_data(as_text=True)
    return c, email


def stored_hash(username):
    with read_cursor("att_master") as cur:
        cur.execute("SELECT password_hash FROM soc_users WHERE username=%s", (username,))
        return cur.fetchone()[0]


class TestFirstSignIn:
    def test_sets_a_password_after_the_code_and_only_then_opens_the_console(self, client, db_engine):
        email = make_user("alice")
        code_step(client, email)
        assert client.get("/socperson").status_code == 302                     # the code alone is not enough
        assert client.get("/socperson/api/me").status_code == 401
        html = page(client)
        assert "Set your password" in html and 'autocomplete="new-password"' in html and email in html
        r = set_pw(client, TEST_PASSWORD)
        assert r.status_code == 303 and r.headers["Location"].endswith("/socperson")
        assert client.get("/socperson").status_code == 200
        stored = stored_hash("alice")
        assert stored.startswith("$argon2id$") and TEST_PASSWORD not in stored
        assert soc_auth.get_user("alice")["has_password"] is True
        acts = audit_actions(db_engine)
        assert ("alice", "password_set", None) in acts and ("alice", "login", None) in acts

    @pytest.mark.parametrize("password, confirm, message", [
        ("short", None, "Use at least 12 characters."),
        ("x" * 129, None, "Use at most 128 characters."),
        ("alice@example.com", None, "Do not use your email address as your password."),
        ("ALICE", None, "Use at least 12 characters."),
        (TEST_PASSWORD, TEST_PASSWORD + "!", "The two passwords do not match."),
    ])
    def test_a_weak_or_mistyped_password_is_refused_and_nothing_opens(self, client, password, confirm, message):
        email = make_user("alice")
        code_step(client, email)
        r = set_pw(client, password, confirm)
        assert r.status_code == 400 and _console.alert_text(r) == message
        assert soc_auth.get_user("alice")["has_password"] is False
        assert client.get("/socperson").status_code == 302
        assert set_pw(client, TEST_PASSWORD).status_code == 303                 # still on the step: try again

    def test_the_email_address_itself_is_refused_whatever_its_case(self, client):
        email = make_user("alice")
        code_step(client, email)
        r = set_pw(client, "  Alice@Example.COM ")
        assert r.status_code == 400 and "email address" in _console.alert_text(r)


class TestLaterSignIns:
    def test_ask_for_the_password_after_the_code(self, client):
        c, email = signed_in("alice")
        c.post("/socperson/logout")
        code_step(c, email)
        html = page(c)
        assert "Enter your password" in html and 'autocomplete="current-password"' in html
        assert "Set your password" not in html
        assert c.get("/socperson").status_code == 302
        r = enter_pw(c, TEST_PASSWORD)
        assert r.status_code == 303 and r.headers["Location"].endswith("/socperson")
        assert c.get("/socperson/api/me").status_code == 200

    def test_a_wrong_password_is_refused_and_five_lock_the_account(self, db_engine):
        c, email = signed_in("bob", "analyst")
        c.post("/socperson/logout")
        code_step(c, email)
        r = enter_pw(c, "not the password")
        assert r.status_code == 401 and _console.alert_text(r) == "That password is not correct."
        assert c.get("/socperson").status_code == 302
        last = None
        for _ in range(4):
            last = enter_pw(c, "not the password")
        assert last.status_code == 429 and "Too many failed attempts" in _console.alert_text(last)
        assert soc_auth.get_user("bob")["is_locked"] is True
        assert "Send sign-in code" in page(c)                                   # back to the start
        assert enter_pw(c, TEST_PASSWORD).status_code == 401                    # the step is gone
        assert any(a == "login_failed" and "wrong password" in (d or "")
                   for _, a, d in _detail_rows(db_engine))

    def test_someone_with_only_the_inbox_cannot_replace_an_existing_password(self, client):
        c, email = signed_in("alice")
        before = stored_hash("alice")
        attacker = flask_app.test_client()
        code_step(attacker, email)                                              # they read the code
        assert "Set your password" not in page(attacker)
        r = set_pw(attacker, NEW_PASSWORD)
        assert r.status_code == 303 and r.headers["Location"].endswith("/socperson/login")
        assert attacker.get("/socperson").status_code == 302
        assert stored_hash("alice") == before
        assert enter_pw(attacker, NEW_PASSWORD).status_code == 401
        assert not soc_auth.set_first_password(soc_auth.get_user("alice")["id"], NEW_PASSWORD)
        assert stored_hash("alice") == before

    def test_the_step_cannot_be_skipped_or_left_open(self, client):
        assert enter_pw(client, TEST_PASSWORD).status_code == 401              # no code in this browser
        assert set_pw(client, TEST_PASSWORD).status_code == 401
        email = make_user("alice")
        code_step(client, email)
        with client.session_transaction() as s:
            s["soc_pw_at"] = time.time() - 3600                                # walked away from it
        r = set_pw(client, TEST_PASSWORD)
        assert r.status_code == 401 and "expired" in _console.alert_text(r)
        assert soc_auth.get_user("alice")["has_password"] is False
        assert "Send sign-in code" in page(client)
        code_step(client, email)
        soc_auth.update_user(soc_auth.get_user("alice")["id"], active=False)    # disabled mid-way
        assert set_pw(client, TEST_PASSWORD).status_code == 401
        assert soc_auth.get_user("alice")["has_password"] is False

    def test_taking_the_address_off_the_list_ends_the_step(self, client):
        email = make_user("alice")
        code_step(client, email)
        _console.disallow(email)
        assert set_pw(client, TEST_PASSWORD).status_code == 401
        assert client.get("/socperson").status_code == 302

    def test_a_password_from_before_the_upgrade_is_rehashed_on_use(self):
        from werkzeug.security import generate_password_hash as pbkdf2
        c, email = signed_in("alice")
        c.post("/socperson/logout")
        with write_cursor("att_master") as cur:
            cur.execute("UPDATE soc_users SET password_hash=%s WHERE username='alice'",
                        (pbkdf2(TEST_PASSWORD, method="pbkdf2:sha256"),))
        assert sign_in(c, email).status_code == 303
        assert stored_hash("alice").startswith("$argon2id$")


class TestResetting:
    def test_an_admin_resets_someone_elses_password_but_not_their_own(self, db_engine):
        admin, _ = signed_in("alice")
        bob_browser, bob_email = signed_in("bob", "analyst")
        users = {u["username"]: u for u in admin.get("/socperson/api/users").get_json()["users"]}
        assert users["alice"]["has_password"] is True and users["bob"]["has_password"] is True
        assert all("password_hash" not in u for u in users.values())
        bob, me = soc_auth.get_user("bob")["id"], soc_auth.get_user("alice")["id"]
        assert admin.post(f"/socperson/api/users/{bob}", json={"action": "reset_password"}).status_code == 200
        assert soc_auth.get_user("bob")["has_password"] is False
        # A leaked password may be in use right now: bob's sessions end, the admin's do not.
        assert bob_browser.get("/socperson/api/me").status_code == 401
        assert admin.get("/socperson/api/me").status_code == 200
        assert ("alice", "user_password_reset", "bob") in audit_actions(db_engine)
        r = admin.post(f"/socperson/api/users/{me}", json={"action": "reset_password"})
        assert r.status_code == 400 and soc_auth.get_user("alice")["has_password"] is True
        # Bob signs in again and, with no password left, sets a new one.
        code_step(bob_browser, bob_email)
        assert "Set your password" in page(bob_browser)
        assert set_pw(bob_browser, NEW_PASSWORD).status_code == 303
        bob_browser.post("/socperson/logout")
        assert sign_in(bob_browser, bob_email, TEST_PASSWORD).status_code == 401    # the old one is gone
        assert sign_in(bob_browser, bob_email, NEW_PASSWORD).status_code == 303

    @pytest.mark.parametrize("role", ["viewer", "analyst"])
    def test_only_admins_reset_passwords(self, role):
        signed_in("alice")
        c, _ = signed_in("bob", role)
        alice = soc_auth.get_user("alice")["id"]
        assert c.post(f"/socperson/api/users/{alice}", json={"action": "reset_password"}).status_code == 403
        assert soc_auth.get_user("alice")["has_password"] is True

    def test_the_server_script_resets_and_unlocks_a_sole_admin(self, db_engine, capsys, monkeypatch):
        import dotenv
        from scripts import soc_reset_password
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)      # never this PC's real .env
        c, email = signed_in("alice")
        uid = soc_auth.get_user("alice")["id"]
        for _ in range(soc_auth.MAX_FAILURES):
            soc_auth.register_failure(soc_auth.get_user("alice"), None, "wrong password")
        assert soc_auth.get_user("alice")["is_locked"] is True
        assert soc_reset_password.main([email.upper()]) == 0
        assert "Password reset for alice" in capsys.readouterr().out
        user = soc_auth.get_user_by_id(uid)
        assert user["has_password"] is False and user["is_locked"] is False
        assert c.get("/socperson/api/me").status_code == 401                    # sessions ended
        assert ("cli", "user_password_reset", "alice") in audit_actions(db_engine)
        assert soc_reset_password.main(["nobody@example.com"]) == 1


def _run_upgrade_statement():
    """The one migration statement under test -- not the whole ensure_soc_tables(),
    which also recreates the audit-log trigger other tests are writing through."""
    from migrations.soc_tables import STATEMENTS
    (stmt,) = [s for s in STATEMENTS if "UPDATE soc_users" in s]
    with write_cursor("att_master") as cur:
        cur.execute(stmt)


class TestUpgrade:
    def test_an_account_from_the_authenticator_login_sets_a_fresh_password(self, client, db_engine):
        """Old accounts had a password set from the command line plus an authenticator;
        the upgrade voids both so the person sets their own password after the code."""
        from utils.auth import generate_password_hash
        email = make_user("olga")
        with write_cursor("att_master") as cur:
            cur.execute("UPDATE soc_users SET password_hash=%s, totp_secret='enc:secret', totp_enabled=TRUE, "
                        "enroll_code_hash='x' WHERE username='olga'", (generate_password_hash("handed over by cli"),))
        _run_upgrade_statement()
        with read_cursor("att_master") as cur:
            cur.execute("SELECT password_hash, totp_secret, totp_enabled, enroll_code_hash FROM soc_users "
                        "WHERE username='olga'")
            pw_hash, secret, enabled, enroll = cur.fetchone()
        assert pw_hash.startswith("!") and secret is None and enabled is False and enroll is None
        code_step(client, email)
        assert "Set your password" in page(client)
        assert set_pw(client, TEST_PASSWORD).status_code == 303
        # Running the migration again (every start-up) leaves the new password alone.
        before = stored_hash("olga")
        _run_upgrade_statement()
        assert stored_hash("olga") == before


def _detail_rows(db):
    cur = db.cursor()
    cur.execute("SELECT actor, action, detail FROM att_master.soc_audit_log ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    return rows
