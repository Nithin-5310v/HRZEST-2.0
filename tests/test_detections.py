"""Correlation rules in utils/detections.py, driven with fixture rows.

Each test seeds exactly the telemetry that should (or should not) trip a
rule, runs the real SQL against the real tables, and inspects what would be
emitted. `emit` is injected so no alert is actually dispatched.
"""
import pytest

from utils import detections as det


class Sink:
    def __init__(self):
        self.events = []

    def __call__(self, event_type, message, level="WARNING", **fields):
        self.events.append({"event_type": event_type, "message": message, "level": level, **fields})

    def types(self):
        return [e["event_type"] for e in self.events]


@pytest.fixture
def sink():
    return Sink()


def _event(cur, event_type, identifier=None, ip=None, level="WARNING", n=1):
    for _ in range(n):
        cur.execute(
            "INSERT INTO security_events (event_type, level, message, identifier, ip) VALUES (%s,%s,'t',%s,%s)",
            (event_type, level, identifier, ip))


def _punch(cur, emp, lat=None, lon=None, device=None, flags=None, liveness="passed",
           outcome="accepted", minutes_ago=0):
    cur.execute(
        "INSERT INTO punch_evidence (employee_id, channel, device_id, lat, lon, flags, liveness, outcome, created_at) "
        "VALUES (%s,'mobile',%s,%s,%s,%s,%s,%s, NOW() - (%s * INTERVAL '1 minute'))",
        (emp, device, lat, lon, flags, liveness, outcome, minutes_ago))


def _run(db_engine, sink, scope="global", label="public"):
    cur = db_engine.cursor()
    out = det.run_global(cur, sink) if scope == "global" else det.run_tenant(cur, label, sink)
    cur.close()
    return out


class TestGlobalRules:
    def test_credential_stuffing_fires(self, db_engine, sink):
        cur = db_engine.cursor()
        for i in range(6):
            _event(cur, "auth.failure", identifier=f"user{i}", ip="198.51.100.50", n=2)
        out = _run(db_engine, sink)
        assert [f["rule"] for f in out] == ["credential_stuffing"]
        assert sink.events[0]["level"] == "ERROR"          # ERROR pages via utils/alerts
        assert sink.events[0]["ip"] == "198.51.100.50"

    def test_one_user_mistyping_does_not_fire(self, db_engine, sink):
        _event(db_engine.cursor(), "auth.failure", identifier="alice", ip="198.51.100.51", n=15)
        assert _run(db_engine, sink) == []

    def test_cooldown_suppresses_repeat_alerts(self, db_engine, sink):
        cur = db_engine.cursor()
        for i in range(6):
            _event(cur, "auth.failure", identifier=f"u{i}", ip="198.51.100.52", n=2)
        assert len(_run(db_engine, sink)) == 1
        assert _run(db_engine, sink) == []                 # same key inside cooldown
        cur.execute("UPDATE detection_state SET last_fired = NOW() - INTERVAL '2 hours'")
        assert len(_run(db_engine, sink)) == 1             # cooldown elapsed

    def test_distributed_bruteforce_against_one_account(self, db_engine, sink):
        cur = db_engine.cursor()
        for i in range(6):
            _event(cur, "auth.failure", identifier="ceo", ip=f"198.51.100.{60 + i}")
        out = _run(db_engine, sink)
        assert "distributed_bruteforce" in [f["rule"] for f in out]

    def test_tenant_boundary_probe(self, db_engine, sink):
        _event(db_engine.cursor(), "tenant.session_mismatch", identifier="eve", n=3)
        assert [f["rule"] for f in _run(db_engine, sink)] == ["tenant_boundary_probe"]

    def test_privilege_probe_needs_error_level_denials(self, db_engine, sink):
        cur = db_engine.cursor()
        _event(cur, "access.denied", identifier="bob", level="INFO", n=10)
        assert _run(db_engine, sink) == []
        _event(cur, "access.denied", identifier="bob", level="ERROR", n=5)
        assert [f["rule"] for f in _run(db_engine, sink)] == ["privilege_probe"]

    def test_recon_then_probe_correlates_honeypot_with_app_activity(self, db_engine, sink):
        cur = db_engine.cursor()
        _event(cur, "honeypot.tcp_decoy_hit", identifier="198.51.100.77", ip="10.0.0.9")
        assert _run(db_engine, sink) == []                 # recon alone is just noise
        _event(cur, "waf.sqli_blocked", ip="198.51.100.77", level="ERROR", n=2)
        out = _run(db_engine, sink)
        assert [f["rule"] for f in out] == ["recon_then_probe"]
        assert out[0]["key"] == "198.51.100.77"

    def test_http_honeypot_hit_also_correlates(self, db_engine, sink):
        cur = db_engine.cursor()
        _event(cur, "secops.honeypot_triggered", ip="198.51.100.78", level="CRITICAL")
        _event(cur, "auth.failure", identifier="x", ip="198.51.100.78")
        assert "recon_then_probe" in [f["rule"] for f in _run(db_engine, sink)]

    def test_waf_burst_needs_many_sources(self, db_engine, sink):
        cur = db_engine.cursor()
        _event(cur, "waf.xss_blocked", ip="198.51.100.90", level="ERROR", n=60)
        assert _run(db_engine, sink) == []                 # one noisy scanner is the WAF's job
        for i in range(6):
            _event(cur, "waf.xss_blocked", ip=f"198.51.100.{100 + i}", level="ERROR", n=10)
        assert "waf_burst_distributed" in [f["rule"] for f in _run(db_engine, sink)]

    def test_pii_reveal_burst(self, db_engine, sink):
        _event(db_engine.cursor(), "data.reveal", identifier="hr1", n=25)
        assert [f["rule"] for f in _run(db_engine, sink)] == ["pii_reveal_burst"]
        assert sink.events[0]["level"] == "WARNING"


class TestTenantRules:
    def test_impossible_travel(self, db_engine, sink):
        cur = db_engine.cursor()
        _punch(cur, "E1", 12.9716, 77.5946, minutes_ago=30)     # Bengaluru
        _punch(cur, "E1", 19.0760, 72.8777, minutes_ago=5)      # Mumbai, ~845 km, 25 min later
        out = _run(db_engine, sink, "tenant", "acme")
        assert [f["rule"] for f in out] == ["impossible_travel"]
        assert out[0]["key"] == "acme:E1"

    def test_ordinary_movement_does_not_fire(self, db_engine, sink):
        cur = db_engine.cursor()
        _punch(cur, "E2", 12.9716, 77.5946, minutes_ago=30)
        _punch(cur, "E2", 12.9750, 77.6000, minutes_ago=5)
        assert _run(db_engine, sink, "tenant", "acme") == []

    def test_far_apart_but_hours_apart_is_fine(self, db_engine, sink):
        cur = db_engine.cursor()
        _punch(cur, "E3", 12.9716, 77.5946, minutes_ago=100)
        _punch(cur, "E3", 19.0760, 72.8777, minutes_ago=5)      # >60 min apart
        assert _run(db_engine, sink, "tenant", "acme") == []

    def test_rejected_punches_do_not_count(self, db_engine, sink):
        cur = db_engine.cursor()
        _punch(cur, "E4", 12.9716, 77.5946, minutes_ago=30)
        _punch(cur, "E4", 19.0760, 72.8777, minutes_ago=5, outcome="rejected")
        assert _run(db_engine, sink, "tenant", "acme") == []

    def test_shared_device(self, db_engine, sink):
        cur = db_engine.cursor()
        for e in ("A", "B", "C"):
            _punch(cur, e, device="dev-shared-0001")
        _punch(cur, "D", device="dev-solo-00002")
        out = _run(db_engine, sink, "tenant", "acme")
        assert [(f["rule"], f["key"]) for f in out] == [("shared_device", "acme:dev-shared-0001")]

    def test_repeat_mock_location_and_liveness_and_replay(self, db_engine, sink):
        cur = db_engine.cursor()
        for _ in range(3):
            _punch(cur, "M1", flags="mock_location", outcome="rejected", liveness="not_required")
        for _ in range(5):
            _punch(cur, "L1", liveness="failed", outcome="rejected")
        for _ in range(2):
            _punch(cur, "R1", flags="photo_exact_replay", outcome="rejected")
        rules = sorted(f["rule"] for f in _run(db_engine, sink, "tenant", "acme"))
        assert rules == ["photo_replay", "repeat_liveness_failure", "repeat_mock_location"]

    def test_mass_export_and_sensitive_change(self, db_engine, sink):
        cur = db_engine.cursor()
        for _ in range(5):
            cur.execute("INSERT INTO audit_logs (actor, action) VALUES ('hr2','export_monthly_report')")
        cur.execute("INSERT INTO audit_logs (actor, action) VALUES ('boss','update_employee_role')")
        cur.execute("INSERT INTO audit_logs (actor, action) VALUES ('boss','update_profile')")   # not sensitive
        out = _run(db_engine, sink, "tenant", "acme")
        assert sorted(f["rule"] for f in out) == ["mass_export", "sensitive_admin_change"]
        change = next(e for e in sink.events if e["event_type"] == "detection.sensitive_admin_change")
        assert "update_employee_role" in change["message"] and change["identifier"] == "boss"


class TestEngine:
    def test_every_rule_sql_executes(self, db_engine):
        """Catches a typo'd column/escape in any rule even when no fixture
        data exercises it."""
        cur = db_engine.cursor()
        for rule in det.RULES:
            cur.execute(rule.sql, rule.params())
            cur.fetchall()

    def test_rules_have_unique_names_and_valid_levels(self):
        names = [r.name for r in det.RULES]
        assert len(names) == len(set(names))
        assert all(r.level in ("WARNING", "ERROR") and r.scope in ("global", "tenant") for r in det.RULES)

    def test_run_detections_isolates_failures(self, monkeypatch, sink):
        """One bad schema/rule must not stop the run or raise."""
        import database

        def boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(database, "get_db_connection", boom)
        monkeypatch.setattr(database, "get_master_db", boom)
        assert det.run_detections(emit=sink) == []

    def test_env_threshold_override(self, monkeypatch):
        monkeypatch.setenv("DETECT_SHARED_DEVICE_EMPLOYEES", "2")
        rules = {r.name: r for r in det._rules()}
        assert rules["shared_device"].params() == (2,)
