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

from flask import Blueprint, request, session, redirect, render_template, flash, g, Response, current_app, send_file
from PIL import Image, UnidentifiedImageError

from database import get_db_connection, get_master_db
from utils.auth import role_required
from utils.helpers import (
    tpath, get_company_settings,
    TRIAL_EMPLOYEE_CAP, _tenant_is_trialing, invalidate_settings_cache,
    _validate_image_file, save_uploaded_logo,
)
from utils.payment_history import get_payment_history, get_payment_row
from utils.receipt_pdf import build_receipt_pdf
from utils.ticket_constants import TICKET_STATUSES, TICKET_PRIORITIES
from utils.csv_export import csv_response
from blueprints.org import _EMAIL_RE, _MOBILE_RE, _clean_mobile, _load_own_tenant_application
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
# Choices for the Organization profile form; the form and the save both use them.
INDUSTRIES = (
    "Information Technology", "Manufacturing", "Healthcare", "Education", "Finance & Banking", "Retail & E-commerce",
    "Construction & Real Estate", "Hospitality", "Logistics & Transport", "Media & Marketing",
    "Professional Services", "Government & Non-profit", "Other",
)
COMPANY_SIZES = ("1-10", "11-50", "51-200", "201-500", "501-1000", "1000+")
_POSTAL_RE = re.compile(r"[A-Za-z0-9 -]{3,12}")
_WEBSITE_RE = re.compile(r"(https?://)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(/\S*)?")


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


@company_profile_bp.route("/welcome", methods=["GET"])
@role_required("admin")
def company_welcome():
    """The loader a new company's admin sees right after registering, on the
    way to the Organization page."""
    return render_template("company_welcome.html", next_url=tpath("/profile"))


def _store_logo(logo_file):
    """Saves an uploaded company logo: (logo_url, error). The saved filename
    is fixed per company; ?v= stops browsers showing the old logo."""
    logo_path, err = save_uploaded_logo(logo_file, getattr(g, "tenant_slug", None) or g.tenant_db)
    if err:
        return None, err
    return f"/static/{logo_path}?v={int(time.time())}", None


@company_profile_bp.route("/profile/logo", methods=["POST"])
@role_required("admin")
def profile_logo_upload():
    """The Change logo button in the Organization page's header."""
    logo_file = request.files.get("logo")
    if not logo_file or not logo_file.filename:
        flash("Please choose a logo file.", "error")
        return redirect(tpath("/profile"))
    logo_url, err = _store_logo(logo_file)
    if err:
        flash(f"Company logo: {err}", "error")
        return redirect(tpath("/profile"))
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("UPDATE company_settings SET logo_url=%s", (logo_url,))
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    flash("Company logo updated.", "success")
    return redirect(tpath("/profile"))


@company_profile_bp.route("/profile/logo/remove", methods=["POST"])
@role_required("admin")
def profile_logo_remove():
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("UPDATE company_settings SET logo_url=NULL")
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    flash("Company logo removed.", "success")
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


@company_profile_bp.route("/profile/details", methods=["POST"])
@role_required("admin")
def profile_details_save():
    """The Organization page's one form: company details, office address and
    the admin's own details. The address's city, state, PIN and country, the
    organization code, website, logo and photo are optional; everything else
    is mandatory. All of it stays editable: a
    file field left empty keeps what's already saved. The code prefixes new
    employee IDs only; existing IDs keep theirs. Everything is checked before anything is written, so a bad
    file can't leave the form half saved."""
    code = (request.form.get("company_code") or "").strip().upper()
    name = " ".join((request.form.get("display_name") or "").split())[:100]
    text = {key: " ".join((request.form.get(key) or "").split())[:limit] for key, limit in (
        ("job_title", 100), ("address", 500), ("city", 100), ("state", 100), ("postal_code", 12), ("country", 100),
        ("website", 200), ("email", 150),
    )}
    mobile = _clean_mobile(request.form.get("mobile"))
    industry = request.form.get("industry")
    company_size = request.form.get("company_size")
    logo_file = request.files.get("logo")
    photo_file = request.files.get("photo")
    was_pending = get_company_settings().get("org_profile_pending")
    has_logo = logo_file and logo_file.filename
    has_photo = photo_file and photo_file.filename
    if code and not re.fullmatch(r"[A-Z0-9]{2,10}", code):
        flash("Organization code must be 2 to 10 letters or numbers.", "error")
        return redirect(tpath("/profile"))
    if not name:
        flash("Please enter your name.", "error")
        return redirect(tpath("/profile"))
    error = None
    if industry not in INDUSTRIES:
        error = "Please select your industry."
    elif company_size not in COMPANY_SIZES:
        error = "Please select your company size."
    elif text["website"] and not _WEBSITE_RE.fullmatch(text["website"]):
        error = "Please enter a valid website address."
    elif not text["address"]:
        error = "Please enter your office address."
    elif text["postal_code"] and not _POSTAL_RE.fullmatch(text["postal_code"]):
        error = "Please enter a valid PIN or postal code."
    elif not text["job_title"]:
        error = "Please enter your job title."
    elif not _EMAIL_RE.match(text["email"]):
        error = "Please enter a valid work email."
    elif not _MOBILE_RE.match(mobile):
        error = "Please enter a valid mobile number."
    if error:
        flash(error, "error")
        return redirect(tpath("/profile"))
    photo_path = admin_photo_path()

    img = None
    if has_photo:
        ok, err = _validate_image_file(photo_file)
        if not ok:
            flash(f"Profile photo: {err}", "error")
            return redirect(tpath("/profile"))
        try:
            img = Image.open(photo_file.stream)
            img.thumbnail((400, 400))
            img = img.convert("RGB")
        except (UnidentifiedImageError, OSError):
            flash("Profile photo: that file isn't a readable image.", "error")
            return redirect(tpath("/profile"))

    logo_url = None
    if has_logo:
        logo_url, err = _store_logo(logo_file)
        if err:
            flash(f"Company logo: {err}", "error")
            return redirect(tpath("/profile"))

    if img:
        os.makedirs(os.path.dirname(photo_path), exist_ok=True)
        img.save(photo_path, "JPEG", quality=88)
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("UPDATE admin_users SET display_name=%s, job_title=%s, email=%s, mobile=%s WHERE username=%s",
                   (name, text["job_title"], text["email"], mobile, session.get("admin_username")))
    # Every mandatory field is in place by here, so this unlocks the dashboard
    # for a new company (app.py's _enforce_org_profile).
    cursor.execute(
        "UPDATE company_settings SET company_code=%s, industry=%s, company_size=%s, website=%s, address=%s, city=%s, "
        "state=%s, postal_code=%s, country=%s, org_profile_pending=0",
        (code or None, industry, company_size, text["website"] or None, text["address"], text["city"] or None,
         text["state"] or None, text["postal_code"] or None, text["country"] or None),
    )
    if logo_url:
        cursor.execute("UPDATE company_settings SET logo_url=%s", (logo_url,))
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    flash("Organization profile saved.", "success")
    return redirect(tpath("/admin" if was_pending else "/profile"))


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
    cursor.execute("SELECT job_title, email, mobile FROM admin_users WHERE username=%s",
                   (session.get("admin_username"),))
    job_title, admin_email, admin_mobile = cursor.fetchone() or (None, None, None)
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
        industries=INDUSTRIES, company_sizes=COMPANY_SIZES,
        # Signup already asked for the email and mobile, so a new admin
        # starts with them filled in.
        admin_job_title=job_title or "", admin_email=admin_email or "",
        admin_mobile=admin_mobile or co.get("contact_phone") or "",
    )


@company_profile_bp.route("/profile/company_info", methods=["POST"])
@role_required("admin")
def profile_update_company_info():
    contact_phone = (request.form.get("contact_phone") or "").strip()[:30]
    address = (request.form.get("address") or "").strip()[:500]
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE company_settings SET contact_phone=%s, address=%s",
        (contact_phone or None, address or None),
    )
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    flash("Company contact details updated.", "success")
    return redirect(tpath("/profile?section=contact"))


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
