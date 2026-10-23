# -*- coding: utf-8 -*-
"""Scheduled audit-integrity jobs (wired in wsgi.py).

emit_anchors()         every few minutes: log the current head hash of every
                       audit chain as one JSON line. The app's stdout is
                       already shipped to Loki on a separate host by
                       Promtail, so this is the "off-box copy the database
                       credentials cannot reach" that makes a full chain
                       rewrite detectable (utils/audit_chain.py).
run_full_verification() daily: recompute every chain and compare with the
                       head pointers; CRITICAL security event on a break.

Anchors use the last chained row (readable by the restricted runtime role),
not the head-pointer table, so emitting them needs no elevated privilege.
"""
import json


def _tenant_schemas(app_log):
    from database import get_master_db
    try:
        mdb = get_master_db()
        mcur = mdb.cursor()
        mcur.execute("SELECT db_name FROM tenants")
        out = [r[0] for r in mcur.fetchall()]
        mcur.close()
        mdb.close()
        return out
    except Exception:
        app_log.warning("audit jobs: could not list tenants", exc_info=True)
        return []


def emit_anchors():
    from extensions import app_log
    from database import get_db_connection
    from migrations.audit_integrity import CHAINED_TABLES
    import re
    ident = re.compile(r"^[A-Za-z0-9_]+$")
    emitted = 0

    db = get_db_connection()
    try:
        cur = db.cursor()
        cur.execute(
            "SELECT table_schema FROM information_schema.tables WHERE table_name='audit_chain_head' "
            "ORDER BY table_schema")
        schemas = [r[0] for r in cur.fetchall() if ident.match(r[0])]
        for schema in schemas:
            for table in CHAINED_TABLES:
                try:
                    cur.execute(
                        f'SELECT row_hash, id FROM "{schema}"."{table}" '  # nosec B608 -- identifiers validated + constant table names
                        "WHERE row_hash IS NOT NULL ORDER BY id DESC LIMIT 1")
                    row = cur.fetchone()
                except Exception:  # nosec B112 -- skip a table the role cannot read
                    continue
                if not row:
                    continue
                app_log.info("audit_anchor", extra={
                    "audit_anchor": True, "schema": schema, "table": table,
                    "head": row[0], "last_id": row[1],
                })
                emitted += 1
        cur.close()
    finally:
        db.close()
    return emitted


def run_full_verification():
    from extensions import app_log, log_security_event
    from database import get_db_connection, owner_privileges
    from utils.audit_chain import list_chained_schemas, verify_schema

    broken = []
    with owner_privileges():
        db = get_db_connection()
        try:
            cur = db.cursor()
            for schema in list_chained_schemas(cur):
                for report in verify_schema(cur, schema):
                    if not report["ok"]:
                        broken.append(report)
            cur.close()
        finally:
            db.close()

    for report in broken:
        log_security_event(
            "audit.chain_violation",
            "Audit hash chain verification failed: history may have been altered",
            level="CRITICAL",
            identifier=f"{report['schema']}.{report['table']}",
            reason=json.dumps(report["violations"][:3], default=str)[:180],
        )
    app_log.info("audit chain verification complete: %d broken", len(broken))
    return broken
