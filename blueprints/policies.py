# -*- coding: utf-8 -*-
"""Policies blueprint -- CRUD for company_policies (Terms, Rules, POSH,
etc.), a brand-new feature: this content used to be hardcoded static text
in templates/employee_portal.html, not stored or editable anywhere.
Company-wide by nature (no assigned_hr_username scoping, matching the
existing holidays table's own precedent) -- both 'admin' and HR-role
sessions can manage it, since either tier is allowed to speak for company
policy the same way both can already add/delete holidays.

Standalone blueprint file (not folded into blueprints/hr_dashboard.py) so
it's reusable/linkable outside that dashboard's shell too -- the HR
Dashboard's Policies tab just posts to these routes and reloads."""
from flask import Blueprint, request, session, redirect, jsonify, flash
from database import get_db_connection
from utils.auth import role_required, employee_required, HR_ROLE
from utils.helpers import tpath

policies_bp = Blueprint("policies", __name__)


def _policies_redirect():
    """Where to land after a mutation. HR sessions have a Policies tab on
    /hr_dashboard (blueprints/hr_dashboard.py); 'admin' sessions can manage
    policies too (see module docstring) but that route is HR-only
    (@role_required(HR_ROLE) there, deliberately -- an unscoped company-wide
    /hr_dashboard would be redundant with /admin, not a new capability), so
    redirecting an admin session there would just 403."""
    if session.get("admin_role") == HR_ROLE:
        return redirect(tpath("/hr_dashboard#policies"))
    return redirect(tpath("/profile/policies"))


def _form_category():
    """The submitted category. The 'other' choice means the admin typed
    their own category name in custom_category -- stored as-is, so a
    company isn't limited to the built-in categories."""
    category = request.form.get("category", "").strip()
    if category == "other":
        category = request.form.get("custom_category", "").strip()[:50]
    return category


@policies_bp.route("/policies/add", methods=["POST"])
@role_required("admin", HR_ROLE)
def add_policy():
    category = _form_category()
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not category or not title or not body:
        flash("Category, title, and body are all required.", "error")
        return _policies_redirect()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM company_policies")
    next_order = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO company_policies (category, title, body, sort_order, created_by, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (category, title, body, next_order, session.get("admin_username"), session.get("admin_username"))
    )
    db.commit()
    cursor.close()
    db.close()
    flash("Policy added.", "success")
    return _policies_redirect()


@policies_bp.route("/policies/<int:policy_id>/edit", methods=["POST"])
@role_required("admin", HR_ROLE)
def edit_policy(policy_id):
    category = _form_category()
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not category or not title or not body:
        flash("Category, title, and body are all required.", "error")
        return _policies_redirect()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE company_policies SET category=%s, title=%s, body=%s, updated_by=%s WHERE id=%s",
        (category, title, body, session.get("admin_username"), policy_id)
    )
    db.commit()
    cursor.close()
    db.close()
    flash("Policy updated.", "success")
    return _policies_redirect()


@policies_bp.route("/policies/<int:policy_id>/publish", methods=["POST"])
@role_required("admin", HR_ROLE)
def publish_policy(policy_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE company_policies SET is_published=1, updated_by=%s WHERE id=%s",
                   (session.get("admin_username"), policy_id))
    db.commit()
    cursor.close()
    db.close()
    flash("Policy published.", "success")
    return _policies_redirect()


@policies_bp.route("/policies/<int:policy_id>/unpublish", methods=["POST"])
@role_required("admin", HR_ROLE)
def unpublish_policy(policy_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE company_policies SET is_published=0, updated_by=%s WHERE id=%s",
                   (session.get("admin_username"), policy_id))
    db.commit()
    cursor.close()
    db.close()
    flash("Policy unpublished.", "success")
    return _policies_redirect()


@policies_bp.route("/policies/<int:policy_id>/delete", methods=["POST"])
@role_required("admin", HR_ROLE)
def delete_policy(policy_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM company_policies WHERE id=%s", (policy_id,))
    db.commit()
    cursor.close()
    db.close()
    flash("Policy deleted.", "success")
    return _policies_redirect()


@policies_bp.route("/api/employee/policies")
@employee_required
def api_employee_policies():
    """Read-only, published-only list for the employee-facing side. Not
    wired into templates/employee_portal.html yet (that page still shows
    its own hardcoded text) -- added now so that future swap is a one-query
    change instead of a new endpoint to design from scratch."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, category, title, body FROM company_policies "
        "WHERE is_published=1 ORDER BY sort_order, id"
    )
    rows = [{"id": r[0], "category": r[1], "title": r[2], "body": r[3]} for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return jsonify({"ok": True, "policies": rows})
