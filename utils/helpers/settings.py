# -*- coding: utf-8 -*-
"""helpers: company settings, auth config and feature flags (with TTL caches), company time zone"""
import datetime
import json
import os
import pytz
import threading
from database import get_db_connection
from extensions import app_log, redis_client
from .validation import _SAFE_IDENT_RE


# ── Company settings cache (60-second TTL) ────────────────────────────────────
# Every cache below is keyed by _tenant_cache_key(), not a bare {"data":...}
# singleton -- a single shared dict would let one tenant's company_settings/
# auth_config/companies-list/pending-counts leak into another tenant's
# response for up to its TTL window under ordinary concurrent multi-tenant
# traffic (two different companies' requests interleaved on the same
# process), not just a test artifact. get_db_connection() already scopes the
# underlying query correctly per g.tenant_db; only the cache layer on top of
# it was unscoped.
#
# Backed by Redis (shared across gunicorn workers) when extensions.redis_client
# is configured, falling back to this in-memory per-worker dict otherwise --
# same pattern as utils/waf.py's breach counter. Without Redis, an admin
# changing a setting on the worker that handles their request left every
# OTHER worker serving the stale value for up to _CO_CACHE_TTL seconds,
# since invalidate_settings_cache() only ever cleared its own process's dict.
_co_cache = {}
_auth_cache = {}
_settings_lock = threading.Lock()
# Configurable via env so an operator can trade off "settings changes are
# visible sooner" against "fewer DB round-trips under load" without a code
# change -- same pattern as utils/waf.py's threshold env vars. Default (60s)
# is what this cache has always used.
_CO_CACHE_TTL = int(os.environ.get("SETTINGS_CACHE_TTL_SECONDS", "60"))

# Sentinel distinct from None -- get_overdue_onboarding_count() legitimately
# caches 0, and get_auth_config()/get_company_settings() can cache a dict
# with falsy values, so "no entry" can't just be represented as a falsy return.
_CACHE_MISS = object()


def _tenant_cache_key():
    try:
        from flask import g as _flask_g
        return getattr(_flask_g, "tenant_db", None) or "__no_tenant__"
    except RuntimeError:
        return "__no_tenant__"  # no active Flask request/app context


def _co_expired(cache):
    entry = cache.get(_tenant_cache_key())
    return entry is None or entry["data"] is None or datetime.datetime.now() >= entry["expires"]


def _cache_get(mem_cache, redis_prefix):
    """Read a cached value for the current tenant. Tries Redis first when
    configured -- including falling back to the in-memory dict if a
    configured Redis errors or is unreachable mid-request, matching
    utils/waf.py's _record_breach_redis fallback. Returns _CACHE_MISS on
    a genuine miss (expired/absent), never None/falsy, since a cached
    value can itself be None-ish."""
    key = _tenant_cache_key()
    if redis_client is not None:
        try:
            raw = redis_client.get(f"{redis_prefix}:{key}")
            return json.loads(raw) if raw is not None else _CACHE_MISS
        except Exception as exc:
            app_log.warning("%s: redis read failed, using in-memory fallback: %s", redis_prefix, exc)
    with _settings_lock:
        if not _co_expired(mem_cache):
            return mem_cache[key]["data"]
    return _CACHE_MISS


def _cache_set(mem_cache, redis_prefix, data, ttl):
    """Write a cached value for the current tenant -- Redis when configured
    (falling back to the in-memory dict on error), the in-memory dict
    otherwise. The in-memory dict is kept as the fallback path even when
    Redis is configured, not written in parallel with it, since it's only
    ever read when a Redis attempt fails or Redis isn't configured at all."""
    key = _tenant_cache_key()
    if redis_client is not None:
        try:
            redis_client.setex(f"{redis_prefix}:{key}", ttl, json.dumps(data))
            return
        except Exception as exc:
            app_log.warning("%s: redis write failed, using in-memory fallback: %s", redis_prefix, exc)
    with _settings_lock:
        mem_cache[key] = {"data": data, "expires": datetime.datetime.now() + datetime.timedelta(seconds=ttl)}


def _redis_clear_prefix(prefix):
    """Delete every tenant's Redis key under this cache's namespace -- the
    Redis-backed equivalent of the in-memory dict's own .clear(). Uses
    SCAN rather than KEYS so this never blocks the shared Redis instance,
    even though the actual key count here (one per tenant) is small."""
    if redis_client is None:
        return
    try:
        keys = list(redis_client.scan_iter(match=f"{prefix}:*"))
        if keys:
            redis_client.delete(*keys)
    except Exception as exc:
        app_log.warning("Redis cache clear failed for prefix %s: %s", prefix, exc)


def invalidate_settings_cache():
    """Clears every tenant's cached entry, not just the caller's current
    one -- deliberately, matching this cache's original (pre-tenant-keying)
    "always clear everything" behavior. A caller that updated
    company_settings without an active Flask request context (a test
    fixture writing directly via its own DB connection, a script) would
    otherwise invalidate the "__no_tenant__" key while the real per-tenant
    entry that live requests actually read from stays stale -- invalidation
    is rare enough (only after a genuine settings write) that clearing
    everyone's cache is a fine trade for never getting this wrong."""
    with _settings_lock:
        _co_cache.clear()
        _auth_cache.clear()
    _redis_clear_prefix("settings_co")
    _redis_clear_prefix("settings_auth")


def get_company_settings():
    cached = _cache_get(_co_cache, "settings_co")
    if cached is not _CACHE_MISS:
        return dict(cached)
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("SELECT * FROM company_settings LIMIT 1")
        row = cursor.fetchone()
        row_dict = {}
        if row:
            if hasattr(row, "keys"):
                row_dict = {k.lower(): row[k] for k in row.keys()}
            elif cursor.description:
                cols = [desc[0].lower() for desc in cursor.description]
                row_dict = dict(zip(cols, row))
        cursor.close()
        db.close()

        if row_dict:
            result = {
                "company_name": row_dict.get("company_name") or "My Company",
                "company_tagline": row_dict.get("company_tagline") or "HRzest.com",
                "company_logo": row_dict.get("company_logo"),
                "currency_symbol": row_dict.get("currency_symbol") or "₹",
                "company_code": row_dict.get("company_code") or "COMP",
                "timezone": row_dict.get("timezone") or "Asia/Kolkata",
                "setup_done": bool(row_dict.get("setup_done")),
                "org_profile_pending": bool(row_dict.get("org_profile_pending")),
                "industry": row_dict.get("industry") or "",
                "company_size": row_dict.get("company_size") or "",
                "city": row_dict.get("city") or "",
                "state": row_dict.get("state") or "",
                "postal_code": row_dict.get("postal_code") or "",
                "country": row_dict.get("country") or "",
                "session_timeout": row_dict.get("session_timeout") or 30,
                "logo_url": row_dict.get("logo_url") or "",
                "plan": row_dict.get("plan") or "basic",
                "email_domain": row_dict.get("email_domain") or "",
                # None = unlimited (unmetered tenant, or predates this
                # column); a real integer is the seat count actually paid
                # for at signup -- see add_employee_seat_cap_check() below.
                "paid_employee_slots": row_dict.get("paid_employee_slots"),
                "contact_phone": row_dict.get("contact_phone") or "",
                "address": row_dict.get("address") or "",
                "contact_email": row_dict.get("contact_email") or "",
                "website": row_dict.get("website") or "",
                # Which weekdays this company treats as working days (Settings
                # -> Company Profile -> Working Days). Read by utils/
                # attendance_utils.py's company_working_weekdays(), which
                # drives attendance/payroll day-counting -- see that function's
                # own docstring for why this used to be silently ignored.
                "working_days": (row_dict.get("working_days") or "Mon,Tue,Wed,Thu,Fri").split(","),
                # Per-company shift and payroll rules, read via utils/config.py's
                # shift_rules()/salary_rules(). Stored as plain JSON-safe values
                # (times as "HH:MM:SS") so the Redis cache path can hold them.
                **_rule_settings(row_dict),
            }
            _cache_set(_co_cache, "settings_co", result, _CO_CACHE_TTL)
            return dict(result)
    except Exception as exc:
        from database import DatabaseUnavailable
        if isinstance(exc, DatabaseUnavailable):
            raise
        # Falls through to generic hardcoded defaults below -- every page
        # in the app would silently render wrong branding/settings, worth
        # a trace.
        app_log.warning("get_company_settings failed, using generic defaults: %s", exc, exc_info=True)
    return {"company_name": "My Company", "company_tagline": "HRzest.com",
            "company_logo": None, "currency_symbol": "₹", "timezone": "Asia/Kolkata",
            "setup_done": False, "company_code": "", "session_timeout": 30, "logo_url": "", "plan": "basic",
            "email_domain": "", "paid_employee_slots": None, "contact_phone": "", "address": "",
            "contact_email": "", "website": "", "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            **_rule_settings({})}


def _rule_settings(row_dict):
    """The company's shift/payroll rule columns as JSON-safe values (see
    utils/config.py); company_settings column defaults where unset."""
    import utils.config as cfg

    def _t(key, default):
        return cfg.as_time(row_dict.get(key), default).strftime("%H:%M:%S")

    def _num(key, default):
        v = row_dict.get(key)
        return float(v) if v is not None else default
    grace = row_dict.get("grace_minutes")
    return {
        "shift_start": _t("shift_start", cfg.DEFAULT_SHIFT_START),
        "shift_half": _t("shift_half", cfg.DEFAULT_SHIFT_HALF),
        "shift_end": _t("shift_end", cfg.DEFAULT_SHIFT_END),
        "grace_minutes": int(grace) if grace is not None else cfg.DEFAULT_GRACE_MINUTES,
        "late_deduction_pct": _num("late_deduction_pct", cfg.DEFAULT_LATE_DEDUCTION_PCT),
        "half_day_deduction_pct": _num("half_day_deduction_pct", cfg.DEFAULT_HALF_DAY_DEDUCTION_PCT),
        "holiday_pay": row_dict.get("holiday_pay") or cfg.DEFAULT_HOLIDAY_PAY,
        "leave_pay": row_dict.get("leave_pay") or cfg.DEFAULT_LEAVE_PAY,
    }


def _company_tzinfo():
    """Resolve the current tenant's configured timezone (company_settings.timezone,
    via get_company_settings() -- already tenant-scoped through get_db_connection()'s
    flask.g.tenant_db resolution) to a pytz tzinfo, falling back to Asia/Kolkata for an
    unset/unrecognized value -- same fallback get_company_settings() itself uses."""
    tz_name = (get_company_settings().get("timezone") or "Asia/Kolkata").strip()
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("Asia/Kolkata")


def company_now():
    """Current wall-clock datetime in the current tenant's configured timezone
    (company_settings.timezone), timezone-aware. Anchored to a real UTC instant
    first (datetime.datetime.now(pytz.utc)) rather than the naive local
    datetime.datetime.now(), since the server host's own system clock is not
    guaranteed to be UTC either. Use this (or company_today()) instead of
    datetime.datetime.now()/datetime.date.today() anywhere "today"/"now" is used
    to decide which calendar day a check-in, leave date, or payroll period
    belongs to."""
    return datetime.datetime.now(pytz.utc).astimezone(_company_tzinfo())


def company_today():
    """Current calendar date in the current tenant's configured timezone. See
    company_now() -- this is just company_now().date()."""
    return company_now().date()


# Platform-wide dates -- billing periods, dunning -- belong to no one
# company, so they use the platform's own timezone rather than the
# container's clock (UTC), which moved month boundaries to 05:30 IST
# (QA BUG-27).
PLATFORM_TZ = pytz.timezone("Asia/Kolkata")


def platform_today():
    return datetime.datetime.now(PLATFORM_TZ).date()


# ── Companies list + overdue-onboarding count caches (short TTL) ─────────────
# Both back per-request context processors (app.py's inject_companies_context
# / inject_overdue_onboardings) that previously ran on every single
# admin-rendered page with no cache at all, stacking on top of the
# always-fresh security checks (_enforce_ip_ban, _enforce_admin_mfa_enrollment)
# that must stay uncached. These two are pure reference/reporting data -- a
# few seconds of staleness (a brand-new company not yet in the switcher, an
# onboarding-overdue badge lagging slightly) is an acceptable trade for
# cutting 2 of the ~4 DB round trips every admin page load previously paid.
_companies_cache = {}
_onboarding_cache = {}
_COMPANIES_CACHE_TTL = int(os.environ.get("COMPANIES_CACHE_TTL_SECONDS", "30"))
_ONBOARDING_CACHE_TTL = int(os.environ.get("ONBOARDING_CACHE_TTL_SECONDS", "20"))


def invalidate_companies_cache():
    # Clears every tenant's entry -- see invalidate_settings_cache()'s
    # docstring for why (a caller without an active request context must
    # not silently invalidate the wrong key).
    with _settings_lock:
        _companies_cache.clear()
    _redis_clear_prefix("settings_companies")


def get_companies_list():
    """Cached list of (id, name, code, has_pin) rows from the companies
    table -- tuples on the in-memory-fallback path, lists when served from
    Redis (JSON has no tuple type); every caller only ever index/iterates
    them, never checks the exact type. Call invalidate_companies_cache()
    after any write to companies (add/edit/delete/set-pin/rename-code)."""
    cached = _cache_get(_companies_cache, "settings_companies")
    if cached is not _CACHE_MISS:
        return list(cached)
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute("""
            SELECT id, name, COALESCE(code,''), COALESCE(pin,'')
            FROM companies ORDER BY name
        """)
        rows = cur.fetchall()
        cur.close()
        db.close()
        _cache_set(_companies_cache, "settings_companies", rows, _COMPANIES_CACHE_TTL)
        return list(rows)
    except Exception:
        return []


def get_overdue_onboarding_count():
    """Cached count of non-completed onboarding tasks past their due date."""
    cached = _cache_get(_onboarding_cache, "settings_onboarding")
    if cached is not _CACHE_MISS:
        return cached
    try:
        db = get_db_connection()
        cur = db.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM employee_onboarding
            WHERE status != 'Completed' AND due_date < %s
        """, (company_today(),))
        count = cur.fetchone()[0]
        cur.close()
        db.close()
        _cache_set(_onboarding_cache, "settings_onboarding", count, _ONBOARDING_CACHE_TTL)
        return count
    except Exception:
        return 0


_AUTH_CONFIG_DEFAULTS = {
    "fingerprint_enabled": False, "qr_enabled": True, "face_enabled": True,
    "location_enabled": True, "employee_password_auth": True,
    "geo_radius": 100, "office_lat": None, "office_lon": None,
    "app_gps_checkin": None,
}


def get_auth_config():
    cached = _cache_get(_auth_cache, "settings_auth")
    if cached is not _CACHE_MISS:
        return dict(cached)
    try:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("""
            SELECT COALESCE(fingerprint_enabled,0), COALESCE(qr_enabled,1),
                   COALESCE(face_enabled,1), COALESCE(location_enabled,1),
                   COALESCE(employee_password_auth,1), COALESCE(geo_radius,100),
                   office_lat, office_lon, app_gps_checkin
            FROM company_settings LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()
        db.close()
        if row:
            result = {
                "fingerprint_enabled": bool(row[0]), "qr_enabled": bool(row[1]),
                "face_enabled": bool(row[2]), "location_enabled": bool(row[3]),
                "employee_password_auth": bool(row[4]), "geo_radius": row[5],
                "office_lat": row[6], "office_lon": row[7],
                "app_gps_checkin": None if row[8] is None else bool(row[8]),
            }
            _cache_set(_auth_cache, "settings_auth", result, _CO_CACHE_TTL)
            return dict(result)
    except Exception as exc:
        app_log.warning("get_auth_config failed, using defaults: %s", exc, exc_info=True)
    return dict(_AUTH_CONFIG_DEFAULTS)


def allowed_checkin_combos(auth_cfg):
    """The attendance check-in methods this organisation's settings permit,
    decided on the server -- a client-supplied auth_combo outside this set
    must be rejected. Whenever face recognition is enabled, every allowed
    method includes the face check (so QR-only / fingerprint-only can't be
    used to skip it); otherwise QR and fingerprint methods are offered as
    enabled."""
    qr = bool(auth_cfg.get("qr_enabled"))
    face = bool(auth_cfg.get("face_enabled"))
    fingerprint = bool(auth_cfg.get("fingerprint_enabled"))
    allowed = set()
    if face:
        if qr:
            allowed.add("qr_face")
        if fingerprint:
            allowed.add("face_fingerprint")
    else:
        if qr:
            allowed.add("qr_only")
        if qr and fingerprint:
            allowed.add("qr_fingerprint")
        if fingerprint:
            allowed.add("fingerprint_only")
    return allowed


def app_gps_checkin_allowed(auth_cfg):
    """Whether the mobile app's location-only check-in (/api/employee/checkin:
    login token plus GPS, no QR, face or fingerprint) is allowed. The
    admin's explicit setting decides; unset, it is allowed only when the
    company has no verified check-in method enabled, so it can't be used to
    skip the QR or face check the company requires (QA NV-18)."""
    explicit = auth_cfg.get("app_gps_checkin")
    if explicit is not None:
        return bool(explicit)
    return not allowed_checkin_combos(auth_cfg)


def _read_global_features():
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute("""
            SELECT face_auth_enabled, geo_enabled, COALESCE(geo_radius,300), qr_enabled,
                   pin_enabled, COALESCE(fingerprint_enabled,0), COALESCE(biometric_enabled,0),
                   COALESCE(notify_leave,1), COALESCE(notify_payslip,1),
                   COALESCE(notify_resignation,1), COALESCE(notify_doc_expiry,1),
                   COALESCE(session_timeout,30),
                   COALESCE(late_deduction_pct,10), COALESCE(half_day_deduction_pct,50),
                   COALESCE(grace_minutes,15), COALESCE(holiday_pay,'paid'),
                   COALESCE(leave_pay,'exclude'),
                   COALESCE(shift_start,'09:00:00'), COALESCE(shift_half,'13:00:00'),
                   COALESCE(shift_end,'18:00:00')
            FROM company_settings LIMIT 1
        """)
        r = cur.fetchone()
        cur.close()
        db.close()
        if r:
            return {
                "face_auth_enabled": bool(r[0]), "geo_enabled": bool(r[1]),
                "geo_radius": r[2], "qr_enabled": bool(r[3]), "pin_enabled": bool(r[4]),
                "fingerprint_enabled": bool(r[5]), "biometric_enabled": bool(r[6]),
                "notify_leave": bool(r[7]), "notify_payslip": bool(r[8]),
                "notify_resignation": bool(r[9]), "notify_doc_expiry": bool(r[10]),
                "session_timeout": r[11], "late_deduction_pct": float(r[12]),
                "half_day_deduction_pct": float(r[13]), "grace_minutes": int(r[14]),
                "holiday_pay": r[15], "leave_pay": r[16],
                "shift_start": r[17], "shift_half": r[18], "shift_end": r[19],
            }
    except Exception as exc:
        app_log.warning("_read_global_features failed, using defaults: %s", exc, exc_info=True)
    return {
        "face_auth_enabled": True, "geo_enabled": False, "geo_radius": 300,
        "qr_enabled": True, "pin_enabled": True, "fingerprint_enabled": False,
        "biometric_enabled": False, "notify_leave": True, "notify_payslip": True,
        "notify_resignation": True, "notify_doc_expiry": True, "session_timeout": 30,
        "late_deduction_pct": 10.0, "half_day_deduction_pct": 50.0, "grace_minutes": 15,
        "holiday_pay": "paid", "leave_pay": "exclude",
        "shift_start": "09:00:00", "shift_half": "13:00:00", "shift_end": "18:00:00",
    }


def get_co_features(company_id=None):
    if not company_id:
        return _read_global_features()
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute("""
            SELECT face_auth_enabled, geo_enabled, geo_radius, qr_enabled,
                   pin_enabled, fingerprint_enabled, biometric_enabled,
                   notify_leave, notify_payslip, notify_resignation, notify_doc_expiry,
                   session_timeout, late_deduction_pct, half_day_deduction_pct,
                   grace_minutes, holiday_pay, leave_pay, shift_start, shift_half, shift_end
            FROM company_feature_settings WHERE company_id=%s
        """, (company_id,))
        r = cur.fetchone()
        cur.close()
        db.close()
        if r:
            return {
                "face_auth_enabled": bool(r[0]), "geo_enabled": bool(r[1]),
                "geo_radius": r[2], "qr_enabled": bool(r[3]), "pin_enabled": bool(r[4]),
                "fingerprint_enabled": bool(r[5]), "biometric_enabled": bool(r[6]),
                "notify_leave": bool(r[7]), "notify_payslip": bool(r[8]),
                "notify_resignation": bool(r[9]), "notify_doc_expiry": bool(r[10]),
                "session_timeout": r[11], "late_deduction_pct": float(r[12]),
                "half_day_deduction_pct": float(r[13]), "grace_minutes": int(r[14]),
                "holiday_pay": r[15], "leave_pay": r[16],
                "shift_start": r[17], "shift_half": r[18], "shift_end": r[19],
            }
    except Exception as exc:
        app_log.warning("Per-company feature settings lookup failed, falling back to global: %s", exc, exc_info=True)
    return _read_global_features()


# shift_start/shift_half/shift_end/holiday_pay/leave_pay were missing from
# this allowlist versus app.py's copy -- not a security gap on their own
# (both copies fail closed on anything not listed), but a functional one:
# any caller trying to persist a per-company shift override through this
# copy would have been silently rejected while app.py's identical-looking
# function accepted it. Added to match.
_VALID_CFS_COLS = frozenset({
    "face_auth_enabled", "geo_enabled", "geo_radius", "qr_enabled", "pin_enabled",
    "fingerprint_enabled", "biometric_enabled", "notify_leave", "notify_payslip",
    "notify_resignation", "notify_doc_expiry", "session_timeout",
    "late_deduction_pct", "half_day_deduction_pct", "grace_minutes",
    "shift_start", "shift_half", "shift_end", "holiday_pay", "leave_pay",
})


def _upsert_co_feature(company_id, field, value):
    if not company_id:
        return
    # Double-gate: frozenset membership + regex ensures only safe identifier chars
    if field not in _VALID_CFS_COLS or not _SAFE_IDENT_RE.match(field):
        app_log.error("_upsert_co_feature: rejected column %r", field)
        return
    try:
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute(f"""
            INSERT INTO company_feature_settings (company_id, {field})
            VALUES (%s, %s)
            ON CONFLICT (company_id) DO UPDATE SET {field}=EXCLUDED.{field}
        """, (company_id, value))  # nosec B608
        db.commit()
        cur.close()
        db.close()
    except Exception as exc:
        # The caller (a settings-save route) has no idea this silently
        # failed -- it flashes "saved" while nothing actually persisted.
        app_log.warning("_upsert_co_feature failed (company_id=%s, field=%s): %s", company_id, field, exc, exc_info=True)


def _upsert_co_features(company_id, fields_dict):
    if not company_id or not fields_dict:
        return
    # Validate every key against frozenset AND regex before any interpolation
    bad = [k for k in fields_dict if k not in _VALID_CFS_COLS or not _SAFE_IDENT_RE.match(k)]
    if bad:
        app_log.error("_upsert_co_features: rejected columns %s", bad)
        return
    try:
        safe_fields = {k: v for k, v in fields_dict.items() if k in _VALID_CFS_COLS}
        cols = ", ".join(safe_fields.keys())
        vals = list(safe_fields.values())
        placeholders = ", ".join(["%s"] * len(vals))
        updates = ", ".join(f"{k}=EXCLUDED.{k}" for k in safe_fields.keys())
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute(f"""
            INSERT INTO company_feature_settings (company_id, {cols})
            VALUES (%s, {placeholders})
            ON CONFLICT (company_id) DO UPDATE SET {updates}
        """, [company_id] + vals)  # nosec B608
        db.commit()
        cur.close()
        db.close()
    except Exception as exc:
        app_log.warning("_upsert_co_features failed (company_id=%s, fields=%s): %s", company_id, list(fields_dict.keys()), exc, exc_info=True)
