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
