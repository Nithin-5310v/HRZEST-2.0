# -*- coding: utf-8 -*-
"""RFC 9116 security.txt and the vulnerability-disclosure policy page
(security review OBS-06, 2026-11).

  /.well-known/security.txt   the file itself (text/plain; charset=utf-8)
  /security.txt               301 -> /.well-known/security.txt
  /security-policy            the human-readable policy the file points to

RENEW `EXPIRES` BEFORE IT PASSES. RFC 9116 says a security.txt past its
Expires date must be treated as stale, so researchers may ignore it.
tests/test_security_txt.py fails once the date is under 60 days away, so
CI reminds us. Push the date about a year ahead (less than a year, per the
RFC) and re-check that the Contact mailbox still works.
"""
from flask import Blueprint, Response, redirect, render_template

security_txt_bp = Blueprint("security_txt", __name__)

CONTACT = "mailto:security@hrzest.com"
CANONICAL = "https://hrzest.com/.well-known/security.txt"
POLICY_URL = "https://hrzest.com/security-policy"
# Renew before this date -- see the module docstring.
EXPIRES = "2027-11-01T00:00:00.000Z"


def security_txt_body():
    return "\n".join([
        f"Contact: {CONTACT}",
        f"Expires: {EXPIRES}",
        "Preferred-Languages: en",
        f"Canonical: {CANONICAL}",
        f"Policy: {POLICY_URL}",
        "",
    ])


@security_txt_bp.route("/.well-known/security.txt")
def well_known_security_txt():
    resp = Response(security_txt_body(), mimetype="text/plain")
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@security_txt_bp.route("/security.txt")
def legacy_security_txt():
    return redirect("/.well-known/security.txt", code=301)


@security_txt_bp.route("/security-policy")
def security_policy():
    return render_template("security_policy.html", contact_email=CONTACT.removeprefix("mailto:"))
