"""BUG-40: a PF or PT rate configured as 0 was deducted at 12% / Rs.200.

Payroll settings accept 0 for every field (a company not registered for
PF, or in a state without professional tax), but both payslip paths read
the row as `float(value or 12)`, `float(value or 200)` ... -- 0 is falsy,
so a configured 0 came back as the default and the payslip deducted 12%
PF and Rs.200 PT anyway. Only a NULL column should fall back to the
default.
"""
import re

import pytest

from tests.test_admin_views_routes import _admin_session
from utils.salary_utils import PAYROLL_CONFIG_DEFAULTS, payroll_config_from_row

ZERO_ROW = (0, 0, 0, 0, 0)


def test_configured_zero_stays_zero():
    cfg = payroll_config_from_row(ZERO_ROW)
    assert cfg == {"pf_employee_pct": 0.0, "pf_employer_pct": 0.0, "professional_tax": 0.0,
                   "tds_annual_pct": 0.0, "pf_basic_cap": 0.0}


def test_null_column_falls_back_to_its_default():
    cfg = payroll_config_from_row((None, 10, None, None, None))
    assert cfg["pf_employee_pct"] == PAYROLL_CONFIG_DEFAULTS["pf_employee_pct"]
    assert cfg["pf_employer_pct"] == 10.0
    assert cfg["professional_tax"] == PAYROLL_CONFIG_DEFAULTS["professional_tax"]


def test_no_config_row_uses_the_defaults():
    assert payroll_config_from_row(None) == PAYROLL_CONFIG_DEFAULTS


@pytest.fixture
def zero_statutory(db_engine, seed_employee):
    cur = db_engine.cursor()
    cur.execute("DELETE FROM payroll_config")
    cur.execute("INSERT INTO payroll_config (pf_employee_pct, pf_employer_pct, professional_tax, tds_annual_pct, "
                "pf_basic_cap) VALUES (0, 0, 0, 0, 15000)")
    cur.execute("INSERT INTO salary_config (employee_id, salary_per_day, monthly_ctc, basic_pct) "
                "VALUES (%s, 1000, 26000, 50) ON CONFLICT (employee_id) DO UPDATE "
                "SET salary_per_day=1000, monthly_ctc=26000, basic_pct=50", (seed_employee["employee_id"],))
    # A full month present: with no attendance the whole month is loss of
    # pay and the payslip scales every deduction down to the zero earned.
    for day in range(1, 31):
        cur.execute("INSERT INTO attendance (employee_id, date, login_time, logout_time, status, attendance_type) "
                    "VALUES (%s, %s, '09:00:00', '18:00:00', 'Full Day Login', 'Full Day') "
                    "ON CONFLICT DO NOTHING", (seed_employee["employee_id"], f"2026-09-{day:02d}"))
    cur.close()


def _row_amount(html, label):
    m = re.search(re.escape(label) + r"[^<]*</td><td[^>]*>Rs\. ([\d,]+\.\d\d)", html)
    assert m, f"no '{label}' row"
    return float(m.group(1).replace(",", ""))


def test_payslip_deducts_no_pf_or_pt_when_both_are_set_to_zero(client, seed_admin, seed_employee, zero_statutory):
    _admin_session(client, seed_admin["username"])
    html = client.get(f"/view_payslip/{seed_employee['employee_id']}/2026/9").get_data(as_text=True)
    assert _row_amount(html, "PF -- Employee Contribution") == 0.0
    assert _row_amount(html, "Professional Tax") == 0.0
