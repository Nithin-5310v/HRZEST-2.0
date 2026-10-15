# -*- coding: utf-8 -*-
"""
daily_report.py -- Nightly attendance summary email, available to every tenant.

Scheduled by APScheduler (registered in wsgi.py) to run at 23:59 every day.
Generates a per-company attendance summary and emails it to all admin addresses.

Note: this job has no per-tenant iteration yet -- it runs once against the
single-tenant DB_NAME fallback schema (same phasing as the rest of this
app pre-multi-tenant-migration; see app.py's _resolve_tenant() "Default
single-tenant fallback" branch). A real multi-tenant deployment would need
this to loop over att_master.tenants and call get_tenant_db(schema) per
company -- tracked as separate follow-up work, not done here.
"""
import os
import threading
from flask import Blueprint, g as _g, jsonify

from database import get_db_connection
from extensions import app, app_log
from utils.auth import admin_required
from utils.email_utils import get_email_config, send_email_async, get_admin_emails
from utils.helpers import get_pending_counts, company_today

daily_report_bp = Blueprint("daily_report", __name__)

_report_lock = threading.Lock()


# ── HTML email template ──────────────────────────────────────────────────────

def _build_email_html(date_str: str, stats: dict) -> str:
    present = stats.get("present", 0)
    absent = stats.get("absent", 0)
    late = stats.get("late", 0)
    total = stats.get("total", 0)
    on_leave = stats.get("on_leave", 0)
    pending_lv = stats.get("pending_leaves", 0)
    pct = round((present / total * 100), 1) if total else 0

    rows_html = ""
    for r in stats.get("rows", [])[:30]:   # cap at 30 rows in email
        status_color = {"Present": "#16A34A", "Absent": "#DC2626", "Late": "#D97706"}.get(r["status"], "#64748B")
        rows_html += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;">{r['employee_id']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;">{r['name']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;color:{status_color};font-weight:700;">{r['status']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;">{r.get('login_time') or '--'}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #E2E8F0;">{r.get('logout_time') or '--'}</td>
        </tr>"""

    pending_alert_html = "" if pending_lv == 0 else (
        '<div style="margin:16px 24px;padding:14px 18px;background:#FEF3C7;border-left:4px solid #F59E0B;'
        'border-radius:8px;font-size:14px;color:#92400E;"><strong>⏳ {} leave request{} pending approval.'
        '</strong></div>'
    ).format(pending_lv, "s" if pending_lv != 1 else "")
    more_rows_html = (
        "<p style='text-align:center;color:#94A3B8;font-size:12px;margin-top:12px;'>"
        "Showing first 30 employees. Log in to view full report.</p>"
    ) if len(stats.get("rows", [])) > 30 else ""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Daily Attendance Report</title></head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:Arial,sans-serif;">
  <div style="max-width:680px;margin:32px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#0F172A,#1E3A8A);padding:32px;text-align:center;">
      <h1 style="color:#fff;margin:0;font-size:22px;font-weight:800;">📊 Daily Attendance Report</h1>
      <p style="color:rgba(255,255,255,0.75);margin:8px 0 0;font-size:14px;">{date_str}</p>
    </div>

    <!-- KPI cards -->
    <div style="display:flex;justify-content:space-around;padding:24px 16px;background:#F8FAFC;border-bottom:1px solid #E2E8F0;">
      <div style="text-align:center;">
        <div style="font-size:28px;font-weight:800;color:#16A34A;">{present}</div>
        <div style="font-size:12px;color:#64748B;font-weight:600;">PRESENT</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:28px;font-weight:800;color:#DC2626;">{absent}</div>
        <div style="font-size:12px;color:#64748B;font-weight:600;">ABSENT</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:28px;font-weight:800;color:#D97706;">{late}</div>
        <div style="font-size:12px;color:#64748B;font-weight:600;">LATE</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:28px;font-weight:800;color:#8B5CF6;">{on_leave}</div>
        <div style="font-size:12px;color:#64748B;font-weight:600;">ON LEAVE</div>
      </div>
      <div style="text-align:center;">
        <div style="font-size:28px;font-weight:800;color:#0F172A;">{pct}%</div>
        <div style="font-size:12px;color:#64748B;font-weight:600;">ATTENDANCE</div>
      </div>
    </div>

    <!-- Pending alerts -->
    {pending_alert_html}

    <!-- Employee table -->
    <div style="padding:24px;">
      <h2 style="font-size:16px;font-weight:800;color:#0F172A;margin:0 0 16px;">Today's Attendance</h2>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#F1F5F9;">
            <th style="padding:10px 12px;text-align:left;color:#475569;font-weight:700;">ID</th>
            <th style="padding:10px 12px;text-align:left;color:#475569;font-weight:700;">Name</th>
            <th style="padding:10px 12px;text-align:left;color:#475569;font-weight:700;">Status</th>
            <th style="padding:10px 12px;text-align:left;color:#475569;font-weight:700;">Check-In</th>
            <th style="padding:10px 12px;text-align:left;color:#475569;font-weight:700;">Check-Out</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      {more_rows_html}
    </div>

    <!-- Footer -->
    <div style="padding:20px 24px;background:#F8FAFC;border-top:1px solid #E2E8F0;text-align:center;">
      <p style="color:#94A3B8;font-size:12px;margin:0;">
        This is an automated daily report from HRzest.com.<br>
        © {company_today().year} HRzest.com
      </p>
    </div>
  </div>
</body>
</html>"""


# ── Core report generation ───────────────────────────────────────────────────

def generate_and_send_daily_report():
    """
    Main job function -- called by APScheduler nightly. APScheduler invokes
    this with no Flask request (and no app context) at all, so it needs its
    own app_context() -- without one, get_db_connection() silently falls
    back to the "public" schema (see database.py), which has none of these
    tables, and every query below would raise.
    """
    with _report_lock:
        try:
            with app.app_context():
                # No per-request Host header to resolve a subdomain from --
                # same single-tenant fallback app.py's _resolve_tenant() uses
                # (see module docstring re: no cross-tenant iteration yet).
                _g.tenant_db = os.environ.get("DB_NAME", "employee_attendance")
                _run_report()
        except Exception:
            app_log.exception("daily_report: unhandled error in report job")


def _run_report():
    today = company_today()
    date_str = today.strftime("%A, %d %B %Y")
    app_log.info(f"daily_report: generating report for {date_str}")

    admin_emails = get_admin_emails()
    if not admin_emails:
        app_log.info("daily_report: no admin emails on file, skipping send")
        return

    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    # Build global attendance stats (shared across all admins for same company)
    cursor.execute("SELECT COUNT(*) FROM employees")
    total = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(DISTINCT employee_id) FROM attendance
        WHERE date=%s AND login_time IS NOT NULL
    """, (today,))
    present = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(DISTINCT employee_id) FROM attendance
        WHERE date=%s AND status='Late Login'
    """, (today,))
    late = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM leave_requests
        WHERE leave_date=%s AND status='Approved'
    """, (today,))
    on_leave = cursor.fetchone()[0]

    pending_leaves = get_pending_counts()[0]

    absent = max(0, total - present - on_leave)

    # Build employee-level rows
    cursor.execute("""
        SELECT e.employee_id, e.name,
               a.login_time, a.logout_time, a.status,
               CASE WHEN lr.id IS NOT NULL THEN 'On Leave' ELSE NULL END as leave_status
        FROM employees e
        LEFT JOIN attendance a ON e.employee_id=a.employee_id AND a.date=%s
        LEFT JOIN leave_requests lr ON e.employee_id=lr.employee_id
              AND lr.leave_date=%s AND lr.status='Approved'
        ORDER BY e.name
    """, (today, today))
    raw_rows = cursor.fetchall()

    rows = []
    for r in raw_rows:
        emp_status = r[5] or r[4] or ("Present" if r[2] else "Absent")
        rows.append({
            "employee_id": r[0],
            "name": r[1],
            "login_time": str(r[2])[:5] if r[2] else None,
            "logout_time": str(r[3])[:5] if r[3] else None,
            "status": emp_status,
        })

    cursor.close()
    db.close()

    stats = {
        "total": total, "present": present, "absent": absent,
        "late": late, "on_leave": on_leave, "pending_leaves": pending_leaves,
        "rows": rows,
    }

    cfg = get_email_config()
    if not cfg or not cfg.get("host"):
        app_log.warning("daily_report: no email config found, skipping send")
        return

    subject = f"📊 Daily Attendance Report -- {date_str}"
    html = _build_email_html(date_str, stats)
    for email in admin_emails:
        send_email_async(email, subject, html, cfg)
    app_log.info(f"daily_report: done -- sent to {len(admin_emails)} admins")


def send_weekly_employee_digests():
    """Generates and sends weekly personal attendance summary digests to employees."""
    with _report_lock:
        app_log.info("weekly_digest: starting employee digest run")
        try:
            db = get_db_connection()
            cursor = db.cursor(buffered=True)
            cursor.execute("SELECT employee_id, name, email FROM employees WHERE is_active=1 AND email IS NOT NULL AND email != ''")
            employees = cursor.fetchall()
            cursor.close()
            db.close()

            cfg = get_email_config()
            if not cfg or not cfg.get("host"):
                app_log.warning("weekly_digest: SMTP not configured, skipping send")
                return

            for emp in employees:
                emp_id, emp_name, emp_email = emp
                subject = f"✨ Your Weekly Attendance Summary -- {emp_name}"
                html = f"""
                <div style="font-family:sans-serif;max-width:550px;margin:20px auto;background:#fff;padding:24px;border-radius:12px;border:1px solid #e2e8f0;">
                  <h2 style="color:#1e3a8a;margin-top:0;">Weekly Digest for {emp_name}</h2>
                  <p style="color:#475569;font-size:14px;">Here is your weekly attendance and leave balance update.</p>
                  <div style="background:#f8fafc;padding:16px;border-radius:8px;margin:16px 0;">
                    <strong>Employee ID:</strong> {emp_id}<br>
                    <strong>Status:</strong> Active &amp; Up-to-date
                  </div>
                  <p style="font-size:13px;color:#64748b;">Log into your
                    <a href="http://localhost:5000/employee_portal" style="color:#3b82f6;">Employee Portal</a>
                    to view detailed payslips and apply for leave.</p>
                </div>
                """
                send_email_async(emp_email, subject, html, cfg)
            app_log.info(f"weekly_digest: sent to {len(employees)} active employees")
        except Exception as err:
            app_log.error("weekly_digest error: %s", err)


# ── Manual trigger routes (for testing) ──────────────────────────────────────

@daily_report_bp.route("/api/admin/trigger_daily_report", methods=["POST"])
@admin_required
def trigger_daily_report():
    """Any admin can manually trigger the daily report for testing."""
    t = threading.Thread(target=generate_and_send_daily_report, daemon=True)
    t.start()
    return jsonify({"ok": True, "msg": "Daily report generation started. Check admin email shortly."})


@daily_report_bp.route("/api/admin/trigger_weekly_digest", methods=["POST"])
@admin_required
def trigger_weekly_digest():
    """Triggers the weekly employee email digest."""
    t = threading.Thread(target=send_weekly_employee_digests, daemon=True)
    t.start()
    return jsonify({"ok": True, "msg": "Weekly employee digest generation started."})
