# -*- coding: utf-8 -*-
"""Per-employee flat-rate billing for the multi-tenant SaaS.

Tiered plans (Basic/Medium/Prime) were retired in favor of a single flat
rate per employee -- every feature is available to every tenant
regardless of headcount; the only billing lever left is employee count.
att_master.tenants.plan is kept as a column (historical/audit value only,
always written as PLAN_LABEL below) rather than dropped, since altering
it out is a destructive migration with no functional upside -- nothing
reads it as a live feature/pricing switch anymore.
"""
import time
from database import get_tenant_db

# ₹99/employee/month, flat. No minimum charge, no employee cap, no tiers.
# Seed/fallback value only now -- the live rate lives in att_master.
# platform_costs.per_employee_paise (editable from the Platform Admin
# dashboard) and is what get_per_employee_paise()/calculate_price() below
# actually use. This constant still matters as: (1) that column's DEFAULT
# for a fresh install, and (2) the fallback if the DB read itself fails.
PER_EMPLOYEE_PAISE = 9900

# Written into att_master.tenants.plan on every new tenant -- a fixed
# audit-trail label, not a lookup key into any tier table (there isn't one).
PLAN_LABEL = "per_employee"

# Short TTL cache so every calculate_price()/display call doesn't hit the
# master DB -- same idea as utils/helpers.py's company-settings cache.
# invalidate_rate_paise_cache() (called right after the platform admin
# saves a new rate) means a change is live on the very next read rather
# than waiting out the TTL.
_RATE_CACHE_TTL_SEC = 30
_rate_cache = {"value": None, "checked_at": 0.0}

# Same cache shape as _rate_cache above, one entry per platform_costs
# column -- base_fee_paise/minimum_monthly_paise both default to 0 (see
# app.py's ALTER TABLE), so calculate_price()'s formula below is
# byte-identical to the old flat-rate-only behavior until a platform admin
# explicitly sets one of these two new knobs.
_extra_cost_cache = {"base_fee_paise": {"value": None, "checked_at": 0.0},
                     "minimum_monthly_paise": {"value": None, "checked_at": 0.0}}


def get_per_employee_paise() -> int:
    """The live flat per-employee rate, admin-editable at
    /super_admin -- every price calculation in the app (signup checkout,
    seat top-ups, recurring auto-debit plan creation, dunning bills, the
    Platform Admin P&L) goes through this (directly or via calculate_price()
    below), so a rate change the platform admin saves takes effect
    everywhere on the next read, no redeploy needed. Falls back to the
    last-known-good cached value, then to PER_EMPLOYEE_PAISE, if the DB
    read itself fails -- billing must never hard-fail because this one
    lookup hiccuped."""
    now = time.time()
    if _rate_cache["value"] is not None and (now - _rate_cache["checked_at"]) < _RATE_CACHE_TTL_SEC:
        return _rate_cache["value"]
    try:
        from database import get_master_db
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT per_employee_paise FROM platform_costs WHERE id=1")
        row = cur.fetchone()
        cur.close()
        conn.close()
        value = row[0] if row and row[0] else PER_EMPLOYEE_PAISE
    except Exception:
        value = _rate_cache["value"] if _rate_cache["value"] is not None else PER_EMPLOYEE_PAISE
    _rate_cache["value"] = value
    _rate_cache["checked_at"] = now
    return value


def invalidate_rate_paise_cache():
    """Called right after the platform admin saves a new rate
    (blueprints/platform_admin.py's platform_admin_set_rate()) so the
    change is visible on the very next read instead of waiting out
    _RATE_CACHE_TTL_SEC."""
    _rate_cache["value"] = None
    _rate_cache["checked_at"] = 0.0


def _get_extra_cost_paise(column: str) -> int:
    """Shared reader for platform_costs.base_fee_paise / minimum_monthly_paise
    -- same cache-with-fallback shape as get_per_employee_paise() above,
    just parameterized by column since both default to 0 and are read the
    same way."""
    entry = _extra_cost_cache[column]
    now = time.time()
    if entry["value"] is not None and (now - entry["checked_at"]) < _RATE_CACHE_TTL_SEC:
        return entry["value"]
    try:
        from database import get_master_db
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute(f"SELECT {column} FROM platform_costs WHERE id=1")  # nosec B608 -- column is a key of _extra_cost_cache
        row = cur.fetchone()
        cur.close()
        conn.close()
        value = row[0] if row and row[0] is not None else 0
    except Exception:
        value = entry["value"] if entry["value"] is not None else 0
    entry["value"] = value
    entry["checked_at"] = now
    return value


def get_base_fee_paise() -> int:
    """Flat per-tenant fee added to every bill regardless of headcount,
    admin-editable at /super_admin. Defaults to 0 -- most tenants are
    billed purely per-employee same as before this existed."""
    return _get_extra_cost_paise("base_fee_paise")


def get_minimum_monthly_paise() -> int:
    """Floor applied to the total bill (base fee + per-employee) so a very
    small headcount is never billed below this. Defaults to 0 (no floor)."""
    return _get_extra_cost_paise("minimum_monthly_paise")


def invalidate_extra_cost_cache():
    """Called right after the platform admin saves base_fee_paise/
    minimum_monthly_paise (blueprints/platform_admin.py's
    platform_admin_set_rate()) -- same purpose as
    invalidate_rate_paise_cache() above, for the two newer knobs."""
    for entry in _extra_cost_cache.values():
        entry["value"] = None
        entry["checked_at"] = 0.0


def get_tenant_employee_count(schema_name: str) -> int:
    """Live employee count for a tenant schema -- the sole input to billing
    now. Fails to 0 on any DB error rather than raising, since this is used
    in dashboard/pricing display contexts that shouldn't 500 on a transient
    connection hiccup."""
    try:
        conn = get_tenant_db(schema_name)
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT COUNT(*) FROM employees")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception:
        return 0


def calculate_price(employee_count: int) -> int:
    """Price in paise for `employee_count` employees: a flat per-tenant base
    fee (get_base_fee_paise(), 0 by default) plus the per-employee rate
    (get_per_employee_paise()), floored at get_minimum_monthly_paise() (also
    0 by default) so a very small headcount is never billed below the
    platform's minimum. With both new knobs left at their 0 default this is
    exactly the old flat-rate formula -- single source of truth for both
    display (create_org, Platform Admin dashboard) and the Razorpay order
    amount (blueprints/billing.py's create_order), so those never compute
    different numbers for the same input."""
    if employee_count < 0:
        employee_count = 0
    total = get_base_fee_paise() + employee_count * get_per_employee_paise()
    return max(total, get_minimum_monthly_paise())


def format_price_inr(paise: int) -> str:
    """paise -> "₹1,999" style display string (no decimal paise shown --
    every price here is a whole-rupee amount already)."""
    return f"₹{paise // 100:,}"


def get_billing_snapshot(schema_name: str) -> dict:
    """Single source of truth for a tenant's auto-debit mandate status and
    recent invoice history -- used by both the web Seats & Billing page
    (blueprints/seats.py's seats_page()) and the mobile billing-status API
    (blueprints/core.py's api_billing_status()) so the two can never drift
    on what "auto-debit status"/"billing history" means. Deliberately
    doesn't include employee_count -- get_tenant_employee_count() above is
    already that single source and callers already have it.

    Returns raw values (real datetimes, integer paise) rather than
    pre-formatted display strings -- callers format for their own
    presentation layer (Jinja's .strftime() vs JSON's .isoformat() /
    format_price_inr()). Every date/timestamp is passed through
    coerce_datetime() first: under the local-fallback SQLite path these
    columns come back as plain strings (see coerce_datetime's docstring),
    and a caller blindly calling .strftime()/.isoformat() on a str would
    crash -- coercing once here means neither caller has to know or care
    which DB backend is live. Fails soft (auto_debit=None, invoices=[]) on
    any DB error, matching get_tenant_employee_count()'s posture."""
    from database import get_master_db
    from utils.helpers import coerce_datetime

    auto_debit = None
    invoices = []
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute(
            "SELECT status, quantity_synced, activated_at FROM auto_debit_mandates WHERE tenant_schema=%s",
            (schema_name,)
        )
        row = cur.fetchone()
        if row:
            auto_debit = {
                "status": row[0], "quantity_synced": row[1],
                "activated_at": coerce_datetime(row[2]),
            }
        cur.execute(
            "SELECT billing_period, employee_count, amount_paise, status, created_at, failure_reason, rate_paise "
            "FROM monthly_invoices WHERE tenant_schema=%s ORDER BY created_at DESC LIMIT 12",
            (schema_name,)
        )
        invoices = [
            {
                "billing_period": coerce_datetime(r[0]), "employee_count": r[1],
                "amount_paise": r[2], "status": r[3],
                "created_at": coerce_datetime(r[4]), "failure_reason": r[5],
                "rate_paise": r[6],
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
    except Exception as exc:
        from extensions import app_log
        app_log.warning("get_billing_snapshot(%s): auto-debit/invoice lookup failed: %s", schema_name, exc)
    return {"auto_debit": auto_debit, "invoices": invoices}
