# Testing

Every change ships with tests, and the whole back-end suite must pass before a
pull request is opened. CI runs the same suite again on the PR; Jenkins runs it
a third time before anything reaches production.

| Suite | Where | Run it | Size / time |
| --- | --- | --- | --- |
| Back end (pytest) | `tests/` (~240 files) | `python -m pytest -n 4` | ~4,700 tests; ~8 min with `-n 4`, 15-20 min with `-n 2` |
| Mobile (Jest) | `mobile/**/__tests__/` | `npx jest` in `mobile/` | ~270 tests, 1-2 min |
| Browser end-to-end (Playwright) | `tests_e2e/` | see [`tests_e2e/README.md`](../../tests_e2e/README.md) | smoke + critical flows |
| Lint | whole repo | `python -m flake8 .` | must be clean |
| Static analysis | whole repo | Bandit + Semgrep (CI only, see [ci-and-release.md](ci-and-release.md)) | must be clean |

## Back-end tests

### What you need

- **PostgreSQL 16.** The suite has no SQLite or mock fallback. Use your own
  Postgres, or the throwaway one that matches CI exactly:

  ```bash
  docker compose -f docker-compose.test.yml up -d    # postgres:16, user postgres / ci_testpass
  ```

- The dev dependencies: `pip install -r requirements.txt -r requirements-dev.txt`.
- Connection settings in `.env` or the shell (`DB_HOST`, `DB_USER`, `DB_PASS`).

### How the test database works (`tests/conftest.py`)

- **Its own database.** The suite never touches your dev data. It creates
  `att_test` (or the name in `HRZEST_TEST_DB`, which **must contain "test"**; the
  conftest refuses anything else) and runs every migration into it.
- **One database per worker.** With `-n 4`, workers use `att_test_gw0`...`gw3`.
- **One run per database.** An advisory lock stops a second run on the same
  database: two runs on one database wipe each other's rows mid-test and fail
  with bogus duplicate-key errors. To run two checkouts side by side, give one
  its own name: `HRZEST_TEST_DB=att_test_mine python -m pytest -n 2`.
- **Clean state per test.** After every test the database is restored to a
  snapshot taken at session start, and in-process caches (company settings, TOTP,
  AI-service health, ...) are cleared by `_clear_process_caches()`. Tests can
  write freely.
- **A company of its own.** The suite's company is "Test Co", reachable at
  `/att-test-suite/...`. Use it to test company-prefixed URLs.
- **Relaxed for tests.** `TESTING=True` turns off the CSRF check and rate limits,
  and `PASSWORD_BREACH_CHECK` is off (no calls to the Pwned Passwords API).

### Fixtures you'll use most

| Fixture | Gives you |
| --- | --- |
| `client` | A Flask test client |
| `db_engine` | A raw psycopg2 connection to the test database (autocommit) |
| `seed_admin` | Admin `test_admin` / `Test@1234`, email `admin@test.local` |
| `seed_employee` | Employee `TST001` / `EmpPass@1`, email `emp@test.local` |
| `seed_hr_admin`, `seed_assigned_employee` | An HR account and an employee assigned to it |
| `seed_manager_admin`, `seed_direct_report` | A manager and a direct report |
| `signed_qr` | `signed_qr("TST001")` returns a valid signed employee QR value |
| `app_gps_checkin_on` | Turns on location-only app check-in for the company |
| `razorpay_demo_mode` | Payments in demo mode (no Razorpay calls) |
| `mock_package(mocker, "blueprints.employee_portal", "get_auth_config", ...)` | Patches a name everywhere a package re-exports it |

Signing in inside a test: post to `/login`, or set the session directly with
`client.session_transaction()`. For the mobile API, get a token from
`/api/employee/login` or `/api/login` and send `Authorization: Bearer <token>`.

### Running a subset

```bash
python -m pytest tests/test_auth_routes.py -q                 # one file
python -m pytest tests/test_auth_routes.py::TestAdminLogin -q  # one class
python -m pytest -k "forgot_password" -q                       # by name
```

### On a busy or low-memory PC

The development PC has 7.7 GB of RAM. With VS Code, browsers, Jenkins and a full
`-n 4` run, it has run out of memory several times. The symptoms look like a
broken code base but aren't:

- `argon2.exceptions.HashingError: Memory allocation error`, then
  `psycopg2.InterfaceError: connection already closed` on thousands of tests;
- `FATAL: the database system is in recovery mode` (PostgreSQL crashed and is
  restarting);
- `OSError: [WinError 1450] Insufficient system resources`.

Use `-n 2`, close what you can, and check `pg_isready` before re-running.
Jenkins runs its own suites against the same PostgreSQL, so avoid a full local
run while a Jenkins build is testing.

### In a git worktree

A worktree doesn't have the main folder's `.env`. Load it explicitly before
pytest starts, for example with a small wrapper that calls
`dotenv.load_dotenv(r"<main folder>\.env")` and then `pytest.main([...])`.

## Writing a test for a change

1. **Make it fail first.** Write the test, run it against the old code (for
   example `git stash` the fix), and see it fail. A test that passes either way
   proves nothing. Say in the commit message that it fails on the old code.
2. **Name it after the behaviour**, not the implementation:
   `test_unknown_email_says_there_is_no_such_account`, not `test_forgot_3`.
3. **Put a docstring on the file or class saying why**: the bug or decision it
   pins, with the date or PR. Later readers need to know what they'd break.
4. **Test through the HTTP layer** where you can (`client.post(...)`). Call a
   helper directly only for pure logic.
5. **New routes need a guard.** `tests/test_route_auth_guards.py` fails on any
   route without an auth decorator, unless it's listed in `ALLOWED_UNGUARDED`
   with a reason. See [auth-and-security.md](auth-and-security.md).
6. **Patch the network, not the logic.** For outside services (AI, email,
   Razorpay, OAuth) patch the call site (`urllib.request.urlopen`,
   `send_email_smtp`, ...), so the code under test still runs.

## Mobile tests

```bash
cd mobile
npx jest                 # all
npx jest src/screens     # a folder
npx jest --runInBand     # one file at a time: use this when the PC is busy
```

React Native Testing Library 14 is async: `await render(...)` and
`await fireEvent.press(...)`. Parallel runs on a loaded PC can hit Jest's
30-second timeout. If tests time out, re-run them with `--runInBand` before
deciding they're broken.

## Lint and static analysis

```bash
python -m flake8 .        # what CI runs; covers tests/ too
```

In a worktree whose `mobile/node_modules` is a link to another checkout, add
`--extend-exclude=mobile/node_modules`. Run flake8 over the **whole repo**, not
just the files you touched: CI fails on any file, and new test files have broken
it before.

Bandit and Semgrep run in CI only (Semgrep doesn't run on Windows). To run
Bandit locally with CI's flags:

```bash
bandit -r . -x ./tests,./venv,./.venv,./.venv_test,./mobile,./node_modules -ll -iii
```
