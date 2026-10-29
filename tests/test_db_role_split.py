"""Least-privilege database roles (database.owner_privileges /
grant_runtime_privileges / scripts/provision_db_roles.py).

These only mean something when the suite is run with the split configured --
DB_USER = restricted runtime role, DB_OWNER_USER = owner role (see
docs/SECURITY_HARDENING.md, "Database roles") -- so they skip otherwise. CI runs
them in a dedicated job.
"""
import os

import psycopg2
import pytest

import database

requires_split = pytest.mark.skipif(
    not database.owner_role_configured(),
    reason="DB_OWNER_USER split not configured",
)


@pytest.fixture
def runtime():
    """A raw connection as the restricted runtime role (what request
    handlers use), autocommit like the app's pool."""
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ["DB_USER"], password=os.environ.get("DB_PASS", ""), dbname=os.environ["DB_NAME"])
    conn.autocommit = True
    yield conn.cursor()
    conn.close()


def _denied(cur, sql, *params):
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(sql, params)


@requires_split
class TestRuntimeRoleIsNotAnOwner:
    def test_not_superuser_and_cannot_create_roles(self, runtime):
        runtime.execute("SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname=current_user")
        assert runtime.fetchone() == (False, False, False)

    def test_cannot_run_ddl(self, runtime):
        _denied(runtime, "CREATE TABLE should_not_exist (id int)")
        _denied(runtime, "ALTER TABLE employees ADD COLUMN sneaky text")
        _denied(runtime, "DROP TABLE employees")
        _denied(runtime, "CREATE SCHEMA sneaky")

    def test_cannot_drop_the_audit_triggers(self, runtime):
        _denied(runtime, "DROP TRIGGER trg_audit_logs_immutable ON audit_logs")
        _denied(runtime, "ALTER TABLE audit_logs DISABLE TRIGGER ALL")

    def test_normal_dml_works(self, runtime):
        runtime.execute("SELECT COUNT(*) FROM employees")
        runtime.fetchone()
        runtime.execute("INSERT INTO login_attempts (identifier, attempt_type, failed_count) "
                        "VALUES ('rolesplit','admin',1) ON CONFLICT DO NOTHING")


@requires_split
class TestAuditTablesAreAppendOnlyForRuntime:
    def test_can_insert_and_the_chain_still_works(self, runtime):
        runtime.execute("INSERT INTO audit_logs (actor, action) VALUES ('rolesplit','probe') RETURNING row_hash")
        row_hash = runtime.fetchone()[0]
        assert row_hash and len(row_hash) == 64, \
            "SECURITY DEFINER chain trigger must work for a role with no access to the head table"
        runtime.execute("INSERT INTO security_events (event_type, level, message) "
                        "VALUES ('rolesplit.probe','INFO','x') RETURNING row_hash")
        assert len(runtime.fetchone()[0]) == 64

    def test_no_update_delete_truncate_even_with_the_bypass_guc(self, runtime):
        runtime.execute("SET audit.bypass = 'on'")
        for sql in ("UPDATE audit_logs SET detail='x'", "DELETE FROM audit_logs",
                    "TRUNCATE audit_logs", "UPDATE security_events SET message='x'",
                    "DELETE FROM security_events"):
            _denied(runtime, sql)

    def test_cannot_touch_the_chain_head(self, runtime):
        _denied(runtime, "SELECT * FROM audit_chain_head")
        _denied(runtime, "UPDATE audit_chain_head SET head = repeat('0', 64)")
        _denied(runtime, "DELETE FROM audit_chain_head")

    def test_soc_audit_trail_is_append_only_by_grant(self, runtime):
        """Not only by its trigger: the runtime role holds no UPDATE/DELETE/TRUNCATE at all."""
        runtime.execute("INSERT INTO att_master.soc_audit_log (actor, action) VALUES ('rolesplit','probe')")
        for sql in ("UPDATE att_master.soc_audit_log SET detail='x'", "DELETE FROM att_master.soc_audit_log",
                    "TRUNCATE att_master.soc_audit_log"):
            _denied(runtime, sql)


@requires_split
class TestOwnerContext:
    def test_owner_connections_can_do_ddl_and_runtime_ones_cannot(self):
        with database.owner_privileges():
            conn = database.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT current_user")
            assert cur.fetchone()[0] == os.environ["DB_OWNER_USER"]
            cur.execute("CREATE TABLE _owner_probe (id int)")
            cur.execute("DROP TABLE _owner_probe")
            cur.close()
        finally:
            conn.close()
        rt = database.get_db_connection()
        try:
            cur = rt.cursor()
            cur.execute("SELECT current_user")
            assert cur.fetchone()[0] == os.environ["DB_USER"]
        finally:
            rt.close()

    def test_context_is_reentrant_and_thread_local(self):
        import threading
        seen = {}

        def other():
            seen["in_context"] = database._in_owner_context()

        with database.owner_privileges():
            with database.owner_privileges():
                assert database._in_owner_context()
            assert database._in_owner_context()
            t = threading.Thread(target=other)
            t.start()
            t.join()
        assert not database._in_owner_context()
        assert seen["in_context"] is False


def test_grant_statements_are_pure_and_reject_unsafe_identifiers():
    ok = database.runtime_grant_statements("tenant_acme", "hrzest_app")
    assert any("GRANT USAGE ON SCHEMA \"tenant_acme\"" in s for s in ok)
    assert any("REVOKE UPDATE, DELETE, TRUNCATE" in s and "audit_logs" in s for s in ok)
    assert any("REVOKE UPDATE, DELETE, TRUNCATE" in s and "soc_audit_log" in s for s in ok)
    assert any("audit_chain_head" in s and "REVOKE ALL" in s for s in ok)
    assert database.runtime_grant_statements('x"; DROP SCHEMA public;--', "hrzest_app") == []
    assert database.runtime_grant_statements("public", "bad role") == []
