# -*- coding: utf-8 -*-
"""platform_admin: audit log over security_events, SOC dashboard link"""
import os
from database import get_db_connection
from flask import flash, redirect, render_template, request
from . import _AUDIT_LOG_PER_PAGE, _AUDIT_LOG_PREFIXES, _platform_admin_required, platform_admin_bp


# ── Audit log: browsable view over security_events ──────────────────────
# security_events already exists (extensions.py's log_security_event) and
# every platform-admin action in this blueprint already writes to it --
# this just gives the admin a page to search/filter/page through that
# history instead of only ever seeing the dashboard's 20-row preview.

@platform_admin_bp.route("/super_admin/audit-log", methods=["GET"])
@_platform_admin_required
def platform_admin_audit_log():
    q = request.args.get("q", "").strip()
    level_filter = request.args.get("level", "").strip().upper()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    prefix_clause = " OR ".join(["event_type LIKE %s"] * len(_AUDIT_LOG_PREFIXES))
    conditions = [f"({prefix_clause})"]
    params = [f"{p}%" for p in _AUDIT_LOG_PREFIXES]
    if level_filter in ("INFO", "WARNING", "ERROR", "CRITICAL"):
        conditions.append("level=%s")
        params.append(level_filter)
    if q:
        conditions.append("(message ILIKE %s OR identifier ILIKE %s OR event_type ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    where_sql = " AND ".join(conditions)  # nosec B608 -- conditions are fixed literals built above, never from unescaped request input; all variable values are bound params  # noqa: E501

    conn = get_db_connection()
    cur = conn.cursor(buffered=True)
    cur.execute(f"SELECT COUNT(*) FROM security_events WHERE {where_sql}", params)  # nosec B608 -- see where_sql note above
    total = cur.fetchone()[0]
    total_pages = max(1, -(-total // _AUDIT_LOG_PER_PAGE))
    page = min(page, total_pages)
    offset = (page - 1) * _AUDIT_LOG_PER_PAGE
    cur.execute(
        f"SELECT event_type, level, message, identifier, ip, created_at FROM security_events "  # nosec B608 -- see where_sql note above
        f"WHERE {where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        params + [_AUDIT_LOG_PER_PAGE, offset],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    events = [
        {"event_type": r[0], "level": r[1], "message": r[2], "identifier": r[3], "ip": r[4], "created_at": r[5]}
        for r in rows
    ]
    return render_template(
        "super_admin_audit_log.html", events=events, q=q, level_filter=level_filter,
        page=page, total_pages=total_pages, total=total,
    )


@platform_admin_bp.route("/super_admin/soc", methods=["GET"])
@_platform_admin_required
def platform_admin_soc_dashboard():
    """Embeds the standalone cross-app SOC dashboard (soc-dashboard/, a
    separate Node/Express + React service -- see its README) in an iframe.

    That service aggregates security events (login attempts, failed
    logins, unauthorized access, suspicious activity) from the 4 separate
    apps -- HRMS Portal, Job Portal, Gradzest Portal, Food Delivery App --
    into one shared MongoDB collection. It is unrelated to this app's own
    `security_events` Postgres table used above by the audit log: that one
    is this Flask app's internal platform-admin action log.

    The frame loads the dashboard's plain address and the admin enters its
    admin token in the dashboard's own prompt. This app never reads or sends
    that token: it used to append SOC_ADMIN_TOKEN to the iframe URL, which put
    a permanent admin credential in the page source, browser history and the
    dashboard server's access logs.

    Outside APP_ENV=development the address must be https://, because the
    token is typed into the frame (an http frame on an https console is also
    blocked by browsers as mixed content, so it would only show a blank box).
    """
    soc_url = os.environ.get("SOC_DASHBOARD_URL", "").strip().rstrip("/")
    if not soc_url:
        flash("SOC dashboard is not configured. Set SOC_DASHBOARD_URL in the environment.", "error")
        return redirect("/super_admin")
    if not soc_url.startswith("https://") and os.environ.get("APP_ENV", "production") != "development":
        flash("SOC_DASHBOARD_URL must start with https:// so the dashboard's admin token is never sent unencrypted.", "error")
        return redirect("/super_admin")
    return render_template("super_admin_soc_dashboard.html", iframe_src=soc_url + "/")
