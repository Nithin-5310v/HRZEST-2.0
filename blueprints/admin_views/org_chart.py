# -*- coding: utf-8 -*-
"""admin_views: org chart page and data, admin tools"""
import os
from database import get_db_connection
from flask import jsonify, render_template, request, session
from utils.auth import admin_required, require_access, role_required
from utils.helpers import get_company_settings, get_pending_action_counts
from . import admin_views_bp
from .dashboard import _load_activity_log


@admin_views_bp.route("/org_chart")
@role_required("admin")
def org_chart_page():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    active_cid = session.get("active_company_id")
    pending_leaves, pending_resignations, pending_tickets = get_pending_action_counts(cursor, active_cid, tickets_open_only=True)
    if active_cid:
        cursor.execute(
            "SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != '' AND company_id=%s ORDER BY department", (active_cid,))
    else:
        cursor.execute(
            "SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != '' ORDER BY department")
    departments = [r[0] for r in cursor.fetchall()]
    co = get_company_settings()
    cursor.close()
    db.close()
    return render_template("org_chart.html", active_nav="admin_tools",
                           co=co, departments=departments,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           )


@admin_views_bp.route("/admin_tools")
@admin_required
@require_access("admin_tools")
def admin_tools():
    tab = request.args.get("tab", "org_chart")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    active_cid = session.get("active_company_id")
    pending_leaves, pending_resignations, pending_tickets = get_pending_action_counts(cursor, active_cid, tickets_open_only=True)

    if active_cid:
        cursor.execute(
            "SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != '' AND company_id=%s ORDER BY department", (active_cid,))
    else:
        cursor.execute(
            "SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != '' ORDER BY department")
    departments = [r[0] for r in cursor.fetchall()]

    co = get_company_settings()
    cursor.close()
    db.close()
    return render_template("admin_tools.html",
                           co=co, tab=tab, departments=departments,
                           pending_leaves=pending_leaves, pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           active_nav="admin_tools",
                           **_load_activity_log(),
                           )


@admin_views_bp.route("/api/org_chart_data")
@admin_required
def api_org_chart_data():
    dept_filter = request.args.get("dept", "")
    active_cid = session.get("active_company_id")
    db = get_db_connection()
    cursor = db.cursor()
    query = """
        SELECT e.employee_id, e.name, e.role, e.department,
               e.manager_id, e.face_image,
               COALESCE(e.manager_name, '') as manager_name
        FROM employees e
        WHERE COALESCE(e.is_active, 1) = 1
    """
    params = []
    if active_cid:
        query += " AND e.company_id = %s"
        params.append(active_cid)
    if dept_filter:
        query += " AND e.department = %s"
        params.append(dept_filter)
    query += " ORDER BY e.name"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    db.close()

    emp_map = {}
    for r in rows:
        emp_map[r[0]] = {
            "id": r[0],
            "name": r[1],
            "role": r[2] or "Employee",
            "department": r[3] or "",
            "manager_id": r[4],
            "has_photo": bool(r[5] and os.path.exists(r[5])),
            "children": []
        }

    roots = []
    for emp in emp_map.values():
        mid = emp["manager_id"]
        if mid and mid in emp_map and mid != emp["id"]:
            emp_map[mid]["children"].append(emp)
        else:
            roots.append(emp)

    # Sort children alphabetically
    def sort_tree(node):
        node["children"].sort(key=lambda x: x["name"])
        for child in node["children"]:
            sort_tree(child)
        return node

    roots.sort(key=lambda x: x["name"])
    tree = [sort_tree(r) for r in roots]
    return jsonify({"ok": True, "tree": tree, "total": len(emp_map)})
