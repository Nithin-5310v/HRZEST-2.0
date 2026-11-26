# -*- coding: utf-8 -*-
"""Employee document expiry dates.

An uploaded document (passport, visa, work permit, licence, ...) may carry an
expiry date. Every upload route (blueprints/documents.py: web and Bearer, admin
and self-service) reads the optional `expiry_date` field through
parse_expiry(), *before* the file is stored, so a malformed value is a clear
message instead of a database error that leaves the stored file behind.

expiry_report() is what the admin side shows: documents already expired and
documents expiring in the next N days, by the company's own date
(company_today(), not the server's UTC clock) and limited to a team-scoped
role's own people.
"""
import datetime
import re

from utils.helpers import company_today, hr_scope_subquery

FORMAT_ERROR = "Expiry date must be a date in YYYY-MM-DD format."

# What <input type="date"> and the mobile app send. date.fromisoformat alone
# also accepts "20270312" and week dates ("2027-W10-1"); nothing sends those.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_expiry(raw):
    """-> (date or None, error message or None) for the optional form field.
    Blank means "no expiry date"."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if _ISO_DATE.fullmatch(raw):
        try:
            return datetime.date.fromisoformat(raw), None
        except ValueError:          # 2027-02-30 and the like
            pass
    return None, FORMAT_ERROR


def _as_date(value):
    """The column is DATE on PostgreSQL but TEXT in the SQLite dev fallback."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def expiry_report(cursor, days=30):
    """{"today", "days", "expired": [...], "expiring": [...]}: documents whose
    expiry date has passed, and those expiring within `days` days (today
    included), soonest first. Each entry: id, employee_id, employee_name,
    doc_type, filename, expiry_date (date), days_left (negative once expired)."""
    days = max(1, min(int(days), 366))
    today = company_today()
    scope_sql, scope_args = hr_scope_subquery("d")
    cursor.execute(
        "SELECT d.id, d.employee_id, e.name, d.doc_type, d.original_name, d.expiry_date "
        "FROM employee_documents d JOIN employees e ON e.employee_id = d.employee_id "
        f"WHERE d.expiry_date IS NOT NULL AND d.expiry_date <= %s {scope_sql} "  # nosec B608 -- fixed fragment
        "ORDER BY d.expiry_date ASC, e.name ASC",
        (today + datetime.timedelta(days=days), *scope_args),
    )
    expired, expiring = [], []
    for doc_id, emp_id, name, doc_type, filename, raw_expiry in cursor.fetchall():
        expiry = _as_date(raw_expiry)
        if expiry is None:
            continue
        entry = {"id": doc_id, "employee_id": emp_id, "employee_name": name, "doc_type": doc_type,
                 "filename": filename, "expiry_date": expiry, "days_left": (expiry - today).days}
        (expired if expiry < today else expiring).append(entry)
    return {"today": today, "days": days, "expired": expired, "expiring": expiring}


def expiry_status(value, soon_days=30):
    """("expired" | "soon" | "ok" | None, days_left) for one document's expiry
    date, by the company's date -- what the document lists use for their badge."""
    expiry = _as_date(value) if value else None
    if expiry is None:
        return None, None
    days_left = (expiry - company_today()).days
    if days_left < 0:
        return "expired", days_left
    return ("soon" if days_left <= soon_days else "ok"), days_left
