# -*- coding: utf-8 -*-
"""platform_admin: audit log over security_events"""
from database import get_db_connection
from flask import render_template, request
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
