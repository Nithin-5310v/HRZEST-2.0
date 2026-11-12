"""Shared fixtures for the Playwright end-to-end suite.

Unlike tests/ (which drives Flask's in-process test client against the app
object directly), these tests drive a real browser against a real HTTP
server -- see README.md in this directory for why that needs its own
process/DB bootstrap rather than reusing tests/conftest.py's fixtures.
"""
import os
import socket
import threading
import time

import psycopg2
import pytest

# Must happen before `import wsgi` below -- these are read at import time by
# extensions.py/app.py, not re-read per-request. MFA disabled for the same
# reason tests/conftest.py disables it: exercising the emailed-OTP/TOTP
# second factor would mean this suite also has to simulate reading a code
# out of a real mailbox, which is its own project, not a prerequisite for
# testing the flows below.
#
# DB_NAME defaults to a DEDICATED database (att_test_e2e), not att_test --
# these tests write rows directly via SQL outside of tests/conftest.py's
# per-test snapshot/restore fixture (this is a separate process with no
# access to that fixture graph), so sharing att_test would risk leaking
# e2e-only rows into the main suite's assumptions about that database's
# contents, and colliding with whatever else (another session, CI) is
# actively using att_test at the same time. Create it once with:
#   createdb -h localhost -U postgres att_test_e2e
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_NAME", "att_test_e2e")
os.environ["APP_ENV"] = "development"
os.environ["SECRET_KEY"] = "e2e-test-secret-key-not-for-production"
os.environ["ENCRYPTION_KEY"] = "_jboJL8OrI9muPNyf0xCNrakSo_Iz5EbJSQ1KpDcAgY="
os.environ["MANDATORY_ADMIN_MFA"] = "false"
os.environ["MANDATORY_LOGIN_MFA"] = "false"
os.environ["MANDATORY_PLATFORM_ADMIN_MFA"] = "false"
os.environ["REQUIRE_EMAIL_2FA"] = "false"
# _seed_defaults_and_admin() (app.py) only creates the admin_users row seen
# by ADMIN_USERNAME below if ADMIN_PASSWORD is set at that first boot -- see
# its own docstring on why (a blank password means "no admin yet, use
# /setup"). Values here are e2e-only, never real credentials.
os.environ.setdefault("ADMIN_USERNAME", "e2e_admin")
os.environ.setdefault("ADMIN_PASSWORD", "E2eAdmin#Passw0rd!")
os.environ.setdefault("ADMIN_EMAIL", "e2e-admin@example.invalid")


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    """Starts the real app (same wsgi.py entry point production/gunicorn
    use -- full migration bootstrap, all blueprints registered in their
    real order) on a background thread for the whole session, and tears
    it down after. Yields the base URL."""
    import wsgi  # noqa: F401 -- import side effect: runs migrations, registers blueprints, builds wsgi.app

    from werkzeug.serving import make_server

    # Same as tests/conftest.py: the suite logs in more often than /login's
    # 20-a-minute limit allows, and test order decides which test gets the 429.
    from extensions import limiter
    limiter.enabled = False

    port = _free_port()
    server = make_server("127.0.0.1", port, wsgi.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="e2e-live-server")
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    # make_server() binds synchronously before serve_forever() is even
    # called, so the socket is already accepting connections here -- no
    # polling/sleep needed before the first request.
    yield base_url

    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def base_url(live_server):
    """Overrides pytest-base-url's own base_url fixture (normally set via
    --base-url) so `page.goto("/login")` etc. resolve against our live
    server without needing a CLI flag. Session-scoped to match pytest-
    base-url's own _verify_url fixture, which depends on this at session
    scope -- a narrower scope here is a ScopeMismatch error, not just a
    style choice."""
    return live_server


@pytest.fixture(scope="session")
def db_conn(live_server):
    """Raw connection to the same database the live server just migrated --
    deliberately not going through database.py's pooled/tenant-aware
    get_db_connection(), which expects an active Flask app/request context
    this fixture doesn't have. Depends on live_server (not just the env
    vars it sets) so the schema is guaranteed to exist by the time any
    test tries to seed a row into it."""
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=os.environ["DB_PORT"],
        user=os.environ["DB_USER"], password=os.environ.get("DB_PASS", ""),
        dbname=os.environ["DB_NAME"],
    )
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def admin_creds():
    """The env-seeded admin from module-load time above -- created once by
    wsgi.py's own startup init_db() call, exactly like a real fresh
    production boot, not a test-only shortcut."""
    return {"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]}


@pytest.fixture
def employee_creds(db_conn):
    """A fresh employee row per test, with a known bcrypt-hashed password
    (utils.auth.generate_password_hash -- the same hashing this app's own
    login route verifies against), cleaned up afterward so tests don't
    accumulate rows across runs."""
    from utils.auth import generate_password_hash

    emp_id = f"E2E{int(time.time() * 1000) % 1000000}"
    password = "E2eEmployee#Passw0rd!"
    cur = db_conn.cursor()
    cur.execute(
        "INSERT INTO employees (employee_id, name, password, force_pin_change) VALUES (%s,%s,%s,0)",
        (emp_id, "E2E Test Employee", generate_password_hash(password)),
    )
    cur.close()
    yield {"employee_id": emp_id, "password": password}
    cur = db_conn.cursor()
    cur.execute("DELETE FROM leave_requests WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM tickets WHERE employee_id=%s", (emp_id,))
    cur.execute("DELETE FROM employees WHERE employee_id=%s", (emp_id,))
    cur.close()


@pytest.fixture
def login_as(base_url):
    """Returns a helper(page, identifier, password) that drives the real
    /login form -- a fixture (not a plain importable function) so it works
    regardless of pytest's import-mode setting, and closes over base_url
    so call sites don't have to keep passing it."""
    def _login(page, identifier, password):
        page.goto(f"{base_url}/login")
        page.fill('input[name="identifier"]', identifier)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
    return _login
