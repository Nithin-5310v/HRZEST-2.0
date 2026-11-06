# -*- coding: utf-8 -*-
"""Employee portal blueprint -- self-service profile, photos, QR/ID card, check-in APIs.

Package layout (split from one module; routes register in this order):
  profile        self-service profile, bank details, experience, education, photo (web)
  qr_id_card     my QR code and ID card
  portal         employee portal page, my_* shortcuts, change password, portal API
  checkin        mobile check-in, offline punch sync, auth config, QR+face check-in
  self_service_api mobile self-service API: salary, attendance, profile, preferences, history, photo, ID card
  assistant      HR chat assistant and device-risk reporting
"""
from extensions import app
from flask import Blueprint

employee_portal_bp = Blueprint("employee_portal", __name__)

UPLOAD_FOLDER = app.config["UPLOAD_FOLDER"]


def _fmt_t(t):
    if t is None:
        return None
    if hasattr(t, 'strftime'):
        return t.strftime("%H:%M:%S")
    total = int(t.total_seconds())
    return "{:02d}:{:02d}:{:02d}".format(total // 3600, (total % 3600) // 60, total % 60)


# Route modules -- importing them registers their routes on the blueprint above.
from . import profile, qr_id_card, portal, checkin, self_service_api, assistant  # noqa: E402,F401

# Re-exported so `blueprints.employee_portal.<name>` keeps resolving for existing callers.
from .profile import (  # noqa: E402,F401
    add_education_entry,
    add_experience,
    delete_education_entry,
    delete_experience,
    update_my_bank_details,
    update_my_photo,
    update_my_profile,
)
from .qr_id_card import (  # noqa: E402,F401
    _generate_and_persist_qr,
    api_my_qr,
    my_id_card,
    my_qr,
)
from .portal import (  # noqa: E402,F401
    api_employee_change_password,
    api_employee_portal,
    employee_portal,
    my_attendance,
    my_leaves,
    my_profile,
    my_salary,
)
from .checkin import (  # noqa: E402,F401
    _mobile_location_enforced,
    api_employee_auth_config,
    api_employee_checkin,
    api_employee_qr_face_checkin,
    api_employee_sync_punches,
)
from .self_service_api import (  # noqa: E402,F401
    api_add_my_education,
    api_add_my_experience,
    api_delete_my_education,
    api_delete_my_experience,
    api_employee_attendance,
    api_employee_my_id_card,
    api_employee_profile,
    api_employee_salary,
    api_employee_upload_photo,
    api_list_my_education,
    api_list_my_experience,
    api_update_my_bank_details,
    api_update_my_profile,
    api_update_notification_preferences,
)
from .assistant import (  # noqa: E402,F401
    _DEVICE_RISK_KILL_WEIGHT,
    api_employee_chat,
    api_employee_device_risk,
)
