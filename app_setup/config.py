# -*- coding: utf-8 -*-
"""App-wide constants shared by the request hooks (split out of app.py)."""
# secret_key, session cookie flags, PERMANENT_SESSION_LIFETIME and
# UPLOAD_FOLDER (app.config["UPLOAD_FOLDER"], created on startup) are
# authoritative in extensions.py. Re-exported, never redefined here.
from extensions import UPLOAD_FOLDER  # noqa: F401


_SESSION_MAX_AGE = 8 * 3600  # 8 hours absolute -- stolen cookie cannot be used indefinitely
# How often _resolve_tenant() re-checks tenants.status for an
# already-session-cached tenant. Bounds how long a platform-admin
# suspension (blueprints/platform_admin.py) takes to actually lock out an
# already-logged-in session, rather than never (cookie sessions have no
# server-side store to revoke from outside).
_TENANT_STATUS_RECHECK_SEC = 5 * 60
