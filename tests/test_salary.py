"""Tests for salary/attendance calculations: classification, working days."""
import datetime


# ── Attendance classification (classify_by_worked_minutes) ───────────────────
# Signature: classify_by_worked_minutes(login_status, total_minutes, s_start, s_end)
# Returns "Full Day", "Late - Full Day", or "Half Day"
# Logic: if total_minutes >= (shift_length_mins * 0.75) → Full Day / Late - Full Day; else Half Day

class TestClassifyByWorkedMinutes:
    S_START = datetime.time(9, 0)
    S_END = datetime.time(18, 0)   # 540-minute shift

    def _classify(self, login_status, total_minutes):
        from utils.attendance_utils import classify_by_worked_minutes
        return classify_by_worked_minutes(login_status, total_minutes, self.S_START, self.S_END)

    def test_full_day_on_normal_login(self):
        assert self._classify("Normal", 480) == "Full Day"

    def test_late_full_day_on_late_login(self):
        assert self._classify("Late Login", 480) == "Late - Full Day"

    def test_half_day_when_below_75_percent(self):
        # 75% of 540 min = 405 min; 400 < 405 → Half Day
        result = self._classify("Normal", 400)
        assert result == "Half Day"

    def test_full_day_at_exactly_75_percent(self):
        # 540 * 0.75 = 405 min
        result = self._classify("Normal", 405)
        assert result == "Full Day"

    def test_full_day_above_threshold(self):
        result = self._classify("Normal", 540)
        assert result == "Full Day"

    def test_zero_minutes_is_half_day(self):
        result = self._classify("Normal", 0)
        assert result == "Half Day"


# ── get_working_days ──────────────────────────────────────────────────────────
# Signature: get_working_days(year, month) → list[datetime.date]
# Excludes whichever weekdays this company's Settings -> Working Days isn't
# configured for (company_settings.working_days, default "Mon,Tue,Wed,Thu,Fri"
# -- a 5-day week). It used to hardcode "every day except Sunday" (a 6-day
# week) regardless of what was actually configured; these tests assume the
# column's own stated default (Mon-Fri) is in effect, which is the ambient
# state unless some other test has left company_settings.working_days set
# to something else and not cleaned up after itself.

class TestGetWorkingDays:
    def test_returns_list_of_dates(self):
        from utils.attendance_utils import get_working_days
        days = get_working_days(2025, 1)
        assert isinstance(days, list)
        assert all(isinstance(d, datetime.date) for d in days)

    def test_no_sundays_in_result(self):
        from utils.attendance_utils import get_working_days
        days = get_working_days(2025, 1)
        assert not any(d.weekday() == 6 for d in days), "Sundays must be excluded"

    def test_january_2025_day_count(self):
        from utils.attendance_utils import get_working_days
        # Jan 2025: 31 days, 4 Sundays (5,12,19,26) + 4 Saturdays (4,11,18,25)
        # -> 23 working days under the default Mon-Fri week.
        days = get_working_days(2025, 1)
        assert len(days) == 23

    def test_february_2025_day_count(self):
        from utils.attendance_utils import get_working_days
        # Feb 2025: 28 days, 4 Sundays + 4 Saturdays (1,8,15,22) -> 20 working
        # days under the default Mon-Fri week.
        days = get_working_days(2025, 2)
        assert len(days) == 20

    def test_all_days_within_month(self):
        from utils.attendance_utils import get_working_days
        days = get_working_days(2025, 3)
        assert all(d.month == 3 and d.year == 2025 for d in days)
