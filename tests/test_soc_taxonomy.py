"""SOC taxonomy: every emitted event maps somewhere sensible, nothing is invented."""
import ast
import pathlib

from utils import soc_taxonomy as tax

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _emitted_event_types():
    """Literal first arguments of every log_security_event / send_security_alert call."""
    found = set()
    files = list((ROOT / "blueprints").glob("*.py")) + list((ROOT / "utils").glob("*.py")) + [
        ROOT / "app.py", ROOT / "extensions.py", ROOT / "database.py", ROOT / "wsgi.py"]
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in ("log_security_event", "send_security_alert") and node.args:
                    a = node.args[0]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        found.add(a.value)
    return found


def test_every_emitted_event_type_lands_in_a_real_family():
    for et in _emitted_event_types():
        c = tax.classify(et)
        assert c["family"] in tax.FAMILIES, et
        assert c["label"], et


def test_security_relevant_events_are_not_left_in_other():
    """A new attack-shaped event family must be mapped, not silently 'other'."""
    others = sorted(et for et in _emitted_event_types() if tax.classify(et)["family"] == "other")
    assert others == [], f"map these in utils/soc_taxonomy.py: {others}"


def test_detection_catalog_matches_the_real_rules():
    from utils.detections import RULES
    real = {r.name for r in RULES}
    assert real == set(tax.DETECTIONS), (
        f"unmapped rules: {sorted(real - set(tax.DETECTIONS))}; stale mappings: {sorted(set(tax.DETECTIONS) - real)}")


def test_every_technique_referenced_has_a_name():
    ids = set()
    for _, _, tids in tax._EXACT.values():
        ids.update(tids)
    for _, tids in tax._PREFIX.values():
        ids.update(tids)
    for _, tids, _ in tax.DETECTIONS.values():
        ids.update(tids)
    assert ids <= set(tax.MITRE), sorted(ids - set(tax.MITRE))


def test_technique_links_are_well_formed():
    t = tax.classify("waf.sqli_blocked")["mitre"][0]
    assert t["id"] == "T1190" and t["url"] == "https://attack.mitre.org/techniques/T1190/"
    sub = tax._technique("T1110.004")
    assert sub["url"] == "https://attack.mitre.org/techniques/T1110/004/"


def test_detection_events_use_the_rule_label():
    c = tax.classify("detection.credential_stuffing")
    assert c["family"] == "detection" and c["label"] == "Credential stuffing"
    assert [m["id"] for m in c["mitre"]] == ["T1110.004"]


def test_unknown_events_fall_back_to_prefix_then_other():
    assert tax.classify("waf.something_new")["family"] == "waf"
    assert tax.classify("billing.brand_new")["family"] == "billing"
    assert tax.classify("zzz.unknown")["family"] == "other"
    assert tax.classify("")["family"] == "other"
    assert tax.classify(None)["family"] == "other"


def test_no_invented_attack_mapping_for_business_events():
    for et in ("billing.state_changed", "platform_admin.ticket_created", "privacy.consent"):
        assert tax.classify(et)["mitre"] == [], et


def test_alert_and_severity_rules():
    assert tax.is_alert("auth.failure", "ERROR")
    assert tax.is_alert("auth.failure", "CRITICAL")
    assert not tax.is_alert("auth.failure", "WARNING")
    assert tax.is_alert("detection.photo_replay", "WARNING")
    assert tax.severity_of("x", "CRITICAL") == "critical"
    assert tax.severity_of("x", "ERROR") == "high"
    assert tax.severity_of("x", "warning") == "medium"
    assert tax.severity_of("x", None) == "info"


def test_family_filters_cover_exact_and_prefix_members():
    exact, prefixes = tax.event_types_for_family("waf")
    assert "waf.sqli_blocked" in exact and "waf." in prefixes
    _, det = tax.event_types_for_family("detection")
    assert det == ["detection."]


def test_tenant_hint():
    assert tax.tenant_hint("/acme/admin") == "acme"
    assert tax.tenant_hint("/acme") == "acme"
    assert tax.tenant_hint("/login") is None            # reserved top-level route
    assert tax.tenant_hint("/super_admin/x") is None
    assert tax.tenant_hint("/soc/api/overview") is None
    assert tax.tenant_hint("/index.php") is None         # looks like a file, not a slug
    assert tax.tenant_hint("") is None and tax.tenant_hint(None) is None
