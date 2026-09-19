# -*- coding: utf-8 -*-
"""Company Admin "Profile / Account" page -- four tabs (Company Info,
Subscription & Billing, Payment History, Support Tickets) giving a company
admin one place to see their own subscription status, full payment history,
and support-ticket history, none of which had a single home before this.

Every route is @role_required("admin") (same gate as /settings) and every
query resolves the tenant server-side from g.tenant_db -- never from a
client-supplied id -- via _resolve_own_tenant_id() below, mirroring the
existing reverse-direction pattern in blueprints/platform_admin.py's
_tenant_schema_for_id() and blueprints/org.py's _load_own_tenant_application().
"""
import datetime

from flask import Blueprint, request, session, redirect, render_template, flash, g, Response

from database import get_db_connection, get_master_db
from utils.auth import role_required
from utils.helpers import (
    tpath, get_company_settings, coerce_datetime,
    TRIAL_EMPLOYEE_CAP, _tenant_is_trialing, invalidate_settings_cache,
)
from utils.plan_limits import get_billing_snapshot, format_price_inr
from utils.payment_history import get_payment_history, get_payment_row
from utils.receipt_pdf import build_receipt_pdf
from utils.ticket_constants import TICKET_STATUSES, TICKET_PRIORITIES
from utils.csv_export import csv_response
from blueprints.org import _load_own_tenant_application

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


# ── Company Info tab ─────────────────────────────────────────────────────────
@company_profile_bp.route("/profile", methods=["GET"])
@role_required("admin")
def profile_company_info():
    co = get_company_settings()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT email FROM admin_users WHERE username=%s", (session.get("admin_username"),))
    _admin_row = cursor.fetchone()
    admin_recovery_email = _admin_row[0] if _admin_row else ""
    cursor.close()
    db.close()

    application = _load_own_tenant_application()
    seat_cap = TRIAL_EMPLOYEE_CAP if _tenant_is_trialing() else co.get("paid_employee_slots")

    return render_template(
        "profile.html", active_nav="profile", active_tab="company",
        co=co, admin_recovery_email=admin_recovery_email,
        application=application, seat_cap=seat_cap,
        email_ok=request.args.get("email_ok") == "1",
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
    return redirect(tpath("/profile"))


# ── Subscription & Billing tab (read-only) ───────────────────────────────────
@company_profile_bp.route("/profile/billing", methods=["GET"])
@role_required("admin")
def profile_billing():
    snapshot = get_billing_snapshot(g.tenant_db)
    invoices = [
        {**inv, "amount_display": format_price_inr(inv["amount_paise"]),
         "rate_display": format_price_inr(inv["rate_paise"]) if inv["rate_paise"] else None}
        for inv in snapshot["invoices"]
    ]

    mconn = get_master_db()
    mcur = mconn.cursor(buffered=True)
    mcur.execute("SELECT trial_end_date, subscription_status FROM tenants WHERE db_name=%s", (g.tenant_db,))
    row = mcur.fetchone()
    mcur.close()
    mconn.close()
    trial_end_date = coerce_datetime(row[0]) if row else None
    subscription_status = row[1] if row else "active"
    trial_days_remaining = None
    if subscription_status == "trialing" and trial_end_date:
        trial_days_remaining = max(0, (trial_end_date - datetime.datetime.utcnow()).days)

    return render_template(
        "profile.html", active_nav="profile", active_tab="billing",
        co=get_company_settings(), auto_debit_status=snapshot["auto_debit"], invoices=invoices,
        trial_end_date=trial_end_date, subscription_status=subscription_status,
        trial_days_remaining=trial_days_remaining, employee_seat_cap=(
            TRIAL_EMPLOYEE_CAP if subscription_status == "trialing" else get_company_settings().get("paid_employee_slots")
        ),
    )


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
