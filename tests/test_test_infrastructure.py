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
