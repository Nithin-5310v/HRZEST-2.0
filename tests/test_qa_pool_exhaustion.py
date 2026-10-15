"""SCALE-09: a busy pool is not a broken pool.

When every pooled connection was in use (a load spike), psycopg2 raises
PoolError("connection pool exhausted"), and _borrow_connection() treated
that like a dead database: it built a whole new pool next to the old one
(which kept its connections). Repeated spikes multiplied the connection
count until Postgres refused new clients. Now a borrower waits briefly
for a connection to come back, then gets a 503 -- and only a genuinely
broken pool is rebuilt.
"""
import psycopg2
import psycopg2.pool
import pytest

import database


class _Conn:
    autocommit = False


class _BusyPool:
    """Exhausted for the first `busy_calls` borrows, then hands out connections."""
    closed = False

    def __init__(self, busy_calls):
        self.busy_calls = busy_calls
        self.calls = 0

    def getconn(self):
        self.calls += 1
        if self.calls <= self.busy_calls:
            raise psycopg2.pool.PoolError("connection pool exhausted")
        return _Conn()


@pytest.fixture
def no_rebuild(monkeypatch):
    rebuilds = []
    monkeypatch.setattr(database, "_create_pool", lambda *a, **k: rebuilds.append(1))
    monkeypatch.setattr(database, "_POOL_WAIT_SECONDS", 0.3)
    return rebuilds


def test_busy_pool_waits_for_a_free_connection_instead_of_rebuilding(monkeypatch, no_rebuild):
    pool = _BusyPool(busy_calls=2)
    monkeypatch.setattr(database, "_pool", pool)
    conn = database._borrow_connection()
    assert isinstance(conn, _Conn) and conn.autocommit is True
    assert no_rebuild == [] and database._pool is pool


def test_pool_that_stays_busy_gives_503_not_a_second_pool(monkeypatch, no_rebuild):
    pool = _BusyPool(busy_calls=10 ** 6)
    monkeypatch.setattr(database, "_pool", pool)
    with pytest.raises(database.DatabaseUnavailable):
        database._borrow_connection()
    assert no_rebuild == [] and database._pool is pool


def test_broken_pool_is_still_rebuilt(monkeypatch):
    class _Broken:
        closed = False

        def getconn(self):
            raise psycopg2.OperationalError("server closed the connection unexpectedly")

    healthy = _BusyPool(busy_calls=0)

    def _rebuild(*a, **k):
        monkeypatch.setattr(database, "_pool", healthy)

    monkeypatch.setattr(database, "_pool", _Broken())
    monkeypatch.setattr(database, "_create_pool", _rebuild)
    assert isinstance(database._borrow_connection(), _Conn)
    assert database._pool is healthy


def test_closed_pool_is_rebuilt_not_waited_on(monkeypatch):
    class _Closed:
        closed = True

        def getconn(self):
            raise psycopg2.pool.PoolError("connection pool is closed")

    healthy = _BusyPool(busy_calls=0)
    monkeypatch.setattr(database, "_pool", _Closed())
    monkeypatch.setattr(database, "_create_pool", lambda *a, **k: monkeypatch.setattr(database, "_pool", healthy))
    assert isinstance(database._borrow_connection(), _Conn)
    assert database._pool is healthy
