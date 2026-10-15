"""TEST-02: the test database name is configurable and isolated per xdist worker."""
import os

import pytest

from tests.conftest import _test_db_name


def test_default_is_att_test():
    assert _test_db_name({}) == "att_test"


def test_env_override():
    assert _test_db_name({"HRZEST_TEST_DB": "att_qa"}) == "att_qa"


def test_each_xdist_worker_gets_its_own_database():
    assert _test_db_name({"PYTEST_XDIST_WORKER": "gw3"}) == "att_test_gw3"
    assert _test_db_name({"HRZEST_TEST_DB": "att_qa", "PYTEST_XDIST_WORKER": "gw0"}) == "att_qa_gw0"


@pytest.mark.parametrize("bad", ['att"; DROP DATABASE x; --', "Att_Test", "att-test", ""])
def test_rejects_unsafe_names(bad):
    with pytest.raises(ValueError):
        _test_db_name({"HRZEST_TEST_DB": bad})


def test_this_session_uses_the_derived_name():
    assert os.environ["DB_NAME"] == _test_db_name(os.environ)


def test_reset_clears_in_process_caches():
    """TEST-03: the per-test DB restore also clears what the app cached from
    the old rows, so a value cached by one test can't leak into whichever
    test pytest-randomly runs next."""
    import blueprints.platform_admin as platform_admin
    from utils import helpers, plan_limits
    from tests.conftest import _clear_process_caches
    dicts = [helpers._co_cache, helpers._auth_cache, helpers._pending_counts_cache,
             helpers._companies_cache, helpers._onboarding_cache, platform_admin._employee_count_cache]
    for d in dicts:
        d["stale"] = ("stale", float("inf"))
    plan_limits._rate_cache.update(value=1, checked_at=float("inf"))
    for entry in plan_limits._extra_cost_cache.values():
        entry.update(value=1, checked_at=float("inf"))

    _clear_process_caches()

    assert all("stale" not in d for d in dicts)
    assert plan_limits._rate_cache["value"] is None
    assert all(entry["value"] is None for entry in plan_limits._extra_cost_cache.values())


EXTERNAL_SERVICE_VARS = (
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "N8N_WEBHOOK_URL", "N8N_WEBHOOK_SECRET",
    "EXPO_ACCESS_TOKEN", "SECURITY_ALERT_WEBHOOK_URL", "SECURITY_ALERT_SIGNING_SECRET",
    "SECURITY_ALERT_EMAIL", "SMTP_HOST", "SMTP_USER", "SMTP_PASS",
    "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "REDIS_URL", "REDIS_HOST", "REDIS_PASSWORD",
    "SOC_ADMIN_TOKEN", "SOC_DASHBOARD_URL", "GRAFANA_URL",
)


def test_no_external_service_credentials_reach_the_app():
    """TEST-01: whatever the developer's .env holds, tests run with the same
    blank external-service credentials as CI -- no real email, push,
    security alerts, AI calls or shared Redis from a local test run."""
    leaked = [n for n in EXTERNAL_SERVICE_VARS if os.environ.get(n)]
    leaked += [k for k, v in os.environ.items() if k.startswith("HEARTBEAT_URL_") and v]
    assert leaked == []


def test_modules_that_read_credentials_at_import_got_blank_ones():
    # Only booleans are asserted: a failure must never print a credential.
    import utils.alerts as alerts
    import utils.auth as auth
    import_time_values_blank = not (alerts._WEBHOOK_URL or alerts._SIGNING_SECRET or auth._TURNSTILE_SECRET_KEY)
    assert import_time_values_blank is True


def _relfilenodes(cur):
    cur.execute(
        "SELECT c.oid, c.relfilenode FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN ('public', 'att_master') AND c.relkind IN ('r', 'i', 't')"
    )
    return dict(cur.fetchall())


def test_reset_does_not_rewrite_table_files(db_engine, _db_baseline_snapshot):
    """TEST-05: the per-test reset used TRUNCATE, which gives every table and
    index a new data file and deletes the old one -- over a million file
    creates/deletes per run. On Windows a checkpoint fsync racing those
    deletes PANICked the local Postgres ("could not fsync file ...: Invalid
    argument") into a recovery of many hours. The reset must leave the
    files alone and still restore the baseline rows and sequences."""
    from tests.conftest import _restore_snapshot
    snap, _baseline_schemas = _db_baseline_snapshot
    cur = db_engine.cursor()
    cur.execute("INSERT INTO holidays (date, name) VALUES ('2099-01-01', 'QA reset check')")
    cur.execute("SELECT COUNT(*) FROM admin_users")
    admins = cur.fetchone()[0]
    before = _relfilenodes(cur)

    _restore_snapshot(cur, snap, set())

    assert _relfilenodes(cur) == before
    cur.execute("SELECT COUNT(*) FROM holidays WHERE name = 'QA reset check'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM admin_users")
    assert cur.fetchone()[0] == admins
    cur.execute("SHOW session_replication_role")
    assert cur.fetchone()[0] == "origin"            # FK checks are back on for the tests' own SQL
    cur.close()
