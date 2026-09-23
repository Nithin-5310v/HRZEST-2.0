import psycopg2

from extensions import app_log


_UPDATED_AT_TRIGGER_FN = """
    CREATE OR REPLACE FUNCTION _set_updated_at() RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""


def _attach_updated_at_trigger(cursor, table):
    cursor.execute(f'DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}')
    cursor.execute(f"""
        CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION _set_updated_at()
    """)


def _init_core_tables(cursor, db):
    """Create every base table (and its triggers/seed rows) this app
     needs, in dependency order -- e.g. company_settings before the
     migrations below that ALTER it. Idempotent: every statement is
     CREATE TABLE IF NOT EXISTS, safe to re-run on every startup."""
    cursor.execute(_UPDATED_AT_TRIGGER_FN)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) UNIQUE NOT NULL,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(150) DEFAULT NULL,
            face_image VARCHAR(255),
            qr_code VARCHAR(255)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            date DATE NOT NULL,
            login_time TIME DEFAULT NULL,
            logout_time TIME DEFAULT NULL,
            status VARCHAR(50) DEFAULT NULL,
            logout_status VARCHAR(50) DEFAULT NULL,
            attendance_type VARCHAR(50) DEFAULT NULL,
            UNIQUE (employee_id, date)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holidays (
            id SERIAL PRIMARY KEY,
            date DATE UNIQUE NOT NULL,
            name VARCHAR(100) NOT NULL
        )
    """)
    # Company-wide policies (Terms, Rules, POSH, etc.) -- previously just
    # hardcoded static text in templates/employee_portal.html; this table
    # backs blueprints/policies.py's admin+HR CRUD (surfaced as the HR
    # Dashboard's Policies tab) and utils/helpers.py's seeded copy of that
    # old hardcoded text (see _seed_defaults_and_admin). No company_id
    # column, matching the holidays table above -- policies, like
    # holidays, apply company-wide, not per assigned_hr_username.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_policies (
            id SERIAL PRIMARY KEY,
            category VARCHAR(50) NOT NULL,
            title VARCHAR(200) NOT NULL,
            body TEXT NOT NULL,
            is_published SMALLINT DEFAULT 1,
            sort_order INT DEFAULT 0,
            created_by VARCHAR(50),
            updated_by VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _attach_updated_at_trigger(cursor, "company_policies")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS salary_config (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) UNIQUE NOT NULL,
            salary_per_day DECIMAL(10,2) DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payroll_config (
            id SERIAL PRIMARY KEY,
            pf_employee_pct DECIMAL(5,2) DEFAULT 12.00,
            pf_employer_pct DECIMAL(5,2) DEFAULT 12.00,
            professional_tax DECIMAL(8,2) DEFAULT 200.00,
            tds_annual_pct DECIMAL(5,2) DEFAULT 0.00,
            pf_basic_cap DECIMAL(10,2) DEFAULT 15000.00
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            email VARCHAR(150) DEFAULT NULL,
            reset_token VARCHAR(64) DEFAULT NULL,
            reset_token_expiry TIMESTAMP DEFAULT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_config (
            id SERIAL PRIMARY KEY,
            smtp_host VARCHAR(150) NOT NULL,
            smtp_port INT NOT NULL DEFAULT 587,
            smtp_user VARCHAR(150) NOT NULL,
            smtp_pass VARCHAR(255) NOT NULL,
            from_name VARCHAR(100) DEFAULT 'HR Department',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _attach_updated_at_trigger(cursor, "email_config")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leave_requests (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            leave_date DATE NOT NULL,
            reason VARCHAR(500) NOT NULL,
            status VARCHAR(20) DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            recipient_type VARCHAR(20) NOT NULL CHECK (recipient_type IN ('admin', 'employee')),
            employee_id VARCHAR(50) NULL,
            title VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Expo push tokens -- one row per device install. owner_type/owner_id
    # mirrors notifications.recipient_type/employee_id: 'employee' rows are
    # keyed by employee_id, 'admin' rows by admin_users.username (covers
    # admin/hr/manager alike, same as notifications.recipient_type='admin'
    # already being a shared, not per-user, inbox). UNIQUE on the token
    # alone, not (owner_type, owner_id, token) -- a physical device can only
    # be signed in as one identity at a time, so re-registering the same
    # token under a different owner (a logout/login as someone else on the
    # same phone) should reassign that row, not create a duplicate that
    # would double-send or send to the wrong stale owner.
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS resignation_requests (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            last_working_day DATE NOT NULL,
            reason TEXT NOT NULL,
            status VARCHAR(20) DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            category VARCHAR(100) NOT NULL,
            subject VARCHAR(255) NOT NULL,
            description TEXT NOT NULL,
            priority VARCHAR(20) DEFAULT 'Medium',
            status VARCHAR(30) DEFAULT 'Open',
            admin_response TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _attach_updated_at_trigger(cursor, "tickets")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shifts (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            start_time TIME NOT NULL,
            half_time  TIME NOT NULL,
            end_time   TIME NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS announcements (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            content TEXT NOT NULL,
            priority VARCHAR(20) DEFAULT 'Normal' CHECK (priority IN ('Normal','Important','Urgent')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS break_config (
            id SERIAL PRIMARY KEY,
            break_name VARCHAR(100) NOT NULL,
            break_time TIME NOT NULL,
            duration_minutes INT NOT NULL DEFAULT 10,
            is_active SMALLINT DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS incentive_goals (
            id SERIAL PRIMARY KEY,
            title VARCHAR(150) NOT NULL,
            description TEXT,
            incentive_amount DECIMAL(10,2) NOT NULL DEFAULT 0,
            is_active SMALLINT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_incentives (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            goal_id INT NOT NULL,
            month INT NOT NULL,
            year INT NOT NULL,
            amount DECIMAL(10,2) NOT NULL DEFAULT 0,
            notes TEXT,
            awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_experience (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            company VARCHAR(150) NOT NULL,
            designation VARCHAR(100) NOT NULL,
            from_year VARCHAR(10) NOT NULL,
            to_year VARCHAR(10) DEFAULT NULL,
            is_current SMALLINT DEFAULT 0,
            description TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_education (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            degree VARCHAR(150) NOT NULL,
            institution VARCHAR(200) NOT NULL,
            year_of_passing VARCHAR(10) DEFAULT NULL,
            percentage VARCHAR(20) DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leave_types (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            annual_quota INT NOT NULL DEFAULT 12,
            is_paid SMALLINT DEFAULT 1,
            is_active SMALLINT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leave_balances (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            leave_type_id INT NOT NULL,
            year INT NOT NULL,
            total_days INT NOT NULL DEFAULT 0,
            used_days DECIMAL(4,1) NOT NULL DEFAULT 0,
            UNIQUE (employee_id, leave_type_id, year)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_documents (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            doc_type VARCHAR(100) NOT NULL,
            original_name VARCHAR(255) NOT NULL,
            stored_name VARCHAR(255) NOT NULL,
            uploaded_by VARCHAR(20) DEFAULT 'admin',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS performance_reviews (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            quarter SMALLINT NOT NULL,
            year INT NOT NULL,
            overall_rating DECIMAL(3,1) DEFAULT 0,
            reviewer_feedback TEXT,
            employee_comment TEXT,
            status VARCHAR(20) DEFAULT 'Draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (employee_id, quarter, year)
        )
    """)
    _attach_updated_at_trigger(cursor, "performance_reviews")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS performance_kpis (
            id SERIAL PRIMARY KEY,
            review_id INT NOT NULL,
            kpi_title VARCHAR(200) NOT NULL,
            description TEXT,
            target VARCHAR(200),
            achievement VARCHAR(200),
            weight INT DEFAULT 20,
            rating SMALLINT DEFAULT 0,
            comments TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hike_config (
            id SERIAL PRIMARY KEY,
            label VARCHAR(80) NOT NULL,
            min_rating DECIMAL(3,1) NOT NULL,
            max_rating DECIMAL(3,1) NOT NULL,
            hike_pct DECIMAL(5,2) DEFAULT 0,
            incentive_pct DECIMAL(5,2) DEFAULT 0,
            color VARCHAR(20) DEFAULT '#1e3a8a'
        )
    """)
    cursor.execute("SELECT COUNT(*) FROM hike_config")
    if cursor.fetchone()[0] == 0:
        for _lbl, _mn, _mx, _hp, _ip, _clr in [
            ("Exceptional", 4.5, 5.0, 20.00, 15.00, "#15803d"),
            ("Exceeds Expectations", 4.0, 4.4, 15.00, 10.00, "#2563eb"),
            ("Meets Expectations", 3.0, 3.9, 10.00, 5.00, "#7c3aed"),
            ("Needs Improvement", 2.0, 2.9, 5.00, 0.00, "#d97706"),
            ("Below Expectations", 0.0, 1.9, 0.00, 0.00, "#dc2626"),
        ]:
            cursor.execute(
                "INSERT INTO hike_config (label, min_rating, max_rating, hike_pct, incentive_pct, color) VALUES (%s,%s,%s,%s,%s,%s)",
                (_lbl, _mn, _mx, _hp, _ip, _clr)
            )
        db.commit()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS overtime_records (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            date DATE NOT NULL,
            shift_end TIME NOT NULL,
            actual_logout TIME NOT NULL,
            ot_minutes INT NOT NULL DEFAULT 0,
            ot_pay DECIMAL(10,2) DEFAULT 0,
            status VARCHAR(20) DEFAULT 'Pending',
            notes TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (employee_id, date)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_templates (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            is_active SMALLINT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_template_tasks (
            id SERIAL PRIMARY KEY,
            template_id INT NOT NULL,
            task_title VARCHAR(300) NOT NULL,
            task_description TEXT,
            requires_document SMALLINT DEFAULT 0,
            due_days INT DEFAULT 7,
            sort_order INT DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_onboarding (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            template_id INT NOT NULL,
            assigned_date DATE NOT NULL,
            due_date DATE,
            status VARCHAR(20) DEFAULT 'In Progress',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_onboarding_tasks (
            id SERIAL PRIMARY KEY,
            onboarding_id INT NOT NULL,
            template_task_id INT NOT NULL,
            employee_id VARCHAR(50) NOT NULL,
            task_title VARCHAR(300) NOT NULL,
            task_description TEXT,
            requires_document SMALLINT DEFAULT 0,
            due_days INT DEFAULT 7,
            status VARCHAR(20) DEFAULT 'Pending',
            completed_at TIMESTAMP NULL,
            document_path VARCHAR(500),
            admin_notes TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offer_letters (
            id SERIAL PRIMARY KEY,
            onboarding_id INT NOT NULL,
            employee_id VARCHAR(50) NOT NULL,
            designation VARCHAR(150),
            department VARCHAR(150),
            work_location VARCHAR(200),
            monthly_ctc DECIMAL(12,2) DEFAULT 0,
            joining_date DATE,
            offer_valid_until DATE,
            probation_months INT DEFAULT 6,
            reporting_to VARCHAR(150),
            additional_notes TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP DEFAULT NULL,
            status VARCHAR(20) DEFAULT 'draft',
            notice_period_days INT DEFAULT 30,
            candidate_address TEXT
        )
    """)
    for _col, _sql in [
        ("notice_period_days", "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS notice_period_days INT DEFAULT 30"),
        ("candidate_address", "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS candidate_address TEXT"),
        ("response_token", "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS response_token VARCHAR(64) DEFAULT NULL"),
        ("candidate_response", "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS candidate_response VARCHAR(20) DEFAULT NULL"),
        ("responded_at", "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS responded_at TIMESTAMP DEFAULT NULL"),
        ("response_token_expiry", "ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS response_token_expiry TIMESTAMP DEFAULT NULL"),
    ]:
        try:
            cursor.execute(_sql)
            db.commit()
        except psycopg2.Error:
            db.rollback()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            actor VARCHAR(100) NOT NULL,
            actor_type VARCHAR(20) DEFAULT 'admin',
            action VARCHAR(150) NOT NULL,
            target_table VARCHAR(100),
            target_id VARCHAR(100),
            detail TEXT,
            ip_address VARCHAR(45),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_actor ON audit_logs (actor)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_action ON audit_logs (action)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_created ON audit_logs (created_at)")
    # blueprints/email_blast.py's INSERT INTO broadcast_emails had no
    # matching CREATE TABLE anywhere in the schema -- every admin email-blast
    # request (broadcast to all/department/individual employees) failed at
    # the enqueue step with UndefinedTable, silently returning a 500 despite
    # otherwise looking fully built. Audit record of one broadcast dispatch,
    # separate from the per-recipient rows it fans out into email_queue.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_emails (
            id SERIAL PRIMARY KEY,
            sender_username VARCHAR(150) NOT NULL,
            target_type VARCHAR(20) NOT NULL,
            target_value VARCHAR(150),
            subject VARCHAR(500) NOT NULL,
            body_snippet VARCHAR(200),
            recipient_count INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id SERIAL PRIMARY KEY,
            identifier VARCHAR(150) NOT NULL,
            attempt_type VARCHAR(20) DEFAULT 'admin',
            failed_count INT DEFAULT 0,
            last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            locked_until TIMESTAMP DEFAULT NULL,
            UNIQUE (identifier, attempt_type)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_lockouts (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL,
            date DATE NOT NULL,
            failed_count INT NOT NULL DEFAULT 0,
            locked SMALLINT NOT NULL DEFAULT 0,
            lock_reason VARCHAR(200) DEFAULT NULL,
            locked_at TIMESTAMP DEFAULT NULL,
            unlocked_by VARCHAR(50) DEFAULT NULL,
            UNIQUE (employee_id, date)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS known_login_ips (
            id SERIAL PRIMARY KEY,
            identifier VARCHAR(150) NOT NULL,
            attempt_type VARCHAR(20) DEFAULT 'admin',
            ip_address VARCHAR(45) NOT NULL,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (identifier, attempt_type, ip_address)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_risk (
            sid          VARCHAR(64) PRIMARY KEY,
            identifier   VARCHAR(150) NOT NULL,
            attempt_type VARCHAR(20) DEFAULT 'admin',
            score        INT NOT NULL DEFAULT 0,
            status       VARCHAR(20) NOT NULL DEFAULT 'active',
            last_reason  VARCHAR(300) DEFAULT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_events (
            id          SERIAL PRIMARY KEY,
            event_type  VARCHAR(80) NOT NULL,
            level       VARCHAR(10) NOT NULL,
            message     VARCHAR(500) NOT NULL,
            identifier  VARCHAR(150),
            ip          VARCHAR(64),
            path        VARCHAR(255),
            method      VARCHAR(10),
            extra_json  TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_events_created ON security_events (created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_security_events_type ON security_events (event_type)")

    # Immutable audit trail: audit_logs (data/config changes) and
    # security_events (auth/access events) must be append-only -- a plain
    # table is only as tamper-resistant as every future line of code that
    # touches it, which isn't a guarantee. This trigger makes tampering
    # (or an attacker who reaches DB access) unable to alter or erase
    # history no matter what app code does, short of dropping the trigger
    # itself (a superuser-only DDL action, not something an app-level bug
    # or compromised web-tier credential can do).
    cursor.execute("""
        CREATE OR REPLACE FUNCTION _reject_audit_mutation() RETURNS TRIGGER AS $$
        BEGIN
            -- Deliberate, narrow escape hatch for test-fixture cleanup only:
            -- the app itself never issues `SET audit.bypass`, so this
            -- doesn't weaken production immutability -- a session has to
            -- explicitly opt in via raw SQL the app never sends.
            IF current_setting('audit.bypass', true) = 'on' THEN
                RETURN COALESCE(NEW, OLD);
            END IF;
            RAISE EXCEPTION 'audit tables are append-only: % on % is not permitted', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for _tbl in ("audit_logs", "security_events"):
        cursor.execute(f'DROP TRIGGER IF EXISTS trg_{_tbl}_immutable ON {_tbl}')
        cursor.execute(f"""
            CREATE TRIGGER trg_{_tbl}_immutable
            BEFORE UPDATE OR DELETE ON {_tbl}
            FOR EACH ROW EXECUTE FUNCTION _reject_audit_mutation()
        """)
    db.commit()

    # Application-layer IP ban list -- the SOC dashboard's "one-click ban"
    # tactical mitigation. Not a substitute for a real edge/WAF ban (e.g.
    # Cloudflare), since a request still costs a TLS handshake + one Python
    # request cycle before _enforce_ip_ban rejects it -- but it needs no
    # cloud API credentials and takes effect immediately app-wide, with no
    # redeploy needed.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS banned_ips (
            ip         VARCHAR(45) PRIMARY KEY,
            reason     VARCHAR(300),
            banned_by  VARCHAR(150),
            banned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP DEFAULT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_queue (
            id           SERIAL PRIMARY KEY,
            to_email     VARCHAR(255) NOT NULL,
            subject      VARCHAR(500) NOT NULL,
            html_body    TEXT   NOT NULL,
            attachment_b64 TEXT   DEFAULT NULL,
            attachment_filename VARCHAR(255) DEFAULT NULL,
            status       VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending','sending','done','failed')),
            attempts     SMALLINT DEFAULT 0,
            last_error   TEXT DEFAULT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at      TIMESTAMP DEFAULT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_eq_status ON email_queue (status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_eq_created ON email_queue (created_at)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id SERIAL PRIMARY KEY,
            year INT NOT NULL,
            month INT NOT NULL,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_by VARCHAR(100),
            email_count INT DEFAULT 0,
            UNIQUE (year, month)
        )
    """)
    # ── Salary disbursement ───────────────────────────────────────────────
    # payout_bank_config: singleton per tenant, same shape/upsert convention
    # as email_config above (DELETE+INSERT, one encrypted-credentials row) --
    # this is the company's OWN source bank account salary is paid FROM, not
    # an employee's. account_number is encrypted at rest via encrypt_pii,
    # same as employees.bank_account. Also carries the recurring-schedule
    # settings (day of month, lead time, enabled) since those are equally
    # tenant-wide singleton config -- no reason to split into a second table.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payout_bank_config (
            id SERIAL PRIMARY KEY,
            account_holder_name VARCHAR(150) NOT NULL,
            bank_name VARCHAR(150) NOT NULL,
            account_number VARCHAR(255) NOT NULL,
            ifsc_code VARCHAR(20) NOT NULL,
            disbursement_day_of_month INT DEFAULT 1 CHECK (disbursement_day_of_month BETWEEN 1 AND 28),
            approval_lead_days INT DEFAULT 2,
            enabled SMALLINT DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _attach_updated_at_trigger(cursor, "payout_bank_config")
    # salary_disbursement_runs / _items: the real per-employee batch ledger
    # payroll_runs never had (payroll_runs is just a year/month lock marker
    # with an aggregate count, no line items) -- see prepare_pending_disbursements()
    # in blueprints/disbursement.py for the state machine this backs.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS salary_disbursement_runs (
            id SERIAL PRIMARY KEY,
            year INT NOT NULL,
            month INT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending_approval'
                CHECK (status IN ('pending_approval','approved','processing','completed','failed','cancelled')),
            total_amount DECIMAL(14,2) DEFAULT 0,
            employee_count INT DEFAULT 0,
            prepared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_by VARCHAR(100),
            approved_at TIMESTAMP,
            UNIQUE (year, month)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS salary_disbursement_items (
            id SERIAL PRIMARY KEY,
            run_id INT NOT NULL REFERENCES salary_disbursement_runs(id) ON DELETE CASCADE,
            employee_id VARCHAR(50) NOT NULL,
            amount DECIMAL(12,2) NOT NULL DEFAULT 0,
            bank_last4 VARCHAR(4),
            status VARCHAR(30) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','sent','failed','stub_not_configured','missing_bank_details')),
            payout_reference VARCHAR(255),
            email_sent SMALLINT DEFAULT 0,
            error_message VARCHAR(500)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sdi_run ON salary_disbursement_items (run_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS compoff_balance (
            id SERIAL PRIMARY KEY,
            employee_id VARCHAR(50) NOT NULL UNIQUE,
            earned_minutes INT DEFAULT 0,
            used_minutes INT DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _attach_updated_at_trigger(cursor, "compoff_balance")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            token VARCHAR(64) PRIMARY KEY,
            token_type VARCHAR(20) NOT NULL DEFAULT 'admin',
            identity VARCHAR(100) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    # Single-use, short-lived tokens that let the mobile app (Bearer-token
    # auth only) hand a WebView a real session-cookie admin login, so
    # web-only pages -- currently just /settings/seats (blueprints/seats.py,
    # blueprints/auto_debit.py) -- can be reused as-is inside the app rather
    # than reimplementing Razorpay Checkout natively. Same hash-and-expire
    # shape as admin_users.reset_token (blueprints/auth.py's
    # admin_forgot_password) -- see /api/mobile/web_session_link and
    # /mobile_bridge_login/<token> in blueprints/core.py.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mobile_bridge_tokens (
            token_hash VARCHAR(64) PRIMARY KEY,
            admin_username VARCHAR(100) NOT NULL,
            target_path VARCHAR(100) NOT NULL DEFAULT '/settings/seats',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    # Pre-existing tables created before target_path existed --
    # CREATE TABLE IF NOT EXISTS above is a no-op against them.
    cursor.execute("ALTER TABLE mobile_bridge_tokens ADD COLUMN IF NOT EXISTS target_path VARCHAR(100) NOT NULL DEFAULT '/settings/seats'")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mobile_biometric_proofs (
            employee_id VARCHAR(50) PRIMARY KEY,
            nonce VARCHAR(64) DEFAULT NULL,
            nonce_expires_at TIMESTAMP DEFAULT NULL,
            verified_at TIMESTAMP DEFAULT NULL
        )
    """)
    db.commit()
    # Seed default leave types if empty
    cursor.execute("SELECT COUNT(*) FROM leave_types")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO leave_types (name, annual_quota, is_paid) VALUES (%s,%s,%s)",
            [
                ("Casual Leave", 12, 1),
                ("Sick Leave", 12, 1),
                ("Earned Leave", 15, 1),
                ("Maternity Leave", 90, 1),
                ("Paternity Leave", 5, 1),
                ("Comp-off", 0, 1),
            ]
        )
        db.commit()
    # Ensure Comp-off leave type exists
    cursor.execute("SELECT id FROM leave_types WHERE name='Comp-off' LIMIT 1")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO leave_types (name, annual_quota, is_paid) VALUES ('Comp-off', 0, 1)")
        db.commit()
    # Seed default breaks if table is empty
    cursor.execute("SELECT COUNT(*) FROM break_config")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO break_config (break_name, break_time, duration_minutes) VALUES (%s, %s, %s)",
            [
                ("Coffee Break 1", "11:00:00", 10),
                ("Lunch Break", "13:00:00", 60),
                ("Coffee Break 2", "16:00:00", 10),
            ]
        )
        db.commit()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            code VARCHAR(20) DEFAULT NULL,
            working_days VARCHAR(30) DEFAULT 'Mon,Tue,Wed,Thu,Fri',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_feature_settings (
            company_id INT PRIMARY KEY,
            face_auth_enabled  SMALLINT DEFAULT 1,
            qr_enabled         SMALLINT DEFAULT 1,
            fingerprint_enabled SMALLINT DEFAULT 0,
            geo_enabled        SMALLINT DEFAULT 0,
            geo_radius         INT DEFAULT 300,
            pin_enabled        SMALLINT DEFAULT 1,
            biometric_enabled  SMALLINT DEFAULT 0,
            notify_leave       SMALLINT DEFAULT 1,
            notify_payslip     SMALLINT DEFAULT 1,
            notify_resignation SMALLINT DEFAULT 1,
            notify_doc_expiry  SMALLINT DEFAULT 1,
            session_timeout    INT DEFAULT 30,
            late_deduction_pct DECIMAL(5,2) DEFAULT 10.00,
            half_day_deduction_pct DECIMAL(5,2) DEFAULT 50.00,
            grace_minutes      INT DEFAULT 15,
            holiday_pay        VARCHAR(20) DEFAULT 'paid' CHECK (holiday_pay IN ('paid','unpaid')),
            leave_pay          VARCHAR(20) DEFAULT 'exclude' CHECK (leave_pay IN ('exclude','absent')),
            shift_start        TIME DEFAULT '09:00:00',
            shift_half         TIME DEFAULT '13:00:00',
            shift_end          TIME DEFAULT '18:00:00',
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)
    db.commit()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS id_card_templates (
            company_id INT PRIMARY KEY,
            front_image VARCHAR(255) DEFAULT NULL,
            back_image VARCHAR(255) DEFAULT NULL,
            fields TEXT DEFAULT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)
    db.commit()

    # Create company_settings table (must precede the migration loop below,
    # which ALTERs this table -- on a fresh install with nothing to migrate
    # from, an ALTER before the table exists silently no-ops instead of
    # erroring, so column order here isn't just cosmetic).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_settings (
            id SERIAL PRIMARY KEY,
            company_name VARCHAR(200) DEFAULT 'My Company',
            company_tagline VARCHAR(300) DEFAULT 'HRzest.com',
            company_logo VARCHAR(255) DEFAULT NULL,
            currency_symbol VARCHAR(10) DEFAULT '₹',
            timezone VARCHAR(60) DEFAULT 'Asia/Kolkata',
            setup_done SMALLINT DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _attach_updated_at_trigger(cursor, "company_settings")
    db.commit()
    # Add default shift columns if not present
    for col, default in [("shift_start", "09:00:00"), ("shift_half", "13:00:00"), ("shift_end", "18:00:00")]:
        try:
            cursor.execute(f"ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS {col} TIME DEFAULT '{default}'")  # nosec B608 -- col/default come from the fixed literal list above, never user input  # noqa: E501
            db.commit()
        except Exception as exc:
            app_log.warning("Migration: ALTER company_settings ADD COLUMN %s failed: %s", col, exc, exc_info=True)
