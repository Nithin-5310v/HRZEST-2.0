# -*- coding: utf-8 -*-
"""platform_admin: login (password + emailed MFA code) and logout"""
import time
from database import get_master_db
from extensions import app_log, limiter, log_security_event
from flask import current_app, redirect, render_template, request, session
from utils.auth import _mask_identifier, check_password_hash, dummy_password_check
from utils.login_otp import discard_challenge, issue_challenge, verify_challenge
from utils.totp import send_mfa_login_email
from . import _MFA_OTP_TTL_SEC, platform_admin_bp


def _complete_platform_admin_login(username):
    """Build the real session once credentials (and MFA, when required)
    have checked out -- shared by the MFA-verify completion below and the
    direct-login path taken when MANDATORY_PLATFORM_ADMIN_MFA is off."""
    session.clear()
    session["platform_admin_logged_in"] = True
    session["platform_admin_username"] = username
    session["platform_admin_last_activity"] = time.time()
    # Picked up by app.py's _enforce_session_lifetime before_request hook
    # (not gated on admin_logged_in -- it checks this key on every
    # request), giving this identity the same 8-hour absolute session cap
    # as every other login flow, for free.
    session["_session_created"] = time.time()
    session.permanent = True
    log_security_event(
        "auth.platform_admin_login_success",
        f"Platform admin session established for '{username}'",
        level="INFO", identifier=username,
    )
    return redirect("/super_admin")


def _discard_platform_challenge(challenge_id):
    if not challenge_id:
        return
    try:
        conn = get_master_db()
        cur = conn.cursor()
        try:
            discard_challenge(cur, challenge_id)
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        app_log.warning("Could not discard platform admin login challenge: %s", type(exc).__name__)


@platform_admin_bp.route("/super_admin/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes")
def platform_admin_login():
    """Single-page credentials + emailed-OTP MFA login -- one URL, one
    template. Which of the two steps renders is driven entirely by session
    state (platform_mfa_pending), not by a second page: submitting
    username/password here starts the OTP step in place (re-renders this
    same route); submitting otp_code here while that session state is set
    completes it via _complete_platform_admin_login."""
    if session.get("platform_admin_logged_in"):
        return redirect("/super_admin")

    if request.args.get("restart"):
        _discard_platform_challenge(session.get("platform_mfa_challenge_id"))
        session.pop("platform_mfa_pending", None)
        session.pop("platform_mfa_user", None)
        session.pop("platform_mfa_challenge_id", None)
        session.pop("platform_mfa_issued_at", None)
        return redirect("/super_admin/login")

    mfa_pending = bool(session.get("platform_mfa_pending"))
    if mfa_pending:
        issued_at = session.get("platform_mfa_issued_at") or 0
        if (time.time() - issued_at) > _MFA_OTP_TTL_SEC:
            _discard_platform_challenge(session.get("platform_mfa_challenge_id"))
            session.clear()
            return render_template("super_admin_login.html", error="Your code expired. Please log in again.")

    if request.method == "POST" and mfa_pending and "otp_code" in request.form:
        display_user = session.get("platform_mfa_user")
        submitted = (request.form.get("otp_code") or "").strip()
        # The code is checked against the att_master challenge row; the
        # session only ever held the opaque challenge id.
        conn = get_master_db()
        cur = conn.cursor()
        try:
            verified = verify_challenge(cur, session.get("platform_mfa_challenge_id", ""), submitted,
                                        ("platform_admin",))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        if verified:
            return _complete_platform_admin_login(verified[1])

        log_security_event(
            "auth.platform_admin_mfa_failure", "Invalid platform admin login MFA code",
            level="WARNING", identifier=_mask_identifier(display_user),
        )
        return render_template("super_admin_login.html", mfa_step=True, error="Invalid verification code.")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        row = None
        if username:
            conn = get_master_db()
            cur = conn.cursor(buffered=True)
            cur.execute("SELECT password, email FROM platform_admins WHERE username=%s", (username,))
            row = cur.fetchone()
            cur.close()
            conn.close()

        if row and check_password_hash(row[0], password):
            # Local-dev escape hatch, same convention as blueprints/auth.py's
            # MANDATORY_LOGIN_MFA -- defaults True (secure by default), only
            # off when explicitly set in .env. Password is still checked
            # above either way; this only skips the emailed-OTP step.
            if not current_app.config["MANDATORY_PLATFORM_ADMIN_MFA"]:
                return _complete_platform_admin_login(username)

            email = row[1]
            conn = get_master_db()
            cur = conn.cursor()
            try:
                challenge_id, otp_code = issue_challenge(cur, "platform_admin", username, _MFA_OTP_TTL_SEC)
                conn.commit()
            finally:
                cur.close()
                conn.close()
            try:
                send_mfa_login_email(email, username, "Platform Administrator", "", otp_code)
            except Exception as exc:
                app_log.warning("Platform admin MFA email send error: %s", type(exc).__name__)

            session.clear()
            session["platform_mfa_pending"] = True
            session["platform_mfa_user"] = username  # display only
            session["platform_mfa_challenge_id"] = challenge_id
            session["platform_mfa_issued_at"] = time.time()
            return render_template("super_admin_login.html", mfa_step=True)

        if not row:
            dummy_password_check(password)
        log_security_event(
            "auth.platform_admin_login_failed",
            "Failed platform admin login attempt",
            level="WARNING", identifier=_mask_identifier(username),
        )
        return render_template("super_admin_login.html", error="Invalid credentials.")

    return render_template("super_admin_login.html", mfa_step=mfa_pending)


@platform_admin_bp.route("/super_admin/logout", methods=["POST"])
def platform_admin_logout():
    session.clear()
    return redirect("/super_admin/login")
