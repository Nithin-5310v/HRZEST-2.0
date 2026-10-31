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
from flask import Blueprint, request, session, redirect, jsonify, flash, g
from database import get_db_connection
from utils.auth import (
    role_required, employee_required, employee_api_required, api_required, api_role_required, HR_ROLE,
)
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


def _published_policies():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, category, title, body FROM company_policies "
        "WHERE is_published=1 ORDER BY sort_order, id"
    )
    rows = [{"id": r[0], "category": r[1], "title": r[2], "body": r[3]} for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return rows


@policies_bp.route("/api/employee/policies")
@employee_required
def api_employee_policies():
    """Read-only, published-only list for the employee portal's Policies view."""
    return jsonify({"ok": True, "policies": _published_policies()})


# ── Bearer-token API (mobile app) ────────────────────────────────────────────
# The app's twins of the routes above: employees read the published
# policies, admin and HR tokens manage them (same roles as the web routes).

@policies_bp.route("/api/employee/company_policies")
@employee_api_required
def api_app_employee_policies():
    """Plus how to reach the company, for the screen's "Need help?" card."""
    from utils.helpers import get_company_settings
    co = get_company_settings()
    contact = {k: co.get(k) or "" for k in ("company_name", "contact_phone", "contact_email", "website")}
    return jsonify({"ok": True, "policies": _published_policies(), "contact": contact})


_TITLE_MAX = 200     # company_policies.title VARCHAR(200)
_CATEGORY_MAX = 50   # company_policies.category VARCHAR(50)
_BODY_MAX = 20000


def _json_policy_fields():
    """(category, title, body, error) from the request's JSON body."""
    data = request.get_json(silent=True) or {}
    category = str(data.get("category") or "").strip()
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    if not category or not title or not body:
        return None, None, None, "Category, title, and body are all required."
    if len(category) > _CATEGORY_MAX:
        return None, None, None, f"Category must be at most {_CATEGORY_MAX} characters."
    if len(title) > _TITLE_MAX:
        return None, None, None, f"Title must be at most {_TITLE_MAX} characters."
    if len(body) > _BODY_MAX:
        return None, None, None, f"Policy text must be at most {_BODY_MAX} characters."
    return category, title, body, None


def _all_policies(cursor):
    cursor.execute(
        "SELECT id, category, title, body, is_published, updated_at FROM company_policies "
        "ORDER BY sort_order, id"
    )
    return [
        {"id": r[0], "category": r[1], "title": r[2], "body": r[3], "is_published": bool(r[4]),
         "updated_at": r[5].isoformat() if r[5] else None}
        for r in cursor.fetchall()
    ]


@policies_bp.route("/api/admin/policies", methods=["GET"])
@api_required
@api_role_required("admin", HR_ROLE)
def api_admin_policies():
    """Every policy, drafts included, plus the built-in categories and the
    ready-made templates the web Policies tab offers."""
    from blueprints.company_profile import POLICY_CATEGORIES
    from migrations.column_migrations import _POLICY_SEED_CONTENT
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    policies = _all_policies(cursor)
    cursor.close()
    db.close()
    return jsonify({
        "ok": True, "policies": policies,
        "categories": [{"key": k, "label": v} for k, v in POLICY_CATEGORIES],
        "templates": [{"category": c, "title": t, "body": b} for c, t, b in _POLICY_SEED_CONTENT],
    })


@policies_bp.route("/api/admin/policies", methods=["POST"])
@api_required
@api_role_required("admin", HR_ROLE)
def api_admin_add_policy():
    category, title, body, error = _json_policy_fields()
    if error:
        return jsonify({"ok": False, "msg": error}), 400
    published = 1 if (request.get_json(silent=True) or {}).get("is_published", True) else 0
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM company_policies")
    next_order = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO company_policies (category, title, body, is_published, sort_order, created_by, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (category, title, body, published, next_order, g.api_user, g.api_user)
    )
    policy_id = cursor.fetchone()[0]
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "id": policy_id, "msg": "Policy added."})


def _api_update_policy(policy_id, sql, params, msg):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(sql, params)
    found = cursor.rowcount > 0
    db.commit()
    cursor.close()
    db.close()
    if not found:
        return jsonify({"ok": False, "msg": "Policy not found."}), 404
    return jsonify({"ok": True, "msg": msg})


@policies_bp.route("/api/admin/policies/<int:policy_id>", methods=["POST"])
@api_required
@api_role_required("admin", HR_ROLE)
def api_admin_edit_policy(policy_id):
    category, title, body, error = _json_policy_fields()
    if error:
        return jsonify({"ok": False, "msg": error}), 400
    return _api_update_policy(
        policy_id,
        "UPDATE company_policies SET category=%s, title=%s, body=%s, updated_by=%s WHERE id=%s",
        (category, title, body, g.api_user, policy_id), "Policy updated.")


@policies_bp.route("/api/admin/policies/<int:policy_id>/publish", methods=["POST"])
@api_required
@api_role_required("admin", HR_ROLE)
def api_admin_publish_policy(policy_id):
    return _api_update_policy(
        policy_id, "UPDATE company_policies SET is_published=1, updated_by=%s WHERE id=%s",
        (g.api_user, policy_id), "Policy published.")


@policies_bp.route("/api/admin/policies/<int:policy_id>/unpublish", methods=["POST"])
@api_required
@api_role_required("admin", HR_ROLE)
def api_admin_unpublish_policy(policy_id):
    return _api_update_policy(
        policy_id, "UPDATE company_policies SET is_published=0, updated_by=%s WHERE id=%s",
        (g.api_user, policy_id), "Policy unpublished.")


@policies_bp.route("/api/admin/policies/<int:policy_id>", methods=["DELETE"])
@api_required
@api_role_required("admin", HR_ROLE)
def api_admin_delete_policy(policy_id):
    return _api_update_policy(
        policy_id, "DELETE FROM company_policies WHERE id=%s", (policy_id,), "Policy deleted.")
