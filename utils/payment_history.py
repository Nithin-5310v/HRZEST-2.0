"""Shared "all payments this tenant has ever made" feed.

Generalized out of blueprints/platform_admin.py's _recent_payments() so the
Company Admin Profile page's Payment History tab and the Platform Admin's
per-company payment history use the exact same merge/sort logic instead of
drifting apart. platform_admin.py's own call sites now call get_payment_history()
directly (see its own docstring there for why three separate source tables
exist in the first place -- signup checkout, seat top-ups, recurring auto-debit).

Every row also carries its own `id`/`kind_key` (not present on the original
_recent_payments() output, which only needed enough to *display* a row, never
to look one back up) so a single payment can be re-fetched later -- e.g. for
a PDF receipt -- without trusting anything else about it from the client.
"""
import datetime

from database import get_master_db
from utils.plan_limits import format_price_inr
from utils.helpers import coerce_datetime
from extensions import app_log

# Raw per-table status strings, normalized to one shared vocabulary so a
# caller can filter by "success"/"pending"/"failed" without knowing that
# payment_orders alone has a fourth ("provisioned") terminal-success state.
_STATUS_NORMALIZE = {
    "created": "pending", "pending": "pending",
    "paid": "success", "provisioned": "success",
    "failed": "failed",
}


def _normalize_status(raw_status):
    return _STATUS_NORMALIZE.get(raw_status, raw_status)


def get_payment_history(tenant_id=None, tenant_schema=None, status=None,
                        date_from=None, date_to=None, limit=None, offset=0):
    """Merged, sorted (newest first) feed of every payment made by one
    tenant (or, with both keys left None, across every tenant -- the
    platform-admin-wide view). Each row keeps its own table's raw `status`
    string (existing templates already branch on 'paid'/'provisioned' etc.
    -- left untouched for backward compatibility) plus a normalized
    `status_group` ('success'/'pending'/'failed'). `status` (the parameter)
    filters on `status_group`, applied after merging since the three source
    tables don't share raw status strings. `date_from`/
    `date_to` (date objects, inclusive) filter on created_at at the SQL
    level per source table. `limit`/`offset` paginate the final merged
    list; `limit=None` returns everything matching the filters.

    Returns {"payments": [...], "total": N} -- `total` is the filtered
    count *before* pagination, for building a pager. Fails soft (empty
    list, total=0) on any DB error, matching _recent_payments()'s posture.
    """
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)

        date_clause = ""
        date_params = []
        if date_from is not None:
            date_clause += " AND created_at >= %s"
            date_params.append(date_from)
        if date_to is not None:
            date_clause += " AND created_at < %s"
            date_params.append(date_to + datetime.timedelta(days=1))

        # A table can contribute at most this many of the final top-N rows
        # once merged -- capping each per-table SELECT at it (like the
        # original _recent_payments() capped each at plain `limit`) avoids
        # a full-table scan on the platform-wide (no tenant_id/tenant_schema)
        # dashboard feed. Only applied in that unscoped case: a single
        # tenant's own history is small enough (at most a few hundred rows
        # even after years of monthly invoices) that fetching it all keeps
        # `total` exact for the paginated Payment History tab, rather than
        # under-counting whatever a cap would have left out.
        is_unscoped = tenant_id is None and tenant_schema is None
        row_cap = offset + limit if (is_unscoped and limit is not None and status is None) else None
        cap_clause = "" if row_cap is None else " LIMIT %s"
        cap_param = () if row_cap is None else (row_cap,)

        # -- Signup checkout orders (payment_orders, keyed by tenant_id) --
        if tenant_id is not None:
            cur.execute(
                "SELECT id, company_name, subdomain, employee_count, amount_paise, status, "
                "razorpay_order_id, razorpay_payment_id, created_at, paid_at, "
                "admin_username, admin_email "
                "FROM payment_orders WHERE tenant_id=%s" + date_clause + " ORDER BY created_at DESC" + cap_clause,
                (tenant_id, *date_params, *cap_param)
            )
        elif tenant_schema is None and tenant_id is None:
            cur.execute(
                "SELECT id, company_name, subdomain, employee_count, amount_paise, status, "
                "razorpay_order_id, razorpay_payment_id, created_at, paid_at, "
                "admin_username, admin_email "
                "FROM payment_orders WHERE 1=1" + date_clause + " ORDER BY created_at DESC" + cap_clause,
                tuple(date_params) + cap_param
            )
        else:
            cur.execute("SELECT 1 WHERE FALSE")  # tenant_schema given but no tenant_id: no signup rows to show
        signup_payments = [
            {
                "id": r[0], "kind_key": "signup", "kind": "Signup",
                "company_name": r[1], "subdomain": r[2], "employee_count": r[3],
                "amount_paise": r[4], "amount_display": format_price_inr(r[4]),
                "status": r[5], "status_group": _normalize_status(r[5]),
                "is_demo": (r[6] or "").startswith("demo_order_"),
                "order_id": r[6], "payment_id": r[7], "created_at": r[8], "paid_at": r[9],
                "admin_username": r[10], "admin_email": r[11],
            }
            for r in cur.fetchall()
        ]

        # -- Seat top-up orders (seat_topup_orders, keyed by tenant_schema) --
        if tenant_schema is not None:
            cur.execute(
                "SELECT s.id, t.company_name, t.subdomain, s.seats_purchased, s.amount_paise, s.status, "
                "s.razorpay_order_id, s.razorpay_payment_id, s.created_at, s.paid_at, s.requested_by, "
                "s.company_name AS fallback_company_name "
                "FROM seat_topup_orders s LEFT JOIN tenants t ON t.db_name = s.tenant_schema "
                "WHERE s.tenant_schema=%s" + date_clause.replace("created_at", "s.created_at") +
                " ORDER BY s.created_at DESC" + cap_clause,
                (tenant_schema, *date_params, *cap_param)
            )
        elif tenant_id is None:
            cur.execute(
                "SELECT s.id, t.company_name, t.subdomain, s.seats_purchased, s.amount_paise, s.status, "
                "s.razorpay_order_id, s.razorpay_payment_id, s.created_at, s.paid_at, s.requested_by, "
                "s.company_name AS fallback_company_name "
                "FROM seat_topup_orders s LEFT JOIN tenants t ON t.db_name = s.tenant_schema "
                "WHERE 1=1" + date_clause.replace("created_at", "s.created_at") + " ORDER BY s.created_at DESC" + cap_clause,
                tuple(date_params) + cap_param
            )
        else:
            cur.execute("SELECT 1 WHERE FALSE")
        seat_payments = [
            {
                "id": r[0], "kind_key": "seat_topup", "kind": "Seat Top-up",
                "company_name": r[1] or r[11], "subdomain": r[2] or "—", "employee_count": r[3],
                "amount_paise": r[4], "amount_display": format_price_inr(r[4]),
                "status": r[5], "status_group": _normalize_status(r[5]),
                "is_demo": (r[6] or "").startswith("demo_seat_order_"),
                "order_id": r[6], "payment_id": r[7], "created_at": r[8], "paid_at": r[9],
                "admin_username": r[10], "admin_email": "—",
            }
            for r in cur.fetchall()
        ]

        # -- Recurring / dunning charges (monthly_invoices, keyed by tenant_schema) --
        if tenant_schema is not None:
            cur.execute(
                "SELECT m.id, t.company_name, t.subdomain, m.employee_count, m.amount_paise, m.status, "
                "m.razorpay_subscription_id, m.razorpay_payment_id, m.created_at, m.company_name AS fallback_company_name "
                "FROM monthly_invoices m LEFT JOIN tenants t ON t.db_name = m.tenant_schema "
                "WHERE m.tenant_schema=%s" + date_clause.replace("created_at", "m.created_at") +
                " ORDER BY m.created_at DESC" + cap_clause,
                (tenant_schema, *date_params, *cap_param)
            )
        elif tenant_id is None:
            cur.execute(
                "SELECT m.id, t.company_name, t.subdomain, m.employee_count, m.amount_paise, m.status, "
                "m.razorpay_subscription_id, m.razorpay_payment_id, m.created_at, m.company_name AS fallback_company_name "
                "FROM monthly_invoices m LEFT JOIN tenants t ON t.db_name = m.tenant_schema "
                "WHERE 1=1" + date_clause.replace("created_at", "m.created_at") + " ORDER BY m.created_at DESC" + cap_clause,
                tuple(date_params) + cap_param
            )
        else:
            cur.execute("SELECT 1 WHERE FALSE")
        invoice_payments = [
            {
                "id": r[0], "kind_key": "invoice", "kind": "Auto-Debit",
                "company_name": r[1] or r[9], "subdomain": r[2] or "—", "employee_count": r[3],
                "amount_paise": r[4], "amount_display": format_price_inr(r[4]),
                "status": r[5], "status_group": _normalize_status(r[5]),
                "is_demo": (r[6] or "").startswith("demo_sub_"),
                "order_id": r[6], "payment_id": r[7], "created_at": r[8], "paid_at": r[8],
                "admin_username": "—", "admin_email": "—",
            }
            for r in cur.fetchall()
        ]
        cur.close()
        conn.close()

        merged = signup_payments + seat_payments + invoice_payments
        if status is not None:
            merged = [p for p in merged if p["status_group"] == status]
        merged.sort(
            key=lambda p: coerce_datetime(p["paid_at"]) or coerce_datetime(p["created_at"]) or datetime.datetime.min,
            reverse=True,
        )
        total = len(merged)
        if limit is not None:
            merged = merged[offset:offset + limit]
        elif offset:
            merged = merged[offset:]
        return {"payments": merged, "total": total}
    except Exception as exc:
        app_log.warning("get_payment_history: lookup failed (tenant_id=%s, tenant_schema=%s): %s",
                        tenant_id, tenant_schema, exc)
        return {"payments": [], "total": 0}


def get_payment_row(kind_key, row_id, tenant_id=None, tenant_schema=None):
    """Re-fetch exactly one payment row fresh from its source table, scoped
    to the given tenant -- used by the PDF-receipt route, which must never
    trust amount/date/company_name arriving from the client's URL. Returns
    None if the row doesn't exist OR doesn't belong to this tenant (the
    caller should treat both cases identically -- a 404, not a 403, so a
    guess at another tenant's row id doesn't even confirm it exists)."""
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        if kind_key == "signup":
            cur.execute(
                "SELECT company_name, amount_paise, COALESCE(paid_at, created_at), "
                "COALESCE(razorpay_payment_id, razorpay_order_id) "
                "FROM payment_orders WHERE id=%s AND tenant_id=%s",
                (row_id, tenant_id)
            )
        elif kind_key == "seat_topup":
            cur.execute(
                "SELECT t.company_name, s.amount_paise, COALESCE(s.paid_at, s.created_at), "
                "COALESCE(s.razorpay_payment_id, s.razorpay_order_id) "
                "FROM seat_topup_orders s LEFT JOIN tenants t ON t.db_name = s.tenant_schema "
                "WHERE s.id=%s AND s.tenant_schema=%s",
                (row_id, tenant_schema)
            )
        elif kind_key == "invoice":
            cur.execute(
                "SELECT t.company_name, m.amount_paise, m.created_at, "
                "COALESCE(m.razorpay_payment_id, m.razorpay_order_id, m.razorpay_subscription_id) "
                "FROM monthly_invoices m LEFT JOIN tenants t ON t.db_name = m.tenant_schema "
                "WHERE m.id=%s AND m.tenant_schema=%s",
                (row_id, tenant_schema)
            )
        else:
            cur.close()
            conn.close()
            return None
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        return {
            "company_name": row[0] or "Company", "amount_paise": row[1],
            "paid_at": coerce_datetime(row[2]), "reference_id": row[3] or "—",
        }
    except Exception as exc:
        app_log.warning("get_payment_row(%s, %s): lookup failed: %s", kind_key, row_id, exc)
        return None
