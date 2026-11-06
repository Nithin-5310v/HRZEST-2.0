# -*- coding: utf-8 -*-
"""admin_views: SMTP settings behind the email 2FA gate"""
from database import get_db_connection
from extensions import app_log, limiter, log_security_event
from flask import jsonify, request, session
from utils.auth import (
    EMAIL_2FA_WINDOW_SEC,
    admin_required,
    check_password_hash,
    email_settings_step_up_clear,
    email_settings_step_up_refresh,
    require_email_2fa,
    role_required,
    security_settings_step_up_clear,
)
from utils.email_utils import get_email_config, send_email_smtp
from utils.helpers import decrypt_pii, encrypt_pii
from utils.totp import (
    get_or_create_admin_totp_secret,
    mark_totp_enabled,
    reset_admin_totp_secret,
    totp_qr_data_uri,
    verify_totp_code,
)
from . import admin_views_bp


# ── Email Settings 2FA gate ────────────────────────────────────────────────────
# The SMTP form used to be rendered server-side with the (encrypted) password
# bound straight into a <input value="...">, which leaked ciphertext into the
# page source and would silently re-encrypt that ciphertext as the "new"
# password on every unrelated save (see fixed POST /email_config below). The
# whole Email tab is now API-driven and gated behind TOTP step-up instead.

@admin_views_bp.route("/api/settings/2fa/setup")
@admin_required
def api_email_2fa_setup():
    """Called when the admin has no TOTP enrolled yet -- returns a QR code to
    scan with an authenticator app. Idempotent: re-generates the same secret
    (doesn't rotate it) until enrollment is confirmed via /2fa/enable."""
    username = session.get("admin_username")
    secret, enabled = get_or_create_admin_totp_secret(username)
    if enabled:
        return jsonify({"ok": True, "already_enabled": True})
    return jsonify({
        "ok": True, "already_enabled": False,
        "qr_code": totp_qr_data_uri(username, secret),
        "secret": secret,  # shown once, for manual entry if the QR can't be scanned
    })


@admin_views_bp.route("/api/settings/2fa/enable", methods=["POST"])
@admin_required
def api_email_2fa_enable():
    """Confirms enrollment: the admin must prove they actually captured the
    secret by entering one live code before totp_enabled flips on."""
    username = session.get("admin_username")
    code = (request.get_json(silent=True) or {}).get("code", "")
    if not verify_totp_code(username, code, require_enabled=False):
        log_security_event("auth.2fa_enroll_failed", "TOTP enrollment confirmation failed",
                           level="WARNING", identifier=username)
        return jsonify({"ok": False, "msg": "Invalid code"}), 400
    mark_totp_enabled(username)
    # Confirming enrollment with a live code IS proof of possession -- as good
    # as verify-2fa. Without this, the frontend's "unlock immediately after
    # enabling" step would hit the require_email_2fa gate with no session
    # flag set yet, get a 403, and fall back to re-showing the enrollment
    # screen -- which looks exactly like "my code keeps getting rejected"
    # even though every code was valid the whole time.
    email_settings_step_up_refresh()
    log_security_event("auth.2fa_enrolled", "Admin enabled TOTP 2FA for Email Settings",
                       level="INFO", identifier=username)
    return jsonify({"ok": True})


@admin_views_bp.route("/api/settings/2fa/reset", methods=["POST"])
@admin_required
@limiter.limit("5 per hour")
def api_email_2fa_reset():
    """Re-enrollment for an admin who deleted the entry from their
    authenticator app: without this they can never produce a valid code
    again for any TOTP-gated area (Security hub, SOC, Email Settings), since
    the old secret is gone from their device but still enabled server-side.
    Requires the account password again -- an active session alone isn't
    enough proof to strip an existing MFA factor.

    Logged at ERROR (not WARNING) specifically so it fires the real-time
    security webhook alert alongside a best-effort email to the admin's own
    registered address -- stripping an MFA factor is exactly the kind of rare,
    high-consequence action that deserves an out-of-band notice, so the
    legitimate owner finds out even if a stolen session + phished password
    did this, not them."""
    username = session.get("admin_username")
    password = (request.get_json(silent=True) or {}).get("password", "")
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT password, email FROM admin_users WHERE username=%s", (username,))
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row or not check_password_hash(row[0], password):
        log_security_event("auth.2fa_reset_denied", "TOTP reset attempt failed password check",
                           level="WARNING", identifier=username)
        return jsonify({"ok": False, "msg": "Incorrect password"}), 401
    admin_email = row[1]
    reset_admin_totp_secret(username)
    email_settings_step_up_clear()
    security_settings_step_up_clear()
    log_security_event("auth.2fa_reset", "Admin reset their TOTP secret for re-enrollment",
                       level="ERROR", identifier=username)
    if admin_email:
        config = get_email_config()
        if config:
            try:
                send_email_smtp(
                    admin_email, "Your two-factor authentication was reset",
                    "<p>The two-factor authentication (TOTP) on your admin account "
                    f"(<b>{username}</b>) was just reset, and the old authenticator "
                    "entry no longer works.</p>"
                    "<p>If you just did this yourself to re-enroll, no action is needed.</p>"
                    "<p><b>If you did not do this</b>, someone may have your password -- "
                    "change it immediately and review the security event log.</p>",
                    config,
                )
            except Exception:
                app_log.error("Failed to send 2FA-reset notification to admin %s", username, exc_info=True)
    return jsonify({"ok": True})


@admin_views_bp.route("/api/settings/verify-2fa", methods=["POST"])
@admin_required
def api_settings_verify_2fa():
    """The step-up gate itself. On a correct code, opens a rolling 15-minute
    window (utils/auth.py:email_settings_step_up_refresh) that /api/settings/
    email and friends require. Session-based, not a separate cookie -- the
    admin session is already HTTP-only/secure per extensions.py's cookie
    config, so a second cookie would add no isolation, just complexity."""
    username = session.get("admin_username")
    code = (request.get_json(silent=True) or {}).get("code", "")
    if not verify_totp_code(username, code, require_enabled=True):
        log_security_event("access.denied", "Invalid 2FA code for Email Settings step-up",
                           level="WARNING", identifier=username)
        return jsonify({"ok": False, "msg": "Invalid verification code"}), 401
    email_settings_step_up_refresh()
    log_security_event("auth.step_up_verified", "Admin completed 2FA step-up for Email Settings",
                       level="INFO", identifier=username)
    return jsonify({"ok": True, "expires_in": EMAIL_2FA_WINDOW_SEC})


@admin_views_bp.route("/api/settings/2fa/lock", methods=["POST"])
@admin_required
def api_settings_lock():
    """Explicit re-lock -- called by the frontend's inactivity timer, and
    available for a manual 'Lock' button. Idempotent."""
    email_settings_step_up_clear()
    return jsonify({"ok": True})


@admin_views_bp.route("/api/settings/email")
@role_required("admin")
@require_email_2fa
def api_get_email_settings():
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    # smtp_pass is deliberately never selected here -- the password can only
    # reach the client via the separate, individually-logged reveal endpoint.
    cursor.execute(
        "SELECT smtp_host, smtp_port, smtp_user, from_name, from_email, "
        "(smtp_pass IS NOT NULL AND smtp_pass != '') AS has_password "
        "FROM email_config ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return jsonify({"ok": True, "config": None, "expires_in": EMAIL_2FA_WINDOW_SEC})
    return jsonify({
        "ok": True,
        "config": {
            "host": row[0], "port": row[1], "user": row[2],
            "password": "********" if row[5] else "",
            "from_name": row[3], "from_email": row[4] or row[2],
        },
        "expires_in": EMAIL_2FA_WINDOW_SEC,
    })


@admin_views_bp.route("/api/settings/email", methods=["POST"])
@role_required("admin")
@require_email_2fa
def api_save_email_settings():
    data = request.get_json(silent=True) or {}
    host = (data.get("host") or "").strip()
    port = data.get("port")
    user = (data.get("user") or "").strip()
    from_name = (data.get("from_name") or "HRzest.com").strip()
    from_email = (data.get("from_email") or "").strip() or user
    password = (data.get("password") or "").strip()
    if not host or not port or not user:
        return jsonify({"ok": False, "msg": "Host, port, and username are required"}), 400
    try:
        port = int(port)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "Port must be a number"}), 400

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    # A masked or blank password means "leave it unchanged" -- only a genuine
    # new value gets (re-)encrypted. This is the fix for the bug where the
    # server-rendered form used to re-save its own displayed ciphertext as
    # the "new" password on every unrelated edit, corrupting it.
    if password and password != "********":
        encrypted_password = encrypt_pii(password)
    else:
        cursor.execute("SELECT smtp_pass FROM email_config ORDER BY id DESC LIMIT 1")
        prev = cursor.fetchone()
        # re-encrypt, not copy: the fetch already decrypted it (database.py)
        encrypted_password = encrypt_pii(decrypt_pii(prev[0])) if prev and prev[0] else ""
    cursor.execute("DELETE FROM email_config")
    cursor.execute(
        "INSERT INTO email_config (smtp_host, smtp_port, smtp_user, smtp_pass, from_name, from_email) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (host, port, user, encrypted_password, from_name, from_email),
    )
    db.commit()
    cursor.close()
    db.close()
    log_security_event("data.update", "Admin updated SMTP configuration",
                       level="INFO", identifier=session.get("admin_username"))
    return jsonify({"ok": True})


@admin_views_bp.route("/api/settings/email/reveal-password", methods=["POST"])
@role_required("admin")
@require_email_2fa
def api_reveal_email_password():
    """Separate from the GET above on purpose: viewing the masked settings
    and revealing the real password are different sensitivity levels, so
    each admin action to actually see the plaintext gets its own audit-log
    line rather than being indistinguishable from a routine page load."""
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT smtp_pass FROM email_config ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row or not row[0]:
        return jsonify({"ok": False, "msg": "No SMTP password set"}), 404
    log_security_event("data.reveal", "Admin revealed SMTP password in Email Settings",
                       level="WARNING", identifier=session.get("admin_username"))
    return jsonify({"ok": True, "password": decrypt_pii(row[0])})
