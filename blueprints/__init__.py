# -*- coding: utf-8 -*-
"""Blueprint registry: the one list of every blueprint the app serves.

register_all(app) is called by app.py (production via wsgi.py, and `python
app.py`) and by tests/conftest.py, so the set and ORDER of blueprints can't
drift between them. Order matters: app.py's _register_api_v1_aliases()
mirrors whatever /api/* routes exist when it runs, and Werkzeug breaks rule
ties by registration order.
"""
import importlib

# (module under blueprints/, blueprint attribute) -- registration order.
BLUEPRINTS = (
    ("health", "health_bp"),
    ("notifications", "notifications_bp"),
    ("payroll", "payroll_bp"),
    ("leave", "leave_bp"),
    ("admin_views", "admin_views_bp"),
    ("auth", "auth_bp"),
    ("employees", "employees_bp"),
    ("attendance", "attendance_bp"),
    ("tickets", "tickets_bp"),
    ("performance", "performance_bp"),
    ("documents", "documents_bp"),
    ("org", "org_bp"),
    ("onboarding", "onboarding_bp"),
    ("employee_portal", "employee_portal_bp"),
    ("core", "core_bp"),
    ("ai_hrms", "ai_hrms_bp"),
    ("email_blast", "email_blast_bp"),
    ("daily_report", "daily_report_bp"),
    ("billing", "billing_bp"),
    ("webhooks", "webhooks_bp"),
    ("seats", "seats_bp"),
    ("auto_debit", "auto_debit_bp"),
    ("billing_dunning", "billing_dunning_bp"),
    ("platform_admin", "platform_admin_bp"),
    ("honeypot_routes", "honeypot_bp"),
    ("disbursement", "disbursement_bp"),
    ("hr_dashboard", "hr_dashboard_bp"),
    ("policies", "policies_bp"),
    ("company_profile", "company_profile_bp"),
    ("hr_team", "hr_team_bp"),
    ("seo", "seo_bp"),
    ("oauth_signup", "oauth_signup_bp"),
    ("privacy", "privacy_bp"),
    ("soc", "soc_bp"),
)


def iter_blueprints():
    for module, attr in BLUEPRINTS:
        yield getattr(importlib.import_module(f"blueprints.{module}"), attr)


def register_all(app):
    """Register every blueprint on `app` once (idempotent)."""
    for bp in iter_blueprints():
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)
