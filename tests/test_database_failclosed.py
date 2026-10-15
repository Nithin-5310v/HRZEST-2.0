"""The database layer must fail closed outside development instead of
silently switching to a per-process SQLite file (database.py).

Regression coverage for the audit-time observation that one failed connect
(connect_timeout defaults to 1 s) left a worker writing HR/attendance/payroll
data to an ephemeral local file for the rest of its life.
"""
import psycopg2
import pytest

import database


@pytest.fixture
def pool_unreachable(monkeypatch):
    saved = database._pool
    monkeypatch.setattr(
        database.psycopg2.pool, "ThreadedConnectionPool",
        lambda *a, **k: (_ for _ in ()).throw(psycopg2.OperationalError("connection refused")))
    yield
    database._pool = saved


class TestFallbackPolicy:
    def test_allowed_in_development(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.delenv("ALLOW_SQLITE_FALLBACK", raising=False)
        assert database._sqlite_fallback_allowed() is True

    def test_refused_by_default_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("ALLOW_SQLITE_FALLBACK", raising=False)
        assert database._sqlite_fallback_allowed() is False

    @pytest.mark.parametrize("val,expected", [("true", True), ("1", True), ("no", False), ("", False)])
    def test_explicit_opt_in(self, monkeypatch, val, expected):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ALLOW_SQLITE_FALLBACK", val)
        assert database._sqlite_fallback_allowed() is expected


class TestCreatePool:
    def test_production_raises_and_leaves_pool_unset_so_the_next_call_retries(self, monkeypatch, pool_unreachable):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("ALLOW_SQLITE_FALLBACK", raising=False)
        with pytest.raises(RuntimeError):
            database._create_pool(retries=1, delay=0)
        assert database._pool is None

    def test_development_still_falls_back_for_local_demos(self, monkeypatch, pool_unreachable):
        monkeypatch.setenv("APP_ENV", "development")
        database._create_pool(retries=1, delay=0)
        assert isinstance(database._pool, database._SqlitePool)
