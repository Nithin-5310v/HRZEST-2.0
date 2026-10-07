"""BUG-02: if PostgreSQL can't be reached, the app must fail loudly (503)
instead of silently switching the worker to a local SQLite file -- except
in APP_ENV=development, where the SQLite fallback stays available for
local demos."""
import psycopg2
import pytest

import database


def _unreachable(*args, **kwargs):
    raise psycopg2.OperationalError("could not connect to server: Connection refused")


@pytest.fixture
def postgres_down(monkeypatch):
    monkeypatch.setattr(database.psycopg2.pool, "ThreadedConnectionPool", _unreachable)
    monkeypatch.setattr(database, "_pool", None)  # restored after the test


def test_no_sqlite_fallback_outside_development(monkeypatch, postgres_down):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(database.DatabaseUnavailable):
        database._create_pool(retries=2, delay=0)
    assert not isinstance(database._pool, database._SqlitePool)
    assert database._pool is None  # next request retries Postgres


@pytest.mark.parametrize("app_env", ["staging", "", "Production"])
def test_unknown_or_unset_env_also_fails_closed(monkeypatch, postgres_down, app_env):
    monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(database.DatabaseUnavailable):
        database._create_pool(retries=1, delay=0)
    assert database._pool is None


def test_development_keeps_sqlite_fallback(monkeypatch, postgres_down):
    monkeypatch.setenv("APP_ENV", "development")
    database._create_pool(retries=1, delay=0)
    assert isinstance(database._pool, database._SqlitePool)


def test_borrow_raises_instead_of_switching(monkeypatch, postgres_down):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(database.DatabaseUnavailable):
        database.get_db_connection()
    assert database._pool is None


def test_request_gets_503_not_sqlite_data(client, monkeypatch):
    def _down():
        raise database.DatabaseUnavailable("PostgreSQL is unavailable")
    monkeypatch.setattr(database, "_borrow_connection", _down)
    api = client.get("/api/employee/portal", headers={"Authorization": "Bearer x"})
    assert api.status_code == 503
    assert api.get_json()["ok"] is False
    page = client.get("/login")
    assert page.status_code == 503
    assert b"temporarily unavailable" in page.data.lower()
