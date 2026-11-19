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
import itertools
import re
import time
from collections import deque

from migrations.audit_integrity import CHAINED_TABLES, GENESIS_HASH, hash_sql_expr

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_MAX_REPORTED = 50

# Streaming (any table size, flat memory): rows arrive in id order from a
# server-side cursor, BATCH at a time. Chain order can differ from id order only
# for rows written at about the same moment (ids are handed out before the
# chain lock), so a row whose predecessor has not arrived yet waits in a small
# buffer. More than PENDING_LIMIT rows waiting means a predecessor is missing:
# nothing after it can ever link, so those rows are counted, not kept.
BATCH = 5000
PENDING_LIMIT = 50_000
_RECENT = 10_000            # consumed links remembered to name a late second branch a fork


class VerificationTimeout(Exception):
    """The verification ran past its deadline (the console button's time budget)."""


def _stream(cursor, sql):
    """Yield the rows of `sql` without holding them all in memory: a server-side
    cursor (one snapshot, like a single SELECT), fetched BATCH rows at a time.
    Falls back to the plain cursor where a named cursor is not available."""
    conn = getattr(cursor, "connection", None)
    named = None
    try:
        named = conn.cursor(name=f"audit_verify_{int(time.time() * 1e6)}", withhold=True)
        named.itersize = BATCH
        named.execute(sql)
    except Exception:
        named = None
    try:
        if named is None:
            cursor.execute(sql)
            while True:
                rows = cursor.fetchmany(BATCH)
                if not rows:
                    return
                yield from rows
        else:
            yield from named
    finally:
        if named is not None:
            try:
                named.close()
            except Exception:       # nosec B110 -- best effort; the transaction end drops it anyway
                pass


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


def verify_table(cursor, schema, table, anchors=None, deadline=None):
    """-> report dict. `anchors` is an optional iterable of head hashes (as
    previously emitted by emit_anchor) that must still be on the chain.
    `deadline` (time.monotonic() value) raises VerificationTimeout when passed.
    Memory stays flat whatever the table size (see BATCH / PENDING_LIMIT)."""
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

    cursor.execute(f"SELECT head FROM {_q(schema, 'audit_chain_head')} WHERE tbl = %s", (table,))  # nosec B608
    head_row = cursor.fetchone()
    recorded_head = head_row[0] if head_row else None

    violations, modified, forks = [], [], []
    pending = {}                    # prev_hash -> [(id, row_hash)] waiting for their predecessor
    waiting = 0
    recent, recent_set = deque(), set()
    anchors_left = {(a or "").strip() for a in (anchors or ())} - {"", GENESIS_HASH}
    orphan_count, orphan_ids = 0, []
    rows_chained = 0
    walking = True
    cur = GENESIS_HASH

    def orphan(id_):
        nonlocal orphan_count
        orphan_count += 1
        if len(orphan_ids) < 10:
            orphan_ids.append(id_)

    def consumed(link):
        recent.append(link)
        recent_set.add(link)
        if len(recent) > _RECENT:
            recent_set.discard(recent.popleft())

    # Walk genesis -> head following prev_hash links. Order by *links*, not
    # by id: ids are handed out before the chain lock is taken, so id order
    # and chain order can legitimately differ under concurrency.
    sql = (f"SELECT t.id, t.prev_hash, t.row_hash, {expected} AS expected "  # nosec B608
           f"FROM {qt} t WHERE t.row_hash IS NOT NULL ORDER BY t.id")  # nosec B608
    for n, (id_, prev, row_hash, exp) in enumerate(_stream(cursor, sql)):
        if deadline is not None and n % BATCH == 0 and time.monotonic() > deadline:
            raise VerificationTimeout(f"{schema}.{table}: stopped after {n:,} rows")
        rows_chained += 1
        if row_hash != exp and len(modified) < _MAX_REPORTED:
            modified.append(id_)            # the report lists at most _MAX_REPORTED anyway
        if not walking:
            orphan(id_)
            continue
        if prev in recent_set and prev != cur:
            # A second row claiming a link the walk already used.
            forks.append({"type": "chain_fork", "prev_hash": prev, "ids": [id_]})
            orphan(id_)
            continue
        pending.setdefault(prev, []).append((id_, row_hash))
        waiting += 1
        while cur in pending:
            nxt = sorted(pending.pop(cur))
            waiting -= len(nxt)
            if len(nxt) > 1:
                forks.append({"type": "chain_fork", "prev_hash": cur, "ids": [i for i, _ in nxt][:10]})
                for i, _ in nxt[1:]:
                    orphan(i)
            consumed(cur)
            cur = nxt[0][1]
            anchors_left.discard(cur)
        if waiting > PENDING_LIMIT:
            # A predecessor is missing: nothing from here on can link.
            walking = False
            for i, _ in itertools.chain.from_iterable(pending.values()):
                orphan(i)
            pending.clear()
            waiting = 0
    for i, _ in itertools.chain.from_iterable(pending.values()):
        orphan(i)
    chain_end = cur

    violations.extend({"type": "row_modified", "id": i} for i in modified)
    violations.extend(forks[:_MAX_REPORTED])
    if orphan_count:
        violations.append({
            "type": "orphan_rows", "count": orphan_count, "ids": sorted(orphan_ids)[:10],
            "meaning": "rows not reachable from genesis: a predecessor was deleted/replaced, or a row was inserted out-of-band",
        })

    if recorded_head is not None and recorded_head != chain_end:
        violations.append({
            "type": "head_mismatch", "recorded_head": recorded_head, "chain_end": chain_end,
            "meaning": "rows at the tail of the chain were removed (or the head pointer was altered)",
        })
    if recorded_head is None and rows_chained:
        violations.append({"type": "head_missing", "meaning": "chained rows exist but no head pointer"})

    missing_anchors = []
    for a in (anchors or ()):
        a = (a or "").strip()
        if a and a in anchors_left and a not in missing_anchors:
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
        "rows_chained": rows_chained,
        "unchained_legacy_rows": legacy,
        "unchained_rows_after_chain_start": unchained_after,
        "head": recorded_head,
        "ok": not violations,
        "warnings": (["unchained rows inserted after the chain started (chaining failed for them)"]
                     if unchained_after else []),
        "violations": violations[:_MAX_REPORTED],
    }


def verify_schema(cursor, schema, anchors_by_table=None, deadline=None):
    anchors_by_table = anchors_by_table or {}
    return [verify_table(cursor, schema, t, anchors_by_table.get(t), deadline) for t in CHAINED_TABLES]


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
