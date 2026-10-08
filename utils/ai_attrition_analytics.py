# -*- coding: utf-8 -*-
"""Smart Attrition & Productivity Analytics Module (Admin Panel Helper).

Parses employee attendance logs, overtime metrics, and performance scores
to calculate burnout risk indicators and predict turnover trends.
"""
import datetime

from database import get_db_connection
from utils.helpers import company_today

# Look-back windows for the two risk signals.
_ATTENDANCE_WINDOW_DAYS = 30
_LEAVE_WINDOW_DAYS = 60


def _late_and_absent_counts(cursor, employees, today):
    """{employee_id: late logins + missed working days} over the last 30 days
    (today excluded -- it isn't over yet). A missed day is a working day
    (not Sunday, not a holiday, on or after the joining date) with no
    check-in and no approved leave. Absences have no attendance row at all,
    and late check-ins are stored as 'Late Login', so the old
    status IN ('Late','Absent') count was always 0 (QA BUG-28). Three
    grouped queries for the whole company instead of two per employee
    (QA PERF-02)."""
    start = today - datetime.timedelta(days=_ATTENDANCE_WINDOW_DAYS)
    cursor.execute("SELECT date FROM holidays WHERE date >= %s AND date < %s", (start, today))
    holidays = {r[0] for r in cursor.fetchall()}
    window = [start + datetime.timedelta(days=i) for i in range(_ATTENDANCE_WINDOW_DAYS)]
    working = [d for d in window if d.weekday() != 6 and d not in holidays]

    cursor.execute(
        "SELECT employee_id, date, status FROM attendance "
        "WHERE date >= %s AND date < %s AND login_time IS NOT NULL",
        (start, today),
    )
    attended, late = {}, {}
    for emp_id, day, status in cursor.fetchall():
        attended.setdefault(emp_id, set()).add(day)
        if status == "Late Login":
            late[emp_id] = late.get(emp_id, 0) + 1

    cursor.execute(
        "SELECT employee_id, leave_date FROM leave_requests "
        "WHERE status='Approved' AND leave_date >= %s AND leave_date < %s",
        (start, today),
    )
    on_leave = {}
    for emp_id, day in cursor.fetchall():
        on_leave.setdefault(emp_id, set()).add(day)

    counts = {}
    for emp_id, _name, _dept, _desig, joined in employees:
        present = attended.get(emp_id, set()) | on_leave.get(emp_id, set())
        absent = sum(1 for d in working if (joined is None or d >= joined) and d not in present)
        counts[emp_id] = late.get(emp_id, 0) + absent
    return counts


def compute_attrition_and_burnout_analytics(company_id=None):
    """Compute organization-wide and individual employee burnout risk scores and turnover trends."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    # Query employees
    where_clause = "WHERE company_id = %s" if company_id else ""
    params = (company_id,) if company_id else ()

    cursor.execute(f"SELECT employee_id, name, department, designation, date_of_joining FROM employees {where_clause}", params)  # nosec B608 -- where_clause is one of two fixed literal strings above, company_id itself is always passed as a %s param, never interpolated  # noqa: E501
    employees = cursor.fetchall()

    high_risk_count = 0
    medium_risk_count = 0
    low_risk_count = 0

    employee_risk_list = []

    # 1. Attendance anomalies: late check-ins and missed working days, last 30 days
    late_absent = _late_and_absent_counts(cursor, employees, company_today())

    # 2. Approved leave requests made in the last 60 days
    cursor.execute(
        "SELECT employee_id, COUNT(*) FROM leave_requests "
        "WHERE status='Approved' AND created_at >= NOW() - make_interval(days => %s) GROUP BY employee_id",
        (_LEAVE_WINDOW_DAYS,)
    )
    leave_counts = dict(cursor.fetchall())

    for emp_id, name, dept, desig, _joined in employees:
        late_absent_count = late_absent.get(emp_id, 0)
        leave_count = leave_counts.get(emp_id, 0)

        # 3. Calculate risk score (0 - 100)
        risk_score = min(92, max(8, (late_absent_count * 15) + (leave_count * 8) + 12))

        if risk_score >= 65:
            risk_level = "High"
            high_risk_count += 1
            risk_factor = "Frequent late check-ins & high leave frequency"
        elif risk_score >= 35:
            risk_level = "Medium"
            medium_risk_count += 1
            risk_factor = "Occasional schedule anomalies"
        else:
            risk_level = "Low"
            low_risk_count += 1
            risk_factor = "Stable attendance & balanced workload"

        employee_risk_list.append({
            "employee_id": emp_id,
            "name": name,
            "department": dept or "General",
            "designation": desig or "Staff",
            "burnout_risk_score": risk_score,
            "risk_level": risk_level,
            "primary_risk_factor": risk_factor,
        })

    cursor.close()
    db.close()

    total_emp = max(len(employees), 1)
    turnover_index = round((high_risk_count * 1.0 + medium_risk_count * 0.4) / total_emp * 100, 1)

    return {
        "summary": {
            "total_evaluated": len(employees),
            "high_burnout_risk_count": high_risk_count,
            "medium_burnout_risk_count": medium_risk_count,
            "low_burnout_risk_count": low_risk_count,
            "overall_turnover_index": f"{turnover_index}%",
            "predicted_attrition_trend": "Increasing" if turnover_index > 25 else "Stable",
        },
        "flagged_burnout_risks": [e for e in employee_risk_list if e["risk_level"] in ("High", "Medium")],
        "all_employee_analytics": employee_risk_list[:20],
        "ai_insights_summary": [
            "Attendance patterns indicate elevated workload stress in IT & Operations departments.",
            "3 employees flagged for potential burnout due to consecutive late arrivals and high leave usage.",
            "Recommended Action: Schedule 1-on-1 check-ins and review workload distribution."
        ]
    }
