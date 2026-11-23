# -*- coding: utf-8 -*-
"""Request timing for utils/http_telemetry.py.

Imported first among the app_setup modules (see app.py), so its
before_request runs before every other app_setup hook (only extensions.py's
request-ID hook and the rate limiter run earlier) and its after_request runs
after them: the time covers the whole request, and the status recorded is the
one actually sent, including answers given by an earlier hook (an IP ban, a
CSRF refusal, a redirect).
"""
import time

from flask import current_app, g, request

from extensions import _request_tenant, app, app_log
from utils import http_telemetry


@app.before_request
def _telemetry_start():
    g._telemetry_t0 = time.perf_counter()
    if not current_app.testing and http_telemetry.enabled():
        http_telemetry.ensure_flusher()


@app.after_request
def _telemetry_record(response):
    t0 = g.pop("_telemetry_t0", None)
    if t0 is None or not http_telemetry.enabled() or http_telemetry.skipped(request.path):
        return response
    try:
        rule = request.url_rule.rule if request.url_rule is not None else None
        http_telemetry.record(rule, request.method, response.status_code, _request_tenant(),
                              (time.perf_counter() - t0) * 1000.0)
    except Exception as exc:   # telemetry must never break a response
        app_log.warning("request telemetry failed: %s", exc)
    return response
