"""Jenkins' test step: the pytest suite against this PC's PostgreSQL.

The database login comes from HRZEST_ENV_FILE (the development copy's
.env); tests/conftest.py then swaps DB_NAME for throwaway test databases.
Each branch gets its own (att_test_jenkins_<branch>, plus _gw0.._gwN per
xdist worker), so develop and main can build at the same time -- but their
TEST RUNS take turns. Two full suites at once (4 workers each, plus the
PostgreSQL they share) exhausted the Jenkins PC's memory: argon2 "Memory
allocation error", failed logins, PostgreSQL crashing into recovery mode,
and builds failing for no reason in the code (main #137, 2026-11-26). So
this script holds a PostgreSQL advisory lock (TEST_RUN_LOCK_KEY) for the
whole run; the other branch's run waits for it, up to LOCK_WAIT_MINUTES,
then goes ahead anyway rather than fail the build. The lock lives on its
own connection and is released when this process exits, even if killed.

Taking turns wasn't enough on its own: a single 4-worker suite still ran
the PC out of memory alongside everything else running there (Jenkins,
the dev copy, editors, browsers). PostgreSQL crashed mid-suite and
thousands of tests failed with "connection already closed" (develop #106
and #110, 2026-11-26/27). So the Jenkinsfile runs 2 workers: slower, but
builds don't fail for lack of memory.

Those databases are dropped before every run, so each build starts as
clean as GitHub Actions' fresh service database: a build cancelled
mid-test (a newer push aborts it) skips the per-test cleanup, and its
leftovers -- e.g. a company_settings row recreated with id 2, which
breaks every test that updates "WHERE id=1" -- would otherwise fail
later builds of perfectly good code. Extra arguments go to pytest:

    python ci/jenkins/run_tests.py -n 2
"""
import os
import re
import sys
import time

import psycopg2
from dotenv import load_dotenv

env_file = os.environ.get("HRZEST_ENV_FILE")
if not env_file or not os.path.isfile(env_file):
    sys.exit("HRZEST_ENV_FILE must point at an .env file with the PostgreSQL login (DB_HOST, DB_USER, DB_PASS, ...)")
load_dotenv(env_file)

branch = re.sub(r"[^a-z0-9]+", "_", os.environ.get("BRANCH_NAME", "local").lower()).strip("_")
base = f"att_test_jenkins_{branch}"
os.environ["HRZEST_TEST_DB"] = base


# Any fixed 64-bit number; "HRZJTEST". Advisory locks are per database, so every
# run takes it on the "postgres" database.
TEST_RUN_LOCK_KEY = 0x48525A4A54455354
LOCK_WAIT_MINUTES = float(os.environ.get("HRZEST_TEST_LOCK_WAIT_MINUTES") or 90)


def _connect_postgres_db():
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST") or "localhost", port=int(os.environ.get("DB_PORT") or 5432),
        user=os.environ.get("DB_USER"), password=os.environ.get("DB_PASS"), dbname="postgres",
        connect_timeout=10,
    )
    conn.autocommit = True
    return conn


def wait_for_turn():
    """Take the cross-branch test lock, waiting while the other branch's suite
    runs. Returns the connection holding it (keep it open until pytest ends),
    or None if the wait ran out and this run goes ahead without it."""
    conn = _connect_postgres_db()
    deadline = time.monotonic() + LOCK_WAIT_MINUTES * 60
    waited = False
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (TEST_RUN_LOCK_KEY,))
            if cur.fetchone()[0]:
                if waited:
                    print("the other branch's test run has finished; starting this one", flush=True)
                return conn
        if time.monotonic() >= deadline:
            print(f"still waiting after {LOCK_WAIT_MINUTES:g} minutes; running the tests anyway", flush=True)
            conn.close()
            return None
        if not waited:
            print("another branch's test run is using this PC; waiting for it to finish "
                  "(running both at once exhausts the memory)", flush=True)
            waited = True
        time.sleep(30)


def drop_test_databases():
    conn = _connect_postgres_db()
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


_turn = wait_for_turn()   # held until this process exits
drop_test_databases()

import pytest  # noqa: E402

sys.exit(pytest.main(["-o", "addopts=", "-q", "-p", "no:cacheprovider",
                      "--junitxml=test-results.xml", *sys.argv[1:]]))
