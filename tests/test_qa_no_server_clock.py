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
])
def test_blueprint_does_not_read_the_server_clock(path):
    hits = [f"{path}:{n}" for n, line in enumerate((ROOT / path).read_text(encoding="utf-8").splitlines(), 1)
            if SERVER_CLOCK.search(line)]
    assert hits == []
