# -*- coding: utf-8 -*-
"""platform_admin: internal chat with a company's admin/HR"""
from flask import jsonify, request, session
from utils import chat_utils
from . import _platform_admin_required, _tenant_schema_for_id, platform_admin_bp


# ── Internal chat with a company's admin/HR (utils/chat_utils.py) ──────────
# One shared thread per tenant, keyed by that tenant's db_name -- resolved
# fresh from tenant_id on every call rather than trusted from the client, so
# a platform admin can never read/write a thread for a tenant_id that
# doesn't actually exist.


@platform_admin_bp.route("/super_admin/chat/<int:tenant_id>/messages", methods=["GET"])
@_platform_admin_required
def platform_admin_chat_messages(tenant_id):
    row = _tenant_schema_for_id(tenant_id)
    if not row:
        return jsonify({"ok": False, "msg": "Unknown company."}), 404
    schema_name, company_name = row
    chat_utils.mark_read(schema_name, "platform")
    return jsonify({"ok": True, "company_name": company_name, "messages": chat_utils.list_messages(schema_name)})


@platform_admin_bp.route("/super_admin/chat/<int:tenant_id>/send", methods=["POST"])
@_platform_admin_required
def platform_admin_chat_send(tenant_id):
    row = _tenant_schema_for_id(tenant_id)
    if not row:
        return jsonify({"ok": False, "msg": "Unknown company."}), 404
    schema_name, _company_name = row
    data = request.get_json(silent=True) or {}
    username = session["platform_admin_username"]
    sent = chat_utils.send_message(schema_name, "platform_admin", username, data.get("message"))
    if not sent:
        return jsonify({"ok": False, "msg": "Message cannot be empty."}), 400
    return jsonify({"ok": True})
