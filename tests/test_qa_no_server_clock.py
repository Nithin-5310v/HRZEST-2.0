"""BUG-21/22/24/25: business dates come from the company's clock.

Default periods ("this month"), "today" checks, generated-on dates and due
dates used the server clock (UTC in the container) instead of
company_today()/company_now(), so around midnight IST -- and all day for
non-IST tenants -- they were a day (or a month) off. A month default only
differs on the last/first day, so this guard checks the source: these
blueprints must not read the server's date. Runnable with --noconftest.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_CLOCK = re.compile(r"datetime\.date\.today\(\)|datetime\.datetime\.now\(\)")


@pytest.mark.parametrize("path", [
    "blueprints/payroll.py",            # BUG-21
    "blueprints/employee_portal.py",    # BUG-22
    "blueprints/performance.py",        # BUG-24
    "blueprints/employees.py",          # BUG-25
])
def test_blueprint_does_not_read_the_server_clock(path):
    hits = [f"{rel}:{n}" for rel, src in _sources(path)
            for n, line in enumerate(src.splitlines(), 1) if SERVER_CLOCK.search(line)]
    assert hits == []


def _sources(path):
    """(relative name, source) for a module -- or, once a module has been split
    into a package (blueprints/x.py -> blueprints/x/), every file in it, so the
    guard keeps covering the code instead of failing or silently passing."""
    file = ROOT / path
    if file.is_file():
        return [(path, file.read_text(encoding="utf-8"))]
    pkg = file.with_suffix("")
    files = sorted(pkg.glob("*.py"))
    assert files, f"{path}: neither the module nor a package of that name exists"
    return [(f"{pkg.relative_to(ROOT).as_posix()}/{f.name}", f.read_text(encoding="utf-8")) for f in files]


# ── BUG-27: platform billing periods are on the platform's clock (IST) ─────

@pytest.mark.parametrize("path", ["blueprints/billing_dunning.py", "blueprints/auto_debit.py"])
def test_billing_periods_use_the_platform_date(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    assert "datetime.date.today()" not in source
    assert "platform_today()" in source


def test_platform_today_is_the_ist_date():
    import datetime as _dt

    import pytz
    from utils.helpers import platform_today
    assert platform_today() == _dt.datetime.now(pytz.timezone("Asia/Kolkata")).date()
