# -*- coding: utf-8 -*-
"""helpers: input validation and coercion (employee IDs, datetimes, ints, floats)"""
import datetime
import math
import re
from flask import abort


_SAFE_IDENT_RE = re.compile(r'^[a-z][a-z0-9_]*$')

# Employee IDs are used to build filesystem paths (dataset/<emp_id>.jpg,
# private_uploads/qrcodes/<emp_id>.png) before the DB row necessarily exists yet
# (registration), so a DB existence check can't be relied on to reject
# path-traversal characters the way it does for update-in-place routes.
_EMP_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')


def validate_emp_id(emp_id: str) -> bool:
    return bool(emp_id) and bool(_EMP_ID_RE.match(emp_id))


def emp_id_param(value):
    """An employee id taken from the request, checked against the same
    format as validate_emp_id(). Malformed input answers 400 Bad Request,
    like int_param() -- it never reaches a query or a redirect URL as sent
    (QA SEC-14)."""
    if not isinstance(value, str) or not validate_emp_id(value):
        abort(400, description="An employee id in the request is invalid.")
    return value


def coerce_datetime(value):
    """A TIMESTAMP column comes back as a real datetime from Postgres, but
    as a plain str from the local-fallback SQLite path (database.py's
    _SqliteConnWrapper opens sqlite3.connect() with no detect_types, so
    declared column types aren't used to auto-convert query results).
    Callers doing datetime arithmetic or comparison/sorting on a value that
    might have come from either backend (e.g. blueprints/auto_debit.py's
    monthly-charge-due check, blueprints/platform_admin.py's payment-feed
    sort) should route it through this first rather than assume one type.
    Returns a datetime unchanged, parses an ISO-ish string, or None for
    anything else/unparseable -- never raises."""
    if value is None or isinstance(value, datetime.datetime):
        return value
    # A DATE column (e.g. monthly_invoices.billing_period) comes back from
    # psycopg2 as a plain datetime.date, not datetime.datetime -- must be
    # checked before the str branch since date isn't a datetime subclass.
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def int_param(value):
    """int() for a value taken from the request (query string, form, JSON).
    Malformed input answers 400 Bad Request -- JSON for API calls, see
    _error_page() -- instead of an unhandled ValueError: a 500 with a logged
    traceback and an error alert (QA BUG-08..16)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        abort(400, description="A number in the request is invalid.")


def parse_finite_float(raw):
    """float() for a number taken from a form or query string, refusing NaN
    and infinity. float() accepts "nan", "inf" and "1e999" without raising,
    and a NaN or infinite payroll rate or deduction percentage silently
    corrupts every calculation that uses it (QA SEC-13). Raises ValueError,
    which the callers' invalid-value handling already catches."""
    if raw is None:
        raise ValueError("missing number")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("number must be finite")
    return value
