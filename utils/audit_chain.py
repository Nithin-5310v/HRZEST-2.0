# -*- coding: utf-8 -*-
"""Verifier for the audit hash chain written by migrations/audit_integrity.py.

Pure read path: takes a DB cursor, never writes. Used by
scripts/verify_audit_chain.py (cron/systemd timer + CI) and exercised by
tests/test_audit_integrity.py.

What a clean report proves -- and what it does not
--------------------------------------------------
Proves: every chained row still hashes to what was recorded, the rows form
one unbroken chain from the genesis value to the head pointer, and (when
anchors are supplied) every head hash previously shipped off-box is still an
ancestor of today's chain.

Does not prove: that rows were never *added* by someone able to run the
trigger (that is just a legitimate insert), nor anything about rows written
before the chain was installed (reported as `unchained_legacy_rows`). A
database administrator who rewrites the whole chain AND the head pointer is
only caught by the anchors -- that is what they are for.
"""
import re
import time
from collections import defaultdict

from migrations.audit_integrity import CHAINED_TABLES, GENESIS_HASH, hash_sql_expr

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_MAX_REPORTED = 50


# Every SQL string below interpolates only identifiers that pass _q()'s regex and
# table names that are CHAINED_TABLES keys; values are always bound parameters.
def _q(schema, table):
    if not (_IDENT_RE.match(schema) and _IDENT_RE.match(table)):
        raise ValueError(f"unsafe identifier: {schema!r}.{table!r}")
    return f'"{schema}"."{table}"'


def list_chained_schemas(cursor):
    cursor.execute(
        "SELECT table_schema FROM information_schema.tables "
        "WHERE table_name = 'audit_chain_head' ORDER BY table_schema"
    )
    return [r[0] for r in cursor.fetchall()]


def verify_table(cursor, schema, table, anchors=None):
    """-> report dict. `anchors` is an optional iterable of head hashes (as
    previously emitted by emit_anchor) that must still be on the chain."""
    if table not in CHAINED_TABLES:
        raise ValueError(f"{table!r} is not a chained audit table")
    qt = _q(schema, table)
    expected = hash_sql_expr(table, "t.prev_hash", "t")

    cursor.execute(f"SELECT COUNT(*), MIN(id) FILTER (WHERE row_hash IS NOT NULL) FROM {qt}")  # nosec B608
    total, first_chained_id = cursor.fetchone()

    legacy = unchained_after = 0
    if first_chained_id is not None:
        cursor.execute(f"SELECT COUNT(*) FROM {qt} WHERE row_hash IS NULL AND id < %s", (first_chained_id,))  # nosec B608
        legacy = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM {qt} WHERE row_hash IS NULL AND id > %s", (first_chained_id,))  # nosec B608
        unchained_after = cursor.fetchone()[0]
    else:
        legacy = total

    cursor.execute(
        f"SELECT t.id, t.prev_hash, t.row_hash, {expected} AS expected "  # nosec B608
        f"FROM {qt} t WHERE t.row_hash IS NOT NULL ORDER BY t.id"  # nosec B608
    )
    rows = cursor.fetchall()

    cursor.execute(f"SELECT head FROM {_q(schema, 'audit_chain_head')} WHERE tbl = %s", (table,))  # nosec B608
    head_row = cursor.fetchone()
    recorded_head = head_row[0] if head_row else None

    violations = []
    by_prev = defaultdict(list)
    for id_, prev, row_hash, exp in rows:
        if row_hash != exp:
            violations.append({"type": "row_modified", "id": id_})
        by_prev[prev].append((id_, row_hash))

    # Walk genesis -> head following prev_hash links. Order by *links*, not
    # by id: ids are handed out before the chain lock is taken, so id order
    # and chain order can legitimately differ under concurrency.
    on_chain_ids, on_chain_hashes = set(), {GENESIS_HASH}
    cur = GENESIS_HASH
    while True:
        nxt = by_prev.get(cur)
        if not nxt:
            break
        nxt.sort()
        if len(nxt) > 1:
            violations.append({"type": "chain_fork", "prev_hash": cur, "ids": [i for i, _ in nxt][:10]})
        id_, row_hash = nxt[0]
        if id_ in on_chain_ids:       # defensive: malformed loop
            break
        on_chain_ids.add(id_)
        on_chain_hashes.add(row_hash)
        cur = row_hash
    chain_end = cur

    orphan_ids = [i for i, *_ in rows if i not in on_chain_ids]
    if orphan_ids:
        violations.append({
            "type": "orphan_rows", "count": len(orphan_ids), "ids": orphan_ids[:10],
            "meaning": "rows not reachable from genesis: a predecessor was deleted/replaced, or a row was inserted out-of-band",
        })

    if recorded_head is not None and recorded_head != chain_end:
        violations.append({
            "type": "head_mismatch", "recorded_head": recorded_head, "chain_end": chain_end,
            "meaning": "rows at the tail of the chain were removed (or the head pointer was altered)",
        })
    if recorded_head is None and rows:
        violations.append({"type": "head_missing", "meaning": "chained rows exist but no head pointer"})

    missing_anchors = []
    for a in (anchors or ()):
        a = (a or "").strip()
        if a and a not in on_chain_hashes:
            missing_anchors.append(a)
    if missing_anchors:
        violations.append({
            "type": "anchor_missing", "count": len(missing_anchors), "hashes": missing_anchors[:5],
            "meaning": "a head hash that was shipped off-box is no longer part of the chain: history was rewritten",
        })

    return {
        "schema": schema,
        "table": table,
        "rows_total": total,
        "rows_chained": len(rows),
        "unchained_legacy_rows": legacy,
        "unchained_rows_after_chain_start": unchained_after,
        "head": recorded_head,
        "ok": not violations,
        "warnings": (["unchained rows inserted after the chain started (chaining failed for them)"]
                     if unchained_after else []),
        "violations": violations[:_MAX_REPORTED],
    }


def verify_schema(cursor, schema, anchors_by_table=None):
    anchors_by_table = anchors_by_table or {}
    return [verify_table(cursor, schema, t, anchors_by_table.get(t)) for t in CHAINED_TABLES]


def build_anchor(report):
    """The record to ship off-box (stdout -> Promtail -> Loki)."""
    return {
        "audit_anchor": True,
        "schema": report["schema"],
        "table": report["table"],
        "head": report["head"],
        "rows_chained": report["rows_chained"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
