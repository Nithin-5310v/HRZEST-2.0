"""Roadmap HR-07/7.2 (21 Nov 2026): the test harness must refuse non-local
database hosts, and must stop once with the fix when it can't run its
per-test restore -- instead of failing every test's teardown."""
import psycopg2
import pytest
from _pytest.outcomes import Exit

from tests.conftest import _refuse_non_local_db_host, _require_restore_privilege


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "", "LOCALHOST"])
def test_local_hosts_are_accepted(host):
    _refuse_non_local_db_host({"DB_HOST": host})


@pytest.mark.parametrize("host", ["db.hrzest.com", "18.61.48.198", "prod-postgres.internal"])
def test_other_hosts_are_refused(host):
    with pytest.raises(RuntimeError, match="Refusing to run"):
        _refuse_non_local_db_host({"DB_HOST": host})


def test_a_named_container_host_can_be_allowed():
    _refuse_non_local_db_host({"DB_HOST": "postgres", "HRZEST_TEST_ALLOWED_DB_HOSTS": "postgres, db"})


class _Cursor:
    def __init__(self, deny):
        self.deny = deny
        self.closed = False

    def execute(self, sql, *_a):
        if self.deny and sql.startswith("SET session_replication_role"):
            raise psycopg2.errors.InsufficientPrivilege("permission denied to set parameter")

    def fetchone(self):
        return ("hrzest_app", "16.14")

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, deny):
        self.cur = _Cursor(deny)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_missing_restore_privilege_stops_the_run_once_with_the_fix():
    conn = _Conn(deny=True)
    with pytest.raises(Exit) as exc:
        _require_restore_privilege(conn)
    message = str(exc.value)
    assert "session_replication_role" in message
    assert "provision_db_roles.py" in message and "GRANT SET ON PARAMETER" in message
    assert "superuser" in message
    assert conn.closed


def test_privilege_present_passes_quietly():
    _require_restore_privilege(_Conn(deny=False))
