"""
Pytest configuration and fixtures.

Tests run against a real test PostgreSQL database (att_test) to avoid
mock/prod divergence. Requires DB_* env vars pointing to a local PostgreSQL
instance. Set them in .env.test or export before running:

    DB_HOST=localhost DB_USER=postgres DB_PASS=secret pytest tests/
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()


def _test_db_name(environ):
    """Test database for this process: HRZEST_TEST_DB (default "att_test"),
    suffixed with the pytest-xdist worker id when running in parallel
    (att_test_gw0, att_test_gw1, ...). Each worker -- and any other checkout
    that sets its own HRZEST_TEST_DB -- then gets a database of its own
    instead of every run sharing (and resetting under each other) one att_test."""
    base = environ.get("HRZEST_TEST_DB", "att_test")
    worker = environ.get("PYTEST_XDIST_WORKER", "")
    name = f"{base}_{worker}" if worker else base
    if not name.replace("_", "").isalnum() or not name.islower():
        raise ValueError(f"Invalid test database name: {name!r}")
    return name


def _ensure_test_database(name):
    """Create the test database on the local server if it doesn't exist yet
    (each new xdist worker database starts empty; init_db() builds the schema)."""
    import psycopg2
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"), port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ.get("DB_USER", "postgres"), password=os.environ.get("DB_PASS", ""),
        dbname="postgres",
    )
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{name}"')  # nosec B608 -- name validated by _test_db_name
        cur.close()
    finally:
        conn.close()


# Override values for testing
# HRZEST_TEST_DB lets two suites run side by side against separate databases: this harness
# TRUNCATEs every table after each test, so two runs sharing one database corrupt each other.
# The name must contain "test" (scripts/profile_requests.py and factory_reset.py refuse otherwise).
os.environ["DB_NAME"] = _test_db_name(os.environ)
assert "test" in os.environ["DB_NAME"], "HRZEST_TEST_DB must contain 'test'"
_ensure_test_database(os.environ["DB_NAME"])
os.environ["APP_ENV"] = "development"   # avoids HTTPS-only cookies
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
# utils/helpers.py's PII-encryption bootstrap hard-fails at import time if
# this is missing, in every environment including tests — no dev/test
# exception, by design (see utils/helpers.py). Fixed test-only key, not a
# real secret.
os.environ["ENCRYPTION_KEY"] = "_jboJL8OrI9muPNyf0xCNrakSo_Iz5EbJSQ1KpDcAgY="

# No external services from tests (QA TEST-01): whatever the developer's .env
# holds, tests run with the same blank credentials as CI, so a local run can't
# send real email, push notifications, security alerts or AI requests, or
# share a Redis with the running app. Set to "" rather than removed so a later
# load_dotenv() (app.py calls it again) can't bring them back. Razorpay is
# handled per test by the razorpay_demo_mode fixture below.
_EXTERNAL_SERVICE_VARS = (
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "N8N_WEBHOOK_URL", "N8N_WEBHOOK_SECRET",
    "EXPO_ACCESS_TOKEN", "SECURITY_ALERT_WEBHOOK_URL", "SECURITY_ALERT_SIGNING_SECRET",
    "SECURITY_ALERT_EMAIL", "SMTP_HOST", "SMTP_USER", "SMTP_PASS",
    "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "REDIS_URL", "REDIS_HOST", "REDIS_PASSWORD",
    "SOC_ADMIN_TOKEN", "SOC_DASHBOARD_URL", "GRAFANA_URL",
)
for _name in _EXTERNAL_SERVICE_VARS + tuple(k for k in os.environ if k.startswith("HEARTBEAT_URL_")):
    os.environ[_name] = ""

# The mobile/API check-in routes now refuse a punch that omits coordinates when
# the company's location setting is on (MOBILE_REQUIRE_LOCATION, default true in
# production). Several dozen older tests exercise those routes' punch logic
# (login/logout/relogin, shifts, offline sync) with bodies like `json={}`, so --
# like the rate limiter and the MFA gates below -- the suite relaxes the rule by
# default. tests/test_punch_integrity.py::TestMobileCheckin turns it back on
# explicitly to cover the rule itself.
os.environ.setdefault("MOBILE_REQUIRE_LOCATION", "false")

# wsgi.py (the real production entrypoint) registers the migrated
# blueprints on the shared `app` instance from extensions.py BEFORE
# importing app.py — mirror that exact order here. app.py's
# _register_api_v1_aliases() runs at import time and mirrors whatever
# routes are already in app.url_map, so blueprints registered AFTER
# `import app` would silently lose their /api/v1/* aliases (a real gap
# this order previously had: /api/v1/employees 404'd in tests while
# working in production, because wsgi.py's order is already correct).
from extensions import app as flask_app
from blueprints.health import health_bp
from blueprints.notifications import notifications_bp
from blueprints.payroll import payroll_bp
from blueprints.leave import leave_bp
from blueprints.admin_views import admin_views_bp
from blueprints.auth import auth_bp
from blueprints.employees import employees_bp
from blueprints.attendance import attendance_bp
from blueprints.tickets import tickets_bp
from blueprints.performance import performance_bp
from blueprints.documents import documents_bp
from blueprints.org import org_bp
from blueprints.oauth_signup import oauth_signup_bp
from blueprints.onboarding import onboarding_bp
from blueprints.employee_portal import employee_portal_bp
from blueprints.core import core_bp
from blueprints.ai_hrms import ai_hrms_bp
from blueprints.email_blast import email_blast_bp
from blueprints.daily_report import daily_report_bp
from blueprints.billing import billing_bp
from blueprints.webhooks import webhooks_bp
from blueprints.seats import seats_bp
from blueprints.auto_debit import auto_debit_bp
from blueprints.billing_dunning import billing_dunning_bp
from blueprints.platform_admin import platform_admin_bp
from blueprints.honeypot_routes import honeypot_bp
from blueprints.disbursement import disbursement_bp
from blueprints.hr_dashboard import hr_dashboard_bp
from blueprints.policies import policies_bp
from blueprints.company_profile import company_profile_bp
from blueprints.seo import seo_bp
from blueprints.privacy import privacy_bp
from blueprints.soc import soc_bp
flask_app.register_blueprint(health_bp)
flask_app.register_blueprint(notifications_bp)
flask_app.register_blueprint(payroll_bp)
flask_app.register_blueprint(leave_bp)
flask_app.register_blueprint(admin_views_bp)
flask_app.register_blueprint(auth_bp)
flask_app.register_blueprint(employees_bp)
flask_app.register_blueprint(attendance_bp)
flask_app.register_blueprint(tickets_bp)
flask_app.register_blueprint(performance_bp)
flask_app.register_blueprint(documents_bp)
flask_app.register_blueprint(org_bp)
flask_app.register_blueprint(oauth_signup_bp)
flask_app.register_blueprint(onboarding_bp)
flask_app.register_blueprint(employee_portal_bp)
flask_app.register_blueprint(core_bp)
flask_app.register_blueprint(ai_hrms_bp)
flask_app.register_blueprint(email_blast_bp)
flask_app.register_blueprint(daily_report_bp)
flask_app.register_blueprint(billing_bp)
flask_app.register_blueprint(webhooks_bp)
flask_app.register_blueprint(seats_bp)
flask_app.register_blueprint(auto_debit_bp)
flask_app.register_blueprint(billing_dunning_bp)
flask_app.register_blueprint(platform_admin_bp)
flask_app.register_blueprint(honeypot_bp)
flask_app.register_blueprint(disbursement_bp)
flask_app.register_blueprint(hr_dashboard_bp)
flask_app.register_blueprint(policies_bp)
flask_app.register_blueprint(company_profile_bp)
flask_app.register_blueprint(seo_bp)
flask_app.register_blueprint(privacy_bp)
flask_app.register_blueprint(soc_bp)

# Mirror wsgi.py's WSGI-level tenant-prefix stripping so tests exercise the
# real path-based tenant resolution (www.hrzest.com/<slug>/...), not just
# the pre-migration subdomain/session-cache branches.
from utils.tenant_routing import TenantPrefixMiddleware
flask_app.wsgi_app = TenantPrefixMiddleware(flask_app.wsgi_app)

# Import app AFTER blueprints are registered so all module-level reads pick
# up test values AND _register_api_v1_aliases() sees the full route set.
import app as _app_module  # noqa: F401 — triggers route registration + init_db

# Fall back to typical local-Postgres defaults ONLY if .env (already loaded
# by database.py's load_dotenv() above) didn't provide them — setting these
# before the imports would permanently lock out the real .env values, since
# load_dotenv() never overrides an already-set env var.
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", os.getenv("DB_USER", "postgres"))
os.environ.setdefault("DB_PASS", os.getenv("DB_PASS", ""))

# Disable Flask-Limiter for all tests — its .enabled attribute is set at init
# time (not dynamically from config), so we patch the instance directly.
_app_module.limiter.enabled = False

# Disable the mandatory-admin-MFA-enrollment gate (app.py's
# _enforce_admin_mfa_enrollment) for the suite by default — most tests log in
# admin sessions directly via session_transaction without an enrolled TOTP
# secret, same reasoning as disabling the rate limiter above. Tests for the
# gate itself (tests/test_mandatory_admin_mfa.py) re-enable it locally.
flask_app.config["MANDATORY_ADMIN_MFA"] = False

# Disable the mandatory-emailed-OTP-at-login gate (blueprints/auth.py's
# MANDATORY_LOGIN_MFA) for the same reason: nearly the entire suite uses a
# plain POST /login as its "get an authenticated session" setup and
# expects it to complete immediately. Tests for the gate itself
# (tests/test_login_mfa.py) re-enable it locally.
flask_app.config["MANDATORY_LOGIN_MFA"] = False

# Disable the platform (super-admin) console's mandatory-MFA gate and the
# Email Settings TOTP step-up gate for the same reason as the two flags
# above -- both now default on (app.py), and most of the suite logs a
# platform-admin/admin session in directly without walking through
# enrollment or a step-up code. Tests for these gates specifically
# (tests/test_platform_admin.py's TestLoginFlow, tests/test_email_2fa.py)
# re-enable them locally via their own fixture/per-test overrides.
flask_app.config["MANDATORY_PLATFORM_ADMIN_MFA"] = False
flask_app.config["REQUIRE_EMAIL_2FA"] = False

# Make utils/async_writer.py's background-thread write queue run
# synchronously for the whole suite -- see set_synchronous_mode()'s
# docstring. Existing tests that call _write_queue.join() to wait for a
# drain (test_auth.py, test_auth_routes.py, test_comprehensive.py,
# test_leave_routes.py) keep working unchanged: with nothing ever queued,
# that join() returns immediately.
from utils.async_writer import set_synchronous_mode
set_synchronous_mode(True)


@pytest.fixture(scope="session")
def db_engine():
    """Return a raw psycopg2 connection to the test database for fixture setup."""
    import psycopg2
    # Fixture setup/teardown (per-test TRUNCATE, seeding, audit.bypass
    # cleanup) is privileged work. When the DB_OWNER_USER split is configured
    # the application under test runs as the restricted runtime role (DB_USER)
    # -- that is the point -- so the harness itself connects as the owner.
    owner = os.environ.get("DB_OWNER_USER", "").strip()
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        user=owner or os.environ["DB_USER"],
        password=(os.environ.get("DB_OWNER_PASS", "") if owner else os.environ.get("DB_PASS", "")),
        dbname=os.environ["DB_NAME"],
    )
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session", autouse=True)
def _init_test_db(db_engine):
    """Run init_db() once per test session to set up schema in att_test."""
    with flask_app.app_context():
        from app import init_db
        init_db()
    # Clear transient state tables so stale data from prior runs doesn't bleed in
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts WHERE 1=1")
    # init_db() only marks setup_done=1 when ADMIN_PASSWORD is set in the
    # environment (it seeds an admin from env, then marks setup complete
    # since one now exists) -- CI's pytest job doesn't set that var, so on
    # a fresh database setup_done stays 0 and every test that logs in via
    # the real /login POST route (rather than the session_transaction
    # bypass some test files use) gets redirected to /setup before its
    # credentials are even checked. Tests assume setup is already done
    # (see test_auth_blueprint.py's test_get_when_setup_done_redirects_to_login),
    # so make that true directly instead of depending on env-var seeding.
    cur.execute("UPDATE company_settings SET setup_done=1 WHERE setup_done=0")
    cur.close()
    # Register the test schema as an unlimited/all-features tenant so the
    # rest of the suite (which knows nothing about pricing tiers) isn't
    # affected by utils/plan_limits.py's employee-count/feature enforcement
    # -- same reasoning as MANDATORY_LOGIN_MFA being force-disabled above.
    # tests/test_plan_limits.py exercises the actual tier behavior directly.
    from app import init_master_db
    init_master_db()
    cur = db_engine.cursor()
    cur.execute("SELECT 1 FROM att_master.tenants WHERE db_name=%s", (os.environ["DB_NAME"],))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO att_master.tenants (company_name, subdomain, db_name, plan, status) "
            "VALUES (%s, %s, %s, 'enterprise', 'active')",
            ("Test Co", "att-test-suite", os.environ["DB_NAME"]),
        )
    cur.close()


@pytest.fixture(scope="session", autouse=True)
def _reset_login_attempts(db_engine, _init_test_db):
    """Clear login_attempts before the session starts.

    att_test is a persistent database, not recreated per run — tests that
    intentionally trigger failed logins (wrong password, unknown user)
    accumulate failed_count across every past run. Once a hardcoded test
    identifier crosses _LOGIN_MAX_ATTEMPTS, locked_until gets set and never
    clears (only a *successful* login clears it, which these tests never
    do), so a run days later can spuriously fail on an unrelated assertion
    because the login page renders "Account locked" instead of "Invalid
    credentials". Depends on _init_test_db so the table already exists.
    """
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts")
    cur.close()


@pytest.fixture(scope="session", autouse=True)
def _reset_attendance_lockouts(db_engine, _init_test_db):
    """Clear attendance_lockouts before the session starts -- same
    persistent-database problem _reset_login_attempts (above) solves, for a
    different table. utils/attendance_utils.py's record_attendance_failure()
    accumulates failed_count per (employee_id, date) with no per-test
    cleanup anywhere in this suite; tests/test_attendance_checkin.py's kiosk
    fingerprint/face-mismatch tests call it against the shared TST001
    identifier for *today's real calendar date*, so failed_count keeps
    climbing across every run made on the same day until it crosses
    ATTENDANCE_LOCKOUT_MAX_ATTEMPTS -- at which point every other test that
    checks TST001 in for today (anything hitting /api/attendance/checkin or
    the web /attendance route) starts failing with a stale lockout message
    instead of exercising the behavior it's actually testing.
    """
    cur = db_engine.cursor()
    cur.execute("DELETE FROM attendance_lockouts")
    cur.close()


# ── Per-test database isolation ──────────────────────────────────────────────
# The suite previously shared one persistent att_test database across every
# test with no reset between tests -- rows (and mutated config: several
# tests POST to /settings and never revert it) leaked from one test into the
# next, making the *number* of failures depend on run order: 256 failures
# running the whole suite in one process vs. 124 running the same tests
# split into two, with individual failures passing when run alone (see the
# audit). Fixed below by snapshotting every table's full row contents right
# after one-time baseline setup finishes, then restoring that exact
# snapshot after every single test, pass or fail.
#
# Deliberately NOT done by wrapping each test in one shared connection/
# transaction that rolls back at teardown (the other standard approach for
# this kind of problem): this app has both a real background writer thread
# (utils/async_writer.py, started unconditionally at import -- neutralized
# for tests above via set_synchronous_mode, which was needed for this
# reason regardless of which approach was used) AND a genuine multi-thread
# concurrency test (test_seats.py's
# test_concurrent_signups_at_cap_only_one_succeeds, which spawns two real
# threads racing two independent real connections against a row lock to
# prove only one wins). Forcing every get_db_connection() call during a
# test onto one shared connection would break that test outright and would
# need permanent special-casing for every future concurrency test. Snapshot/
# restore instead leaves the app's normal connection pooling completely
# untouched -- every test still gets real, independent, autocommit
# connections exactly as production does; only the *starting data* is reset.
_BASELINE_SCHEMAS = ("public", "att_master")


def _list_tables(cur, schema):
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s AND table_type='BASE TABLE'",
        (schema,),
    )
    return [r[0] for r in cur.fetchall()]


def _list_user_schemas(cur):
    """Every non-system schema currently in the database -- used to detect
    a schema created mid-test (e.g. blueprints/org.py's
    create_tenant_schema(), exercised by the org/platform-admin
    provisioning tests) so it can be dropped at teardown instead of
    accumulating for the rest of the run."""
    cur.execute(
        "SELECT schema_name FROM information_schema.schemata "
        r"WHERE schema_name NOT LIKE 'pg\_%' ESCAPE '\' AND schema_name != 'information_schema'"
    )
    return set(r[0] for r in cur.fetchall())


def _serial_columns(cur, schema, table):
    """[(column_name, sequence_name), ...] for every column in schema.table
    backed by a sequence (SERIAL/BIGSERIAL/IDENTITY) -- needed because
    TRUNCATE ... RESTART IDENTITY resets the sequence to its start, but
    _restore_snapshot() below then re-inserts baseline rows WITH their
    original id values (bypassing nextval() entirely). Left unfixed, the
    sequence and the actual max id in the table fall out of sync -- the
    very next auto-increment insert in the NEXT test would collide with a
    baseline row's id and fail with a duplicate-key error. See
    _target_setvals() below."""
    cur.execute(
        "SELECT column_name, pg_get_serial_sequence(%s, column_name) "
        "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
        (f"{schema}.{table}", schema, table),
    )
    return [(col, seq) for col, seq in cur.fetchall() if seq]


def _target_setvals(cols, rows, serial_cols):
    """[(sequence_name, value, is_called), ...] -- the exact setval() args
    each serial column needs after this table is truncated and its baseline
    rows re-inserted. Computed once in Python from the already-fetched
    baseline snapshot (max of the captured column, or 1/not-called for an
    empty table) instead of a round-trip SELECT MAX(...) per column per
    restore -- restoring the same frozen snapshot every time means this
    value can never change between calls."""
    out = []
    for col, seq in serial_cols:
        idx = cols.index(col)
        values = [row[idx] for row in rows if row[idx] is not None]
        if values:
            out.append((seq, max(values), True))
        else:
            out.append((seq, 1, False))
    return out


def _snapshot_schema(cur, schema):
    """{table_name: (column_names, [row_tuples], serial_columns)} for every
    base table in `schema`, captured in the table's natural column order.
    schema/table names come from information_schema, never request/test
    input."""
    snap = {}
    for table in _list_tables(cur, schema):
        cur.execute(f'SELECT * FROM "{schema}"."{table}"')  # nosec B608 -- identifiers sourced from information_schema, not external input
        cols = [d[0] for d in cur.description]
        snap[table] = (cols, cur.fetchall(), _serial_columns(cur, schema, table))
    return snap


def _restore_snapshot(cur, schema_snapshots, extra_schemas):
    """Empty every table across the snapshotted schemas, re-insert exactly
    the rows captured in schema_snapshots, then re-sync every serial
    column's sequence in one combined round trip (see _target_setvals()
    above -- one setval() per column was the dominant per-test cost; a
    single statement chaining all of them cut this fixture's per-test
    overhead by roughly 3x). Drops any schema created mid-test that wasn't
    part of the baseline.

    Tables are emptied with DELETE, not TRUNCATE (QA TEST-05): TRUNCATE
    gives every table and index a brand-new data file and deletes the old
    one, which across ~2,700 tests meant over a million file creates and
    deletes per run -- checkpoints spent an hour fsyncing them, and on
    Windows one such fsync racing a delete PANICked the local Postgres into
    a recovery lasting many hours. DELETE keeps the files. FK checks and
    triggers are off (session_replication_role=replica) only while the
    tables are emptied and refilled, so the order doesn't matter; the
    setval() calls below take the place of RESTART IDENTITY."""
    for schema in extra_schemas:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')  # nosec B608 -- schema name sourced from information_schema, not external input

    cur.execute("SET session_replication_role = replica")
    try:
        deletes = "; ".join(
            f'DELETE FROM "{schema}"."{table}"'
            for schema, snap in schema_snapshots.items()
            for table in snap
        )
        if deletes:
            cur.execute(deletes)  # nosec B608 -- table list built entirely from information_schema, never external input
        for schema, snap in schema_snapshots.items():
            for table, (cols, rows, _serial_cols) in snap.items():
                if rows:
                    col_list = ", ".join(f'"{c}"' for c in cols)
                    placeholders = ", ".join(["%s"] * len(cols))
                    insert_sql = (
                        f'INSERT INTO "{schema}"."{table}" ({col_list}) VALUES ({placeholders})'
                    )  # nosec B608 -- schema/table/columns sourced from information_schema; row values are fully parameterized
                    cur.executemany(insert_sql, rows)
    finally:
        cur.execute("SET session_replication_role = DEFAULT")

    setval_args = []
    for schema, snap in schema_snapshots.items():
        for table, (cols, rows, serial_cols) in snap.items():
            setval_args.extend(_target_setvals(cols, rows, serial_cols))

    if setval_args:
        select_list = ", ".join(["setval(%s, %s, %s)"] * len(setval_args))
        flat_params = [v for triple in setval_args for v in triple]
        cur.execute(f"SELECT {select_list}", flat_params)  # nosec B608 -- fixed setval(...) template repeated N times; every value is a bound param


@pytest.fixture(scope="session")
def _db_baseline_snapshot(db_engine, _init_test_db, _reset_login_attempts, _reset_attendance_lockouts):
    """Captured once, after every one-time baseline fixture above has run
    (schema/table creation, seeded company_settings/admin/tenant-registry
    rows, and the one-time login_attempts/attendance_lockouts clears) --
    this is the exact state every single test should start from, and the
    exact state every single test's teardown restores.
    """
    cur = db_engine.cursor()
    snap = {schema: _snapshot_schema(cur, schema) for schema in _BASELINE_SCHEMAS}
    baseline_schemas = _list_user_schemas(cur)
    cur.close()
    return snap, baseline_schemas


@pytest.fixture(autouse=True)
def _reset_db_after_test(db_engine, _db_baseline_snapshot):
    """Restore the exact baseline snapshot after every test, pass or fail --
    teardown-only; no setup step is needed since the previous test's own
    teardown (or, for the very first test, _db_baseline_snapshot itself)
    already leaves the database in the correct starting state.
    """
    yield
    snap, baseline_schemas = _db_baseline_snapshot
    cur = db_engine.cursor()
    extra_schemas = _list_user_schemas(cur) - baseline_schemas
    _restore_snapshot(cur, snap, extra_schemas)
    cur.close()
    _clear_process_caches()


def _clear_process_caches():
    """The restore above (and fixtures that write with raw SQL) changes rows
    behind the app's back, so anything the app cached in-process from those
    rows is stale afterwards -- e.g. a test turning face check-in off would
    leave `face_enabled` cached for whichever test pytest-randomly runs next
    (QA TEST-03). Same reason for the TOTP cache: seed_admin reuses one
    username across the suite, so a stale "MFA enabled" entry would leak."""
    import blueprints.platform_admin as platform_admin
    from utils import helpers, plan_limits
    from utils.totp import clear_totp_cache_for_tests
    helpers.invalidate_settings_cache()
    helpers.invalidate_pending_counts_cache()
    helpers.invalidate_companies_cache()
    helpers._onboarding_cache.clear()
    plan_limits.invalidate_rate_paise_cache()
    plan_limits.invalidate_extra_cost_cache()
    platform_admin._employee_count_cache.clear()
    clear_totp_cache_for_tests()


# A fixed "at the office" coordinate for check-in tests. Offices are per
# company (company_settings.office_lat/lon); tests that enforce the geofence
# set the company's office to this point.
TEST_OFFICE = {"lat": 17.494664737165042, "lon": 78.40496618113566}


@pytest.fixture
def razorpay_demo_mode(monkeypatch):
    """No Razorpay keys for this test, whatever the developer's local .env
    holds. CI has none, and the billing/seats/auto-debit/dunning tests are
    written for that demo branch -- with a real rzp_test_ key loaded they
    stop taking it (and would call the Razorpay sandbox). utils/
    razorpay_utils.py reads its keys once at import, so its module copies
    are what get blanked; tests that need the live branch still monkeypatch
    razorpay_configured on top of this. Production code is untouched."""
    import utils.razorpay_utils as rz
    monkeypatch.setattr(rz, "_KEY_ID", "")
    monkeypatch.setattr(rz, "_KEY_SECRET", "")
    monkeypatch.setattr(rz, "_WEBHOOK_SECRET", "")


@pytest.fixture
def app_gps_checkin_on(db_engine):
    """Turns on the mobile app's location-only check-in
    (company_settings.app_gps_checkin). The default test company has QR
    and face enabled, so /api/employee/checkin refuses it otherwise (QA
    NV-18). For tests that exercise that endpoint itself; the per-test
    snapshot restore puts the setting back."""
    from utils.helpers import invalidate_settings_cache
    cur = db_engine.cursor()
    cur.execute("UPDATE company_settings SET app_gps_checkin=1")
    cur.close()
    invalidate_settings_cache()


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True   # disables CSRF check + rate limits
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["SESSION_COOKIE_SECURE"] = False
    flask_app.config["RATELIMIT_ENABLED"] = False  # Flask-Limiter 3.x flag
    with flask_app.test_client() as c:
        yield c


@pytest.fixture
def signed_qr():
    """Returns a signed_qr(emp_id) callable that signs an employee_id the
    same way qr_generator.generate_qr() does, for tests that POST a QR
    check-in payload (auth_combo in qr_only/qr_face/qr_fingerprint) --
    blueprints/attendance.py now runs every such employee_id through
    qr_generator.verify_qr_value(), which requires the "{emp_id}.{signature}"
    format, not a bare employee_id. Reuses the real signing function (not a
    reimplementation) so this can never drift from production behavior.
    Works even for an employee_id that doesn't exist in the DB -- signing is
    a pure HMAC over the string, independent of whether the employee is
    real, which is exactly what a negative-case test (e.g. an unknown
    employee ID) needs to reach the app's own "employee not found" check
    instead of failing at signature verification."""
    from qr_generator import _qr_signature

    def _sign(emp_id):
        return f"{emp_id}.{_qr_signature(emp_id)}"
    return _sign


@pytest.fixture
def seed_admin(db_engine):
    """Insert a test admin user; clean up after the test.

    Also clears any login_attempts row for "test_admin" on every use, not
    just once per session (see _reset_login_attempts above) -- this
    identifier is shared across the whole suite, and _LOGIN_MAX_ATTEMPTS=3
    is low enough that a handful of legitimate wrong-password tests
    elsewhere in a 1900+-test run can accumulate a real 15-minute lockout
    partway through, silently breaking every subsequent test that expects
    a plain POST /login to succeed (assertions failing with a 302
    back to the login page instead of the expected 200/redirect-to-admin).
    A session-scoped one-time reset only guards against stale state from a
    *previous* run; it does nothing once a run's own tests start
    accumulating failures again during that same run.
    """
    from utils.auth import generate_password_hash
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts WHERE identifier='test_admin'")
    cur.execute(
        "INSERT INTO admin_users (username, password, email) VALUES (%s,%s,%s) "
        "ON CONFLICT (username) DO NOTHING",
        ("test_admin", generate_password_hash("Test@1234"), "admin@test.local"),
    )
    yield {"username": "test_admin", "password": "Test@1234"}
    cur.execute("DELETE FROM admin_users WHERE username='test_admin'")
    cur.close()


@pytest.fixture
def seed_employee(db_engine):
    """Insert a test employee; clean up after the test.

    Also clears any login_attempts row for "TST001" on every use -- same
    shared-identifier lockout risk as seed_admin above."""
    from utils.auth import generate_password_hash
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts WHERE identifier='TST001'")
    cur.execute(
        "INSERT INTO employees (employee_id, name, email, password, force_pin_change) "
        "VALUES (%s,%s,%s,%s,0) ON CONFLICT (employee_id) DO NOTHING",
        ("TST001", "Test Employee", "emp@test.local", generate_password_hash("EmpPass@1")),
    )
    yield {"employee_id": "TST001", "password": "EmpPass@1", "name": "Test Employee"}
    cur.execute("DELETE FROM employees WHERE employee_id='TST001'")
    cur.execute("DELETE FROM api_tokens WHERE identity='TST001'")
    cur.close()


@pytest.fixture
def seed_hr_admin(db_engine):
    """Insert a test HR-role admin_users account; clean up after the test.
    Mirrors seed_admin above but role='hr' -- for tests of the
    assigned_hr_username scoping added to attendance/leave/tickets/
    performance/onboarding/payroll (see utils/helpers.py's
    hr_scope_column/hr_scope_subquery/hr_scope_denied)."""
    from utils.auth import generate_password_hash, HR_ROLE
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts WHERE identifier='test_hr_admin'")
    cur.execute(
        "INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,1) "
        "ON CONFLICT (username) DO NOTHING",
        ("test_hr_admin", generate_password_hash("Test@1234"), HR_ROLE, "hr@test.local"),
    )
    yield {"username": "test_hr_admin", "password": "Test@1234"}
    cur.execute("DELETE FROM admin_users WHERE username='test_hr_admin'")
    cur.close()


@pytest.fixture
def seed_assigned_employee(db_engine, seed_hr_admin):
    """A second test employee whose assigned_hr_username is seed_hr_admin's
    username -- pairs with seed_employee (TST001, left unassigned) so an
    HR-scoping test can seed one employee IN scope and one OUT of scope."""
    from utils.auth import generate_password_hash
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts WHERE identifier='TST002'")
    cur.execute(
        "INSERT INTO employees (employee_id, name, email, password, force_pin_change, assigned_hr_username) "
        "VALUES (%s,%s,%s,%s,0,%s) "
        "ON CONFLICT (employee_id) DO UPDATE SET assigned_hr_username=EXCLUDED.assigned_hr_username",
        ("TST002", "Test Assigned Employee", "emp2@test.local",
         generate_password_hash("EmpPass@1"), seed_hr_admin["username"]),
    )
    yield {"employee_id": "TST002", "password": "EmpPass@1", "name": "Test Assigned Employee"}
    cur.execute("DELETE FROM employees WHERE employee_id='TST002'")
    cur.execute("DELETE FROM api_tokens WHERE identity='TST002'")
    cur.close()


@pytest.fixture
def seed_manager_admin(db_engine):
    """Insert a test manager-role admin_users account; clean up after the
    test. Mirrors seed_hr_admin above but role='manager' -- for tests of
    the employees.manager_id scoping added to leave/resignation/overtime
    (see utils/helpers.py's manager_scope_column/manager_scope_subquery/
    manager_scope_denied)."""
    from utils.auth import generate_password_hash, MANAGER_ROLE
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts WHERE identifier='test_manager_admin'")
    cur.execute(
        "INSERT INTO admin_users (username, password, role, email, is_active) VALUES (%s,%s,%s,%s,1) "
        "ON CONFLICT (username) DO NOTHING",
        ("test_manager_admin", generate_password_hash("Test@1234"), MANAGER_ROLE, "manager@test.local"),
    )
    yield {"username": "test_manager_admin", "password": "Test@1234"}
    cur.execute("DELETE FROM admin_users WHERE username='test_manager_admin'")
    cur.close()


@pytest.fixture
def seed_direct_report(db_engine, seed_manager_admin):
    """A third test employee whose manager_id is seed_manager_admin's
    username -- pairs with seed_employee (TST001) and seed_assigned_employee
    (TST002) so a manager-scoping test can seed one employee IN scope and
    one OUT of scope."""
    from utils.auth import generate_password_hash
    cur = db_engine.cursor()
    cur.execute("DELETE FROM login_attempts WHERE identifier='TST003'")
    cur.execute(
        "INSERT INTO employees (employee_id, name, email, password, force_pin_change, manager_id) "
        "VALUES (%s,%s,%s,%s,0,%s) "
        "ON CONFLICT (employee_id) DO UPDATE SET manager_id=EXCLUDED.manager_id",
        ("TST003", "Test Direct Report", "emp3@test.local",
         generate_password_hash("EmpPass@1"), seed_manager_admin["username"]),
    )
    yield {"employee_id": "TST003", "password": "EmpPass@1", "name": "Test Direct Report"}
    cur.execute("DELETE FROM employees WHERE employee_id='TST003'")
    cur.execute("DELETE FROM api_tokens WHERE identity='TST003'")
    cur.close()
