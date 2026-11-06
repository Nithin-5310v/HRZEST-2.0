# -*- coding: utf-8 -*-
"""helpers: response helpers: JSON-vs-HTML negotiation, the error page renderer"""
from flask import jsonify, request, session
from .urls import static_url


# ── Error page renderer ───────────────────────────────────────────────────────
# This used to render_template("error.html", ...) -- that template doesn't
# exist anywhere in templates/. Every call would have raised
# jinja2.exceptions.TemplateNotFound, turning a 404/403/500 handler into a
# second, unhandled 500. Never triggered because nothing called this copy
# (app.py's own separate, working implementation handled every real error
# page) -- found by checking whether the "weaker" duplicate was even
# functional, not just less-featured, before deciding which one to keep.
# Replaced with app.py's version (session-aware back-navigation, inline
# styling, no template dependency) rather than fixing the missing
# template, since that's what every real error page has actually looked
# like in production.


def wants_json_response():
    """True for API, AJAX and JSON-accepting requests -- they get a JSON error
    body instead of the HTML error page."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
        # A request that posts a JSON body (e.g. the attendance check-in
        # fetch to /attendance) is read as JSON by its caller -- an HTML
        # error page there shows up as "Unexpected token '<'".
        or request.is_json
    )


def _error_page(code, icon, title, subtitle, hint):
    # API clients (the mobile app, the pages' fetch() calls) can't parse an
    # HTML page; same {"ok": False, "msg": ...} shape as every API error
    # (QA ERR-01). Only the generic title goes out -- never exception details.
    if wants_json_response():
        return jsonify({"ok": False, "msg": title}), code
    back_admin = session.get("admin_logged_in")
    back_emp = session.get("employee_id")
    back_link = "/admin" if back_admin else ("/employee_portal" if back_emp else "/")
    back_label = "Go to Admin Dashboard" if back_admin else ("Go to My Portal" if back_emp else "Go to Home")
    # Same landing_v2.css design system as admin_login.html/create_org.html
    # (see admin_login.html's header comment) -- this page used to be the
    # last one still on the old plain blue-and-white style.
    return f"""<!doctype html>
<html lang="en" data-theme="light"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{code} – {title}</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
<link rel="stylesheet" href="{static_url('shared.min.css')}" />
<link rel="stylesheet" href="{static_url('landing_v2.css')}" />
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: var(--font-body); }}
  html, body {{ min-height: 100vh; color: var(--text-main); }}
  body {{
    background: linear-gradient(160deg, #FDF3E3 0%, #F7ECF7 28%, #EAF0FC 56%, var(--bg-secondary) 100%);
    background-attachment: fixed;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }}
  .box {{
    width: 100%; max-width: 460px; text-align: center;
    background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-lg);
    padding: 48px 40px 40px; box-shadow: var(--shadow-lg); position: relative; overflow: hidden;
  }}
  .box::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; background: var(--gradient-brand); }}
  .icon {{
    width: 68px; height: 68px; margin: 0 auto 18px; border-radius: var(--radius-md);
    background: var(--gradient-brand); display: flex; align-items: center; justify-content: center;
    font-size: 32px; box-shadow: 0 8px 24px rgba(79, 70, 229, 0.35);
  }}
  .code {{ font-family: var(--font-heading); font-size: 15px; font-weight: 800; letter-spacing: 2px;
           color: var(--accent-cyan); margin-bottom: 8px; text-transform: uppercase; }}
  .title {{ font-family: var(--font-heading); font-size: 22px; font-weight: 800; color: var(--text-main); margin-bottom: 10px; }}
  .sub {{ font-size: 14px; color: var(--text-muted); margin-bottom: 6px; line-height: 1.6; }}
  .hint {{ font-size: 12.5px; color: var(--text-subtle); margin-bottom: 28px; }}
  .actions {{ display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }}
  a.btn {{
    display: inline-flex; align-items: center; justify-content: center;
    padding: 12px 26px; border-radius: var(--radius-sm); background: var(--gradient-brand);
    color: #fff; font-size: 14px; font-weight: 700; text-decoration: none;
    transition: var(--transition-fast); box-shadow: var(--shadow-md);
  }}
  a.btn:hover {{ box-shadow: var(--shadow-lg); transform: translateY(-1px); }}
  a.sec {{
    display: inline-flex; align-items: center; justify-content: center;
    padding: 12px 22px; border-radius: var(--radius-sm); background: var(--bg-secondary);
    color: var(--text-main); font-size: 14px; font-weight: 600; text-decoration: none;
    transition: var(--transition-fast); border: 1px solid var(--border-color);
  }}
  a.sec:hover {{ background: var(--bg-tertiary); }}
  @media (max-width: 480px) {{ .box {{ padding: 36px 26px 30px; }} }}
</style>
<link rel="stylesheet" href="{static_url('public-theme.css')}" />
</head><body>
<div class="box">
  <div class="icon">{icon}</div>
  <div class="code">Error {code}</div>
  <div class="title">{title}</div>
  <div class="sub">{subtitle}</div>
  <div class="hint">{hint}</div>
  <div class="actions">
    <a href="{back_link}" class="btn">{back_label}</a>
    <a href="javascript:history.back()" class="sec">← Go Back</a>
  </div>
</div>
</body></html>""", code
