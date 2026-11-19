# -*- coding: utf-8 -*-
"""att_master tables behind the SOC console (blueprints/soc.py).

They live in the platform's own master schema, never in a tenant schema: SOC
staff are platform operators, and nothing a tenant admin can reach should be
able to read or write these rows.

  soc_users       analyst accounts. Sign-in is an emailed one-time code for the
                  addresses in SOC_LOGIN_EMAILS (utils/soc_auth.py); the
                  password / TOTP columns are left over from the earlier
                  password + authenticator login and are no longer read
  soc_cases       triage / incident cases opened from events, detections or logs
  soc_case_notes  analyst notes on a case
  soc_audit_log   every SOC login and action. Append-only: a trigger rejects
                  UPDATE and DELETE, so an analyst (or an attacker holding an
                  analyst session) cannot quietly rewrite their own trail.
  soc_ban_reviews addresses automated detections want banned, awaiting an
                  analyst's ban / dismiss / trust decision

The trigger is a speed bump against the application role, not a defence
against a database owner; for that, ship these rows off-box with the rest of
the audit stream (see docs/SECURITY_HARDENING.md).
"""

SOC_ROLES = ("viewer", "analyst", "admin")
CASE_STATUSES = ("open", "investigating", "contained", "resolved", "false_positive")

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS soc_users (
        id                  SERIAL PRIMARY KEY,
        username            VARCHAR(60)  NOT NULL UNIQUE,
        email               VARCHAR(200),
        password_hash       TEXT         NOT NULL,
        role                VARCHAR(12)  NOT NULL DEFAULT 'viewer'
                            CHECK (role IN ('viewer','analyst','admin')),
        totp_secret         TEXT,
        totp_enabled        BOOLEAN      NOT NULL DEFAULT FALSE,
        totp_last_step      BIGINT       NOT NULL DEFAULT 0,
        enroll_code_hash    TEXT,
        active              BOOLEAN      NOT NULL DEFAULT TRUE,
        failed_count        INT          NOT NULL DEFAULT 0,
        locked_until        TIMESTAMP,
        last_login_at       TIMESTAMP,
        last_login_ip       VARCHAR(64),
        password_changed_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
        created_by          VARCHAR(60),
        created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS soc_cases (
        id              SERIAL PRIMARY KEY,
        title           VARCHAR(200) NOT NULL,
        severity        VARCHAR(10)  NOT NULL DEFAULT 'medium'
                        CHECK (severity IN ('critical','high','medium','low','info')),
        status          VARCHAR(16)  NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','investigating','contained','resolved','false_positive')),
        assignee        VARCHAR(60),
        source_type     VARCHAR(20)  NOT NULL DEFAULT 'manual',
        source_event_id INT,
        summary         TEXT,
        resolution      TEXT,
        created_by      VARCHAR(60)  NOT NULL,
        created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
        closed_at       TIMESTAMP
    )
    """,
    "ALTER TABLE soc_users ADD COLUMN IF NOT EXISTS enroll_code_hash TEXT",
    # Emailed one-time sign-in code: HMAC of the code (never the code), its
    # expiry, and when the last one went out (one per minute at most).
    "ALTER TABLE soc_users ADD COLUMN IF NOT EXISTS login_code_hash TEXT",
    "ALTER TABLE soc_users ADD COLUMN IF NOT EXISTS login_code_expires_at TIMESTAMP",
    "ALTER TABLE soc_users ADD COLUMN IF NOT EXISTS login_code_sent_at TIMESTAMP",
    # Bumped to end every open session of that person at once (passkey reset,
    # "Sign out everywhere"); each session remembers the value it started with.
    "ALTER TABLE soc_users ADD COLUMN IF NOT EXISTS session_epoch INT NOT NULL DEFAULT 0",
    # Passkeys (utils/soc_passkeys.py): the step after the emailed code. Public
    # keys only -- the private key never leaves the person's device.
    """
    CREATE TABLE IF NOT EXISTS soc_passkeys (
        id             SERIAL PRIMARY KEY,
        user_id        INT          NOT NULL,
        credential_id  TEXT         NOT NULL UNIQUE,
        public_key     TEXT         NOT NULL,
        sign_count     BIGINT       NOT NULL DEFAULT 0,
        created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
        last_used_at   TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_soc_passkeys_user ON soc_passkeys (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_soc_cases_status ON soc_cases (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_soc_cases_event ON soc_cases (source_event_id)",
    """
    CREATE TABLE IF NOT EXISTS soc_case_notes (
        id         SERIAL PRIMARY KEY,
        case_id    INT          NOT NULL,
        author     VARCHAR(60)  NOT NULL,
        body       TEXT         NOT NULL,
        created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_soc_case_notes_case ON soc_case_notes (case_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS soc_audit_log (
        id         SERIAL PRIMARY KEY,
        actor      VARCHAR(60)  NOT NULL,
        action     VARCHAR(80)  NOT NULL,
        target     VARCHAR(200),
        detail     TEXT,
        ip         VARCHAR(64),
        created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_soc_audit_created ON soc_audit_log (created_at DESC)",
    # The console no longer looks addresses up at an outside service (it shows only
    # what HRZEST recorded), so the cache of those third-party answers goes too.
    "DROP TABLE IF EXISTS soc_ip_intel",
    """
    CREATE OR REPLACE FUNCTION soc_audit_log_immutable() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'soc_audit_log is append-only';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_soc_audit_log_immutable ON soc_audit_log",
    """
    CREATE TRIGGER trg_soc_audit_log_immutable
    BEFORE UPDATE OR DELETE ON soc_audit_log
    FOR EACH ROW EXECUTE FUNCTION soc_audit_log_immutable()
    """,
    # Addresses the WAF / rate limiter / honeypots want banned, waiting for an
    # analyst's decision (utils/soc_ban_review.py). One row per address.
    """
    CREATE TABLE IF NOT EXISTS soc_ban_reviews (
        ip                  VARCHAR(64)  PRIMARY KEY,
        status              VARCHAR(12)  NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','banned','dismissed','trusted')),
        source              VARCHAR(20)  NOT NULL,
        reason              TEXT         NOT NULL,
        suggested_minutes   INT,
        hits                INT          NOT NULL DEFAULT 1,
        first_seen          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        decided_by          VARCHAR(80),
        decided_at          TIMESTAMP,
        decision_note       TEXT,
        trusted_until       TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_soc_ban_reviews_status ON soc_ban_reviews (status, last_seen DESC)",
]


def ensure_soc_tables(cursor):
    """Idempotent. `cursor` must be on the att_master search_path."""
    for stmt in STATEMENTS:
        cursor.execute(stmt)
