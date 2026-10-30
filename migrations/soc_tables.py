# -*- coding: utf-8 -*-
"""att_master tables behind the SOC console (blueprints/soc.py).

They live in the platform's own master schema, never in a tenant schema: SOC
staff are platform operators, and nothing a tenant admin can reach should be
able to read or write these rows.

  soc_users       analyst accounts (password hash + encrypted TOTP secret)
  soc_cases       triage / incident cases opened from events, detections or logs
  soc_case_notes  analyst notes on a case
  soc_audit_log   every SOC login and action. Append-only: a trigger rejects
                  UPDATE and DELETE, so an analyst (or an attacker holding an
                  analyst session) cannot quietly rewrite their own trail.

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
]


def ensure_soc_tables(cursor):
    """Idempotent. `cursor` must be on the att_master search_path."""
    for stmt in STATEMENTS:
        cursor.execute(stmt)
