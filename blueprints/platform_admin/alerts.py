# -*- coding: utf-8 -*-
"""platform_admin: duplicate-signup alerts, and the signup blocks set from them"""
from database import get_master_db
from extensions import log_security_event
from flask import flash, redirect, render_template, request, session
from . import _platform_admin_required, platform_admin_bp

_ALERTS_URL = "/super_admin/duplicate-alerts"


@platform_admin_bp.route("/super_admin/duplicate-alerts", methods=["GET"])
@_platform_admin_required
def platform_admin_duplicate_alerts():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, application_id, attempted_company_name, attempted_admin_email, conflicting_tenant_id, "
        "conflicting_company_name, conflicting_admin_email, match_type, acknowledged, acknowledged_by, "
        "acknowledged_at, source_ip, created_at, action_taken "
        "FROM tenant_duplicate_alerts ORDER BY acknowledged ASC, created_at DESC"
    )
    rows = cur.fetchall()
    cur.execute("SELECT id, kind, value, reason, blocked_by, created_at FROM signup_blocks ORDER BY created_at DESC")
    blocks = [dict(zip(["id", "kind", "value", "reason", "blocked_by", "created_at"], r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    cols = ["id", "application_id", "attempted_company_name", "attempted_admin_email", "conflicting_tenant_id",
            "conflicting_company_name", "conflicting_admin_email", "match_type", "acknowledged", "acknowledged_by",
            "acknowledged_at", "source_ip", "created_at", "action_taken"]
    blocked = {(b["kind"], b["value"]) for b in blocks}
    alerts = []
    for r in rows:
        a = dict(zip(cols, r))
        a["email_blocked"] = ("email", (a["attempted_admin_email"] or "").strip().lower()) in blocked
        a["ip_blocked"] = bool(a["source_ip"]) and ("ip", a["source_ip"]) in blocked
        alerts.append(a)
    return render_template("super_admin_duplicate_alerts.html", alerts=alerts, blocks=blocks)


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
    return redirect(_ALERTS_URL)


@platform_admin_bp.route("/super_admin/duplicate-alerts/bulk-acknowledge", methods=["POST"])
@_platform_admin_required
def platform_admin_bulk_acknowledge_duplicate_alerts():
    alert_ids = [int(v) for v in request.form.getlist("alert_ids") if v.isdigit()]
    if not alert_ids:
        flash("No alerts were selected.", "error")
        return redirect(_ALERTS_URL)

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
    return redirect(_ALERTS_URL)


def _block_alerts(cur, alert_ids, block_email, block_ip, reason):
    """Blocks the email and/or source IP of each alert and marks the alerts
    handled. Returns how many new blocks were added (an address already
    blocked is left as it was)."""
    admin = session.get("platform_admin_username")
    placeholders = ",".join(["%s"] * len(alert_ids))
    cur.execute(
        f"SELECT id, attempted_admin_email, source_ip FROM tenant_duplicate_alerts WHERE id IN ({placeholders})",  # nosec B608 -- fixed-width %s list  # noqa: E501
        alert_ids,
    )
    added = 0
    found = []
    for alert_id, email, ip in cur.fetchall():
        found.append(alert_id)
        targets = []
        if block_email and (email or "").strip():
            targets.append(("email", email.strip().lower()))
        if block_ip and (ip or "").strip():
            targets.append(("ip", ip.strip()))
        for kind, value in targets:
            cur.execute(
                "INSERT INTO signup_blocks (kind, value, reason, alert_id, blocked_by) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (kind, value) DO NOTHING",
                (kind, value, reason or None, alert_id, admin),
            )
            added += cur.rowcount
    if found:
        placeholders = ",".join(["%s"] * len(found))
        cur.execute(
            f"UPDATE tenant_duplicate_alerts SET acknowledged=1, acknowledged_by=%s, acknowledged_at=NOW(), "  # nosec B608 -- fixed-width %s list  # noqa: E501
            f"action_taken='blocked' WHERE id IN ({placeholders})",
            [admin] + found,
        )
    return found, added


@platform_admin_bp.route("/super_admin/duplicate-alerts/<int:alert_id>/block", methods=["POST"])
@_platform_admin_required
def platform_admin_block_duplicate_alert(alert_id):
    """Stops whoever raised this alert from registering a company again: by
    their email, their IP address, or both. They still only ever see the
    generic "contact support" message."""
    block_email = request.form.get("block_email") == "1"
    block_ip = request.form.get("block_ip") == "1"
    if not block_email and not block_ip:
        flash("Choose the email, the IP address, or both to block.", "error")
        return redirect(_ALERTS_URL)
    reason = (request.form.get("reason") or "").strip()[:300]
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    found, added = _block_alerts(cur, [alert_id], block_email, block_ip, reason)
    conn.commit()
    cur.close()
    conn.close()
    if not found:
        flash("Alert not found.", "error")
        return redirect(_ALERTS_URL)
    log_security_event(
        "platform_admin.signup_blocked", f"Duplicate alert #{alert_id}: blocked "
        + " and ".join(k for k, on in (("email", block_email), ("IP", block_ip)) if on),
        level="WARNING", identifier=session.get("platform_admin_username"), alert_id=alert_id,
    )
    flash("Blocked. They can't register a new company from these details." if added
          else "Already blocked -- the alert is marked handled.", "success")
    return redirect(_ALERTS_URL)


@platform_admin_bp.route("/super_admin/duplicate-alerts/bulk-block", methods=["POST"])
@_platform_admin_required
def platform_admin_bulk_block_duplicate_alerts():
    alert_ids = [int(v) for v in request.form.getlist("alert_ids") if v.isdigit()]
    if not alert_ids:
        flash("No alerts were selected.", "error")
        return redirect(_ALERTS_URL)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    found, added = _block_alerts(cur, alert_ids, True, False, "")
    conn.commit()
    cur.close()
    conn.close()
    log_security_event(
        "platform_admin.signup_blocked", f"Blocked the emails of {len(found)} duplicate alert(s)",
        level="WARNING", identifier=session.get("platform_admin_username"), alert_ids=found,
    )
    flash(f"Blocked the email of {len(found)} alert(s).", "success")
    return redirect(_ALERTS_URL)


@platform_admin_bp.route("/super_admin/duplicate-alerts/<int:alert_id>/delete", methods=["POST"])
@_platform_admin_required
def platform_admin_delete_duplicate_alert(alert_id):
    """Removes an alert from the list (a false alarm). Any block already set
    from it stays until it is unblocked below."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("DELETE FROM tenant_duplicate_alerts WHERE id=%s", (alert_id,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if deleted:
        log_security_event("platform_admin.duplicate_alert_deleted", f"Duplicate alert #{alert_id} deleted",
                           level="INFO", identifier=session.get("platform_admin_username"), alert_id=alert_id)
    flash("Alert deleted." if deleted else "Alert not found.", "success" if deleted else "error")
    return redirect(_ALERTS_URL)


@platform_admin_bp.route("/super_admin/signup-blocks/<int:block_id>/delete", methods=["POST"])
@_platform_admin_required
def platform_admin_unblock_signup(block_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("DELETE FROM signup_blocks WHERE id=%s RETURNING kind", (block_id,))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    if not row:
        flash("That block was already removed.", "error")
        return redirect(_ALERTS_URL)
    log_security_event("platform_admin.signup_unblocked", f"Signup block #{block_id} ({row[0]}) removed",
                       level="INFO", identifier=session.get("platform_admin_username"), block_id=block_id)
    flash("Unblocked. They can register again.", "success")
    return redirect(_ALERTS_URL)
