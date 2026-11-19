"""SOC console passkey step (utils/soc_passkeys.py): after the emailed code.

Why: with the emailed code alone, whoever controls a listed inbox controls the
console. The passkey adds the person's own device. These tests walk the real
sign-in routes; only py_webauthn's signature checks are replaced, because a
browser authenticator cannot run here -- the fake records what the server asked
the library to check (site, user verification, stored key, counter).
"""
import time
from types import SimpleNamespace

import pytest

from extensions import app as flask_app
from tests import test_soc_console as _console
from tests.test_soc_console import allow_resend, audit_actions, make_user, request_code
from utils import soc_auth, soc_passkeys, soc_queries
from utils.webauthn_utils import _wa_b64url_encode

# The console tests' autouse reset (SOC env vars, captured sign-in codes, caches).
_fresh_soc_state = _console._fresh_soc_state


@pytest.fixture(autouse=True)
def _passkeys_on(_fresh_soc_state, monkeypatch):
    monkeypatch.setenv("SOC_PASSKEY", "on")


@pytest.fixture
def authenticator(monkeypatch):
    state = {"accept": True, "registered": [], "asserted": []}

    def register(**kw):
        state["registered"].append(kw)
        if not state["accept"]:
            raise ValueError("attestation did not verify")
        return SimpleNamespace(credential_id=f"cred-{len(state['registered'])}".encode(),
                               credential_public_key=b"public-key", sign_count=0)

    def assert_(**kw):
        state["asserted"].append(kw)
        if not state["accept"]:
            raise ValueError("signature did not verify")
        return SimpleNamespace(new_sign_count=kw["credential_current_sign_count"] + 1)

    monkeypatch.setattr(soc_passkeys.webauthn, "verify_registration_response", register)
    monkeypatch.setattr(soc_passkeys.webauthn, "verify_authentication_response", assert_)
    return state


def cred_id(n=1):
    return _wa_b64url_encode(f"cred-{n}".encode())


def code_step(c, email):
    allow_resend(email)
    code = request_code(c, email)
    r = c.post("/socperson/login", data={"action": "code", "code": code})
    assert r.status_code == 303 and r.headers["Location"].endswith("/socperson/login"), r.get_data(as_text=True)


def passkey_step(c, credential_id=None):
    o = c.post("/socperson/login/passkey/options")
    body = {"credential": {"id": credential_id or cred_id(), "rawId": credential_id or cred_id(),
                           "type": "public-key", "response": {}}}
    return o, c.post("/socperson/login/passkey/verify", json=body)


def signed_in(username, role="admin", credential_id=None):
    email = make_user(username, role)
    c = flask_app.test_client()
    code_step(c, email)
    o, v = passkey_step(c, credential_id)
    assert v.status_code == 200 and v.get_json()["ok"], v.get_json()
    return c, email


class TestPasskeyStep:
    def test_first_sign_in_creates_a_passkey_and_only_then_opens_the_console(self, client, authenticator, db_engine):
        email = make_user("alice")
        code_step(client, email)
        assert client.get("/socperson").status_code == 302                     # the code alone is not enough
        assert client.get("/socperson/api/me").status_code == 401
        page = client.get("/socperson/login").get_data(as_text=True)
        assert "Set up your passkey" in page and "/socperson/assets/login.js" in page
        o, v = passkey_step(client)
        opts = o.get_json()
        assert opts["mode"] == "register" and opts["options"]["rp"]["id"] == "localhost"
        assert opts["options"]["user"]["name"] == email
        assert opts["options"]["authenticatorSelection"]["userVerification"] == "required"
        assert v.status_code == 200 and client.get("/socperson").status_code == 200
        (call,) = authenticator["registered"]
        assert call["require_user_verification"] is True and call["expected_rp_id"] == "localhost"
        uid = soc_auth.get_user("alice")["id"]
        assert [c for c, _, _ in soc_passkeys.credentials(uid)] == [cred_id()]
        assert ("alice", "passkey_enrolled", None) in audit_actions(db_engine)

    def test_later_sign_ins_ask_for_that_passkey(self, authenticator):
        c, email = signed_in("alice")
        c.post("/socperson/logout")
        code_step(c, email)
        assert "Confirm it" in c.get("/socperson/login").get_data(as_text=True)
        o, v = passkey_step(c, cred_id())
        opts = o.get_json()
        assert opts["mode"] == "verify" and [x["id"] for x in opts["options"]["allowCredentials"]] == [cred_id()]
        assert opts["options"]["userVerification"] == "required"
        assert v.status_code == 200 and c.get("/socperson").status_code == 200
        (call,) = authenticator["asserted"]
        assert call["credential_public_key"] == b"public-key" and call["credential_current_sign_count"] == 0
        assert call["require_user_verification"] is True
        assert soc_passkeys.credentials(soc_auth.get_user("alice")["id"])[0][2] == 1      # counter stored

    def test_someone_elses_passkey_or_a_bad_signature_is_refused_and_locks_out(self, authenticator):
        signed_in("alice")
        c, bob = signed_in("bob", "analyst", credential_id=cred_id(2))
        c.post("/socperson/logout")
        code_step(c, bob)
        _, v = passkey_step(c, cred_id(1))                                   # alice's passkey
        assert v.status_code == 401 and c.get("/socperson").status_code == 302
        authenticator["accept"] = False
        last = None
        for _ in range(4):
            _, last = passkey_step(c, cred_id(2))
        assert last.status_code == 429 and last.get_json()["restart"] is True
        assert soc_auth.get_user("bob")["is_locked"] is True
        assert "Send sign-in code" in c.get("/socperson/login").get_data(as_text=True)    # back to the start

    def test_the_step_cannot_be_skipped_reused_or_left_open(self, client, authenticator):
        assert client.post("/socperson/login/passkey/options").status_code == 401
        assert client.post("/socperson/login/passkey/verify", json={"credential": {"id": "x"}}).status_code == 401
        email = make_user("alice")
        code_step(client, email)
        # a verify without fresh options has no challenge to check against
        assert client.post("/socperson/login/passkey/verify", json={"credential": {"id": cred_id()}}).status_code == 400
        with client.session_transaction() as s:
            s["soc_pk_at"] = time.time() - 3600                              # walked away from it
        assert client.post("/socperson/login/passkey/options").status_code == 401
        assert "Send sign-in code" in client.get("/socperson/login").get_data(as_text=True)
        code_step(client, email)
        soc_auth.update_user(soc_auth.get_user("alice")["id"], active=False)    # disabled mid-way
        assert client.post("/socperson/login/passkey/options").status_code == 401

    def test_admin_resets_someone_elses_passkey_but_not_their_own(self, authenticator, db_engine):
        admin, _ = signed_in("alice")
        bob_browser, bob_email = signed_in("bob", "analyst", credential_id=cred_id(2))
        users = admin.get("/socperson/api/users").get_json()
        assert users["passkey_required"] is True
        assert {u["username"]: u["passkeys"] for u in users["users"]} == {"alice": 1, "bob": 1}
        bob, me = soc_auth.get_user("bob")["id"], soc_auth.get_user("alice")["id"]
        assert bob_browser.get("/socperson/api/me").status_code == 200
        assert admin.post(f"/socperson/api/users/{bob}", json={"action": "reset_passkey"}).status_code == 200
        assert soc_passkeys.credentials(bob) == []
        # The lost device's open session ends at once; the admin's own does not.
        assert bob_browser.get("/socperson/api/me").status_code == 401
        assert admin.get("/socperson/api/me").status_code == 200
        assert ("alice", "user_passkey_reset", "bob") in audit_actions(db_engine)
        assert admin.post(f"/socperson/api/users/{me}", json={"action": "reset_passkey"}).status_code == 400
        assert len(soc_passkeys.credentials(me)) == 1
        # Bob signs in again and, with no passkey left, creates a new one.
        code_step(bob_browser, bob_email)
        o, v = passkey_step(bob_browser, cred_id(3))
        assert o.get_json()["mode"] == "register" and v.status_code == 200
        assert bob_browser.get("/socperson/api/me").status_code == 200

    def test_switching_it_off_is_the_recovery_path_and_posture_says_so(self, client, monkeypatch):
        def posture():
            with flask_app.app_context():
                return {x["id"]: x["status"] for x in soc_queries.posture()["checks"]}["socpasskey"]
        assert posture() == "pass"
        monkeypatch.setenv("SOC_PASSKEY", "off")
        email = make_user("alice")
        allow_resend(email)
        r = client.post("/socperson/login", data={"action": "code", "code": request_code(client, email)})
        assert r.status_code == 303 and r.headers["Location"].endswith("/socperson")
        assert posture() == "fail"

    def test_without_passkey_support_nobody_gets_in_and_the_page_says_why(self, client, monkeypatch):
        monkeypatch.setattr(soc_passkeys, "available", lambda: False)
        code_step(client, make_user("alice"))
        assert "SOC_PASSKEY=off" in client.get("/socperson/login").get_data(as_text=True)
        assert client.post("/socperson/login/passkey/options").status_code == 503
        assert client.get("/socperson").status_code == 302
