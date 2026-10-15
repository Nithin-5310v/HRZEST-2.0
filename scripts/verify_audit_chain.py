#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the audit_logs / security_events hash chains and emit off-box anchors.

    python scripts/verify_audit_chain.py                       # verify every schema
    python scripts/verify_audit_chain.py --schema public
    python scripts/verify_audit_chain.py --emit-anchor         # also print anchor lines
    python scripts/verify_audit_chain.py --anchors anchors.jsonl

Exit status: 0 chains intact, 2 tampering/inconsistency detected, 3 could not run.

Schedule it (scripts/hrzest-audit-verify.timer): the `--emit-anchor` lines go to
stdout, which the container/journal log pipeline ships to Loki -- a separate
host the database credentials can't reach. To audit against history, export
the anchor lines from Loki (`{job=...} |= "audit_anchor"`) to a JSONL file and
pass it with --anchors: every head ever shipped must still be on the chain.

Connects as the owner role when DB_OWNER_USER is configured (the runtime role
deliberately has no access to audit_chain_head).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_anchors(path):
    by_key = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            # Tolerate Loki exports where the JSON is embedded in a larger line.
            start = line.find("{")
            try:
                rec = json.loads(line[start:]) if start >= 0 else None
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("audit_anchor") and rec.get("head"):
                by_key.setdefault((rec.get("schema"), rec.get("table")), []).append(rec["head"])
    return by_key


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema", action="append", help="limit to schema(s); default: every schema with a chain")
    ap.add_argument("--emit-anchor", action="store_true", help="print one anchor JSON line per chain")
    ap.add_argument("--anchors", help="JSONL file of previously emitted anchors to check against")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)

    from database import get_db_connection, owner_privileges
    from utils.audit_chain import list_chained_schemas, verify_schema, build_anchor

    anchors = _load_anchors(args.anchors) if args.anchors else {}
    reports = []
    try:
        with owner_privileges():
            conn = get_db_connection()
            cur = conn.cursor()
            schemas = args.schema or list_chained_schemas(cur)
            for schema in schemas:
                per_table = {t: anchors.get((schema, t), []) for t in ("audit_logs", "security_events")}
                reports.extend(verify_schema(cur, schema, per_table))
            cur.close()
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- CLI boundary
        print(f"verify_audit_chain: could not run: {exc}", file=sys.stderr)
        return 3

    bad = [r for r in reports if not r["ok"]]
    if args.json:
        print(json.dumps({"ok": not bad, "reports": reports}, indent=2, default=str))
    else:
        for r in reports:
            flag = "OK   " if r["ok"] else "FAIL "
            print(f"{flag}{r['schema']}.{r['table']}: chained={r['rows_chained']} "
                  f"legacy_unchained={r['unchained_legacy_rows']} head={str(r['head'])[:12]}")
            for w in r["warnings"]:
                print(f"      warning: {w}")
            for v in r["violations"]:
                print(f"      VIOLATION {v}")
    if args.emit_anchor:
        for r in reports:
            if r["head"]:
                print(json.dumps(build_anchor(r)), flush=True)
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
