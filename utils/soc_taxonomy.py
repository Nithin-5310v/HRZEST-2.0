# -*- coding: utf-8 -*-
"""Classification of the product's security events for the SOC console.

The app emits ~150 distinct `event_type` strings through log_security_event().
The console needs to group them (family), explain them (label), rank them
(level) and tie the attack-shaped ones to MITRE ATT&CK so an analyst can pivot
on technique. This module is that single mapping.

Rules of the road
-----------------
* Map to ATT&CK only where the link is defensible. Events that are plain
  business audit (billing state changes, ticket updates, ...) or fraud /
  integrity signals that ATT&CK does not model (mock location, liveness
  failure) carry NO technique -- an empty list is the honest answer, and the
  UI shows the family instead.
* Unknown event types fall back to their dotted prefix, then to "other".
  A new `log_security_event("foo.bar", ...)` therefore shows up under the
  family "foo" immediately; it just has no technique until someone maps it.
* Pure functions, no I/O, so the whole table is unit-testable.
"""

LEVEL_RANK = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO")

# technique id -> (name). Only techniques referenced below.
MITRE = {
    "T1110": "Brute Force",
    "T1110.001": "Brute Force: Password Guessing",
    "T1110.003": "Brute Force: Password Spraying",
    "T1110.004": "Brute Force: Credential Stuffing",
    "T1078": "Valid Accounts",
    "T1190": "Exploit Public-Facing Application",
    "T1595": "Active Scanning",
    "T1595.002": "Active Scanning: Vulnerability Scanning",
    "T1595.003": "Active Scanning: Wordlist Scanning",
    "T1539": "Steal Web Session Cookie",
    "T1098": "Account Manipulation",
    "T1556.006": "Modify Authentication Process: Multi-Factor Authentication",
    "T1070": "Indicator Removal",
    "T1565.001": "Data Manipulation: Stored Data Manipulation",
    "T1213": "Data from Information Repositories",
    "T1567": "Exfiltration Over Web Service",
    "T1548": "Abuse Elevation Control Mechanism",
    "T1499": "Endpoint Denial of Service",
    "T1059": "Command and Scripting Interpreter",
    "T1083": "File and Directory Discovery",
}

FAMILIES = {
    "auth": "Authentication",
    "access": "Access control",
    "waf": "Web application firewall",
    "honeypot": "Honeypot / deception",
    "ratelimit": "Rate limiting",
    "session": "Session security",
    "detection": "Correlation detections",
    "audit": "Audit integrity",
    "data": "Sensitive data access",
    "privacy": "Privacy & consent",
    "billing": "Payments & webhooks",
    "attendance": "Attendance integrity",
    "device": "Device posture",
    "admin": "Administrative activity",
    "validation": "Input validation",
    "app": "Application health",
    "soc": "SOC console",
    "other": "Other",
}

# (family, human label, techniques)
_EXACT = {
    # --- authentication ---
    "auth.failure": ("auth", "Failed login", ["T1110"]),
    "auth.lockout": ("auth", "Account locked after repeated failures", ["T1110"]),
    "auth.mfa_failure": ("auth", "MFA code rejected", ["T1110"]),
    "auth.platform_admin_login_failed": ("auth", "Platform-admin login failed", ["T1110"]),
    "auth.platform_admin_mfa_failure": ("auth", "Platform-admin MFA rejected", ["T1110"]),
    "auth.injection_attempt": ("auth", "Injection-shaped login input", ["T1190"]),
    "auth.2fa_reset": ("auth", "MFA reset", ["T1556.006"]),
    "auth.2fa_reset_denied": ("auth", "MFA reset denied", ["T1556.006"]),
    "auth.2fa_enrolled": ("auth", "MFA enrolled", []),
    "auth.2fa_enroll_failed": ("auth", "MFA enrolment failed", []),
    "auth.password_changed": ("auth", "Password changed", ["T1098"]),
    "auth.admin_login_success": ("auth", "Admin login", ["T1078"]),
    "auth.platform_admin_login_success": ("auth", "Platform-admin login", ["T1078"]),
    "auth.oauth_login_success": ("auth", "OAuth login", ["T1078"]),
    "auth.mobile_bridge_login": ("auth", "Mobile-to-web bridge login", ["T1078"]),
    "auth.step_up_verified": ("auth", "Step-up MFA passed", []),
    "auth.mfa_email_missing": ("auth", "MFA email missing", []),
    # --- access ---
    "access.denied": ("access", "Access denied", []),
    "tenant.session_mismatch": ("access", "Session used on another tenant's URL", ["T1078"]),
    # --- session ---
    "session.compromised": ("session", "Session flagged compromised", ["T1539"]),
    "session.rejected": ("session", "Session rejected", ["T1539"]),
    # --- WAF / rate limit ---
    "waf.sqli_blocked": ("waf", "SQL injection blocked", ["T1190"]),
    "waf.xss_blocked": ("waf", "Cross-site scripting blocked", ["T1190"]),
    "waf.path_traversal_blocked": ("waf", "Path traversal blocked", ["T1190", "T1083"]),
    "waf.auto_ban": ("waf", "IP auto-banned after repeated blocks", ["T1190"]),
    "waf.ban_skipped_shared_ip": ("waf", "Ban withheld (shared address)", []),
    "waf.ban_write_failed": ("waf", "Ban could not be written", []),
    "waf.redis_error": ("waf", "WAF counter store error", []),
    "ratelimit.exceeded": ("ratelimit", "Rate limit exceeded", ["T1499", "T1110"]),
    # --- deception ---
    "secops.honeypot_triggered": ("honeypot", "Honeypot path requested", ["T1595"]),
    "honeypot.tcp_decoy_hit": ("honeypot", "TCP decoy port probed", ["T1595"]),
    "honeypot.tcp_report_invalid": ("honeypot", "Forged honeypot report", ["T1190"]),
    "secops.honeypot_ban_withheld": ("honeypot", "Honeypot ban withheld (shared address)", []),
    # --- audit integrity ---
    "audit.chain_violation": ("audit", "Audit hash-chain violation", ["T1070", "T1565.001"]),
    # --- data ---
    "data.reveal": ("data", "Sensitive field revealed", ["T1213"]),
    "data.update": ("data", "Sensitive data updated", []),
    "privacy.export": ("privacy", "Personal-data export", ["T1213"]),
    "privacy.erased": ("privacy", "Personal data erased", []),
    "privacy.request_opened": ("privacy", "Privacy request opened", []),
    "privacy.consent": ("privacy", "Consent recorded", []),
    # --- forged / replayed provider callbacks ---
    "billing.signature_invalid": ("billing", "Payment signature invalid", ["T1190"]),
    "billing.overdue_signature_invalid": ("billing", "Overdue-payment signature invalid", ["T1190"]),
    "billing.overdue_order_tenant_mismatch": ("billing", "Payment order / tenant mismatch", ["T1190"]),
    "seats.signature_invalid": ("billing", "Seat-purchase signature invalid", ["T1190"]),
    "auto_debit.signature_invalid": ("billing", "Auto-debit signature invalid", ["T1190"]),
    "org.trial_signature_invalid": ("billing", "Trial signature invalid", ["T1190"]),
    "org.trial_subscription_mismatch": ("billing", "Trial subscription mismatch", ["T1190"]),
    "org.duplicate_company_blocked": ("admin", "Duplicate company signup blocked", []),
    # --- integrity / fraud (no ATT&CK equivalent) ---
    "attendance.combo_rejected": ("attendance", "Punch rejected: factor policy", []),
    "attendance.office_networks_changed": ("attendance", "Office network list changed", ["T1098"]),
    "device.wifi_risk_reported": ("device", "Risky Wi-Fi reported by device", []),
    # --- app health ---
    "app.server_error": ("app", "Unhandled server error", []),
    "validation.failure": ("validation", "Input validation failure", []),
}

_PREFIX = {
    "auth": ("auth", []),
    "access": ("access", []),
    "waf": ("waf", ["T1190"]),
    "honeypot": ("honeypot", ["T1595"]),
    "secops": ("honeypot", []),
    "session": ("session", []),
    "audit": ("audit", []),
    "data": ("data", []),
    "privacy": ("privacy", []),
    "billing": ("billing", []),
    "seats": ("billing", []),
    "auto_debit": ("billing", []),
    "trial_billing": ("billing", []),
    "payout": ("billing", []),
    "org": ("admin", []),
    "platform_admin": ("admin", []),
    "admin": ("admin", []),
    "attendance": ("attendance", []),
    "device": ("device", []),
    "ratelimit": ("ratelimit", []),
    "validation": ("validation", []),
    "app": ("app", []),
    "db": ("app", []),          # db.unavailable: PostgreSQL down, requests answered 503
    "tenant": ("access", []),
    "mfa": ("auth", []),
    "soc": ("soc", []),
    "detection": ("detection", []),
}

# Correlation rules from utils/detections.py: name -> (label, techniques, what it means)
DETECTIONS = {
    "credential_stuffing": ("Credential stuffing", ["T1110.004"],
                            "One source address failing against many distinct accounts."),
    "distributed_bruteforce": ("Distributed brute force", ["T1110.001"],
                               "Many source addresses failing against one account."),
    "tenant_boundary_probe": ("Tenant boundary probe", ["T1190"],
                              "A session reaching for another company's data."),
    "privilege_probe": ("Privilege probe", ["T1548"],
                        "Repeated attempts at actions above the caller's role."),
    "recon_then_probe": ("Recon followed by attack", ["T1595", "T1190"],
                         "Honeypot / scanner activity then attack-shaped requests from the same source."),
    "pii_reveal_burst": ("PII reveal burst", ["T1213"],
                         "An account revealing masked personal fields at an unusual rate."),
    "waf_burst_distributed": ("Distributed WAF burst", ["T1190"],
                              "Many addresses hitting the WAF at once."),
    "mass_export": ("Mass data export", ["T1213", "T1567"],
                    "Repeated bulk exports by one account."),
    "sensitive_admin_change": ("Sensitive admin change", ["T1098"],
                               "Privilege / payout / security-setting change."),
    "repeat_liveness_failure": ("Repeated liveness failure", [],
                                "Punch attempts failing the liveness check repeatedly (spoofing indicator)."),
    "impossible_travel": ("Impossible travel", ["T1078"],
                          "Punches too far apart in space for the elapsed time."),
    "shared_device": ("Shared device", [],
                      "One phone punching for several employees (buddy punching)."),
    "repeat_mock_location": ("Repeated mock location", [],
                             "Repeated punches flagged as GPS-spoofed."),
    "photo_replay": ("Photo replay", [],
                     "The same punch photo reused across punches or employees (replayed image)."),
}


def _technique(tid):
    return {"id": tid, "name": MITRE.get(tid, tid),
            "url": "https://attack.mitre.org/techniques/" + tid.replace(".", "/") + "/"}


def classify(event_type, level=None):
    """-> {family, family_label, label, mitre:[{id,name,url}], known:bool}."""
    et = (event_type or "").strip()
    if et.startswith("detection."):
        name = et.split(".", 1)[1]
        label, tids, _ = DETECTIONS.get(name, (name.replace("_", " ").capitalize(), [], ""))
        return {"family": "detection", "family_label": FAMILIES["detection"], "label": label,
                "mitre": [_technique(t) for t in tids], "known": name in DETECTIONS}
    if et in _EXACT:
        fam, label, tids = _EXACT[et]
        return {"family": fam, "family_label": FAMILIES[fam], "label": label,
                "mitre": [_technique(t) for t in tids], "known": True}
    prefix = et.split(".", 1)[0] if et else ""
    if prefix in _PREFIX:
        fam, tids = _PREFIX[prefix]
        label = et.split(".", 1)[1].replace("_", " ").capitalize() if "." in et else et
        return {"family": fam, "family_label": FAMILIES[fam], "label": label,
                "mitre": [_technique(t) for t in tids], "known": False}
    return {"family": "other", "family_label": FAMILIES["other"], "label": et or "(blank)",
            "mitre": [], "known": False}


def family_of(event_type):
    return classify(event_type)["family"]


def event_types_for_family(family):
    """Exact event types that belong to a family (used to build SQL filters)
    plus the prefixes that default into it. -> (exact:list, prefixes:list)."""
    exact = [k for k, v in _EXACT.items() if v[0] == family]
    prefixes = [p + "." for p, v in _PREFIX.items() if v[0] == family]
    if family == "detection":
        prefixes = ["detection."]
    return exact, prefixes


def level_rank(level):
    return LEVEL_RANK.get((level or "").upper(), 1)


def is_alert(event_type, level):
    """What the console calls an *alert*: a correlation detection, or anything
    logged at ERROR / CRITICAL (the same bar utils/alerts.py uses to page)."""
    return (event_type or "").startswith("detection.") or level_rank(level) >= LEVEL_RANK["ERROR"]


def severity_of(event_type, level):
    """critical | high | medium | low | info, from the log level (what the code
    chose) with detections never below 'high' when they page."""
    r = level_rank(level)
    if r >= 4:
        return "critical"
    if r == 3:
        return "high"
    if r == 2:
        return "medium"
    return "info"


def tenant_hint(path):
    """Best-effort tenant slug from a request path ('/acme/admin' -> 'acme').
    Returns None for the platform's own top-level routes. security_events has no
    tenant column, so this is a hint, never an authorisation decision."""
    if not path or not path.startswith("/"):
        return None
    seg = path.split("/", 2)[1].lower() if len(path) > 1 else ""
    if not seg:
        return None
    try:
        from utils.tenant_routing import RESERVED_PATH_SEGMENTS
    except Exception:       # pragma: no cover - import-time safety only
        RESERVED_PATH_SEGMENTS = frozenset()
    if seg in RESERVED_PATH_SEGMENTS or "." in seg:
        return None
    return seg
