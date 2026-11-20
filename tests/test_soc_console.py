"""SOC console: authentication, isolation, roles, data endpoints and response actions.

These walk the REAL login flow (email address -> one-time code emailed to it), because
that flow is the console's front door and the part most worth proving. Only the email
delivery itself is replaced: codes land in OUTBOX instead of an inbox.
"""
import csv
import io
import json
import os
import re
import time

import psycopg2
import pytest

from extensions import app as flask_app
from utils import soc_auth, soc_queries
from utils.soc_db import write_cursor

PUBLIC_IP = "45.33.32.156"        # routable (not private / documentation / reserved)
OUTBOX = []                       # (email, code) for every code the console sent
_REAL_DELIVER = soc_auth.deliver_login_code


@pytest.fixture(autouse=True)
def _fresh_soc_state(monkeypatch):
    monkeypatch.delenv("SOC_ALLOWED_CIDRS", raising=False)
    for name in ("SOC_TEST_CIDRS", "SOC_TEST_ACCOUNTS", "SOC_TEST_COMPANIES"):
        monkeypatch.delenv(name, raising=False)
    # Recorded by monkeypatch so whatever allow() adds is undone after the test.
    monkeypatch.setenv("SOC_LOGIN_EMAILS", "")
    # Sign-in here is the emailed code alone; the passkey step after it has its
    # own tests (tests/test_soc_passkeys.py), which turn it back on.
    monkeypatch.setenv("SOC_PASSKEY", "off")
    OUTBOX.clear()
    monkeypatch.setattr(soc_auth, "deliver_login_code", lambda email, code: OUTBOX.append((email, code)) or True)
    soc_queries.clear_cache()
    yield
    soc_queries.clear_cache()


# ── helpers ──────────────────────────────────────────────────────────
def allow(email, role="admin"):
    """List `email` in SOC_LOGIN_EMAILS with `role`, as the server's .env would."""
    keep = [e for e in os.environ.get("SOC_LOGIN_EMAILS", "").split(",")
            if e.strip() and e.split(":")[0].strip().lower() != email.lower()]
    os.environ["SOC_LOGIN_EMAILS"] = ",".join(keep + [f"{email}:{role}"])


def disallow(email):
    os.environ["SOC_LOGIN_EMAILS"] = ",".join(
        e for e in os.environ.get("SOC_LOGIN_EMAILS", "").split(",")
        if e.strip() and e.split(":")[0].strip().lower() != email.lower())


def make_user(username="alice", role="admin"):
    """A console account <username>@example.com, listed in SOC_LOGIN_EMAILS. -> email.
    The row is made directly (like an account from before email sign-in) so audit
    actors stay the short username."""
    email = f"{username}@example.com"
    with write_cursor("att_master") as cur:
        cur.execute("INSERT INTO soc_users (username, email, password_hash, role, created_by) "
                    "VALUES (%s,%s,'!test',%s,'test') ON CONFLICT (username) DO NOTHING", (username, email, role))
    allow(email, role)
    return email


def codes_for(email):
    return [c for e, c in OUTBOX if e == email]


def allow_resend(email):
    """Lift the one-code-per-minute limit for `email` (the test does not wait a minute)."""
    with write_cursor("att_master") as cur:
        cur.execute("UPDATE soc_users SET login_code_sent_at=NULL WHERE lower(email)=%s", (email.lower(),))


def request_code(client, email):
    r = client.post("/socperson/login", data={"action": "email", "email": email})
    assert r.status_code == 303, r.get_data(as_text=True)
    return codes_for(email)[-1] if codes_for(email) else None


def sign_in(client, email):
    """Ask for a code, read it from the captured email, enter it. -> the code response."""
    allow_resend(email)
    code = request_code(client, email)
    assert code, "no code was sent to " + email
    return client.post("/socperson/login", data={"action": "code", "code": code})


def alert_text(r):
    return r.get_data(as_text=True).split('role="alert">')[1].split("<")[0]


@pytest.fixture
def soc(client):
    """soc(username, role) -> (client, email): a fully signed-in console session.
    Depends on `client` for its TESTING config (CSRF off, as for the rest of the suite);
    without it a class run on its own would see CSRF rejections the full run hides."""
    def make(username="alice", role="admin"):
        email = make_user(username, role)
        c = flask_app.test_client()
        r = sign_in(c, email)
        assert r.status_code == 303 and r.headers["Location"].endswith("/socperson"), r.get_data(as_text=True)
        return c, email
    return make


def add_event(db, event_type, level="WARNING", ip=None, identifier=None, path=None, method="GET",
              message="test event", extra=None, minutes_ago=5, tenant=None):
    cur = db.cursor()
    cur.execute(
        "INSERT INTO public.security_events (event_type, level, message, identifier, ip, path, method, extra_json, "
        "created_at, tenant) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW() - (%s * INTERVAL '1 minute'), %s) RETURNING id",
        (event_type, level, message, identifier, ip, path, method, json.dumps(extra) if extra else None, minutes_ago,
         tenant))
    eid = cur.fetchone()[0]
    cur.close()
    return eid


def _grant_runtime_access(cur, *schemas):
    """What real company provisioning does after creating a schema: give the
    restricted runtime role its grants (database.grant_runtime_privileges). A
    no-op unless the suite runs with the owner / runtime split (CI's
    test-role-split job), where the app could not read the schema otherwise."""
    import database
    if not database.owner_role_configured():
        return
    for schema in schemas:
        for stmt in database.runtime_grant_statements(schema, os.environ["DB_USER"]):
            cur.execute(stmt)


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
        r = client.get("/socperson")
        assert r.status_code == 302 and r.headers["Location"].endswith("/socperson/login")
        for path in ("/socperson/api/me", "/socperson/api/overview", "/socperson/api/events", "/socperson/api/users", "/socperson/api/pulse"):
            r = client.get(path)
            assert r.status_code == 401 and r.get_json()["ok"] is False, path
        assert client.post("/socperson/api/bans", json={"ip": PUBLIC_IP, "reason": "x"}).status_code == 401

    def test_only_the_login_stylesheet_is_public(self, client):
        assert client.get("/socperson/assets/login.css").status_code == 200
        for name in ("soc.css", "soc-core.js", "soc-views.js", "soc-analysis.js"):
            assert client.get("/socperson/assets/" + name).status_code == 404, name
        assert client.get("/socperson/assets/../app.py").status_code in (404, 308)
        assert client.get("/socperson/assets/nope.js").status_code == 404

    def test_login_page_is_marked_no_store_and_noindex(self, client):
        r = client.get("/socperson/login")
        assert r.status_code == 200
        assert "no-store" in r.headers["Cache-Control"]
        assert "noindex" in r.headers["X-Robots-Tag"]
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert b"noindex" in r.data

    def test_authenticated_assets_are_served_to_a_session(self, soc):
        c, _ = soc()
        for name in ("soc.css", "soc-core.js", "soc-views.js", "soc-analysis.js"):
            r = c.get("/socperson/assets/" + name)
            assert r.status_code == 200, name
        assert "javascript" in c.get("/socperson/assets/soc-core.js").content_type

    def test_the_old_path_is_gone(self, client, soc):
        c, _ = soc()
        for path in ("/soc", "/soc/login", "/soc/api/me", "/soc/assets/login.css"):
            assert client.get(path).status_code == 404, path
            assert c.get(path).status_code == 404, path
        assert client.post("/soc/login", data={"action": "email", "email": "alice@example.com"}).status_code == 404


class TestLoginFlow:
    def test_a_listed_address_signs_in_with_the_emailed_code(self, client, db_engine):
        allow("Owner@Example.com")
        r = client.post("/socperson/login", data={"action": "email", "email": "  OWNER@example.com "})
        assert r.status_code == 303
        page = client.get("/socperson/login").get_data(as_text=True)
        assert "Check your email" in page and "owner@example.com" in page
        (code,) = codes_for("owner@example.com")
        assert len(code) == 6 and code.isdigit()
        assert client.get("/socperson").status_code == 302          # nothing yet without the code
        r = client.post("/socperson/login", data={"action": "code", "code": code[:3] + " " + code[3:]})
        assert r.status_code == 303 and r.headers["Location"].endswith("/socperson")
        assert client.get("/socperson").status_code == 200
        me = client.get("/socperson/api/me").get_json()["user"]
        assert me["username"] == "owner@example.com" and me["role"] == "admin"
        acts = audit_actions(db_engine)
        assert ("owner@example.com", "login_code_sent", None) in acts and ("owner@example.com", "login", None) in acts

    def test_an_unlisted_address_sees_the_same_screens_and_gets_nothing(self, client, db_engine):
        allow("owner@example.com")
        pages = {}
        for email in ("owner@example.com", "stranger@example.com"):
            c = flask_app.test_client()
            assert c.post("/socperson/login", data={"action": "email", "email": email}).status_code == 303
            page = c.get("/socperson/login").get_data(as_text=True).replace(email, "EMAIL")
            main = page.split("<main")[1].split("</main>")[0]               # what the visitor sees
            pages[email] = re.sub(r"[0-9a-f]{64}", "TOKEN", main)          # per-session CSRF tokens differ
            r = c.post("/socperson/login", data={"action": "code", "code": "000000"})
            pages[email + " wrong"] = (r.status_code, alert_text(r))
        assert pages["owner@example.com"] == pages["stranger@example.com"]
        assert pages["owner@example.com wrong"] == pages["stranger@example.com wrong"] == (401, "That code is not valid or has expired.")
        assert codes_for("stranger@example.com") == []
        assert soc_auth.get_user_by_email("stranger@example.com") is None       # no account made for it
        assert any(a == "login_failed" and actor == "stranger@example.com" for actor, a, _ in audit_actions(db_engine))

    def test_nobody_can_sign_in_when_the_list_is_empty(self, client):
        assert request_code(client, "anyone@example.com") is None
        assert OUTBOX == []

    def test_a_code_works_once(self, client):
        email = make_user("alice")
        r = sign_in(client, email)
        assert r.status_code == 303
        used = codes_for(email)[-1]
        client.post("/socperson/logout")
        # Asking again within the minute sends nothing new; the used code is spent.
        assert request_code(client, email) == used and len(codes_for(email)) == 1
        r = client.post("/socperson/login", data={"action": "code", "code": used})
        assert r.status_code == 401 and client.get("/socperson").status_code == 302

    def test_a_code_expires(self, client, db_engine):
        email = make_user("alice")
        code = request_code(client, email)
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.soc_users SET login_code_expires_at = NOW() - INTERVAL '1 second' WHERE email=%s", (email,))
        cur.close()
        r = client.post("/socperson/login", data={"action": "code", "code": code})
        assert r.status_code == 401 and alert_text(r) == "That code is not valid or has expired."

    def test_one_code_a_minute_and_a_new_code_replaces_the_old(self, client):
        email = make_user("alice")
        first = request_code(client, email)
        assert client.post("/socperson/login", data={"action": "resend"}).status_code == 200
        assert codes_for(email) == [first]                      # still within the minute
        allow_resend(email)
        r = client.post("/socperson/login", data={"action": "resend"})
        assert r.status_code == 200 and b"new code is on its way" in r.data
        second = codes_for(email)[-1]
        assert len(codes_for(email)) == 2
        if second != first:
            assert client.post("/socperson/login", data={"action": "code", "code": first}).status_code == 401
        assert client.post("/socperson/login", data={"action": "code", "code": second}).status_code == 303

    def test_codes_are_stored_only_as_a_keyed_hash(self, client, db_engine):
        email = make_user("alice")
        code = request_code(client, email)
        cur = db_engine.cursor()
        cur.execute("SELECT login_code_hash FROM att_master.soc_users WHERE email=%s", (email,))
        stored = cur.fetchone()[0]
        cur.close()
        assert stored and len(stored) == 64 and code not in stored
        import hashlib
        assert stored != hashlib.sha256(code.encode()).hexdigest()      # not an unkeyed hash either

    def test_wrong_codes_lock_the_account_and_void_its_code(self, client, db_engine):
        email = make_user("alice")
        good = request_code(client, email)
        bad = "000000" if good != "000000" else "111111"
        last = None
        for _ in range(5):
            last = client.post("/socperson/login", data={"action": "code", "code": bad})
        assert last.status_code == 429 and alert_text(last) == "Too many failed attempts. Try again in 15 minutes."
        assert soc_auth.get_user_by_email(email)["is_locked"] is True
        acts = [a for _, a, _ in audit_actions(db_engine)]
        assert "account_locked" in acts
        # The outstanding code died with the lock, and a locked account is sent nothing.
        allow_resend(email)
        assert request_code(client, email) == good and len(codes_for(email)) == 1
        assert client.post("/socperson/login", data={"action": "code", "code": good}).status_code == 429
        soc_auth.unlock(soc_auth.get_user_by_email(email)["id"])
        assert client.post("/socperson/login", data={"action": "code", "code": good}).status_code == 401
        assert sign_in(client, email).status_code == 303

    def test_a_disabled_account_is_sent_nothing(self, client):
        email = make_user("dora", "viewer")
        soc_auth.update_user(soc_auth.get_user("dora")["id"], active=False)
        assert request_code(client, email) is None
        r = client.post("/socperson/login", data={"action": "code", "code": "123456"})
        assert r.status_code == 401 and alert_text(r) == "That code is not valid or has expired."

    def test_restart_discards_a_half_finished_login(self, client):
        email = make_user("alice")
        request_code(client, email)
        assert b"Check your email" in client.get("/socperson/login").data
        client.post("/socperson/login", data={"action": "restart"})
        assert b"Send sign-in code" in client.get("/socperson/login").data

    def test_a_code_without_a_request_in_this_browser_goes_nowhere(self, client):
        make_user("alice")
        r = client.post("/socperson/login", data={"action": "code", "code": "123456"})
        assert r.status_code == 401 and b"expired" in r.data
        assert client.get("/socperson").status_code == 302

    def test_malformed_addresses_are_refused_before_anything_happens(self, client):
        for bad in ("", "not-an-email", "a@b", "a b@example.com", "x@example.com,y@example.com"):
            r = client.post("/socperson/login", data={"action": "email", "email": bad})
            assert r.status_code == 400, bad
        assert OUTBOX == []

    def test_the_address_list(self, monkeypatch):
        monkeypatch.setenv("SOC_LOGIN_EMAILS", " A@X.com , b@y.com:Analyst, c@z.com:root, nonsense, d@w.com:viewer,, ")
        assert soc_auth.login_emails() == {"a@x.com": "admin", "b@y.com": "analyst", "d@w.com": "viewer"}
        assert soc_auth.login_role("B@Y.COM") == "analyst" and soc_auth.login_role("c@z.com") is None
        monkeypatch.setenv("SOC_LOGIN_EMAILS", "x" * 60 + "@example.com")      # longer than a username can be
        assert soc_auth.login_emails() == {}

    def test_without_email_the_code_is_only_ever_logged_in_development(self, monkeypatch):
        import utils.email_utils as email_utils
        from extensions import app_log
        monkeypatch.setattr(email_utils, "get_email_config", lambda: None)
        logged = []
        monkeypatch.setattr(app_log, "warning", lambda msg, *a: logged.append(msg % a))
        monkeypatch.setattr(app_log, "error", lambda msg, *a: logged.append(msg % a))
        monkeypatch.setenv("APP_ENV", "production")
        assert _REAL_DELIVER("ops@example.com", "482913") is False
        assert logged and not any("482913" in m for m in logged)
        monkeypatch.setenv("APP_ENV", "development")
        assert _REAL_DELIVER("ops@example.com", "482913") is False
        assert any("482913" in m for m in logged)

    def test_the_code_email_is_queued_with_the_code(self, monkeypatch):
        import utils.email_utils as email_utils
        sent = []
        monkeypatch.setattr(email_utils, "get_email_config", lambda: {"smtp_host": "x"})
        monkeypatch.setattr(email_utils, "send_email_async", lambda to, subject, body, cfg: sent.append((to, subject, body)))
        assert _REAL_DELIVER("ops@example.com", "482913") is True
        (to, subject, body), = sent
        assert to == "ops@example.com" and "sign-in code" in subject and "482913" in body


# ════════════════════════════════════════════════════════════════════
# session lifetime and invalidation
# ════════════════════════════════════════════════════════════════════
class TestSessionLifecycle:
    def test_idle_timeout(self, soc):
        c, _ = soc()
        assert c.get("/socperson/api/me").status_code == 200
        with c.session_transaction() as s:
            s["soc_last"] -= 3600
        assert c.get("/socperson/api/me").status_code == 401

    def test_absolute_timeout(self, soc):
        c, _ = soc()
        with c.session_transaction() as s:
            s["soc_at"] -= 24 * 3600
            s["soc_last"] = time.time()
        assert c.get("/socperson/api/me").status_code == 401

    def test_background_polling_does_not_extend_the_session(self, soc):
        c, _ = soc()
        with c.session_transaction() as s:
            s["soc_last"] = time.time() - 120
        before = time.time() - 120
        assert c.get("/socperson/api/pulse", headers={"X-SOC-Poll": "1"}).status_code == 200
        with c.session_transaction() as s:
            assert abs(s["soc_last"] - before) < 5            # untouched
        assert c.get("/socperson/api/pulse").status_code == 200
        with c.session_transaction() as s:
            assert s["soc_last"] > before + 100               # a real action refreshes it

    def test_deactivating_an_account_ends_its_session_at_once(self, soc):
        admin, _ = soc("alice", "admin")
        victim, _ = soc("bob", "analyst")
        assert victim.get("/socperson/api/me").status_code == 200
        uid = soc_auth.get_user("bob")["id"]
        assert admin.post(f"/socperson/api/users/{uid}", json={"action": "deactivate"}).status_code == 200
        assert victim.get("/socperson/api/me").status_code == 401

    def test_changing_a_role_in_the_list_ends_the_session(self, soc):
        other, email = soc("bob", "analyst")
        allow(email, "viewer")
        assert other.get("/socperson/api/me").status_code == 401
        assert sign_in(other, email).status_code == 303
        assert other.get("/socperson/api/me").get_json()["user"]["role"] == "viewer"

    def test_removing_an_address_ends_its_session_and_its_sign_in(self, soc):
        c, email = soc("bob", "analyst")
        disallow(email)
        assert c.get("/socperson/api/me").status_code == 401
        allow_resend(email)
        assert request_code(c, email) == codes_for(email)[-1] and len(codes_for(email)) == 1   # nothing new sent

    def test_logout_ends_the_session(self, soc, db_engine):
        c, _ = soc()
        c.post("/socperson/logout")
        assert c.get("/socperson/api/me").status_code == 401
        assert "logout" in [a for _, a, _ in audit_actions(db_engine)]

    def test_sign_out_everywhere_ends_every_session_of_that_person_only(self, soc, db_engine):
        laptop, email = soc("alice", "admin")
        phone = flask_app.test_client()
        assert sign_in(phone, email).status_code == 303
        colleague, _ = soc("bob", "analyst")
        r = laptop.post("/socperson/logout/everywhere")
        assert r.status_code == 303 and r.headers["Location"].endswith("/socperson/login")
        assert laptop.get("/socperson/api/me").status_code == 401
        assert phone.get("/socperson/api/me").status_code == 401           # the other browser too
        assert colleague.get("/socperson/api/me").status_code == 200       # nobody else
        assert ("alice", "logout_everywhere", None) in audit_actions(db_engine)
        assert sign_in(laptop, email).status_code == 303                  # and signing in again works
        assert laptop.get("/socperson/api/me").status_code == 200
        assert flask_app.test_client().post("/socperson/logout/everywhere").status_code == 302   # needs a session

    def test_a_session_without_the_current_epoch_ends(self, soc):
        c, _ = soc()
        with c.session_transaction() as s:
            del s["soc_epoch"]                     # e.g. a session from before this check existed
        assert c.get("/socperson/api/me").status_code == 401

    def test_logout_also_kills_a_copied_cookie(self, soc):
        """Security review Section 9: logout revokes the session server-side."""
        c, _ = soc()
        assert c.get("/socperson/api/me").status_code == 200
        stolen = c.get_cookie("__Host-session").value
        c.post("/socperson/logout")
        c.set_cookie("__Host-session", stolen)
        assert c.get("/socperson/api/me").status_code == 401


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
        assert client.get("/socperson").status_code == 302
        for path in ("/socperson/api/overview", "/socperson/api/users", "/socperson/api/events"):
            r = client.get(path)
            assert r.status_code in (302, 401), path
            assert b'"kpi"' not in r.data and b'"users"' not in r.data and b'"events"' not in r.data, path

    def test_a_console_session_opens_nothing_else(self, soc):
        c, _ = soc("alice", "admin")
        assert c.get("/admin").status_code in (302, 401)
        assert c.get("/super_admin").status_code == 404  # OBS-07
        assert c.get("/employee_portal").status_code in (302, 401)
        assert c.get("/api/employees").status_code == 401

    @pytest.mark.parametrize("slug", ["socperson", "soc"])
    def test_the_console_path_is_never_a_company_slug(self, slug):
        from blueprints.org import _validate_new_tenant_fields
        err = _validate_new_tenant_fields("Soc Inc", slug, "admin1", "password123", "a@b.co", "b.co")
        assert err and "reserved" in err.lower()

    def test_console_is_only_reachable_at_the_canonical_path(self, soc):
        """/<company>/socperson would otherwise be stripped to /socperson by the tenant middleware."""
        c, email = soc()
        for path in ("/att-test-suite/socperson", "/att-test-suite/socperson/login", "/att-test-suite/socperson/api/me",
                     "/att-test-suite/socperson/assets/login.css"):
            assert c.get(path).status_code == 404, path
        assert c.post("/att-test-suite/socperson/login", data={"action": "email", "email": email}).status_code == 404
        assert c.get("/socperson").status_code == 200 and c.get("/socperson/api/me").status_code == 200

    def test_response_headers(self, soc):
        c, _ = soc()
        for path in ("/socperson", "/socperson/api/me", "/socperson/assets/soc.css"):
            r = c.get(path)
            assert "no-store" in r.headers["Cache-Control"], path
            assert "noindex" in r.headers["X-Robots-Tag"], path
        csp = c.get("/socperson").headers["Content-Security-Policy"]
        assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]

    def test_console_is_not_advertised(self, client):
        link = re.compile(rb"""["'(=\s]/soc(?:person)?(?:["'/?#)\s<]|$)""")
        for path in ("/robots.txt", "/sitemap.xml", "/", "/login", "/create_org"):
            assert not link.search(client.get(path).data), path


class TestNetworkAllowList:
    def test_outside_addresses_get_a_404_everywhere(self, client, monkeypatch):
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "10.0.0.0/8")
        for path in ("/socperson", "/socperson/login", "/socperson/api/me", "/socperson/assets/login.css"):
            assert client.get(path).status_code == 404, path
        assert client.post("/socperson/login", data={"action": "email", "email": "a@example.com"}).status_code == 404
        assert client.post("/socperson/logout").status_code == 404

    def test_inside_addresses_work_and_a_malformed_list_denies_everyone(self, client, monkeypatch):
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "127.0.0.0/8, 10.0.0.0/8")
        assert client.get("/socperson/login").status_code == 200
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "not-a-network")
        assert client.get("/socperson/login").status_code == 404            # fails closed

    def test_a_valid_session_is_still_refused_from_outside(self, soc, monkeypatch):
        c, _ = soc()
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "10.0.0.0/8")
        assert c.get("/socperson").status_code == 404 and c.get("/socperson/api/me").status_code == 404

    def test_unset_means_closed_everywhere_but_development(self, monkeypatch):
        monkeypatch.delenv("SOC_ALLOWED_CIDRS", raising=False)
        for env in ("production", "staging", ""):
            monkeypatch.setenv("APP_ENV", env)
            assert soc_auth.ip_allowed("127.0.0.1") is False and soc_auth.ip_allowed(PUBLIC_IP) is False, env
            assert soc_auth.allow_list_state() == "unset_closed"
        monkeypatch.delenv("APP_ENV")                                       # unset counts as production
        assert soc_auth.ip_allowed("127.0.0.1") is False
        monkeypatch.setenv("APP_ENV", "development")
        assert soc_auth.ip_allowed(PUBLIC_IP) is True and soc_auth.allow_list_state() == "unset_open"
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "0.0.0.0/0,::/0")           # open on purpose
        assert soc_auth.ip_allowed(PUBLIC_IP) is True and soc_auth.ip_allowed("2001:db8::1") is True
        assert soc_auth.allow_list_state() == "everywhere"

    def test_unset_list_closes_the_login_page_in_production(self, client, monkeypatch):
        monkeypatch.delenv("SOC_ALLOWED_CIDRS", raising=False)
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/socperson/login").status_code == 404
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "127.0.0.0/8")
        assert client.get("/socperson/login").status_code == 200


class TestWafHandling:
    SQLI = "1' UNION SELECT username,password FROM users--"

    def test_the_login_page_is_still_inspected(self, client):
        assert client.get("/socperson/login", query_string={"x": self.SQLI}).status_code == 403

    def test_analysts_can_search_for_attack_strings(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, path="/x?id=" + self.SQLI)
        r = c.get("/socperson/api/events", query_string={"q": "UNION SELECT"})
        assert r.status_code == 200 and len(r.get_json()["events"]) == 1

    def test_unauthenticated_callers_get_no_exemption(self, client):
        assert client.get("/socperson/api/events", query_string={"q": self.SQLI}).status_code == 403

    def test_log_upload_with_attack_content_is_not_blocked_and_is_analysed(self, soc, db_engine):
        c, _ = soc()
        log = "\n".join([
            f'{PUBLIC_IP} - - [16/Oct/2026:10:00:0{i} +0000] "GET /?id=1%27%20UNION%20SELECT%20password%20FROM%20users-- HTTP/1.1" 200 5 "-" "sqlmap/1.7"'
            for i in range(5)] + ["GET /?q=<script>alert(1)</script>"])
        r = c.post("/socperson/api/loganalysis", data={"log": (io.BytesIO(log.encode()), "attack.log")}, content_type="multipart/form-data")
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
        for path in ("/socperson/api/overview", "/socperson/api/events", "/socperson/api/bans", "/socperson/api/cases",
                     "/socperson/api/posture", "/socperson/api/detections"):
            assert c.get(path).status_code == 200, path
        for method, path, body in [("post", "/socperson/api/bans", {"ip": PUBLIC_IP, "reason": "x y z"}),
                                   ("post", "/socperson/api/bans/remove", {"ip": PUBLIC_IP}),
                                   ("post", "/socperson/api/cases", {"title": "abc"}), ("post", "/socperson/api/integrity/verify", {}),
                                   ("post", "/socperson/api/sessions/revoke", {"tenant_id": 1, "sid": "x"}), ("get", "/socperson/api/events.csv", None),
                                   ("post", "/socperson/api/loki/query", {"query": "{a=\"b\"}"})]:
            r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
            assert r.status_code == 403, path
        assert c.post("/socperson/api/loganalysis", data={"log": (io.BytesIO(b"x"), "a.log")}, content_type="multipart/form-data").status_code == 403
        assert ("vic", "forbidden", "/socperson/api/bans") in audit_actions(db_engine)

    def test_analyst_acts_but_cannot_administer(self, soc):
        c, _ = soc("ann", "analyst")
        assert c.post("/socperson/api/cases", json={"title": "Looks odd"}).status_code == 200
        for path in ("/socperson/api/users", "/socperson/api/audit"):
            assert c.get(path).status_code == 403, path
        assert c.post(f"/socperson/api/users/{soc_auth.get_user('ann')['id']}", json={"action": "unlock"}).status_code == 403

    def test_viewers_do_not_get_session_ids(self, soc, db_engine):
        cur = db_engine.cursor()
        cur.execute("INSERT INTO public.session_risk (sid, identifier, attempt_type, score, status) VALUES (%s,%s,'admin',9,'active')",
                    ("a" * 32, "victim@example.com"))
        v, _ = soc("vic", "viewer")
        a, _ = soc("ann", "analyst")
        sv = v.get("/socperson/api/sessions").get_json()["sessions"]
        sa = a.get("/socperson/api/sessions").get_json()["sessions"]
        assert sv and sa and all("sid" not in s for s in sv) and all("sid" in s for s in sa)
        cur.close()

    def test_user_administration(self, soc, db_engine):
        admin, _ = soc("alice", "admin")
        make_user("bob", "analyst")
        disallow("bob@example.com")
        users = {u["username"]: u for u in admin.get("/socperson/api/users").get_json()["users"]}
        assert set(users) == {"alice", "bob"}
        assert users["alice"]["listed"] is True and users["bob"]["listed"] is False
        secret_cols = ("password_hash", "totp_secret", "enroll_code_hash", "login_code_hash")
        assert all(k not in u for u in users.values() for k in secret_cols)
        # Accounts, passwords, authenticators and roles are no longer managed in the console.
        bob = users["bob"]["id"]
        assert admin.post("/socperson/api/users", json={"username": "newbie", "role": "admin"}).status_code == 405
        for action in ("set_role", "reset_mfa", "reset_password"):
            assert admin.post(f"/socperson/api/users/{bob}", json={"action": action, "role": "admin"}).status_code == 400, action
        assert admin.post("/socperson/api/me/password", json={"current": "x", "new": "y"}).status_code == 404
        assert admin.post(f"/socperson/api/users/{bob}", json={"action": "deactivate"}).status_code == 200
        assert admin.post(f"/socperson/api/users/{bob}", json={"action": "unlock"}).status_code == 200
        assert ("alice", "user_deactivated", "bob") in audit_actions(db_engine)

    def test_admins_cannot_lock_themselves_out(self, soc):
        admin, _ = soc("alice", "admin")
        me = soc_auth.get_user("alice")["id"]
        assert admin.post(f"/socperson/api/users/{me}", json={"action": "deactivate"}).status_code == 400
        assert admin.get("/socperson/api/me").status_code == 200

    def test_user_list_reports_current_lock_state(self, soc, db_engine):
        admin, _ = soc("alice", "admin")
        make_user("bob", "analyst")
        state = lambda: {u["username"]: u["is_locked"] for u in admin.get("/socperson/api/users").get_json()["users"]}["bob"]  # noqa: E731
        cur = db_engine.cursor()
        cur.execute("UPDATE att_master.soc_users SET locked_until = NOW() + INTERVAL '10 minutes' WHERE username='bob'")
        assert state() is True
        cur.execute("UPDATE att_master.soc_users SET locked_until = NOW() - INTERVAL '10 minutes' WHERE username='bob'")
        assert state() is False            # an expired lock is not shown as locked
        cur.close()

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
        d = c.get("/socperson/api/overview", query_string={"hours": 24}).get_json()
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
        assert c.get("/socperson/api/overview", query_string={"hours": 1}).get_json()["kpi"]["waf_blocks"] == 0
        assert c.get("/socperson/api/overview", query_string={"hours": 24}).get_json()["kpi"]["waf_blocks"] == 1
        assert c.get("/socperson/api/overview", query_string={"hours": "abc"}).get_json()["hours"] == 24
        assert c.get("/socperson/api/overview", query_string={"hours": 99999}).get_json()["hours"] == 720

    def test_waf_blocks_ignore_waf_housekeeping(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "waf.xss_blocked", "ERROR", ip=PUBLIC_IP)
        for et in ("waf.redis_error", "waf.ban_write_failed", "waf.ban_skipped_shared_ip", "waf.auto_ban"):
            add_event(db_engine, et, "WARNING", ip=PUBLIC_IP)
        assert c.get("/socperson/api/overview", query_string={"hours": 24}).get_json()["kpi"]["waf_blocks"] == 1

    def test_timeline_includes_empty_buckets(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, minutes_ago=60 * 10)
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, minutes_ago=5)
        tl = c.get("/socperson/api/overview", query_string={"hours": 24}).get_json()["timeline"]
        assert 24 <= len(tl) <= 25                                   # hourly buckets, quiet hours included
        assert [p["t"] for p in tl] == sorted(p["t"] for p in tl)
        assert sum(p["WARNING"] for p in tl) == 2
        assert sum(1 for p in tl if not any(p[k] for k in ("CRITICAL", "ERROR", "WARNING", "INFO"))) >= 20

    def test_quiet_system_is_normal(self, soc):
        c, _ = soc()
        assert c.get("/socperson/api/pulse").get_json()["threat"]["level"] == "normal"

    def test_event_filters(self, soc, db_engine):
        c, _ = soc()
        self.seed(db_engine)
        get = lambda **q: c.get("/socperson/api/events", query_string=q).get_json()["events"]   # noqa: E731
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
        get = lambda q: c.get("/socperson/api/events", query_string={"q": q}).get_json()["events"]   # noqa: E731
        assert [e["message"] for e in get("100%")] == ["discount 100% off"]
        assert [e["message"] for e in get("snake_case")] == ["snake_case here"]
        assert get("' OR '1'='1") == [] and get("\\") == []
        assert c.get("/socperson/api/events", query_string={"q": "x" * 5000}).status_code == 200

    def test_pagination_walks_every_event_once(self, soc, db_engine):
        c, _ = soc()
        made = {add_event(db_engine, "validation.failure", message=f"m{i}") for i in range(7)}
        seen, cursor = [], None
        for _ in range(10):
            d = c.get("/socperson/api/events", query_string={"event_type": "validation.failure", "limit": 3, "cursor": cursor or ""}).get_json()
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
        d = c.get(f"/socperson/api/events/{first}").get_json()["event"]
        assert d["extra"] == {"matched": "union select"} and d["mitre_detail"][0]["id"] == "T1190"
        assert {e["event_type"] for e in d["related"]["same_ip"]} == {"auth.failure"}
        assert {e["event_type"] for e in d["related"]["same_identifier"]} == {"auth.lockout"}
        assert d["chain"] and d["chain"]["row_hash"], "events carry their audit-chain hash"
        assert c.get("/socperson/api/events/99999999").status_code == 404

    def test_csv_export_neutralises_formulas(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="=HYPERLINK(\"http://evil\",\"x\")", path="/@evil", message="+cmd|' /C calc'!A0")
        r = c.get("/socperson/api/events.csv", query_string={"event_type": "auth.failure"})
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
        e = c.get(f"/socperson/api/entity/ip/{PUBLIC_IP}").get_json()["entity"]
        assert e["events"] == 4 and e["high_severity"] == 1 and e["event_types"] == 2
        assert {o["value"] for o in e["other"]} == {"u0", "u1", "u2"}
        assert {f["family"] for f in e["families"]} == {"auth", "waf"} and e["ban"] is None
        assert {t["id"] for t in e["techniques"]} == {"T1110", "T1190"}
        assert c.get("/socperson/api/entity/ip/not-an-ip").status_code == 400
        assert c.get("/socperson/api/entity/ip/1.2.3.4").get_json()["entity"]["events"] == 0

    def test_account_profile(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "auth.failure", ip="1.2.3.4", identifier="eve@example.com")
        add_event(db_engine, "auth.lockout", "ERROR", ip="5.6.7.8", identifier="eve@example.com")
        e = c.get("/socperson/api/entity/identifier", query_string={"value": "eve@example.com"}).get_json()["entity"]
        assert e["events"] == 2 and {o["value"] for o in e["other"]} == {"1.2.3.4", "5.6.7.8"}
        assert c.get("/socperson/api/entity/identifier").status_code == 400

    def test_detection_catalogue_covers_every_real_rule(self, soc, db_engine):
        from utils.detections import RULES
        c, _ = soc()
        add_event(db_engine, "detection.photo_replay", "ERROR", identifier="dev1", message="replay")
        d = c.get("/socperson/api/detections", query_string={"hours": 24}).get_json()
        assert {r["name"] for r in d["rules"]} == {r.name for r in RULES}
        assert all(r["label"] and r["meaning"] for r in d["rules"])
        assert [e["label"] for e in d["events"]] == ["Photo replay"]


class TestResponseActions:
    def test_ban_lifecycle_is_audited_and_enforced(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        assert c.post("/socperson/api/bans", json={"ip": PUBLIC_IP, "reason": "scanning us", "minutes": 60}).status_code == 200
        cur = db_engine.cursor()
        cur.execute("SELECT reason, banned_by, expires_at IS NOT NULL FROM public.banned_ips WHERE ip=%s", (PUBLIC_IP,))
        assert cur.fetchone() == ("SOC: scanning us", "soc:ann", True)
        bans = c.get("/socperson/api/bans").get_json()["bans"]
        assert bans[0]["ip"] == PUBLIC_IP and bans[0]["active"] is True
        # the banned address is refused by the app itself
        blocked = flask_app.test_client().get("/healthz2", environ_overrides={"REMOTE_ADDR": PUBLIC_IP})
        assert blocked.status_code == 403
        assert c.post("/socperson/api/bans/remove", json={"ip": PUBLIC_IP}).status_code == 200
        assert flask_app.test_client().get("/healthz2", environ_overrides={"REMOTE_ADDR": PUBLIC_IP}).status_code != 403
        assert c.post("/socperson/api/bans/remove", json={"ip": PUBLIC_IP}).status_code == 400
        acts = [(a, t) for _, a, t in audit_actions(db_engine)]
        assert ("ban_ip", PUBLIC_IP) in acts and ("unban_ip", PUBLIC_IP) in acts
        cur.close()

    def test_ban_guards(self, soc):
        c, _ = soc("ann", "analyst")
        elsewhere = {"REMOTE_ADDR": "8.8.4.4"}      # so the self-ban guard does not answer first
        for ip in ("10.0.0.5", "127.0.0.1", "192.168.1.1", "169.254.169.254"):
            r = c.post("/socperson/api/bans", json={"ip": ip, "reason": "test ban"}, environ_overrides=elsewhere)
            assert r.status_code == 400 and "private" in r.get_json()["msg"], ip
        assert c.post("/socperson/api/bans", json={"ip": "not-an-ip", "reason": "test ban"}).status_code == 400
        assert c.post("/socperson/api/bans", json={"ip": PUBLIC_IP, "reason": "x"}).status_code == 400            # reason too short
        r = c.post("/socperson/api/bans", json={"ip": "9.9.9.9", "reason": "test ban"}, environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        assert r.status_code == 400 and "connected from" in r.get_json()["msg"]

    def test_a_ban_is_never_shortened_by_a_later_one(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        c.post("/socperson/api/bans", json={"ip": PUBLIC_IP, "reason": "long one", "minutes": 0})
        c.post("/socperson/api/bans", json={"ip": PUBLIC_IP, "reason": "short one", "minutes": 5})
        cur = db_engine.cursor()
        cur.execute("SELECT expires_at FROM public.banned_ips WHERE ip=%s", (PUBLIC_IP,))
        assert cur.fetchone()[0] is None
        cur.close()

    def test_bulk_ban_reports_each_address(self, soc):
        c, _ = soc("ann", "analyst")
        r = c.post("/socperson/api/bans/bulk", json={"ips": [PUBLIC_IP, "10.1.1.1", "nonsense", PUBLIC_IP], "reason": "log analysis", "minutes": 60})
        res = {x["ip"]: x["ok"] for x in r.get_json()["results"]}
        assert res == {PUBLIC_IP: True, "10.1.1.1": False, "nonsense": False}
        assert c.post("/socperson/api/bans/bulk", json={"ips": [], "reason": "x"}).status_code == 400
        many = c.post("/socperson/api/bans/bulk", json={"ips": [f"44.1.1.{i}" for i in range(1, 120)], "reason": "flood test", "minutes": 5}).get_json()
        assert len(many["results"]) == 50                               # capped

    def test_active_bans_are_listed_before_expired_ones(self, soc, db_engine, monkeypatch):
        from utils import soc_queries as q
        monkeypatch.setattr(q, "BANS_LIMIT", 3)
        c, _ = soc("ann", "analyst")
        cur = db_engine.cursor()
        # Three recent expired bans, then one old permanent one: by time alone the
        # permanent ban would fall off a 3-row list although it is still enforced.
        for i in range(3):
            cur.execute("INSERT INTO public.banned_ips (ip, reason, banned_by, banned_at, expires_at) "
                        "VALUES (%s,'old','t',NOW() - INTERVAL '1 hour', NOW() - INTERVAL '1 minute')", (f"44.2.2.{i}",))
        cur.execute("INSERT INTO public.banned_ips (ip, reason, banned_by, banned_at) "
                    "VALUES ('44.3.3.3','permanent','t',NOW() - INTERVAL '30 days')")
        cur.close()
        d = c.get("/socperson/api/bans").get_json()
        assert d["bans"][0]["ip"] == "44.3.3.3" and d["bans"][0]["active"] is True
        assert d["active_total"] == 1 and len(d["bans"]) == 3

    def test_a_company_level_ban_is_listed_and_liftable(self, soc, db_engine):
        schema = "soc_ban_co"
        cur = db_engine.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'CREATE TABLE "{schema}".banned_ips (ip VARCHAR(45) PRIMARY KEY, reason VARCHAR(300), '
                    'banned_by VARCHAR(150), banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, expires_at TIMESTAMP)')
        cur.execute(f'INSERT INTO "{schema}".banned_ips (ip, reason, banned_by) VALUES (%s,\'legacy\',\'admin\')', (PUBLIC_IP,))
        cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
                    "VALUES ('Ban Co','ban-co',%s,'active') RETURNING id", (schema,))
        tid = cur.fetchone()[0]
        # A company schema WITHOUT its own table must not echo the platform list.
        cur.execute('CREATE SCHEMA "soc_noban_co"')
        cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
                    "VALUES ('No Ban Co','noban-co','soc_noban_co','active')")
        _grant_runtime_access(cur, schema, "soc_noban_co")
        cur.execute("INSERT INTO public.banned_ips (ip, reason, banned_by) VALUES ('44.4.4.4','platform','t')")
        c, _ = soc("ann", "analyst")
        bans = c.get("/socperson/api/bans").get_json()["bans"]
        assert sorted((b["ip"], b["tenant_id"]) for b in bans) == [("44.4.4.4", None), (PUBLIC_IP, tid)]
        # Lifting without the company leaves the company's ban alone ...
        assert c.post("/socperson/api/bans/remove", json={"ip": PUBLIC_IP}).status_code == 400
        # ... with it, removes exactly that row.
        assert c.post("/socperson/api/bans/remove", json={"ip": PUBLIC_IP, "tenant_id": tid}).status_code == 200
        cur.execute(f'SELECT COUNT(*) FROM "{schema}".banned_ips')
        assert cur.fetchone()[0] == 0
        # The no-table company falls back to nothing, not to public.banned_ips.
        # (Lifted above; re-add to check the other views agree with the list.)
        cur.execute(f'INSERT INTO "{schema}".banned_ips (ip, reason, banned_by) VALUES (%s,\'legacy\',\'admin\')', (PUBLIC_IP,))
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP)
        soc_queries.clear_cache()
        ov = c.get("/socperson/api/overview").get_json()
        assert ov["active_bans"] == c.get("/socperson/api/bans").get_json()["active_total"] == 2
        assert next(t for t in ov["top_ips"] if t["ip"] == PUBLIC_IP)["banned"] is True
        noban = next(t for t in soc_queries.list_tenants() if t["slug"] == "noban-co")
        assert c.post("/socperson/api/bans/remove", json={"ip": "44.4.4.4", "tenant_id": noban["id"]}).status_code == 400
        cur.execute("SELECT COUNT(*) FROM public.banned_ips WHERE ip='44.4.4.4'")
        assert cur.fetchone()[0] == 1
        assert c.post("/socperson/api/bans/remove", json={"ip": PUBLIC_IP, "tenant_id": 999999}).status_code == 400
        assert ("unban_ip", f"ban-co:{PUBLIC_IP}") in [(a, t) for _, a, t in audit_actions(db_engine)]
        cur.close()

    def test_wrongly_typed_json_fields_get_a_400_not_a_500(self, soc):
        c, _ = soc("alice", "admin")
        eid = c.post("/socperson/api/cases", json={"title": "typed input"}).get_json()["id"]
        bad = [
            ("/socperson/api/bans", {"ip": 123, "reason": ["x"]}),
            ("/socperson/api/bans/remove", {"ip": {"a": 1}}),
            ("/socperson/api/sessions/revoke", {"tenant_id": 1, "sid": 42}),
            ("/socperson/api/cases", {"title": 12345}),
            (f"/socperson/api/cases/{eid}", {"assignee": 5}),
            (f"/socperson/api/cases/{eid}/notes", {"body": ["note"]}),
            (f"/socperson/api/users/{soc_auth.get_user('alice')['id']}", {"action": ["deactivate"]}),
            (f"/socperson/api/users/{soc_auth.get_user('alice')['id']}", {"action": 7}),
        ]
        for url, body in bad:
            r = c.post(url, json=body)
            assert 400 <= r.status_code < 500, (url, body, r.status_code)
        # A wrongly typed assignee or resolution is refused, never read as "clear it".
        assert c.post(f"/socperson/api/cases/{eid}", json={"assignee": "alice"}).status_code == 200
        assert c.post(f"/socperson/api/cases/{eid}", json={"resolution": 7}).status_code == 400
        case = c.get(f"/socperson/api/cases/{eid}").get_json()["case"]
        assert case["assignee"] == "alice"
        # An optional summary that is not text is simply left out.
        assert c.post("/socperson/api/cases", json={"title": "with summary", "summary": {"x": 1}}).status_code == 200

    def test_revoking_a_risky_session(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO public.session_risk (sid, identifier, attempt_type, score, status) "
                    "VALUES (%s,'victim@example.com','admin',8,'active')", ("b" * 32,))
        s = c.get("/socperson/api/sessions").get_json()["sessions"][0]
        assert s["identifier"] == "victim@example.com" and s["sid"] == "b" * 32
        assert c.post("/socperson/api/sessions/revoke", json={"tenant_id": s["tenant_id"], "sid": s["sid"]}).status_code == 200
        cur.execute("SELECT status, last_reason FROM public.session_risk WHERE sid=%s", ("b" * 32,))
        status, reason = cur.fetchone()
        assert status == "compromised" and "ann" in reason
        from utils.session_risk import is_session_compromised
        with flask_app.app_context():
            assert is_session_compromised("b" * 32) is True
        assert c.post("/socperson/api/sessions/revoke", json={"tenant_id": s["tenant_id"], "sid": "missing"}).status_code == 400
        assert c.post("/socperson/api/sessions/revoke", json={"tenant_id": 999999, "sid": "b" * 32}).status_code == 400
        cur.close()


class TestCases:
    def test_case_workflow(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        eid = add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, message="blocked")
        r = c.post("/socperson/api/cases", json={"title": "SQLi from scanner", "severity": "high", "source_type": "event",
                                                 "source_event_id": eid, "summary": "look"})
        cid = r.get_json()["id"]
        again = c.post("/socperson/api/cases", json={"title": "dupe", "source_event_id": eid}).get_json()
        assert again["id"] == cid and "already open" in again["message"]                 # one open case per event
        assert c.post("/socperson/api/cases", json={"title": "x"}).status_code == 400
        assert c.post(f"/socperson/api/cases/{cid}/notes", json={"body": "checked the logs"}).status_code == 200
        assert c.post(f"/socperson/api/cases/{cid}/notes", json={"body": "  "}).status_code == 400
        assert c.post(f"/socperson/api/cases/{cid}", json={"status": "investigating", "assignee": "ann"}).status_code == 200
        assert c.post(f"/socperson/api/cases/{cid}", json={"status": "bogus"}).status_code == 400
        d = c.get(f"/socperson/api/cases/{cid}").get_json()["case"]
        assert d["status"] == "investigating" and d["notes"][0]["body"] == "checked the logs" and d["source_event"]["id"] == eid
        assert d["closed_at"] is None
        assert [x["id"] for x in c.get("/socperson/api/cases", query_string={"status": "open"}).get_json()["cases"]] == [cid]
        c.post(f"/socperson/api/cases/{cid}", json={"status": "resolved", "resolution": "blocked + patched"})
        d = c.get(f"/socperson/api/cases/{cid}").get_json()["case"]
        assert d["closed_at"] and d["resolution"] == "blocked + patched"
        assert c.get("/socperson/api/cases", query_string={"status": "open"}).get_json()["cases"] == []
        assert c.get("/socperson/api/events", query_string={"event_type": "waf.sqli_blocked"}).get_json()["events"][0]["case"]["id"] == cid
        assert c.get("/socperson/api/cases/999999").status_code == 404
        pulse = c.get("/socperson/api/pulse").get_json()
        assert pulse["open_cases"] == 0

    def test_a_reopened_event_gets_a_fresh_case(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        eid = add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP)
        first = c.post("/socperson/api/cases", json={"title": "first", "source_event_id": eid}).get_json()["id"]
        c.post(f"/socperson/api/cases/{first}", json={"status": "false_positive"})
        second = c.post("/socperson/api/cases", json={"title": "second", "source_event_id": eid}).get_json()["id"]
        assert second != first

    def test_assignees_are_real_analysts_and_my_cases_filter(self, soc):
        c, _ = soc("ann", "analyst")
        make_user("bob", "analyst")
        make_user("vic", "viewer")                       # cannot work a case
        assert c.get("/socperson/api/assignees").get_json()["assignees"] == ["ann", "bob"]
        mine = c.post("/socperson/api/cases", json={"title": "mine to work"}).get_json()["id"]       # creator is the assignee
        other = c.post("/socperson/api/cases", json={"title": "for bob"}).get_json()["id"]
        assert c.post(f"/socperson/api/cases/{other}", json={"assignee": "bob"}).status_code == 200
        for bad in ("vic", "b0b", "nobody"):
            r = c.post(f"/socperson/api/cases/{other}", json={"assignee": bad})
            assert r.status_code == 400 and "active analyst" in r.get_json()["msg"], bad
        assert c.post(f"/socperson/api/cases/{other}", json={"assignee": ""}).status_code == 200           # unassign
        ids = [x["id"] for x in c.get("/socperson/api/cases", query_string={"status": "open", "assignee": "ann"}).get_json()["cases"]]
        assert ids == [mine]

    def test_detection_rules_show_how_their_cases_ended(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        outcomes = ["false_positive", "false_positive", "resolved", None]           # None = still open
        for i, status in enumerate(outcomes):
            eid = add_event(db_engine, "detection.credential_stuffing", "ERROR", identifier=f"10.9.9.{i}")
            cid = c.post("/socperson/api/cases", json={"title": f"stuffing {i}", "source_type": "detection", "source_event_id": eid}).get_json()["id"]
            if status:
                c.post(f"/socperson/api/cases/{cid}", json={"status": status})
        rules = {r["name"]: r for r in c.get("/socperson/api/detections").get_json()["rules"]}
        o = rules["credential_stuffing"]["case_outcomes"]
        assert o == {"cases": 4, "open": 1, "confirmed": 1, "false_positive": 2, "fp_rate": 67, "needs_tuning": True}
        assert rules["distributed_bruteforce"]["case_outcomes"] is None


class TestAssessmentViews:
    def test_tenants_attribute_events_by_url_prefix(self, soc, db_engine):
        """Rows from before security_events.tenant existed: the path is the fallback."""
        c, _ = soc()
        add_event(db_engine, "access.denied", "WARNING", path="/att-test-suite/admin")
        add_event(db_engine, "waf.sqli_blocked", "ERROR", path="/att-test-suite/search")
        add_event(db_engine, "access.denied", "WARNING", path="/login")
        t = {x["slug"]: x for x in c.get("/socperson/api/tenants").get_json()["tenants"]}["att-test-suite"]
        assert t["events"] == 2 and t["alerts"] == 1 and t["risk"] >= 8

    def test_a_real_company_request_is_recorded_with_its_company(self, soc, client, db_engine):
        """The app takes the /<company>/ prefix out of the path before it runs, so
        the path alone never named the company: Tenant risk counted almost nothing.
        The event now records the company the request ran as -- in its own column
        and inside the hash-chained extra_json."""
        c, _ = soc()
        sqli = "1' UNION SELECT username,password FROM users--"
        visitor = {"REMOTE_ADDR": PUBLIC_IP}         # not the server's own address, which the console leaves out
        assert client.get("/att-test-suite/admin", query_string={"q": sqli}, environ_base=visitor).status_code == 403
        cur = db_engine.cursor()
        cur.execute("SELECT id, path, tenant, extra_json FROM public.security_events "
                    "WHERE event_type LIKE 'waf.%%blocked' ORDER BY id DESC LIMIT 1")
        eid, path, tenant, extra = cur.fetchone()
        cur.close()
        assert tenant == "att-test-suite" and not path.startswith("/att-test-suite")
        assert json.loads(extra)["tenant"] == "att-test-suite"
        # Every console view now sees which company it was ...
        t = {x["slug"]: x for x in c.get("/socperson/api/tenants").get_json()["tenants"]}["att-test-suite"]
        assert t["events"] >= 1 and t["alerts"] >= 1
        found = c.get("/socperson/api/events", query_string={"tenant": "att-test-suite"}).get_json()["events"]
        assert eid in [e["id"] for e in found]
        assert c.get(f"/socperson/api/events/{eid}").get_json()["event"]["tenant"] == "att-test-suite"
        # ... and a request that names no company belongs to none.
        assert flask_app.test_client().get("/admin", query_string={"q": sqli}, environ_base=visitor).status_code == 403
        cur = db_engine.cursor()
        cur.execute("SELECT tenant FROM public.security_events WHERE event_type LIKE 'waf.%%blocked' ORDER BY id DESC LIMIT 1")
        assert cur.fetchone()[0] is None
        cur.close()

    def test_a_listed_test_company_is_left_out_by_its_recorded_company(self, soc, db_engine, monkeypatch):
        monkeypatch.setenv("SOC_TEST_COMPANIES", "att-test-suite")
        c, _ = soc()
        add_event(db_engine, "auth.failure", "ERROR", ip=PUBLIC_IP, path="/login", tenant="att-test-suite",
                  message="from the test company")
        msgs = {e["message"] for e in c.get("/socperson/api/events").get_json()["events"]}
        assert "from the test company" not in msgs

    def test_attendance_integrity_aggregates_punch_evidence(self, soc, db_engine):
        c, _ = soc()
        cur = db_engine.cursor()
        punches = [("E1", "accepted", False, ""), ("E2", "rejected", False, "photo_replay,shared_device"),
                   ("E3", "accepted", True, "mock_location")]
        for emp, outcome, mocked, flags in punches:
            cur.execute("INSERT INTO public.punch_evidence (employee_id, channel, outcome, mocked, flags) "
                        "VALUES (%s,'mobile',%s,%s,%s)", (emp, outcome, mocked, flags))
        cur.close()
        d = c.get("/socperson/api/attendance", query_string={"days": 7}).get_json()
        assert d["totals"] == {"punches": 3, "rejected": 1, "mock_location": 1, "flagged": 2}
        assert {f["flag"]: f["count"] for f in d["flags"]} == {"photo_replay": 1, "shared_device": 1, "mock_location": 1}
        assert d["tenants"][0]["slug"] == "att-test-suite"

    def test_liveness_issues_count_outcomes_not_challenges(self, soc, db_engine):
        c, _ = soc()
        cur = db_engine.cursor()
        # A passed challenge carries its action ("blink") -- that is not an issue.
        for emp, state, action in [("E1", "passed", "blink"), ("E2", "failed", "turn_left"),
                                   ("E3", "challenge_invalid", None), ("E4", "missing", None),
                                   ("E5", "not_required", None)]:
            cur.execute("INSERT INTO public.punch_evidence (employee_id, channel, liveness, liveness_action) "
                        "VALUES (%s,'mobile',%s,%s)", (emp, state, action))
        cur.close()
        d = c.get("/socperson/api/attendance", query_string={"days": 7}).get_json()
        assert d["tenants"][0]["liveness_issues"] == 3

    def test_posture_never_reveals_secrets(self, soc, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "ultra-secret-signing-key-123")
        monkeypatch.setenv("ENCRYPTION_KEY", "ultra-secret-fernet-key-456")
        c, _ = soc()
        r = c.get("/socperson/api/posture")
        body = r.get_data(as_text=True)
        assert "ultra-secret" not in body
        d = r.get_json()
        assert {k["id"] for k in d["checks"]} >= {"env", "secret", "enc", "proxy", "dbsplit", "liveness", "socnet", "soclogin"}
        assert "alice@example.com" not in body          # how many may sign in, never who
        assert d["summary"]["pass"] >= 2 and all(k["status"] in ("pass", "warn", "fail", "info") for k in d["checks"])

    def test_posture_reflects_the_allow_list(self, soc, monkeypatch):
        c, _ = soc()
        status = lambda: {x["id"]: x["status"] for x in c.get("/socperson/api/posture").get_json()["checks"]}["socnet"]   # noqa: E731
        assert status() == "warn"                                       # unset, open only because APP_ENV=development
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "127.0.0.0/8")
        assert status() == "pass"
        monkeypatch.setenv("SOC_ALLOWED_CIDRS", "0.0.0.0/0,::/0,127.0.0.0/8")
        assert status() == "warn"                                       # open to everyone on purpose

    def test_posture_reports_whether_anyone_can_sign_in(self, monkeypatch):
        def status():
            with flask_app.app_context():
                return {x["id"]: x for x in soc_queries.posture()["checks"]}["soclogin"]
        monkeypatch.setenv("SOC_LOGIN_EMAILS", "")
        assert status()["status"] == "fail"
        monkeypatch.setenv("SOC_LOGIN_EMAILS", "ops@example.com, bad-entry")
        check = status()
        assert check["status"] == "pass" and "1 address" in check["detail"] and "ops@example.com" not in str(check)

    def test_audit_chain_verification(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        for i in range(5):
            add_event(db_engine, "auth.failure", ip=PUBLIC_IP, message=f"m{i}")
        assert c.get("/socperson/api/integrity").get_json()["report"] is None
        r = c.post("/socperson/api/integrity/verify").get_json()
        # The console must report exactly what the verifier says about the shared chain (whose
        # state this test does not control), and must report the rows it just added.
        from utils import audit_chain
        cur = db_engine.cursor()
        expected = audit_chain.verify_table(cur, "public", "security_events")
        cur.close()
        assert r["report"]["ok"] is expected["ok"] and r["report"]["rows_chained"] >= 5
        assert r["report"]["violations"] == expected["violations"] and r["by"] == "ann"
        last = c.get("/socperson/api/integrity").get_json()
        assert last["report"]["rows_chained"] == r["report"]["rows_chained"] and last["by"] == "ann" and last["at"]
        assert ("ann", "verify_audit_chain", None) in audit_actions(db_engine)

    def test_last_verification_is_read_from_the_database(self, soc, db_engine):
        # Stored in soc_audit_log, not one worker's memory: another worker (or a
        # restarted server) sees it, and a legacy "ok=True" row is ignored.
        c, _ = soc("ann", "analyst")
        soc_auth.audit("old", "verify_audit_chain", detail="ok=True")
        assert c.get("/socperson/api/integrity").get_json()["report"] is None
        soc_auth.audit("bob", "verify_audit_chain", detail=json.dumps({"ok": True, "rows_chained": 7}))
        last = c.get("/socperson/api/integrity").get_json()
        assert last["report"] == {"ok": True, "rows_chained": 7} and last["by"] == "bob"

    def test_verify_reports_its_own_time_and_whether_it_was_saved(self, soc, monkeypatch):
        import blueprints.soc as soc_blueprint
        c, _ = soc("ann", "analyst")
        # The audit write fails: the response says so, and is not built by
        # reading back "the newest row" (an older or concurrent run's result).
        with monkeypatch.context() as m:        # scoped: undo() would also drop the fixtures' setup
            m.setattr(soc_auth, "audit", lambda *a, **k: None)
            m.setattr(soc_blueprint, "_latest_integrity", lambda: pytest.fail("read back the newest row"))
            r = c.post("/socperson/api/integrity/verify").get_json()
            assert r["stored"] is False and r["at"]
        r = c.post("/socperson/api/integrity/verify").get_json()
        assert r["stored"] is True and r["at"] == c.get("/socperson/api/integrity").get_json()["at"]

    def test_long_reports_are_trimmed_to_valid_json(self):
        import blueprints.soc as soc_blueprint
        report = {"ok": False, "rows_chained": 9, "violations": [{"id": i, "reason": "x" * 50} for i in range(500)]}
        stored = json.loads(soc_blueprint._integrity_detail(report))
        assert stored["ok"] is False and stored["violations_total"] == 500 and len(stored["violations"]) < 500

    def test_verification_that_outruns_its_time_budget_says_where_it_runs_instead(self, soc, monkeypatch):
        """No row cap any more (the verifier streams); a press is bounded by time."""
        from utils import audit_chain
        c, _ = soc("ann", "analyst")
        seen = {}

        def too_slow(cur, schema, table, anchors=None, deadline=None):
            seen["deadline"] = deadline                  # the console passes a time budget ...
            raise audit_chain.VerificationTimeout("public.security_events: stopped after 5,000,000 rows")
        monkeypatch.setattr(audit_chain, "verify_table", too_slow)
        r = c.post("/socperson/api/integrity/verify")
        assert seen["deadline"] is not None              # ... and the verifier enforces it (test_audit_integrity.py)
        assert r.status_code == 400 and "verify_audit_chain.py" in r.get_json()["msg"]
        assert "03:45" in r.get_json()["msg"]


class TestLoki:
    def test_not_configured(self, soc, monkeypatch):
        monkeypatch.delenv("LOKI_URL", raising=False)
        c, _ = soc("ann", "analyst")
        assert c.get("/socperson/api/loki/status").get_json()["configured"] is False
        assert c.post("/socperson/api/loki/query", json={"query": '{job="app"}'}).get_json()["configured"] is False

    def test_url_without_scheme_is_not_reported_as_configured(self, soc, monkeypatch):
        # The query path refuses a schemeless URL, so the status must agree --
        # otherwise the Logs view shows as enabled and every search is empty.
        monkeypatch.setenv("LOKI_URL", "loki:3100")
        c, _ = soc("ann", "analyst")
        assert c.get("/socperson/api/loki/status").get_json()["configured"] is False
        from utils import soc_queries
        check = next(x for x in soc_queries.posture()["checks"] if x["id"] == "loki")
        assert check["status"] == "warn"

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
        r = c.post("/socperson/api/loki/query", json=hostile).get_json()
        assert r["configured"] and r["lines"][0]["line"] == "line <b>one</b>" and r["minutes"] == 1440
        assert seen["url"].startswith("http://loki.internal:3100/loki/api/v1/query_range?") and "evil" not in seen["url"]
        assert "limit=500" in seen["url"] and seen["timeout"] == 10
        assert c.post("/socperson/api/loki/query", json={"query": "x" * 501}).status_code == 400
        assert c.post("/socperson/api/loki/query", json={"query": ""}).status_code == 400

    def test_loki_failures_are_reported_not_raised(self, soc, monkeypatch):
        monkeypatch.setenv("LOKI_URL", "http://loki.internal:3100")

        def boom(*a, **k):
            raise OSError("connection refused")
        monkeypatch.setattr("urllib.request.urlopen", boom)
        c, _ = soc("ann", "analyst")
        assert c.post("/socperson/api/loki/query", json={"query": '{a="b"}'}).status_code == 502


class TestLogAnalysisEndpoint:
    def test_rejects_missing_file_and_reports_truncation(self, soc, monkeypatch):
        from utils import soc_loganalysis
        c, _ = soc("ann", "analyst")
        assert c.post("/socperson/api/loganalysis", data={}, content_type="multipart/form-data").status_code == 400
        monkeypatch.setattr(soc_loganalysis, "MAX_BYTES", 2000)
        big = ("\n".join(f'{PUBLIC_IP} - - [16/Oct/2026:10:00:00 +0000] "GET /{i} HTTP/1.1" 200 1 "-" "Mozilla/5.0"' for i in range(200))).encode()
        r = c.post("/socperson/api/loganalysis", data={"log": (io.BytesIO(big), "big.log")}, content_type="multipart/form-data").get_json()
        assert r["report"]["limits"]["truncated_bytes"] is True

    def test_platform_activity_is_analysed_without_an_upload(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        for i in range(12):
            add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier=f"u{i}@example.com", path="/acme/login", method="POST")
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, path="/acme/search")
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, minutes_ago=60 * 30)          # outside 24h
        r = c.post("/socperson/api/loganalysis/platform", json={"hours": 24})
        assert r.status_code == 200, r.get_data(as_text=True)
        rep = r.get_json()["report"]
        # 13, not 15: the analyst's own SOC sign-in events (soc.*) are not platform traffic.
        assert rep["source"] == {"kind": "platform", "events": 13, "total": 13, "capped": False, "hours": 24}
        assert "127.0.0.1" not in {s["ip"] for s in rep["sources"]}
        assert rep["summary"]["records"] == 13 and rep["summary"]["findings"] >= 1
        assert any(s["ip"] == PUBLIC_IP for s in rep["sources"])
        assert ("ann", "log_analysis", "platform-activity-24h") in audit_actions(db_engine)

    def test_platform_activity_keeps_the_newest_events_within_the_size_limit(self, db_engine):
        for i in range(5):
            add_event(db_engine, "auth.failure", ip=PUBLIC_IP, message=f"m{i}", minutes_ago=50 - i)
        one = len(soc_queries.platform_log_text(24)[0].split("\n")[0]) + 1
        text, src = soc_queries.platform_log_text(24, max_bytes=one * 2 + 5)
        assert src["events"] == 2 and src["total"] == 5 and src["capped"] is True
        assert [json.loads(x)["message"] for x in text.split("\n")] == ["m3", "m4"]   # newest, oldest first

    def test_platform_activity_leaves_out_analysts_console_traffic_but_not_probes(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        analyst_ip, prober = "198.51.100.77", "203.0.113.99"
        add_event(db_engine, "soc.login_success", "INFO", ip=analyst_ip, identifier="ann", path="/socperson/login")
        add_event(db_engine, "ratelimit.exceeded", ip=analyst_ip, path="/socperson/api/loganalysis/platform")
        add_event(db_engine, "access.denied", ip=prober, path="/socperson/api/events")
        add_event(db_engine, "auth.failure", ip=analyst_ip, path="/acme/login")     # outside /socperson/: kept
        text, src = soc_queries.platform_log_text(24)
        rows = [json.loads(x) for x in text.split("\n") if x]
        assert {(r["ip"], r["path"]) for r in rows if r.get("ip") in (analyst_ip, prober)} == {
            (prober, "/socperson/api/events"), (analyst_ip, "/acme/login")}
        assert src["total"] == len(rows)

    def test_platform_activity_keeps_the_user_agent_of_long_extras(self, db_engine):
        add_event(db_engine, "waf.xss_blocked", "ERROR", ip=PUBLIC_IP, path="/x",
                  extra={"payload": "A" * 3000, "user_agent": "sqlmap/1.8"})
        rows = [json.loads(x) for x in soc_queries.platform_log_text(24)[0].split("\n") if x]
        assert any(r.get("user_agent") == "sqlmap/1.8" for r in rows)

    def test_platform_analysis_needs_analyst(self, soc):
        v, _ = soc("vic", "viewer")
        assert v.post("/socperson/api/loganalysis/platform", json={}).status_code == 403

    def test_binary_and_non_utf8_input_is_survivable(self, soc):
        c, _ = soc("ann", "analyst")
        r = c.post("/socperson/api/loganalysis", data={"log": (io.BytesIO(bytes(range(256)) * 50), "junk.bin")}, content_type="multipart/form-data")
        assert r.status_code == 200 and r.get_json()["ok"]


# ════════════════════════════════════════════════════════════════════
# genuine activity only (utils/soc_scope.py)
# ════════════════════════════════════════════════════════════════════
TOR_IP = "185.220.101.1"
CONSOLE_IP = "198.51.100.77"          # an analyst's public address (has signed in to the console)
TEST_IP = "151.101.1.10"              # inside SOC_TEST_CIDRS below


def _views(c):
    """Every console view that summarises events, as {name: response json}."""
    return {
        "overview": c.get("/socperson/api/overview").get_json(),
        "events": c.get("/socperson/api/events").get_json(),
        "alerts": c.get("/socperson/api/events", query_string={"alerts": 1}).get_json(),
        "detections": c.get("/socperson/api/detections").get_json(),
        "pulse": c.get("/socperson/api/pulse").get_json(),
        "brief": c.get("/socperson/api/brief", query_string={"kind": "kpi", "value": "events"}).get_json()["brief"],
        "csv": c.get("/socperson/api/events.csv").get_data(as_text=True),
        "platform": c.post("/socperson/api/loganalysis/platform", json={"hours": 24}).get_json()["report"],
    }


def _shown(views, marker):
    """Names of the views where an event whose message contains `marker` shows up."""
    out = set()
    for name in ("events", "alerts"):
        if any(marker in (e["message"] or "") for e in views[name]["events"]):
            out.add(name)
    if any(marker in (e["message"] or "") for e in views["overview"]["recent_alerts"]):
        out.add("overview")
    if any(marker in (e["message"] or "") for e in views["detections"]["events"]):
        out.add("detections")
    if any(marker in (e["message"] or "") for e in views["brief"]["recent"]):
        out.add("brief")
    if marker in views["csv"]:
        out.add("csv")
    return out


class TestGenuineActivityOnly:
    def test_the_servers_own_traffic_is_left_out_of_every_view(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        before = _views(c)
        for ip in ("127.0.0.1", "10.0.0.2", "172.17.0.1", "100.64.3.4", "fd00::7"):
            add_event(db_engine, "waf.sqli_blocked", "CRITICAL", ip=ip, message="server-own " + ip)
            add_event(db_engine, "detection.credential_stuffing", "ERROR", ip=ip, message="server-own det " + ip)
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, message="visitor attack")
        add_event(db_engine, "detection.credential_stuffing", "ERROR", ip=PUBLIC_IP, message="visitor det")
        after = _views(c)
        assert _shown(after, "server-own") == set()
        assert _shown(after, "visitor") == {"events", "alerts", "overview", "detections", "brief", "csv"}
        k0, k1 = before["overview"]["kpi"], after["overview"]["kpi"]
        assert k1["events"] - k0["events"] == 2 and k1["critical"] == k0["critical"] == 0
        assert k1["detections"] - k0["detections"] == 1 and k1["unique_ips"] == 1
        assert sum(p["CRITICAL"] for p in after["overview"]["timeline"]) == 0
        assert after["pulse"]["last_hour"]["CRITICAL"] == 0 and after["pulse"]["threat"]["level"] != "critical"
        assert after["overview"]["threat"]["level"] != "critical"
        assert [t["ip"] for t in after["overview"]["top_ips"]] == [PUBLIC_IP]
        assert after["overview"]["excluded"]["server"] >= 10                     # and the page says so
        assert after["brief"]["stats"]["events"] - before["brief"]["stats"]["events"] == 2
        assert after["platform"]["source"]["events"] - before["platform"]["source"]["events"] == 2

    def test_lookup_of_a_left_out_address_says_why(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "auth.failure", ip="10.0.0.9")
        e = c.get("/socperson/api/entity/ip/10.0.0.9").get_json()["entity"]
        assert e["events"] == 0 and "server's own addresses" in e["excluded"]
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP)
        e = c.get(f"/socperson/api/entity/ip/{PUBLIC_IP}").get_json()["entity"]
        assert e["events"] == 1 and e["excluded"] is None and "geo" not in e

    def test_related_events_leave_out_the_same_things(self, soc, db_engine, monkeypatch):
        monkeypatch.setenv("SOC_TEST_CIDRS", "151.101.0.0/16")
        c, _ = soc()
        eid = add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="victim@example.com", message="seed")
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP, identifier="other@example.com", message="genuine related")
        add_event(db_engine, "auth.failure", ip=TEST_IP, identifier="victim@example.com", message="test related")
        add_event(db_engine, "auth.failure", ip="10.0.0.3", identifier="victim@example.com", message="internal related")
        rel = c.get(f"/socperson/api/events/{eid}").get_json()["event"]["related"]
        msgs = {e["message"] for key in ("same_ip", "same_identifier") for e in rel[key]}
        assert msgs == {"genuine related"}

    def test_the_consoles_own_use_is_left_out_but_attacks_on_it_are_not(self, soc, db_engine):
        c, _ = soc("ann", "analyst")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.soc_audit_log (actor, action, ip) VALUES ('ann', 'login', %s)", (CONSOLE_IP,))
        cur.close()
        soc_queries.clear_cache()
        add_event(db_engine, "soc.action", "INFO", ip=PUBLIC_IP, message="own action")          # always the console's own
        add_event(db_engine, "soc.login_success", "INFO", ip=PUBLIC_IP, message="own login")
        add_event(db_engine, "soc.login_failed", ip=CONSOLE_IP, path="/socperson/login", message="own typo")
        add_event(db_engine, "ratelimit.exceeded", ip=CONSOLE_IP, path="/socperson/api/events", message="own 429")
        add_event(db_engine, "soc.login_failed", ip=TOR_IP, path="/socperson/login", message="stranger guess")
        add_event(db_engine, "soc.ip_denied", ip=TOR_IP, path="/socperson", message="stranger probe")
        add_event(db_engine, "auth.failure", ip=CONSOLE_IP, path="/acme/login", message="analyst on the app")
        msgs = {e["message"] for e in c.get("/socperson/api/events").get_json()["events"]}
        assert {"stranger guess", "stranger probe", "analyst on the app"} <= msgs
        assert not msgs & {"own action", "own login", "own typo", "own 429"}
        assert c.get("/socperson/api/overview").get_json()["excluded"]["console"] >= 4
        # The console's own record of itself is still complete.
        assert ("ann", "login", None) in audit_actions(db_engine)

    def test_listed_testing_is_left_out_everywhere(self, soc, db_engine, monkeypatch):
        monkeypatch.setenv("SOC_TEST_CIDRS", "151.101.0.0/16, not-a-range")
        monkeypatch.setenv("SOC_TEST_ACCOUNTS", "QA.Bot@example.com")
        monkeypatch.setenv("SOC_TEST_COMPANIES", "qa-co")
        c, _ = soc("ann", "analyst")
        cur = db_engine.cursor()
        cur.execute("INSERT INTO att_master.tenants (company_name, subdomain, db_name, status) "
                    "VALUES ('QA Co', 'qa-co', 'qa_co', 'active'), ('Real Co', 'real-co', 'real_co', 'active')")
        cur.close()
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=TEST_IP, message="testing from the office")
        add_event(db_engine, "auth.failure", "ERROR", ip=PUBLIC_IP, identifier="qa.bot@EXAMPLE.com", message="testing account")
        add_event(db_engine, "auth.failure", "ERROR", ip=PUBLIC_IP, path="/qa-co/login", message="testing company")
        add_event(db_engine, "auth.failure", "ERROR", ip=PUBLIC_IP, path="/real-co/login", message="real customer")
        views = _views(c)
        assert _shown(views, "testing") == set()
        assert _shown(views, "real customer") == {"events", "alerts", "overview", "brief", "csv"}
        assert views["overview"]["excluded"]["testing"] == 3
        tenants = {t["slug"]: t for t in c.get("/socperson/api/tenants").get_json()["tenants"]}
        assert "qa-co" not in tenants and tenants["real-co"]["events"] == 1
        e = c.get("/socperson/api/entity/identifier", query_string={"value": "qa.bot@example.com"}).get_json()["entity"]
        assert e["events"] == 0 and "SOC_TEST_ACCOUNTS" in e["excluded"]
        e = c.get(f"/socperson/api/entity/ip/{TEST_IP}").get_json()["entity"]
        assert e["events"] == 0 and "SOC_TEST_CIDRS" in e["excluded"]

    def test_ban_review_leaves_out_the_server_and_testing(self, soc, db_engine, monkeypatch):
        monkeypatch.setenv("SOC_TEST_CIDRS", "151.101.0.0/16")
        c, _ = soc("ann", "analyst")
        cur = db_engine.cursor()
        for ip in (TEST_IP, "10.0.0.4", PUBLIC_IP):
            cur.execute("INSERT INTO att_master.soc_ban_reviews (ip, source, reason) VALUES (%s, 'waf', 'x') "
                        "ON CONFLICT (ip) DO NOTHING", (ip,))
        cur.close()
        d = c.get("/socperson/api/ban-reviews").get_json()
        assert [r["ip"] for r in d["reviews"]] == [PUBLIC_IP] and d["counts"]["pending"] == 1

    def test_nothing_is_looked_up_outside(self, soc, db_engine):
        import importlib.util
        c, _ = soc()
        assert c.get("/socperson/api/geo", query_string={"ips": PUBLIC_IP}).status_code == 404
        assert importlib.util.find_spec("utils.soc_geo") is None
        cur = db_engine.cursor()
        cur.execute("SELECT to_regclass('att_master.soc_ip_intel')")
        assert cur.fetchone()[0] is None                      # the cache of outside answers is gone
        cur.close()
        checks = {x["id"]: x for x in c.get("/socperson/api/posture").get_json()["checks"]}
        assert "geo" not in checks and "outside lookup" in checks["scope"]["detail"]

    def test_overview_warns_when_only_the_proxy_is_visible(self, soc, db_engine):
        c, _ = soc()
        for _ in range(3):
            add_event(db_engine, "auth.failure", ip="10.0.0.2")
        assert c.get("/socperson/api/overview").get_json()["proxy_hint"] is True
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP)
        for _ in range(5):
            add_event(db_engine, "auth.failure", ip=TOR_IP)
        assert c.get("/socperson/api/overview").get_json()["proxy_hint"] is False

    def test_proxy_hint_looks_at_the_busiest_address_not_the_most_severe(self, soc, db_engine):
        # The proxy carries most traffic at WARNING; one scanner has fewer but
        # ERROR events. The proxy's events are left out, but the hint must fire.
        c, _ = soc()
        for _ in range(10):
            add_event(db_engine, "auth.failure", ip="10.0.0.2")
        for _ in range(2):
            add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP)
        d = c.get("/socperson/api/overview").get_json()
        assert [t["ip"] for t in d["top_ips"]] == [PUBLIC_IP] and d["proxy_hint"] is True
        assert d["proxy_ip"] == "10.0.0.2"

    def test_latest_alerts_are_newest_first(self, soc, db_engine):
        c, _ = soc()
        # Inserted out of time order (as a backfill or a delayed writer would).
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, minutes_ago=30, message="older")
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, minutes_ago=2, message="newest")
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP, minutes_ago=90, message="oldest")
        msgs = [a["message"] for a in c.get("/socperson/api/overview").get_json()["recent_alerts"]]
        assert msgs[:3] == ["newest", "older", "oldest"]


# ════════════════════════════════════════════════════════════════════
# analyst brief (Overview drill-down)
# ════════════════════════════════════════════════════════════════════
class TestBrief:
    def test_kpi_brief_matches_its_tile(self, soc, db_engine):
        c, _ = soc()
        for i in range(4):
            add_event(db_engine, "auth.failure", ip=TOR_IP, identifier=f"user{i}@example.com", path="/acme/login")
        add_event(db_engine, "auth.mfa_failure", ip=PUBLIC_IP, identifier="boss@example.com")
        add_event(db_engine, "auth.failure", ip=TOR_IP, minutes_ago=60 * 30)        # previous window
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP)              # not a failed login
        tile = c.get("/socperson/api/overview").get_json()["kpi"]["failed_logins"]
        b = c.get("/socperson/api/brief", query_string={"kind": "kpi", "value": "failed_logins"}).get_json()["brief"]
        assert b["stats"]["events"] == tile == 5 and b["stats"]["previous"] == 1 and b["stats"]["change_pct"] == 400
        assert b["title"] == "Failed logins" and b["what"] and b["why"] and b["check"] and b["respond"]
        assert b["stats"]["unique_ips"] == 2 and b["stats"]["accounts"] == 5
        top = b["sources"][0]
        assert top["ip"] == TOR_IP and top["events"] == 4
        assert not {"countries", "masked"} & set(b) and "geo" not in top          # nothing from outside services
        assert set(b["events_filter"]["event_type"].split(",")) == {"auth.failure", "auth.mfa_failure"}
        assert b["paths"][0] == {"path": "/acme/login", "count": 4}

    def test_event_type_family_technique_and_level(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "waf.xss_blocked", "ERROR", ip=PUBLIC_IP)
        add_event(db_engine, "detection.credential_stuffing", "ERROR", identifier=PUBLIC_IP)

        def get(**q):
            return c.get("/socperson/api/brief", query_string=q).get_json()["brief"]
        b = get(kind="event_type", value="waf.xss_blocked")
        assert b["stats"]["events"] == 1 and "scripting" in b["what"] and b["mitre"][0]["id"] == "T1190"
        assert get(kind="family", value="waf")["stats"]["events"] == 1
        t = get(kind="technique", value="T1190")
        assert t["stats"]["events"] == 1 and t["events_filter"] == {"event_type": "waf.xss_blocked"}
        # No event of this technique in the window: nothing to open. An empty
        # filter ({}) would have opened every event instead.
        none = get(kind="technique", value="T1059.007")
        assert none["stats"]["events"] == 0 and none["events_filter"] is None
        lv = get(kind="level", value="error")
        assert lv["stats"]["events"] == 2 and lv["title"] == "Error events" and "alert" in lv["why"]
        d = get(kind="event_type", value="detection.credential_stuffing")
        assert d["title"] == "Credential stuffing" and "many distinct accounts" in d["what"]

    def test_unknown_selections_are_refused(self, soc):
        c, _ = soc()
        for q in ({"kind": "kpi", "value": "nope"}, {"kind": "family", "value": "nope"}, {"kind": "technique", "value": "T9999"},
                  {"kind": "level", "value": "LOUD"}, {"kind": "sql", "value": "x"}):
            assert c.get("/socperson/api/brief", query_string=q).status_code == 400, q

    def test_live_list_counts_only_events_newer_than_the_newest_shown(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "waf.sqli_blocked", "ERROR", ip=PUBLIC_IP)
        top = c.get("/socperson/api/events", query_string={"alerts": "1"}).get_json()["events"][0]["id"]
        assert c.get("/socperson/api/events", query_string={"alerts": "1", "since": top}).get_json()["events"] == []
        add_event(db_engine, "waf.xss_blocked", "ERROR", ip=PUBLIC_IP)
        add_event(db_engine, "auth.failure", "WARNING", ip=PUBLIC_IP)              # not an alert
        new = c.get("/socperson/api/events", query_string={"alerts": "1", "since": top}).get_json()["events"]
        assert [e["event_type"] for e in new] == ["waf.xss_blocked"]

    def test_events_filter_accepts_the_briefs_type_list(self, soc, db_engine):
        c, _ = soc()
        add_event(db_engine, "auth.failure", ip=PUBLIC_IP)
        add_event(db_engine, "auth.mfa_failure", ip=PUBLIC_IP)
        add_event(db_engine, "access.denied", ip=PUBLIC_IP)
        ev = c.get("/socperson/api/events", query_string={"event_type": "auth.failure,auth.mfa_failure"}).get_json()["events"]
        assert sorted(e["event_type"] for e in ev) == ["auth.failure", "auth.mfa_failure"]
