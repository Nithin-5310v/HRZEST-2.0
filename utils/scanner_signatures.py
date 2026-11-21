# -*- coding: utf-8 -*-
"""Request-header signatures shared by the in-app WAF (utils/waf.py), which
blocks on them, and the SOC log analyser (utils/soc_loganalysis.py), which
reports them. One list, so a tool the WAF refuses is also one the analyser
flags in a pasted log, and the other way round.

Pure module: no Flask, no database, safe to import anywhere.
"""
import re

_I = re.IGNORECASE

# Attack tools that announce themselves in User-Agent. Matching one is not
# proof of an attack (a customer's own authorised scan uses the same tools),
# which is why a hit is blocked but the *ban* still goes to an analyst
# (utils/soc_ban_review.py) unless SOC_AUTO_BAN is on.
SCANNER_UA_RE = re.compile(
    r"sqlmap|nikto|nmap|masscan|zgrab|gobuster|dirbuster|dirb\b|wpscan|nuclei|acunetix|nessus|openvas|burp|havij|w3af"
    r"|arachni|metasploit|ffuf|feroxbuster|whatweb|netsparker|appscan|paros|skipfish|jaeles|dalfox", _I)

# JNDI lookup strings (Log4Shell and its obfuscated forms). Nothing in this
# stack runs Log4j, but the probe is a reliable marker of an exploit kit and
# it arrives in whatever header the kit thinks will be logged. ${${...}} is
# the nested-lookup obfuscation (${${lower:j}ndi:...}).
JNDI_RE = re.compile(r"\$\{\s*(?:jndi\s*:|\$\{|(?:lower|upper|env|sys|date|::-)[^}]{0,40}\})", _I)


def scanner_user_agent(value):
    """The matched tool name, or None."""
    if not value:
        return None
    m = SCANNER_UA_RE.search(value)
    return m.group(0) if m else None


def jndi_lookup(value):
    """The matched lookup prefix, or None."""
    if not value:
        return None
    m = JNDI_RE.search(value)
    return m.group(0) if m else None
