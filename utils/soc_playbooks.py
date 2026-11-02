# -*- coding: utf-8 -*-
"""Analyst briefs: what a slice of security events means and what to do.

Every clickable thing on the Overview (a KPI tile, an area, an event type, an
ATT&CK technique, a severity level) opens a brief built from this table plus
live numbers (utils/soc_queries.brief). The text is written for a SOC analyst
triaging this product: what the signal is, why it matters here, what to look
at before acting, and the proportionate response.

Lookup order: exact event type -> detection rule -> KPI -> family -> generic.
Pure data and functions, no I/O.
"""
from utils import soc_taxonomy as tax


def _b(what, why, check, respond):
    return {"what": what, "why": why, "check": list(check), "respond": list(respond)}


# ── KPI tiles on the Overview ────────────────────────────────────────
KPI = {
    "events": ("All security events", _b(
        "Every security-relevant event the platform recorded in the window: logins, denials, WAF blocks, "
        "honeypot hits, detections, data access and admin changes.",
        "This is the baseline. A sudden rise usually means an attack, a scan, a deployment problem or a "
        "misbehaving client -- the breakdown below shows which.",
        ["Which area and event type grew compared with the previous window?",
         "Is the rise concentrated on a few source addresses or accounts (top lists below)?",
         "Do the sources come from hosting ranges, VPNs or Tor (location column)?"],
        ["Drill into the largest area or event type.",
         "If nothing attack-shaped stands out, check application health (5xx / validation failures)."])),
    "critical": ("Critical events", _b(
        "Events the code logs at CRITICAL: the most serious conditions, such as an audit-chain violation "
        "(possible log tampering) or a security control failing.",
        "CRITICAL is the paging level. Each one should be acknowledged by a person, not left in the list.",
        ["What event type is it and which source / account is involved?",
         "Is it a single occurrence or repeating?",
         "For audit-chain violations: run Audit integrity and compare with the off-box anchors."],
        ["Open a case for every critical event you cannot immediately explain.",
         "Preserve evidence (do not restart or clean up the database) until the cause is known."])),
    "errors": ("Error-level events", _b(
        "Events logged at ERROR: blocked attacks (WAF), correlation detections, sessions used across tenants, "
        "repeated privilege violations and similar.",
        "ERROR plus CRITICAL is what the console calls an alert. Three or more ERROR events in an hour raises "
        "the threat level to High.",
        ["Group by event type: one noisy type, or several different kinds of attack?",
         "Same source across several types usually means one attacker working through techniques.",
         "Check whether the source is already banned."],
        ["Ban sources that are clearly hostile and public (not shared / private).",
         "Open a case when an account (not only an IP) is involved."])),
    "detections": ("Correlation detections", _b(
        "Patterns found by the scheduled correlation rules (utils/detections.py), such as credential stuffing, "
        "distributed brute force, impossible travel or mass data export.",
        "A detection already joins many raw events into one finding, so it carries more weight than any single "
        "event. Each rule has a cooldown, so one firing can represent a sustained pattern.",
        ["Which rule fired, and what key (IP, account, device, employee) does it name?",
         "Open the event to see the related events from the same IP / account.",
         "Has the same rule fired for the same key before (Detections page)?"],
        ["Follow the rule's own playbook below.",
         "If it is a false positive, close the case as false positive so the rule can be tuned."])),
    "failed_logins": ("Failed logins", _b(
        "Rejected sign-in attempts: wrong password, rejected MFA code and failed platform-admin logins.",
        "Some failures are normal (typos). Attack patterns are many accounts from one source (credential "
        "stuffing), many sources against one account (spraying / distributed brute force) or failures followed "
        "by a success (a guessed password).",
        ["Top sources: one IP against many accounts? Hosting / VPN / Tor sources?",
         "Top accounts: one account from many IPs or countries?",
         "Is there an 'Admin login' success from the same source right after the failures?",
         "How many ended in a lockout (sub-count on the tile)?"],
        ["Ban hostile public sources; do not ban shared / private addresses.",
         "If a success followed the failures, treat the account as compromised: reset password, revoke sessions, "
         "check MFA enrolment.",
         "Tell the tenant admin when their users are being targeted."])),
    "lockouts": ("Account lockouts", _b(
        "Accounts locked after repeated failed logins.",
        "A lockout stops guessing, but many lockouts can also be an attacker deliberately locking users out.",
        ["Which accounts, and from which sources?", "Did the real user also report being locked out?"],
        ["Ban the attacking sources; unlock genuine users once the source is blocked."])),
    "waf_blocks": ("WAF blocks", _b(
        "Requests the web application firewall refused because they carried SQL injection, cross-site "
        "scripting or path traversal payloads.",
        "A block means the attack did not reach the application, but it shows someone is actively probing for "
        "vulnerabilities. Repeated blocks from one source trigger an automatic ban.",
        ["Which payload types and which paths were targeted?",
         "Is it one scanner (same user agent, many paths) or many sources at once (coordinated)?",
         "Did the same source get any non-blocked requests that succeeded (Events from this IP)?"],
        ["Ban persistent public sources (auto-ban usually handles this).",
         "If a legitimate user is being blocked, review the WAF rule instead of banning."])),
    "honeypot_hits": ("Honeypot hits", _b(
        "Requests to decoy paths or ports that no real user ever visits (for example /.env, /wp-admin, or the "
        "TCP decoy port).",
        "There is no innocent reason to touch a honeypot: it is reconnaissance by a scanner or attacker. It is "
        "a high-confidence, low-noise signal.",
        ["What else did this source do after the hit (recon followed by attack)?",
         "Is the source a known scanner / hosting range?"],
        ["Ban the source if it is public and not shared.",
         "If it then probed real endpoints, open a case."])),
    "unique_ips": ("Source addresses", _b(
        "The distinct client addresses that produced security events in the window, with their location and "
        "network.",
        "Shows who is generating the activity. Many events from few addresses means targeted activity; events "
        "spread across hosting providers and anonymizers suggest automated attack infrastructure.",
        ["Countries and networks you do not expect for this customer base.",
         "Addresses marked VPN / Tor / Hosting.",
         "A private address at the top means the app is seeing its proxy, not the visitors."],
        ["Profile the most active sources and ban the hostile ones.",
         "Fix TRUSTED_PROXY_HOPS if real client IPs are not visible."])),
}

# ── families (areas) ─────────────────────────────────────────────────
FAMILY = {
    "auth": KPI["failed_logins"][1],
    "access": _b(
        "Requests refused because the caller lacked the required role, or used one tenant's session on another "
        "tenant's URL.",
        "Occasional denials are normal (a stale link). Repeated denials from one account are privilege probing; "
        "cross-tenant session use is an attempt to read another company's data.",
        ["Which account and which routes?", "Did the role recently change?", "Any cross-tenant attempts?"],
        ["Open a case for repeated or cross-tenant attempts; revoke the session if it looks stolen."]),
    "waf": KPI["waf_blocks"][1],
    "honeypot": KPI["honeypot_hits"][1],
    "ratelimit": _b(
        "Clients that exceeded a request rate limit.",
        "Usually automation: brute force, scraping or a flood. Can also be a buggy client.",
        ["Which endpoint and which source?", "Is the source a hosting range or a known integration?"],
        ["Ban abusive public sources; contact the tenant if it is their own integration."]),
    "session": _b(
        "Sessions rejected or flagged as compromised by the session risk engine.",
        "A stolen session cookie gives an attacker the user's access without a password.",
        ["Which account, and did the IP / device change mid-session?", "Was the session revoked from Response?"],
        ["Revoke the session, reset the user's password and check what the session accessed."]),
    "detection": KPI["detections"][1],
    "audit": _b(
        "Integrity problems with the hash-chained security log.",
        "A chain violation means a log row was edited, deleted or reordered -- possible tampering to hide an "
        "intrusion.",
        ["Run Audit integrity now.", "Compare the chain head with the off-box anchors in Loki."],
        ["Treat as a potential incident: preserve the database, open a critical case."]),
    "data": _b(
        "Access to and changes of sensitive personal data (for example revealing masked fields).",
        "Bulk or unusual access is a sign of data theft by an insider or a compromised account.",
        ["Who accessed how much, and is it in line with their job?", "Outside working hours?"],
        ["Open a case for unusual volumes; confirm with the tenant admin."]),
    "privacy": _b(
        "Personal-data exports, erasures, consent and privacy requests.",
        "Exports move personal data out of the platform; they should be expected and attributable.",
        ["Who exported, how often, and for which employees?"],
        ["Verify unexpected exports with the tenant admin."]),
    "billing": _b(
        "Payment callbacks with invalid signatures or mismatched orders.",
        "Forged payment callbacks try to mark unpaid plans as paid.",
        ["Source of the callback (should be the payment provider's ranges)", "Which tenant / order?"],
        ["Ban forged sources; reconcile the order with the payment provider."]),
    "attendance": _b(
        "Attendance integrity signals: rejected punches, factor-policy violations, office network changes.",
        "Attendance fraud (buddy punching, GPS spoofing, photo replay) costs the customer money and is often "
        "repeated by the same employees.",
        ["Which employees and devices repeat?", "See Attendance fraud for flags per tenant."],
        ["Share findings with the tenant's HR admin; do not act on single events."]),
    "device": _b("Device posture reports such as risky Wi-Fi.", "Indicates a user on an untrusted network.",
                 ["Which users and how often?"], ["Advise the tenant; no immediate action unless combined with other signals."]),
    "admin": _b(
        "Administrative and platform-level changes.",
        "Privilege, payout and security-setting changes are what an attacker does after taking over an admin.",
        ["Was the change expected by the tenant?", "Was the admin's login from a usual place?"],
        ["Confirm with the tenant; if unexpected, revert and treat the admin account as compromised."]),
    "validation": _b("Requests rejected by input validation.", "Mostly harmless client errors; bursts can be fuzzing.",
                     ["Same source many times? Odd payloads?"], ["Ban only if it is clearly fuzzing from a public source."]),
    "app": _b("Application health: unhandled server errors, database unavailability.",
              "Not an attack by itself, but errors can be triggered by attack payloads or cause outages.",
              ["Error spike timing vs deployments", "Same source triggering errors repeatedly?"],
              ["Hand to engineering; ban only sources deliberately triggering errors."]),
    "soc": _b("Actions taken in this SOC console.", "Your own team's activity; useful for review and accountability.",
              ["Who acted and when"], ["None needed unless an action looks unauthorised."]),
    "other": _b("Events that are not yet classified.", "Unmapped event types may still matter.",
                ["Read the message and fields of a few examples."], ["Ask engineering to map the type in soc_taxonomy.py."]),
}

# ── specific event types (override the family brief) ────────────────
EVENT_TYPE = {
    "auth.failure": KPI["failed_logins"][1],
    "auth.lockout": KPI["lockouts"][1],
    "auth.mfa_failure": _b(
        "A second-factor code was rejected after the password was accepted.",
        "The password was correct. Repeated MFA failures mean someone may already have the user's password.",
        ["Is the source the user's usual location / network?", "Several MFA failures in a row?"],
        ["Contact the user; if they did not try to sign in, reset the password immediately."]),
    "auth.injection_attempt": _b(
        "Login input containing SQL / script injection syntax.",
        "A deliberate attack on the login form, not a typo.",
        ["Source and other activity from it"], ["Ban the source if public."]),
    "auth.admin_login_success": _b(
        "An administrator signed in successfully.",
        "Normal on its own; suspicious right after failures, or from a new country, VPN or hosting range.",
        ["Failures from the same source just before?", "Usual location for this admin?"],
        ["If unexpected, revoke the session and reset the password."]),
    "tenant.session_mismatch": FAMILY["access"],
    "waf.sqli_blocked": _b(
        "A request containing SQL injection was blocked.",
        "SQL injection aims to read or modify the database directly.",
        ["Targeted path and parameter", "Scanner user agent (sqlmap etc.)?"], ["Ban persistent public sources."]),
    "waf.xss_blocked": _b(
        "A request containing a cross-site scripting payload was blocked.",
        "XSS aims to run script in other users' browsers to steal sessions.",
        ["Targeted field / path"], ["Ban persistent public sources."]),
    "waf.path_traversal_blocked": _b(
        "A request trying to escape the web root (../) was blocked.",
        "Path traversal aims to read server files such as configuration and secrets.",
        ["Which files were targeted?"], ["Ban the source; confirm no file was served (Events from this IP)."]),
    "audit.chain_violation": FAMILY["audit"],
    "data.reveal": FAMILY["data"],
    "session.compromised": FAMILY["session"],
}

LEVEL = {"CRITICAL": KPI["critical"][1], "ERROR": KPI["errors"][1],
         "WARNING": _b("Events logged at WARNING: suspicious but expected in small numbers (failed logins, denials).",
                       "Worth watching in bulk; individual warnings rarely need action.",
                       ["Which types dominate?", "Concentrated on a few sources or accounts?"],
                       ["Escalate patterns, not single events."]),
         "INFO": _b("Informational events: successful logins, changes, exports.",
                    "Context for investigations (what happened before / after an alert).",
                    ["Use as a timeline around alerts."], ["None on their own."])}

GENERIC = _b("Security events matching this selection.", "Review the numbers and examples below.",
             ["Top sources and accounts", "Recent examples"], ["Drill into anything unexpected."])


def brief_for(kind, value):
    """-> {title, subtitle, what, why, check[], respond[], mitre[]} for one slice."""
    mitre = []
    if kind == "kpi" and value in KPI:
        title, b = KPI[value]
        sub = "Overview indicator"
    elif kind == "event_type":
        cls = tax.classify(value)
        title, sub, mitre = cls["label"], cls["family_label"] + " · " + value, cls["mitre"]
        if value.startswith("detection."):
            rule = value.split(".", 1)[1]
            label, _, meaning = tax.DETECTIONS.get(rule, (cls["label"], [], ""))
            base = KPI["detections"][1]
            b = _b(meaning or base["what"], base["why"], base["check"], base["respond"])
        else:
            b = EVENT_TYPE.get(value) or FAMILY.get(cls["family"]) or GENERIC
    elif kind == "family":
        title, sub = tax.FAMILIES.get(value, value), "Area"
        b = FAMILY.get(value) or GENERIC
    elif kind == "technique":
        title, sub = tax.MITRE.get(value, value), "MITRE ATT&CK " + value
        mitre = [tax._technique(value)]
        b = _b(f"Events this platform maps to the ATT&CK technique {value} ({tax.MITRE.get(value, value)}).",
               "ATT&CK describes attacker behaviour; seeing a technique means activity consistent with it, not "
               "proof of compromise.",
               ["Which event types produced it (list below)?", "Same sources across several techniques?"],
               ["Follow the playbook of the dominant event type.", "Open the technique on attack.mitre.org for detection and mitigation guidance."])
    elif kind == "level":
        title, sub = value.capitalize() + " events", "Severity level"
        b = LEVEL.get(value) or GENERIC
    else:
        title, sub, b = "Security events", "", GENERIC
    return {"title": title, "subtitle": sub, "mitre": mitre, **b}
