# -*- coding: utf-8 -*-
"""admin_views: settings page, rule/flag/company saves, Bearer-token settings API"""
import datetime
import utils.config as cfg
from database import get_db_connection
from flask import flash, g, jsonify, redirect, render_template, request, session
from utils.attendance_utils import _td_to_time, standard_working_days_per_month
from utils.auth import api_required, api_role_required, role_required
from utils.helpers import (
    _upsert_co_feature,
    _upsert_co_features,
    amount,
    app_gps_checkin_allowed,
    company_today,
    get_auth_config,
    get_co_features,
    get_pending_action_counts,
    invalidate_settings_cache,
    parse_finite_float,
    tpath,
)
from utils.helpers.rbac import (
    RBAC_MODULES,
    VALID_BASE_TYPES,
    all_role_permissions,
    assign_employee_role,
    attach_to,
    create_role,
    delete_role,
    detach_from,
    employees_by_role,
    list_employees_with_roles,
    list_roles,
    people_hierarchy,
    rename_role,
    role_ceilings,
    role_tree,
    set_all_permissions,
    set_permissions,
    team_of,
    update_role,
)
from . import _TOGGLE_COLUMN_MAP, _TOGGLE_LABEL_MAP, _update_company_setting_flag, admin_views_bp


@admin_views_bp.route("/settings")
@role_required("admin")
def settings_page():
    tab = request.args.get("tab", "attendance")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)

    cursor.execute("SELECT email FROM admin_users WHERE username=%s", (session.get("admin_username"),))
    _admin_row = cursor.fetchone()
    admin_recovery_email = _admin_row[0] if _admin_row else ""

    # Email config: intentionally NOT fetched here. The Email Settings tab
    # sits behind a 2FA step-up gate (utils/auth.py:require_email_2fa) and is
    # loaded client-side via /api/settings/email only after verification --
    # never server-rendered, so the password (and the rest of the SMTP
    # config) can't leak into the page's initial HTML before the admin
    # proves identity. See templates/settings.html's #email-2fa-gate.

    # Shifts (with company)
    cursor.execute("""
        SELECT s.id, s.name, s.start_time, s.half_time, s.end_time,
               COALESCE(s.company_id, 0), COALESCE(c.name, '')
        FROM shifts s
        LEFT JOIN companies c ON c.id = s.company_id
        ORDER BY c.name, s.start_time
    """)
    shift_rows = []
    for sid, sname, st, ht, et, scid, scname in cursor.fetchall():
        shift_rows.append({
            "id": sid, "name": sname,
            "start": _td_to_time(st).strftime("%H:%M") if st else "--",
            "half": _td_to_time(ht).strftime("%H:%M") if ht else "--",
            "end": _td_to_time(et).strftime("%H:%M") if et else "--",
            "company_id": scid, "company_name": scname,
        })
    cursor.execute(
        "SELECT e.employee_id, e.name, e.role, s.name FROM employees e LEFT JOIN shifts s ON e.shift_id = s.id ORDER BY e.name")
    emp_list = [{"emp_id": r[0], "name": r[1], "role": r[2] or "", "shift": r[3] or "Default"}
                for r in cursor.fetchall()]

    # Company-specific shifts (company_id IS NOT NULL)
    cursor.execute(
        "SELECT id, name, start_time, half_time, end_time, company_id FROM shifts WHERE company_id IS NOT NULL ORDER BY company_id, start_time")
    _co_shifts_raw = cursor.fetchall()
    company_shifts = {}
    for _csid, _csname, _csstart, _cshalf, _csend, _cscid in _co_shifts_raw:
        def _tdfmt(v):
            if v is None:
                return "--"
            if isinstance(v, datetime.timedelta):
                _s = int(v.total_seconds())
                return "%02d:%02d" % (_s // 3600, (_s % 3600) // 60)
            if isinstance(v, datetime.time):
                return v.strftime("%H:%M")
            return str(v)[:5]
        company_shifts.setdefault(_cscid, []).append(
            (_csid, _csname, _tdfmt(_csstart), _tdfmt(_cshalf), _tdfmt(_csend)))

    # Company-specific breaks (company_id IS NOT NULL), nested per shift
    cursor.execute(
        "SELECT id, break_name, break_time, duration_minutes, is_active, company_id, COALESCE(shift_id,0) "
        "FROM break_config WHERE company_id IS NOT NULL ORDER BY company_id, shift_id, break_time")
    _co_breaks_raw = cursor.fetchall()
    company_breaks = {}
    for _cbid, _cbname, _cbt, _cbdur, _cbactive, _cbcid, _cbsid in _co_breaks_raw:
        if _cbt is None:
            _cbt_str = "--"
        elif isinstance(_cbt, datetime.timedelta):
            _s = int(_cbt.total_seconds())
            _cbt_str = "%02d:%02d" % (_s // 3600, (_s % 3600) // 60)
        elif isinstance(_cbt, datetime.time):
            _cbt_str = _cbt.strftime("%H:%M")
        else:
            _cbt_str = str(_cbt)[:5]
        company_breaks.setdefault(_cbcid, {}).setdefault(_cbsid, []).append(
            (_cbid, _cbname, _cbt_str, _cbdur, _cbactive))

    # Breaks (with shift_id) -- pre-format break_time as HH:MM
    cursor.execute(
        "SELECT id, break_name, break_time, duration_minutes, is_active, COALESCE(shift_id,0) "
        "FROM break_config WHERE company_id IS NULL ORDER BY shift_id, break_time")
    breaks = []
    for _bid, _bname, _bt, _bdur, _bactive, _bshift in cursor.fetchall():
        if _bt is None:
            _bt_str = "--"
        elif isinstance(_bt, datetime.timedelta):
            _s = int(_bt.total_seconds())
            _bt_str = "%02d:%02d" % (_s // 3600, (_s % 3600) // 60)
        elif isinstance(_bt, datetime.time):
            _bt_str = _bt.strftime("%H:%M")
        else:
            _bt_str = str(_bt)[:5]
        breaks.append((_bid, _bname, _bt_str, _bdur, _bactive, _bshift))

    # Salary
    cursor.execute("""
        SELECT e.employee_id, e.name, s.salary_per_day, e.role, s.last_revised,
               COALESCE(e.phone,''), COALESCE(e.email,'')
        FROM employees e
        LEFT JOIN salary_config s ON e.employee_id = s.employee_id
        ORDER BY e.name
    """)
    salaries = [(r[0], r[1], amount(r[2])) + tuple(r[3:]) for r in cursor.fetchall()]

    # Announcements (admin sees all; include visibility and target employee name)
    cursor.execute("""
        SELECT a.id, a.title, a.content, a.priority, a.created_at,
               COALESCE(a.visibility,'public'), COALESCE(a.target_employee_id,''), COALESCE(e.name,'')
        FROM announcements a
        LEFT JOIN employees e ON e.employee_id = a.target_employee_id
        ORDER BY a.created_at DESC
    """)
    ann_list = cursor.fetchall()

    # Pending counts
    pending_leaves, pending_resignations, pending_tickets = get_pending_action_counts(cursor, tickets_open_only=True)

    cursor.execute("SELECT COALESCE(company_code,''), COALESCE(default_onboarding_template_id,0) FROM company_settings LIMIT 1")
    _cr = cursor.fetchone()
    company_code = _cr[0] if _cr else ""
    default_onboarding_tpl = int(_cr[1]) if _cr and _cr[1] else 0

    # Company stats
    cursor.execute("SELECT COUNT(*) FROM employees")
    total_employees = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM employees e
        WHERE NOT EXISTS (
            SELECT 1 FROM resignation_requests r
            WHERE r.employee_id = e.employee_id AND r.status = 'Approved'
        )
    """)
    active_employees = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT department) FROM employees WHERE department IS NOT NULL AND department != ''")
    total_departments = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM shifts")
    total_shifts = cursor.fetchone()[0]
    cursor.execute("SELECT id, name FROM onboarding_templates WHERE is_active=1 ORDER BY name")
    onboarding_templates = cursor.fetchall()

    cursor.execute("""
        SELECT c.id, c.name, COALESCE(c.code,''), c.created_at,
               COUNT(e.employee_id) AS emp_count,
               COALESCE(c.working_days,'Mon,Tue,Wed,Thu,Fri'),
               CASE WHEN c.pin IS NOT NULL AND c.pin != '' THEN 1 ELSE 0 END AS has_pin,
               COALESCE(c.logo_path,''),
               CASE WHEN t.company_id IS NOT NULL THEN 1 ELSE 0 END AS has_id_template,
               COALESCE(c.address,''),
               COALESCE(c.website,''),
               COALESCE(c.email,''),
               COALESCE(c.phone,'')
        FROM companies c
        LEFT JOIN employees e ON e.company_id = c.id
        LEFT JOIN id_card_templates t ON t.company_id = c.id
        GROUP BY c.id, c.name, c.code, c.created_at, c.working_days, c.pin, c.logo_path, t.company_id,
                 c.address, c.website, c.email, c.phone
        ORDER BY c.name
    """)
    companies = cursor.fetchall()

    # Feature flags -- per-company when active, global otherwise
    _active_cid_settings = session.get("active_company_id")
    fr = get_co_features(_active_cid_settings)
    cursor.execute(
        "SELECT COALESCE(working_days,'Mon,Tue,Wed,Thu,Fri'), COALESCE(company_name,''), "
        "COALESCE(timezone,'Asia/Kolkata'), office_lat, office_lon FROM company_settings LIMIT 1")
    _gset = cursor.fetchone()
    features = {
        "face_auth": fr["face_auth_enabled"],
        "geo": fr["geo_enabled"],
        "geo_radius": fr["geo_radius"],
        # Always schema-wide (one physical office per tenant) -- unlike
        # everything else in this dict, never sourced from company_feature_settings.
        "office_lat": _gset[3] if _gset else None,
        "office_lon": _gset[4] if _gset else None,
        "qr": fr["qr_enabled"],
        "pin": fr["pin_enabled"],
        "fingerprint": fr["fingerprint_enabled"],
        "biometric": fr["biometric_enabled"],
        "notify_leave": fr["notify_leave"],
        "notify_payslip": fr["notify_payslip"],
        "notify_resignation": fr["notify_resignation"],
        "notify_doc_expiry": fr["notify_doc_expiry"],
        "session_timeout": fr["session_timeout"],
        "working_days": (_gset[0] if _gset else "Mon,Tue,Wed,Thu,Fri").split(","),
        "standard_working_days": standard_working_days_per_month(),
        "company_name": _gset[1] if _gset else "",
        "timezone": _gset[2] if _gset else "Asia/Kolkata",
        # salary rules from company features
        "late_deduction_pct": fr["late_deduction_pct"],
        "half_day_deduction_pct": fr["half_day_deduction_pct"],
        "grace_minutes": fr["grace_minutes"],
        "holiday_pay": fr["holiday_pay"],
        "leave_pay": fr["leave_pay"],
        "shift_start": fr["shift_start"],
        "shift_half": fr["shift_half"],
        "shift_end": fr["shift_end"],
    }

    # Resolve salary/shift display values: company-specific overrides global
    def _td_str(v):
        if v is None:
            return None
        if isinstance(v, str):
            return v[:5]
        if isinstance(v, datetime.timedelta):
            t = int(v.total_seconds())
            return "%02d:%02d" % (t // 3600, (t % 3600) // 60)
        if isinstance(v, datetime.time):
            return v.strftime("%H:%M")
        return str(v)[:5]

    _default_shift = cfg.shift_rules()
    _co_shift_start = _td_str(fr.get("shift_start")) or _default_shift.start.strftime("%H:%M")
    _co_shift_half = _td_str(fr.get("shift_half")) or _default_shift.half.strftime("%H:%M")
    _co_shift_end = _td_str(fr.get("shift_end")) or _default_shift.end.strftime("%H:%M")

    cursor.execute(
        "SELECT r.id, r.year, r.month, r.status, r.total_amount, r.employee_count, "
        "r.prepared_at, r.approved_by, r.approved_at, "
        "COALESCE(SUM(CASE WHEN i.status='missing_bank_details' THEN 1 ELSE 0 END), 0) AS missing_count "
        "FROM salary_disbursement_runs r "
        "LEFT JOIN salary_disbursement_items i ON i.run_id = r.id "
        "GROUP BY r.id, r.year, r.month, r.status, r.total_amount, r.employee_count, "
        "r.prepared_at, r.approved_by, r.approved_at "
        "ORDER BY r.prepared_at DESC LIMIT 24"
    )
    disbursement_runs = cursor.fetchall()
    cursor.execute("SELECT 1 FROM payout_bank_config LIMIT 1")
    payout_configured = cursor.fetchone() is not None
    from utils.payout_utils import payout_provider_configured as _ppc
    payout_provider_ready = _ppc()

    # Seats & Billing (Razorpay) -- was its own standalone page
    # (blueprints/seats.py's seats_page(), templates/seat_checkout.html);
    # embedded here so "Billing" behaves the same as every other Finances
    # sub-section (switchSub, no full page nav) instead of navigating away.
    # seats_page() itself now just redirects here (same retirement pattern
    # as /email_config's GET redirect to /settings?tab=email). paid_employee_
    # slots comes from `co`, already injected into every template by
    # app.py's inject_company() context processor -- no need to fetch it
    # again here.
    from utils.plan_limits import get_billing_snapshot, format_price_inr
    from utils.razorpay_utils import razorpay_configured as _razorpay_configured, payments_enabled, test_mode
    _billing_snapshot = get_billing_snapshot(g.tenant_db)
    auto_debit_status = _billing_snapshot["auto_debit"]
    invoices = [
        {**inv, "amount_display": format_price_inr(inv["amount_paise"]),
         "rate_display": format_price_inr(inv["rate_paise"]) if inv["rate_paise"] else None}
        for inv in _billing_snapshot["invoices"]
    ]
    razorpay_is_configured = _razorpay_configured()

    # Trial/subscription lifecycle (blueprints/trial_billing.py) --
    # att_master.tenants, not this tenant's own schema, same cross-schema
    # join every other billing-lock/status read in this app already does
    # (app.py's inject_billing_lock_status()).
    from database import get_master_db
    from utils.helpers import coerce_datetime
    _mconn = get_master_db()
    _mcur = _mconn.cursor(buffered=True)
    _mcur.execute(
        "SELECT trial_end_date, subscription_status FROM tenants WHERE db_name=%s",
        (g.tenant_db,)
    )
    _trial_row = _mcur.fetchone()
    _mcur.close()
    _mconn.close()
    trial_end_date = coerce_datetime(_trial_row[0]) if _trial_row else None
    subscription_status = _trial_row[1] if _trial_row else "active"

    cursor.close()
    db.close()
    return render_template("settings.html",
                           disbursement_runs=disbursement_runs,
                           payout_configured=payout_configured,
                           payout_provider_ready=payout_provider_ready,
                           auto_debit_status=auto_debit_status,
                           invoices=invoices,
                           razorpay_configured=razorpay_is_configured,
                           payments_enabled=payments_enabled(),
                           razorpay_test_mode=razorpay_is_configured and test_mode(),
                           trial_end_date=trial_end_date,
                           subscription_status=subscription_status,
                           tab=tab,
                           admin_recovery_email=admin_recovery_email,
                           company_code=company_code,
                           total_employees=total_employees,
                           active_employees=active_employees,
                           total_departments=total_departments,
                           total_shifts=total_shifts,
                           companies=companies,
                           company_shifts=company_shifts,
                           company_breaks=company_breaks,
                           shifts=shift_rows,
                           emp_list=emp_list,
                           breaks=breaks,
                           salaries=salaries,
                           ann_list=ann_list,
                           pending_leaves=pending_leaves,
                           pending_resignations=pending_resignations,
                           pending_tickets=pending_tickets,
                           saved=request.args.get("saved") == "1",
                           active_nav="settings",
                           default_start=_co_shift_start,
                           default_half=_co_shift_half,
                           default_end=_co_shift_end,
                           now_month=company_today().month,
                           now_year=company_today().year,
                           default_onboarding_tpl=default_onboarding_tpl,
                           onboarding_templates=onboarding_templates,
                           late_deduction_pct=round(fr["late_deduction_pct"], 1),
                           half_day_deduction_pct=round(fr["half_day_deduction_pct"], 1),
                           grace_minutes=fr["grace_minutes"],
                           holiday_pay=fr["holiday_pay"],
                           leave_pay=fr["leave_pay"],
                           auth_config={
                               "face_enabled": fr["face_auth_enabled"],
                               "qr_enabled": fr["qr_enabled"],
                               "fingerprint_enabled": fr["fingerprint_enabled"],
                               "location_enabled": fr["geo_enabled"],
                               "employee_password_auth": True,
                               "app_gps_checkin": app_gps_checkin_allowed(get_auth_config()),
                           },
                           features=features,
                           )


@admin_views_bp.route("/save_default_onboarding_template", methods=["POST"])
@role_required("admin")
def save_default_onboarding_template():
    tpl_id = request.form.get("default_onboarding_template_id") or None
    if tpl_id == "0" or tpl_id == "":
        tpl_id = None
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE company_settings SET default_onboarding_template_id=%s", (tpl_id,))
    db.commit()
    cursor.close()
    db.close()
    flash("Default onboarding template saved.", "success")
    return redirect(tpath("/onboarding?tab=templates"))


@admin_views_bp.route("/save_salary_rules", methods=["POST"])
@role_required("admin")
def save_salary_rules():
    try:
        # parse_finite_float: a "nan" here used to be clamped to 100% (QA SEC-13).
        late_pct = max(0.0, min(100.0, parse_finite_float(request.form.get("late_deduction_pct", 10))))
        half_pct = max(0.0, min(100.0, parse_finite_float(request.form.get("half_day_deduction_pct", 50))))
        grace_min = max(0, min(120, int(request.form.get("grace_minutes", 15))))
    except (ValueError, TypeError):
        flash("Invalid values.", "error")
        return redirect(tpath("/settings?tab=payroll"))
    holiday_pay = request.form.get("holiday_pay", "paid")
    leave_pay = request.form.get("leave_pay", "exclude")
    if holiday_pay not in ("paid", "unpaid"):
        holiday_pay = "paid"
    if leave_pay not in ("exclude", "absent"):
        leave_pay = "exclude"
    shift_start_raw = request.form.get("shift_start", "").strip()
    shift_half_raw = request.form.get("shift_half", "").strip()
    shift_end_raw = request.form.get("shift_end", "").strip()
    # Working Days lives here (Payroll Rules), not Company Profile, since it's
    # what actually drives payroll/attendance day-counting -- see
    # utils/attendance_utils.py's company_working_weekdays(). It's a plain
    # company_settings column, not one of the active_cid multi-company
    # features _upsert_co_features manages, so it's updated directly in both
    # branches below.
    _VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    w_days_raw = request.form.getlist("working_days")
    w_days_set = set(w_days_raw)
    if w_days_set and not w_days_set.issubset(_VALID_DAYS):
        flash("Invalid working days selected.", "error")
        return redirect(tpath("/settings?tab=payroll"))
    w_days = ",".join(d for d in w_days_raw if d in _VALID_DAYS) or "Mon,Tue,Wed,Thu,Fri"
    active_cid = session.get("active_company_id")
    if active_cid:
        _fields = {
            "late_deduction_pct": late_pct, "half_day_deduction_pct": half_pct,
            "grace_minutes": grace_min, "holiday_pay": holiday_pay, "leave_pay": leave_pay,
        }
        if shift_start_raw and shift_half_raw and shift_end_raw:
            _fields.update({"shift_start": shift_start_raw, "shift_half": shift_half_raw,
                            "shift_end": shift_end_raw})
        _upsert_co_features(active_cid, _fields)
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("UPDATE company_settings SET working_days=%s", (w_days,))
        db.commit()
        cursor.close()
        db.close()
    else:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "UPDATE company_settings SET late_deduction_pct=%s, half_day_deduction_pct=%s, "
            "grace_minutes=%s, holiday_pay=%s, leave_pay=%s, working_days=%s",
            (late_pct, half_pct, grace_min, holiday_pay, leave_pay, w_days)
        )
        if shift_start_raw and shift_half_raw and shift_end_raw:
            cursor.execute(
                "UPDATE company_settings SET shift_start=%s, shift_half=%s, shift_end=%s",
                (shift_start_raw, shift_half_raw, shift_end_raw)
            )
        db.commit()
        cursor.close()
        db.close()
    invalidate_settings_cache()
    flash("Salary rules saved.", "success")
    return redirect(tpath("/settings?tab=payroll"))


@admin_views_bp.route("/toggle_auth_method", methods=["POST"])
@role_required("admin")
def toggle_auth_method():
    method = request.form.get("method", "")
    enabled = request.form.get("enabled", "0") == "1"
    if method not in _TOGGLE_COLUMN_MAP:
        flash("Invalid authentication method.", "danger")
        return redirect(tpath("/settings?tab=attendance"))
    column = _TOGGLE_COLUMN_MAP[method]
    label = _TOGGLE_LABEL_MAP[method]
    active_cid = session.get("active_company_id")
    # Map old column names to company_feature_settings column names
    _cfs_map = {"face_enabled": "face_auth_enabled", "location_enabled": "geo_enabled",
                "employee_password_auth": None,  # password auth stays global
                "app_gps_checkin": None}  # read by /api/employee/checkin via get_auth_config()
    cfs_col = _cfs_map.get(column, column)
    if active_cid and cfs_col:
        _upsert_co_feature(active_cid, cfs_col, 1 if enabled else 0)
    else:
        _VALID_CS_TOGGLE = frozenset(_TOGGLE_COLUMN_MAP.values())
        if column not in _VALID_CS_TOGGLE:
            flash("Invalid setting.", "danger")
            return redirect(tpath("/settings?tab=attendance"))
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        _update_company_setting_flag(cursor, column, 1 if enabled else 0)
        db.commit()
        cursor.close()
        db.close()
    # The check-in endpoints read these through the cached auth config;
    # without this a change only applied once the cache expired.
    invalidate_settings_cache()
    state = "enabled" if enabled else "disabled"
    flash(f"{label} {state}.", "success")
    return redirect(tpath("/settings?tab=attendance"))


@admin_views_bp.route("/toggle_fingerprint", methods=["POST"])
@role_required("admin")
def toggle_fingerprint():
    enabled = request.form.get("enabled", "0") == "1"
    active_cid = session.get("active_company_id")
    if active_cid:
        _upsert_co_feature(active_cid, "fingerprint_enabled", 1 if enabled else 0)
    else:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("UPDATE company_settings SET fingerprint_enabled=%s", (1 if enabled else 0,))
        db.commit()
        cursor.close()
        db.close()
    state = "enabled" if enabled else "disabled"
    flash(f"Fingerprint authentication {state}.", "success")
    return redirect(tpath("/settings?tab=attendance"))


@admin_views_bp.route("/save_company_code", methods=["POST"])
@role_required("admin")
def save_company_code():
    code = request.form.get("company_code", "").strip().upper()[:10]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE company_settings SET company_code=%s", (code,))
    db.commit()
    cursor.close()
    db.close()
    flash(f"Company code set to '{code}'.", "success")
    return redirect(tpath("/settings?tab=company"))


@admin_views_bp.route("/save_company_info", methods=["POST"])
@role_required("admin")
def save_company_info():
    import pytz as _pytz
    _VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    name = request.form.get("company_name", "").strip()[:200]
    code = request.form.get("company_code", "").strip().upper()[:10]
    timezone = request.form.get("timezone", "Asia/Kolkata").strip()
    w_days_raw = request.form.getlist("working_days")
    if timezone not in _pytz.all_timezones_set:
        flash("Invalid timezone selected.", "danger")
        return redirect(tpath("/settings?tab=company"))
    w_days_set = set(w_days_raw)
    if w_days_set and not w_days_set.issubset(_VALID_DAYS):
        flash("Invalid working days selected.", "danger")
        return redirect(tpath("/settings?tab=company"))
    w_days = ",".join(d for d in w_days_raw if d in _VALID_DAYS)
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE company_settings SET company_name=%s, company_code=%s, timezone=%s, working_days=%s",
        (name, code, timezone, w_days or "Mon,Tue,Wed,Thu,Fri")
    )
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    flash("Company info saved.", "success")
    return redirect(tpath("/settings?tab=company"))


@admin_views_bp.route("/toggle_feature", methods=["POST"])
@role_required("admin")
def toggle_feature():
    allowed = {
        "face_auth_enabled", "geo_enabled", "qr_enabled", "pin_enabled",
        "fingerprint_enabled", "biometric_enabled",
        "notify_leave", "notify_payslip", "notify_resignation", "notify_doc_expiry",
    }
    data = request.get_json(force=True) or {}
    feature = data.get("feature", "")
    value = 1 if data.get("value") else 0
    if feature not in allowed:
        return jsonify({"ok": False, "error": "unknown feature"}), 400
    active_cid = session.get("active_company_id")
    # Explicit allowlist maps feature name → exact DB column (no dynamic interpolation)
    _CS_COL_MAP = {
        "face_auth_enabled": "face_auth_enabled",
        "geo_enabled": "geo_enabled",
        "qr_enabled": "qr_enabled",
        "pin_enabled": "pin_enabled",
        "fingerprint_enabled": "fingerprint_enabled",
        "biometric_enabled": "biometric_enabled",
        "notify_leave": "notify_leave",
        "notify_payslip": "notify_payslip",
        "notify_resignation": "notify_resignation",
        "notify_doc_expiry": "notify_doc_expiry",
    }
    cs_col = _CS_COL_MAP.get(feature)
    if not cs_col:
        return jsonify({"ok": False, "error": "unknown feature"}), 400
    if active_cid:
        _upsert_co_feature(active_cid, cs_col, value)
    else:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        _update_company_setting_flag(cursor, cs_col, value)
        db.commit()
        cursor.close()
        db.close()
    return jsonify({"ok": True})


@admin_views_bp.route("/save_geo_radius", methods=["POST"])
@role_required("admin")
def save_geo_radius():
    try:
        radius = int(request.form.get("geo_radius", 100))
        if not (50 <= radius <= 5000):
            raise ValueError
    except (ValueError, TypeError):
        flash("Geo radius must be between 50 and 5000 metres.", "danger")
        return redirect(tpath("/settings?tab=attendance"))
    # Office lat/lon are optional -- both blank means "not configured yet",
    # which utils/attendance_utils.py's is_within_office_range() treats as
    # geofencing being a no-op regardless of the location_enabled toggle.
    lat_raw = request.form.get("office_lat", "").strip()
    lon_raw = request.form.get("office_lon", "").strip()
    office_lat = office_lon = None
    if lat_raw or lon_raw:
        try:
            office_lat = float(lat_raw)
            office_lon = float(lon_raw)
            if not (-90 <= office_lat <= 90 and -180 <= office_lon <= 180):
                raise ValueError
        except (ValueError, TypeError):
            flash("Office location must be a valid latitude/longitude pair.", "danger")
            return redirect(tpath("/settings?tab=attendance"))
    active_cid = session.get("active_company_id")
    if active_cid:
        _upsert_co_feature(active_cid, "geo_radius", radius)
    else:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("UPDATE company_settings SET geo_radius=%s", (radius,))
        db.commit()
        cursor.close()
        db.close()
    # Office coordinates are always schema-wide (one physical office per
    # tenant), regardless of which sub-company is active -- unlike radius,
    # which follows the existing per-company_id pattern above.
    if lat_raw or lon_raw:
        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute("UPDATE company_settings SET office_lat=%s, office_lon=%s", (office_lat, office_lon))
        db.commit()
        cursor.close()
        db.close()
    flash("Attendance settings saved.", "success")
    return redirect(tpath("/settings?tab=attendance"))


# ── Settings (Bearer-token API) ──────────────────────────────────────────────
# Bearer twins of the session-only settings routes above -- mobile's
# SettingsScreen used to show hardcoded fallback text for all of this (no
# fetch at all) and every Save button was a no-op alert. These make that
# real. Company/geofence/feature-toggle writes are admin-only, matching
# role_required("admin") on their web equivalents; company_id-scoped
# multi-company support (active_cid in save_geo_radius/toggle_feature
# above) is intentionally not replicated here since mobile has no company
# switcher to begin with -- these always act on the schema-wide row.

@admin_views_bp.route("/api/settings", methods=["GET"])
@api_required
def api_settings_get():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT * FROM company_settings LIMIT 1")
    row = cursor.fetchone()
    settings = {}
    if row and cursor.description:
        cols = [d[0].lower() for d in cursor.description]
        for col, val in zip(cols, row):
            if col in ("id", "company_logo"):
                continue
            settings[col] = str(val) if hasattr(val, "isoformat") else val
    cursor.close()
    db.close()
    return jsonify({"ok": True, "settings": settings})


@admin_views_bp.route("/api/settings/company", methods=["POST"])
@api_required
@api_role_required("admin")
def api_settings_save_company():
    import pytz as _pytz
    _VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    data = request.get_json(silent=True) or {}
    name = (data.get("company_name") or "").strip()[:200]
    code = (data.get("company_code") or "").strip().upper()[:10]
    timezone = (data.get("timezone") or "Asia/Kolkata").strip()
    w_days_raw = data.get("working_days") or []
    if not isinstance(w_days_raw, list):
        w_days_raw = [d.strip() for d in str(w_days_raw).split(",") if d.strip()]
    if timezone not in _pytz.all_timezones_set:
        return jsonify({"ok": False, "msg": "Invalid timezone selected."}), 400
    w_days_set = set(w_days_raw)
    if w_days_set and not w_days_set.issubset(_VALID_DAYS):
        return jsonify({"ok": False, "msg": "Invalid working days selected."}), 400
    w_days = ",".join(d for d in w_days_raw if d in _VALID_DAYS)

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE company_settings SET company_name=%s, company_code=%s, timezone=%s, working_days=%s",
        (name, code, timezone, w_days or "Mon,Tue,Wed,Thu,Fri")
    )
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    return jsonify({"ok": True, "msg": "Company info saved."})


@admin_views_bp.route("/api/settings/geo_radius", methods=["POST"])
@api_required
@api_role_required("admin")
def api_settings_save_geo_radius():
    data = request.get_json(silent=True) or {}
    try:
        radius = int(data.get("geo_radius", 100))
        if not (50 <= radius <= 5000):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Geo radius must be between 50 and 5000 metres."}), 400

    office_lat = office_lon = None
    lat_raw = str(data.get("office_lat") or "").strip()
    lon_raw = str(data.get("office_lon") or "").strip()
    if lat_raw or lon_raw:
        try:
            office_lat = float(lat_raw)
            office_lon = float(lon_raw)
            if not (-90 <= office_lat <= 90 and -180 <= office_lon <= 180):
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"ok": False, "msg": "Office location must be a valid latitude/longitude pair."}), 400

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE company_settings SET geo_radius=%s", (radius,))
    if lat_raw or lon_raw:
        cursor.execute("UPDATE company_settings SET office_lat=%s, office_lon=%s", (office_lat, office_lon))
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True, "msg": "Attendance settings saved."})


@admin_views_bp.route("/api/settings/toggle_feature", methods=["POST"])
@api_required
@api_role_required("admin")
def api_settings_toggle_feature():
    allowed = {
        "face_auth_enabled", "geo_enabled", "qr_enabled", "pin_enabled",
        "fingerprint_enabled", "biometric_enabled",
        "notify_leave", "notify_payslip", "notify_resignation", "notify_doc_expiry",
    }
    data = request.get_json(silent=True) or {}
    feature = data.get("feature", "")
    value = 1 if data.get("value") else 0
    if feature not in allowed:
        return jsonify({"ok": False, "msg": "Unknown feature."}), 400
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    _update_company_setting_flag(cursor, feature, value)
    db.commit()
    cursor.close()
    db.close()
    return jsonify({"ok": True})


@admin_views_bp.route("/api/settings/salary_rules", methods=["POST"])
@api_required
@api_role_required("admin")
def api_settings_save_salary_rules():
    data = request.get_json(silent=True) or {}
    try:
        late_pct = max(0.0, min(100.0, float(data.get("late_deduction_pct", 10))))
        half_pct = max(0.0, min(100.0, float(data.get("half_day_deduction_pct", 50))))
        grace_min = max(0, min(120, int(data.get("grace_minutes", 15))))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Invalid values."}), 400
    holiday_pay = data.get("holiday_pay", "paid")
    leave_pay = data.get("leave_pay", "exclude")
    if holiday_pay not in ("paid", "unpaid"):
        holiday_pay = "paid"
    if leave_pay not in ("exclude", "absent"):
        leave_pay = "exclude"

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE company_settings SET late_deduction_pct=%s, half_day_deduction_pct=%s, "
        "grace_minutes=%s, holiday_pay=%s, leave_pay=%s",
        (late_pct, half_pct, grace_min, holiday_pay, leave_pay)
    )
    db.commit()
    cursor.close()
    db.close()
    invalidate_settings_cache()
    return jsonify({"ok": True, "msg": "Salary rules saved."})


# save_security_settings retired: the Security tab is now the MFA-gated
# hub above (api_security_settings_session_timeout does the same DB write,
# JSON-based, reachable only after the step-up gate reveals the row).


# ── Roles & Access (per-company RBAC) ─────────────────────────────────────────
# A small module of its own, admin-only (the same gate as the rest of
# /settings): Hierarchy (the role tree and who is under whom), Roles (create;
# one page per role), Access matrix (every role x module), Assignments (who
# holds which role, who they report to) and a Team page per role-holder.
# They edit the roles / role_permissions tables utils.helpers.rbac reads.
# superadmin is never shown and can't be changed; the 'admin' role is kept at
# full access by set_permissions() so no company locks itself out.
_MODULE_LABELS = {
    "employees": "Employees", "attendance": "Attendance", "payroll": "Salary & Payslips",
    "leaves": "Leaves & Holidays", "announcements": "Announcements", "tickets": "Tickets",
    "overtime": "OT & Comp-off", "performance": "Performance", "onboarding": "Onboarding",
    "resignations": "Resignations", "reports": "Analytics & Reports", "admin_tools": "Admin Tools",
    "organization": "Organization",
}
# Where a form sends the admin back to (?next=...) -- a fixed list, never a URL.
_ROLES_PAGES = {
    "hierarchy": "/settings/roles", "list": "/settings/roles/list", "access": "/settings/roles/access",
    "assignments": "/settings/roles/assignments",
}


def _roles_redirect(page=None, role_key=None, employee_id=None):
    if role_key:
        return redirect(tpath(f"/settings/roles/role/{role_key}"))
    if employee_id:
        return redirect(tpath(f"/settings/roles/team/{employee_id}"))
    return redirect(tpath(_ROLES_PAGES.get(page or request.form.get("next", ""), "/settings/roles")))


def _roles_page(template, section, **ctx):
    """Render one Roles & Access page with what every page's header needs."""
    roles = list_roles()
    return render_template(
        template, active_nav="roles", roles_section=section,
        rbac_roles=roles, rbac_tree=role_tree(roles), rbac_modules=RBAC_MODULES,
        module_labels=_MODULE_LABELS, rbac_base_types=VALID_BASE_TYPES, **ctx,
    )


def _flash_hierarchy_effects(capped=(), lowered=()):
    if capped:
        flash("Some access was set to the most the role it reports to allows: "
              + ", ".join(_MODULE_LABELS.get(m, m) if isinstance(m, str) else
                          f"{m[0]} – {_MODULE_LABELS.get(m[1], m[1])}" for m in capped) + ".", "info")
    if lowered:
        flash("Roles below it were lowered to fit: " + ", ".join(lowered) + ".", "info")


@admin_views_bp.route("/settings/roles")
@role_required("admin")
def settings_roles():
    """Roles & Access -> Hierarchy: the role tree and who is under whom."""
    return _roles_page("roles_hierarchy.html", "hierarchy",
                       people=people_hierarchy(), rbac_permissions=all_role_permissions(),
                       rbac_assignments=employees_by_role())


@admin_views_bp.route("/settings/roles/list")
@role_required("admin")
def settings_roles_list():
    """Roles & Access -> Roles: create a role; every role in hierarchy order."""
    return _roles_page("roles_list.html", "list", rbac_permissions=all_role_permissions(),
                       rbac_assignments=employees_by_role())


@admin_views_bp.route("/settings/roles/role/<role_key>")
@role_required("admin")
def settings_role_detail(role_key):
    """One role: its details, Reports to, data scope, access and people."""
    roles = list_roles()
    role = next((r for r in roles if r["role_key"] == role_key and not r["is_system"]), None)
    if role is None:
        flash("Role not found.", "error")
        return _roles_redirect("list")
    from utils.helpers.rbac import _descendant_keys
    from utils.helpers.db import _db
    with _db() as (cur, _conn):
        below = _descendant_keys(cur, role_key)
    return _roles_page("role_detail.html", "list", role=role,
                       rbac_permissions=all_role_permissions(), ceiling=role_ceilings().get(role_key, {}),
                       members=employees_by_role().get(role_key, []),
                       parent_choices=[r for r in role_tree(roles) if r["role_key"] != role_key
                                       and r["role_key"] not in below])


@admin_views_bp.route("/settings/roles/access")
@role_required("admin")
def settings_roles_access():
    """Roles & Access -> Access matrix: every role x every module."""
    return _roles_page("roles_access.html", "access", rbac_permissions=all_role_permissions(),
                       ceilings=role_ceilings())


@admin_views_bp.route("/settings/roles/assignments")
@role_required("admin")
def settings_roles_assignments():
    """Roles & Access -> Assignments: who holds which role, and who they
    report to."""
    employees = list_employees_with_roles()
    from utils.helpers.db import _db
    with _db() as (cur, _conn):
        cur.execute("SELECT employee_id, COALESCE(manager_id,'') FROM employees")
        managers = dict(cur.fetchall())
    holders = [e for e in employees if e["access_role"]]
    return _roles_page("roles_assignments.html", "assignments", rbac_employees=employees,
                       managers=managers, holders=holders)


@admin_views_bp.route("/settings/roles/team/<employee_id>")
@role_required("admin")
def settings_roles_team(employee_id):
    """One role-holder's team: who is attached to them, and attach more."""
    employees = list_employees_with_roles()
    person = next((e for e in employees if e["employee_id"] == employee_id and e["access_role"]), None)
    if person is None:
        flash("That person doesn't hold a role.", "error")
        return _roles_redirect("assignments")
    from utils.helpers.db import _db
    with _db() as (cur, _conn):
        cur.execute("SELECT employee_id, COALESCE(manager_id,''), COALESCE(assigned_hr_username,'') "
                    "FROM employees")
        links = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    team = team_of(employee_id)
    # Everyone above them (walking up Reporting manager / Assigned HR) --
    # attaching one of those would loop the hierarchy.
    above, frontier = set(), [p for p in links.get(employee_id, ("", "")) if p]
    while frontier:
        p = frontier.pop()
        if p in above or p == employee_id:
            continue
        above.add(p)
        frontier += [q for q in links.get(p, ("", "")) if q]
    direct = [dict(e, via_manager=links.get(e["employee_id"], ("", ""))[0] == employee_id,
                   via_hr=links.get(e["employee_id"], ("", ""))[1] == employee_id)
              for e in employees
              if employee_id in links.get(e["employee_id"], ("", "")) and e["employee_id"] != employee_id]
    candidates = [e for e in employees if e["employee_id"] != employee_id
                  and e["employee_id"] not in above
                  and links.get(e["employee_id"], ("", ""))[0] != employee_id]
    return _roles_page("roles_team.html", "assignments", person=person, direct=direct,
                       team_size=len(team), candidates=candidates)


@admin_views_bp.route("/roles/create", methods=["POST"])
@role_required("admin")
def rbac_create_role():
    try:
        # Every new role is 'custom': its dashboard is whatever access it gets.
        key = create_role(request.form.get("display_name", ""), "custom",
                          request.form.get("parent_role_key", ""), request.form.get("description", ""),
                          request.form.get("data_scope", "company"))
        flash("Role created. It starts with no access — choose what it can do below.", "success")
        return _roles_redirect(role_key=key)
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect("list")


@admin_views_bp.route("/roles/update", methods=["POST"])
@role_required("admin")
def rbac_update_role():
    role_key = request.form.get("role_key", "")
    try:
        lowered = update_role(role_key, request.form.get("display_name", ""),
                              request.form.get("parent_role_key", ""), request.form.get("description", ""),
                              request.form.get("data_scope", "company"))
        flash("Role saved.", "success")
        _flash_hierarchy_effects(lowered=lowered)
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect(role_key=role_key)


@admin_views_bp.route("/roles/rename", methods=["POST"])
@role_required("admin")
def rbac_rename_role():
    try:
        rename_role(request.form.get("role_key", ""),
                    request.form.get("display_name", ""))
        flash("Role renamed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect()


@admin_views_bp.route("/roles/delete", methods=["POST"])
@role_required("admin")
def rbac_delete_role():
    try:
        delete_role(request.form.get("role_key", ""))
        flash("Role deleted. Roles that reported to it now report to the role above it.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
        return _roles_redirect(role_key=request.form.get("role_key", ""))
    return _roles_redirect("list")


@admin_views_bp.route("/roles/assign", methods=["POST"])
@role_required("admin")
def rbac_assign_role():
    """Give a new hire a dashboard, promote/demote (change role), or remove
    an employee's admin access entirely; optionally set who they report to
    (their Reporting manager, which places them in that person's team).
    role_key='' removes access."""
    employee_id = request.form.get("employee_id", "")
    role_key = request.form.get("role_key", "")
    try:
        saved_role, emp_name = assign_employee_role(employee_id, role_key)
        if saved_role:
            role_label = next(
                (r["display_name"] for r in list_roles() if r["role_key"] == saved_role),
                saved_role
            )
            flash(f"{emp_name} is now assigned the “{role_label}” role.", "success")
        else:
            flash(f"{emp_name}'s access role was removed — they'll see the regular employee dashboard.",
                  "success")
        if "reports_to" in request.form:
            reports_to = request.form.get("reports_to", "")
            from utils.helpers.db import _db
            with _db() as (cur, conn):
                cur.execute("SELECT COALESCE(manager_id,'') FROM employees WHERE employee_id=%s", (employee_id,))
                current = (cur.fetchone() or ("",))[0]
                if not reports_to and current:
                    cur.execute("UPDATE employees SET manager_id=NULL WHERE employee_id=%s", (employee_id,))
                    conn.commit()
            if reports_to and reports_to != current:
                attach_to(reports_to, employee_id)
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect("assignments")


@admin_views_bp.route("/roles/permissions", methods=["POST"])
@role_required("admin")
def rbac_save_permissions():
    role_key = request.form.get("role_key", "")
    # One <input name="perm_<module>"> per module in the grid.
    perms = {m: request.form.get("perm_" + m, "none") for m in RBAC_MODULES}
    try:
        capped, lowered = set_permissions(role_key, perms)
        flash("Access updated.", "success")
        _flash_hierarchy_effects(capped, lowered)
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect(role_key=role_key)


@admin_views_bp.route("/roles/matrix", methods=["POST"])
@role_required("admin")
def rbac_save_matrix():
    """Save the whole Access matrix: one field per cell, cell_<role>__<module>."""
    custom = [r["role_key"] for r in list_roles() if not r["is_system"]]
    grid = {rk: {m: request.form.get(f"cell_{rk}__{m}", "none") for m in RBAC_MODULES}
            for rk in custom if f"row_{rk}" in request.form}
    try:
        capped, lowered = set_all_permissions(grid)
        flash("Access matrix saved.", "success")
        _flash_hierarchy_effects(capped, lowered)
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect("access")


@admin_views_bp.route("/roles/team/attach", methods=["POST"])
@role_required("admin")
def rbac_team_attach():
    holder = request.form.get("holder_id", "")
    try:
        name = attach_to(holder, request.form.get("employee_id", ""))
        flash(f"{name} now reports to them and is part of their team.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect(employee_id=holder)


@admin_views_bp.route("/roles/team/detach", methods=["POST"])
@role_required("admin")
def rbac_team_detach():
    holder = request.form.get("holder_id", "")
    try:
        name = detach_from(holder, request.form.get("employee_id", ""))
        flash(f"{name} no longer reports to them.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _roles_redirect(employee_id=holder)
