"""Jenkins' test step: the pytest suite against this PC's PostgreSQL.

The database login comes from HRZEST_ENV_FILE (the development copy's
.env); tests/conftest.py then swaps DB_NAME for throwaway test databases.
Each branch gets its own (att_test_jenkins_<branch>, plus _gw0.._gwN per
xdist worker), so develop and main can build at the same time.

Those databases are dropped before every run, so each build starts as
clean as GitHub Actions' fresh service database: a build cancelled
mid-test (a newer push aborts it) skips the per-test cleanup, and its
leftovers -- e.g. a company_settings row recreated with id 2, which
breaks every test that updates "WHERE id=1" -- would otherwise fail
later builds of perfectly good code. Extra arguments go to pytest:

    python ci/jenkins/run_tests.py -n 4
"""
import os
import re
import sys

import psycopg2
from dotenv import load_dotenv

env_file = os.environ.get("HRZEST_ENV_FILE")
if not env_file or not os.path.isfile(env_file):
    sys.exit("HRZEST_ENV_FILE must point at an .env file with the PostgreSQL login (DB_HOST, DB_USER, DB_PASS, ...)")
load_dotenv(env_file)

branch = re.sub(r"[^a-z0-9]+", "_", os.environ.get("BRANCH_NAME", "local").lower()).strip("_")
base = f"att_test_jenkins_{branch}"
os.environ["HRZEST_TEST_DB"] = base


def drop_test_databases():
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST") or "localhost", port=int(os.environ.get("DB_PORT") or 5432),
        user=os.environ.get("DB_USER"), password=os.environ.get("DB_PASS"), dbname="postgres",
        connect_timeout=10,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database WHERE datname ~ %s",
                        (f"^{base}(_gw[0-9]+)?$",))
            for (name,) in cur.fetchall():
                # Names matched above: att_test_jenkins_<[a-z0-9_]>, nothing to quote.
                cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')  # nosec B608
                print(f"dropped stale test database {name}")
    finally:
        conn.close()


drop_test_databases()

import pytest  # noqa: E402

sys.exit(pytest.main(["-o", "addopts=", "-q", "-p", "no:cacheprovider",
                      "--junitxml=test-results.xml", *sys.argv[1:]]))
