# -*- coding: utf-8 -*-
"""Tamper-evidence for the append-only audit tables (audit_logs,
security_events).

Why this exists on top of the existing UPDATE/DELETE-rejecting trigger
(core_tables.py): that trigger stops *application bugs* from rewriting
history, but anyone holding the application's own database credentials can
`DROP TRIGGER` (the app boots as the table owner/superuser by default) or,
until now, simply `SET audit.bypass = 'on'` and delete rows. Immutability
that the attacker can switch off is not evidence. Three layers fix that:

1. Hash chain (this module). Every inserted row stores
   `prev_hash` and `row_hash = SHA-256(prev_hash || the row's own columns)`,
   computed by a BEFORE INSERT trigger so no call site can forget or lie.
   Altering or deleting any historical row breaks every hash after it.
2. Off-box anchors (scripts/verify_audit_chain.py --emit-anchor). The
   current head hash is periodically written to stdout, which Promtail ships
   to Loki on another host. An attacker who recomputes the whole chain in the
   database still cannot rewrite the copy of the head they never touched.
3. Privilege separation (database.py's optional owner role +
   grant_runtime_privileges). With DB_OWNER_USER set, the runtime role can
   INSERT/SELECT the audit tables but holds no UPDATE/DELETE/TRUNCATE and no
   right to drop triggers or touch the chain head.

The `audit.bypass` escape hatch used by test-fixture cleanup is only
compiled into the trigger function when APP_ENV=development or
FLASK_ENV=testing; a production boot installs the strict variant, so the
GUC no longer does anything there.
"""
import os

GENESIS_HASH = "0" * 64

# Column order is part of the hash -- change it only with a hash version bump.
# Explicit lists (not to_jsonb(NEW)) so a future ALTER TABLE ADD COLUMN does
# not retroactively change every historical row's hash and read as tampering.
CHAINED_TABLES = {
    "audit_logs": (
        "id", "actor", "actor_type", "action", "target_table", "target_id",
        "detail", "ip_address", "created_at",
    ),
    "security_events": (
        "id", "event_type", "level", "message", "identifier", "ip", "path",
        "method", "extra_json", "created_at",
    ),
}


def bypass_allowed() -> bool:
    return (os.environ.get("APP_ENV", "production") == "development"
            or os.environ.get("FLASK_ENV", "") == "testing")


def hash_sql_expr(table: str, prev_expr: str, row_alias: str) -> str:
    """The one place the hashed byte string is defined -- used verbatim by
    both the INSERT trigger and the verifier so they cannot drift."""
    cols = ", ".join(f"{row_alias}.{c}" for c in CHAINED_TABLES[table])  # nosec B608
    return f"encode(sha256(convert_to(jsonb_build_array({prev_expr}, {cols})::text, 'UTF8')), 'hex')"  # nosec B608


def _immutability_fn_sql() -> str:
    bypass = ""
    if bypass_allowed():
        bypass = """
            -- Test-fixture cleanup only. Compiled in solely when
            -- APP_ENV=development / FLASK_ENV=testing at migration time.
            IF current_setting('audit.bypass', true) = 'on' THEN
                RETURN COALESCE(NEW, OLD);
            END IF;"""
    return f"""
        CREATE OR REPLACE FUNCTION _reject_audit_mutation() RETURNS TRIGGER AS $$
        BEGIN{bypass}
            RAISE EXCEPTION 'audit tables are append-only: % on % is not permitted', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
    """


def _chain_fn_sql(table: str) -> str:
    new_hash = hash_sql_expr(table, "prev", "NEW")
    # table is a CHAINED_TABLES key and new_hash is built from constant column names.
    return f"""
        CREATE OR REPLACE FUNCTION _audit_chain_{table}() RETURNS TRIGGER AS $fn$
        DECLARE
            prev TEXT;
        BEGIN
            -- The row lock on this table's head pointer is held until the
            -- inserting transaction ends, so concurrent inserts are
            -- serialised and the chain can never fork.
            EXECUTE format(
                'INSERT INTO %I.audit_chain_head (tbl, head) VALUES (%L, %L) ON CONFLICT (tbl) DO NOTHING',
                TG_TABLE_SCHEMA, TG_TABLE_NAME, '{GENESIS_HASH}');
            EXECUTE format(
                'SELECT head FROM %I.audit_chain_head WHERE tbl = %L FOR UPDATE',
                TG_TABLE_SCHEMA, TG_TABLE_NAME) INTO prev;
            NEW.prev_hash := prev;
            NEW.row_hash := {new_hash};
            EXECUTE format(
                'UPDATE %I.audit_chain_head SET head = %L, updated_at = CURRENT_TIMESTAMP WHERE tbl = %L',
                TG_TABLE_SCHEMA, NEW.row_hash, TG_TABLE_NAME);
            RETURN NEW;
        EXCEPTION WHEN OTHERS THEN
            -- Never lose the audit row itself because chaining failed (lock
            -- timeout, permissions). The row lands unchained; the verifier
            -- reports any unchained row sitting after the chain's start.
            NEW.prev_hash := NULL;
            NEW.row_hash := NULL;
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp;
    """  # nosec B608


def init_audit_integrity(cursor, db):
    """Idempotent; runs on every boot for every schema (public + tenants)."""
    present = []
    for tbl in CHAINED_TABLES:
        cursor.execute("SELECT to_regclass(%s)", (tbl,))
        if cursor.fetchone()[0] is not None:
            present.append(tbl)
    if not present:
        return
    cursor.execute(_immutability_fn_sql())
    for tbl in present:
        cursor.execute(f'DROP TRIGGER IF EXISTS trg_{tbl}_immutable ON {tbl}')
        cursor.execute(f"""
            CREATE TRIGGER trg_{tbl}_immutable
            BEFORE UPDATE OR DELETE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION _reject_audit_mutation()
        """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_chain_head (
            tbl        VARCHAR(64) PRIMARY KEY,
            head       VARCHAR(64) NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for tbl in present:
        cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS prev_hash VARCHAR(64)")
        cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS row_hash VARCHAR(64)")
        cursor.execute(_chain_fn_sql(tbl))
        cursor.execute(f'DROP TRIGGER IF EXISTS trg_{tbl}_chain ON {tbl}')
        cursor.execute(f"""
            CREATE TRIGGER trg_{tbl}_chain
            BEFORE INSERT ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION _audit_chain_{tbl}()
        """)
    db.commit()
