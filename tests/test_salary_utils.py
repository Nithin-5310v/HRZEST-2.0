"""
utils/salary_utils.py tests — pure functions, no DB/Flask needed.

This module computes actual pay and renders the payslip an employee sees,
so these tests pin the exact arithmetic rather than just checking "it
doesn't crash." The payroll rules come from the current company's settings
via cfg.salary_rules() (utils/config.py) — patched here for deterministic
assertions.

Run with:
    python -m pytest tests/test_salary_utils.py -v
"""
import datetime
import pytest
import utils.config as cfg
from utils.salary_utils import compute_salary_entry, build_salary_slip_html


_DEFAULT_RULES = cfg.SalaryRules(late_deduction_rate=0.10, half_day_rate=0.50,
                                 holiday_pay="paid", leave_pay="exclude")


def _set_rules(monkeypatch, **changes):
    rules = _DEFAULT_RULES._replace(**changes)
    monkeypatch.setattr(cfg, "salary_rules", lambda: rules)


@pytest.fixture(autouse=True)
def deterministic_config(monkeypatch):
    _set_rules(monkeypatch)


def _dates(n, start=datetime.date(2026, 1, 1)):
    return [start + datetime.timedelta(days=i) for i in range(n)]


# ===========================================================================
# compute_salary_entry — attendance -> pay arithmetic
# ===========================================================================

class TestComputeSalaryEntryBasics:
    def test_all_full_days_earns_full_amount(self):
        days = _dates(5)
        att_map = {
            "E1": {d: ("E1", str(d), datetime.time(9, 0), datetime.time(18, 0), None, None, "Full Day") for d in days}
        }
        entry = compute_salary_entry("E1", "Emp One", 1000, att_map, days)
        assert entry["full_days"] == 5
        assert entry["full_earn"] == 5000.0
        assert entry["net"] == 5000.0
        assert entry["gross"] == 5000.0
        assert entry["deduction"] == 0.0

    def test_late_day_applies_late_deduction_rate(self):
        days = _dates(1)
        att_map = {"E1": {days[0]: ("E1", str(days[0]), datetime.time(10, 0), datetime.time(18, 0), None, None, "Late - Full Day")}}
        entry = compute_salary_entry("E1", "Emp One", 1000, att_map, days)
        assert entry["late_days"] == 1
        # spd=1000, LATE_DEDUCTION_RATE=0.10 -> late_earn = 1000 * 0.90 = 900
        assert entry["late_earn"] == 900.0
        assert entry["late_ded"] == 100.0
        assert entry["net"] == 900.0

    def test_half_day_applies_half_day_rate(self):
        days = _dates(1)
        att_map = {"E1": {days[0]: ("E1", str(days[0]), datetime.time(9, 0), datetime.time(13, 0), None, None, "Half Day")}}
        entry = compute_salary_entry("E1", "Emp One", 1000, att_map, days)
        assert entry["half_days"] == 1
        # spd=1000, HALF_DAY_RATE=0.50 -> half_earn = 1000 * 0.50 = 500
        assert entry["half_earn"] == 500.0
        assert entry["half_ded"] == 500.0

    def test_missing_attendance_record_counts_as_absent(self):
        days = _dates(3)
        entry = compute_salary_entry("E1", "Emp One", 1000, {}, days)
        assert entry["absent"] == 3
        assert entry["absent_ded"] == 3000.0
        assert entry["net"] == 0.0
        assert entry["gross"] == 3000.0
        assert entry["deduction"] == 3000.0

    def test_mixed_days_totals_are_consistent(self):
        days = _dates(4)
        att_map = {"E1": {
            days[0]: ("E1", str(days[0]), None, None, None, None, "Full Day"),
            days[1]: ("E1", str(days[1]), None, None, None, None, "Late - Full Day"),
            days[2]: ("E1", str(days[2]), None, None, None, None, "Half Day"),
            # days[3] absent (no record)
        }}
        entry = compute_salary_entry("E1", "Emp One", 1000, att_map, days)
        assert entry["full_days"] == 1
        assert entry["late_days"] == 1
        assert entry["half_days"] == 1
        assert entry["absent"] == 1
        assert entry["gross"] == 4000.0
        assert entry["net"] == round(1000 + 900 + 500, 2)
        assert entry["deduction"] == round(entry["gross"] - entry["net"], 2)


class TestComputeSalaryEntryHolidaysAndLeave:
    def test_paid_holiday_counts_as_full_day(self, monkeypatch):
        _set_rules(monkeypatch, holiday_pay="paid")
        days = _dates(1)
        entry = compute_salary_entry("E1", "Emp One", 1000, {}, days, holidays_set={days[0]})
        assert entry["holiday_days"] == 1
        assert entry["full_days"] == 1
        assert entry["absent"] == 0
        assert entry["net"] == 1000.0

    def test_unpaid_holiday_counts_as_absent(self, monkeypatch):
        _set_rules(monkeypatch, holiday_pay="unpaid")
        days = _dates(1)
        entry = compute_salary_entry("E1", "Emp One", 1000, {}, days, holidays_set={days[0]})
        assert entry["holiday_days"] == 1
        assert entry["absent"] == 1
        assert entry["full_days"] == 0
        assert entry["net"] == 0.0

    def test_leave_excluded_from_billable_no_pay_no_deduction(self, monkeypatch):
        _set_rules(monkeypatch, leave_pay="exclude")
        days = _dates(3)
        entry = compute_salary_entry("E1", "Emp One", 1000, {}, days, leave_dates={days[0]})
        assert entry["leave_days"] == 1
        # billable = 3 days - 1 leave = 2 effective billable days
        assert entry["billable"] == 2
        assert entry["absent"] == 2  # the other 2 days have no attendance record
        assert entry["gross"] == 2000.0  # leave day excluded from gross entirely

    def test_leave_as_absent_counts_as_absent_deduction(self, monkeypatch):
        _set_rules(monkeypatch, leave_pay="absent")
        days = _dates(1)
        entry = compute_salary_entry("E1", "Emp One", 1000, {}, days, leave_dates={days[0]})
        assert entry["leave_days"] == 0
        assert entry["absent"] == 1
        assert entry["billable"] == 1
        assert entry["net"] == 0.0


class TestComputeSalaryEntryLegacyInference:
    def test_null_attendance_type_falls_back_to_infer_type_legacy(self):
        """final = att_type if att_type else infer_type_legacy(...) — legacy
        rows with a NULL attendance_type must still be classified from
        status/login/logout instead of silently falling to absent."""
        days = _dates(1)
        att_map = {"E1": {
            days[0]: ("E1", str(days[0]), datetime.time(9, 0), datetime.time(18, 0), "Full Day Login", "Completed", None)
        }}
        entry = compute_salary_entry("E1", "Emp One", 1000, att_map, days)
        # infer_type_legacy on a full-day login + completed logout should
        # classify as a full/late day, not silently drop to absent.
        assert entry["absent"] == 0


# ===========================================================================
# build_salary_slip_html — payslip rendering, XSS-safety, arithmetic
# ===========================================================================

class TestBuildSalarySlipHtml:
    def _base_salary_data(self, **overrides):
        data = {
            "monthly_ctc": 50000, "basic_pct": 50,
            "full_days": 20, "late_days": 2, "half_days": 1, "absent": 2,
            "holiday_days": 4, "leave_days": 1, "incentive": 0,
        }
        data.update(overrides)
        return data

    def test_renders_valid_html_with_expected_values(self):
        html = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(), company_name="Acme Inc",
        )
        assert "<html>" in html
        assert "Jane Doe" in html
        assert "EMP001" in html
        assert "Acme Inc" in html
        # basic = 50000 * 50% = 25000.00
        assert "Rs. 25,000.00" in html

    def test_xss_in_employee_name_is_escaped(self):
        """emp_name is interpolated via _html.escape(str(emp_name)) — a
        malicious name must never appear as raw, executable HTML in a
        payslip page any employee can view."""
        malicious = "<script>alert(1)</script>"
        html = build_salary_slip_html(
            malicious, "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(),
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_xss_in_designation_and_department_is_escaped(self):
        html = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(),
            emp_designation="<img src=x onerror=alert(1)>",
            emp_dept="<b>Eng</b>",
        )
        assert "<img src=x onerror=alert(1)>" not in html
        assert "<b>Eng</b>" not in html

    def test_bank_account_is_masked(self):
        html = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(), bank_account="1234567890",
        )
        assert "1234567890" not in html
        assert "7890" in html  # last 4 digits kept visible
        assert "*" in html

    def test_monthly_ctc_falls_back_to_salary_per_day_times_26(self):
        html = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(monthly_ctc=0, spd=2000),
        )
        # monthly_ctc = 2000 * 26 = 52,000.00
        assert "Rs. 52,000.00" in html

    def test_gross_never_exceeds_ctc_conveyance_capped(self):
        """conveyance = min(1600, max(0, ctc - basic - hra)) — with a very
        low CTC, conveyance and special_allowance must not push gross above
        monthly_ctc."""
        html = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(monthly_ctc=1000, basic_pct=50),
        )
        # basic=500, hra=200, remaining for conveyance+special = 300 (< 1600 cap)
        assert "Rs. 1,000.00" in html  # gross salary line

    def test_statutory_deductions_capped_when_exceeding_gross_earned(self):
        """stat_ded > gross_earned triggers a proportional scale-down so
        net_pay can never go negative — this is the safety net for a very
        low-CTC employee with many absences."""
        html = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(monthly_ctc=2000, full_days=1, late_days=0,
                                   half_days=0, absent=25),
            payroll_cfg={"pf_employee_pct": 12, "professional_tax": 200, "tds_annual_pct": 0},
        )
        assert "Rs. -" not in html  # no negative currency values rendered

    def test_incentive_row_only_shown_when_positive(self):
        html_no_incentive = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(incentive=0),
        )
        html_with_incentive = build_salary_slip_html(
            "Jane Doe", "EMP001", "jane@test.local", "January", 2026, 1,
            self._base_salary_data(incentive=5000),
        )
        assert "Incentive / Bonus" not in html_no_incentive
        assert "Incentive / Bonus" in html_with_incentive
        assert "5000.00" in html_with_incentive
