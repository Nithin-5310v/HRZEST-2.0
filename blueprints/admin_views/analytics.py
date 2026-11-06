# -*- coding: utf-8 -*-
"""admin_views: analytics dashboard"""
import datetime
from database import get_db_connection
from extensions import app_log
from flask import render_template
from utils.attendance_utils import attendance_pct_series
from utils.auth import role_required
from utils.helpers import company_today, decrypt_pii, get_company_settings
from . import admin_views_bp


@admin_views_bp.route("/analytics")
@role_required("admin")
def analytics():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    co = get_company_settings()

    today = company_today()
    _doj_start = today.replace(day=1)
    _doj_end = datetime.date(today.year + 1, 1, 1) if today.month == 12 else today.replace(month=today.month + 1, day=1)

    # These six were six separate cursor.execute() round-trips (one per
    # scalar count) -- each is an independent COUNT over its own table, so
    # they can run as one query via scalar subqueries instead.
    cursor.execute("""
        SELECT
            (SELECT COUNT(*) FROM leave_requests WHERE status='Pending'),
            (SELECT COUNT(*) FROM resignation_requests WHERE status='Pending'),
            (SELECT COUNT(*) FROM tickets WHERE status IN ('Open','In Progress')),
            (SELECT COUNT(*) FROM employees),
            (SELECT COUNT(*) FROM employees WHERE date_of_joining >= %s AND date_of_joining < %s),
            (SELECT COUNT(DISTINCT employee_id) FROM attendance WHERE date=%s AND login_time IS NOT NULL)
    """, (_doj_start, _doj_end, today))
    (pending_leaves, pending_resignations, pending_tickets,
     total_employees, new_this_month, today_present) = cursor.fetchone()
    today_absent = max(0, total_employees - today_present)

    cursor.execute("SELECT date FROM holidays")
    all_holidays = {r[0] for r in cursor.fetchall()}

    # Precompute all 6 month boundaries up front so their attendance can be
    # pulled in a single grouped query below instead of one
    # cursor.execute() per month.
    _month_bounds = []
    for i in range(5, -1, -1):
        ref = today.replace(day=1) - datetime.timedelta(days=1) * (i * 28)
        ref = ref.replace(day=1)
        y, m = ref.year, ref.month
        month_start = datetime.date(y, m, 1)
        month_end = datetime.date(y + 1, 1, 1) if m == 12 else datetime.date(y, m + 1, 1)
        _month_bounds.append((y, m, month_start, month_end))

    cursor.execute("""
        SELECT a.date, COUNT(DISTINCT a.employee_id)
        FROM attendance a JOIN employees e ON e.employee_id = a.employee_id
        WHERE a.date >= %s AND a.date < %s AND a.login_time IS NOT NULL
        GROUP BY a.date
    """, (_month_bounds[0][2], _month_bounds[-1][3]))
    _present_by_day = {r[0]: r[1] for r in cursor.fetchall()}
    monthly_series = attendance_pct_series(_month_bounds, _present_by_day, all_holidays, today, total_employees)

    # Current month is always the loop's last entry (i=0 above) -- reuse it
    # instead of re-running the identical query a second time.
    avg_attendance_pct = monthly_series[-1]['att_pct'] if monthly_series else 0

    cursor.execute("""
        SELECT department, COUNT(*) as cnt FROM employees
        WHERE department IS NOT NULL AND department != ''
        GROUP BY department ORDER BY cnt DESC
    """)
    dept_data = [{'department': r[0], 'count': r[1]} for r in cursor.fetchall()]

    cursor.execute("""
        SELECT lt.name, COUNT(*) as cnt
        FROM leave_requests lr
        JOIN leave_types lt ON lr.leave_type_id = lt.id
        WHERE lr.status='Approved' AND EXTRACT(YEAR FROM lr.leave_date)=%s
        GROUP BY lt.name ORDER BY cnt DESC
    """, (today.year,))
    leave_by_type = [{'name': r[0], 'count': r[1]} for r in cursor.fetchall()]

    cursor.execute("""
        SELECT e.employee_id, e.name,
               ROUND(COUNT(CASE WHEN a.login_time IS NOT NULL THEN 1 END)::NUMERIC /
                     GREATEST((LEAST((date_trunc('month', %s::date) + INTERVAL '1 month - 1 day')::date, %s::date) - %s::date) + 1, 1) * 100, 1) AS pct
        FROM employees e
        LEFT JOIN attendance a ON e.employee_id=a.employee_id AND EXTRACT(MONTH FROM a.date)=%s AND EXTRACT(YEAR FROM a.date)=%s
        GROUP BY e.employee_id, e.name
        ORDER BY pct DESC LIMIT 5
    """, (datetime.date(today.year, today.month, 1), today, datetime.date(today.year, today.month, 1), today.month, today.year))
    top_present = [{'name': r[1], 'employee_id': r[0], 'pct': float(r[2] or 0)} for r in cursor.fetchall()]

    # gender is Fernet-encrypted (non-deterministic ciphertext -- the same
    # plaintext never produces the same bytes twice), so GROUP BY gender at
    # the SQL level would group by ciphertext and put every employee in
    # their own bucket. Aggregate in Python instead, after decrypting.
    cursor.execute("SELECT gender FROM employees WHERE gender IS NOT NULL AND gender != ''")
    _gender_counts = {}
    for (_g_enc,) in cursor.fetchall():
        _g = decrypt_pii(_g_enc)
        if _g:
            _gender_counts[_g] = _gender_counts.get(_g, 0) + 1
    gender_data = [{'gender': g, 'count': c} for g, c in
                   sorted(_gender_counts.items(), key=lambda kv: -kv[1])]

    # Attendance heatmap -- last 35 days (5 weeks) present count per day
    heatmap_start = today - datetime.timedelta(days=34)
    cursor.execute("""
        SELECT date, COUNT(DISTINCT employee_id) as cnt
        FROM attendance
        WHERE date BETWEEN %s AND %s AND login_time IS NOT NULL
        GROUP BY date
    """, (heatmap_start, today))
    heatmap_raw = {r[0]: r[1] for r in cursor.fetchall()}
    heatmap_data = []
    for i in range(35):
        d = heatmap_start + datetime.timedelta(days=i)
        heatmap_data.append({'date': d.strftime('%Y-%m-%d'), 'day': d.strftime('%a'), 'count': heatmap_raw.get(d, 0)})

    # Department-wise attendance rate this month
    cursor.execute("""
        SELECT e.department,
               COUNT(DISTINCT e.employee_id) as total_emp,
               COUNT(DISTINCT CASE WHEN a.login_time IS NOT NULL THEN a.employee_id END) as present_emp
        FROM employees e
        LEFT JOIN attendance a ON e.employee_id=a.employee_id AND EXTRACT(MONTH FROM a.date)=%s AND EXTRACT(YEAR FROM a.date)=%s
        WHERE e.department IS NOT NULL AND e.department != ''
        GROUP BY e.department
        ORDER BY present_emp DESC
    """, (today.month, today.year))
    dept_attendance = []
    for r in cursor.fetchall():
        dept, total, present = r[0], r[1], r[2]
        pct = round(present / total * 100, 1) if total else 0
        dept_attendance.append({'dept': dept, 'total': total, 'present': present, 'pct': pct})

    # Late arrival trend -- last 14 days
    late_start = today - datetime.timedelta(days=13)
    cursor.execute("""
        SELECT date, COUNT(DISTINCT employee_id) as late_cnt
        FROM attendance
        WHERE date BETWEEN %s AND %s AND status='Late Login'
        GROUP BY date ORDER BY date ASC
    """, (late_start, today))
    late_raw = {r[0]: r[1] for r in cursor.fetchall()}
    late_trend = []
    for i in range(14):
        d = late_start + datetime.timedelta(days=i)
        late_trend.append({'date': d.strftime('%d %b'), 'count': late_raw.get(d, 0)})

    # Employee retention -- tenure bands
    cursor.execute("SELECT date_of_joining FROM employees WHERE date_of_joining IS NOT NULL")
    retention = {'0-6m': 0, '6-12m': 0, '1-3y': 0, '3y+': 0}
    for (doj,) in cursor.fetchall():
        if isinstance(doj, str):
            try:
                doj = datetime.date.fromisoformat(doj)
            except Exception as _e:
                app_log.debug("Skipping bad date_of_joining value %r: %s", doj, _e)
                continue
        months = (today.year - doj.year) * 12 + (today.month - doj.month)
        if months < 6:
            retention['0-6m'] += 1
        elif months < 12:
            retention['6-12m'] += 1
        elif months < 36:
            retention['1-3y'] += 1
        else:
            retention['3y+'] += 1

    # Smart Alerts Panel
    smart_alerts = []

    # These four were four separate round-trips scattered across alerts #2
    # and #6 below -- same consolidation as the block above, run together
    # up front so the alert-building logic below just reads the results.
    week_start = today - datetime.timedelta(days=today.weekday())
    last_week_start = week_start - datetime.timedelta(days=7)
    # expiry_date bounds computed in Python (today / today+30d) rather than
    # Postgres's CURRENT_DATE + INTERVAL '30 days' -- the latter has no
    # SQLite equivalent, so under the local dev fallback (database.py) this
    # whole 4-column query silently failed and fetchone() returned None.
    thirty_days_out = today + datetime.timedelta(days=30)
    cursor.execute("""
        SELECT
            (SELECT COUNT(*) FROM leave_requests WHERE leave_date >= %s),
            (SELECT COUNT(*) FROM leave_requests WHERE leave_date >= %s AND leave_date < %s),
            (SELECT COUNT(*) FROM overtime_records WHERE status='Pending'),
            (SELECT COUNT(*) FROM employee_documents
                WHERE expiry_date IS NOT NULL
                  AND expiry_date >= %s
                  AND expiry_date <= %s)
    """, (week_start, last_week_start, week_start, today, thirty_days_out))
    (leaves_this_week, leaves_last_week,
     ot_pending_count, expiring_docs) = cursor.fetchone()

    # 1. Employees absent 3+ consecutive working days
    working_days_back = []
    for i in range(1, 15):
        d = today - datetime.timedelta(days=i)
        if d.weekday() != 6 and d not in all_holidays:
            working_days_back.append(d)
        if len(working_days_back) == 5:
            break
    last3 = working_days_back[:3]
    if len(last3) == 3:
        cursor.execute("""
            SELECT e.name, e.employee_id
            FROM employees e
            WHERE NOT EXISTS (
                SELECT 1 FROM attendance a
                WHERE a.employee_id = e.employee_id
                AND a.date IN (%s,%s,%s)
                AND a.login_time IS NOT NULL
            )
        """, (last3[0], last3[1], last3[2]))
        absent3 = cursor.fetchall()
        if absent3:
            names = ', '.join(r[1] for r in absent3[:3])
            extra = f' +{len(absent3)-3} more' if len(absent3) > 3 else ''
            smart_alerts.append({
                'level': 'danger',
                'icon': 'ti-user-off',
                'title': f'{len(absent3)} employee{"s" if len(absent3) > 1 else ""} absent for 3+ consecutive days',
                'detail': names + extra,
                'link': '/monthly_report'
            })

    # 2. Leave requests spike this week vs last week
    if leaves_last_week > 0 and leaves_this_week > leaves_last_week * 1.4:
        pct_jump = round((leaves_this_week - leaves_last_week) / leaves_last_week * 100)
        smart_alerts.append({
            'level': 'warning',
            'icon': 'ti-calendar-up',
            'title': f'Leave requests spiked {pct_jump}% compared to last week',
            'detail': f'{leaves_this_week} requests this week vs {leaves_last_week} last week',
            'link': '/leave_requests'
        })

    # 3. Employees with attendance below 50% this month
    cursor.execute("""
        SELECT e.name, e.employee_id,
               COUNT(CASE WHEN a.login_time IS NOT NULL THEN 1 END) as present_days,
               COUNT(a.date) as total_days
        FROM employees e
        LEFT JOIN attendance a ON e.employee_id=a.employee_id
            AND EXTRACT(MONTH FROM a.date)=%s AND EXTRACT(YEAR FROM a.date)=%s
        GROUP BY e.employee_id, e.name
        HAVING COUNT(a.date) > 0
           AND (COUNT(CASE WHEN a.login_time IS NOT NULL THEN 1 END)::NUMERIC / COUNT(a.date)) < 0.5
    """, (today.month, today.year))
    low_att = cursor.fetchall()
    if low_att:
        names = ', '.join(r[1] for r in low_att[:3])
        extra = f' +{len(low_att)-3} more' if len(low_att) > 3 else ''
        smart_alerts.append({
            'level': 'warning',
            'icon': 'ti-chart-bar-off',
            'title': f'{len(low_att)} employee{"s" if len(low_att) > 1 else ""} below 50% attendance this month',
            'detail': names + extra,
            'link': '/monthly_report'
        })

    # 4. High pending leave approvals
    if pending_leaves >= 5:
        smart_alerts.append({
            'level': 'warning',
            'icon': 'ti-clock-pause',
            'title': f'{pending_leaves} leave requests pending approval',
            'detail': 'Employees may be waiting -- review and approve',
            'link': '/leave_requests'
        })

    # 5. New joiners who have never logged in
    cursor.execute("""
        SELECT e.name, e.employee_id FROM employees e
        WHERE e.date_of_joining >= %s
        AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.employee_id=e.employee_id AND a.login_time IS NOT NULL)
    """, (today - datetime.timedelta(days=30),))
    never_logged = cursor.fetchall()
    if never_logged:
        names = ', '.join(r[1] for r in never_logged[:3])
        extra = f' +{len(never_logged)-3} more' if len(never_logged) > 3 else ''
        _plural = len(never_logged) > 1
        smart_alerts.append({
            'level': 'info',
            'icon': 'ti-user-question',
            'title': f'{len(never_logged)} new joiner{"s" if _plural else ""} '
                     f'{"have" if _plural else "has"} never logged attendance',
            'detail': names + extra,
            'link': '/employees'
        })

    # 6. Pending overtime approvals
    if ot_pending_count >= 3:
        smart_alerts.append({
            'level': 'info',
            'icon': 'ti-clock-bolt',
            'title': f'{ot_pending_count} overtime requests waiting for approval',
            'detail': 'Review pending OT requests from the dashboard',
            'link': '/overtime'
        })

    # 7. Documents expiring in next 30 days
    if expiring_docs > 0:
        smart_alerts.append({
            'level': 'warning',
            'icon': 'ti-file-alert',
            'title': f'{expiring_docs} employee document{"s" if expiring_docs > 1 else ""} expiring within 30 days',
            'detail': 'Review and renew documents before they expire',
            'link': '/employees'
        })

    if not smart_alerts:
        smart_alerts.append({
            'level': 'success',
            'icon': 'ti-circle-check',
            'title': 'All systems healthy -- no anomalies detected',
            'detail': 'Attendance, leaves and approvals are all on track',
            'link': ''
        })

    cursor.close()
    db.close()

    return render_template("analytics.html",
                           co=co,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           total_employees=total_employees,
                           new_this_month=new_this_month,
                           today_present=today_present,
                           today_absent=today_absent,
                           avg_attendance_pct=avg_attendance_pct,
                           monthly_series=monthly_series,
                           dept_data=dept_data,
                           leave_by_type=leave_by_type,
                           top_present=top_present,
                           gender_data=gender_data,
                           heatmap_data=heatmap_data,
                           dept_attendance=dept_attendance,
                           late_trend=late_trend,
                           retention=retention,
                           smart_alerts=smart_alerts,
                           active_nav="analytics",
                           )
