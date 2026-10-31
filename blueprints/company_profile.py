# -*- coding: utf-8 -*-
"""Company Admin "Organization" page (sidebar label; routes stay at /profile) -- tabs for
Organization Details, Policies, Payment History and Support Tickets. Anything
Settings already manages (work schedule, office location, billing, admin
email) lives there only, not duplicated here.

Every route is @role_required("admin") (same gate as /settings) and every
query resolves the tenant server-side from g.tenant_db -- never from a
client-supplied id -- via _resolve_own_tenant_id() below, mirroring the
existing reverse-direction pattern in blueprints/platform_admin.py's
_tenant_schema_for_id() and blueprints/org.py's _load_own_tenant_application().
"""
import datetime
import hashlib
import os
import re
import time
from urllib.parse import urlsplit

from flask import (
    Blueprint, request, session, redirect, render_template, flash, g, Response, current_app, send_file, jsonify,
)
from PIL import Image, UnidentifiedImageError

from database import get_db_connection, get_master_db
from utils.auth import role_required, api_required, api_role_required
from utils.helpers import (
    tpath, get_company_settings,
    TRIAL_EMPLOYEE_CAP, _tenant_is_trialing, invalidate_settings_cache,
    _validate_image_file, save_uploaded_logo,
)
from utils.payment_history import get_payment_history, get_payment_row
from utils.receipt_pdf import build_receipt_pdf
from utils.ticket_constants import TICKET_STATUSES, TICKET_PRIORITIES
from utils.csv_export import csv_response
from blueprints.org import _load_own_tenant_application
from migrations.column_migrations import _POLICY_SEED_CONTENT

company_profile_bp = Blueprint("company_profile", __name__)

_PAYMENTS_PAGE_SIZE = 20


def _resolve_own_tenant_id():
    """The logged-in admin's own tenant_id, resolved server-side from
    g.tenant_db -- never trust a client-supplied tenant/company id. Returns
    None if not found (shouldn't happen for a real logged-in admin, but
    every caller must handle it rather than assume)."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT id FROM tenants WHERE db_name=%s", (g.tenant_db,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


# ── Admin profile photo (top-banner avatar) ─────────────────────────────────
def admin_photo_path(username=None):
    """Where the logged-in admin's own profile photo lives: one JPEG per
    company + admin username, under UPLOAD_FOLDER/admin_photos. The name is
    a hash, so an email username never ends up in a file path. Also read by
    app.py's inject_admin_avatar() for the banner avatar."""
    username = username or session.get("admin_username") or ""
    key = hashlib.sha256(f"{g.tenant_db}:{username}".encode()).hexdigest()[:40]
    return os.path.join(current_app.config["UPLOAD_FOLDER"], "admin_photos", key + ".jpg")


@company_profile_bp.route("/profile/photo", methods=["GET"])
@role_required("admin")
def profile_photo():
    path = admin_photo_path()
    if not os.path.exists(path):
        return "", 404
    return send_file(path, mimetype="image/jpeg", max_age=0)


@company_profile_bp.route("/profile/photo", methods=["POST"])
@role_required("admin")
def profile_photo_upload():
    file = request.files.get("photo")
    ok, err = _validate_image_file(file)
    if not ok:
        flash(err, "error")
        return redirect(tpath("/profile"))
    try:
        img = Image.open(file.stream)
        img.thumbnail((400, 400))
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError):
        flash("That file isn't a readable image.", "error")
        return redirect(tpath("/profile"))
    path = admin_photo_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "JPEG", quality=88)
    flash("Profile photo updated.", "success")
    return redirect(tpath("/profile"))


@company_profile_bp.route("/profile/photo/remove", methods=["POST"])
@role_required("admin")
def profile_photo_remove():
    try:
        os.remove(admin_photo_path())
    except FileNotFoundError:
        pass
    flash("Profile photo removed.", "success")
    return redirect(tpath("/profile"))


# ── Organization details (web Overview + Contact panels, and the app) ───────
# What an admin can change about their company. Legal identifiers (GSTIN,
# PAN, CIN) stay out: they go through document verification instead.
ORG_FIELDS = ("company_name", "company_tagline", "contact_phone", "contact_email", "website", "address")
_ORG_MAX = {"company_name": 200, "company_tagline": 300, "contact_phone": 30,
            "contact_email": 255, "website": 255, "address": 500}
_ORG_LABELS = {"company_name": "Company name", "company_tagline": "Tagline", "contact_phone": "Phone",
               "contact_email": "Email", "website": "Website", "address": "Address"}
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{5,29}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")


def _clean_website(value):
    """'acme.com' -> 'https://acme.com'. Only http(s) links with a real
    host name -- the value is rendered as a link, so no javascript: etc.
    Returns (url, error)."""
    url = value if "://" in value else "https://" + value
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        host = ""
    if url.split("://", 1)[0].lower() not in ("http", "https") or not _HOST_RE.match(host):
        return None, "Enter a valid website, e.g. www.yourcompany.com."
    return url, None


def clean_org_fields(data):
    """Validate whichever ORG_FIELDS appear in `data` (a form or JSON dict).
    Fields left out are not touched; a blank value clears the field (except
    the company name, which is required). Returns (updates, error)."""
    updates = {}
    for field in ORG_FIELDS:
        if field not in data:
            continue
        value = str(data.get(field) or "").strip()
        if len(value) > _ORG_MAX[field]:
            return None, f"{_ORG_LABELS[field]} must be at most {_ORG_MAX[field]} characters."
        if field == "company_name" and len(value) < 2:
            return None, "Company name is required."
        if value and field == "contact_phone" and not _PHONE_RE.match(value):
            return None, "Enter a valid phone number, e.g. +91 98765 43210."
        if value and field == "contact_email" and not _EMAIL_RE.match(value):
            return None, "Enter a valid email address."
        if value and field == "website":
            value, error = _clean_website(value)
            if error:
                return None, error
        updates[field] = value or None
    return updates, None


def save_org_fields(updates):
    if not updates:
        return
    db = get_db_connection()
    cursor = db.cursor()
    # Column names come from ORG_FIELDS only, never from the request.
    cursor.execute(
        "UPDATE company_settings SET " + ", ".join(f"{col}=%s" for col in updates),
        tuple(updates.values()),
    )
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()


def _resolve_own_tenant_slug():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT subdomain FROM tenants WHERE db_name=%s", (g.tenant_db,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def save_org_logo(logo_file):
    """Store a new company logo; returns an error message or None. Each
    upload gets a fresh file name so browsers don't keep showing the old
    logo from cache (logo_url is also read as a file path, so it can't
    carry a ?v= query)."""
    slug = _resolve_own_tenant_slug() or g.tenant_db
    logo_path, error = save_uploaded_logo(logo_file, f"{slug}-{int(time.time())}")
    if error:
        return f"Company logo: {error}"
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("UPDATE company_settings SET logo_url=%s", (f"/static/{logo_path}",))
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    return None


def org_details():
    """The company's editable details, as the app's Organization screen shows them."""
    co = get_company_settings()
    details = {field: co.get(field) or "" for field in ORG_FIELDS}
    details["company_code"] = co.get("company_code") or ""
    details["logo_url"] = co.get("logo_url") or ""
    return details


# ── Company Info tab ─────────────────────────────────────────────────────────
@company_profile_bp.route("/profile", methods=["GET"])
@role_required("admin")
def profile_company_info():
    co = get_company_settings()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT COUNT(DISTINCT department) FROM employees "
        "WHERE department IS NOT NULL AND department <> ''"
    )
    department_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM company_policies WHERE is_published=1")
    published_policy_count = cursor.fetchone()[0]
    cursor.close()
    db.close()

    application = _load_own_tenant_application()
    seat_cap = TRIAL_EMPLOYEE_CAP if _tenant_is_trialing() else co.get("paid_employee_slots")

    return render_template(
        "profile.html", active_nav="profile",
        # Overview / Legal / Contact share this route; the side menu picks one.
        active_tab=request.args.get("section") if request.args.get("section") in ("legal", "contact") else "company",
        co=co, application=application, seat_cap=seat_cap,
        department_count=department_count, published_policy_count=published_policy_count,
    )


@company_profile_bp.route("/profile/company_info", methods=["POST"])
@role_required("admin")
def profile_update_company_info():
    """The Contact panel: phone, email, website, address."""
    contact_keys = ("contact_phone", "contact_email", "website", "address")
    updates, error = clean_org_fields({k: request.form[k] for k in contact_keys if k in request.form})
    if error:
        flash(error, "error")
        return redirect(tpath("/profile?section=contact"))
    save_org_fields(updates)
    flash("Company contact details updated.", "success")
    return redirect(tpath("/profile?section=contact"))


@company_profile_bp.route("/profile/organization", methods=["POST"])
@role_required("admin")
def profile_update_organization():
    """The Overview's Organization details panel: name, tagline, logo."""
    updates, error = clean_org_fields(
        {k: request.form[k] for k in ("company_name", "company_tagline") if k in request.form})
    logo = request.files.get("logo")
    if not error and logo and logo.filename:
        error = save_org_logo(logo)
    if error:
        flash(error, "error")
        return redirect(tpath("/profile"))
    save_org_fields(updates)
    flash("Organization details updated.", "success")
    return redirect(tpath("/profile"))


@company_profile_bp.route("/api/admin/organization", methods=["GET"])
@api_required
@api_role_required("admin")
def api_admin_organization():
    application = _load_own_tenant_application()
    return jsonify({
        "ok": True, "organization": org_details(),
        "gst_number": (application or {}).get("gst_number") or "",
        "verification": (application or {}).get("doc_review_status") or "",
    })


@company_profile_bp.route("/api/admin/organization", methods=["POST"])
@api_required
@api_role_required("admin")
def api_admin_update_organization():
    """JSON (details only) or multipart (details plus an optional `logo` file)."""
    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
    updates, error = clean_org_fields(data)
    logo = request.files.get("logo")
    if not error and logo and logo.filename:
        error = save_org_logo(logo)
    if error:
        return jsonify({"ok": False, "msg": error}), 400
    save_org_fields(updates)
    return jsonify({"ok": True, "msg": "Organization details saved.", "organization": org_details()})


# ── Policies tab ─────────────────────────────────────────────────────────────
# Add/edit/publish/delete post to blueprints/policies.py's routes, which
# redirect admin sessions back here.
POLICY_CATEGORIES = [
    ("terms", "Terms & Conditions"),
    ("rules", "Rules & Regulations"),
    ("limitations", "Limitations"),
    ("instructions", "Instructions"),
    ("posh", "POSH Policy"),
    ("resignation", "Resignation Policy"),
]


@company_profile_bp.route("/profile/policies", methods=["GET"])
@role_required("admin")
def profile_policies():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, category, title, body, is_published, updated_at FROM company_policies "
        "ORDER BY sort_order, id"
    )
    policies = [dict(zip(["id", "category", "title", "body", "is_published", "updated_at"], r))
                for r in cursor.fetchall()]
    cursor.close()
    db.close()
    return render_template(
        "profile.html", active_nav="profile", active_tab="policies",
        policies=policies, policy_categories=POLICY_CATEGORIES,
        # Ready-made starting points for the Add Policy form -- new companies
        # no longer get these seeded, so they're offered here instead.
        policy_templates=[{"category": c, "title": t, "body": b} for c, t, b in _POLICY_SEED_CONTENT],
    )


# ── Subscription & Billing ───────────────────────────────────────────────────
# Lives in Settings -> Billing; this old tab URL just forwards there so
# existing links and bookmarks keep working.
@company_profile_bp.route("/profile/billing", methods=["GET"])
@role_required("admin")
def profile_billing():
    return redirect(tpath("/settings?tab=billing"))


# ── Payment History tab ──────────────────────────────────────────────────────
def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _payments_filters():
    status = request.args.get("status") or None
    if status not in (None, "success", "pending", "failed"):
        status = None
    return {
        "status": status,
        "date_from": _parse_date(request.args.get("from")),
        "date_to": _parse_date(request.args.get("to")),
    }


@company_profile_bp.route("/profile/payments", methods=["GET"])
@role_required("admin")
def profile_payments():
    tenant_id = _resolve_own_tenant_id()
    filters = _payments_filters()
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * _PAYMENTS_PAGE_SIZE
    result = get_payment_history(
        tenant_id=tenant_id, tenant_schema=g.tenant_db,
        limit=_PAYMENTS_PAGE_SIZE, offset=offset, **filters,
    )
    total_pages = max(1, (result["total"] + _PAYMENTS_PAGE_SIZE - 1) // _PAYMENTS_PAGE_SIZE)
    return render_template(
        "profile.html", active_nav="profile", active_tab="payments",
        co=get_company_settings(), payments=result["payments"], total_payments=result["total"],
        page=page, total_pages=total_pages, filters=filters,
    )


@company_profile_bp.route("/profile/payments/export.csv", methods=["GET"])
@role_required("admin")
def profile_payments_export_csv():
    tenant_id = _resolve_own_tenant_id()
    filters = _payments_filters()
    result = get_payment_history(tenant_id=tenant_id, tenant_schema=g.tenant_db, **filters)
    rows = [
        [p["kind"], p["order_id"] or p["payment_id"] or "—", p["employee_count"],
         p["amount_display"], p["status"],
         (p["paid_at"] or p["created_at"]).strftime("%Y-%m-%d %H:%M") if (p["paid_at"] or p["created_at"]) else ""]
        for p in result["payments"]
    ]
    return csv_response(
        "payment_history.csv",
        ["Type", "Reference ID", "Employees Billed", "Amount", "Status", "Date"],
        rows,
    )


@company_profile_bp.route("/profile/payments/<kind_key>/<int:row_id>/receipt.pdf", methods=["GET"])
@role_required("admin")
def profile_payment_receipt(kind_key, row_id):
    tenant_id = _resolve_own_tenant_id()
    row = get_payment_row(kind_key, row_id, tenant_id=tenant_id, tenant_schema=g.tenant_db)
    if not row:
        return "Receipt not found.", 404
    kind_label = {"signup": "Signup Payment", "seat_topup": "Seat Top-up",
                  "invoice": "Monthly Auto-Debit Charge"}.get(kind_key, "Payment")
    pdf_bytes = build_receipt_pdf(
        row["company_name"], row["amount_paise"], row["paid_at"], row["reference_id"], kind_label,
    )
    resp = Response(pdf_bytes, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="receipt_{kind_key}_{row_id}.pdf"'
    return resp


# ── Support Tickets tab ──────────────────────────────────────────────────────
@company_profile_bp.route("/profile/tickets", methods=["GET"])
@role_required("admin")
def profile_tickets():
    tenant_id = _resolve_own_tenant_id()
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    tickets = []
    if tenant_id is not None:
        cur.execute(
            "SELECT id, subject, priority, status, created_at, updated_at, linked_ticket_id "
            "FROM tenant_support_tickets WHERE tenant_id=%s ORDER BY created_at DESC",
            (tenant_id,)
        )
        cols = ["id", "subject", "priority", "status", "created_at", "updated_at", "linked_ticket_id"]
        tickets = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return render_template(
        "profile.html", active_nav="profile", active_tab="tickets",
        co=get_company_settings(), tickets=tickets,
        ticket_statuses=TICKET_STATUSES, ticket_priorities=TICKET_PRIORITIES,
    )


@company_profile_bp.route("/profile/tickets", methods=["POST"])
@role_required("admin")
def profile_create_ticket():
    tenant_id = _resolve_own_tenant_id()
    if tenant_id is None:
        flash("Could not resolve your company account.", "error")
        return redirect(tpath("/profile/tickets"))

    subject = (request.form.get("subject") or "").strip()[:200]
    description = (request.form.get("description") or "").strip()[:4000]
    priority = request.form.get("priority") or "normal"
    if priority not in TICKET_PRIORITIES:
        priority = "normal"
    if not subject or not description:
        flash("Subject and description are required.", "error")
        return redirect(tpath("/profile/tickets"))

    linked_ticket_id = request.form.get("linked_ticket_id", type=int)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    if linked_ticket_id is not None:
        # A linked ticket must belong to this same tenant -- never trust the
        # id alone, since a client could type any integer.
        cur.execute("SELECT 1 FROM tenant_support_tickets WHERE id=%s AND tenant_id=%s",
                    (linked_ticket_id, tenant_id))
        if not cur.fetchone():
            linked_ticket_id = None

    cur.execute(
        "INSERT INTO tenant_support_tickets (tenant_id, subject, description, priority, created_by, linked_ticket_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (tenant_id, subject, description, priority, session.get("admin_username"), linked_ticket_id),
    )
    conn.commit()
    cur.close()
    conn.close()
    flash("Support ticket raised.", "success")
    return redirect(tpath("/profile/tickets"))


def _fetch_own_ticket(ticket_id, tenant_id):
    """Returns the ticket row + its comments, or None if it doesn't exist OR
    doesn't belong to this tenant -- both cases are treated identically by
    the caller (a 404, not a 403), so a guessed id doesn't even confirm it
    exists."""
    if tenant_id is None:
        return None
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "SELECT id, subject, description, priority, status, created_at, updated_at, linked_ticket_id "
        "FROM tenant_support_tickets WHERE id=%s AND tenant_id=%s",
        (ticket_id, tenant_id)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return None
    ticket = dict(zip(
        ["id", "subject", "description", "priority", "status", "created_at", "updated_at", "linked_ticket_id"],
        row,
    ))
    cur.execute(
        "SELECT author, comment, author_kind, created_at FROM tenant_ticket_comments "
        "WHERE ticket_id=%s ORDER BY created_at ASC",
        (ticket_id,)
    )
    ticket["comments"] = [
        dict(zip(["author", "comment", "author_kind", "created_at"], r)) for r in cur.fetchall()
    ]
    cur.close()
    conn.close()
    return ticket


@company_profile_bp.route("/profile/tickets/<int:ticket_id>", methods=["GET"])
@role_required("admin")
def profile_ticket_detail(ticket_id):
    tenant_id = _resolve_own_tenant_id()
    ticket = _fetch_own_ticket(ticket_id, tenant_id)
    if not ticket:
        return "Ticket not found.", 404
    return render_template("profile_ticket_detail.html", active_nav="profile", ticket=ticket,
                           co=get_company_settings())


@company_profile_bp.route("/profile/tickets/<int:ticket_id>/reply", methods=["POST"])
@role_required("admin")
def profile_reply_ticket(ticket_id):
    tenant_id = _resolve_own_tenant_id()
    ticket = _fetch_own_ticket(ticket_id, tenant_id)
    if not ticket:
        return "Ticket not found.", 404

    comment = (request.form.get("comment") or "").strip()[:2000]
    if not comment:
        flash("Reply cannot be empty.", "error")
        return redirect(tpath(f"/profile/tickets/{ticket_id}"))
    if ticket["status"] == "closed":
        flash("This ticket is closed. Raise a new ticket linked to it instead.", "error")
        return redirect(tpath(f"/profile/tickets/{ticket_id}"))

    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute(
        "INSERT INTO tenant_ticket_comments (ticket_id, author, comment, author_kind) VALUES (%s,%s,%s,'company_admin')",
        (ticket_id, session.get("admin_username"), comment),
    )
    cur.execute("UPDATE tenant_support_tickets SET updated_at=NOW() WHERE id=%s", (ticket_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("Reply sent.", "success")
    return redirect(tpath(f"/profile/tickets/{ticket_id}"))
