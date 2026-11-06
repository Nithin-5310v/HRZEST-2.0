# -*- coding: utf-8 -*-
"""Admin views blueprint -- dashboard, settings, companies, analytics, audit.

Bandit B608 audit note (applies to every nosec-marked line in this file):
Bandit flags f-string-built SQL as possible injection. Verified false
positive in every case here -- the interpolated fragment is always one of:
  (a) a hardcoded literal string chosen by a bool (the `_co_sub`/`_co_join`/
      `_co_filter`/`where` pattern, e.g. `"AND company_id=%s" if active_cid
      else ""`) -- never user input;
  (b) a column name from a fixed allowlist dict (`column`/`cs_col`, checked
      against `_TOGGLE_COLUMN_MAP`/`_CS_COL_MAP` before use); or
  (c) a table name iterating a hardcoded Python list literal (`tbl` in
      `related_tables`).
All actual values are always passed as %s-bound params, never interpolated.

Package layout (split from one module; routes register in this order):
  dashboard      admin dashboard, omnisearch, live tiles, attendance chart, MFA-required page, activity log
  settings       settings page, rule/flag/company saves, Bearer-token settings API
  email_settings SMTP settings behind the email 2FA gate
  companies      company switching, company PIN, company CRUD and images
  id_card_templates per-company custom ID card templates
  announcements  announcements, attachments, test email, expiring documents
  analytics      analytics dashboard
  org_chart      org chart page and data, admin tools
  tools          instant SMTP test, internal chat with the platform operator
"""
from flask import Blueprint

_ANN_ALLOWED_EXT = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls', 'xlsx'}

admin_views_bp = Blueprint("admin_views", __name__)

_TOGGLE_COLUMN_MAP = {
    "fingerprint": "fingerprint_enabled",
    "qr": "qr_enabled",
    "face": "face_enabled",
    "location": "location_enabled",
    "password": "employee_password_auth",
    "app_gps": "app_gps_checkin",
}
# company_settings columns the settings toggles write (toggle_auth_method,
# toggle_feature, api_settings_toggle_feature). A column name can't be a
# query parameter, so each column gets one fixed statement, built from these
# constants at import; nothing from a request is ever formatted into SQL
# (QA SEC-12).
_CS_FLAG_COLUMNS = frozenset(_TOGGLE_COLUMN_MAP.values()) | {
    "face_auth_enabled", "geo_enabled", "qr_enabled", "pin_enabled", "fingerprint_enabled",
    "biometric_enabled", "notify_leave", "notify_payslip", "notify_resignation", "notify_doc_expiry",
}
_CS_FLAG_UPDATE_SQL = {col: "UPDATE company_settings SET " + col + "=%s"  # nosec B608 -- constant column names
                       for col in _CS_FLAG_COLUMNS}


def _update_company_setting_flag(cursor, column, value):
    sql = _CS_FLAG_UPDATE_SQL.get(column)
    if sql is None:
        raise ValueError(f"not a company_settings flag column: {column!r}")
    cursor.execute(sql, (value,))


_TOGGLE_LABEL_MAP = {
    "fingerprint": "Fingerprint / Biometric",
    "qr": "QR Code",
    "face": "Face Recognition",
    "location": "Location Verification",
    "password": "Password Login",
    "app_gps": "Location-only App Check-in",
}


# Route modules -- importing them registers their routes on the blueprint above.
from . import dashboard, settings, email_settings, companies, id_card_templates, announcements, org_chart, tools  # noqa: E402, F401

# Re-exported so `blueprints.admin_views.<name>` keeps resolving for existing callers.
from .dashboard import (  # noqa: E402,F401
    _ACTIVITY_LOG_PER_PAGE,
    _load_activity_log,
    activity_log_page,
    admin,
    admin_mfa_required_page,
    api_admin_search,
    attendance_chart_data,
    dashboard_live,
)
from .settings import (  # noqa: E402,F401
    api_settings_get,
    api_settings_save_company,
    api_settings_save_geo_radius,
    api_settings_save_salary_rules,
    api_settings_toggle_feature,
    save_company_code,
    save_company_info,
    save_default_onboarding_template,
    save_geo_radius,
    save_salary_rules,
    settings_page,
    toggle_auth_method,
    toggle_feature,
    toggle_fingerprint,
)
from .email_settings import (  # noqa: E402,F401
    api_email_2fa_enable,
    api_email_2fa_reset,
    api_email_2fa_setup,
    api_get_email_settings,
    api_reveal_email_password,
    api_save_email_settings,
    api_settings_lock,
    api_settings_verify_2fa,
)
from .companies import (  # noqa: E402,F401
    _delete_company_image,
    _save_company_image,
    add_company,
    clear_company,
    delete_company,
    edit_company,
    set_company_pin,
    switch_company,
    view_companies,
)
from .id_card_templates import (  # noqa: E402,F401
    _ID_CARD_FIELD_KEYS,
    id_card_template_editor,
    id_card_template_reset,
    id_card_template_save_positions,
    id_card_template_upload,
)
from .announcements import (  # noqa: E402,F401
    announcements_admin,
    api_expiring_documents,
    download_announcement,
    test_email,
)
from .analytics import (  # noqa: E402,F401
    analytics,
)
from .org_chart import (  # noqa: E402,F401
    admin_tools,
    api_org_chart_data,
    org_chart_page,
)
from .tools import (  # noqa: E402,F401
    _device_owner_kind,
    api_admin_chat_messages,
    api_admin_chat_send,
    api_test_email,
)
