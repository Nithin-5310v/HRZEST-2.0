from database import get_db_connection
from extensions import app_log

from .core_tables import _UPDATED_AT_TRIGGER_FN, _attach_updated_at_trigger


def init_master_db():
    """Create the att_master tenant-registry schema and its tenants table if
    they don't exist. Postgres has no mid-connection database switch, so this
    is a schema within the shared database now, not a separate physical
    database the way MySQL's att_master was."""
    try:
        # Schema must exist before get_master_db() can SET search_path to it,
        # so this first connection stays on the default (public) schema --
        # get_db_connection() now always resets search_path explicitly on
        # every borrow, so it's safe to use here without leaking att_master
        # onto whichever connection the pool hands out next.
        db = get_db_connection()
        cur = db.cursor()
        cur.execute('CREATE SCHEMA IF NOT EXISTS att_master')
        db.commit()
        cur.close()
        db.close()

        from database import get_master_db
        db = get_master_db()
        cur = db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id SERIAL PRIMARY KEY,
                company_name VARCHAR(200) NOT NULL,
                subdomain VARCHAR(100) UNIQUE NOT NULL,
                db_name VARCHAR(100) UNIQUE NOT NULL,
                admin_email VARCHAR(200) DEFAULT NULL,
                plan VARCHAR(50) DEFAULT 'starter',
                payment_option VARCHAR(20) DEFAULT 'online',
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Pre-existing masters created before payment_option existed --
        # CREATE TABLE IF NOT EXISTS above is a no-op against them.
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS payment_option VARCHAR(20) DEFAULT 'online'")
        # Payment-dunning state, separate from `status` (which stays purely
        # admin-initiated active/suspended). 'current' = paid up; 'grace' =
        # unpaid past the due date but still inside the 5-day deadline
        # (fully functional, just warned); 'locked' = grace period expired
        # with no payment -- login still works (_resolve_tenant() below
        # doesn't gate on this) but _enforce_billing_lock() blocks every
        # state-changing request until a payment clears it automatically
        # (blueprints/billing_dunning.py's Razorpay webhook), no admin
        # action required either way.
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS billing_state VARCHAR(20) NOT NULL DEFAULT 'current'")
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS grace_period_ends_at TIMESTAMP DEFAULT NULL")
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP DEFAULT NULL")
        # Trial lifecycle (blueprints/trial_billing.py, TRIAL_DURATION_DAYS).
        # subscription_status defaults to 'active' so every pre-existing
        # tenant is unaffected -- only a tenant provisioned through the
        # trial signup path (blueprints/org.py's create_org_setup_trial_confirm())
        # is ever set to 'trialing'. trial_start_date/trial_end_date stay
        # NULL until the tenant's FIRST admin login (app.py's
        # inject_billing_lock_status() stamps them then, not at
        # provisioning) -- the trial clock starts when the company actually
        # starts using the product, not when the mandate was authorized.
        # billing_cycle_day records the day-of-month the mandate was
        # authorized on, for display only (Razorpay's own subscription
        # schedule is the actual source of truth for when a charge fires).
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_start_date TIMESTAMP DEFAULT NULL")
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_end_date TIMESTAMP DEFAULT NULL")
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(20) NOT NULL DEFAULT 'active'")
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS billing_cycle_day SMALLINT DEFAULT NULL")
        # Dedup guard for blueprints/trial_billing.py's check_trial_ending_soon()
        # -- a one-time reminder sent TRIAL_REMINDER_WINDOW_HOURS before
        # trial_end_date; this column is what stops it firing twice.
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_reminder_sent_at TIMESTAMP DEFAULT NULL")
        # Business-registration identifier collected at signup (blueprints/
        # org.py's create_org()/api_create_org()) -- lets check_duplicate_gst()
        # block a second free trial/signup under a different company name
        # but the same real-world business, a gap the old company-name-only
        # dedup didn't cover. NULL for tenants provisioned before this
        # existed, or via the Platform Admin direct-create path (which
        # deliberately doesn't collect one -- see platform_admin_create_tenant()).
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS gst_number VARCHAR(20) DEFAULT NULL")
        # PAN/CIN, same duplicate-detection purpose as gst_number just
        # above -- backfilled from utils/document_verification.py's OCR
        # extraction once an application is automatically (or manually)
        # approved, used by check_duplicate_pan()/check_duplicate_cin() to
        # catch a re-registration under a different company name/GST that
        # still shares the same real-world business's PAN or CIN. NULL for
        # every tenant provisioned before this existed.
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS pan VARCHAR(20) DEFAULT NULL")
        cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cin VARCHAR(30) DEFAULT NULL")
        # Platform-operator identity (blueprints/platform_admin.py) --
        # lives in att_master, not any tenant schema, since tenant
        # admin_users rows only exist inside their own schema and this
        # identity must not be tied to one. No totp_secret/totp_enabled
        # columns: login MFA is a plain emailed one-time code (session-held,
        # like blueprints/auth.py's _start_login_mfa), not enrolled TOTP --
        # reusing utils/totp.py's enrollment helpers here would silently
        # read/write the wrong schema's admin_users table anyway, since
        # /super_admin/* deliberately has no resolved g.tenant_db.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_admins (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                email VARCHAR(200) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Internal messaging between the platform operator and a company's
        # own admin/HR staff (blueprints/platform_admin.py's per-tenant chat
        # panel, and admin_base.html's company-side widget). Lives here, not
        # in a tenant schema, because platform admin -- which has no
        # g.tenant_db (see this blueprint's module docstring) -- must be able
        # to read/write it directly; tenant_schema is the join key back to a
        # specific company. One shared thread per tenant: sender_kind
        # distinguishes who actually wrote each line, but company_admin and
        # hr both read/write the same thread for their company, since both
        # represent "this company" to the platform operator on the other end.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                tenant_schema VARCHAR(100) NOT NULL,
                sender_kind VARCHAR(20) NOT NULL,
                sender_name VARCHAR(150) NOT NULL,
                message VARCHAR(2000) NOT NULL,
                read_by_platform SMALLINT NOT NULL DEFAULT 0,
                read_by_company SMALLINT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant ON chat_messages (tenant_schema, created_at)")
        # Razorpay orders for the paid public /create_org signup flow (see
        # blueprints/billing.py, utils/razorpay_utils.py). Lives here, not
        # in a tenant schema, because the order is created and paid BEFORE
        # the tenant schema exists -- provisioning only happens once
        # verify_payment confirms the signature. No password/credential
        # field: the provisioned admin account gets a random password
        # (never stored/emailed) and the customer sets their own via the
        # same reset-token link blueprints/auth.py's admin_forgot_password
        # already uses.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_orders (
                id SERIAL PRIMARY KEY,
                razorpay_order_id VARCHAR(100) UNIQUE NOT NULL,
                razorpay_payment_id VARCHAR(100) DEFAULT NULL,
                plan VARCHAR(50) NOT NULL,
                employee_count INT NOT NULL,
                amount_paise INT NOT NULL,
                company_name VARCHAR(200) NOT NULL,
                subdomain VARCHAR(100) NOT NULL,
                admin_username VARCHAR(100) NOT NULL,
                admin_email VARCHAR(200) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'created',
                tenant_id INT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP DEFAULT NULL
            )
        """)
        # Company email domain, carried from create_order() through to
        # verify_payment() (where provision_tenant() actually runs) since
        # the tenant's own company_settings row doesn't exist yet at
        # create_order() time.
        cur.execute("ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS email_domain VARCHAR(255) DEFAULT NULL")
        # Company logo, uploaded on the signup form and staged here at
        # create_order() time (before any tenant/company_settings row
        # exists) -- verify_payment() reads it back and hands it to
        # provision_tenant() once the tenant schema is actually created.
        cur.execute("ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS logo_path VARCHAR(255) DEFAULT NULL")
        # Links a payment_orders row back to the tenant_applications row it
        # was raised from, once payment moves to after admin approval (see
        # tenant_applications below) -- nullable since platform-admin-created
        # tenants (platform_admin.py) and the free/manual signup path never
        # go through payment_orders at all.
        cur.execute("ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS application_id INT DEFAULT NULL")
        # Gated company signup: a prospective tenant now goes through this
        # pending-application state machine (email OTP -> KYC document
        # upload -> manual platform-admin review) before provision_tenant()
        # is ever called, instead of being provisioned instantly from the
        # signup form. status: started -> otp_verified -> pending_review ->
        # approved | approved_pending_payment -> provisioned, or terminal
        # rejected / expired. admin_password_hash is the ONLY form the
        # password is ever stored in during the (possibly multi-day) pending
        # window -- provision_tenant() was changed to accept a pre-hashed
        # password precisely so plaintext never sits here waiting on review.
        # access_token_hash: a random opaque token (secrets.token_urlsafe)
        # is generated once at application-start and returned to the caller
        # (web: stashed in session; mobile: held by the app for the rest of
        # the signup flow) -- every later step (verify OTP, upload
        # documents, check status) must present it, hashed the same way
        # api_tokens are (utils/auth.py's _hash_token). This is what stops
        # someone from guessing a sequential application id and hijacking
        # or peeking at someone else's in-progress signup, on either
        # platform, without needing a session cookie mobile doesn't have.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_applications (
                id SERIAL PRIMARY KEY,
                company_name VARCHAR(200) NOT NULL,
                subdomain VARCHAR(100) NOT NULL,
                admin_username VARCHAR(100) NOT NULL,
                admin_email VARCHAR(200) NOT NULL,
                admin_password_hash VARCHAR(255) NOT NULL,
                email_domain VARCHAR(255) DEFAULT NULL,
                employee_count INT DEFAULT NULL,
                payment_option VARCHAR(20) NOT NULL DEFAULT 'manual',
                logo_path VARCHAR(255) DEFAULT NULL,

                access_token_hash VARCHAR(64) NOT NULL,
                otp_code_hash VARCHAR(64) DEFAULT NULL,
                otp_expires_at TIMESTAMP DEFAULT NULL,
                otp_attempts SMALLINT NOT NULL DEFAULT 0,
                email_verified_at TIMESTAMP DEFAULT NULL,

                doc_registration_cert VARCHAR(500) DEFAULT NULL,
                doc_address_proof VARCHAR(500) DEFAULT NULL,
                doc_visiting_card VARCHAR(500) DEFAULT NULL,
                doc_name_board_photo VARCHAR(500) DEFAULT NULL,
                documents_submitted_at TIMESTAMP DEFAULT NULL,

                status VARCHAR(30) NOT NULL DEFAULT 'started',
                reviewed_by VARCHAR(100) DEFAULT NULL,
                reviewed_at TIMESTAMP DEFAULT NULL,
                rejection_reason VARCHAR(1000) DEFAULT NULL,
                tenant_id INT DEFAULT NULL,

                source_ip VARCHAR(45) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tenant_applications_status ON tenant_applications (status, created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tenant_applications_email ON tenant_applications (admin_email)")
        # Business-registration identifier -- see tenants.gst_number above
        # for why this exists; carried on the application row first and
        # copied onto tenants.gst_number by provision_tenant() once approved.
        cur.execute("ALTER TABLE tenant_applications ADD COLUMN IF NOT EXISTS gst_number VARCHAR(20) DEFAULT NULL")
        # Tracks KYC document review progress SEPARATELY from `status` above.
        # `status` used to double as both "has this application been
        # provisioned" AND "where's the KYC review at" -- that stopped
        # working once every self-serve signup provisions instantly right
        # after OTP verification (blueprints/org.py's
        # _provision_application_instantly()), before any document is even
        # uploaded: an application can now be status='provisioned' (has a
        # live tenant) while doc_review_status is anywhere from
        # 'pending_documents' through 'approved'/'rejected'. Values (no DB
        # CHECK, app-level enforced -- matches this table's existing
        # status/payment_option convention, neither of which has one
        # either): pending_documents -> pending_review -> approved | rejected.
        cur.execute("ALTER TABLE tenant_applications ADD COLUMN IF NOT EXISTS doc_review_status VARCHAR(20) NOT NULL DEFAULT 'pending_documents'")
        # Automated document verification (utils/document_verification.py)
        # -- a durable job queue modeled on utils/email_utils.py's
        # email_queue (same reasoning: silently losing this job would
        # strand a real applicant behind the login gate forever). NULL
        # until all 4 documents are uploaded (_save_application_documents()
        # sets 'pending' at that point); 'processing' while a worker has
        # claimed it; 'done' once an outcome was applied (approved,
        # rejected, OR left at doc_review_status='pending_review' for a
        # human -- 'done' means "the automated attempt finished", not
        # "auto-approved"); 'failed' after verification_attempts exhausts
        # its retry budget (falls back to the manual queue either way).
        cur.execute("ALTER TABLE tenant_applications ADD COLUMN IF NOT EXISTS verification_job_status VARCHAR(20) DEFAULT NULL")
        cur.execute("ALTER TABLE tenant_applications ADD COLUMN IF NOT EXISTS verification_attempts INT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE tenant_applications ADD COLUMN IF NOT EXISTS verification_error TEXT DEFAULT NULL")
        # Raw structured extraction result (gst_number/pan/cin/company_name/
        # address plus per-field legibility) -- kept even for an
        # inconclusive/manual-fallback outcome so a human reviewer in
        # platform_admin.py's queue isn't starting from zero context.
        cur.execute("ALTER TABLE tenant_applications ADD COLUMN IF NOT EXISTS ocr_extraction JSONB DEFAULT NULL")
        # Internal-only record of a signup blocked because its company_name
        # matched an existing tenant. Deliberately a SEPARATE table from
        # tenant_applications (rather than a flag/column on it) so the real
        # conflicting tenant's identity can never be joined into any
        # registrant-facing view -- it is only ever read by the platform-admin
        # duplicate-alerts screen (blueprints/platform_admin.py).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_duplicate_alerts (
                id SERIAL PRIMARY KEY,
                application_id INT DEFAULT NULL,
                attempted_company_name VARCHAR(200) NOT NULL,
                attempted_admin_email VARCHAR(200) NOT NULL,
                conflicting_tenant_id INT NOT NULL,
                conflicting_company_name VARCHAR(200) NOT NULL,
                conflicting_admin_email VARCHAR(200) DEFAULT NULL,
                match_type VARCHAR(20) NOT NULL DEFAULT 'exact',
                acknowledged SMALLINT NOT NULL DEFAULT 0,
                acknowledged_by VARCHAR(100) DEFAULT NULL,
                acknowledged_at TIMESTAMP DEFAULT NULL,
                source_ip VARCHAR(45) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tenant_duplicate_alerts_ack ON tenant_duplicate_alerts (acknowledged, created_at)")
        # Razorpay orders for an EXISTING tenant buying more employee seats
        # after signup (blueprints/seats.py) -- separate from payment_orders
        # above (that table stages a brand-new tenant that doesn't exist yet;
        # this one always references an already-provisioned tenant_schema).
        # Paid orders top up that tenant's own company_settings.paid_employee_slots
        # once verify_payment() confirms the signature, the same seat cap
        # add_employee_seat_cap_check() (utils/helpers.py) enforces at
        # employee-registration time.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS seat_topup_orders (
                id SERIAL PRIMARY KEY,
                tenant_schema VARCHAR(100) NOT NULL,
                company_name VARCHAR(200) NOT NULL,
                razorpay_order_id VARCHAR(100) UNIQUE NOT NULL,
                razorpay_payment_id VARCHAR(100) DEFAULT NULL,
                seats_purchased INT NOT NULL,
                amount_paise INT NOT NULL,
                requested_by VARCHAR(100) DEFAULT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'created',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP DEFAULT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_seat_topup_orders_tenant ON seat_topup_orders (tenant_schema, created_at)")
        # Singleton row (id=1) caching the one shared Razorpay Plan ID used
        # for every tenant's monthly per-employee auto-debit subscription
        # (blueprints/auto_debit.py) -- created once via the Plans API on
        # first enrollment rather than requiring manual Razorpay-dashboard
        # setup, then reused by every tenant's own Subscription (which
        # carries that tenant's `quantity` = employee count).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_config (
                id SMALLINT PRIMARY KEY DEFAULT 1,
                razorpay_plan_id VARCHAR(100) DEFAULT NULL,
                CHECK (id = 1)
            )
        """)
        cur.execute("INSERT INTO billing_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        # One row per tenant that has ever enrolled in monthly auto-debit
        # (blueprints/auto_debit.py). status='pending' until the Razorpay
        # Checkout subscription-authorization round-trip confirms via
        # /api/auto_debit/confirm; 'active' is billed going forward by
        # Razorpay's own recurring engine (or, in demo mode, by this app's
        # own simulated monthly cron -- see sync_and_bill_auto_debit()).
        # quantity_synced tracks what Razorpay's subscription was last told
        # the headcount is, so the daily sync job only PATCHes when it
        # actually changed.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auto_debit_mandates (
                id SERIAL PRIMARY KEY,
                tenant_schema VARCHAR(100) UNIQUE NOT NULL,
                company_name VARCHAR(200) NOT NULL,
                razorpay_customer_id VARCHAR(100) DEFAULT NULL,
                razorpay_subscription_id VARCHAR(100) DEFAULT NULL,
                quantity_synced INT NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                requested_by VARCHAR(100) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                activated_at TIMESTAMP DEFAULT NULL,
                cancelled_at TIMESTAMP DEFAULT NULL
            )
        """)
        # Set TRUE on every currently-active mandate when the platform admin
        # changes the per-employee rate (blueprints/platform_admin.py's
        # platform_admin_set_rate()) -- Razorpay Plans are immutable, so an
        # existing subscription keeps charging its original rate forever
        # otherwise. Checked by the subscription.charged webhook handler
        # (blueprints/auto_debit.py's _handle_subscription_charged()): once
        # a flagged mandate's current cycle is paid, that subscription is
        # cancelled and the tenant is emailed to re-authorize a fresh one on
        # the new rate -- migration happens at the subscriber's own next
        # renewal, never mid-cycle.
        cur.execute("ALTER TABLE auto_debit_mandates ADD COLUMN IF NOT EXISTS needs_rate_migration BOOLEAN NOT NULL DEFAULT FALSE")
        # 'pending_cancellation' is a legal status value alongside the ones
        # documented above -- set by /api/auto_debit/cancel when called with
        # at_period_end=true (blueprints/auto_debit.py). The mandate keeps
        # billing normally until Razorpay's subscription.cancelled webhook
        # fires at the end of the current cycle, which is what actually
        # flips status to 'cancelled'.
        # One row per successfully (or unsuccessfully) collected monthly
        # auto-debit charge -- written by the Razorpay webhook
        # (subscription.charged / payment.failed) in real mode, or by the
        # simulated monthly cron in demo mode. Visible to the tenant
        # (templates/seat_checkout.html billing history) and to the
        # Platform Admin dashboard (recurring-billing feed).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS monthly_invoices (
                id SERIAL PRIMARY KEY,
                tenant_schema VARCHAR(100) NOT NULL,
                company_name VARCHAR(200) NOT NULL,
                employee_count INT NOT NULL,
                amount_paise INT NOT NULL,
                razorpay_payment_id VARCHAR(100) DEFAULT NULL,
                razorpay_subscription_id VARCHAR(100) DEFAULT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'paid',
                billing_period DATE NOT NULL,
                failure_reason VARCHAR(255) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_monthly_invoices_tenant ON monthly_invoices (tenant_schema, billing_period)")
        # Lets blueprints/billing_dunning.py's payment.captured webhook find
        # the pending row it staged at order-creation time before a
        # razorpay_payment_id even exists yet -- the recurring auto-debit
        # path above never needed this column since its webhook arrives
        # already carrying a subscription_id to look up by instead.
        cur.execute("ALTER TABLE monthly_invoices ADD COLUMN IF NOT EXISTS razorpay_order_id VARCHAR(100) DEFAULT NULL")
        # Exact per-employee rate applied to this specific charge, captured
        # at charge time -- amount_paise/employee_count already implies it,
        # but storing it explicitly keeps the audit trail correct even if
        # the platform-wide rate changes later (get_per_employee_paise() is
        # a live, mutable read; this column is a frozen historical fact).
        cur.execute("ALTER TABLE monthly_invoices ADD COLUMN IF NOT EXISTS rate_paise INT DEFAULT NULL")
        # Real DB-level guarantee against double-processing a retried
        # Razorpay webhook (subscription.charged / payment.captured), on top
        # of the existing check-then-write idiom in _record_charge() /
        # _mark_invoice_paid_and_unlock() -- partial index since most rows
        # (pending orders, demo charges) never get a real payment id.
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_monthly_invoices_payment_id ON monthly_invoices "
            "(razorpay_payment_id) WHERE razorpay_payment_id IS NOT NULL")
        # Lightweight traffic counter for the public marketing pages
        # (landing page, get-started, create_org) -- one row per
        # (path, day), incremented via ON CONFLICT below rather than one
        # row per visit, so this stays cheap regardless of traffic volume.
        # No cookies/fingerprinting/IP storage -- just a count.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS page_views (
                id SERIAL PRIMARY KEY,
                path VARCHAR(255) NOT NULL,
                view_date DATE NOT NULL,
                count INT NOT NULL DEFAULT 0,
                UNIQUE(path, view_date)
            )
        """)
        # "Request info" / contact-form submissions from the public landing
        # page (templates/landing.html) -- visitors not ready to
        # self-register yet, surfaced to the Platform Admin dashboard for
        # manual follow-up.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                email VARCHAR(200) NOT NULL,
                company_name VARCHAR(200) DEFAULT NULL,
                message TEXT DEFAULT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Pre-existing leads tables created before phone existed --
        # CREATE TABLE IF NOT EXISTS above is a no-op against them.
        cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone VARCHAR(30) DEFAULT NULL")
        # Product feedback / star ratings from the public landing page
        # (templates/landing.html's #feedback form -> POST /api/feedback,
        # blueprints/org.py). Separate table from leads above -- this is
        # unsolicited product input from anyone (existing customer,
        # prospect, or neither), not a sales inquiry, so it carries no
        # name/company/status-workflow, just what the form actually asks
        # for. Surfaced read-only on the Platform Admin dashboard
        # (blueprints/platform_admin.py's _recent_feedback()).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                feedback_type VARCHAR(50) NOT NULL DEFAULT 'Feature Request',
                rating SMALLINT DEFAULT NULL,
                email VARCHAR(200) DEFAULT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Singleton row (id=1) holding the platform operator's own monthly
        # running costs -- there's no API that can discover real AWS/
        # maintenance spend automatically, so these are admin-entered and
        # compared against MRR to drive the Platform Admin dashboard's
        # Profit & Loss bar and pricing-adjustment suggestion.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_costs (
                id SMALLINT PRIMARY KEY DEFAULT 1,
                monthly_aws_paise INT NOT NULL DEFAULT 0,
                monthly_maintenance_paise INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK (id = 1)
            )
        """)
        cur.execute("INSERT INTO platform_costs (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
        # Same singleton row now also carries the flat per-employee billing
        # rate -- was a hardcoded Python constant (utils/plan_limits.py's
        # PER_EMPLOYEE_PAISE, kept as the DEFAULT/seed value and as a
        # fail-safe fallback), moved here so the platform admin can change
        # it at runtime and have every price calculation pick it up on the
        # next read (utils/plan_limits.py's get_per_employee_paise(), 30s
        # cache) -- no redeploy needed.
        cur.execute("ALTER TABLE platform_costs ADD COLUMN IF NOT EXISTS per_employee_paise INT NOT NULL DEFAULT 9900")
        # Both default 0 -- utils/plan_limits.py's calculate_price() formula
        # (base_fee + employee_count*rate, floored at minimum_monthly) is
        # byte-identical to the old flat-rate-only behavior until a
        # platform admin explicitly sets one of these (blueprints/
        # platform_admin.py's platform_admin_set_rate()).
        cur.execute("ALTER TABLE platform_costs ADD COLUMN IF NOT EXISTS base_fee_paise INT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE platform_costs ADD COLUMN IF NOT EXISTS minimum_monthly_paise INT NOT NULL DEFAULT 0")
        # ── Mini-CRM: per-company internal notes + a support-ticket queue ──
        # (blueprints/platform_admin.py's company profile / tickets pages).
        # Both key off tenants.id, not tenant_schema, so a note/ticket
        # survives even if the tenant itself is later deleted (platform_
        # admin_delete_tenant()) -- same "billing history outlives the
        # tenant row" posture payment_orders etc. already have, useful for
        # "why did we delete this account" context later.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_notes (
                id SERIAL PRIMARY KEY,
                tenant_id INT NOT NULL,
                author VARCHAR(100) NOT NULL,
                note VARCHAR(4000) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tenant_notes_tenant ON tenant_notes (tenant_id, created_at DESC)")
        # Trackable support requests, distinct from the existing real-time
        # chat_messages panel (that's for back-and-forth conversation; this
        # is for something with a lifecycle -- status, priority, and a
        # resolution timestamp -- that the platform admin can report on
        # across every company, not just read in the moment).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_support_tickets (
                id SERIAL PRIMARY KEY,
                tenant_id INT NOT NULL,
                subject VARCHAR(200) NOT NULL,
                description VARCHAR(4000) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'open',
                priority VARCHAR(10) NOT NULL DEFAULT 'normal',
                created_by VARCHAR(100) DEFAULT NULL,
                resolved_at TIMESTAMP DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tenant_tickets_tenant ON tenant_support_tickets (tenant_id, created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tenant_tickets_status ON tenant_support_tickets (status, created_at DESC)")
        # _set_updated_at() is normally only created per-tenant-schema (init_db()
        # above) -- att_master needs its own copy before a trigger here can
        # reference it, since Postgres resolves the function name via
        # att_master's own search_path, not a tenant schema's.
        cur.execute(_UPDATED_AT_TRIGGER_FN)
        _attach_updated_at_trigger(cur, "tenant_support_tickets")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenant_ticket_comments (
                id SERIAL PRIMARY KEY,
                ticket_id INT NOT NULL,
                author VARCHAR(100) NOT NULL,
                comment VARCHAR(2000) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket ON tenant_ticket_comments (ticket_id, created_at)")
        db.commit()
        cur.close()
        db.close()
    except Exception as _e:
        app_log.warning("init_master_db failed (non-fatal for single-tenant mode): %s", _e)
