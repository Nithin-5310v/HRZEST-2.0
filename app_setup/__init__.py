# -*- coding: utf-8 -*-
"""Flask app wiring split out of app.py: config, templating, tenant
resolution, security guards, CSRF/CSP/headers and error handlers.

Each module registers its hooks on the shared app (extensions.app) when
imported; app.py imports them in a fixed order (see the comment there).
"""
