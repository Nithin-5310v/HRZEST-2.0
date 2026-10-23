"""The opt-in `SET search_path` skip (database.py, DB_SKIP_REDUNDANT_SET_SEARCH_PATH).

A wrong skip would mean serving one tenant's request from another tenant's
schema, so these tests are deliberately adversarial: interleaved schemas on
the same pooled connections, many threads, dirty returns, closed connections,
the forced re-validation, and the default-off behaviour.
"""
import threading

import psycopg2
import pytest

import database

SCHEMAS = ("trk_a", "trk_b")


@pytest.fixture
def two_tenants(db_engine):
    cur = db_engine.cursor()
    for s in SCHEMAS:
        cur.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{s}"')
        cur.execute(f'CREATE TABLE "{s}".marker (v text)')
        cur.execute(f'INSERT INTO "{s}".marker VALUES (%s)', (s,))
        cur.execute(f'GRANT USAGE ON SCHEMA "{s}" TO PUBLIC')
        cur.execute(f'GRANT SELECT ON "{s}".marker TO PUBLIC')
    yield SCHEMAS
    for s in SCHEMAS:
        cur.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')


@pytest.fixture
def tracking_on(monkeypatch):
    monkeypatch.setattr(database, "_SKIP_REDUNDANT_SP", True)
    monkeypatch.setattr(database, "_SP_REVALIDATE_EVERY", 200)


def _marker(db):
    cur = db.cursor()
    cur.execute("SELECT v FROM marker")
    v = cur.fetchone()[0]
    cur.close()
    return v


def _raw(db):
    return getattr(db, "_conn")


class TestPoolUsesTrackedConnections:
    def test_connection_factory(self):
        pool, conn = database._borrow()
        try:
            assert isinstance(conn, database._TrackedConnection)
        finally:
            pool.putconn(conn)


class TestDefaultIsOff:
    def test_every_borrow_still_sends_set(self, two_tenants, monkeypatch):
        monkeypatch.setattr(database, "_SKIP_REDUNDANT_SP", False)
        sent = []
        real = database._TrackedConnection.cursor

        class _Spy:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, sql, *a):
                sent.append(sql)
                return self._cur.execute(sql, *a)

            def __getattr__(self, n):
                return getattr(self._cur, n)

        monkeypatch.setattr(database._TrackedConnection, "cursor", lambda self, *a, **k: _Spy(real(self, *a, **k)))
        for _ in range(4):
            db = database.get_tenant_db("trk_a")
            db.close()
        assert len([s for s in sent if "search_path" in s]) == 4


class TestTrackingOn:
    def test_repeat_borrow_of_same_schema_skips_the_set(self, two_tenants, tracking_on):
        # Leave the next pooled connection on the other schema first: under
        # random test order an earlier test can return it still on trk_a,
        # which would make the first borrow below a reuse already.
        database.get_tenant_db("trk_b").close()
        db = database.get_tenant_db("trk_a")
        raw = _raw(db)
        assert raw._hrz_sp == "trk_a" and raw._hrz_sp_reuse == 0
        db.close()
        db2 = database.get_tenant_db("trk_a")
        assert _raw(db2) is raw
        assert raw._hrz_sp_reuse == 1                 # SET was skipped
        assert _marker(db2) == "trk_a"
        db2.close()

    def test_switching_schema_always_sets(self, two_tenants, tracking_on):
        db = database.get_tenant_db("trk_a")
        raw = _raw(db)
        db.close()
        db2 = database.get_tenant_db("trk_b")
        assert _raw(db2) is raw
        assert raw._hrz_sp == "trk_b" and raw._hrz_sp_reuse == 0
        assert _marker(db2) == "trk_b"
        db2.close()

    def test_interleaved_schemas_never_cross(self, two_tenants, tracking_on):
        order = ["trk_a", "trk_a", "trk_b", "trk_a", "trk_b", "trk_b", "trk_a", "trk_a", "trk_b"] * 20
        for schema in order:
            db = database.get_tenant_db(schema)
            try:
                assert _marker(db) == schema
            finally:
                db.close()

    def test_default_borrow_goes_back_to_public_after_a_tenant(self, two_tenants, tracking_on):
        db = database.get_tenant_db("trk_a")
        db.close()
        pub = database.get_db_connection()         # no tenant context -> "public"
        cur = pub.cursor()
        cur.execute("SELECT current_schema()")
        assert cur.fetchone()[0] == "public"
        cur.close()
        pub.close()

    def test_many_threads_never_see_the_wrong_schema(self, two_tenants, tracking_on):
        errors = []

        def worker(idx):
            try:
                for i in range(60):
                    schema = SCHEMAS[(idx + i) % 2]
                    db = database.get_tenant_db(schema)
                    try:
                        got = _marker(db)
                        if got != schema:
                            errors.append((schema, got))
                    finally:
                        db.close()
            except Exception as exc:   # noqa: BLE001
                errors.append(repr(exc))

        ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errors, errors[:5]

    def test_forced_revalidation_after_n_reuses(self, two_tenants, tracking_on, monkeypatch):
        monkeypatch.setattr(database, "_SP_REVALIDATE_EVERY", 3)
        db = database.get_tenant_db("trk_a")
        raw = _raw(db)
        db.close()
        seen = []
        for _ in range(8):
            d = database.get_tenant_db("trk_a")
            seen.append(raw._hrz_sp_reuse)
            d.close()
        assert max(seen) <= 3 and 0 in seen[1:], seen      # counter resets: a real SET was sent again

    def test_connection_returned_mid_transaction_is_forgotten(self, two_tenants, tracking_on):
        db = database.get_tenant_db("trk_a")
        raw = _raw(db)
        raw.autocommit = False
        cur = raw.cursor()
        cur.execute("SELECT 1")                    # opens a transaction
        cur.close()
        db.close()                                 # returned dirty
        assert raw._hrz_sp is None
        raw.autocommit = True

    def test_a_closed_connection_is_never_reused_as_known(self, tracking_on):
        pool, conn = database._borrow()
        database._set_search_path(conn, "public")
        assert conn._hrz_sp == "public"
        conn.close()
        assert conn.closed
        # a skip decision must refuse a closed connection and fall through to a real SET (which then errors)
        with pytest.raises(psycopg2.Error):
            database._set_search_path(conn, "public")
        pool.putconn(conn, close=True)

    def test_invalid_names_are_still_rejected_and_state_is_not_falsified(self, tracking_on):
        pool, conn = database._borrow()
        try:
            conn._hrz_sp = None
            for bad in ("", "a b", "x;y", "a-b", 'q"', 'bad"; DROP SCHEMA public;--'):
                with pytest.raises(ValueError):
                    database._set_search_path(conn, bad)
            assert conn._hrz_sp is None
        finally:
            pool.putconn(conn)
