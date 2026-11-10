# -*- coding: utf-8 -*-
"""Each company's monthly billing cycle.

A company is billed from the day it took its plan (tenants.billing_anchor_date):
plan taken 10 Nov -> its bill renews on 10 Dec, 10 Jan, ... and Auto-Debit
charges on those same dates. A plan taken on the 29th-31st renews on the last
day of a shorter month and goes back to its own day after it (31 Jan, 28 Feb,
31 Mar) -- every date is worked out from the anchor itself, never from the
previous date, so it doesn't drift.

The anchor is set when the plan starts: a paid signup (org/provisioning.py),
Auto-Debit switched on to end a trial or settle an overdue bill
(auto_debit.py's confirm()), a trial turning paid (trial_billing.py) or an
overdue bill paid by a company that never had a plan date. Companies from
before this column existed have it NULL and keep renewing on the 1st, exactly
as they did.
"""
import calendar
import datetime

# A charge that lands up to this many days before a billing date pays for the
# period that starts on it -- Razorpay's own schedule can land a day or two
# early for a plan taken on the 29th-31st. Same allowance when deciding
# whether the current period is paid.
EARLY_CHARGE_DAYS = 3


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _shift(anchor, months):
    """anchor moved by whole months, on the anchor's day (or the month's last
    day when the month is shorter)."""
    m = anchor.month - 1 + months
    y = anchor.year + m // 12
    m = m % 12 + 1
    return datetime.date(y, m, min(anchor.day, calendar.monthrange(y, m)[1]))


def _months_between(anchor, d):
    return (d.year - anchor.year) * 12 + d.month - anchor.month


def period_start(anchor, today):
    """First day of the billing period `today` falls in. Before the anchor
    there is no period yet -- the anchor itself is returned."""
    if today <= anchor:
        return anchor
    n = _months_between(anchor, today)
    start = _shift(anchor, n)
    return start if start <= today else _shift(anchor, n - 1)


def next_billing_date(anchor, today):
    """The next date the bill renews -- the day after the current period."""
    if today < anchor:
        return anchor
    return _shift(anchor, _months_between(anchor, period_start(anchor, today)) + 1)


def period_end(anchor, start):
    """Last day of the period that starts on `start`."""
    return next_billing_date(anchor, start) - datetime.timedelta(days=1)


def anchor_from(billing_anchor_date, created_at, today=None):
    """(anchor, explicit) from a tenants row. explicit=False is a company from
    before plan dates existed: it renews on the 1st, starting with the month
    it was created in."""
    d = _as_date(billing_anchor_date)
    if d:
        return d, True
    from utils.helpers import platform_today
    created = _as_date(created_at) or today or platform_today()
    return created.replace(day=1), False


def tenant_anchor(db_name, cur=None):
    """(anchor, explicit) for one company, by schema name."""
    own = cur is None
    if own:
        from database import get_master_db
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
    try:
        cur.execute("SELECT billing_anchor_date, created_at FROM tenants WHERE db_name=%s", (db_name,))
        row = cur.fetchone()
    finally:
        if own:
            cur.close()
            conn.close()
    return anchor_from(row[0] if row else None, row[1] if row else None)


def first_period_prepaid(anchor, explicit, created_at):
    """Whether the first period was paid at signup checkout: the plan date is
    the day the company was created (paid signup) -- or, for a company from
    before plan dates, its first calendar month. A trial that turned paid
    later has to pay its first period like any other."""
    created = _as_date(created_at)
    if created is None:
        return False
    if not explicit:
        return created.replace(day=1) == anchor
    return abs((anchor - created).days) <= 1


def charge_period(anchor, explicit, charged_on):
    """The billing period a charge made on `charged_on` pays for."""
    if not explicit:
        return charged_on.replace(day=1)
    return period_start(anchor, charged_on + datetime.timedelta(days=EARLY_CHARGE_DAYS))


def period_is_paid(cur, db_name, start):
    """Whether the period starting on `start` has a paid bill: one recorded
    for that period (or a later one), or any paid bill from just before it
    started -- a charge recorded before the plan date moved (the webhook can
    beat confirm()) still counts."""
    cur.execute(
        "SELECT 1 FROM monthly_invoices WHERE tenant_schema=%s AND status='paid' "
        "AND (billing_period >= %s OR created_at >= %s) LIMIT 1",
        (db_name, start, start - datetime.timedelta(days=EARLY_CHARGE_DAYS))
    )
    return cur.fetchone() is not None


def charge_epoch(day):
    """10:00 platform time on `day`, as epoch seconds -- Razorpay's start_at."""
    from utils.helpers.settings import PLATFORM_TZ
    return int(PLATFORM_TZ.localize(datetime.datetime.combine(day, datetime.time(10, 0))).timestamp())
