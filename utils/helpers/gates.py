# -*- coding: utf-8 -*-
"""helpers: employee-registration gates: email domain, trial cap, paid seat cap"""
import re
from database import get_db_connection
from .settings import get_company_settings


# ── Company email domain (employee-registration gate) ────────────────────────
_DOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$')


def clean_email_domain(raw: str) -> str:
    """Normalize a user-entered company domain -- strips a leading
    scheme/"www.", everything up to an "@", any path, and a trailing dot,
    and lowercases it, so "https://Acme.com/", "admin@acme.com" and
    "acme.com" all land on "acme.com". A full email address is the
    common mistake on the Platform Admin form, where this field sits
    next to "Admin Email" and browsers autofill an email into it."""
    s = (raw or "").strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = s.rsplit('@', 1)[-1]
    s = s.split('/')[0].split(':')[0].rstrip('.')
    if s.startswith('www.'):
        s = s[4:]
    return s


def validate_email_domain_format(domain: str) -> str:
    """Returns an error message, or None if the domain string is a
    plausible one (e.g. "acme.com") -- format only, no DNS/MX lookup."""
    if not domain:
        return "Company email domain is required."
    if not _DOMAIN_RE.match(domain):
        return f"'{domain}' is not a valid email domain. Enter a domain like acme.com."
    return None


def validate_employee_email_domain(email) -> str:
    """Enforces "employee email must match the company's configured
    domain" -- but only once a company has actually set one
    (get_company_settings()["email_domain"]); companies that haven't
    configured a domain yet keep today's behavior (email optional, no
    domain check), so this never breaks an existing tenant that predates
    the feature. Returns an error message, or None if OK."""
    domain = (get_company_settings().get("email_domain") or "").strip().lower()
    if not domain:
        return None
    email = (email or "").strip().lower()
    if not email:
        return f"Employee email is required (must be a @{domain} address)."
    if not email.endswith("@" + domain):
        return f"Employee email must be a @{domain} address."
    return None


# ── Trial employee cap (hard, server-side, independent of the frontend) ──────
TRIAL_EMPLOYEE_CAP = 2


def _tenant_is_trialing() -> bool:
    """Cheap, lock-free check against the master control-plane DB (a
    different DB/connection than the tenant DB entirely, so it can't
    share the tenant-side row lock below anyway).

    A trial with auto-debit already active is over: enabling auto-debit
    ends the trial (blueprints/auto_debit.py's confirm()), but a company
    that enabled it before that rule existed is still marked 'trialing'
    and would stay capped at TRIAL_EMPLOYEE_CAP while paying -- end its
    trial here the same way, then treat it as paid."""
    try:
        from flask import g as _g
        from database import get_master_db
        db = get_master_db()
        cur = db.cursor(buffered=True)
        try:
            cur.execute(
                "SELECT t.subscription_status, m.status FROM tenants t "
                "LEFT JOIN auto_debit_mandates m ON m.tenant_schema = t.db_name WHERE t.db_name=%s",
                (_g.tenant_db,)
            )
            row = cur.fetchone()
            if not row or row[0] != "trialing":
                return False
            if row[1] != "active":
                return True
            cur.execute(
                "UPDATE tenants SET subscription_status='active', payment_option='online' "
                "WHERE db_name=%s AND subscription_status='trialing'", (_g.tenant_db,)
            )
            db.commit()
            return False
        finally:
            cur.close()
            db.close()
    except Exception:
        return False


# ── Paid employee-seat cap (employee-registration gate) ──────────────────────
def add_employee_seat_cap_check(cursor=None) -> str:
    """Enforces whichever employee-count cap applies to this tenant --
    the hardcoded TRIAL_EMPLOYEE_CAP for a tenant mid-trial, or otherwise
    company_settings.paid_employee_slots (the seat count actually paid for
    at signup via blueprints/billing.py's payment-verified flow). Both
    checks live in this one function (rather than a separate
    _get_trial_employee_cap_error()) so there's exactly one COUNT(*) and
    one lock acquisition per call, not one per cap type. A slots value of
    None means unlimited: the free/unmetered provisioning paths
    (local-dev fallback, mobile app registration, Platform Admin's own
    tenant creation) never set this column, and existing tenants
    provisioned before this check existed also have it unset, so this
    never blocks them -- unless they're also mid-trial, which still
    applies TRIAL_EMPLOYEE_CAP regardless. Returns an error message ready
    to flash/return as-is, or None if there's room.

    Pass the caller's own open cursor -- the same one it will use for the
    employee INSERT right after this returns None -- to make the cap
    race-safe, AND wrap both this call and that INSERT in
    `with database.transaction(db):`. The transaction() part is not
    optional: database.py's connection pool hands out connections with
    autocommit=True (_borrow_connection()), so on a bare cursor (no
    explicit transaction) the row lock this takes below would release
    itself the instant its own SELECT statement finished -- a full
    statement earlier than the INSERT it's supposed to still be
    protecting -- and the race would be exactly as open as if no cursor
    had been passed at all. With a real transaction, and only when
    there's an actual cap to enforce (trialing, or paid_employee_slots is
    not None), this takes a row lock on company_settings FIRST (the one
    tenant-wide settings row), before counting employees, so a second
    concurrent caller blocks here until the first either commits its
    INSERT (and this recount then correctly sees it, and rejects) or
    rolls back (and this recount doesn't see it, so a slot is still
    free). That's what actually closes the classic check-then-insert
    TOCTOU race: two requests both reading COUNT()==cap-1 before either
    has inserted, and both proceeding. Unlimited, non-trial tenants skip
    the lock entirely -- there's no cap to protect, so there's no reason
    to serialize their employee creation.

    Called with no cursor (own short-lived, read-only connection,
    released immediately, no lock taken) is NOT race-safe by itself --
    it's kept only for a cheap early/UX preflight (e.g. rejecting before
    an expensive face-photo upload), not as the actual enforcement point.
    Every call site must also perform the cursor-and-transaction-carrying,
    lock-protected call immediately before its INSERT for the cap to
    actually hold under concurrency -- see blueprints/employees.py's
    add_employee_page()/api_register_employee() and blueprints/core.py's
    api_employee_signup() for the pattern.
    """
    is_trialing = _tenant_is_trialing()
    cap = get_company_settings().get("paid_employee_slots")
    if not is_trialing and cap is None:
        return None

    owns_conn = cursor is None
    db = None
    try:
        if owns_conn:
            db = get_db_connection()
            cursor = db.cursor()
        else:
            # Postgres: blocks concurrent callers until the lock holder's
            # transaction ends. SQLite fallback: FOR UPDATE isn't
            # supported and this execute() silently no-ops (see
            # database.py's _SqliteCursor.execute), which is fine there --
            # that fallback already serializes all queries behind one
            # process-wide lock, so no additional locking is needed.
            cursor.execute("SELECT id FROM company_settings ORDER BY id LIMIT 1 FOR UPDATE")

        cursor.execute("SELECT COUNT(*) FROM employees")
        current = cursor.fetchone()[0]
    except Exception:
        return None  # fail open -- a transient DB hiccup shouldn't block registration
    finally:
        if owns_conn:
            try:
                cursor.close()
                db.close()
            except Exception:  # nosec B110 -- best-effort close; must not mask the result
                pass

    if is_trialing and current >= TRIAL_EMPLOYEE_CAP:
        return (
            f"Your trial allows up to {TRIAL_EMPLOYEE_CAP} employees. "
            f"Upgrade under Settings → Finances → Billing to add more."
        )
    if cap is not None and current >= cap:
        return (
            f"You've reached your plan's limit of {cap} employee"
            f"{'s' if cap != 1 else ''}. Buy more seats under Seats & Billing to add more."
        )
    return None
