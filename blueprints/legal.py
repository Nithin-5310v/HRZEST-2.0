# -*- coding: utf-8 -*-
"""The public Privacy Policy (/privacy) and Terms of Service (/terms).

The business details on both pages (address, email, phone) come from
utils/tax_invoice.seller_details(), the same details printed on invoices.
Change LEGAL_UPDATED whenever the wording of either page changes.
"""
from flask import Blueprint, redirect, render_template

legal_bp = Blueprint("legal", __name__)

# The "Last updated" date shown on both pages.
LEGAL_UPDATED = "23 November 2026"


def legal_context():
    """What legal_layout.html needs (also used by /security-policy)."""
    from utils.tax_invoice import seller_details
    return {"seller": seller_details(), "legal_updated": LEGAL_UPDATED, "legal_year": LEGAL_UPDATED[-4:]}


@legal_bp.route("/privacy")
def privacy_policy():
    return render_template("legal_privacy.html", **legal_context())


@legal_bp.route("/terms")
def terms_of_service():
    try:
        from utils.plan_limits import get_per_employee_paise
        per_employee_rupees = get_per_employee_paise() // 100
    except Exception:
        per_employee_rupees = None
    return render_template("legal_terms.html", per_employee_rupees=per_employee_rupees, **legal_context())


# Common alternative addresses people (and other sites) link to.
@legal_bp.route("/privacy-policy")
def privacy_policy_alias():
    return redirect("/privacy", 301)


@legal_bp.route("/terms-and-conditions")
def terms_alias():
    return redirect("/terms", 301)
