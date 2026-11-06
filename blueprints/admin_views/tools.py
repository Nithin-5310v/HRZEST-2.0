# -*- coding: utf-8 -*-
"""admin_views: instant SMTP test, internal chat with the platform operator"""

from extensions import app_log
from flask import g, jsonify, request, session
from utils import chat_utils
from utils.auth import HR_ROLE, admin_required
from utils.email_utils import get_email_config, send_email_smtp
from utils.graph_mail import GraphMailError
from utils.helpers import company_now
from . import admin_views_bp


# ── Instant SMTP Connection Test Endpoint ─────────────────────────────────────
@admin_views_bp.route("/api/admin/test_email", methods=["POST"])
@admin_required
def api_test_email():
    """Send a diagnostic test email using current SMTP configuration."""
    data = request.get_json() or {}
    target_email = data.get("to_email", "").strip() or request.form.get("to_email", "").strip()

    if not target_email:
        target_email = session.get("admin_email") or session.get("email")

    if not target_email:
        return jsonify({"ok": False, "msg": "Target email address is required."}), 400

    cfg = get_email_config()
    if not cfg or not cfg.get("host"):
        return jsonify({"ok": False, "msg": "No SMTP configuration found in system. Please configure SMTP first."}), 400

    subject = "⚡ SMTP Connection Test -- HRzest.com"
    now_str = company_now().strftime("%d %b %Y, %I:%M %p")
    html_body = f"""
    <div style="font-family: Arial, sans-serif; padding: 24px; background: #f8fafc; border-radius: 12px;">
      <h2 style="color: #16a34a; margin-top: 0;">✅ SMTP Connection Test Successful!</h2>
      <p style="color: #334155; font-size: 14px;">
        Your email server configuration is working cleanly. Automated daily attendance reports and security alerts are operational.
      </p>
      <p style="font-size: 12px; color: #64748b;">
        <strong>Host:</strong> {cfg.get('host')}:{cfg.get('port')}<br>
        <strong>Timestamp:</strong> {now_str}
      </p>
    </div>
    """

    try:
        send_email_smtp(target_email, subject, html_body, cfg)   # raises on failure; returns nothing
        return jsonify({"ok": True, "msg": f"Test email successfully delivered to {target_email}!"})
    except GraphMailError as e:
        # Microsoft's own reason (e.g. "Authorization_RequestDenied") --
        # never the secret or the token -- so the admin can fix the app setup.
        app_log.warning("api_test_email: Microsoft 365 refused: %s", e)
        return jsonify({"ok": False, "msg": f"Microsoft 365 email test failed. {e}"}), 500
    except Exception:
        app_log.exception("api_test_email: SMTP delivery exception")
        return jsonify({"ok": False,
                        "msg": "SMTP test failed. Check the SMTP host, port and credentials, then try again."}), 500


# 'admin' or HR_ROLE for whichever role this admin session actually has --
# used to scope rows owned by this account (e.g. chat_messages.sender_kind
# below) across the shared admin/hr login.
def _device_owner_kind():
    return HR_ROLE if session.get("admin_role") == HR_ROLE else "admin"


# ── Internal chat with the platform operator (utils/chat_utils.py) ─────────
# One shared thread per company -- admin and hr both read/write it, keyed by
# g.tenant_db (this schema's own name), the same join key platform_admin.py
# resolves from its tenants.db_name column.

@admin_views_bp.route("/api/admin/chat/messages", methods=["GET"])
@admin_required
def api_admin_chat_messages():
    chat_utils.mark_read(g.tenant_db, "company")
    return jsonify({"ok": True, "messages": chat_utils.list_messages(g.tenant_db)})


@admin_views_bp.route("/api/admin/chat/send", methods=["POST"])
@admin_required
def api_admin_chat_send():
    data = request.get_json(silent=True) or {}
    sender_kind = _device_owner_kind()  # 'admin' or HR_ROLE, matches chat_messages.sender_kind
    sent = chat_utils.send_message(g.tenant_db, sender_kind, session["admin_username"], data.get("message"))
    if not sent:
        return jsonify({"ok": False, "msg": "Message cannot be empty."}), 400
    return jsonify({"ok": True})
