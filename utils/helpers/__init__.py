# -*- coding: utf-8 -*-
"""Shared utility helpers used across multiple blueprints.

Package layout (split out of one 1,900-line module; every name is still
importable as utils.helpers.<name>):
  validation     input validation and coercion (employee IDs, datetimes, ints, floats)
  urls           tenant-aware paths, static asset cache-busting, safe redirects
  http           response helpers: JSON-vs-HTML negotiation, the error page renderer
  crypto         field encryption at rest (PII, pay amounts, email/phone) -- fail-secure key bootstrap
  db             DB context manager, audit log, in-app notifications
  settings       company settings, auth config and feature flags (with TTL caches), company time zone
  uploads        upload validation, malware scanning (ClamAV), logo / application-document storage
  scoping        company / HR / manager scoping WHERE fragments and ownership checks
  notifications  holiday/announcement fan-out, employee sidebar info, pending-approval counts
  gates          employee-registration gates: email domain, trial cap, paid seat cap
"""


# Submodules in dependency order. crypto runs the ENCRYPTION_KEY fail-secure check on import.
from . import validation, urls, http, crypto, db, settings, uploads, scoping, notifications, gates, rbac  # noqa: E402,F401,E501

# Re-exported so `utils.helpers.<name>` keeps resolving for existing callers.
from .validation import (  # noqa: E402,F401
    _EMP_ID_RE,
    _SAFE_IDENT_RE,
    coerce_datetime,
    emp_id_param,
    int_param,
    parse_finite_float,
    validate_emp_id,
)
from .urls import (  # noqa: E402,F401
    _APP_URL,
    _STATIC_ASSET_CACHE,
    _STATIC_ASSET_CACHE_LOCK,
    _safe_app_url,
    _safe_redirect,
    _safe_referrer_redirect,
    company_url,
    employee_login_url,
    self_service_emp_id,
    self_service_return_path,
    static_url,
    tpath,
)
from .http import (  # noqa: E402,F401
    _error_page,
    wants_json_response,
)
from .crypto import (  # noqa: E402,F401
    _ENCRYPTION_KEY,
    _pii_cipher,
    amount,
    contact_lookup_keys,
    decrypt_pii,
    decrypt_pii_date,
    encrypt_amount,
    encrypt_contact,
    encrypt_pii,
)
from .db import (  # noqa: E402,F401
    _audit,
    _create_notification,
    _db,
)
from .settings import (  # noqa: E402,F401
    PLATFORM_TZ,
    _AUTH_CONFIG_DEFAULTS,
    _CACHE_MISS,
    _COMPANIES_CACHE_TTL,
    _CO_CACHE_TTL,
    _ONBOARDING_CACHE_TTL,
    _VALID_CFS_COLS,
    _auth_cache,
    _cache_get,
    _cache_set,
    _co_cache,
    _co_expired,
    _companies_cache,
    _company_tzinfo,
    _onboarding_cache,
    _read_global_features,
    _redis_clear_prefix,
    _rule_settings,
    _settings_lock,
    _tenant_cache_key,
    _upsert_co_feature,
    _upsert_co_features,
    allowed_checkin_combos,
    app_gps_checkin_allowed,
    company_now,
    company_today,
    get_auth_config,
    get_co_features,
    get_companies_list,
    get_company_settings,
    get_overdue_onboarding_count,
    invalidate_companies_cache,
    invalidate_settings_cache,
    platform_today,
)
from .uploads import (  # noqa: E402,F401
    _ALLOWED_IMG_EXT,
    _ALLOWED_IMG_MIME,
    _ALLOWED_MIME_MAP,
    _APPLICATION_DOC_KINDS,
    _CLAMAV_HOST,
    _CLAMAV_PORT,
    _IMG_MAGIC,
    _LOGO_NAME_RE,
    _MALWARE_SCAN_ENABLED,
    _MAX_DOC_SIZE_MB,
    _MAX_PHOTO_SIZE_MB,
    _clamav_available,
    _clamd_lib,
    _scan_for_malware,
    _validate_image_file,
    _validate_upload,
    save_application_document,
    save_uploaded_logo,
)
from .scoping import (  # noqa: E402,F401
    co_scope_column,
    co_scope_subquery,
    get_pending_action_counts,
    hr_scope_column,
    hr_scope_denied,
    hr_scope_subquery,
    manager_scope_column,
    manager_scope_denied,
    manager_scope_subquery,
)
from .notifications import (  # noqa: E402,F401
    _PENDING_COUNTS_CACHE_TTL,
    _email_announcement,
    _pending_counts_cache,
    get_employee_sidebar_info,
    get_pending_counts,
    invalidate_pending_counts_cache,
    notify_new_holiday,
    post_announcement,
)
from .gates import (  # noqa: E402,F401
    TRIAL_EMPLOYEE_CAP,
    _DOMAIN_RE,
    _tenant_is_trialing,
    add_employee_seat_cap_check,
    clean_email_domain,
    validate_email_domain_format,
    validate_employee_email_domain,
)
from .rbac import (  # noqa: E402,F401
    RBAC_MODULES,
    VALID_ACCESS,
    VALID_BASE_TYPES,
    all_role_permissions,
    assign_employee_role,
    base_type_for_role,
    can_access,
    defined_role_base_type,
    create_role,
    delete_role,
    employees_by_role,
    get_role_permissions,
    list_employees_with_roles,
    list_roles,
    rename_role,
    set_permissions,
)
