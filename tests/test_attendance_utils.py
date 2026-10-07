"""Unit tests for utils/attendance_utils.py branches not already exercised
indirectly through blueprint tests: time conversion, shift lookup,
legacy status inference, and overtime detection.
"""
import datetime
import utils.attendance_utils as au


class TestTdToTime:
    def test_none_returns_none(self):
        assert au._td_to_time(None) is None

    def test_time_instance_passthrough(self):
        t = datetime.time(9, 30, 0)
        assert au._td_to_time(t) is t

    def test_timedelta_converted_to_time(self):
        assert au._td_to_time(datetime.timedelta(hours=9, minutes=30)) == datetime.time(9, 30, 0)

    def test_timedelta_over_24h_wraps(self):
        assert au._td_to_time(datetime.timedelta(hours=25, minutes=5)) == datetime.time(1, 5, 0)


class TestGetEmployeeShift:
    def test_assigned_shift_is_returned(self, db_engine, seed_employee):
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO shifts (name, start_time, half_time, end_time) VALUES (%s,%s,%s,%s) RETURNING id",
            ("Helper Shift", "10:00:00", "14:00:00", "19:00:00"),
        )
        shift_id = cur.fetchone()[0]
        cur.execute("UPDATE employees SET shift_id=%s WHERE employee_id=%s",
                    (shift_id, seed_employee["employee_id"]))
        try:
            start, half, end, name = au.get_employee_shift(seed_employee["employee_id"], cur)
            assert (start, half, end, name) == (
                datetime.time(10, 0), datetime.time(14, 0), datetime.time(19, 0), "Helper Shift")
        finally:
            cur.execute("UPDATE employees SET shift_id=NULL WHERE employee_id=%s", (seed_employee["employee_id"],))
            cur.execute("DELETE FROM shifts WHERE id=%s", (shift_id,))
            cur.close()

    def test_no_shift_assigned_falls_back_to_default(self, db_engine, seed_employee):
        import utils.config as cfg
        cur = db_engine.cursor()
        start, half, end, name = au.get_employee_shift(seed_employee["employee_id"], cur)
        cur.close()
        default = cfg.shift_rules()
        assert (start, half, end, name) == (default.start, default.half, default.end, "Default")


class TestInferTypeLegacy:
    def test_no_login_time_is_absent(self):
        assert au.infer_type_legacy("Present", None, datetime.time(18, 0)) == "Absent"

    def test_half_day_or_early_logout_with_logout_present_is_half_day(self):
        assert au.infer_type_legacy("Half Day Logout", datetime.time(9, 0), datetime.time(13, 0)) == "Half Day"
        assert au.infer_type_legacy("Early Logout", datetime.time(9, 0), datetime.time(15, 0)) == "Half Day"


class TestDetectOvertime:
    def test_missing_logout_time_is_a_noop(self, seed_employee):
        # logout_time=None -> _td_to_time(None) is None -> early return,
        # no overtime_records row should be written.
        au.detect_overtime(seed_employee["employee_id"], datetime.date.today(), None)

    def test_significant_overtime_is_recorded(self, db_engine, seed_employee):
        emp_id = seed_employee["employee_id"]
        cur = db_engine.cursor()
        cur.execute(
            "INSERT INTO shifts (name, start_time, half_time, end_time) VALUES (%s,%s,%s,%s) RETURNING id",
            ("OT Shift", "09:00:00", "13:00:00", "18:00:00"),
        )
        shift_id = cur.fetchone()[0]
        cur.execute("UPDATE employees SET shift_id=%s WHERE employee_id=%s", (shift_id, emp_id))
        cur.execute(
            "INSERT INTO salary_config (employee_id, salary_per_day) VALUES (%s,%s) "
            "ON CONFLICT (employee_id) DO UPDATE SET salary_per_day=EXCLUDED.salary_per_day",
            (emp_id, 800),
        )
        today = datetime.date.today()
        try:
            au.detect_overtime(emp_id, today, datetime.time(19, 0))  # 60 min past shift end
            cur.execute(
                "SELECT ot_minutes, status FROM overtime_records WHERE employee_id=%s AND date=%s",
                (emp_id, today),
            )
            row = cur.fetchone()
            assert row == (60, "Pending")
        finally:
            cur.execute("DELETE FROM overtime_records WHERE employee_id=%s AND date=%s", (emp_id, today))
            cur.execute("DELETE FROM salary_config WHERE employee_id=%s", (emp_id,))
            cur.execute("UPDATE employees SET shift_id=NULL WHERE employee_id=%s", (emp_id,))
            cur.execute("DELETE FROM shifts WHERE id=%s", (shift_id,))
            cur.close()

    def test_db_error_is_swallowed(self, monkeypatch, seed_employee):
        monkeypatch.setattr(au, "get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        au.detect_overtime(seed_employee["employee_id"], datetime.date.today(), datetime.time(19, 0))
