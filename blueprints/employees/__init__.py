# -*- coding: utf-8 -*-
"""Employees blueprint -- CRUD, photos, QR codes, ID cards.

Package layout (split from one module; routes register in this order):
  manage         admin actions, edit/delete, employee profile, employee info API
  directory      employee list, departments, employee detail page
  registration   add employee (web), welcome credentials email, employee-ID generation
  photos         reference photo upload, QR codes, photo serving
  id_cards       ID card rendering (default and custom templates) and routes
  api            JSON API for the mobile app: list/register/detail/edit/delete, org chart
"""
from extensions import app
from flask import Blueprint

employees_bp = Blueprint("employees", __name__)

UPLOAD_FOLDER = app.config["UPLOAD_FOLDER"]


# Route modules -- importing them registers their routes on the blueprint above.
from . import manage, directory, registration, photos, id_cards, api  # noqa: E402,F401

# Re-exported so `blueprints.employees.<name>` keeps resolving for existing callers.
from .manage import (  # noqa: E402,F401
    admin_action,
    api_employee_info,
    delete_employee,
    edit_employee,
    edit_employee_page,
    employee_profile,
)
from .directory import (  # noqa: E402,F401
    api_departments,
    employee_detail,
    view_employees,
)
from .registration import (  # noqa: E402,F401
    _max_emp_seq,
    _next_emp_id,
    _send_welcome_credentials_email,
    _split_emp_id,
    add_employee_page,
    generate_emp_id,
)
from .photos import (  # noqa: E402,F401
    admin_view_qr,
    my_photo,
    regenerate_qr,
    serve_dataset,
    update_employee_photo,
)
from .id_cards import (  # noqa: E402,F401
    _IDC_BLUE,
    _IDC_DARK,
    _IDC_DGRAY,
    _IDC_GOLD,
    _IDC_LGRAY,
    _IDC_MGRAY,
    _IDC_MID,
    _IDC_PALE,
    _IDC_RED,
    _IDC_WHITE,
    _IDC_WORK_MODE_LABELS,
    _ID_CARD_TEXT_FIELDS,
    _build_id_card_buf,
    _idc_blood_drop,
    _idc_box_bg_color,
    _idc_box_text,
    _idc_center_text,
    _idc_combine,
    _idc_contrast_color,
    _idc_fmt_shift_time,
    _idc_font,
    _idc_footer_contact,
    _idc_header_logo_and_name,
    _idc_parse_color,
    _idc_qr_image,
    _idc_safe_text,
    _idc_shift_timing_text,
    _idc_static_path,
    _idc_text_width,
    _idc_work_mode_text,
    _idc_wrap_text,
    _render_custom_side,
    _render_default_back,
    _render_default_front,
    admin_id_card,
    admin_view_id_card,
    api_employee_id_card,
)
from .api import (  # noqa: E402,F401
    api_delete_employee,
    api_edit_employee,
    api_employee_detail,
    api_employees,
    api_org_chart,
    api_register_employee,
)
