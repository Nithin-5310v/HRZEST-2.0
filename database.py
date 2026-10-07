# -*- coding: utf-8 -*-
import os
import re
import time
import logging
import sqlite3
import threading
import psycopg2
import psycopg2.pool
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()
_HASHI_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hashi", ".env")
load_dotenv(_HASHI_ENV)

_log = logging.getLogger("attendance")
# Logger is configured in app.py; database.py uses the same named logger so
# output is consistent without adding a second handler here.

# sslmode: "prefer" encrypts the connection whenever the server supports it
# (RDS always does) but degrades gracefully to plaintext otherwise, so local
# Postgres installs without SSL configured (e.g. this repo's dev setup)
# still work. Set DB_SSLMODE=require (or verify-full, with DB_SSLROOTCERT
# pointing at the RDS CA bundle) for a stricter production posture.
#
# statement_timeout bounds how long any single query may run -- without it, a
# slow/runaway query (buggy report filter, or a deliberate DoS attempt) can
# hold a connection -- and with only `maxconn=20` in the pool, a handful of
# stuck queries is enough to starve every other request of a connection.
# connect_timeout bounds how long establishing a new connection may hang if
# the DB host is unreachable, instead of blocking a worker indefinitely.
_DB_CONFIG = dict(
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", "5432")),
    user=os.environ.get("DB_USER", "postgres"),
    password=os.environ.get("DB_PASS", ""),
    dbname=os.environ.get("DB_NAME", "employee_attendance"),
    sslmode=os.environ.get("DB_SSLMODE", "prefer"),
    connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "1")),
    options=f"-c statement_timeout={int(os.environ.get('DB_STATEMENT_TIMEOUT_MS', '30000'))}",
)
if os.environ.get("DB_SSLROOTCERT"):
    _DB_CONFIG["sslrootcert"] = os.environ["DB_SSLROOTCERT"]


class _PooledConnection:
    """Wraps a psycopg2 pooled connection so `.close()` returns it to the
    pool (via putconn) instead of actually closing it -- psycopg2 pools
    require that explicitly, unlike mysql-connector's pooled connections
    where `.close()` already did this. The rest of the codebase's ~240
    call sites all do `db = get_db_connection(); ...; db.close()`, so
    wrapping here avoids rewriting every one of them.

    Also absorbs `buffered=`/`dictionary=` kwargs on .cursor() -- both are
    mysql-connector-only options with no psycopg2 equivalent (psycopg2
    cursors are effectively buffered client-side by default), so this
    keeps the ~230 existing `cursor(buffered=True)` call sites working
    unchanged instead of needing every one edited.
    """
    __slots__ = ("_pool", "_conn")

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn

    def cursor(self, *args, **kwargs):
        kwargs.pop("buffered", None)
        kwargs.pop("dictionary", None)
        return self._conn.cursor(*args, **kwargs)

    def close(self):
        if self._conn is not None:
            self._pool.putconn(self._conn)
            self._conn = None

    def __del__(self):
        # Safety net for the ~200 call sites that do `db = get_db_connection();
        # ...; db.close()` with no try/finally: if a handler raises before
        # reaching .close(), CPython drops this object's refcount to zero as
        # soon as the local variable goes out of scope (no reference cycle
        # here), running __del__ right away -- return the connection to the
        # pool instead of leaking it out of circulation forever. Not a
        # substitute for closing explicitly (GC timing isn't guaranteed on
        # every Python implementation), just a backstop for the common case.
        try:
            self.close()
        except Exception as exc:
            # debug, not warning: __del__ can run during interpreter
            # shutdown when logging handlers may already be torn down --
            # this is a documented backstop for the uncommon case anyway,
            # not a signal something is actually wrong in the normal path.
            _log.debug("_PooledConnection.__del__ close failed: %s", exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _set_search_path(conn, schema_name):
    """Switch a pooled connection's search_path to the given tenant schema.
    Equivalent to MySQL's `USE <db>` from the old design, but scoped to a
    schema within one shared Postgres database instead of a separate
    physical database per tenant (Postgres has no mid-connection USE)."""
    if not re.match(r'^[a-zA-Z0-9_]+$', schema_name):
        raise ValueError(f"Invalid tenant schema name: {schema_name!r}")
    cur = conn.cursor()
    cur.execute(f'SET search_path TO "{schema_name}", public')
    cur.close()


# The SQLite fallback below shares ONE sqlite3.Connection across every
# thread (check_same_thread=False just disables Python's own guard --
# it does NOT make concurrent access safe, per the sqlite3 docs: "you
# have to serialize accesses yourself"). Without this lock, two threads
# racing to execute/commit on the same connection can corrupt its
# in-flight cursor state or throw spurious "database is locked" errors.
# SQLite has no real concurrent-writer story anyway, so serializing here
# costs nothing over the pooled-connection concurrency Postgres normally
# provides -- this path only runs at all when Postgres is unreachable.
_sqlite_lock = threading.Lock()


class _SqliteCursor:
    def __init__(self, conn):
        with _sqlite_lock:
            self._cur = conn.cursor()
        self.rowcount = -1

    def execute(self, query, params=()):
        q = query.replace("%s", "?")
        q = re.sub(r'SET search_path TO [^;]+;?', '', q, flags=re.IGNORECASE)
        q = re.sub(r'\bILIKE\b', 'LIKE', q, flags=re.IGNORECASE)
        q = re.sub(r'\bTIMESTAMP WITH TIME ZONE\b', 'TEXT', q, flags=re.IGNORECASE)
        q = re.sub(r'\bNOW\(\)', 'CURRENT_TIMESTAMP', q, flags=re.IGNORECASE)
        q = re.sub(r'\s+FOR UPDATE SKIP LOCKED\b', '', q, flags=re.IGNORECASE)  # single-writer SQLite
        with _sqlite_lock:
            try:
                res = self._cur.execute(q, params)
                self.rowcount = self._cur.rowcount
                return res
            except Exception as e:
                _log.debug("SQLite query warning: %s | Query: %s", e, query)
                return self

    def executemany(self, query, seq_of_params=()):
        q = query.replace("%s", "?")
        with _sqlite_lock:
            try:
                res = self._cur.executemany(q, seq_of_params)
                self.rowcount = self._cur.rowcount
                return res
            except Exception as e:
                _log.debug("SQLite executemany warning: %s", e)
                return self

    def fetchone(self):
        try:
            return self._cur.fetchone()
        except Exception:
            return (0, 0, 0, 0, 0)

    def fetchall(self):
        try:
            return self._cur.fetchall()
        except Exception:
            return []

    @property
    def description(self):
        return self._cur.description

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def close(self):
        try:
            self._cur.close()
        except Exception as exc:
            _log.debug("_SqliteCursor.close failed: %s", exc)


_SQLITE_FALLBACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "local_fallback.db")


class _SqliteConnWrapper:
    def __init__(self, db_path=_SQLITE_FALLBACK_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.autocommit = True

    def cursor(self, *args, **kwargs):
        return _SqliteCursor(self.conn)

    def close(self):
        pass

    def commit(self):
        with _sqlite_lock:
            self.conn.commit()

    def rollback(self):
        with _sqlite_lock:
            self.conn.rollback()


class _SqlitePool:
    def __init__(self):
        self.conn = _SqliteConnWrapper()
        try:
            _seed_sqlite_db(self.conn.conn)
        except Exception as exc:
            _log.debug("_seed_sqlite_db failed: %s", exc)

    def getconn(self):
        return self.conn

    def putconn(self, conn):
        pass

    @property
    def _used(self): return []
    @property
    def _pool(self): return []
    @property
    def maxconn(self): return 10


def _seed_sqlite_db(raw_conn):
    try:
        cur = raw_conn.cursor()
        cur.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'admin',
            email TEXT,
            plan TEXT DEFAULT 'premium',
            totp_secret TEXT,
            totp_enabled INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reset_token TEXT,
            reset_token_expiry TEXT
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            employee_id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT,
            department TEXT,
            role TEXT,
            password TEXT,
            force_pin_change INTEGER DEFAULT 0,
            company_id INTEGER DEFAULT 1,
            company_code TEXT DEFAULT 'COMP',
            doj TEXT,
            date_of_joining TEXT,
            work_mode TEXT DEFAULT 'office',
            status TEXT DEFAULT 'active',
            face_image TEXT,
            qr_code TEXT,
            phone TEXT,
            gender TEXT,
            dob TEXT,
            blood_group TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            pincode TEXT,
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            emergency_contact_relation TEXT,
            aadhar_number TEXT,
            pan_number TEXT,
            bank_name TEXT,
            bank_account TEXT,
            bank_ifsc TEXT,
            uan_number TEXT,
            about_me TEXT,
            manager_name TEXT,
            manager_id TEXT,
            shift_id INTEGER,
            fingerprint_credential_id TEXT,
            fingerprint_public_key TEXT,
            fingerprint_sign_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            work_lat TEXT,
            work_lon TEXT,
            designation TEXT,
            email_alerts_enabled INTEGER DEFAULT 1,
            joining_date TEXT,
            reset_token TEXT,
            reset_token_expiry TEXT
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT,
            emp_id TEXT,
            name TEXT,
            login_time TEXT,
            logout_time TEXT,
            login_t TEXT,
            logout_t TEXT,
            status TEXT,
            logout_status TEXT,
            logout_s TEXT,
            attendance_type TEXT,
            att_type TEXT,
            date TEXT,
            company_id INTEGER DEFAULT 1,
            worked_minutes INTEGER,
            last_relogin TEXT
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS salary_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT UNIQUE,
            salary_per_day REAL DEFAULT 1000.0,
            monthly_ctc REAL DEFAULT 30000.0,
            basic_pct INTEGER DEFAULT 50,
            last_revised TEXT,
            last_hike_quarter INTEGER,
            last_hike_year INTEGER
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT 'General Shift',
            start_time TEXT DEFAULT '09:00:00',
            half_time TEXT DEFAULT '13:00:00',
            end_time TEXT DEFAULT '18:00:00',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            company_id INTEGER
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS break_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            break_name TEXT NOT NULL,
            break_time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 10,
            is_active INTEGER DEFAULT 1,
            break_type TEXT DEFAULT 'coffee',
            shift_id INTEGER,
            company_id INTEGER
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT,
            leave_date TEXT,
            reason TEXT,
            status TEXT DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            leave_type_id INTEGER,
            is_half_day INTEGER DEFAULT 0,
            half_day_session TEXT,
            cancelled_at TEXT
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS employee_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            uploaded_by TEXT DEFAULT 'admin',
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expiry_date TEXT
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS resignation_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT,
            last_working_day TEXT,
            reason TEXT,
            status TEXT DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            company_id INTEGER DEFAULT 1
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id TEXT,
            subject TEXT,
            description TEXT,
            status TEXT DEFAULT 'Open',
            company_id INTEGER DEFAULT 1,
            category TEXT,
            priority TEXT DEFAULT 'Medium',
            admin_response TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            name TEXT
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            priority TEXT DEFAULT 'Normal',
            visibility TEXT DEFAULT 'public',
            target_employee_id TEXT,
            attachment_original_name TEXT,
            attachment_stored_ref TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS email_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            html_body TEXT NOT NULL,
            attachment_b64 TEXT,
            attachment_filename TEXT,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT,
            claimed_at TEXT
        );
        ''')
        try:
            cur.execute("ALTER TABLE email_queue ADD COLUMN claimed_at TEXT")  # dev files created before SCALE-06
        except sqlite3.OperationalError:
            pass
        cur.execute('''
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            code TEXT,
            pin TEXT,
            logo_path TEXT,
            address TEXT,
            website TEXT,
            email TEXT,
            phone TEXT,
            working_days TEXT DEFAULT 'Mon,Tue,Wed,Thu,Fri',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        ''')
        cur.execute('''
        CREATE TABLE IF NOT EXISTS company_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT DEFAULT 'My Company',
            company_code TEXT DEFAULT 'COMP',
            company_tagline TEXT DEFAULT 'HRzest.com',
            currency_symbol TEXT DEFAULT '₹',
            logo_url TEXT DEFAULT '',
            plan TEXT DEFAULT 'basic',
            work_start TEXT DEFAULT '09:00',
            work_end TEXT DEFAULT '18:00',
            setup_done INTEGER DEFAULT 0,
            compoff_minutes_per_day INTEGER DEFAULT 480,
            shift_start TEXT DEFAULT '09:00:00',
            shift_half TEXT DEFAULT '13:00:00',
            shift_end TEXT DEFAULT '18:00:00',
            email_domain TEXT,
            paid_employee_slots INTEGER,
            compoff_min_ot_minutes INTEGER DEFAULT 120,
            late_deduction_pct REAL DEFAULT 10.00,
            half_day_deduction_pct REAL DEFAULT 50.00,
            grace_minutes INTEGER DEFAULT 15,
            holiday_pay TEXT DEFAULT 'paid',
            leave_pay TEXT DEFAULT 'exclude',
            default_onboarding_template_id INTEGER,
            fingerprint_enabled INTEGER DEFAULT 0,
            qr_enabled INTEGER DEFAULT 1,
            face_enabled INTEGER DEFAULT 1,
            location_enabled INTEGER DEFAULT 1,
            employee_password_auth INTEGER DEFAULT 1,
            face_auth_enabled INTEGER DEFAULT 0,
            geo_enabled INTEGER DEFAULT 0,
            geo_radius INTEGER DEFAULT 100,
            office_lat REAL,
            office_lon REAL,
            pin_enabled INTEGER DEFAULT 1,
            biometric_enabled INTEGER DEFAULT 0,
            notify_leave INTEGER DEFAULT 1,
            notify_payslip INTEGER DEFAULT 1,
            notify_resignation INTEGER DEFAULT 1,
            notify_doc_expiry INTEGER DEFAULT 1,
            session_timeout INTEGER DEFAULT 30,
            working_days TEXT DEFAULT 'Mon,Tue,Wed,Thu,Fri',
            company_logo TEXT,
            timezone TEXT DEFAULT 'Asia/Kolkata',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        ''')

        cur.execute('INSERT OR IGNORE INTO company_settings (id, company_name, setup_done) VALUES (1, "My Company", 0)')
        raw_conn.commit()
    except Exception as e:
        _log.warning("SQLite schema creation error: %s", e)


# ── Default tenant pool ──────────────────────────────────────────────────────
_pool = None
# Guards every _pool creation/replacement below -- without it, concurrent
# requests hitting a cold start (or a caught getconn() failure resetting
# _pool to None) can each construct their own ThreadedConnectionPool and
# stomp the shared global reference; whichever pool object loses that race
# is simply dropped, leaking its connections (never .closeall()'d) until
# Postgres's own idle-connection reaping kicks in.
_pool_lock = threading.Lock()
# How long a request waits for a connection when every pooled connection is
# in use, before giving up with a 503 (see _wait_for_free_connection).
_POOL_WAIT_SECONDS = float(os.environ.get("DB_POOL_WAIT_SECONDS", "5"))


def _ensure_pg_schema(raw_conn):
    try:
        cur = raw_conn.cursor()
        cur.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS logo_url TEXT DEFAULT '';")
        cur.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'basic';")
        cur.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS company_code TEXT DEFAULT 'COMP';")
        cur.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS setup_done INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'basic';")
        cur.execute("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS email TEXT DEFAULT '';")
        raw_conn.commit()
        cur.close()
    except Exception as e:
        _log.warning("PostgreSQL schema migration note: %s", e)


class DatabaseUnavailable(RuntimeError):
    """PostgreSQL can't be reached. Raised instead of switching to the local
    SQLite fallback anywhere but APP_ENV=development; app.py turns it into a
    503 so the outage is loud, and the next request retries Postgres."""


def _sqlite_fallback_allowed():
    # Exact match, read per call: staging, unset or mistyped environments fail closed.
    return os.environ.get("APP_ENV", "production") == "development"


def _create_pool(retries=1, delay=0.1):
    global _pool
    for attempt in range(1, retries + 1):
        candidate = None
        try:
            candidate = psycopg2.pool.ThreadedConnectionPool(
                # psycopg2's pool only *keeps* up to `minconn` idle connections on
                # putconn() -- anything returned past that count is closed outright
                # (see psycopg2.pool.AbstractConnectionPool._putconn), so minconn
                # isn't just a startup warm-up size, it's the ongoing retention
                # floor. At minconn=1, any request that legitimately needs 2+
                # simultaneous connections (a route holding its own connection
                # open while calling a helper like fetch_holidays_set()/
                # get_co_features() that borrows another) was constantly
                # thrashing the pool back down to 1 idle connection, forcing a
                # brand-new TCP+auth handshake to Postgres (tens of ms) on the
                # next such request instead of reusing an idle one -- this is
                # what made admin pages intermittently feel slow to load.
                minconn=int(os.environ.get("DB_POOL_MIN_CONN", "5")),
                maxconn=int(os.environ.get("DB_POOL_MAX_CONN", "20")),
                **_DB_CONFIG,
            )
            _log.info('"Connected to PostgreSQL (attempt %d)"', attempt)
            conn = candidate.getconn()
            _ensure_pg_schema(conn)
            candidate.putconn(conn)
            _pool = candidate  # only a pool that actually works is ever installed
            return
        except (psycopg2.Error, Exception) as e:
            _log.warning('"PostgreSQL not ready (attempt %d/%d): %s"', attempt, retries, e)
            if candidate is not None:
                try:
                    candidate.closeall()
                except Exception as close_exc:
                    _log.debug("closeall() on a failed pool failed: %s", close_exc)
            if attempt < retries:
                time.sleep(delay)
    if not _sqlite_fallback_allowed():
        # Never serve production traffic from a per-instance SQLite file:
        # writes would be lost and every tenant would look empty. Fail
        # loudly instead and retry Postgres on the next request.
        _pool = None
        _log.critical(
            "PostgreSQL unavailable after %d attempt(s) -- refusing to fall back to SQLite "
            "outside APP_ENV=development", retries,
        )
        raise DatabaseUnavailable("PostgreSQL is unavailable")
    _log.error(
        "PostgreSQL unavailable -- falling back to local SQLite (%s). "
        "This is fine for local/demo use, but if this happens in production, "
        "writes are going to a per-instance SQLite file instead of the shared "
        "database and WILL be lost/inconsistent across restarts or workers.",
        _SQLITE_FALLBACK_PATH,
    )
    _pool = _SqlitePool()


def _borrow_connection():
    """Get a connection from the pool (building/rebuilding it as needed) with
    autocommit on. MySQL sessions default to autocommit (this app relies on
    that -- most writes never call .commit()). psycopg2 connections default to
    autocommit=False, where a single failed statement aborts the whole
    transaction and poisons the connection for every future borrower once
    it's returned to the pool, so autocommit is set explicitly here to match
    MySQL's behavior."""
    if _pool is None:
        with _pool_lock:
            if _pool is None:  # re-check: another thread may have built it while we waited
                _create_pool()
    pool = _pool
    try:
        conn = pool.getconn()
    except Exception as exc:
        if isinstance(exc, psycopg2.pool.PoolError) and not getattr(pool, "closed", False):
            # Every connection is in use: the pool is busy, not broken.
            # Building a second pool next to it (the old behaviour) only
            # multiplied the connection count under load until Postgres
            # refused new clients (QA SCALE-09).
            conn = _wait_for_free_connection(pool)
        else:
            if not isinstance(pool, _SqlitePool):
                with _pool_lock:
                    # Only rebuild if _pool is still the same broken object we
                    # just failed against -- another thread may have already
                    # replaced it while we waited for the lock.
                    if _pool is pool:
                        _create_pool(retries=1, delay=0.1)
            conn = _pool.getconn()
    conn.autocommit = True
    return conn


def _wait_for_free_connection(pool):
    deadline = time.monotonic() + _POOL_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.05)
        try:
            return pool.getconn()
        except psycopg2.pool.PoolError:
            continue
    _log.error("Database connection pool exhausted for %.1fs -- returning 503", _POOL_WAIT_SECONDS)
    raise DatabaseUnavailable("connection pool exhausted")


def get_db_connection():
    """Return a connection for the current tenant (or the default "public" schema).

    Checks flask.g.tenant_db if Flask is active, then falls back to "public".
    Import of flask.g is done lazily to avoid circular imports.

    Always explicitly resets search_path on every borrow, even for the
    default (non-tenant) case -- a pooled connection can come back from any
    previous borrower, including ones (like init_master_db/get_tenant_db)
    that pointed it at a tenant schema. Skipping the reset "because it's
    already the default" assumes the connection's history, which isn't a
    safe assumption for a shared pool; that assumption previously caused
    tenant schema search_paths to leak onto unrelated borrows.
    """
    tenant_db = None
    try:
        from flask import g as _flask_g
        tenant_db = getattr(_flask_g, "tenant_db", None)
    except RuntimeError:
        # Outside of a Flask application context -- skip g lookup
        pass

    conn = _borrow_connection()
    _set_search_path(conn, tenant_db or "public")
    return _PooledConnection(_pool, conn)


# ── Master DB (tenant registry) ──────────────────────────────────────────────
# Postgres has no mid-connection database switch, so the tenant registry now
# lives as its own schema in the same physical database as everything else,
# rather than a second physical MySQL database (att_master).
_MASTER_SCHEMA = "att_master"


def get_master_db():
    """Return a connection scoped to the att_master tenant-registry schema."""
    conn = _borrow_connection()
    _set_search_path(conn, _MASTER_SCHEMA)
    return _PooledConnection(_pool, conn)


# ── Tenant DB helper ─────────────────────────────────────────────────────────

def get_tenant_db(schema_name: str):
    """Return a pooled connection switched to the given tenant schema.

    schema_name is validated against ^[a-zA-Z0-9_]+$ before use (in
    _set_search_path) to prevent injection via the SET search_path statement.
    """
    conn = _borrow_connection()
    _set_search_path(conn, schema_name)
    return _PooledConnection(_pool, conn)


# ── Tenant schema provisioning ────────────────────────────────────────────────

def create_tenant_schema(schema_name: str):
    """Create a new tenant schema in the shared Postgres database.

    Validates schema_name before use. Replaces the old create_tenant_database()
    -- tenants are now schemas within one database, not separate MySQL
    databases (Postgres can't switch databases mid-connection the way MySQL's
    USE did, so schema-per-tenant is the equivalent isolation model).
    """
    if not re.match(r'^[a-zA-Z0-9_]+$', schema_name):
        raise ValueError(f"Invalid tenant schema name: {schema_name!r}")
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _create_pool()
    conn = _pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
        conn.commit()
        cur.close()
        _log.info('"Created tenant schema: %s"', schema_name)
    finally:
        _pool.putconn(conn)


# Kept as an alias so any not-yet-updated call sites fail loudly and
# obviously rather than silently doing the wrong thing.
def create_tenant_database(db_name: str):
    raise RuntimeError(
        "create_tenant_database() was replaced by create_tenant_schema() -- "
        "tenants are Postgres schemas now, not separate databases."
    )


@contextmanager
def transaction(conn):
    """Wrap a block of multi-statement writes in one explicit transaction
    instead of _borrow_connection()'s default per-statement autocommit.

    Needed wherever several related INSERT/UPDATE/DELETE statements must
    all succeed or all fail together (e.g. deleting an employee's rows
    across several tables) -- under autocommit, a failure partway through
    leaves the earlier statements permanently committed instead of rolled
    back. Accepts either a _PooledConnection wrapper or a raw psycopg2
    connection; always restores autocommit=True afterwards so the
    connection behaves as every other borrower of this pool expects.
    """
    raw = getattr(conn, "_conn", conn)
    raw.autocommit = False
    try:
        yield conn
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.autocommit = True
