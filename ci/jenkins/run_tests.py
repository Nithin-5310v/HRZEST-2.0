"""Jenkins' test step: the pytest suite against this PC's PostgreSQL.

The database login comes from HRZEST_ENV_FILE (the development copy's
.env); tests/conftest.py then swaps DB_NAME for throwaway test databases.
Each branch gets its own (att_test_jenkins_<branch>), so develop and main
can build at the same time. Extra arguments go to pytest:

    python ci/jenkins/run_tests.py -n 4
"""
import os
import re
import sys

from dotenv import load_dotenv

env_file = os.environ.get("HRZEST_ENV_FILE")
if not env_file or not os.path.isfile(env_file):
    sys.exit("HRZEST_ENV_FILE must point at an .env file with the PostgreSQL login (DB_HOST, DB_USER, DB_PASS, ...)")
load_dotenv(env_file)

branch = re.sub(r"[^a-z0-9]+", "_", os.environ.get("BRANCH_NAME", "local").lower()).strip("_")
os.environ["HRZEST_TEST_DB"] = f"att_test_jenkins_{branch}"

import pytest  # noqa: E402

sys.exit(pytest.main(["-o", "addopts=", "-q", "-p", "no:cacheprovider",
                      "--junitxml=test-results.xml", *sys.argv[1:]]))
