# -*- coding: utf-8 -*-
"""Attendance calculation helpers."""
import datetime
import calendar
from database import get_db_connection
from extensions import app_log
from utils.helpers import company_today
from utils.geo import haversine_km
import utils.config as cfg


def is_within_range(user_lat, user_lon, office_lat, office_lon, radius_m=None):
    if radius_m is None:
        radius_m = cfg.OFFICE_RADIUS_M
    return haversine_km(user_lat, user_lon, office_lat, office_lon) * 1000 <= radius_m


def is_within_office_range(user_lat, user_lon):
    """Per-tenant office geofence check for office-mode (non-WFH)
    check-ins, replacing the old always-on check against the single
    process-wide cfg.OFFICE_LAT/LON/RADIUS_M constants (which geofenced
    every tenant against whichever office the original deploying company
    configured in .env). Returns True (no enforcement) whenever the
    tenant hasn't turned location verification on, or hasn't set an
    office location yet -- geofencing is opt-in per company, not a
    default every new signup is silently held to."""
    from utils.helpers import get_auth_config
    auth_cfg = get_auth_config()
    if not auth_cfg["location_enabled"]:
        return True
    if auth_cfg["office_lat"] is None or auth_cfg["office_lon"] is None:
        return True
    return is_within_range(
        user_lat, user_lon, auth_cfg["office_lat"], auth_cfg["office_lon"],
        radius_m=auth_cfg["geo_radius"],
    )


def geofence_check_error(work_mode, work_lat, work_lon, lat, lon):
    """Validate a check-in's reported lat/lon against the employee's work
    mode. Returns an error message string to send back to the client, or
    None when the location is fine (or wasn't provided -- callers only
    call this when lat/lon are present).

    Shared by the admin-triggered kiosk check-in (blueprints/attendance.py)
    and the employee self-checkin (blueprints/employee_portal.py), which
    otherwise hand-repeated this exact WFH-vs-office branch."""
    if work_mode == 'wfh':
        if work_lat and work_lon and not is_within_range(float(lat), float(lon), float(work_lat), float(work_lon)):
            return "You are outside your registered home location."
    elif not is_within_office_range(float(lat), float(lon)):
        return "You are outside the office premises."
    return None


def fetch_employee_work_location(cursor, emp_id, include_face=False):
    """Run the "name, work_mode, work_lat, work_lon [, face_image]" lookup
    that every check-in route needs before calling geofence_check_error()
    above -- previously copy-pasted at each call site in blueprints/
    attendance.py and blueprints/employee_portal.py. Uses the caller's own
    cursor/connection (lifecycle -- close/rollback on miss -- stays with
    the caller, since each route handles that differently). Returns the row
    tuple, or None if emp_id doesn't exist."""
    cols = "name, work_mode, work_lat, work_lon, face_image" if include_face else "name, work_mode, work_lat, work_lon"
    cursor.execute(f"SELECT {cols} FROM employees WHERE employee_id=%s", (emp_id,))  # nosec B608 -- cols is one of two literals
    return cursor.fetchone()


def compute_session_worked_minutes(current_time, today, login_time, last_relogin_stored, worked_mins_stored):
    """Minutes worked in the current login/relogin session, added to
    worked_mins_stored from any prior session(s) earlier the same day.
    Shared by the same two check-in call sites as geofence_check_error --
    both compute "total minutes worked today" identically at logout time."""
    session_start = last_relogin_stored if last_relogin_stored else login_time
    if not isinstance(session_start, datetime.time):
        session_start = _td_to_time(session_start)
    cur_dt = datetime.datetime.combine(today, current_time)
    start_dt = datetime.datetime.combine(today, session_start)
    session_m = max(0, int((cur_dt - start_dt).total_seconds() / 60))
    return worked_mins_stored + session_m


def _td_to_time(val):
    if val is None:
        return None
    if isinstance(val, datetime.time):
        return val
    total = int(val.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return datetime.time(h % 24, m, s)


def get_employee_shift(emp_id, cursor):
    cursor.execute(
        "SELECT s.start_time, s.half_time, s.end_time, s.name "
        "FROM employees e JOIN shifts s ON e.shift_id = s.id "
        "WHERE e.employee_id = %s",
        (emp_id,)
    )
    row = cursor.fetchone()
    if row:
        return _td_to_time(row[0]), _td_to_time(row[1]), _td_to_time(row[2]), row[3]
    default = cfg.shift_rules()
    return default.start, default.half, default.end, "Default"


def classify_login(punch_time, s_start, s_half):
    """Status of the first punch of the day against the employee's shift
    (get_employee_shift) and the company's grace minutes. Shared by every
    check-in path so the same punch is classified the same way whichever
    device it came from (QA BUG-30)."""
    grace_end = (datetime.datetime.combine(datetime.date(2000, 1, 1), s_start) +
                 datetime.timedelta(minutes=cfg.shift_rules().grace_minutes)).time()
    if punch_time <= grace_end:
        return "Full Day Login"
    if punch_time <= s_half:
        return "Late Login"
    return "Half Day Login"


def classify_logout(punch_time, s_half, s_end):
    """Status of a logout punch against the employee's shift (see classify_login)."""
    if punch_time < s_half:
        return "Half Day Logout"
    if punch_time < s_end:
        return "Early Logout"
    return "Completed"


def classify_by_worked_minutes(login_status, total_minutes, s_start, s_end):
    today_d = datetime.date.today()
    shift_mins = max(1, int((
        datetime.datetime.combine(today_d, s_end) -
        datetime.datetime.combine(today_d, s_start)
    ).total_seconds() / 60))
    if total_minutes >= shift_mins * 0.75:
        return "Late - Full Day" if login_status == "Late Login" else "Full Day"
    return "Half Day"


def infer_type_legacy(status, login_time, logout_time):
    if not login_time:
        return "Absent"
    if not logout_time:
        return "Half Day" if status == "Half Day Login" else "Present"
    if status in ("Half Day Logout", "Early Logout"):
        return "Half Day"
    return "Full Day"


def detect_overtime(employee_id, date, logout_time):
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "SELECT s.end_time FROM employees e JOIN shifts s ON e.shift_id=s.id "
            "WHERE e.employee_id=%s",
            (employee_id,)
        )
        row = cursor.fetchone()
        shift_end = _td_to_time(row[0]) if row else cfg.shift_rules().end
        logout_t = _td_to_time(logout_time) if not isinstance(logout_time, datetime.time) else logout_time
        if logout_t is None or shift_end is None:
            cursor.close()
            db.close()
            return
        end_mins = shift_end.hour * 60 + shift_end.minute
        out_mins = logout_t.hour * 60 + logout_t.minute
        ot_mins = out_mins - end_mins
        if ot_mins < 30:
            cursor.close()
            db.close()
            return
        cursor.execute(
            "SELECT COALESCE(salary_per_day,0) FROM salary_config WHERE employee_id=%s",
            (employee_id,)
        )
        sc = cursor.fetchone()
        spd = float(sc[0]) if sc else 0.0
        ot_pay = round((spd / 8 / 60) * ot_mins, 2)
        cursor.execute("""
            INSERT INTO overtime_records
                (employee_id, date, shift_end, actual_logout, ot_minutes, ot_pay, status)
            VALUES (%s,%s,%s,%s,%s,%s,'Pending')
            ON CONFLICT (employee_id, date) DO UPDATE SET
                actual_logout=EXCLUDED.actual_logout,
                ot_minutes=EXCLUDED.ot_minutes,
                ot_pay=EXCLUDED.ot_pay
        """, (employee_id, date, shift_end, logout_t, ot_mins, ot_pay))
        db.commit()
        cursor.close()
        db.close()
    except Exception as exc:
        app_log.warning("Overtime record failed for %s on %s: %s", employee_id, date, exc, exc_info=True)


def get_working_days(year, month):
    _, last_day = calendar.monthrange(year, month)
    return [
        datetime.date(year, month, d)
        for d in range(1, last_day + 1)
        if datetime.date(year, month, d).weekday() != 6
    ]


def fetch_holidays_set(year, month):
    _, last_day = calendar.monthrange(year, month)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT date FROM holidays WHERE date BETWEEN %s AND %s",
        (datetime.date(year, month, 1), datetime.date(year, month, last_day))
    )
    holidays = {row[0] for row in cursor.fetchall()}
    cursor.close()
    db.close()
    return holidays


def get_billable_past_days(year, month):
    # company_today() (not the server's own local date) -- this feeds every
    # payroll/attendance-report "days counted so far this month" calculation
    # (blueprints/payroll.py, blueprints/attendance.py, blueprints/
    # employee_portal.py), so a server clock in a different timezone than
    # the tenant's configured one must not shift which days are billable.
    today = company_today()
    return [d for d in get_working_days(year, month) if d <= today]


# ── Attendance lockout (Medium/Prime plans only) ────────────────────────────
# Mirrors utils/auth.py's _check_login_lockout/_record_login_failure/
# _clear_login_failures pattern, keyed by (employee_id, date) instead of
# (identifier, attempt_type) and backed by its own attendance_lockouts
# table rather than the attendance table itself -- a row in `attendance`
# for a given (employee_id, date) doesn't exist until the FIRST SUCCESSFUL
# check-in (see blueprints/attendance.py's attendance() route), so a failed
# attempt has nowhere to persist on that table. Written synchronously (no
# async_writer queue like the login-lockout path) since attendance check-in
# isn't brute-forced at login-brute-force scale and this already runs
# inside the check-in request handler.
ATTENDANCE_LOCKOUT_MAX_ATTEMPTS = 4


def check_attendance_lockout(employee_id, date):
    """Returns (locked: bool, reason: str)."""
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "SELECT locked, lock_reason FROM attendance_lockouts WHERE employee_id=%s AND date=%s",
            (employee_id, date)
        )
        row = cursor.fetchone()
        cursor.close()
        db.close()
        if row and row[0]:
            return True, row[1] or "Attendance marking is locked for today. Contact your admin."
    except Exception as exc:
        # Fails open (treated as not-locked) -- acceptable since this is a
        # UX guard against repeated failed check-ins, not the actual
        # authentication check itself, but still worth logging since a
        # lockout silently not being enforced is worth knowing about.
        app_log.warning("check_attendance_lockout failed for %s on %s: %s", employee_id, date, exc, exc_info=True)
    return False, ""


def record_attendance_failure(employee_id, date, reason):
    """Called on every failed kiosk check-in attempt (face mismatch,
    fingerprint verify failure) -- available to every tenant. At
    ATTENDANCE_LOCKOUT_MAX_ATTEMPTS, locks online attendance for that
    employee/date -- only an admin can mark it after that (correct_attendance/
    bulk_mark_attendance, which also clear the lock on a successful write)."""
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "INSERT INTO attendance_lockouts (employee_id, date, failed_count) "
            "VALUES (%s, %s, 1) "
            "ON CONFLICT (employee_id, date) DO UPDATE SET "
            "failed_count=attendance_lockouts.failed_count+1",
            (employee_id, date)
        )
        db.commit()
        cursor.execute(
            "SELECT failed_count FROM attendance_lockouts WHERE employee_id=%s AND date=%s",
            (employee_id, date)
        )
        row = cursor.fetchone()
        if row and row[0] >= ATTENDANCE_LOCKOUT_MAX_ATTEMPTS:
            cursor.execute(
                "UPDATE attendance_lockouts SET locked=1, lock_reason=%s, locked_at=NOW() "
                "WHERE employee_id=%s AND date=%s",
                (reason, employee_id, date)
            )
            db.commit()
        cursor.close()
        db.close()
    except Exception as exc:
        app_log.warning("record_attendance_failure failed for %s on %s: %s", employee_id, date, exc, exc_info=True)


def clear_attendance_lockout(employee_id, date, admin_username=None):
    """Called after an admin manually marks attendance for that day
    (correct_attendance/bulk_mark_attendance) -- marking attendance IS the
    unlock action, no separate unlock UI needed."""
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "UPDATE attendance_lockouts SET locked=0, unlocked_by=%s "
            "WHERE employee_id=%s AND date=%s",
            (admin_username, employee_id, date)
        )
        db.commit()
        cursor.close()
        db.close()
    except Exception as exc:
        app_log.warning("clear_attendance_lockout failed for %s on %s: %s", employee_id, date, exc, exc_info=True)
