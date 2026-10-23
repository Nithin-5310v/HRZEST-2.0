"""Tamper-evidence for audit_logs / security_events (migrations/
audit_integrity.py + utils/audit_chain.py).

Runs against an isolated schema (`audit_it`) built with the real migration
code, so these tests exercise exactly what production installs without
touching the audit rows other tests rely on.

Regression coverage for the audit finding that the "immutable" log could be
defeated by anyone holding the app's DB credentials (DROP TRIGGER, or the
audit.bypass GUC) with no way to notice.
"""
import json
import os
import subprocess
import sys
import threading

import psycopg2
import pytest

from migrations import audit_integrity as ai
from utils import audit_chain

SCHEMA = "audit_it"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _connect():
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ["DB_USER"], password=os.environ.get("DB_PASS", ""),
        dbname=os.environ["DB_NAME"],
    )
    conn.autocommit = True
    return conn


@pytest.fixture
def chain(db_engine):
    """A fresh schema with real audit tables + the real chain migration."""
    cur = db_engine.cursor()
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCHEMA}")
    cur.execute(f"SET search_path TO {SCHEMA}, public")
    for tbl in ai.CHAINED_TABLES:
        cur.execute(f"CREATE TABLE {SCHEMA}.{tbl} (LIKE public.{tbl} INCLUDING DEFAULTS)")
        cur.execute(f"ALTER TABLE {SCHEMA}.{tbl} ADD PRIMARY KEY (id)")
    ai.init_audit_integrity(cur, db_engine)
    yield cur
    cur.execute("SET search_path TO public")
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.close()


def _add_audit(cur, n=1, prefix="act"):
    for i in range(n):
        cur.execute(
            "INSERT INTO audit_logs (actor, actor_type, action, target_table, target_id, detail, ip_address) "
            "VALUES (%s,'admin',%s,'employees',%s,%s,'203.0.113.5')",
            ("hr_alice", f"{prefix}.{i}", str(i), f"detail {i}"))


def _add_event(cur, n=1):
    for i in range(n):
        cur.execute(
            "INSERT INTO security_events (event_type, level, message, identifier, ip) "
            "VALUES ('auth.failure','WARNING',%s,'bob','203.0.113.9')", (f"msg {i}",))


def _bypass(cur, sql, *params):
    cur.execute("SET audit.bypass = 'on'")
    try:
        cur.execute(sql, params)
    finally:
        cur.execute("SET audit.bypass = 'off'")


def _report(cur, table="audit_logs", anchors=None):
    return audit_chain.verify_table(cur, SCHEMA, table, anchors)


class TestChainIsWrittenByTheDatabase:
    def test_rows_get_linked_hashes(self, chain):
        _add_audit(chain, 3)
        chain.execute("SELECT id, prev_hash, row_hash FROM audit_logs ORDER BY id")
        rows = chain.fetchall()
        assert len(rows) == 3
        assert rows[0][1] == ai.GENESIS_HASH
        assert rows[1][1] == rows[0][2]
        assert rows[2][1] == rows[1][2]
        assert all(len(r[2]) == 64 for r in rows)
        chain.execute("SELECT head FROM audit_chain_head WHERE tbl='audit_logs'")
        assert chain.fetchone()[0] == rows[2][2]

    def test_each_table_has_its_own_chain(self, chain):
        _add_audit(chain, 2)
        _add_event(chain, 2)
        chain.execute("SELECT tbl FROM audit_chain_head ORDER BY tbl")
        assert [r[0] for r in chain.fetchall()] == ["audit_logs", "security_events"]
        assert _report(chain, "audit_logs")["ok"]
        assert _report(chain, "security_events")["ok"]

    def test_caller_cannot_forge_hashes(self, chain):
        chain.execute(
            "INSERT INTO audit_logs (actor, action, prev_hash, row_hash) VALUES ('mallory','x',%s,%s)",
            ("f" * 64, "e" * 64))
        chain.execute("SELECT prev_hash, row_hash FROM audit_logs")
        prev, h = chain.fetchone()
        assert prev == ai.GENESIS_HASH and h != "e" * 64
        assert _report(chain)["ok"]

    def test_empty_table_verifies_clean(self, chain):
        r = _report(chain)
        assert r["ok"] and r["rows_chained"] == 0


class TestTamperingIsDetected:
    def test_modified_row(self, chain):
        _add_audit(chain, 3)
        _bypass(chain, "UPDATE audit_logs SET detail='nothing to see' WHERE action='act.1'")
        r = _report(chain)
        assert not r["ok"]
        assert any(v["type"] == "row_modified" for v in r["violations"])

    def test_deleted_middle_row(self, chain):
        _add_audit(chain, 4)
        _bypass(chain, "DELETE FROM audit_logs WHERE action='act.1'")
        r = _report(chain)
        assert not r["ok"]
        assert any(v["type"] == "orphan_rows" for v in r["violations"])

    def test_deleted_tail_rows(self, chain):
        _add_audit(chain, 4)
        _bypass(chain, "DELETE FROM audit_logs WHERE action IN ('act.2','act.3')")
        r = _report(chain)
        assert not r["ok"]
        assert any(v["type"] == "head_mismatch" for v in r["violations"])

    def test_whole_table_wiped(self, chain):
        _add_audit(chain, 3)
        _bypass(chain, "DELETE FROM audit_logs")
        r = _report(chain)
        assert not r["ok"]
        assert any(v["type"] == "head_mismatch" for v in r["violations"])

    def test_row_inserted_out_of_band_with_trigger_disabled(self, chain):
        _add_audit(chain, 2)
        chain.execute("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_chain")
        chain.execute(
            "INSERT INTO audit_logs (actor, action, prev_hash, row_hash) VALUES ('mallory','planted',%s,%s)",
            ("a" * 64, "b" * 64))
        chain.execute("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_chain")
        r = _report(chain)
        assert not r["ok"]
        types = {v["type"] for v in r["violations"]}
        assert "row_modified" in types or "orphan_rows" in types

    def test_unprivileged_mutation_is_still_rejected(self, chain):
        _add_audit(chain, 1)
        with pytest.raises(psycopg2.Error):
            chain.execute("UPDATE audit_logs SET detail='x'")
        with pytest.raises(psycopg2.Error):
            chain.execute("DELETE FROM audit_logs")


class TestAnchors:
    def test_full_chain_rewrite_is_caught_by_an_offbox_anchor(self, chain):
        """The case the in-DB chain alone cannot catch: an attacker who can
        rewrite rows AND recompute every hash AND the head pointer."""
        _add_audit(chain, 5)
        anchor = audit_chain.build_anchor(_report(chain))["head"]
        assert anchor

        # Attacker: wipe, replay a doctored history through the real trigger
        # (so the chain is perfectly self-consistent), head pointer included.
        chain.execute("SET audit.bypass = 'on'")
        chain.execute("DELETE FROM audit_logs")
        chain.execute("DELETE FROM audit_chain_head WHERE tbl='audit_logs'")
        chain.execute("SET audit.bypass = 'off'")
        _add_audit(chain, 5, prefix="doctored")

        r = _report(chain)
        assert r["ok"], "the rewritten chain is internally consistent (that is the point)"
        r2 = _report(chain, anchors=[anchor])
        assert not r2["ok"]
        assert any(v["type"] == "anchor_missing" for v in r2["violations"])

    def test_legitimate_growth_keeps_old_anchor_valid(self, chain):
        _add_audit(chain, 3)
        anchor = _report(chain)["head"]
        _add_audit(chain, 3, prefix="later")
        assert _report(chain, anchors=[anchor])["ok"]

    def test_build_anchor_shape(self, chain):
        _add_audit(chain, 1)
        a = audit_chain.build_anchor(_report(chain))
        assert a["audit_anchor"] is True
        assert a["schema"] == SCHEMA and a["table"] == "audit_logs"
        assert len(a["head"]) == 64


class TestEdgeCases:
    def test_unchained_row_after_chain_start_is_a_warning_not_a_break(self, chain):
        _add_audit(chain, 2)
        chain.execute("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_chain")
        _add_audit(chain, 1, prefix="gap")
        chain.execute("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_chain")
        _add_audit(chain, 1, prefix="after")
        r = _report(chain)
        assert r["unchained_rows_after_chain_start"] == 1
        assert r["warnings"]
        assert r["ok"]

    def test_legacy_rows_before_the_chain_are_counted_not_failed(self, chain):
        chain.execute("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_chain")
        _add_audit(chain, 3, prefix="legacy")
        chain.execute("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_chain")
        _add_audit(chain, 2)
        r = _report(chain)
        assert r["ok"]
        assert r["unchained_legacy_rows"] == 3
        assert r["rows_chained"] == 2

    def test_concurrent_writers_never_fork_the_chain(self, chain):
        per_thread, threads = 15, 6
        errors = []

        def worker(n):
            try:
                c = _connect()
                cur = c.cursor()
                cur.execute(f"SET search_path TO {SCHEMA}, public")
                for i in range(per_thread):
                    cur.execute(
                        "INSERT INTO audit_logs (actor, action) VALUES (%s,%s)", (f"t{n}", f"op.{i}"))
                c.close()
            except Exception as exc:   # noqa: BLE001
                errors.append(exc)

        ts = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errors, errors
        r = _report(chain)
        assert r["rows_chained"] == per_thread * threads
        assert r["ok"], r["violations"]
        assert r["unchained_rows_after_chain_start"] == 0


class TestBypassIsCompiledOutOfProduction:
    def test_strict_function_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)
        assert "audit.bypass" not in ai._immutability_fn_sql()
        assert ai.bypass_allowed() is False

    def test_bypass_available_for_dev_and_testing(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        assert "audit.bypass" in ai._immutability_fn_sql()
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("FLASK_ENV", "testing")
        assert "audit.bypass" in ai._immutability_fn_sql()

    def test_production_function_actually_rejects_despite_the_guc(self, chain, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)
        ai.init_audit_integrity(chain, _NoCommit())
        _add_audit(chain, 1)
        chain.execute("SET audit.bypass = 'on'")
        with pytest.raises(psycopg2.Error):
            chain.execute("DELETE FROM audit_logs")
        chain.execute("SET audit.bypass = 'off'")


class _NoCommit:
    def commit(self):
        pass


class TestVerifierCli:
    def test_cli_reports_ok_and_emits_anchor(self, chain, db_engine):
        _add_audit(chain, 2)
        env = dict(os.environ)
        env.pop("DB_OWNER_USER", None)
        out = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "verify_audit_chain.py"),
             "--schema", SCHEMA, "--emit-anchor"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        parsed = [json.loads(ln) for ln in out.stdout.splitlines() if ln.startswith("{")]
        anchors = [a for a in parsed if a.get("audit_anchor")]
        assert any(a["table"] == "audit_logs" and a["schema"] == SCHEMA for a in anchors)

    def test_cli_exit_code_2_on_tampering(self, chain):
        _add_audit(chain, 3)
        _bypass(chain, "UPDATE audit_logs SET detail='x' WHERE action='act.0'")
        out = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "verify_audit_chain.py"), "--schema", SCHEMA],
            cwd=REPO_ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=120)
        assert out.returncode == 2, out.stdout + out.stderr
        assert "VIOLATION" in out.stdout


class TestScheduledJobs:
    """utils/audit_jobs.py: the anchor emitter and the daily verifier."""

    def test_anchors_carry_the_current_chain_head(self, chain, monkeypatch):
        import extensions
        from utils import audit_jobs
        _add_audit(chain, 3)
        logged = []
        monkeypatch.setattr(extensions.app_log, "info",
                            lambda msg, *a, **k: logged.append((msg, k.get("extra"))))
        audit_jobs.emit_anchors()
        mine = [e for m, e in logged
                if m == "audit_anchor" and e and e.get("schema") == SCHEMA and e.get("table") == "audit_logs"]
        assert mine, "no anchor emitted for the test schema"
        assert mine[0]["audit_anchor"] is True
        assert mine[0]["head"] == _report(chain)["head"]

    def test_anchor_lines_round_trip_through_the_cli_loader(self, tmp_path):
        """What the app logs must be what scripts/verify_audit_chain.py --anchors reads."""
        import importlib.util
        import json as _json
        spec = importlib.util.spec_from_file_location(
            "vac", os.path.join(REPO_ROOT, "scripts", "verify_audit_chain.py"))
        vac = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vac)
        f = tmp_path / "anchors.jsonl"
        loki_style = 'ts=2026-10-15 ' + _json.dumps({
            "message": "audit_anchor", "audit_anchor": True, "schema": "public",
            "table": "audit_logs", "head": "a" * 64, "last_id": 7})
        f.write_text(loki_style + "\nnot json at all\n", encoding="utf-8")
        assert vac._load_anchors(str(f)) == {("public", "audit_logs"): ["a" * 64]}

    def test_daily_verification_pages_critical_on_tampering(self, chain, monkeypatch):
        import extensions
        from utils import audit_jobs
        _add_audit(chain, 3)
        _bypass(chain, "UPDATE audit_logs SET detail='rewritten' WHERE action='act.1'")
        events = []
        monkeypatch.setattr(extensions, "log_security_event",
                            lambda et, msg, level="WARNING", **f: events.append((et, level, f)))
        audit_jobs.run_full_verification()
        hit = [e for e in events
               if e[0] == "audit.chain_violation" and e[2].get("identifier") == f"{SCHEMA}.audit_logs"]
        assert hit and hit[0][1] == "CRITICAL"
