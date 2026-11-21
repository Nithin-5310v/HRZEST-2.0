"""
Coverage tests for database.py.
Targets uncovered lines: schema validation, master/tenant connections,
tenant schema creation.
"""
import pytest


class TestSetSearchPath:

    def test_invalid_schema_name_raises_value_error(self):
        from database import _set_search_path
        from database import _borrow_connection
        conn = _borrow_connection()
        try:
            with pytest.raises(ValueError, match="Invalid tenant schema name"):
                _set_search_path(conn, "bad schema!")  # spaces/special chars → ValueError
        finally:
            conn.autocommit = True
            from database import _pool
            _pool.putconn(conn)

    def test_valid_schema_name_accepted(self):
        from database import _set_search_path
        from database import _borrow_connection
        conn = _borrow_connection()
        try:
            _set_search_path(conn, "public")  # should not raise
        finally:
            from database import _pool
            _pool.putconn(conn)


class TestGetMasterDb:

    def test_get_master_db_returns_connection(self):
        """Lines 163-165: get_master_db() borrows and wraps a connection."""
        from database import get_master_db
        conn = get_master_db()
        assert conn is not None
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        conn.close()


class TestGetTenantDb:

    def test_get_tenant_db_returns_connection(self):
        """Lines 176-178: get_tenant_db() switches to named schema."""
        from database import get_tenant_db
        conn = get_tenant_db("public")
        assert conn is not None
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        conn.close()

    def test_get_tenant_db_invalid_schema_raises(self):
        from database import get_tenant_db
        with pytest.raises(ValueError, match="Invalid tenant schema"):
            get_tenant_db("bad schema!")


class TestCreateTenantSchema:

    def test_create_tenant_schema_creates_schema(self, db_engine):
        """Lines 191-204: create_tenant_schema() creates a Postgres schema."""
        from database import create_tenant_schema
        schema = "ci_test_schema_xyz"
        try:
            create_tenant_schema(schema)
            # Verify schema was created
            cur = db_engine.cursor()
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name=%s",
                (schema,)
            )
            row = cur.fetchone()
            cur.close()
            assert row is not None
        finally:
            cur = db_engine.cursor()
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}"')
            cur.close()

    def test_create_tenant_schema_invalid_name_raises(self):
        from database import create_tenant_schema
        with pytest.raises(ValueError):
            create_tenant_schema("bad schema!")


class TestPooledConnection:

    def test_getattr_proxies_to_underlying_connection(self):
        """Line 70: __getattr__ on _PooledConnection proxies to the real conn."""
        from database import get_db_connection
        conn = get_db_connection()
        # 'autocommit' is an attribute on the underlying psycopg2 connection
        _ = conn.autocommit  # should not raise AttributeError
        conn.close()

    def test_cursor_strips_buffered_kwarg(self):
        """Lines 62-64: cursor(buffered=True) works without error."""
        from database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor(buffered=True, dictionary=False)
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
        cur.close()
        conn.close()

    def test_close_returns_connection_to_pool(self):
        """Line 67: close() calls putconn, not actual close."""
        from database import get_db_connection
        conn = get_db_connection()
        conn.close()
        # Pool should be non-empty — borrow again immediately
        conn2 = get_db_connection()
        assert conn2 is not None
        conn2.close()
