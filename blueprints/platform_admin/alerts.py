# -*- coding: utf-8 -*-
"""platform_admin: duplicate-signup alerts"""
from database import get_master_db
from flask import flash, redirect, render_template, request, session
from . import _platform_admin_required, platform_admin_bp


@platform_admin_bp.route("/super_admin/duplicate-alerts", methods=["GET"])
@_platform_admin_required
def platform_admin_duplicate_alerts():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, application_id, attempted_company_name, attempted_admin_email, conflicting_tenant_id, "
        "conflicting_company_name, conflicting_admin_email, match_type, acknowledged, acknowledged_by, "
        "acknowledged_at, source_ip, created_at "
        "FROM tenant_duplicate_alerts ORDER BY acknowledged ASC, created_at DESC"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    cols = ["id", "application_id", "attempted_company_name", "attempted_admin_email", "conflicting_tenant_id",
            "conflicting_company_name", "conflicting_admin_email", "match_type", "acknowledged", "acknowledged_by",
            "acknowledged_at", "source_ip", "created_at"]
    alerts = [dict(zip(cols, r)) for r in rows]
    return render_template("super_admin_duplicate_alerts.html", alerts=alerts)


@platform_admin_bp.route("/super_admin/duplicate-alerts/<int:alert_id>/acknowledge", methods=["POST"])
@_platform_admin_required
def platform_admin_acknowledge_duplicate_alert(alert_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "UPDATE tenant_duplicate_alerts SET acknowledged=1, acknowledged_by=%s, acknowledged_at=NOW() WHERE id=%s",
        (session.get("platform_admin_username"), alert_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    flash("Alert acknowledged.", "success")
    return redirect("/super_admin/duplicate-alerts")


@platform_admin_bp.route("/super_admin/duplicate-alerts/bulk-acknowledge", methods=["POST"])
@_platform_admin_required
def platform_admin_bulk_acknowledge_duplicate_alerts():
    alert_ids = [int(v) for v in request.form.getlist("alert_ids") if v.isdigit()]
    if not alert_ids:
        flash("No alerts were selected.", "error")
        return redirect("/super_admin/duplicate-alerts")

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    placeholders = ",".join(["%s"] * len(alert_ids))
    cur.execute(
        f"UPDATE tenant_duplicate_alerts SET acknowledged=1, acknowledged_by=%s, acknowledged_at=NOW() "  # nosec B608 -- placeholders is a fixed-width %s list sized to alert_ids, all values still bound as params  # noqa: E501
        f"WHERE id IN ({placeholders})",
        [session.get("platform_admin_username")] + alert_ids,
    )
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    flash(f"{updated} alert(s) acknowledged.", "success")
    return redirect("/super_admin/duplicate-alerts")
