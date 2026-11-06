# -*- coding: utf-8 -*-
"""Jinja template filters, globals and context processors.

Split out of app.py; registered on the shared Flask app at import time.
app.py imports the app_setup modules in a fixed order because Flask runs
before_request hooks in registration order (after_request in reverse).
"""
import datetime
import os
import secrets
import utils.config as cfg
from database import get_db_connection, get_master_db
from extensions import app, app_log
from flask import g as _g, session
from utils.auth import HR_ROLE
from utils.helpers import coerce_datetime, get_companies_list, get_company_settings, get_overdue_onboarding_count, static_url as _static_url, tpath as _tpath


@app.context_processor
def inject_common_vars():
    shift = cfg.shift_rules()
    return dict(
        shift_start=shift.start.strftime("%I:%M %p"),
        shift_end=shift.end.strftime("%I:%M %p"),
        # templates/admin_base.html's sidebar "Dashboard" link reads this
        # (`tpath('/employees') if _is_hr else tpath('/admin')`) -- it was
        # referencing this exact name already, just never actually
        # defined anywhere, so Jinja silently treated it as falsy and
        # every admin-side session, HR-role included, always got sent to
        # /admin. HR sessions (whether a real admin_users role='hr'
        # account, or an employee auto-routed here because their
        # employees.role is exactly "HR" -- see blueprints/auth.py's
        # _finish_employee_login) land on /employees instead, which is
        # also where their login already redirects them to.
        _is_hr=(session.get("admin_role") == HR_ROLE),
        hr_has_own_employee=_hr_has_own_employee(),
    )


def _hr_has_own_employee():
    """True if the current HR-role session's admin_username is backed by
    a real employees row -- gates templates/admin_base.html's "My
    Profile" button (blueprints/auth.py's switch_to_my_employee_portal()
    404s harmlessly without this, but showing the button at all when
    there's nothing to switch to is just a dead click). Some admin_users
    role='hr' accounts are standalone (created via /hr_accounts) rather
    than auto-provisioned from an employee whose own role is "HR" -- see
    blueprints/auth.py's _ensure_hr_admin_account -- and have no
    employees row of their own to switch to.

    Cached on flask.g per-request since inject_common_vars() runs once
    per template render but this can be called from more than one context
    processor in the future; cheap either way (one indexed lookup on
    employees.employee_id's UNIQUE constraint)."""
    if session.get("admin_role") != HR_ROLE or not session.get("admin_username"):
        return False
    if not hasattr(_g, "_hr_has_own_employee"):
        try:
            db = get_db_connection()
            cursor = db.cursor(buffered=True)
            cursor.execute("SELECT 1 FROM employees WHERE employee_id=%s", (session["admin_username"],))
            _g._hr_has_own_employee = cursor.fetchone() is not None
            cursor.close()
            db.close()
        except Exception:
            _g._hr_has_own_employee = False
    return _g._hr_has_own_employee


# /favicon.ico and /healthz are served by blueprints/health.py


# Jinja2 filter: handles both datetime.time and datetime.timedelta.
# psycopg2 returns TIME columns as datetime.time (hits the strftime branch
# below); the timedelta branch is a defensive fallback kept from when this
# ran against mysql-connector, which returned TIME columns as timedelta.
@app.template_filter("fmt_time")
def fmt_time_filter(value):
    if value is None:
        return "--"
    if isinstance(value, str):
        return value
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S")
    # timedelta fallback -- see comment above
    total = int(value.total_seconds())
    return "{:02d}:{:02d}:{:02d}".format(total // 3600, (total % 3600) // 60, total % 60)

# Templates that need arithmetic on a TIME value (elapsed-time math, HH/MM/SS
# breakdowns) used to rely on mysql-connector's timedelta.seconds. psycopg2
# returns datetime.time instead, which has no .seconds -- this filter gives
# templates a type-agnostic "total seconds" so that math still works.


@app.template_filter("time_seconds")
def time_seconds_filter(value):
    if value is None:
        return 0
    if hasattr(value, "hour"):
        return value.hour * 3600 + value.minute * 60 + value.second
    return int(value.total_seconds())


@app.template_filter("plan_price")
def plan_price_filter(paise):
    """paise -> "₹1,999" for billing displays (super_admin_dashboard.html,
    create_org.html) -- thin wrapper so templates don't import utils.plan_limits."""
    from utils.plan_limits import format_price_inr
    return format_price_inr(paise)


def _csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = _csrf_token
app.jinja_env.globals["timedelta"] = datetime.timedelta

app.jinja_env.globals["tpath"] = _tpath
app.jinja_env.globals["static_url"] = _static_url


@app.context_processor
def inject_companies_context():
    """Inject active company and companies list into every admin template.
    Reads through get_companies_list()'s 30s cache rather than querying the
    companies table on every single admin page render."""
    if not session.get("admin_logged_in"):
        return {}
    try:
        rows = get_companies_list()
        active_cid = session.get("active_company_id")
        active_company = None
        for r in rows:
            if r[0] == active_cid:
                active_company = {"id": r[0], "name": r[1], "code": r[2]}
                break
        return {
            "all_companies": [{"id": r[0], "name": r[1], "code": r[2], "has_pin": bool(r[3])} for r in rows],
            "active_company": active_company,
        }
    except Exception:
        return {"all_companies": [], "active_company": None}


@app.context_processor
def inject_overdue_onboardings():
    """Reads through get_overdue_onboarding_count()'s 20s cache rather than
    running a COUNT query on every single admin page render."""
    if not session.get("admin_logged_in"):
        return {}
    try:
        return {"overdue_onboardings": get_overdue_onboarding_count()}
    except Exception:
        return {"overdue_onboardings": 0}


@app.context_processor
def inject_billing_lock_status():
    """Lets templates/admin_base.html show a proactive grace/locked banner
    without every route handler having to fetch it -- the billing lock
    (utils/auth.py) only acts once the grace period is over, this is what
    lets an admin see the deadline *before* they hit that block. Cheap
    single-row lookup, same posture as inject_overdue_onboardings above
    (only runs for a logged-in admin session, fails soft to "nothing to
    show" rather than ever breaking a page render)."""
    if not session.get("admin_logged_in"):
        return {}
    try:
        from flask import g as _g
        db = get_master_db()
        cur = db.cursor(buffered=True)
        cur.execute(
            "SELECT id, billing_state, grace_period_ends_at, subscription_status, trial_start_date "
            "FROM tenants WHERE db_name=%s",
            (_g.tenant_db,)
        )
        row = cur.fetchone()
        cur.close()
        db.close()
        if not row:
            return {"billing_lock_status": None}
        tenant_id, billing_state, grace_period_ends_at, subscription_status, trial_start_date = row
        # First-login trial-start stamp: deliberately here, not in
        # blueprints/auth.py's admin_login(), since that route has two
        # separate points where a session actually gets established
        # (direct login when MANDATORY_LOGIN_MFA is off, vs after a
        # separate /mfa_verify completion when it's on) -- this context
        # processor runs on every admin-authenticated page render
        # regardless of which path was used, so it's the one place
        # guaranteed to see "this tenant's first real login" exactly once,
        # via the trial_start_date IS NULL guard below. Fires at most once
        # ever per tenant; every render after that just no-ops on the
        # subscription_status/trial_start_date check.
        if subscription_status == "trialing" and trial_start_date is None:
            from blueprints.trial_billing import TRIAL_DURATION_DAYS
            db2 = get_master_db()
            cur2 = db2.cursor()
            cur2.execute(
                "UPDATE tenants SET trial_start_date=NOW(), "
                "trial_end_date=NOW() + (%s * INTERVAL '1 day') WHERE id=%s AND trial_start_date IS NULL",
                (TRIAL_DURATION_DAYS, tenant_id)
            )
            db2.commit()
            cur2.close()
            db2.close()
        if billing_state == "current":
            return {"billing_lock_status": None}
        return {"billing_lock_status": {"state": billing_state, "grace_period_ends_at": coerce_datetime(grace_period_ends_at)}}
    except Exception:
        return {"billing_lock_status": None}


@app.context_processor
def inject_registration_checklist():
    """Lets templates/admin_base.html show the "Complete your registration"
    checklist -- company details, documents, payment, which signup no
    longer asks for (blueprints/org.py's registration_checklist()) --
    without every route handler fetching it. Same fail-soft posture as
    inject_billing_lock_status() just above. Only for the company's
    top-level admin: HR and manager accounts can't act on these steps.
    Hidden once every step is done; documents still under review keep it
    on show, as "Under review"."""
    if not session.get("admin_logged_in") or session.get("admin_role", "admin") != "admin":
        return {}
    try:
        from flask import g as _g
        from blueprints.org import registration_checklist
        steps = registration_checklist(_g.tenant_db)
    except Exception:
        app_log.warning("inject_registration_checklist: lookup failed", exc_info=True)
        return {"registration_checklist": None}
    if not steps or all(step["status"] == "done" for step in steps):
        return {"registration_checklist": None}
    return {"registration_checklist": steps}


@app.context_processor
def inject_admin_name():
    """Name and initials for templates/admin_base.html's profile menu: the
    company admin's name from the Organization page, an HR or manager's name
    from their employee record, else the login username. Fail-soft, like the
    other banner lookups above."""
    if not session.get("admin_logged_in"):
        return {}
    username = session.get("admin_username") or ""
    name = None
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        if session.get("admin_role", "admin") == "admin":
            cursor.execute("SELECT display_name FROM admin_users WHERE username=%s", (username,))
        else:
            cursor.execute("SELECT name FROM employees WHERE employee_id=%s", (username,))
        row = cursor.fetchone()
        name = row[0] if row else None
        cursor.close()
        db.close()
    except Exception:
        app_log.warning("inject_admin_name: lookup failed", exc_info=True)
    words = (name or "").split()
    initials = "".join(w[0] for w in words[:2]) if len(words) > 1 else (name or username or "Admin")[:2]
    return {"admin_display_name": name, "admin_initials": initials.upper()}


@app.context_processor
def inject_admin_avatar():
    """Photo for the profile avatar in templates/admin_base.html's top
    banner. HR and manager accounts log in with their employee ID, so they
    get their employee photo (served by employees.py's admin-only
    /dataset/<file>); the company admin gets the photo uploaded on its
    Profile page (blueprints/company_profile.py). No photo: initials."""
    if not session.get("admin_logged_in"):
        return {}
    username = session.get("admin_username") or ""
    if session.get("admin_role", "admin") == "admin":
        from blueprints.company_profile import admin_photo_path
        try:
            version = int(os.path.getmtime(admin_photo_path(username)))
        except (OSError, AttributeError):
            return {}
        return {"admin_avatar_url": _tpath("/profile/photo") + f"?v={version}"}
    if not username or os.path.basename(username) != username:
        return {}
    from utils.tenant_files import resolve_face_photo
    path = resolve_face_photo(username) or ""
    try:
        version = int(os.path.getmtime(path))
    except OSError:
        return {}
    return {"admin_avatar_url": _tpath(f"/dataset/{username}.jpg") + f"?v={version}"}


@app.context_processor
def inject_company():
    return {"co": get_company_settings()}
