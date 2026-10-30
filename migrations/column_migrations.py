import psycopg2

from extensions import app_log
from utils.auth import generate_password_hash, check_password_hash
from .core_tables import _attach_updated_at_trigger


def _run_schema_migrations(cursor, db):
    """ALTER existing installs' tables up to the current schema: new
    columns, indexes, FK backstops, and one-time data migrations, split
    into independently ordered phases so each stays small and readable.
    Order matters: column ALTERs must precede everything that assumes
    those columns exist (indexes, PII widening, FK backstops)."""
    _run_column_migrations(cursor, db)
    _run_password_migrations(cursor, db)
    _run_qr_signing_migration(cursor, db)
    _run_index_migrations(cursor, db)
    _run_data_integrity_migrations(cursor, db)
    _run_company_policies_seed_v2(cursor, db)
    _run_push_tokens_table_migration(cursor, db)
    _run_auth_hardening_migration(cursor, db)
    _run_audit_integrity_migration(cursor, db)
    _run_punch_integrity_migration(cursor, db)
    _run_privacy_tables_migration(cursor, db)
    _run_detection_state_migration(cursor, db)
    _run_leave_declined_to_rejected(cursor, db)


def _run_leave_declined_to_rejected(cursor, db):
    """The mobile leave decline used to store 'Declined' (the resignation
    vocabulary) where every other leave path stores 'Rejected', so those
    leaves were missing from rejected counts and badges (QA BUG-33).
    One-time, tracked in _applied_migrations, per tenant."""
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='leave_declined_to_rejected_v1'")
        if not cursor.fetchone():
            cursor.execute("UPDATE leave_requests SET status='Rejected' WHERE status='Declined'")
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('leave_declined_to_rejected_v1')")
            db.commit()
    except Exception as exc:
        db.rollback()
        app_log.warning("Migration 'leave_declined_to_rejected_v1' failed: %s", exc, exc_info=True)


def _run_auth_hardening_migration(cursor, db):
    """Per-tenant objects for the login-hardening fixes. Lives here (not
    core_tables.py) for the same reason as the push_tokens backfill below:
    this function is what _run_migrations_for_all_tenants() re-applies to
    every already-provisioned tenant schema on boot. Every statement is
    IF NOT EXISTS-guarded, so re-running is safe.

    - login_otp_challenges: server-side emailed-login-code store
      (utils/login_otp.py) -- replaces keeping the code in the cookie
      session. Expired rows are purged here on every boot and on every
      new challenge.
    - password_changed_at: lets every password reset/change revoke
      sessions and API tokens issued before it (utils/auth.py).
    - api_tokens.mfa_verified: only tokens issued after the emailed-code
      step may be bridged into a web session (blueprints/core.py).
      Existing tokens default to 0, i.e. not bridgeable until re-login.
    - employees.must_change_password: set by an admin password reset
      (blueprints/employees.py); the employee must replace the temporary
      password at next login, web or mobile. Separate from
      force_pin_change, which every new employee starts with and which
      only the web login enforces."""
    from utils.login_otp import CREATE_TABLE_SQL, CREATE_INDEX_SQL, PURGE_EXPIRED_SQL
    try:
        cursor.execute(CREATE_TABLE_SQL)
        cursor.execute(CREATE_INDEX_SQL)
        cursor.execute(PURGE_EXPIRED_SQL)
        cursor.execute("ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP DEFAULT NULL")
        cursor.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP DEFAULT NULL")
        cursor.execute("ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS mfa_verified SMALLINT NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS must_change_password SMALLINT NOT NULL DEFAULT 0")
        db.commit()
    except psycopg2.Error as exc:
        db.rollback()
        app_log.warning("Migration 'auth hardening' failed: %s", exc, exc_info=True)


def _run_audit_integrity_migration(cursor, db):
    """The audit hash chain + strict immutability trigger (migrations/
    audit_integrity.py). _init_core_tables() only ever runs for a brand-new
    tenant or the default schema, so without this an already-provisioned
    tenant would never receive the chain -- or the production-strict trigger
    function that compiles out the audit.bypass escape hatch. Idempotent."""
    try:
        from .audit_integrity import init_audit_integrity
        init_audit_integrity(cursor, db)
    except Exception as exc:
        db.rollback()
        app_log.warning("Migration 'audit integrity' failed: %s", exc, exc_info=True)


def _run_detection_state_migration(cursor, db):
    """Cooldown bookkeeping for utils/detections.py: one row per (rule, key)
    so concurrent workers and repeated scheduler ticks alert once."""
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detection_state (
                rule       VARCHAR(60)  NOT NULL,
                key        VARCHAR(200) NOT NULL,
                last_fired TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fired      INT          NOT NULL DEFAULT 1,
                PRIMARY KEY (rule, key)
            )
        """)
        db.commit()
    except psycopg2.Error as exc:
        db.rollback()
        app_log.warning("Migration 'detection_state' failed: %s", exc, exc_info=True)


def _run_privacy_tables_migration(cursor, db):
    """consent_records (append-only ledger) and privacy_requests (subject
    access / erasure workflow) behind utils/privacy.py."""
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS consent_records (
                id             SERIAL PRIMARY KEY,
                employee_id    VARCHAR(50) NOT NULL,
                purpose        VARCHAR(50) NOT NULL,
                policy_version VARCHAR(30) NOT NULL,
                granted        BOOLEAN NOT NULL,
                ip             VARCHAR(64),
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_consent_emp_purpose ON consent_records (employee_id, purpose, id DESC)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS privacy_requests (
                id           SERIAL PRIMARY KEY,
                employee_id  VARCHAR(50) NOT NULL,
                kind         VARCHAR(20) NOT NULL,
                status       VARCHAR(20) NOT NULL DEFAULT 'open',
                note         TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                handled_by   VARCHAR(100),
                handled_at   TIMESTAMP,
                result_json  TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_privacy_requests_status ON privacy_requests (status, id)")
        # Consent history is evidence of consent: append-only, same guard as
        # the audit tables (the function is installed by the audit-integrity
        # migration above; skipped if it is somehow absent).
        cursor.execute("SELECT 1 FROM pg_proc WHERE proname = '_reject_audit_mutation' LIMIT 1")
        if cursor.fetchone():
            cursor.execute("DROP TRIGGER IF EXISTS trg_consent_records_immutable ON consent_records")
            cursor.execute(
                "CREATE TRIGGER trg_consent_records_immutable BEFORE UPDATE OR DELETE ON consent_records "
                "FOR EACH ROW EXECUTE FUNCTION _reject_audit_mutation()")
        db.commit()
    except psycopg2.Error as exc:
        db.rollback()
        app_log.warning("Migration 'privacy tables' failed: %s", exc, exc_info=True)


def _run_punch_integrity_migration(cursor, db):
    """Tables/columns behind utils/punch_integrity.py (liveness challenges,
    per-punch evidence, office-network allow-list). Idempotent; lives here
    (not only in core_tables.py) so already-provisioned tenants get it --
    _init_core_tables() never re-runs against an existing tenant."""
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkin_challenges (
                id          VARCHAR(64) PRIMARY KEY,
                employee_id VARCHAR(50) NOT NULL,
                nonce_hash  VARCHAR(64) NOT NULL,
                action      VARCHAR(16) NOT NULL,
                ip          VARCHAR(64),
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at  TIMESTAMP NOT NULL,
                used_at     TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_checkin_challenges_expires ON checkin_challenges (expires_at)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS punch_evidence (
                id              SERIAL PRIMARY KEY,
                employee_id     VARCHAR(50) NOT NULL,
                channel         VARCHAR(20) NOT NULL,
                auth_combo      VARCHAR(30),
                ip              VARCHAR(64),
                device_id       VARCHAR(64),
                lat             DOUBLE PRECISION,
                lon             DOUBLE PRECISION,
                mocked          BOOLEAN DEFAULT FALSE,
                liveness        VARCHAR(20),
                liveness_action VARCHAR(16),
                photo_sha256    VARCHAR(64),
                photo_dhash     VARCHAR(64),
                flags           VARCHAR(200),
                outcome         VARCHAR(20) DEFAULT 'accepted',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_punch_evidence_emp ON punch_evidence (employee_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_punch_evidence_device ON punch_evidence (device_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_punch_evidence_sha ON punch_evidence (photo_sha256)")
        cursor.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS office_ip_cidrs TEXT DEFAULT ''")
        cursor.execute("ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS require_office_network INT DEFAULT 0")
        db.commit()
    except psycopg2.Error as exc:
        db.rollback()
        app_log.warning("Migration 'punch integrity tables' failed: %s", exc, exc_info=True)


def _run_push_tokens_table_migration(cursor, db):
    """Backfills the push_tokens table (see core_tables.py's own copy) into
    tenants provisioned BEFORE it was added there. _init_core_tables() only
    ever runs for a brand-new tenant (init_tenant_db()) or the default
    schema at startup (init_db()) -- it is never re-run against an already-
    provisioned tenant, so a table added only there is invisible to every
    tenant that existed first. This is that same gap company_policies
    already has; closing it here rather than there too, since a genuinely
    new table (as opposed to a new column) needs its CREATE TABLE in this
    file specifically to ever reach _run_migrations_for_all_tenants()'s
    per-tenant backfill (see migrations/orchestration.py). Plain CREATE
    TABLE IF NOT EXISTS is naturally idempotent -- no _applied_migrations
    bookkeeping needed, unlike the one-time *data* migrations below."""
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS push_tokens (
                id SERIAL PRIMARY KEY,
                owner_type VARCHAR(20) NOT NULL CHECK (owner_type IN ('admin', 'employee')),
                owner_id VARCHAR(50) NOT NULL,
                expo_push_token VARCHAR(255) NOT NULL UNIQUE,
                platform VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _attach_updated_at_trigger(cursor, "push_tokens")
        db.commit()
    except psycopg2.Error as exc:
        db.rollback()
        app_log.warning("Migration 'push_tokens table backfill' failed: %s", exc, exc_info=True)


def _run_column_migrations(cursor, db):
    """Add every column an existing install might be missing --
    idempotent (IF NOT EXISTS) and independently try/except-guarded per
    statement so one unsupported ALTER on an older Postgres can't block
    the rest."""
    # Migrations for existing installs
    for sql in [
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS logout_status VARCHAR(50) DEFAULT NULL",
        # Previously issued lazily by blueprints/onboarding.py on every offer-letter save --
        # runtime DDL in a request handler, which forces the web role to be a table owner.
        "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS notice_period_days INT DEFAULT 30",
        "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS candidate_address TEXT",
        # When a drainer claimed the row; lets rows stranded in 'sending' by
        # a crashed worker go back to the queue (QA SCALE-06).
        "ALTER TABLE email_queue ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP DEFAULT NULL",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS attendance_type VARCHAR(50) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS email VARCHAR(150) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS role VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS password VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS shift_id INT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS date_of_joining DATE DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS phone VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS gender VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS dob DATE DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS blood_group VARCHAR(10) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS address TEXT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS city VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS state VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS pincode VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS emergency_contact_phone VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS emergency_contact_relation VARCHAR(50) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS aadhar_number TEXT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS pan_number TEXT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_name VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_account TEXT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_ifsc TEXT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS uan_number TEXT DEFAULT NULL",
        "ALTER TABLE employees ALTER COLUMN aadhar_number TYPE TEXT",
        "ALTER TABLE employees ALTER COLUMN pan_number TYPE TEXT",
        "ALTER TABLE employees ALTER COLUMN bank_account TYPE TEXT",
        "ALTER TABLE employees ALTER COLUMN bank_ifsc TYPE TEXT",
        "ALTER TABLE employees ALTER COLUMN uan_number TYPE TEXT",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS work_mode VARCHAR(20) DEFAULT 'office'",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS work_lat DECIMAL(10,8) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS work_lon DECIMAL(11,8) DEFAULT NULL",
        "ALTER TABLE salary_config ADD COLUMN IF NOT EXISTS last_revised DATE DEFAULT NULL",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS email VARCHAR(150) DEFAULT NULL",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS reset_token VARCHAR(64) DEFAULT NULL",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS reset_token_expiry TIMESTAMP DEFAULT NULL",
        "ALTER TABLE email_config ADD COLUMN IF NOT EXISTS from_email VARCHAR(150) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS about_me TEXT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS manager_name VARCHAR(150) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS manager_id VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS department VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS designation VARCHAR(150) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_active SMALLINT DEFAULT 1",
        # Which HR admin_users account this employee is managed by --
        # NULL means unassigned (visible only to 'admin' role, not to any
        # 'hr' session; see blueprints/employees.py's view_employees()).
        # References admin_users.username, not employees.employee_id --
        # an HR account can be either a real admin_users row (created via
        # /hr_accounts) or the auto-provisioned one for an employee whose
        # own role is exactly "HR" (blueprints/auth.py's
        # _ensure_hr_admin_account), and both share that same username
        # space, so this one column covers both cases identically.
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS assigned_hr_username VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS email_alerts_enabled SMALLINT DEFAULT 1",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS leave_type_id INT DEFAULT NULL",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS is_half_day SMALLINT DEFAULT 0",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS half_day_session VARCHAR(10) DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS company_code VARCHAR(10) DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS email_domain VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS paid_employee_slots INT DEFAULT NULL",
        # Company Admin Profile page's Company Info tab -- the only two
        # non-sensitive fields it lets an admin edit inline.
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(30) DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS address VARCHAR(500) DEFAULT NULL",
        # Read by utils/helpers.py's get_company_settings() as co.logo_url,
        # rendered in templates/admin_base.html's sidebar (admin + HR) and
        # templates/employee_portal.html's sidebar -- this ALTER, not
        # database.py's _ensure_pg_schema() (which only ever runs once,
        # against the connection pool's default schema at process startup),
        # is what actually reaches every tenant schema, including ones
        # created long after startup via provision_tenant().
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS logo_url TEXT DEFAULT NULL",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'admin'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_path VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS address TEXT DEFAULT NULL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS website VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS email VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS phone VARCHAR(30) DEFAULT NULL",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS worked_minutes INT DEFAULT 0",
        "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS last_relogin TIME DEFAULT NULL",
        "ALTER TABLE salary_config ADD COLUMN IF NOT EXISTS monthly_ctc DECIMAL(12,2) DEFAULT 0",
        "ALTER TABLE salary_config ADD COLUMN IF NOT EXISTS basic_pct INT DEFAULT 50",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS compoff_min_ot_minutes INT DEFAULT 120",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS compoff_minutes_per_day INT DEFAULT 480",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS late_deduction_pct DECIMAL(5,2) DEFAULT 10.00",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS half_day_deduction_pct DECIMAL(5,2) DEFAULT 50.00",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS grace_minutes INT DEFAULT 15",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS holiday_pay VARCHAR(20) DEFAULT 'paid' CHECK (holiday_pay IN ('paid','unpaid'))",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS leave_pay VARCHAR(20) DEFAULT 'exclude' CHECK (leave_pay IN ('exclude','absent'))",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS joining_date DATE DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS company_id INT DEFAULT NULL",
        "ALTER TABLE employee_documents ADD COLUMN IF NOT EXISTS expiry_date DATE DEFAULT NULL",
        "ALTER TABLE overtime_records ADD COLUMN IF NOT EXISTS requested_by_employee SMALLINT DEFAULT 0",
        "ALTER TABLE overtime_records ADD COLUMN IF NOT EXISTS employee_reason VARCHAR(500) DEFAULT NULL",
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP DEFAULT NULL",
        "ALTER TABLE salary_config ADD COLUMN IF NOT EXISTS last_hike_quarter SMALLINT DEFAULT NULL",
        "ALTER TABLE salary_config ADD COLUMN IF NOT EXISTS last_hike_year INT DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS default_onboarding_template_id INT DEFAULT NULL",
        "ALTER TABLE employee_onboarding_tasks ADD COLUMN IF NOT EXISTS employee_note VARCHAR(500) DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS fingerprint_enabled SMALLINT DEFAULT 0",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS qr_enabled SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS face_enabled SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS location_enabled SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS employee_password_auth SMALLINT DEFAULT 1",
        # Location-only app check-in (/api/employee/checkin): NULL = follow
        # the check-in policy, 1/0 = the admin's explicit choice (QA NV-18).
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS app_gps_checkin SMALLINT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS fingerprint_credential_id VARCHAR(512) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS fingerprint_public_key TEXT DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS fingerprint_sign_count INT DEFAULT 0",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS face_auth_enabled SMALLINT DEFAULT 0",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS geo_enabled SMALLINT DEFAULT 0",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS geo_radius INT DEFAULT 100",
        # NULL = not configured yet, geofencing is a no-op regardless of
        # location_enabled -- see utils/attendance_utils.py's
        # is_within_office_range(). Previously every tenant was silently
        # geofenced against one process-wide OFFICE_LAT/LON env var; this
        # makes office location a real per-tenant setting.
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS office_lat DOUBLE PRECISION DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS office_lon DOUBLE PRECISION DEFAULT NULL",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS pin_enabled SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS biometric_enabled SMALLINT DEFAULT 0",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS notify_leave SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS notify_payslip SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS notify_resignation SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS notify_doc_expiry SMALLINT DEFAULT 1",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS session_timeout INT DEFAULT 30",
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS working_days VARCHAR(30) DEFAULT 'Mon,Tue,Wed,Thu,Fri'",
        "ALTER TABLE break_config ADD COLUMN IF NOT EXISTS break_type VARCHAR(20) DEFAULT 'coffee' CHECK (break_type IN ('coffee','lunch','custom'))",
        "ALTER TABLE break_config ADD COLUMN IF NOT EXISTS shift_id INT DEFAULT NULL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS working_days VARCHAR(30) DEFAULT 'Mon,Tue,Wed,Thu,Fri'",
        "ALTER TABLE onboarding_templates ADD COLUMN IF NOT EXISTS role VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE shifts ADD COLUMN IF NOT EXISTS company_id INT DEFAULT NULL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS pin VARCHAR(10) DEFAULT NULL",
        "ALTER TABLE break_config ADD COLUMN IF NOT EXISTS company_id INT DEFAULT NULL",
        "ALTER TABLE announcements ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'public' CHECK (visibility IN ('public','private'))",
        "ALTER TABLE announcements ADD COLUMN IF NOT EXISTS target_employee_id VARCHAR(50) DEFAULT NULL",
        "ALTER TABLE announcements ADD COLUMN IF NOT EXISTS attachment_original_name VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE announcements ADD COLUMN IF NOT EXISTS attachment_stored_ref VARCHAR(500) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS reset_token VARCHAR(80) DEFAULT NULL",
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS reset_token_expiry TIMESTAMP DEFAULT NULL",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS totp_enabled SMALLINT NOT NULL DEFAULT 0",
        # Lets an admin terminate/reactivate an HR account (or any admin_users
        # row) without deleting it -- login history and the row itself stay
        # intact. Existing accounts default active (1), so this is a no-op
        # for every login path until something explicitly sets it to 0.
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS is_active SMALLINT NOT NULL DEFAULT 1",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()",
        # OAuth self-serve signup/login (blueprints/oauth_signup.py). A row
        # with oauth_provider set has an unusable random `password` (see
        # provision_tenant() caller in oauth_signup.py) -- password login
        # is disabled for it until/unless the admin sets a real password
        # via the existing forgot-password flow. oauth_subject is the
        # provider's stable account id ('sub' claim) -- see the unique
        # index in _run_index_migrations_v4, which is what a returning
        # OAuth login looks the account up by.
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS oauth_provider VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS oauth_subject VARCHAR(255) DEFAULT NULL",
        # Employee document verification (Company Onboarding -> Trial ->
        # Document Verification -> Subscription Activation): doc_kind
        # ('id_proof'|'address_proof', see blueprints/documents.py's
        # _EMPLOYEE_VERIFICATION_DOC_KINDS) is NULL for every existing
        # free-text doc_type row -- only the new verification-specific
        # upload path populates it. verification_status is NULL until then
        # too; 'pending'|'approved'|'rejected' once it is.
        "ALTER TABLE employee_documents ADD COLUMN IF NOT EXISTS doc_kind VARCHAR(30) DEFAULT NULL",
        "ALTER TABLE employee_documents ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE employee_documents ADD COLUMN IF NOT EXISTS verified_by VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE employee_documents ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP DEFAULT NULL",
        "ALTER TABLE employee_documents ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR(500) DEFAULT NULL",
        # Set exactly once, atomically, the moment the LAST required
        # doc_kind for this employee flips to 'approved' -- doubles as the
        # notification-idempotency guard (blueprints/documents.py's
        # verify_employee_document()) and the persisted "is this employee
        # fully verified" signal.
        "ALTER TABLE employees ADD COLUMN IF NOT EXISTS doc_verification_notified_at TIMESTAMP DEFAULT NULL",
    ]:
        try:
            cursor.execute(sql)
            db.commit()
        except psycopg2.Error:
            db.rollback()


def _run_password_migrations(cursor, db):
    """Back-fill a default PIN for employees with no password hash yet,
    plus the two related one-time migrations tracked in
    _applied_migrations (reset-to-default-PIN, and flagging accounts
    still on that default PIN as needing a forced change)."""
    # Back-fill password for existing employees that have none (default PIN = 1234)
    cursor.execute("SELECT employee_id FROM employees WHERE password IS NULL")
    for (eid,) in cursor.fetchall():
        cursor.execute(
            "UPDATE employees SET password=%s WHERE employee_id=%s",
            (generate_password_hash('1234'), eid)
        )
    db.commit()

    # One-time migration: reset ALL employees to default PIN 1234
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS _applied_migrations (
                name VARCHAR(100) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='default_pin_1234'")
        if not cursor.fetchone():
            cursor.execute("UPDATE employees SET password=%s", (generate_password_hash('1234'),))
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('default_pin_1234')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'default_pin_1234' failed: %s", exc, exc_info=True)

    # Migration: add force_pin_change column and flag employees on default PIN
    try:
        cursor.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS force_pin_change SMALLINT DEFAULT 0")
        db.commit()
    except psycopg2.Error:
        db.rollback()
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='force_pin_change_flag'")
        if not cursor.fetchone():
            cursor.execute("SELECT employee_id, password FROM employees")
            for eid, pwd_hash in cursor.fetchall():
                if pwd_hash and check_password_hash(pwd_hash, '1234'):
                    cursor.execute("UPDATE employees SET force_pin_change=1 WHERE employee_id=%s", (eid,))
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('force_pin_change_flag')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'force_pin_change_flag' failed: %s", exc, exc_info=True)


def _run_qr_signing_migration(cursor, db):
    """One-time: regenerate every existing employee's QR code image so it
    encodes an HMAC-signed value (qr_generator.py's generate_qr/
    verify_qr_value) instead of the raw employee_id. Employee IDs are
    often sequential/guessable (EMP001, EMP002, ...), so a QR that just
    encoded the ID let anyone who knew or guessed a coworker's ID spoof
    their attendance via the QR-only check-in path with no further
    verification -- this closes that hole for every employee already in
    the system, not just ones created after the fix. Existing physical
    badges/printouts (which still show the old, now-invalid QR) will stop
    working at check-in and need reprinting from the regenerated image.
    Runs once per tenant schema (called from init_db(), which
    init_tenant_db() also calls) via the _applied_migrations guard; a
    SECRET_KEY rotation invalidates the signature again afterward, at
    which point blueprints/employees.py's regenerate_qr() refreshes a
    single employee's badge on demand."""
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='qr_code_signing'")
        if cursor.fetchone():
            return
        from qr_generator import generate_qr
        cursor.execute("SELECT employee_id FROM employees WHERE qr_code IS NOT NULL")
        for (eid,) in cursor.fetchall():
            try:
                new_path = generate_qr(eid)
                cursor.execute("UPDATE employees SET qr_code=%s WHERE employee_id=%s", (new_path, eid))
            except Exception as exc:
                app_log.warning("QR regeneration failed for '%s' during 'qr_code_signing' migration: %s", eid, exc)
        cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('qr_code_signing')")
        db.commit()
    except Exception as exc:
        app_log.warning("Migration 'qr_code_signing' failed: %s", exc, exc_info=True)


def _run_index_migrations(cursor, db):
    """Three rounds of performance indexes added as real query-usage
    audits found missing ones -- each in its own function since they're
    independent, one-time, _applied_migrations-tracked units."""
    _run_index_migrations_v1(cursor, db)
    _run_index_migrations_v2(cursor, db)
    _run_index_migrations_v3(cursor, db)
    _run_index_migrations_v4(cursor, db)
    _run_index_migrations_v5(cursor, db)


def _run_index_migrations_v1(cursor, db):
    # Performance indexes migration
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='perf_indexes_v1'")
        if not cursor.fetchone():
            _idx_stmts = [
                "CREATE INDEX IF NOT EXISTS idx_leave_emp ON leave_requests(employee_id)",
                "CREATE INDEX IF NOT EXISTS idx_leave_status ON leave_requests(status)",
                "CREATE INDEX IF NOT EXISTS idx_tickets_emp ON tickets(employee_id)",
                "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)",
                "CREATE INDEX IF NOT EXISTS idx_resign_emp ON resignation_requests(employee_id)",
                "CREATE INDEX IF NOT EXISTS idx_notif_emp ON notifications(employee_id)",
                "CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(is_read)",
                "CREATE INDEX IF NOT EXISTS idx_onboard_emp ON employee_onboarding(employee_id)",
                "CREATE INDEX IF NOT EXISTS idx_onboard_status ON employee_onboarding(status)",
                "CREATE INDEX IF NOT EXISTS idx_payroll_emp ON payroll_runs(employee_id)",
            ]
            for stmt in _idx_stmts:
                try:
                    cursor.execute(stmt)
                    db.commit()
                except Exception:
                    db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('perf_indexes_v1')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'perf_indexes_v1' failed: %s", exc, exc_info=True)


def _run_index_migrations_v2(cursor, db):
    """High-traffic columns missing from v1."""
    # Performance indexes v2 -- high-traffic columns missing from v1
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='perf_indexes_v2'")
        if not cursor.fetchone():
            _idx_stmts_v2 = [
                "CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(date)",
                "CREATE INDEX IF NOT EXISTS idx_emp_active ON employees(is_active)",
                "CREATE INDEX IF NOT EXISTS idx_emp_company ON employees(company_id)",
                "CREATE INDEX IF NOT EXISTS idx_leave_date ON leave_requests(leave_date)",
            ]
            for stmt in _idx_stmts_v2:
                try:
                    cursor.execute(stmt)
                    db.commit()
                except Exception:
                    db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('perf_indexes_v2')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'perf_indexes_v2' failed: %s", exc, exc_info=True)


def _run_index_migrations_v3(cursor, db):
    """Found via a real query-usage audit (grepped every WHERE/JOIN
    against these columns before adding, not guessed):
    offer_letters.response_token is looked up on EVERY candidate-facing
    request (/offer_letter_pdf, /offer_letter_respond) with no index at
    all -- the highest-value one here. The rest cover employee-scoped
    tables that were missing from v1/v2 despite the same WHERE
    employee_id=%s pattern as the tables v1 already covers."""
    # Performance indexes v3 -- found via a real query-usage audit (grepped
    # every WHERE/JOIN against these columns before adding, not guessed):
    # offer_letters.response_token is looked up on EVERY candidate-facing
    # request (/offer_letter_pdf, /offer_letter_respond) with no index at
    # all -- the highest-value one here. The rest cover employee-scoped
    # tables that were missing from v1/v2 despite the same WHERE
    # employee_id=%s pattern as the tables v1 already covers.
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='perf_indexes_v3'")
        if not cursor.fetchone():
            _idx_stmts_v3 = [
                "CREATE INDEX IF NOT EXISTS idx_offer_letters_token ON offer_letters(response_token)",
                "CREATE INDEX IF NOT EXISTS idx_offer_letters_onboarding ON offer_letters(onboarding_id)",
                "CREATE INDEX IF NOT EXISTS idx_ob_tasks_onboarding ON employee_onboarding_tasks(onboarding_id)",
                "CREATE INDEX IF NOT EXISTS idx_perf_kpis_review ON performance_kpis(review_id)",
                "CREATE INDEX IF NOT EXISTS idx_emp_docs_emp ON employee_documents(employee_id)",
                "CREATE INDEX IF NOT EXISTS idx_incentives_emp ON employee_incentives(employee_id)",
                "CREATE INDEX IF NOT EXISTS idx_overtime_emp ON overtime_records(employee_id)",
            ]
            for stmt in _idx_stmts_v3:
                try:
                    cursor.execute(stmt)
                    db.commit()
                except Exception:
                    db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('perf_indexes_v3')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'perf_indexes_v3' failed: %s", exc, exc_info=True)


def _run_index_migrations_v4(cursor, db):
    """OAuth signup/login (blueprints/oauth_signup.py) -- a returning
    admin's OAuth callback looks their admin_users row up by
    (oauth_provider, oauth_subject); this is what makes that lookup real
    (and prevents two provider identities ever mapping to the same
    tenant's login, which app-level code alone can't guarantee under
    concurrent signups)."""
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='oauth_indexes_v4'")
        if not cursor.fetchone():
            try:
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_users_oauth ON admin_users (oauth_provider, oauth_subject) "
                    "WHERE oauth_provider IS NOT NULL"
                )
                db.commit()
            except Exception:
                db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('oauth_indexes_v4')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'oauth_indexes_v4' failed: %s", exc, exc_info=True)


def _run_index_migrations_v5(cursor, db):
    """api_tokens.expires_at -- the periodic expired-token purge
    (utils/auth.py's _purge_expired_tokens) scanned the whole table
    without it (QA PERF-01)."""
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='api_token_expiry_index_v5'")
        if not cursor.fetchone():
            try:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_expires ON api_tokens (expires_at)")
                db.commit()
            except Exception:
                db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('api_token_expiry_index_v5')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'api_token_expiry_index_v5' failed: %s", exc, exc_info=True)


def _run_data_integrity_migrations(cursor, db):
    """One-time migrations that harden data integrity -- each in its own
    function since they are independent, _applied_migrations-tracked
    units: a unique constraint backing the bonus-award idempotency
    check, widening Fernet-encrypted employee PII columns to TEXT so
    ciphertext fits, and NOT VALID foreign-key backstops on every
    employee_id/company_id column that was previously only enforced by
    application code."""
    _run_incentives_unique_constraint_migration(cursor, db)
    _run_pii_widen_migration_v1(cursor, db)
    _run_pii_widen_migration_v2(cursor, db)
    _run_fk_backstop_migration(cursor, db)


def _run_incentives_unique_constraint_migration(cursor, db):
    # Unique constraint backing award_performance_bonus's ON CONFLICT DO
    # NOTHING guard (blueprints/payroll.py) -- without it, two concurrent
    # bonus-award requests for the same employee/goal/quarter could both
    # pass the app-level "already awarded?" check and double-pay a bonus.
    # Wrapped like the index migration above: if any deployment already has
    # duplicate rows, this no-ops via rollback rather than crashing startup.
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='incentives_unique_v1'")
        if not cursor.fetchone():
            try:
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_incentives_unique "
                    "ON employee_incentives(employee_id, goal_id, month, year)"
                )
                db.commit()
            except Exception:
                db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('incentives_unique_v1')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'incentives_unique_v1' failed: %s", exc, exc_info=True)


# Starter content for company_policies, seeded once per tenant schema below
# -- short placeholders, not the full legacy text that used to be hardcoded
# in templates/employee_portal.html (~lines 2315-2750 there), since HR/admin
# can now edit these for real via blueprints/policies.py's CRUD (surfaced
# as the HR Dashboard's Policies tab) instead of that text being frozen in
# a template. category values match that old template's tab ids so a
# future swap of employee_portal.html onto GET /api/employee/policies is a
# drop-in match.
# Superseded by the full-text content below (company_policies_seed_v2) --
# kept only so that migration can detect rows still holding this original
# placeholder text and safely replace them, without touching a row any HR
# has since edited for real.
_POLICY_SEED_CONTENT_V1_LEGACY = {
    "terms": (
        "By using this portal, you agree to keep your login credentials confidential, "
        "keep your personal details accurate, and use the attendance/leave features "
        "honestly. Edit this from the HR Dashboard's Policies tab to add your "
        "organisation's full terms."
    ),
    "rules": (
        "These rules apply to all employees across all departments. Violations may "
        "lead to a warning, suspension, or termination depending on severity. Edit "
        "this from the HR Dashboard's Policies tab to add your organisation's full "
        "rules."
    ),
    "limitations": (
        "This portal is provided as-is for attendance, leave, and payroll "
        "administration. Edit this from the HR Dashboard's Policies tab to add your "
        "organisation's specific limitations and disclaimers."
    ),
    "instructions": (
        "Use the sidebar to check in/out, apply for leave, and view your payslips. "
        "Contact HR for any access issues. Edit this from the HR Dashboard's "
        "Policies tab to add your organisation's specific instructions."
    ),
    "posh": (
        "The organisation is committed to providing a safe workplace, free from "
        "harassment. Report any concerns to your HR contact. Edit this from the HR "
        "Dashboard's Policies tab to add your organisation's full POSH policy."
    ),
    "resignation": (
        "Employees must submit a resignation request with their intended last "
        "working day. Edit this from the HR Dashboard's Policies tab to add your "
        "organisation's full resignation/notice-period policy."
    ),
}

# Full starter policy text for a brand-new tenant's Policies tab -- plain
# text (company_policies.body has no HTML rendering), mirroring the same
# section structure/content that used to be hardcoded directly into
# templates/employee_portal.html before that page started reading from this
# table via GET /api/employee/policies. HR/admin can edit any of this for
# real via blueprints/policies.py once the tenant is live.
_POLICY_SEED_CONTENT = [
    ("terms", "Terms & Conditions",
     "Effective Date: These terms are effective from your date of joining and govern "
     "your use of the employee portal.\n\n"
     "1. Acceptance of Terms\n"
     "By accessing and using this portal, you acknowledge that you have read, "
     "understood, and agree to be bound by these Terms and Conditions. Use of this "
     "portal is restricted solely to authorised employees of the organisation.\n\n"
     "2. Employee Credentials & Account Security\n"
     "- Your login credentials are personal and must not be shared with any other "
     "individual, including colleagues.\n"
     "- You are responsible for all activities that occur under your account.\n"
     "- If you suspect unauthorised access, report it immediately to the HR "
     "department or system administrator.\n"
     "- The organisation reserves the right to suspend or terminate access at any "
     "time without prior notice in case of a policy violation.\n\n"
     "3. Accuracy of Information\n"
     "- Employees are required to ensure that all personal details entered in the "
     "portal are accurate and up to date.\n"
     "- Any false or misleading information may result in disciplinary action, "
     "including termination of employment.\n"
     "- Attendance records generated by the system are considered official records "
     "of the organisation.\n\n"
     "4. Attendance & Leave\n"
     "- Employees must mark attendance honestly and accurately within the "
     "designated check-in and check-out window.\n"
     "- Proxy attendance or tampering with attendance data is a serious offence and "
     "may lead to immediate disciplinary action.\n"
     "- Leave applications must be submitted with genuine reasons. Misuse of leave "
     "entitlements will be dealt with strictly.\n"
     "- Approved leave does not guarantee salary credits if leave balance is "
     "exhausted.\n\n"
     "5. Data Privacy\n"
     "- Personal data collected through this portal is used strictly for "
     "employment and payroll administration purposes.\n"
     "- The organisation complies with applicable data protection laws.\n"
     "- Employee data will not be shared with third parties without consent, "
     "except where required by law.\n\n"
     "6. Intellectual Property\n"
     "All content, software, and features of this portal are the intellectual "
     "property of the organisation. Employees may not reproduce, distribute, or "
     "modify any part of this portal without written permission.\n\n"
     "7. Amendments\n"
     "The organisation reserves the right to modify these Terms and Conditions at "
     "any time. Continued use of the portal following any changes constitutes "
     "acceptance of the revised terms.\n\n"
     "8. Governing Law\n"
     "These terms shall be governed by and construed in accordance with the laws "
     "of India. Any disputes shall be subject to the exclusive jurisdiction of the "
     "courts of competent jurisdiction."),
    ("rules", "Rules & Regulations",
     "Note: These rules apply to all employees across all departments and levels "
     "of the organisation. Violations may lead to warning, suspension, or "
     "termination depending on severity.\n\n"
     "1. Punctuality & Attendance\n"
     "- Employees must report to work on time as per their assigned shift "
     "schedule.\n"
     "- Habitual late arrivals (more than 3 times in a month) will attract a "
     "formal warning.\n"
     "- Unauthorised absence without prior approval will result in loss of pay "
     "(LOP) for those days.\n"
     "- Continuous absence for more than 3 working days without notification may "
     "be treated as voluntary abandonment.\n\n"
     "2. Dress Code & Personal Appearance\n"
     "- Employees must maintain a professional appearance at all times during "
     "working hours.\n"
     "- Formals or organisation-issued uniforms (where applicable) must be worn on "
     "all working days.\n"
     "- Casual wear is permitted only on designated casual Fridays or special "
     "occasions announced by HR.\n\n"
     "3. Workplace Conduct\n"
     "- Employees must treat all colleagues, clients, and visitors with respect "
     "and professionalism.\n"
     "- Aggressive language, shouting, or physical altercations are strictly "
     "prohibited.\n"
     "- Use of offensive, discriminatory, or abusive language -- verbal or "
     "written -- will result in immediate disciplinary action.\n"
     "- Gossiping, spreading rumours, or making false statements about colleagues "
     "is not permitted.\n\n"
     "4. Use of Office Resources\n"
     "- Office equipment, internet, and telephone facilities must be used for "
     "official purposes only.\n"
     "- Personal use of office resources is limited and must not interfere with "
     "work duties.\n"
     "- Employees must not download or install unauthorised software on company "
     "devices.\n"
     "- Misuse or damage to office property will be charged to the responsible "
     "employee.\n\n"
     "5. Confidentiality\n"
     "- Employees must maintain strict confidentiality of all business-sensitive "
     "information.\n"
     "- Client data, internal strategies, salary details, and employee records "
     "must not be disclosed to unauthorised persons.\n"
     "- Confidentiality obligations remain in effect even after the termination "
     "of employment.\n\n"
     "6. Social Media Policy\n"
     "- Employees must not post, share, or comment on content that could harm the "
     "organisation's reputation on any social media platform.\n"
     "- Sharing internal documents, meeting screenshots, or client information on "
     "social media is strictly prohibited.\n"
     "- Personal social media activities during work hours should not impact "
     "productivity.\n\n"
     "7. Anti-Corruption & Ethics\n"
     "- Bribery, fraud, or corruption in any form is strictly prohibited and will "
     "lead to immediate termination and legal action.\n"
     "- Employees must declare any conflict of interest to their manager or HR "
     "promptly.\n"
     "- Accepting gifts worth more than Rs. 500 from vendors or clients must be "
     "disclosed to the management.\n\n"
     "8. Disciplinary Process\n"
     "- Level 1: Verbal warning\n"
     "- Level 2: Written warning placed on record\n"
     "- Level 3: Suspension without pay\n"
     "- Level 4: Termination of employment\n\n"
     "The organisation reserves the right to skip steps in cases of serious "
     "misconduct."),
    ("limitations", "Limitations",
     "Important: These limitations exist to ensure a safe, productive, and "
     "legally compliant workplace for all. Non-compliance may result in "
     "disciplinary action.\n\n"
     "1. Working Hours\n"
     "- Standard working hours are as per the assigned shift. Employees must not "
     "extend working hours without prior approval from their manager.\n"
     "- Overtime work must be pre-approved and will be compensated as per the "
     "organisation's overtime policy.\n"
     "- Working more than 12 hours in a single day is not permitted under any "
     "circumstances without written approval from HR.\n\n"
     "2. Leave Limitations\n"
     "- Casual Leave (CL): Maximum 1 day per month (non-accumulative).\n"
     "- Sick Leave (SL): Medical certificate is mandatory for sick leave exceeding "
     "2 consecutive days.\n"
     "- Earned Leave (EL): Maximum carry-forward as per the organisation's leave "
     "policy.\n"
     "- Leave cannot be applied retroactively without manager approval.\n"
     "- Back-to-back leaves adjoining weekends or holidays require specific "
     "approval.\n\n"
     "3. Internet & Technology Use\n"
     "- Accessing adult, gambling, or any illegal content on office networks or "
     "devices is strictly prohibited.\n"
     "- Streaming platforms and heavy personal internet usage during working "
     "hours are not allowed.\n"
     "- Personal devices must not be connected to the organisation's secured "
     "internal network without IT approval.\n"
     "- Employees must not bypass or attempt to bypass network security measures "
     "(firewalls, VPN policies).\n\n"
     "4. Client Interaction Limitations\n"
     "- Employees must not directly negotiate pricing, contracts, or commitments "
     "with clients without authorisation from their manager.\n"
     "- Making verbal or written promises to clients outside the approved scope "
     "of work is not permitted.\n"
     "- All client communication must be documented and archived as per the "
     "communication policy.\n\n"
     "5. Financial Limitations\n"
     "- Expense claims must be submitted within 7 days of incurring the expense "
     "with valid receipts.\n"
     "- Petty cash usage is limited to pre-approved amounts. Exceeding limits "
     "requires written approval.\n"
     "- Employees must not make purchases on the organisation's behalf exceeding "
     "their authorised spending limit.\n\n"
     "6. Workplace Physical Restrictions\n"
     "- Access to restricted areas (server rooms, HR records room, management "
     "cabins) is only permitted with explicit authorisation.\n"
     "- Visitors must be registered at reception and escorted within the "
     "premises at all times.\n"
     "- Photographs or recordings inside the office premises are not permitted "
     "without HR approval.\n\n"
     "7. Communication Limitations\n"
     "- Official communication must only be sent from the organisation's "
     "designated email addresses.\n"
     "- Employees must not speak to the media on behalf of the organisation "
     "without written approval from management.\n"
     "- Internal escalations must follow the designated reporting structure "
     "(immediate manager -> department head -> HR)."),
    ("instructions", "Instructions",
     "For assistance, raise a Support Ticket through this portal or contact HR "
     "directly.\n\n"
     "1. Marking Attendance\n"
     "- Attendance is marked via face recognition or QR code scan at the office "
     "entrance scanner.\n"
     "- Check-in must be done within 30 minutes of shift start to avoid being "
     "marked Late.\n"
     "- You must also mark Check-out before leaving office. Missing check-out "
     "will mark you as Half Day.\n"
     "- If you face any issue with attendance marking, raise a Support Ticket "
     "immediately with the date and reason.\n\n"
     "2. Applying for Leave\n"
     "- Go to Apply Leave in the sidebar.\n"
     "- Select the leave date, type (Casual / Sick / Earned), and provide a "
     "reason.\n"
     "- Ensure you apply at least 1 day in advance for planned leaves.\n"
     "- Emergency leaves (same-day) require you to call or message your manager "
     "directly and then apply through the portal.\n"
     "- Leave status (Pending / Approved / Rejected) can be tracked under Leave "
     "History.\n\n"
     "3. Viewing Pay Slips\n"
     "- Go to Pay Slips in the sidebar to view and download your monthly salary "
     "slips.\n"
     "- Pay slips are generated on the 1st of every month for the previous "
     "month.\n"
     "- If your salary appears incorrect, raise a Support Ticket with details.\n\n"
     "4. Support Tickets\n"
     "- Use the Support Tickets section to report attendance issues, payroll "
     "discrepancies, or any HR-related queries.\n"
     "- Provide a clear subject and detailed description to help the admin "
     "resolve your ticket faster.\n"
     "- Response time is typically within 2 working days.\n"
     "- Do not raise duplicate tickets for the same issue.\n\n"
     "5. Updating Your Profile\n"
     "- Keep your contact number, emergency contact, and bank details updated "
     "under My Profile.\n"
     "- Changes to critical details (PAN, Aadhar, Bank Account) are subject to HR "
     "verification.\n"
     "- Profile photo updates must be done through HR directly.\n\n"
     "6. Changing Your Password\n"
     "- It is recommended to change your portal password every 90 days.\n"
     "- Password must be at least 6 characters long.\n"
     "- Never share your password with anyone, including IT support staff.\n"
     "- If you forget your password, contact the system administrator or HR.\n\n"
     "7. Resignation Process\n"
     "- Resignation requests must be submitted through the Resignation section "
     "with a minimum of 30 days notice (or as per your employment contract).\n"
     "- Ensure all handover documents are completed before your last working "
     "day.\n"
     "- Final settlement and relieving letter will be processed only after "
     "proper handover and clearance from all departments.\n\n"
     "8. Emergency Contacts\n"
     "HR Department: Contact your HR manager for any portal or policy-related "
     "queries.\n"
     "IT Support: Raise a Support Ticket for technical issues with the portal.\n"
     "Payroll: For salary or PF-related queries, raise a Support Ticket with the "
     "category \"Payroll\"."),
    ("posh", "POSH Policy",
     "Legal Mandate: This policy is governed by the Sexual Harassment of Women "
     "at Workplace (Prevention, Prohibition and Redressal) Act, 2013 (POSH Act). "
     "Compliance is mandatory for all employees, contractors, and visitors.\n\n"
     "1. Purpose & Scope\n"
     "This policy aims to provide a safe, respectful, and dignified working "
     "environment free from sexual harassment. It applies to all employees "
     "(permanent, contractual, temporary, interns), clients, customers, and "
     "visitors at the workplace and during work-related events, trips, and "
     "digital communications.\n\n"
     "2. Definition of Sexual Harassment\n"
     "- Physical contact or advances of a sexual nature\n"
     "- Demand or request for sexual favours\n"
     "- Making sexually coloured remarks or jokes\n"
     "- Showing pornography or objectionable material\n"
     "- Unwelcome sexual emails, messages, or social media contact\n"
     "- Gender-based insults, intimidation, or threats\n"
     "- Stalking (physical or digital)\n"
     "- Any other unwelcome physical, verbal, or non-verbal conduct of a sexual "
     "nature\n\n"
     "3. Internal Complaints Committee (ICC)\n"
     "- Presiding Officer: A senior woman employee\n"
     "- Internal Members: At least two employees committed to women's welfare\n"
     "- External Member: An NGO representative or person familiar with women's "
     "issues\n"
     "Contact the ICC through HR or by raising a Support Ticket marked as POSH "
     "Complaint (Confidential).\n\n"
     "4. Filing a Complaint\n"
     "- A complaint must be filed in writing within 3 months of the incident "
     "(extendable in special circumstances).\n"
     "- The complaint may be submitted to the Presiding Officer of the ICC or to "
     "HR directly.\n"
     "- Complaints can be made in writing, by email, or through this portal's "
     "Support Ticket system (marked Confidential).\n"
     "- Where the aggrieved person is unable to file in writing due to "
     "incapacity, the ICC shall render reasonable assistance.\n\n"
     "5. Inquiry Process\n"
     "- Upon receipt of a complaint, the ICC will commence an inquiry within 7 "
     "working days.\n"
     "- Both the complainant and the respondent will be given a fair opportunity "
     "to present their case.\n"
     "- The inquiry will be completed within 90 days of receipt of the "
     "complaint.\n"
     "- All proceedings of the ICC shall be kept strictly confidential.\n\n"
     "6. Interim Relief\n"
     "- Transfer of the aggrieved person or respondent to another department\n"
     "- Granting paid leave to the aggrieved person\n"
     "- Restraint on the respondent from reporting on the performance of the "
     "aggrieved person\n\n"
     "7. Consequences of Sexual Harassment\n"
     "- Written apology\n"
     "- Warning or reprimand placed on record\n"
     "- Withholding of promotion or pay increment\n"
     "- Deduction from salary as compensation to the aggrieved person\n"
     "- Suspension\n"
     "- Termination of employment\n"
     "- Reporting to law enforcement authorities\n\n"
     "8. Protection Against Retaliation\n"
     "Any employee who retaliates, victimises, or intimidates the complainant or "
     "witnesses will face strict disciplinary action, up to and including "
     "termination.\n\n"
     "9. False Complaints\n"
     "Filing a knowingly false complaint or providing false evidence is also a "
     "punishable offence under the Act. However, the inability to prove a "
     "complaint does not constitute a false complaint.\n\n"
     "10. Awareness & Training\n"
     "- All employees are required to complete the mandatory POSH awareness "
     "training provided by HR.\n"
     "- POSH training sessions are conducted at least once a year.\n"
     "- New employees must complete POSH orientation within 30 days of "
     "joining.\n\n"
     "Zero Tolerance Statement: Our organisation has a zero-tolerance policy "
     "towards sexual harassment in any form. Every employee deserves to work in "
     "an environment of respect, dignity, and safety."),
    ("resignation", "Resignation Policy",
     "Important: Resignation is a formal process. Please read this policy "
     "carefully before submitting your resignation through this portal.\n\n"
     "1. Notice Period\n"
     "- Employees are required to serve the notice period as specified in their "
     "employment contract.\n"
     "- Standard notice period is typically 30 to 90 days depending on your role "
     "and grade.\n"
     "- The exact notice period applicable to you is mentioned in your offer "
     "letter or employment agreement.\n"
     "- Failure to serve the notice period may result in forfeiture of dues or "
     "recovery of notice pay.\n\n"
     "2. How to Submit Your Resignation\n"
     "- Use the Resignation section in your portal sidebar to submit your "
     "resignation formally.\n"
     "- Your resignation request will be reviewed and acknowledged by HR within "
     "2 working days.\n"
     "- Do not consider your resignation accepted until you receive a formal "
     "confirmation from HR.\n\n"
     "3. Exit Process\n"
     "- An exit interview will be scheduled with HR before your last working "
     "day.\n"
     "- All company assets (laptop, ID card, access cards, uniforms) must be "
     "returned before the full and final settlement.\n"
     "- Pending tasks must be handed over to your reporting manager or "
     "designated colleague.\n"
     "- Ensure all leaves, expenses, and reimbursements are cleared before your "
     "last day.\n\n"
     "4. Full & Final Settlement\n"
     "- Full and final settlement will be processed within 30-45 days of your "
     "last working day.\n"
     "- Settlement includes remaining salary, encashable leave balance, and any "
     "outstanding reimbursements.\n"
     "- Any dues owed to the organisation (salary advance, notice pay shortfall) "
     "will be deducted from the settlement.\n\n"
     "5. Experience & Relieving Letter\n"
     "- A relieving letter and experience certificate will be issued after "
     "successful completion of the exit process.\n"
     "- Documents will be provided only after all dues are cleared and company "
     "property is returned.\n\n"
     "6. Withdrawal of Resignation\n"
     "An employee may request to withdraw their resignation by contacting HR, "
     "provided the withdrawal is made before HR issues the formal acceptance. "
     "Withdrawal is subject to management discretion.\n\n"
     "Note: Absconding (leaving without completing the notice period and "
     "without informing HR) will be treated as misconduct and may affect your "
     "full and final settlement, reference letters, and future employment "
     "prospects."),
]


def _run_company_policies_seed_v2(cursor, db):
    """One-time follow-up to company_policies_seed_v1 (since removed --
    new companies now start with no policies and write their own from
    Organization -> Policies; this v2 still upgrades older tenants that got
    the v1 starters). That seed shipped
    with only a one-paragraph placeholder per category (now
    _POLICY_SEED_CONTENT_V1_LEGACY) because templates/employee_portal.html
    still showed its own hardcoded rich text at the time and nothing read
    company_policies yet. Now that the employee portal reads this table
    directly (GET /api/employee/policies), replace that placeholder with
    the full starter text -- but only for rows that still hold the
    original placeholder verbatim, so a tenant whose HR already wrote a
    real policy is left untouched."""
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='company_policies_seed_v2'")
        if cursor.fetchone():
            return
        for category, title, body in _POLICY_SEED_CONTENT:
            legacy_body = _POLICY_SEED_CONTENT_V1_LEGACY.get(category)
            if legacy_body is None:
                continue
            cursor.execute(
                "UPDATE company_policies SET body=%s WHERE category=%s AND body=%s",
                (body, category, legacy_body)
            )
        cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('company_policies_seed_v2')")
        db.commit()
    except Exception as exc:
        app_log.warning("Migration 'company_policies_seed_v2' failed: %s", exc, exc_info=True)


def _run_pii_widen_migration_v1(cursor, db):
    # Widen employee PII columns to TEXT so they can hold Fernet-encrypted
    # values (utils/helpers.py's encrypt_pii) -- a base64 Fernet token has
    # ~100+ chars of fixed IV/HMAC/timestamp overhead even for a 2-character
    # plaintext like a blood group, which overflows every VARCHAR(N) column
    # below (and dob's native DATE type can't hold text/ciphertext at all).
    # Existing plaintext rows keep working unchanged -- decrypt_pii() already
    # falls back to returning its input as-is when it isn't a valid Fernet
    # token, so nothing needs to be migrated, only re-saved to pick up
    # encryption going forward, same pattern already used for aadhar/pan/
    # bank_account/bank_ifsc/uan.
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='employee_pii_columns_to_text_v1'")
        if not cursor.fetchone():
            _pii_widen_stmts = [
                "ALTER TABLE employees ALTER COLUMN dob TYPE TEXT USING dob::text",
                "ALTER TABLE employees ALTER COLUMN gender TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN blood_group TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN city TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN state TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN pincode TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN emergency_contact_name TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN emergency_contact_phone TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN emergency_contact_relation TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN bank_name TYPE TEXT",
            ]
            for stmt in _pii_widen_stmts:
                try:
                    cursor.execute(stmt)
                    db.commit()
                except Exception:
                    db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('employee_pii_columns_to_text_v1')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'employee_pii_columns_to_text_v1' failed: %s", exc, exc_info=True)


def _run_pii_widen_migration_v2(cursor, db):
    """v1 above widened 10 of the 15 Fernet-encrypted employee columns
    to TEXT. These 5 were missed -- encrypt_pii() output runs 100+
    chars for any input, but aadhar_number/bank_ifsc stayed
    VARCHAR(20) and bank_account/uan_number VARCHAR(30), so
    registering an employee with any of these fields filled in
    raised psycopg2.StringDataRightTruncation ('value too long for
    type character varying(20)') -- a real 500 on a standard field
    for an Indian payroll system, found while verifying the
    registration flow end-to-end rather than just reading the code."""
    # v1 above widened 10 of the 15 Fernet-encrypted employee columns to TEXT.
    # These 5 were missed -- encrypt_pii() output runs 100+ chars for any
    # input, but aadhar_number/bank_ifsc stayed VARCHAR(20) and
    # bank_account/uan_number VARCHAR(30), so registering an employee with
    # any of these fields filled in raised psycopg2.StringDataRightTruncation
    # ("value too long for type character varying(20)") -- a real 500 on a
    # standard field for an Indian payroll system, found while verifying the
    # registration flow end-to-end rather than just reading the code.
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='employee_pii_columns_to_text_v2'")
        if not cursor.fetchone():
            _pii_widen_stmts_v2 = [
                "ALTER TABLE employees ALTER COLUMN aadhar_number TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN pan_number TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN bank_account TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN bank_ifsc TYPE TEXT",
                "ALTER TABLE employees ALTER COLUMN uan_number TYPE TEXT",
            ]
            for stmt in _pii_widen_stmts_v2:
                try:
                    cursor.execute(stmt)
                    db.commit()
                except Exception:
                    db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('employee_pii_columns_to_text_v2')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'employee_pii_columns_to_text_v2' failed: %s", exc, exc_info=True)


def _run_fk_backstop_migration(cursor, db):
    """Every employee_id/company_id column below was previously
    enforced only by application code (each delete path manually
    cleaning up related tables) with no FK constraint backing it, so a
    bug or a crash mid-delete could silently orphan rows instead of
    being caught or cascaded. Added NOT VALID so existing orphans (if
    any) from before this migration don't block it -- only rows
    inserted/updated from now on are checked, closing the gap going
    forward without a risky retroactive cleanup of historical data."""
    # Referential-integrity backstop. Every employee_id/company_id column
    # below was previously enforced only by application code (each delete
    # path manually cleaning up related tables) with no FK constraint
    # backing it, so a bug or a crash mid-delete could silently orphan rows
    # instead of being caught or cascaded. Added NOT VALID so existing
    # orphans (if any) from before this migration don't block it -- only
    # rows inserted/updated from now on are checked, closing the gap going
    # forward without a risky retroactive cleanup of historical data.
    try:
        cursor.execute("SELECT 1 FROM _applied_migrations WHERE name='fk_constraints_v1'")
        if not cursor.fetchone():
            _fk_stmts = [
                ("attendance", "employee_id", "employees", "employee_id", "CASCADE"),
                ("salary_config", "employee_id", "employees", "employee_id", "CASCADE"),
                ("leave_requests", "employee_id", "employees", "employee_id", "CASCADE"),
                ("resignation_requests", "employee_id", "employees", "employee_id", "CASCADE"),
                ("notifications", "employee_id", "employees", "employee_id", "CASCADE"),
                ("tickets", "employee_id", "employees", "employee_id", "CASCADE"),
                ("employee_incentives", "employee_id", "employees", "employee_id", "CASCADE"),
                ("employee_experience", "employee_id", "employees", "employee_id", "CASCADE"),
                ("employee_education", "employee_id", "employees", "employee_id", "CASCADE"),
                ("leave_balances", "employee_id", "employees", "employee_id", "CASCADE"),
                ("employee_documents", "employee_id", "employees", "employee_id", "CASCADE"),
                ("performance_reviews", "employee_id", "employees", "employee_id", "CASCADE"),
                ("overtime_records", "employee_id", "employees", "employee_id", "CASCADE"),
                ("compoff_balance", "employee_id", "employees", "employee_id", "CASCADE"),
                ("employee_onboarding", "employee_id", "employees", "employee_id", "CASCADE"),
                ("employees", "company_id", "companies", "id", "SET NULL"),
                ("shifts", "company_id", "companies", "id", "SET NULL"),
                ("break_config", "company_id", "companies", "id", "SET NULL"),
            ]
            for _tbl, _col, _ref_tbl, _ref_col, _on_delete in _fk_stmts:
                _fk_name = f"fk_{_tbl}_{_col}"
                try:
                    cursor.execute(
                        f'ALTER TABLE {_tbl} ADD CONSTRAINT {_fk_name} '
                        f'FOREIGN KEY ({_col}) REFERENCES {_ref_tbl}({_ref_col}) '
                        f'ON DELETE {_on_delete} NOT VALID'
                    )
                    db.commit()
                except Exception:
                    db.rollback()
            cursor.execute("INSERT INTO _applied_migrations (name) VALUES ('fk_constraints_v1')")
            db.commit()
    except Exception as exc:
        app_log.warning("Migration 'fk_constraints_v1' failed: %s", exc, exc_info=True)
