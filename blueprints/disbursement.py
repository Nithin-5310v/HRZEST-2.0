# -*- coding: utf-8 -*-
"""Automated salary disbursement.

Admin uploads the company's OWN bank account (source of funds -- private,
per-tenant data, never shared), sets a recurring pay date, and a daily
scheduled job (registered in wsgi.py) prepares a disbursement batch a few
days ahead of that date. The admin must then explicitly approve the batch --
this is never fully unattended -- before anything (even the stub payout
call) executes. See utils/payout_utils.py's module docstring for why the
actual bank-transfer call is a stub: no real payout provider is connected
today, and this must never claim money moved when it didn't.

Modeled directly on two existing patterns rather than inventing new ones:
  - payout_bank_config's save/step-up shape mirrors email_config
    (blueprints/payroll.py's email_config() route, utils/email_utils.py's
    get_email_config()).
  - The approve/reject two-route, status-guarded state machine mirrors
    blueprints/platform_admin.py's tenant-application approve/reject.
"""
import datetime
import calendar
from flask import Blueprint, request, session, redirect, jsonify, flash

from database import get_db_connection, get_master_db
from extensions import app_log, log_security_event, app
from utils.auth import (
    role_required, require_payout_2fa, payout_settings_step_up_refresh,
    payout_settings_step_up_clear,
)
from utils.totp import verify_totp_code, mark_totp_enabled
from utils.helpers import tpath, _audit, encrypt_pii, decrypt_pii, company_today
from utils.email_utils import send_email_async
from utils.salary_utils import build_salary_slip_html
from utils.payout_utils import (
    get_payout_bank_config, validate_ifsc, mask_account_number,
    execute_payout, payout_provider_configured,
)

disbursement_bp = Blueprint("disbursement", __name__)


# ── Bank details (2FA step-up gated, same posture as Email Settings) ────────

@disbursement_bp.route("/api/payout/bank_config")
@role_required("admin")
@require_payout_2fa
def api_get_payout_bank_config():
    cfg = get_payout_bank_config()
    if not cfg:
        return jsonify({"ok": True, "config": None})
    return jsonify({"ok": True, "config": {
        "account_holder_name": cfg["account_holder_name"],
        "bank_name": cfg["bank_name"],
        "account_number_masked": mask_account_number(cfg["account_number"]),
        "ifsc_code": cfg["ifsc_code"],
        "disbursement_day_of_month": cfg["disbursement_day_of_month"],
        "approval_lead_days": cfg["approval_lead_days"],
        "enabled": cfg["enabled"],
        "payout_provider_configured": payout_provider_configured(),
    }})


@disbursement_bp.route("/api/payout/bank_config", methods=["POST"])
@role_required("admin")
@require_payout_2fa
def api_save_payout_bank_config():
    data = request.get_json(silent=True) or {}
    holder = (data.get("account_holder_name") or "").strip()
    bank_name = (data.get("bank_name") or "").strip()
    account_number = (data.get("account_number") or "").strip()
    ifsc = (data.get("ifsc_code") or "").strip().upper()
    try:
        day = int(data.get("disbursement_day_of_month", 1))
        lead = int(data.get("approval_lead_days", 2))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Invalid schedule values."}), 400
    enabled = 1 if data.get("enabled") else 0

    if not holder or not bank_name:
        return jsonify({"ok": False, "msg": "Account holder name and bank name are required."}), 400
    ifsc_ok, ifsc_err = validate_ifsc(ifsc)
    if not ifsc_ok:
        return jsonify({"ok": False, "msg": ifsc_err}), 400
    if not (1 <= day <= 28):
        return jsonify({"ok": False, "msg": "Disbursement day must be between 1 and 28."}), 400
    if lead < 0 or lead > 27:
        return jsonify({"ok": False, "msg": "Approval lead time must be between 0 and 27 days."}), 400

    # Blank or the masked sentinel means "leave the stored account number
    # unchanged" -- same convention as email_config's password field
    # (blueprints/payroll.py's email_config() route), since the GET above
    # only ever returns a masked value, never plaintext.
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    if account_number and not account_number.startswith("*"):
        encrypted_account = encrypt_pii(account_number)
    else:
        cursor.execute("SELECT account_number FROM payout_bank_config ORDER BY id DESC LIMIT 1")
        prev = cursor.fetchone()
        if not prev:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Account number is required."}), 400
        encrypted_account = prev[0]

    cursor.execute("DELETE FROM payout_bank_config")
    cursor.execute(
        "INSERT INTO payout_bank_config "
        "(account_holder_name, bank_name, account_number, ifsc_code, "
        " disbursement_day_of_month, approval_lead_days, enabled) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (holder, bank_name, encrypted_account, ifsc, day, lead, enabled)
    )
    db.commit()
    cursor.close()
    db.close()
    _audit("save_payout_bank_config", "payout_bank_config", None,
           f"bank={bank_name} ifsc={ifsc} day={day} enabled={enabled}")
    log_security_event("payout.bank_config_saved", "Company payout bank account saved/updated",
                       level="INFO", identifier=session.get("admin_username"))
    return jsonify({"ok": True})


@disbursement_bp.route("/api/payout/verify-2fa", methods=["POST"])
@role_required("admin")
def api_payout_verify_2fa():
    """Step-up gate itself -- opens the same rolling window require_payout_2fa
    checks, exactly mirroring /api/settings/verify-2fa's shape but keyed to
    its own session flag (see utils/auth.py's payout_settings_step_up_*).
    Only valid once TOTP is already enrolled -- see api_payout_2fa_enable
    below for the first-time-enrollment path (QR code -> confirm code),
    which this deliberately does NOT double as (require_totp_code's
    require_enabled=True would just reject every code from an admin who
    has never scanned a QR code at all, with no way forward)."""
    username = session.get("admin_username")
    code = (request.get_json(silent=True) or {}).get("code", "")
    if not verify_totp_code(username, code, require_enabled=True):
        log_security_event("access.denied", "Invalid 2FA code for Payout Settings step-up",
                           level="WARNING", identifier=username)
        return jsonify({"ok": False, "msg": "Invalid verification code"}), 401
    payout_settings_step_up_refresh()
    log_security_event("auth.step_up_verified", "Admin completed 2FA step-up for Payout Settings",
                       level="INFO", identifier=username)
    return jsonify({"ok": True})


@disbursement_bp.route("/api/payout/2fa/enable", methods=["POST"])
@role_required("admin")
def api_payout_2fa_enable():
    """First-time enrollment confirmation: the admin scanned the QR code
    from GET /api/settings/2fa/setup (TOTP enrollment is one secret per
    admin account, shared across every gated area -- Email Settings,
    Payout Settings, etc. -- so that existing setup/QR endpoint is reused
    as-is here, not duplicated) and now proves possession with one live
    code before totp_enabled flips on. Mirrors admin_views.py's
    api_email_2fa_enable() exactly, except it opens the PAYOUT step-up
    window on success, not the email one -- confirming enrollment here
    must unlock the screen the admin was actually trying to get into."""
    username = session.get("admin_username")
    code = (request.get_json(silent=True) or {}).get("code", "")
    if not verify_totp_code(username, code, require_enabled=False):
        log_security_event("auth.2fa_enroll_failed", "TOTP enrollment confirmation failed (Payout Settings)",
                           level="WARNING", identifier=username)
        return jsonify({"ok": False, "msg": "Invalid code"}), 400
    mark_totp_enabled(username)
    payout_settings_step_up_refresh()
    log_security_event("auth.2fa_enrolled", "Admin enabled TOTP 2FA (confirmed via Payout Settings)",
                       level="INFO", identifier=username)
    return jsonify({"ok": True})


@disbursement_bp.route("/api/payout/lock", methods=["POST"])
@role_required("admin")
def api_payout_lock():
    payout_settings_step_up_clear()
    return jsonify({"ok": True})


# ── Disbursement runs: approve, reject ────────────────────────────────────────
# The run list itself is rendered server-side into /settings' template
# context by blueprints/admin_views.py (its own query against
# salary_disbursement_runs) -- there's no separate JSON list endpoint here,
# only the two actions below.


@disbursement_bp.route("/disbursement/<int:run_id>/approve", methods=["POST"])
@role_required("admin")
@require_payout_2fa
def approve_disbursement_run(run_id):
    """Authorizes real money movement -- the one explicit human click the
    plan requires before anything executes.

    "AND status='pending_approval'" on the UPDATE below is load-bearing,
    not decorative -- mirrors billing.py's verify_payment() exactly: two
    near-simultaneous POSTs for the same run (a double-click, a browser
    retry, two admin tabs) must not both pass the SELECT's status check
    above and each go on to call _process_disbursement_run(), which would
    call execute_payout() for every item a second time. Only the request
    whose UPDATE actually flips a row (cursor.rowcount == 1) proceeds to
    process the run; the loser gets an idempotent "already being
    processed" result instead of a second payout pass."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT status, year, month FROM salary_disbursement_runs WHERE id=%s", (run_id,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        flash("Disbursement run not found.", "error")
        return redirect(tpath("/settings?tab=payroll"))
    status, year, month = row
    if status != "pending_approval":
        cursor.close()
        db.close()
        flash("This run isn't awaiting approval.", "error")
        return redirect(tpath("/settings?tab=payroll"))

    approver = session.get("admin_username")
    cursor.execute(
        "UPDATE salary_disbursement_runs SET status='processing', approved_by=%s, approved_at=NOW() "
        "WHERE id=%s AND status='pending_approval'",
        (approver, run_id)
    )
    won_race = cursor.rowcount == 1
    db.commit()
    cursor.close()
    db.close()

    if not won_race:
        flash("This run is already being processed by another request.", "info")
        return redirect(tpath("/settings?tab=payroll"))

    log_security_event("payout.disbursement_approved",
                       f"Disbursement run {run_id} ({year}-{month:02d}) approved for processing",
                       level="INFO", identifier=approver)

    _process_disbursement_run(run_id)

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT status FROM salary_disbursement_runs WHERE id=%s", (run_id,))
    final_status = cursor.fetchone()[0]
    cursor.close()
    db.close()
    if final_status == "completed":
        flash("Disbursement run completed -- salaries transferred and payslips emailed.", "success")
    else:
        flash(
            "Disbursement run finished with status '" + final_status + "'. "
            + ("No payout provider is connected, so no money was actually transferred -- see the run detail."
               if not payout_provider_configured() else "Some items failed -- see the run detail."),
            "error" if final_status == "failed" else "info",
        )
    return redirect(tpath("/settings?tab=payroll"))


@disbursement_bp.route("/disbursement/<int:run_id>/reject", methods=["POST"])
@role_required("admin")
def reject_disbursement_run(run_id):
    reason = request.form.get("reason", "").strip()[:500]
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT status FROM salary_disbursement_runs WHERE id=%s", (run_id,))
    row = cursor.fetchone()
    if not row or row[0] != "pending_approval":
        cursor.close()
        db.close()
        flash("This run can't be cancelled from its current state.", "error")
        return redirect(tpath("/settings?tab=payroll"))
    # Same race-guard shape as approve_disbursement_run() above -- avoids a
    # duplicate audit-log/security-event pair if this fires twice.
    cursor.execute(
        "UPDATE salary_disbursement_runs SET status='cancelled' WHERE id=%s AND status='pending_approval'",
        (run_id,)
    )
    won_race = cursor.rowcount == 1
    db.commit()
    cursor.close()
    db.close()
    if not won_race:
        flash("This run was already cancelled or approved by another request.", "info")
        return redirect(tpath("/settings?tab=payroll"))
    _audit("cancel_disbursement_run", "salary_disbursement_runs", run_id, reason)
    log_security_event("payout.disbursement_cancelled", f"Disbursement run {run_id} cancelled",
                       level="INFO", identifier=session.get("admin_username"), reason=reason)
    flash("Disbursement run cancelled.", "success")
    return redirect(tpath("/settings?tab=payroll"))


def _process_disbursement_run(run_id):
    """Executes an already-approved run: calls the (stub) payout for every
    item, emails a payslip ONLY for items that actually succeeded, and
    finalizes the run's status. Never marks an item 'sent' or the run
    'completed' unless execute_payout() genuinely reported success -- see
    utils/payout_utils.py's module docstring."""
    from_bank = get_payout_bank_config()
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT id, employee_id, amount, status FROM salary_disbursement_items WHERE run_id=%s",
        (run_id,)
    )
    items = cursor.fetchall()
    cursor.execute("SELECT year, month FROM salary_disbursement_runs WHERE id=%s", (run_id,))
    year, month = cursor.fetchone()
    cursor.close()
    db.close()

    all_ok = True
    for item_id, emp_id, amount, item_status in items:
        # Already flagged missing at preparation time (_prepare_one_tenant)
        # -- the admin saw this before approving. Nothing changed since
        # then, so don't re-derive the same outcome, just count it.
        if item_status == "missing_bank_details":
            all_ok = False
            continue

        db2 = get_db_connection()
        cur2 = db2.cursor(buffered=True)
        cur2.execute(
            "SELECT name, email, COALESCE(bank_account,''), COALESCE(bank_name,''), "
            "COALESCE(bank_ifsc,'') FROM employees WHERE employee_id=%s",
            (emp_id,)
        )
        emp_row = cur2.fetchone()
        cur2.close()
        db2.close()
        if not emp_row:
            all_ok = False
            _update_item(item_id, "failed", error="Employee record not found")
            continue
        name, email, enc_account, bank_name, ifsc = emp_row
        to_bank = {
            "account_holder_name": name, "bank_name": bank_name,
            "account_number": decrypt_pii(enc_account), "ifsc_code": ifsc,
        }
        if not from_bank or not to_bank["account_number"]:
            # Only reachable if bank details changed between prep and
            # approval (e.g. an employee's account was removed after this
            # run was prepared) -- the normal missing-details case is
            # already caught by the item_status check above.
            all_ok = False
            _update_item(item_id, "missing_bank_details",
                         error="Missing source or destination bank details")
            continue

        ok, ref_or_error = execute_payout(from_bank, to_bank, amount, emp_id)
        if not ok:
            all_ok = False
            status = "stub_not_configured" if not payout_provider_configured() else "failed"
            _update_item(item_id, status, error=ref_or_error)
            continue

        _update_item(item_id, "sent", reference=ref_or_error)
        if email:
            try:
                _send_disbursement_payslip(emp_id, name, email, year, month, amount)
                _mark_item_email_sent(item_id)
            except Exception:
                app_log.error("disbursement: payslip email failed for %s", emp_id, exc_info=True)

    final_status = "completed" if all_ok and items else "failed"
    db3 = get_db_connection()
    cur3 = db3.cursor(buffered=True)
    cur3.execute("UPDATE salary_disbursement_runs SET status=%s WHERE id=%s", (final_status, run_id))
    if final_status == "completed":
        # Keeps salary_report.html's existing lock UI consistent regardless
        # of whether a month was processed via the old manual "Email All"
        # button or this automated path.
        cur3.execute(
            "INSERT INTO payroll_runs (year, month, processed_by, email_count) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (year, month) DO UPDATE SET processed_at=NOW(), processed_by=EXCLUDED.processed_by, "
            "email_count=EXCLUDED.email_count",
            (year, month, "disbursement-automation", len(items))
        )
    db3.commit()
    cur3.close()
    db3.close()


def _update_item(item_id, status, reference=None, error=None):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE salary_disbursement_items SET status=%s, payout_reference=%s, error_message=%s WHERE id=%s",
        (status, reference, (error or "")[:500] if error else None, item_id)
    )
    db.commit()
    cursor.close()
    db.close()


def _mark_item_email_sent(item_id):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("UPDATE salary_disbursement_items SET email_sent=1 WHERE id=%s", (item_id,))
    db.commit()
    cursor.close()
    db.close()


def _send_disbursement_payslip(emp_id, name, email, year, month, amount):
    """Reuses the existing payslip template/queued-send path -- deliberately
    NOT sent for stub/failed items (see _process_disbursement_run) so an
    unpaid employee never receives anything that could be misread as
    payment confirmation."""
    from utils.email_utils import get_email_config
    email_cfg = get_email_config()
    if not email_cfg:
        return
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT COALESCE(s.salary_per_day,0), COALESCE(s.monthly_ctc,0), COALESCE(s.basic_pct,50),
               COALESCE(e.role,''), COALESCE(e.department,''),
               COALESCE(e.pan_number,''), COALESCE(e.uan_number,''),
               COALESCE(e.bank_account,''), COALESCE(e.bank_name,'')
        FROM employees e LEFT JOIN salary_config s ON e.employee_id = s.employee_id
        WHERE e.employee_id=%s
    """, (emp_id,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return
    spd, ctc, basic_pct, role, dept, pan, uan, bank_account, bank_name = row
    salary_data = {"spd": float(spd), "monthly_ctc": float(ctc), "basic_pct": basic_pct,
                   "net": float(amount)}
    month_name = datetime.date(year, month, 1).strftime("%B %Y")
    html_body = build_salary_slip_html(
        name, emp_id, email, month_name, year, month, salary_data,
        emp_designation=role, emp_dept=dept,
        pan=decrypt_pii(pan), uan=decrypt_pii(uan),
        bank_account=decrypt_pii(bank_account), bank_name=bank_name,
    )
    send_email_async(email, f"Payslip -- {month_name}", html_body, email_cfg)


# ── Scheduled preparation job (registered in wsgi.py) ────────────────────────

def prepare_pending_disbursements():
    """APScheduler cron job -- no request/app context of its own, matching
    check_tenant_billing()'s shape in blueprints/billing_dunning.py."""
    try:
        with app.app_context():
            _run_disbursement_prep()
    except Exception:
        app_log.exception("disbursement: unhandled error in daily prep")


def _run_disbursement_prep():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    # A company whose schema lacks tables would read another company's
    # salary data through public -- skipped until repaired (QA SEC-07).
    cur.execute("SELECT id, company_name, subdomain, db_name FROM tenants "
                "WHERE status='active' AND schema_missing_tables IS NULL")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    for tenant_id, company_name, subdomain, db_name in rows:
        try:
            _prepare_one_tenant(db_name)
        except Exception:
            app_log.exception(f"disbursement: prep failed for tenant '{subdomain}'")


def _prepare_one_tenant(tenant_schema):
    from flask import g as _g
    from blueprints.payroll import compute_salary_data_for_month

    prev_tenant_db = getattr(_g, "tenant_db", None)
    try:
        _g.tenant_db = tenant_schema
        cfg = get_payout_bank_config()
        if not cfg or not cfg["enabled"]:
            return

        today = company_today()
        # Prepare for THIS month if today is within approval_lead_days of
        # the scheduled day, else it's not time yet.
        target_day = min(cfg["disbursement_day_of_month"], calendar.monthrange(today.year, today.month)[1])
        scheduled_date = today.replace(day=target_day)
        days_until = (scheduled_date - today).days
        if not (0 <= days_until <= cfg["approval_lead_days"]):
            return

        db = get_db_connection()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "SELECT id FROM salary_disbursement_runs WHERE year=%s AND month=%s",
            (today.year, today.month)
        )
        already = cursor.fetchone()
        if already:
            cursor.close()
            db.close()
            return

        salary_data = compute_salary_data_for_month(today.year, today.month)
        if not salary_data:
            cursor.close()
            db.close()
            return

        total = round(sum(e["net"] for e in salary_data), 2)
        cursor.execute(
            "INSERT INTO salary_disbursement_runs (year, month, status, total_amount, employee_count) "
            "VALUES (%s,%s,'pending_approval',%s,%s) RETURNING id",
            (today.year, today.month, total, len(salary_data))
        )
        run_id = cursor.fetchone()[0]
        missing_count = 0
        for entry in salary_data:
            # Checked and flagged HERE, at preparation time, not left to
            # surface only as a processing failure after the admin has
            # already approved -- every employee due salary this month
            # needs to actually be payable, and the admin should see who
            # isn't BEFORE clicking approve, not after.
            cursor.execute(
                "SELECT COALESCE(bank_account,''), COALESCE(bank_ifsc,'') FROM employees WHERE employee_id=%s",
                (entry["emp_id"],)
            )
            row = cursor.fetchone()
            enc_acct, ifsc = row if row else ("", "")
            last4 = ""
            has_bank_details = bool(enc_acct) and bool(ifsc)
            if enc_acct:
                plain = decrypt_pii(enc_acct)
                last4 = plain[-4:] if plain else ""
                has_bank_details = has_bank_details and bool(plain)
            item_status = "pending" if has_bank_details else "missing_bank_details"
            if not has_bank_details:
                missing_count += 1
            cursor.execute(
                "INSERT INTO salary_disbursement_items (run_id, employee_id, amount, bank_last4, status) "
                "VALUES (%s,%s,%s,%s,%s)",
                (run_id, entry["emp_id"], entry["net"], last4, item_status)
            )
        db.commit()
        cursor.close()
        db.close()
        log_security_event("payout.disbursement_prepared",
                           f"Disbursement run {run_id} prepared for {today.year}-{today.month:02d} "
                           f"({len(salary_data)} employees, total {total}, {missing_count} missing bank details) "
                           "-- awaiting admin approval",
                           level="INFO", identifier="system")
    finally:
        _g.tenant_db = prev_tenant_db
