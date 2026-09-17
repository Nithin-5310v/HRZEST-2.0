"""Shared vocabulary for the att_master tenant_support_tickets table, used by
both blueprints/platform_admin.py (the platform-admin side) and
blueprints/company_profile.py (the company-admin side that can now create/
reply to these too) -- kept in one place so the two sides can't drift apart
on what a valid status/priority is."""

TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
