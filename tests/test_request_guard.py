"""Per-request batching of the authenticated-session guard (utils/request_guard.py,
utils/auth.py::_prime_guard_state) and the throttled token purge.

The optimisation answers three security questions -- kill-switch flag, account
still active, password changed since login -- with ONE statement instead of
three. These tests pin both halves of that bargain: it really is one
statement, and every one of the three checks still rejects exactly as before.
"""
import time

import pytest

import database
from utils import auth as auth_module
from utils import request_guard


@pytest.fixture
def sql_log(monkeypatch):
    """Records every statement executed through the app's pooled cursors."""
    seen = []
    real = database._PooledConnection.cursor

    class _Rec:
        def __init__(self, cur):
            self._cur = cur

        def execute(self, sql, *a, **k):
            seen.append(" ".join(str(sql).split()))
            return self._cur.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self._cur, name)

    monkeypatch.setattr(database._PooledConnection, "cursor", lambda self, *a, **k: _Rec(real(self, *a, **k)))
    return seen


def _admin_login(client, seed_admin):
    client.post("/login", data={"identifier": seed_admin["username"], "password": seed_admin["password"]})


def _guard_statements(seen):
    return [s for s in seen if "session_risk" in s and "FROM session_risk" in s]


class TestBatchedAdminGuard:
    def test_one_statement_answers_all_three_checks(self, client, seed_admin, sql_log):
        _admin_login(client, seed_admin)
        del sql_log[:]
        r = client.get("/api/admin/attendance/flagged")
        assert r.status_code == 200
        guards = _guard_statements(sql_log)
        assert len(guards) == 1, guards
        assert "admin_users" in guards[0] and "is_active" in guards[0] and "password_changed_at" in guards[0]
        # ...and none of the three separate reads it replaces is issued.
        assert not [s for s in sql_log if s.startswith("SELECT status FROM session_risk")]
        assert not [s for s in sql_log if s.startswith("SELECT COALESCE(is_active,1) FROM admin_users")]
        assert not [s for s in sql_log if s.startswith("SELECT password_changed_at FROM admin_users")]

    def test_a_valid_session_still_passes(self, client, seed_admin):
        _admin_login(client, seed_admin)
        assert client.get("/api/admin/attendance/flagged").status_code == 200

    def test_deactivation_takes_effect_on_the_very_next_request(self, client, seed_admin, db_engine):
        _admin_login(client, seed_admin)
        assert client.get("/api/admin/attendance/flagged").status_code == 200
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET is_active=0 WHERE username=%s", (seed_admin["username"],))
        r = client.get("/api/admin/attendance/flagged")
        assert r.status_code == 302            # session killed, bounced to login
        assert client.get("/api/admin/attendance/flagged").status_code in (302, 401)

    def test_compromised_session_is_rejected(self, client, seed_admin, db_engine):
        _admin_login(client, seed_admin)
        with client.session_transaction() as sess:
            sid = sess["_sid"]
        cur = db_engine.cursor()
        cur.execute("INSERT INTO session_risk (sid, identifier, attempt_type, score, status) "
                    "VALUES (%s,%s,'admin',100,'compromised')", (sid, seed_admin["username"]))
        assert client.get("/api/admin/attendance/flagged").status_code == 302

    def test_password_change_invalidates_older_sessions(self, client, seed_admin, db_engine):
        _admin_login(client, seed_admin)
        assert client.get("/api/admin/attendance/flagged").status_code == 200
        cur = db_engine.cursor()
        cur.execute("UPDATE admin_users SET password_changed_at = NOW() + INTERVAL '5 seconds' WHERE username=%s",
                    (seed_admin["username"],))
        assert client.get("/api/admin/attendance/flagged").status_code == 302

    def test_deleted_account_is_rejected(self, client, seed_admin, db_engine):
        _admin_login(client, seed_admin)
        cur = db_engine.cursor()
        cur.execute("DELETE FROM admin_users WHERE username=%s", (seed_admin["username"],))
        assert client.get("/api/admin/attendance/flagged").status_code == 302


class TestFallbackWhenPasswordStampIsForAnotherTable:
    """An HR employee routed into the admin panel stamps `employees` while the
    account guarded is `admin_users`; that one read must stay separate."""

    def test_still_enforced(self, client, seed_admin, seed_employee, db_engine, sql_log):
        _admin_login(client, seed_admin)
        with client.session_transaction() as sess:
            sess["_pwv"] = ["employees", seed_employee["employee_id"], ""]
        del sql_log[:]
        assert client.get("/api/admin/attendance/flagged").status_code == 200
        assert [s for s in sql_log if s.startswith("SELECT password_changed_at FROM employees")], \
            "the cross-table password read must not be folded into the admin_users statement"
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET password_changed_at = NOW() + INTERVAL '5 seconds' WHERE employee_id=%s",
                    (seed_employee["employee_id"],))
        assert client.get("/api/admin/attendance/flagged").status_code == 302


class TestCacheIsPerRequestAndFailsSafe:
    def test_cache_is_cleared_between_requests(self, client, seed_admin):
        _admin_login(client, seed_admin)
        client.get("/api/admin/attendance/flagged")
        with client.application.test_request_context("/"):
            assert request_guard.cached_risk_status("anything") is None
            assert request_guard.cached_account_active("admin_users", "username", "x") is None
            assert request_guard.cached_password_version("admin_users", "x") is None

    def test_priming_failure_falls_back_to_the_original_checks(self, client, seed_admin, monkeypatch):
        _admin_login(client, seed_admin)

        def boom(*a, **k):
            raise RuntimeError("prime query failed")

        monkeypatch.setattr(auth_module, "_db", boom)
        # With the helper DB context broken the guard cannot prime; the original
        # per-check functions take over (each failing open exactly as before),
        # so the request must not crash.
        assert client.get("/api/admin/attendance/flagged").status_code == 200

    def test_unknown_table_or_column_is_never_interpolated(self, client):
        with client.application.test_request_context("/"):
            auth_module._prime_guard_state("employees; DROP TABLE x", "employee_id", "E1")
            auth_module._prime_guard_state("admin_users", "password", "E1")
            auth_module._prime_guard_state("admin_users", "username", None)
            assert request_guard.cached_account_active("admin_users", "username", "E1") is None

    def test_patching_the_public_names_still_overrides(self, client, seed_admin, monkeypatch):
        """Existing suites rely on monkeypatching these names; the cache must
        not make that a no-op."""
        _admin_login(client, seed_admin)
        monkeypatch.setattr(auth_module, "is_session_compromised", lambda sid: True)
        assert client.get("/api/admin/attendance/flagged").status_code == 302


class TestEmployeeSessionGuard:
    def test_employee_route_uses_one_guard_statement(self, client, seed_employee, sql_log):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
            sess["employee_name"] = seed_employee["name"]
        del sql_log[:]
        client.get("/employee_portal")
        guards = _guard_statements(sql_log)
        assert len(guards) == 1, guards
        assert "employees" in guards[0]

    def test_deactivated_employee_is_rejected(self, client, seed_employee, db_engine):
        with client.session_transaction() as sess:
            sess["employee_id"] = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute("UPDATE employees SET is_active=0 WHERE employee_id=%s", (seed_employee["employee_id"],))
        r = client.get("/employee_portal")
        assert r.status_code == 302 and "login" in r.headers["Location"]


class TestTokenPurgeThrottle:
    class _Cur:
        def __init__(self):
            self.executed = []

        def execute(self, sql, *a):
            self.executed.append(sql)

    class _Conn:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        auth_module._last_token_purge.clear()
        yield
        auth_module._last_token_purge.clear()

    def test_runs_once_per_interval(self, client, monkeypatch):
        monkeypatch.setattr(auth_module, "_TOKEN_PURGE_INTERVAL", 60.0)
        cur, conn = self._Cur(), self._Conn()
        with client.application.test_request_context("/"):
            assert auth_module._purge_expired_tokens(cur, conn) is True
            assert auth_module._purge_expired_tokens(cur, conn) is False
            assert auth_module._purge_expired_tokens(cur, conn) is False
        assert len(cur.executed) == 1 and "DELETE FROM api_tokens" in cur.executed[0] and conn.commits == 1

    def test_interval_zero_restores_every_request_behaviour(self, client, monkeypatch):
        monkeypatch.setattr(auth_module, "_TOKEN_PURGE_INTERVAL", 0.0)
        cur, conn = self._Cur(), self._Conn()
        with client.application.test_request_context("/"):
            for _ in range(3):
                assert auth_module._purge_expired_tokens(cur, conn) is True
        assert len(cur.executed) == 3

    def test_each_tenant_schema_is_throttled_independently(self, client, monkeypatch):
        from flask import g
        monkeypatch.setattr(auth_module, "_TOKEN_PURGE_INTERVAL", 60.0)
        cur, conn = self._Cur(), self._Conn()
        with client.application.test_request_context("/"):
            g.tenant_db = "tenant_a"
            assert auth_module._purge_expired_tokens(cur, conn) is True
            g.tenant_db = "tenant_b"
            assert auth_module._purge_expired_tokens(cur, conn) is True
            g.tenant_db = "tenant_a"
            assert auth_module._purge_expired_tokens(cur, conn) is False

    def test_expired_token_never_authenticates_even_if_not_yet_purged(self, client, seed_employee, db_engine, monkeypatch):
        """The purge is housekeeping; the lookup's own `expires_at > NOW()` is the control."""
        monkeypatch.setattr(auth_module, "_TOKEN_PURGE_INTERVAL", 10_000.0)
        tok = client.post("/api/employee/login", json={
            "employee_id": seed_employee["employee_id"], "password": seed_employee["password"]}).get_json()["token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        assert client.post("/api/employee/privacy/erasure_request", json={}, headers=hdr).status_code == 200
        cur = db_engine.cursor()
        cur.execute("UPDATE api_tokens SET expires_at = NOW() - INTERVAL '1 hour' WHERE identity=%s",
                    (seed_employee["employee_id"],))
        # purge throttled -> the stale row is still physically present...
        auth_module._last_token_purge["public"] = time.monotonic()
        r = client.post("/api/employee/privacy/erasure_request", json={}, headers=hdr)
        assert r.status_code == 401


class TestCacheLifetimeRegressions:
    """Two flaws the existing suites caught in the first version of the cache."""

    def test_stamping_a_new_password_version_never_reads_the_cached_old_one(self, client, seed_admin, db_engine):
        """After a user changes their OWN password the session is re-stamped with
        the new version. If that read were served from the request-start cache it
        would stamp the OLD version and log the user out of the session that just
        changed the password."""
        with client.application.test_request_context("/"):
            from flask import session
            session["_sid"] = "regression-sid"
            session["_pwv"] = ["admin_users", seed_admin["username"], ""]   # login stamps this
            auth_module._prime_guard_state("admin_users", "username", seed_admin["username"])
            old = auth_module._read_password_version("admin_users", seed_admin["username"], use_cache=True)
            cur = db_engine.cursor()
            cur.execute("UPDATE admin_users SET password_changed_at = NOW() + INTERVAL '2 seconds' WHERE username=%s",
                        (seed_admin["username"],))
            fresh = auth_module._read_password_version("admin_users", seed_admin["username"])
            still_cached = auth_module._read_password_version("admin_users", seed_admin["username"], use_cache=True)
        assert fresh != old and fresh != ""
        assert still_cached == old          # the per-request check keeps its start-of-request view

    def test_cache_is_dropped_when_the_request_ends_even_if_the_context_is_preserved(self, client, seed_admin):
        """The shared `client` fixture keeps the last request's context alive, which
        is exactly the situation that let a stale answer leak in the first version."""
        _admin_login(client, seed_admin)
        assert client.get("/api/admin/attendance/flagged").status_code == 200
        with client.session_transaction() as sess:
            sid = sess.get("_sid")
        assert sid, "login should have stamped a session id"
        assert request_guard.cached_risk_status(sid) is None
        assert request_guard.cached_account_active("admin_users", "username", seed_admin["username"]) is None

    def test_state_bound_to_another_request_object_is_ignored(self, client):
        with client.application.test_request_context("/"):
            request_guard.store({"sid": "s", "risk": "compromised", "acct": ("employees", "employee_id", "E"),
                                 "exists": True, "active": 0, "pwv_key": ("employees", "E"), "pwv": "x"})
            assert request_guard.cached_risk_status("s") == "compromised"
        with client.application.test_request_context("/other"):
            assert request_guard.cached_risk_status("s") is None
            assert request_guard.cached_account_active("employees", "employee_id", "E") is None

    def test_state_expires_after_a_few_seconds(self, client, monkeypatch):
        with client.application.test_request_context("/"):
            request_guard.store({"sid": "s", "risk": "active", "acct": ("employees", "employee_id", "E"),
                                 "exists": True, "active": 1})
            assert request_guard.cached_risk_status("s") == "active"
            real = request_guard.time.monotonic
            monkeypatch.setattr(request_guard.time, "monotonic", lambda: real() + request_guard._MAX_AGE_SECONDS + 1)
            assert request_guard.cached_risk_status("s") is None
