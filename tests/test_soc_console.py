"""SOC console: authentication, isolation, roles, data endpoints and response actions.

These walk the REAL login flow (password -> setup code -> authenticator enrolment -> TOTP),
because that flow is the console's front door and the part most worth proving.
"""
import csv
import io
import json
import time

import psycopg2
import pyotp
import pytest

from extensions import app as flask_app
from utils import soc_auth, soc_queries
from utils.helpers import decrypt_pii
from utils.soc_db import write_cursor

PW = "correct-horse-battery-9"
PUBLIC_IP = "45.33.32.156"        # routable (not private / documentation / reserved)


@pytest.fixture(autouse=True)
def _fresh_soc_state(monkeypatch):
    import blueprints.soc as soc_blueprint
    monkeypatch.delenv("SOC_ALLOWED_CIDRS", raising=False)
    soc_queries.clear_cache()
    soc_blueprint._last_integrity.clear()
    yield
    soc_queries.clear_cache()
    soc_blueprint._last_integrity.clear()


# ── helpers ──────────────────────────────────────────────────────────
def make_user(username="alice", role="admin", password=PW):
    ok, msg, code = soc_auth.create_user(username, password, role, f"{username}@example.com", "test")
    assert ok, msg
    return code


def free_totp(username):
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET totp_last_step=0 WHERE username=%s", (username,))


def enroll_and_login(client, username, setup_code, password=PW):
    r = client.post("/soc/login", data={"action": "password", "username": username, "password": password})
    assert r.status_code == 303, r.get_data(as_text=True)
    r = client.post("/soc/login", data={"action": "setup", "setup_code": setup_code})
    assert r.status_code == 303
    assert b"Set up your authenticator" in client.get("/soc/login").data      # renders QR, creates the secret
    secret = decrypt_pii(soc_auth.get_user(username)["totp_secret"])
    r = client.post("/soc/login", data={"action": "code", "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 303 and r.headers["Location"].endswith("/soc")
    return secret


def login_enrolled(client, username, secret, password=PW):
    free_totp(username)
    r = client.post("/soc/login", data={"action": "password", "username": username, "password": password})
    assert r.status_code == 303
    r = client.post("/soc/login", data={"action": "code", "code": pyotp.TOTP(secret).now()})
    return r


@pytest.fixture
def soc(client):
    """soc(username, role) -> (client, totp_secret): a fully signed-in console session.
    Depends on `client` for its TESTING config (CSRF off, as for the rest of the suite);
    without it a class run on its own would see CSRF rejections the full run hides."""
    def make(username="alice", role="admin"):
        code = make_user(username, role)
        c = flask_app.test_client()
        return c, enroll_and_login(c, username, code)
    return make


def add_event(db, event_type, level="WARNING", ip=None, identifier=None, path=None, method="GET",
              message="test event", extra=None, minutes_ago=5):
    cur = db.cursor()
    cur.execute(
        "INSERT INTO public.security_events (event_type, level, message, identifier, ip, path, method, extra_json, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW() - (%s * INTERVAL '1 minute')) RETURNING id",
        (event_type, level, message, identifier, ip, path, method, json.dumps(extra) if extra else None, minutes_ago))
    eid = cur.fetchone()[0]
    cur.close()
    return eid


def audit_actions(db):
    cur = db.cursor()
    cur.execute("SELECT actor, action, target FROM att_master.soc_audit_log ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    return rows


# ════════════════════════════════════════════════════════════════════
# front door
# ════════════════════════════════════════════════════════════════════
class TestAnonymous:
    def test_console_and_api_require_a_session(self, client):
        r = client.get("/soc")
        assert r.status_code == 302 and r.headers["Location"].endswith("/soc/login")
        for path in ("/soc/api/me", "/soc/api/overview", "/soc/api/events", "/soc/api/users", "/soc/api/pulse"):
            r = client.get(path)
            assert r.status_code == 401 and r.get_json()["ok"] is False, path
        assert client.post("/soc/api/bans", json={"ip": PUBLIC_IP, "reason": "x"}).status_code == 401

    def test_only_the_login_stylesheet_is_public(self, client):
        assert client.get("/soc/assets/login.css").status_code == 200
        for name in ("soc.css", "soc-core.js", "soc-views.js", "soc-analysis.js"):
            assert client.get("/soc/assets/" + name).status_code == 404, name
        assert client.get("/soc/assets/../app.py").status_code in (404, 308)
        assert client.get("/soc/assets/nope.js").status_code == 404

    def test_login_page_is_marked_no_store_and_noindex(self, client):
        r = client.get("/soc/login")
        assert r.status_code == 200
        assert "no-store" in r.headers["Cache-Control"]
        assert "noindex" in r.headers["X-Robots-Tag"]
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert b"noindex" in r.data

    def test_authenticated_assets_are_served_to_a_session(self, soc):
        c, _ = soc()
        for name in ("soc.css", "soc-core.js", "soc-views.js", "soc-analysis.js"):
            r = c.get("/soc/assets/" + name)
            assert r.status_code == 200, name
        assert "javascript" in c.get("/soc/assets/soc-core.js").content_type


class TestLoginFlow:
    def test_first_login_needs_the_setup_code_then_enrols_totp(self, client, db_engine):
        code = make_user("alice", "admin")
        assert client.post("/soc/login", data={"action": "password", "username": "alice", "password": PW}).status_code == 303
        assert b"Setup code" in client.get("/soc/login").data
        # a TOTP code without the setup code goes nowhere
        r = client.post("/soc/login", data={"action": "code", "code": "123456"})
        assert r.status_code == 303 and client.get("/soc").status_code == 302
        # wrong setup code
        r = client.post("/soc/login", data={"action": "setup", "setup_code": "AAAA-BBBB-CCCC"})
        assert r.status_code == 401 and b"not valid" in r.data
        # right one (case / dashes forgiven)
        assert client.post("/soc/login", data={"action": "setup", "setup_code": code.lower().replace("-", " ")}).status_code == 303
        page = client.get("/soc/login").get_data(as_text=True)
        assert "Set up your authenticator" in page and "data:image/png;base64," in page
        secret = decrypt_pii(soc_auth.get_user("alice")["totp_secret"])
        assert secret in page
        r = client.post("/soc/login", data={"action": "code", "code": pyotp.TOTP(secret).now()})
        assert r.status_code == 303 and client.get("/soc").status_code == 200
        u = soc_auth.get_user("alice")
        assert u["totp_enabled"] is True and u["enroll_code_hash"] is None      # setup code is single-use
        assert ("alice", "login", None) in audit_actions(db_engine)

    def test_enrolled_user_signs_in_with_password_and_code(self, soc):
        c, secret = soc("alice", "admin")
        c.post("/soc/logout")
        assert c.get("/soc").status_code == 302
        assert login_enrolled(c, "alice", secret).status_code == 303
        assert c.get("/soc").status_code == 200

    def test_a_used_totp_code_cannot_be_replayed(self, soc):
        c, secret = soc("alice", "admin")
        c.post("/soc/logout")
        used = soc_auth.get_user("alice")["totp_last_step"]
        assert used > 0
        totp = pyotp.TOTP(secret)
        # enrolment consumed exactly this 30 s step; presenting its code again must be refused
        c.post("/soc/login", data={"action": "password", "username": "alice", "password": PW})
        r = c.post("/soc/login", data={"action": "code", "code": totp.at(used * 30)})
        assert r.status_code == 401 and c.get("/soc").status_code == 302
        # the next step's code is accepted
        r = c.post("/soc/login", data={"action": "code", "code": totp.at((used + 1) * 30)})
        assert r.status_code == 303 and c.get("/soc").status_code == 200

    def test_failures_are_indistinguishable(self, client):
        make_user("alice", "admin")
        make_user("dora", "viewer")
        soc_auth.update_user(soc_auth.get_user("dora")["id"], active=False)
        msgs = set()
        for data in ({"username": "alice", "password": "wrong-password-1"}, {"username": "nobody", "password": "wrong-password-1"},
                     {"username": "dora", "password": PW}):
            r = client.post("/soc/login", data={"action": "password", **data})
            assert r.status_code == 401
            msgs.add(r.get_data(as_text=True).split('role="alert">')[1].split("<")[0])
        assert msgs == {"Invalid username or password."}

    def test_lockout_after_repeated_failures_even_for_the_right_password(self, client, db_engine):
        make_user("alice", "admin")
        for _ in range(5):
            client.post("/soc/login", data={"action": "password", "username": "alice", "password": "wrong-password-1"})
        r = client.post("/soc/login", data={"action": "password", "username": "alice", "password": PW})
        assert r.status_code == 429 and b"Too many failed attempts" in r.data
        assert soc_auth.get_user("alice")["is_locked"] is True
        acts = [a for _, a, _ in audit_actions(db_engine)]
        assert "account_locked" in acts and "login_blocked_locked" in acts
        soc_auth.unlock(soc_auth.get_user("alice")["id"])
        assert client.post("/soc/login", data={"action": "password", "username": "alice", "password": PW}).status_code == 303

    def test_wrong_totp_codes_count_toward_lockout(self, soc):
        c, secret = soc("alice", "admin")
        c.post("/soc/logout")
        c.post("/soc/login", data={"action": "password", "username": "alice", "password": PW})
        last = None
        for _ in range(5):
            last = c.post("/soc/login", data={"action": "code", "code": "000000"})
        assert last.status_code == 429
        assert soc_auth.get_user("alice")["is_locked"] is True

    def test_restart_discards_a_half_finished_login(self, client):
        make_user("alice", "admin")
        client.post("/soc/login", data={"action": "password", "username": "alice", "password": PW})
        assert b"Setup code" in client.get("/soc/login").data
        client.post("/soc/login", data={"action": "restart"})
        assert b"Username" in client.get("/soc/login").data

    def test_password_policy(self):
        for bad in ("short1", "alice-alice-alice", "aaaaaaaaaaaaaaaa"):
            ok, _, code = soc_auth.create_user("alice", bad, "viewer")
            assert not ok and code is None, bad
        ok, msg, _ = soc_auth.create_user("Bad Name!", PW, "viewer")
        assert not ok and "Username" in msg
        ok, msg, _ = soc_auth.create_user("alice", PW, "superuser")
        assert not ok and msg == "Unknown role."

    def test_secrets_are_not_stored_in_the_clear(self, soc, db_engine):
        make_user("zed", "viewer")
        cur = db_engine.cursor()
        cur.execute("SELECT password_hash, enroll_code_hash FROM att_master.soc_users WHERE username='zed'")
        pw_hash, code_hash = cur.fetchone()
        assert PW not in pw_hash and pw_hash != PW and code_hash
        c, secret = soc("alice", "admin")
        cur.execute("SELECT totp_secret FROM att_master.soc_users WHERE username='alice'")
        assert secret not in cur.fetchone()[0]          # Fernet ciphertext, not the base32 secret
        cur.close()


# ════════════════════════════════════════════════════════════════════
# session lifetime and invalidation
# ════════════════════════════════════════════════════════════════════
class TestSessionLifecycle:
    def test_idle_timeout(self, soc):
        c, _ = soc()
        assert c.get("/soc/api/me").status_code == 200
        with c.session_transaction() as s:
            s["soc_last"] -= 3600
        assert c.get("/soc/api/me").status_code == 401

    def test_absolute_timeout(self, soc):
        c, _ = soc()
        with c.session_transaction() as s:
            s["soc_at"] -= 24 * 3600
            s["soc_last"] = time.time()
        assert c.get("/soc/api/me").status_code == 401

    def test_background_polling_does_not_extend_the_session(self, soc):
        c, _ = soc()
        with c.session_transaction() as s:
            s["soc_last"] = time.time() - 120
        before = time.time() - 120
        assert c.get("/soc/api/pulse", headers={"X-SOC-Poll": "1"}).status_code == 200
        with c.session_transaction() as s:
            assert abs(s["soc_last"] - before) < 5            # untouched
        assert c.get("/soc/api/pulse").status_code == 200
        with c.session_transaction() as s:
            assert s["soc_last"] > before + 100               # a real action refreshes it

    def test_deactivating_an_account_ends_its_session_at_once(self, soc):
        admin, _ = soc("alice", "admin")
        victim, _ = soc("bob", "analyst")
        assert victim.get("/soc/api/me").status_code == 200
        uid = soc_auth.get_user("bob")["id"]
        assert admin.post(f"/soc/api/users/{uid}", json={"action": "deactivate"}).status_code == 200
        assert victim.get("/soc/api/me").status_code == 401

    def test_changing_a_role_ends_the_session(self, soc):
        admin, _ = soc("alice", "admin")
        other, _ = soc("bob", "analyst")
        assert admin.post(f"/soc/api/users/{soc_auth.get_user('bob')['id']}", json={"action": "set_role", "role": "viewer"}).status_code == 200
        assert other.get("/soc/api/me").status_code == 401

    def test_a_password_reset_ends_other_sessions(self, soc):
        admin, _ = soc("alice", "admin")
        other, _ = soc("bob", "analyst")
        uid = soc_auth.get_user("bob")["id"]
        assert admin.post(f"/soc/api/users/{uid}", json={"action": "reset_password", "password": "another-long-password-7"}).status_code == 200
        assert other.get("/soc/api/me").status_code == 401

    def test_changing_own_password_requires_the_current_one_and_signs_out(self, soc):
        c, secret = soc("alice", "admin")
        assert c.post("/soc/api/me/password", json={"current": "nope", "new": "brand-new-password-3"}).status_code == 403
        assert c.post("/soc/api/me/password", json={"current": PW, "new": "short"}).status_code == 400
        assert c.post("/soc/api/me/password", json={"current": PW, "new": "brand-new-password-3"}).status_code == 200
        assert c.get("/soc/api/me").status_code == 401
        assert login_enrolled(c, "alice", secret, "brand-new-password-3").status_code == 303

    def test_logout_ends_the_session(self, soc, db_engine):
        c, _ = soc()
        c.post("/soc/logout")
        assert c.get("/soc/api/me").status_code == 401
        assert "logout" in [a for _, a, _ in audit_actions(db_engine)]


# ════════════════════════════════════════════════════════════════════
# isolation from every other surface
# ════════════════════════════════════════════════════════════════════
class TestIsolation:
    @pytest.mark.parametrize("keys", [
        {"admin_logged_in": True, "admin_username": "x", "tenant_db": "att_test"},
        {"employee_id": "E1"},
        {"platform_admin_logged_in": True, "platform_admin_username": "root", "platform_admin_last_activity": time.time()},
    ])
    def test_no_other_login_opens_the_console(self, client, keys):
        with client.session_transaction() as s:
            s.update(keys)
        # other surfaces' own hooks may answer with a redirect or a 401; what matters is no console data
        assert client.get("/soc").status_code == 302
        for path in ("/soc/api/overview", "/soc/api/users", "/soc/api/events"):
            r = client.get(path)
            assert r.status_code in (302, 401), path
            assert b'"kpi"' not in r.data and b'"users"' not in r.data and b'"events"' not in r.data, path

    def test_a_console_session_opens_nothing_else(self, soc):
        c, _ = soc("alice", "admin")
        assert c.get("/admin").status_code in (302, 401)
        r = c.get("/super_admin")
        assert r.status_code == 302 and "/super_admin/login" in r.headers["Location"]
        assert c.get("/employee_portal").status_code in (302, 401)
        assert c.get("/api/employees").status_code == 401

    def test_soc_is_never_a_company_slug(self):
        from blueprints.org import _validate_new_tenant_fields
        err = _validate_new_tenant_fields("Soc Inc", "soc", "admin1", "password123", "a@b.co", "b.co")
        assert err and "reserved" in err.lower()

    def test_console_is_only_reachable_at_the_canonical_path(self, soc):
        """/<company>/soc would otherwise be stripped to /soc by the tenant middleware."""
        c, _ = soc()
        for path in ("/att-test-suite/soc", "/att-test-suite/soc/login", "/att-test-suite/soc/api/me",
                     "/att-test-suite/soc/assets/login.css"):
            assert c.get(path).status_code == 404, path
        assert c.post("/att-test-suite/soc/login", data={"action": "password", "username": "alice", "password": PW}).status_code == 404
        assert c.get("/soc").status_code == 200 and c.get("/soc/api/me").status_code == 200

    def test_response_headers(self, soc):
        c, _ = soc()
        for path in ("/soc", "/soc/api/me", "/soc/assets/soc.css"):
            r = c.get(path)
            assert "no-store" in r.headers["Cache-Control"], path
            assert "noindex" in r.headers["X-Robots-Tag"], path
        csp = c.get("/soc").headers["Content-Security-Policy"]
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]

    def test_console_is_not_advertised(self, client):
        import re
        link = re.compile(rb"""["'(=\s]/soc(?:["'/?#)\s<]|$)""")
        for path in ("/robots.txt", "/sitemap.xml", "/", "/login", "/create_org"):
            assert not link.search(client.get(path).data), path


class TestNetworkAllowList:
    def test_outside_addresses_get_a_404_everywhere(self, client, monkeypatch):
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "10.0.0.0/8")
        for path in ("/soc", "/soc/login", "/soc/api/me", "/soc/assets/login.css"):
            assert client.get(path).status_code == 404, path
        assert client.post("/soc/login", data={"action": "password", "username": "a", "password": "b"}).status_code == 404
        assert client.post("/soc/logout").status_code == 404

    def test_inside_addresses_work_and_a_malformed_list_denies_everyone(self, client, monkeypatch):
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "127.0.0.0/8, 10.0.0.0/8")
        assert client.get("/soc/login").status_code == 200
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "not-a-network")
        assert client.get("/soc/login").status_code == 404            # fails closed

    def test_a_valid_session_is_still_refused_from_outside(self, soc, monkeypatch):
        c, _ = soc()
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "10.0.0.0/8")
        assert c.get("/soc").status_code == 404 and c.get("/soc/api/me").status_code == 404


class TestWafHandling:
    SQLI = "1' UNION SELECT username,password FROM users--"

    def test_the_login_page_is_still_inspected(self, client):
        assert client.get("/soc/login", query_string={"x": self.SQLI}).status_code == 403

    def test_analysts_can_search_for_attack_strings(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, path="/x?id=" + self.SQLI)
        r = c.get("/soc/api/events", query_string={"q": "UNION SELECT"})
        assert r.status_code == 200 and len(r.get_json()["events"]) == 1

    def test_unauthenticated_callers_get_no_exemption(self, client):
        assert client.get("/soc/api/events", query_string={"q": self.SQLI}).status_code == 403

    def test_log_upload_with_attack_content_is_not_blocked_and_is_analysed(self, soc, db_engine):
        c, _ = soc()
        log = "\n".join([
            f'{PUBLIC_IP} - - [16/Oct/2026:10:00:0{i} +0000] "GET /?id=1%27%20UNION%20SELECT%20password%20FROM%20users-- HTTP/1.1" 200 5 "-" "sqlmap/1.7"'
            for i in range(5)] + ["GET /?q=<script>alert(1)</script>"])
        r = c.post("/soc/api/loganalysis", data={"log": (io.BytesIO(log.encode()), "attack.log")}, content_type="multipart/form-data")
        assert r.status_code == 200, r.get_data(as_text=True)
        report = r.get_json()["report"]
        found = {f["id"] for f in report["findings"]}
        assert {"sqli", "scanner_ua"} <= found and report["filename"] == "attack.log"
        assert ("alice", "log_analysis", "attack.log") in audit_actions(db_engine)


# ════════════════════════════════════════════════════════════════════
# roles
# ════════════════════════════════════════════════════════════════════
class TestRoles:
    def test_viewer_reads_but_cannot_act(self, soc, db_engine):
        c, _ = soc("vic", "viewer")
        for path in ("/soc/api/overview", "/soc/api/events", "/soc/api/bans", "/soc/api/cases", "/soc/api/posture", "/soc/api/detections"):
            assert c.get(path).status_code == 200, path
        for method, path, body in [("post", "/soc/api/bans", {"ip": PUBLIC_IP, "reason": "x y z"}), ("post", "/soc/api/bans/remove", {"ip": PUBLIC_IP}),
                                   ("post", "/soc/api/cases", {"title": "abc"}), ("post", "/soc/api/integrity/verify", {}),
                                   ("post", "/soc/api/sessions/revoke", {"tenant_id": 1, "sid": "x"}), ("get", "/soc/api/events.csv", None),
                                   ("post", "/soc/api/loki/query", {"query": "{a=\"b\"}"})]:
            r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
            assert r.status_code == 403, path
        assert c.post("/soc/api/loganalysis", data={"log": (io.BytesIO(b"x"), "a.log")}, content_type="multipart/form-data").status_code == 403
        assert ("vic", "forbidden", "/soc/api/bans") in audit_actions(db_engine)

    def test_analyst_acts_but_cannot_administer(self, soc):
        c, _ = soc("ann", "analyst")
        assert c.post("/soc/api/cases", json={"title": "Looks odd"}).status_code == 200
        for path in ("/soc/api/users", "/soc/api/audit"):
            assert c.get(path).status_code == 403, path
        assert c.post("/soc/api/users", json={"username": "x1y", "password": PW}).status_code == 403

    def test_viewers_do_not_get_session_ids(self, soc, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO public.session_risk (sid, identifier, attempt_type, score, status) VALUES (%s,%s,'admin',9,'active')",
                    ("a" * 32, "victim@example.com"))
        v, _ = soc("vic", "viewer")
        a, _ = soc("ann", "analyst")
        sv = v.get("/soc/api/sessions").get_json()["sessions"]
        sa = a.get("/soc/api/sessions").get_json()["sessions"]
        assert sv and sa and all("sid" not in s for s in sv) and all("sid" in s for s in sa)
        cur.close()

    def test_user_administration(self, soc, client):
        admin, _ = soc("alice", "admin")
        r = admin.post("/soc/api/users", json={"username": "newbie", "password": PW, "role": "analyst"})
        assert r.status_code == 200 and r.get_json()["setup_code"]
        assert admin.post("/soc/api/users", json={"username": "newbie", "password": PW}).status_code == 400          # duplicate
        assert admin.post("/soc/api/users", json={"username": "weak", "password": "short"}).status_code == 400
        # the new user can actually sign in with what they were given
        nb = flask_app.test_client()
        enroll_and_login(nb, "newbie", r.get_json()["setup_code"])
        assert nb.get("/soc/api/me").get_json()["user"]["role"] == "analyst"
        users = admin.get("/soc/api/users").get_json()["users"]
        assert {u["username"] for u in users} == {"alice", "newbie"}
        assert all("password_hash" not in u and "totp_secret" not in u and "enroll_code_hash" not in u for u in users)

    def test_admins_cannot_lock_themselves_out(self, soc):
        admin, _ = soc("alice", "admin")
        me = soc_auth.get_user("alice")["id"]
        assert admin.post(f"/soc/api/users/{me}", json={"action": "deactivate"}).status_code == 400
        assert admin.post(f"/soc/api/users/{me}", json={"action": "set_role", "role": "viewer"}).status_code == 400
        assert admin.get("/soc/api/me").status_code == 200

    def test_reset_mfa_issues_a_new_setup_code_and_forces_reenrolment(self, soc):
        admin, _ = soc("alice", "admin")
        other, secret = soc("bob", "analyst")
        r = admin.post(f"/soc/api/users/{soc_auth.get_user('bob')['id']}", json={"action": "reset_mfa"})
        assert r.status_code == 200 and r.get_json()["setup_code"]
        u = soc_auth.get_user("bob")
        assert u["totp_enabled"] is False and u["totp_secret"] is None and u["enroll_code_hash"]
        other.post("/soc/logout")
        other.post("/soc/login", data={"action": "password", "username": "bob", "password": PW})
        assert b"Setup code" in other.get("/soc/login").data

    def test_audit_trail_is_append_only(self, soc, db_engine):
        soc("alice", "admin")
        cur = db_engine.cursor()
        for sql in ("UPDATE att_master.soc_audit_log SET actor='x'", "DELETE FROM att_master.soc_audit_log"):
            with pytest.raises(psycopg2.Error):
                cur.execute(sql)
        cur.execute("SELECT COUNT(*) FROM att_master.soc_audit_log")
        assert cur.fetchone()[0] > 0
        cur.close()


# ════════════════════════════════════════════════════════════════════
# data
# ════════════════════════════════════════════════════════════════════
class TestOverviewAndEvents:
    def seed(self, db):
        ids = {}
        ids["sqli"] = add_event(db, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, path="/att-test-suite/search?q=1", message="blocked sqli")
        ids["stuff"] = add_event(db, "detection.credential_stuffing", "ERROR", identifier=PUBLIC_IP, message="stuffing")
        ids["crit"] = add_event(db, "audit.chain_violation", "CRITICAL", message="chain broke")
        for i in range(4):
            add_event(db, "auth.failure", "WARNING", ip=PUBLIC_IP, identifier=f"user{i}@example.com", path="/login", method="POST")
        add_event(db, "secops.honeypot_triggered", "CRITICAL", ip="185.199.108.1", path="/wp-admin")
        add_event(db, "auth.admin_login_success", "INFO", identifier="boss@example.com", ip="52.1.1.1", path="/login")
        return ids

    def test_overview(self, soc, db_engine):
        c, _ = soc()
        self.seed(db_engine)
        d = c.get("/soc/api/overview", query_string={"hours": 24}).get_json()
        k = d["kpi"]
        assert k["critical"] == 2 and k["error"] == 2 and k["detections"] == 1 and k["failed_logins"] == 4
        assert k["waf_blocks"] == 1 and k["honeypot_hits"] == 1
        assert d["threat"]["level"] == "critical" and any("CRITICAL" in r for r in d["threat"]["reasons"])
        fam = {f["family"]: f["count"] for f in d["families"]}
        assert fam["auth"] >= 5 and fam["waf"] == 1 and fam["detection"] == 1 and fam["honeypot"] == 1 and fam["audit"] == 1
        top = d["top_ips"][0]
        assert top["ip"] == PUBLIC_IP and top["events"] == 5 and top["banned"] is False
        assert {"T1190", "T1110", "T1595", "T1070"} <= {t["id"] for t in d["techniques"]}
        assert d["timeline"] and {"t", "CRITICAL", "ERROR", "WARNING", "INFO"} <= set(d["timeline"][0])
        assert {a["event_type"] for a in d["recent_alerts"]} >= {"waf.sqli_blocked", "audit.chain_violation"}

    def test_overview_window_and_clamping(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, minutes_ago=60 * 5)
        assert c.get("/soc/api/overview", query_string={"hours": 1}).get_json()["kpi"]["waf_blocks"] == 0
        assert c.get("/soc/api/overview", query_string={"hours": 24}).get_json()["kpi"]["waf_blocks"] == 1
        assert c.get("/soc/api/overview", query_string={"hours": "abc"}).get_json()["hours"] == 24
        assert c.get("/soc/api/overview", query_string={"hours": 99999}).get_json()["hours"] == 720

    def test_quiet_system_is_normal(self, soc):
        c, _ = soc()
        assert c.get("/soc/api/pulse").get_json()["threat"]["level"] == "normal"

    def test_event_filters(self, soc, db_engine):
        c, _ = soc()
        self.seed(db_engine)
        get = lambda **q: c.get("/soc/api/events", query_string=q).get_json()["events"]   # noqa: E731
        assert {e["event_type"] for e in get(level="CRITICAL")} == {"audit.chain_violation", "secops.honeypot_triggered"}
        assert [e["event_type"] for e in get(family="detection")] == ["detection.credential_stuffing"]
        assert len(get(ip=PUBLIC_IP)) == 5
        assert len(get(identifier="user2@example.com")) == 1
        assert len(get(event_type="auth.failure")) == 4
        assert [e["event_type"] for e in get(tenant="att-test-suite")] == ["waf.sqli_blocked"]
        assert all(e["alert"] for e in get(alerts="1")) and len(get(alerts="1")) >= 4
        assert [e["event_type"] for e in get(q="wp-admin")] == ["secops.honeypot_triggered"]
        # an exact IP matches the ip column (5 events) and the detection event whose identifier is that IP
        assert len(get(q=PUBLIC_IP)) == 6
        hit = get(event_type="waf.sqli_blocked")[0]
        assert hit["family"] == "waf" and hit["mitre"] == ["T1190"] and hit["severity"] == "high" and hit["tenant"] == "att-test-suite"

    def test_search_text_is_matched_literally(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "validation.failure", message="discount 100% off")
        add_event(db_engine, "validation.failure", message="discount 1000 off")
        add_event(db_engine, "validation.failure", message="snake_case here")
        add_event(db_engine, "validation.failure", message="snakeXcase here")
        get = lambda q: c.get("/soc/api/events", query_string={"q": q}).get_json()["events"]   # noqa: E731
        assert [e["message"] for e in get("100%")] == ["discount 100% off"]
        assert [e["message"] for e in get("snake_case")] == ["snake_case here"]
        assert get("' OR '1'='1") == [] and get("\\") == []
        assert c.get("/soc/api/events", query_string={"q": "x" * 5000}).status_code == 200

    def test_pagination_walks_every_event_once(self, soc, db_engine):
        c, _ = soc()
        made = {add_event(db_engine, "validation.failure", message=f"m{i}") for i in range(7)}
        seen, cursor = [], None
        for _ in range(10):
            d = c.get("/soc/api/events", query_string={"event_type": "validation.failure", "limit": 3, "cursor": cursor or ""}).get_json()
            seen += [e["id"] for e in d["events"]]
            cursor = d["next_cursor"]
            if not cursor:
                break
        assert set(seen) == made and len(seen) == len(made)
        assert seen == sorted(seen, reverse=True)

    def test_event_detail_pivots(self, soc, db_engine):
        c, _ = soc()
        first = add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, identifier="eve@example.com", extra={"matched": "union select"})
        add_event(db_engine, "auth.failure", "WARNING", ip=PUBLIC_IP, minutes_ago=3)
        add_event(db_engine, "auth.lockout", "ERROR", identifier="eve@example.com", minutes_ago=2)
        d = c.get(f"/soc/api/events/{first}").get_json()["event"]
        assert d["extra"] == {"matched": "union select"} and d["mitre_detail"][0]["id"] == "T1190"
        assert {e["event_type"] for e in d["related"]["same_ip"]} == {"auth.failure"}
        assert {e["event_type"] for e in d["related"]["same_identifier"]} == {"auth.lockout"}
        assert d["chain"] and d["chain"]["row_hash"], "events carry their audit-chain hash"
        assert c.get("/soc/api/events/99999999").status_code == 404

    def test_csv_export_neutralises_formulas(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="=HYPERLINK(\"http://evil\",\"x\")", path="/@evil", message="+cmd|' /C calc'!A0")
        r = c.get("/soc/api/events.csv", query_string={"event_type": "auth.failure"})
        assert r.status_code == 200 and r.content_type.startswith("text/csv") and "attachment" in r.headers["Content-Disposition"]
        rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
        assert len(rows) == 1
        assert rows[0]["identifier"].startswith("'=") and rows[0]["message"].startswith("'+") and rows[0]["ip"] == PUBLIC_IP
        assert ("ann", "export_events", None) in audit_actions(db_engine)


class TestEntitiesAndDetections:
    def test_ip_profile(self, soc, db_engine):
        c, _ = soc()
        for i in range(3):
            add_event(db_engine, "auth.failure", "WARNING", ip=PUBLIC_IP, identifier=f"u{i}", path="/login", method="POST")
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, path="/x")
        e = c.get(f"/soc/api/entity/ip/{PUBLIC_IP}").get_json()["entity"]
        assert e["events"] == 4 and e["high_severity"] == 1 and e["event_types"] == 2
        assert {o["value"] for o in e["other"]} == {"u0", "u1", "u2"}
        assert {f["family"] for f in e["families"]} == {"auth", "waf"} and e["ban"] is None
        assert {t["id"] for t in e["techniques"]} == {"T1110", "T1190"}
        assert c.get("/soc/api/entity/ip/not-an-ip").status_code == 400
        assert c.get("/soc/api/entity/ip/1.2.3.4").get_json()["entity"]["events"] == 0

    def test_account_profile(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "auth.failure", ip="1.2.3.4", identifier="eve@example.com")
        add_event(db_engine, "auth.lockout", "ERROR", ip="5.6.7.8", identifier="eve@example.com")
        e = c.get("/soc/api/entity/identifier", query_string={"value": "eve@example.com"}).get_json()["entity"]
        assert e["events"] == 2 and {o["value"] for o in e["other"]} == {"1.2.3.4", "5.6.7.8"}
        assert c.get("/soc/api/entity/identifier").status_code == 400

    def test_detection_catalogue_covers_every_real_rule(self, soc, db_engine):
        from utils.detections import RULES
        c, _ = soc()
        add_event(db_engine, "detection.photo_replay", "ERROR", identifier="dev1", message="replay")
        d = c.get("/soc/api/detections", query_string={"hours": 24}).get_json()
        assert {r["name"] for r in d["rules"]} == {r.name for r in RULES}
        assert all(r["label"] and r["meaning"] for r in d["rules"])
        assert [e["label"] for e in d["events"]] == ["Photo replay"]


class TestResponseActions:
    def test_ban_lifecycle_is_audited_and_enforced(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        assert c.post("/soc/api/bans", json={"ip": PUBLIC_IP, "reason": "scanning us", "minutes": 60}).status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT reason, banned_by, expires_at IS NOT NULL FROM public.banned_ips WHERE ip=%s", (PUBLIC_IP,))
        assert cur.fetchone() == ("SOC: scanning us", "soc:ann", True)
        bans = c.get("/soc/api/bans").get_json()["bans"]
        assert bans[0]["ip"] == PUBLIC_IP and bans[0]["active"] is True
        # the banned address is refused by the app itself
        blocked = flask_app.test_client().get("/healthz2", environ_overrides={"REMOTE_ADDR": PUBLIC_IP})
        assert blocked.status_code == 403
        assert c.post("/soc/api/bans/remove", json={"ip": PUBLIC_IP}).status_code == 200
        assert flask_app.test_client().get("/healthz2", environ_overrides={"REMOTE_ADDR": PUBLIC_IP}).status_code != 403
        assert c.post("/soc/api/bans/remove", json={"ip": PUBLIC_IP}).status_code == 400
        acts = [(a, t) for _, a, t in audit_actions(db_engine)]
        assert ("ban_ip", PUBLIC_IP) in acts and ("unban_ip", PUBLIC_IP) in acts
        cur.close()

    def test_ban_guards(self, soc):
        c, _ = soc("ann", "analyst")
        elsewhere = {"REMOTE_ADDR": "8.8.4.4"}      # so the self-ban guard does not answer first
        for ip in ("10.0.0.5", "127.0.0.1", "192.168.1.1", "169.254.169.254"):
            r = c.post("/soc/api/bans", json={"ip": ip, "reason": "test ban"}, environ_overrides=elsewhere)
            assert r.status_code == 400 and "private" in r.get_json()["msg"], ip
        assert c.post("/soc/api/bans", json={"ip": "not-an-ip", "reason": "test ban"}).status_code == 400
        assert c.post("/soc/api/bans", json={"ip": PUBLIC_IP, "reason": "x"}).status_code == 400            # reason too short
        r = c.post("/soc/api/bans", json={"ip": "9.9.9.9", "reason": "test ban"}, environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        assert r.status_code == 400 and "connected from" in r.get_json()["msg"]

    def test_a_ban_is_never_shortened_by_a_later_one(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        c.post("/soc/api/bans", json={"ip": PUBLIC_IP, "reason": "long one", "minutes": 0})
        c.post("/soc/api/bans", json={"ip": PUBLIC_IP, "reason": "short one", "minutes": 5})
        cur = db_engine.cursor()
        cur.execute("SELECT expires_at FROM public.banned_ips WHERE ip=%s", (PUBLIC_IP,))
        assert cur.fetchone()[0] is None
        cur.close()

    def test_bulk_ban_reports_each_address(self, soc):
        c, _ = soc("ann", "analyst")
        r = c.post("/soc/api/bans/bulk", json={"ips": [PUBLIC_IP, "10.1.1.1", "nonsense", PUBLIC_IP], "reason": "log analysis", "minutes": 60})
        res = {x["ip"]: x["ok"] for x in r.get_json()["results"]}
        assert res == {PUBLIC_IP: True, "10.1.1.1": False, "nonsense": False}
        assert c.post("/soc/api/bans/bulk", json={"ips": [], "reason": "x"}).status_code == 400
        many = c.post("/soc/api/bans/bulk", json={"ips": [f"44.1.1.{i}" for i in range(1, 120)], "reason": "flood test", "minutes": 5}).get_json()
        assert len(many["results"]) == 50                               # capped

    def test_revoking_a_risky_session(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO public.session_risk (sid, identifier, attempt_type, score, status) "
                    "VALUES (%s,'victim@example.com','admin',8,'active')", ("b" * 32,))
        s = c.get("/soc/api/sessions").get_json()["sessions"][0]
        assert s["identifier"] == "victim@example.com" and s["sid"] == "b" * 32
        assert c.post("/soc/api/sessions/revoke", json={"tenant_id": s["tenant_id"], "sid": s["sid"]}).status_code == 200
        cur.execute("SELECT status, last_reason FROM public.session_risk WHERE sid=%s", ("b" * 32,))
        status, reason = cur.fetchone()
        assert status == "compromised" and "ann" in reason
        from utils.session_risk import is_session_compromised
        with flask_app.app_context():
            assert is_session_compromised("b" * 32) is True
        assert c.post("/soc/api/sessions/revoke", json={"tenant_id": s["tenant_id"], "sid": "missing"}).status_code == 400
        assert c.post("/soc/api/sessions/revoke", json={"tenant_id": 999999, "sid": "b" * 32}).status_code == 400
        cur.close()


class TestCases:
    def test_case_workflow(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        eid = add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, message="blocked")
        r = c.post("/soc/api/cases", json={"title": "SQLi from scanner", "severity": "high", "source_type": "event", "source_event_id": eid, "summary": "look"})
        cid = r.get_json()["id"]
        again = c.post("/soc/api/cases", json={"title": "dupe", "source_event_id": eid}).get_json()
        assert again["id"] == cid and "already open" in again["message"]                 # one open case per event
        assert c.post("/soc/api/cases", json={"title": "x"}).status_code == 400
        assert c.post(f"/soc/api/cases/{cid}/notes", json={"body": "checked the logs"}).status_code == 200
        assert c.post(f"/soc/api/cases/{cid}/notes", json={"body": "  "}).status_code == 400
        assert c.post(f"/soc/api/cases/{cid}", json={"status": "investigating", "assignee": "ann"}).status_code == 200
        assert c.post(f"/soc/api/cases/{cid}", json={"status": "bogus"}).status_code == 400
        d = c.get(f"/soc/api/cases/{cid}").get_json()["case"]
        assert d["status"] == "investigating" and d["notes"][0]["body"] == "checked the logs" and d["source_event"]["id"] == eid
        assert d["closed_at"] is None
        assert [x["id"] for x in c.get("/soc/api/cases", query_string={"status": "open"}).get_json()["cases"]] == [cid]
        c.post(f"/soc/api/cases/{cid}", json={"status": "resolved", "resolution": "blocked + patched"})
        d = c.get(f"/soc/api/cases/{cid}").get_json()["case"]
        assert d["closed_at"] and d["resolution"] == "blocked + patched"
        assert c.get("/soc/api/cases", query_string={"status": "open"}).get_json()["cases"] == []
        assert c.get("/soc/api/events", query_string={"event_type": "waf.sqli_blocked"}).get_json()["events"][0]["case"]["id"] == cid
        assert c.get("/soc/api/cases/999999").status_code == 404
        pulse = c.get("/soc/api/pulse").get_json()
        assert pulse["open_cases"] == 0

    def test_a_reopened_event_gets_a_fresh_case(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        eid = add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP)
        first = c.post("/soc/api/cases", json={"title": "first", "source_event_id": eid}).get_json()["id"]
        c.post(f"/soc/api/cases/{first}", json={"status": "false_positive"})
        second = c.post("/soc/api/cases", json={"title": "second", "source_event_id": eid}).get_json()["id"]
        assert second != first


class TestAssessmentViews:
    def test_tenants_attribute_events_by_url_prefix(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "access.denied", "WARNING", path="/att-test-suite/admin")
        add_event(db_engine, "waf.sqli_blocked", "ERROR", path="/att-test-suite/search")
        add_event(db_engine, "access.denied", "WARNING", path="/login")
        t = {x["slug"]: x for x in c.get("/soc/api/tenants").get_json()["tenants"]}["att-test-suite"]
        assert t["events"] == 2 and t["alerts"] == 1 and t["risk"] >= 8

    def test_attendance_integrity_aggregates_punch_evidence(self, soc, db_engine):
        c, _ = soc()
        cur = db_engine.cursor()
        punches = [("E1", "accepted", False, ""), ("E2", "rejected", False, "photo_replay,shared_device"),
                   ("E3", "accepted", True, "mock_location")]
        for emp, outcome, mocked, flags in punches:
            cur.execute("INSERT INTO public.punch_evidence (employee_id, channel, outcome, mocked, flags) "
                        "VALUES (%s,'mobile',%s,%s,%s)", (emp, outcome, mocked, flags))
        cur.close()
        d = c.get("/soc/api/attendance", query_string={"days": 7}).get_json()
        assert d["totals"] == {"punches": 3, "rejected": 1, "mock_location": 1, "flagged": 2}
        assert {f["flag"]: f["count"] for f in d["flags"]} == {"photo_replay": 1, "shared_device": 1, "mock_location": 1}
        assert d["tenants"][0]["slug"] == "att-test-suite"

    def test_posture_never_reveals_secrets(self, soc, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "ultra-secret-signing-key-123")
        monkeypatch.setenv("ENCRYPTION_KEY", "ultra-secret-fernet-key-456")
        c, _ = soc()
        r = c.get("/soc/api/posture")
        body = r.get_data(as_text=True)
        assert "ultra-secret" not in body
        d = r.get_json()
        assert {k["id"] for k in d["checks"]} >= {"env", "secret", "enc", "proxy", "dbsplit", "liveness", "socnet"}
        assert d["summary"]["pass"] >= 2 and all(k["status"] in ("pass", "warn", "fail", "info") for k in d["checks"])

    def test_posture_reflects_the_allow_list(self, soc, monkeypatch):
        c, _ = soc()
        status = lambda: {x["id"]: x["status"] for x in c.get("/soc/api/posture").get_json()["checks"]}["socnet"]   # noqa: E731
        assert status() == "warn"
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "127.0.0.0/8")
        assert status() == "pass"

    def test_audit_chain_verification(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        for i in range(5):
            add_event(db_engine, "auth.failure", ip=PUBLIC_IP, message=f"m{i}")
        assert c.get("/soc/api/integrity").get_json()["report"] is None
        r = c.post("/soc/api/integrity/verify").get_json()
        # The console must report exactly what the verifier says about the shared chain (whose
        # state this test does not control), and must report the rows it just added.
        from utils import audit_chain
        cur = db_engine.cursor()
        expected = audit_chain.verify_table(cur, "public", "security_events")
        cur.close()
        assert r["report"]["ok"] is expected["ok"] and r["report"]["rows_chained"] >= 5
        assert r["report"]["violations"] == expected["violations"] and r["by"] == "ann"
        assert c.get("/soc/api/integrity").get_json()["report"]["rows_chained"] == r["report"]["rows_chained"]
        assert ("ann", "verify_audit_chain", None) in audit_actions(db_engine)

    def test_verification_refuses_a_table_too_large_to_hold_in_memory(self, soc, monkeypatch):
        c, _ = soc("ann", "analyst")
        monkeypatch.setattr(soc_queries, "clamp_int", lambda *a, **k: 0)          # cap of zero rows
        r = c.post("/soc/api/integrity/verify")
        assert r.status_code == 400 and "verify_audit_chain.py" in r.get_json()["msg"]


class TestLoki:
    def test_not_configured(self, soc, monkeypatch):
        monkeypatch.delenv("LOKI_URL", raising=False)
        c, _ = soc("ann", "analyst")
        assert c.get("/soc/api/loki/status").get_json()["configured"] is False
        assert c.post("/soc/api/loki/query", json={"query": '{job="app"}'}).get_json()["configured"] is False

    def test_query_is_bounded_and_the_target_comes_only_from_the_environment(self, soc, monkeypatch):
        monkeypatch.setenv("LOKI_URL", "http://loki.internal:3100")
        seen = {}

        class Resp:
            def read(self, n=-1):
                return json.dumps({"data": {"result": [{"stream": {"job": "app"}, "values": [["1760000000000000000", "line <b>one</b>"]]}]}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen["url"], seen["timeout"] = req.full_url, timeout
            return Resp()
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        c, _ = soc("ann", "analyst")
        hostile = {"query": '{job="app"} |= "x"', "minutes": 99999, "limit": 99999, "host": "evil.example", "url": "http://evil.example"}
        r = c.post("/soc/api/loki/query", json=hostile).get_json()
        assert r["configured"] and r["lines"][0]["line"] == "line <b>one</b>" and r["minutes"] == 1440
        assert seen["url"].startswith("http://loki.internal:3100/loki/api/v1/query_range?") and "evil" not in seen["url"]
        assert "limit=500" in seen["url"] and seen["timeout"] == 10
        assert c.post("/soc/api/loki/query", json={"query": "x" * 501}).status_code == 400
        assert c.post("/soc/api/loki/query", json={"query": ""}).status_code == 400

    def test_loki_failures_are_reported_not_raised(self, soc, monkeypatch):
        monkeypatch.setenv("LOKI_URL", "http://loki.internal:3100")

        def boom(*a, **k):
            raise OSError("connection refused")
        monkeypatch.setattr("urllib.request.urlopen", boom)
        c, _ = soc("ann", "analyst")
        assert c.post("/soc/api/loki/query", json={"query": '{a="b"}'}).status_code == 502


class TestLogAnalysisEndpoint:
    def test_rejects_missing_file_and_reports_truncation(self, soc, monkeypatch):
        from utils import soc_loganalysis
        c, _ = soc("ann", "analyst")
        assert c.post("/soc/api/loganalysis", data={}, content_type="multipart/form-data").status_code == 400
        monkeypatch.setattr(soc_loganalysis, "MAX_BYTES", 2000)
        big = ("\n".join(f'{PUBLIC_IP} - - [16/Oct/2026:10:00:00 +0000] "GET /{i} HTTP/1.1" 200 1 "-" "Mozilla/5.0"' for i in range(200))).encode()
        r = c.post("/soc/api/loganalysis", data={"log": (io.BytesIO(big), "big.log")}, content_type="multipart/form-data").get_json()
        assert r["report"]["limits"]["truncated_bytes"] is True

    def test_binary_and_non_utf8_input_is_survivable(self, soc):
        c, _ = soc("ann", "analyst")
        r = c.post("/soc/api/loganalysis", data={"log": (io.BytesIO(bytes(range(256)) * 50), "junk.bin")}, content_type="multipart/form-data")
        assert r.status_code == 200 and r.get_json()["ok"]
