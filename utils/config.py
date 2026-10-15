# -*- coding: utf-8 -*-
"""Shift timings and payroll rules -- per company.

Each company configures its own default shift (company_settings.shift_start/
shift_half/shift_end), grace minutes and payroll rules (late/half-day
deduction %, holiday pay, leave pay). Read them through shift_rules() /
salary_rules(), which go through get_company_settings() -- already scoped to
the current tenant (g.tenant_db) and cached per tenant, invalidated by
invalidate_settings_cache() when settings are saved.

These used to be module-level globals loaded once at startup from the
default schema and overwritten by whichever company last saved its settings,
so one company's rules were silently applied to another (QA BUG-01). Never
reintroduce process-wide mutable state here.
"""
import datetime
from collections import namedtuple

# Default geofence radius. The office location itself is per company
# (company_settings.office_lat/office_lon, set in Settings); the old
# process-wide OFFICE_LAT/OFFICE_LON were no longer read (QA DEAD-02).
OFFICE_RADIUS_M = 300

# Defaults for a company that hasn't configured its own values (these match
# the company_settings column defaults).
DEFAULT_SHIFT_START = datetime.time(9, 0)
DEFAULT_SHIFT_HALF = datetime.time(13, 0)
DEFAULT_SHIFT_END = datetime.time(18, 0)
DEFAULT_GRACE_MINUTES = 15
DEFAULT_LATE_DEDUCTION_PCT = 10.0
DEFAULT_HALF_DAY_DEDUCTION_PCT = 50.0
DEFAULT_HOLIDAY_PAY = "paid"    # "paid" | "unpaid"
DEFAULT_LEAVE_PAY = "exclude"   # "exclude" | "absent"

ShiftRules = namedtuple("ShiftRules", "start half end grace_minutes")
SalaryRules = namedtuple("SalaryRules", "late_deduction_rate half_day_rate holiday_pay leave_pay")


def as_time(value, default):
    """company_settings TIME values arrive as time, timedelta or "HH:MM[:SS]"."""
    if value is None or value == "":
        return default
    if isinstance(value, datetime.time):
        return value
    if isinstance(value, datetime.timedelta):
        total = int(value.total_seconds())
        return datetime.time((total // 3600) % 24, (total % 3600) // 60)
    try:
        parts = [int(p) for p in str(value).split(":")[:3]]
        return datetime.time(*parts)
    except (TypeError, ValueError):
        return default


def _settings():
    from utils.helpers import get_company_settings  # local import: helpers imports far more than config
    return get_company_settings()


def shift_rules():
    """The current company's default shift and grace minutes."""
    co = _settings()
    grace = co.get("grace_minutes")
    return ShiftRules(
        start=as_time(co.get("shift_start"), DEFAULT_SHIFT_START),
        half=as_time(co.get("shift_half"), DEFAULT_SHIFT_HALF),
        end=as_time(co.get("shift_end"), DEFAULT_SHIFT_END),
        grace_minutes=int(grace) if grace is not None else DEFAULT_GRACE_MINUTES,
    )


def salary_rules():
    """The current company's payroll rules (rates as fractions, e.g. 0.10)."""
    co = _settings()
    late = co.get("late_deduction_pct")
    half = co.get("half_day_deduction_pct")
    return SalaryRules(
        late_deduction_rate=(float(late) if late is not None else DEFAULT_LATE_DEDUCTION_PCT) / 100.0,
        half_day_rate=(float(half) if half is not None else DEFAULT_HALF_DAY_DEDUCTION_PCT) / 100.0,
        holiday_pay=co.get("holiday_pay") or DEFAULT_HOLIDAY_PAY,
        leave_pay=co.get("leave_pay") or DEFAULT_LEAVE_PAY,
    )
