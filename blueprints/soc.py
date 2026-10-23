# -*- coding: utf-8 -*-
"""SOC console -- /soc.

A standalone security-operations console for the platform operator. It is
deliberately not part of any other dashboard: nothing links to it, it has its
own accounts and session (utils/soc_auth.py), it is excluded from tenant URL
routing (utils/tenant_routing.RESERVED_PATH_SEGMENTS), and every response is
marked no-store / noindex.

  /soc/login      password -> (first time: setup code + enrol authenticator) -> TOTP
  /soc            the console (single page; data over /soc/api/*)
  /soc/api/*      JSON, role-gated: viewer < analyst < admin

Data comes from what the product already records (see utils/soc_queries.py);
actions are in utils/soc_actions.py; log analysis is utils/soc_loganalysis.py.
"""
import datetime
import os

from flask import Blueprint, jsonify, make_response, redirect, render_template, request, send_from_directory, session

from extensions import limiter
from utils import soc_actions, soc_auth, soc_queries
from utils import soc_loganalysis as loganalysis
from utils.soc_auth import soc_required
from utils.soc_util import clamp_int, to_csv

soc_bp = Blueprint("soc", __name__)

_last_integrity = {}


@soc_bp.after_request
def _headers(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _ok(**data):
    return jsonify({"ok": True, **data})


def _err(msg, status=400):
    return jsonify({"ok": False, "msg": msg}), status


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _actor():
    return soc_auth.current_user()["username"]


def _ip():
    return request.remote_addr


def _guard_network():
    """404 (not 403) for sources outside SOC_ALLOWED_CIDRS: the console does not
    admit it exists. Returns a response to send, or None."""
    if not soc_auth.reachable():
        soc_auth.event("soc.ip_denied", "SOC console request refused (outside allow-list or tenant-prefixed)", ip=request.remote_addr)
        return "Not found", 404
    return None


# ── login ────────────────────────────────────────────────────────────
def _login_state():
    """-> (step, pending_user). step: password | setup | enroll | code."""
    user = soc_auth.pending_user()
    if user is None:
        return "password", None
    if user["totp_enabled"]:
        return "code", user
    return ("enroll" if session.get("soc_setup_ok") else "setup"), user


def _render_login(step, user, error=None, status=200):
    ctx = {"step": step, "error": error, "username": user["username"] if user else "", "qr": None, "secret": None}
    if step == "enroll" and user:
        from utils.helpers import decrypt_pii
        secret = decrypt_pii(user["totp_secret"]) if user.get("totp_secret") else soc_auth.new_totp_secret(user["id"])
        ctx["secret"] = secret
        ctx["qr"] = soc_auth.totp_qr(user["username"], secret)
    return render_template("soc/login.html", **ctx), status


@soc_bp.route("/soc/login", methods=["GET", "POST"])
@limiter.limit("20 per 15 minutes", methods=["POST"])
def login():
    blocked = _guard_network()
    if blocked:
        return blocked
    if soc_auth.current_user():
        return redirect("/soc")
    if request.method == "GET":
        step, user = _login_state()
        return _render_login(step, user)

    action = request.form.get("action", "")
    ip = _ip()
    if action == "restart":
        soc_auth.end_session()
        return redirect("/soc/login", 303)

    if action == "password":
        user, code = soc_auth.authenticate(request.form.get("username", ""), request.form.get("password", ""), ip)
        if code == "locked":
            return _render_login("password", None, "Too many failed attempts. Try again in 15 minutes.", 429)
        if user is None:
            return _render_login("password", None, "Invalid username or password.", 401)
        if code == "disabled":
            return _render_login("password", None, "Invalid username or password.", 401)
        soc_auth.start_pending(user)
        return redirect("/soc/login", 303)

    step, user = _login_state()
    if user is None:
        return redirect("/soc/login", 303)
    fresh = soc_auth.get_user_by_id(user["id"])
    if not fresh or not fresh["active"] or fresh["is_locked"]:
        soc_auth.end_session()
        return _render_login("password", None, "Invalid username or password.", 401)

    if action == "setup" and step == "setup":
        if soc_auth.verify_setup_code(fresh, request.form.get("setup_code", "")):
            session["soc_setup_ok"] = True
            return redirect("/soc/login", 303)
        if soc_auth.register_failure(fresh, ip, "bad setup code"):
            soc_auth.end_session()
            return _render_login("password", None, "Too many failed attempts. Try again in 15 minutes.", 429)
        return _render_login("setup", fresh, "That setup code is not valid.", 401)

    if action == "code" and step in ("code", "enroll"):
        enabling = step == "enroll"
        if soc_auth.verify_totp(fresh, request.form.get("code", ""), enabling=enabling):
            fresh = soc_auth.get_user_by_id(fresh["id"])
            soc_auth.establish(fresh, ip)
            if enabling:
                soc_auth.audit(fresh["username"], "mfa_enrolled", ip=ip)
            return redirect("/soc", 303)
        if soc_auth.register_failure(fresh, ip, "bad TOTP code"):
            soc_auth.end_session()
            return _render_login("password", None, "Too many failed attempts. Try again in 15 minutes.", 429)
        return _render_login(step, fresh, "That code is not valid.", 401)

    return redirect("/soc/login", 303)


@soc_bp.route("/soc/logout", methods=["POST"])
def logout():
    blocked = _guard_network()
    if blocked:
        return blocked
    who = soc_auth.end_session()
    if who:
        soc_auth.audit(who, "logout", ip=_ip())
    return redirect("/soc/login", 303)


# ── assets ───────────────────────────────────────────────────────────
# The console's own JS/CSS live in soc_ui/ (not static/) and are served only to a
# signed-in session, so the page code -- which documents every API route -- is
# not downloadable by anonymous visitors. Only the login stylesheet is public.
_ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soc_ui")
_PUBLIC_ASSETS = frozenset({"login.css"})
_PRIVATE_ASSETS = frozenset({"soc.css", "soc-core.js", "soc-views.js", "soc-analysis.js"})


@soc_bp.route("/soc/assets/<name>")
def asset(name):
    blocked = _guard_network()
    if blocked:
        return blocked
    if name not in _PUBLIC_ASSETS and not (name in _PRIVATE_ASSETS and soc_auth.current_user()):
        return "Not found", 404
    return send_from_directory(_ASSET_DIR, name)


# ── console shell ────────────────────────────────────────────────────
@soc_bp.route("/soc", strict_slashes=False)
@soc_required("viewer")
def console():
    u = soc_auth.current_user()
    return render_template("soc/console.html", username=u["username"], role=u["role"],
                           idle_seconds=soc_auth.idle_seconds(), loki=soc_queries.loki_status()["configured"])


# ── API: session ─────────────────────────────────────────────────────
@soc_bp.route("/soc/api/me")
@soc_required("viewer")
def api_me():
    u = soc_auth.current_user()
    return _ok(user=soc_auth.public_user(u), idle_seconds=soc_auth.idle_seconds(), loki=soc_queries.loki_status()["configured"])


@soc_bp.route("/soc/api/me/password", methods=["POST"])
@soc_required("viewer")
def api_change_own_password():
    from utils.auth import check_password_hash
    u = soc_auth.current_user()
    data = _body()
    if not check_password_hash(u["password_hash"], data.get("current", "") or ""):
        soc_auth.register_failure(u, _ip(), "bad current password on change")
        return _err("Current password is incorrect.", 403)
    ok, err = soc_auth.set_password(u["id"], data.get("new", "") or "", u["username"])
    if not ok:
        return _err(err)
    soc_auth.audit(u["username"], "password_changed", ip=_ip())
    soc_auth.end_session()
    return _ok(message="Password changed. Sign in again.")


# ── API: read ────────────────────────────────────────────────────────
@soc_bp.route("/soc/api/pulse")
@soc_required("viewer")
def api_pulse():
    return _ok(**soc_queries.pulse())


@soc_bp.route("/soc/api/overview")
@soc_required("viewer")
def api_overview():
    return _ok(**soc_queries.overview(request.args.get("hours")))


def _event_filters():
    a = request.args
    return dict(q=a.get("q") or None, levels=[x for x in (a.get("level") or "").split(",") if x] or None,
                family=a.get("family") or None, event_type=a.get("event_type") or None, ip=a.get("ip") or None,
                identifier=a.get("identifier") or None, tenant=a.get("tenant") or None,
                alerts_only=a.get("alerts") == "1", hours=a.get("hours"))


@soc_bp.route("/soc/api/events")
@soc_required("viewer")
def api_events():
    return _ok(**soc_queries.search_events(cursor=request.args.get("cursor"), limit=request.args.get("limit", 100),
                                           **_event_filters()))


@soc_bp.route("/soc/api/events/<int:event_id>")
@soc_required("viewer")
def api_event(event_id):
    ev = soc_queries.event_detail(event_id)
    return _ok(event=ev) if ev else _err("No such event.", 404)


@soc_bp.route("/soc/api/events.csv")
@soc_required("analyst")
@limiter.limit("10 per hour")
def api_events_csv():
    rows, cursor = [], None
    while len(rows) < 5000:
        page = soc_queries.search_events(cursor=cursor, limit=200, **_event_filters())
        rows += page["events"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    csv_text = to_csv(
        ["id", "time", "level", "event_type", "family", "mitre", "identifier", "ip", "method", "path", "message"],
        [[e["id"], e["created_at"], e["level"], e["event_type"], e["family"], " ".join(e["mitre"]), e["identifier"],
          e["ip"], e["method"], e["path"], e["message"]] for e in rows[:5000]])
    soc_auth.audit(_actor(), "export_events", detail=f"{min(len(rows), 5000)} rows", ip=_ip())
    resp = make_response(csv_text)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=soc-events-%s.csv" % datetime.datetime.utcnow().strftime("%Y%m%d-%H%M")
    return resp


@soc_bp.route("/soc/api/detections")
@soc_required("viewer")
def api_detections():
    return _ok(**soc_queries.detections(request.args.get("hours")))


@soc_bp.route("/soc/api/entity/ip/<path:ip>")
@soc_required("viewer")
def api_entity_ip(ip):
    from utils.soc_util import looks_like_ip
    if not looks_like_ip(ip):
        return _err("That is not a valid IP address.")
    return _ok(entity=soc_queries.entity_ip(ip, request.args.get("days")))


@soc_bp.route("/soc/api/entity/identifier")
@soc_required("viewer")
def api_entity_identifier():
    value = (request.args.get("value") or "").strip()
    if not value or len(value) > 150:
        return _err("Give an account identifier.")
    return _ok(entity=soc_queries.entity_identifier(value, request.args.get("days")))


@soc_bp.route("/soc/api/bans")
@soc_required("viewer")
def api_bans():
    return _ok(bans=soc_queries.bans())


@soc_bp.route("/soc/api/sessions")
@soc_required("viewer")
def api_sessions():
    data = soc_queries.risky_sessions()
    # The raw session id is only needed to revoke; analysts get it only with the
    # role that can act on it.
    if soc_auth.ROLE_RANK[soc_auth.current_user()["role"]] < soc_auth.ROLE_RANK["analyst"]:
        data = {**data, "sessions": [{k: v for k, v in s.items() if k != "sid"} for s in data["sessions"]]}
    return _ok(**data)


@soc_bp.route("/soc/api/tenants")
@soc_required("viewer")
def api_tenants():
    return _ok(**soc_queries.tenants_overview(request.args.get("hours")))


@soc_bp.route("/soc/api/attendance")
@soc_required("viewer")
def api_attendance():
    return _ok(**soc_queries.attendance_integrity(request.args.get("days")))


@soc_bp.route("/soc/api/posture")
@soc_required("viewer")
def api_posture():
    return _ok(**soc_queries.posture())


@soc_bp.route("/soc/api/integrity")
@soc_required("viewer")
def api_integrity_last():
    return _ok(report=_last_integrity.get("report"), at=_last_integrity.get("at"), by=_last_integrity.get("by"))


@soc_bp.route("/soc/api/integrity/verify", methods=["POST"])
@soc_required("analyst")
@limiter.limit("6 per hour")
def api_integrity_verify():
    try:
        report = soc_queries.audit_integrity()
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"Verification could not complete ({type(exc).__name__}). Try a quieter moment.", 503)
    _last_integrity.update(report=report, at=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z", by=_actor())
    soc_auth.audit(_actor(), "verify_audit_chain", detail=f"ok={report.get('ok')}", ip=_ip())
    return _ok(report=report, at=_last_integrity["at"], by=_last_integrity["by"])


# ── API: log analysis ────────────────────────────────────────────────
@soc_bp.route("/soc/api/loganalysis", methods=["POST"])
@soc_required("analyst")
@limiter.limit("20 per hour")
def api_loganalysis():
    """Log text arrives as a multipart FILE (the browser wraps pasted text in one).
    The WAF inspects form fields and JSON bodies but not file contents, so
    analysing a log full of attack strings does not trip the WAF and ban the
    analyst's own address."""
    f = request.files.get("log")
    if f is None:
        return _err("Attach a log file.")
    raw = f.stream.read(loganalysis.MAX_BYTES + 1)
    report = loganalysis.analyze(raw.decode("utf-8", errors="replace"))
    report["filename"] = (f.filename or "pasted.log")[:100]
    soc_auth.audit(_actor(), "log_analysis", target=report["filename"],
                   detail=f"{report['summary']['lines_read']} lines, {report['summary']['findings']} findings", ip=_ip())
    return _ok(report=report)


@soc_bp.route("/soc/api/loki/status")
@soc_required("viewer")
def api_loki_status():
    return _ok(**soc_queries.loki_status())


@soc_bp.route("/soc/api/loki/query", methods=["POST"])
@soc_required("analyst")
@limiter.limit("30 per minute")
def api_loki_query():
    data = _body()
    try:
        result = soc_queries.loki_query(data.get("query"), data.get("minutes"), data.get("limit"))
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"Loki could not be queried ({type(exc).__name__}).", 502)
    soc_auth.audit(_actor(), "loki_query", detail=str(data.get("query"))[:200], ip=_ip())
    return _ok(**result)


# ── API: response actions ────────────────────────────────────────────
@soc_bp.route("/soc/api/bans", methods=["POST"])
@soc_required("analyst")
def api_ban():
    d = _body()
    ok, msg = soc_actions.ban_ip(d.get("ip"), d.get("reason"), d.get("minutes"), _actor(), _ip())
    return _ok(message=msg) if ok else _err(msg)


@soc_bp.route("/soc/api/bans/remove", methods=["POST"])
@soc_required("analyst")
def api_unban():
    ok, msg = soc_actions.unban_ip(_body().get("ip"), _actor(), _ip())
    return _ok(message=msg) if ok else _err(msg)


@soc_bp.route("/soc/api/bans/bulk", methods=["POST"])
@soc_required("analyst")
def api_ban_bulk():
    d = _body()
    ips = d.get("ips")
    if not isinstance(ips, list) or not ips:
        return _err("Give a list of addresses.")
    return _ok(results=soc_actions.bulk_ban(ips, d.get("reason"), d.get("minutes"), _actor(), _ip()))


@soc_bp.route("/soc/api/sessions/revoke", methods=["POST"])
@soc_required("analyst")
def api_revoke_session():
    d = _body()
    ok, msg = soc_actions.revoke_session(clamp_int(d.get("tenant_id"), 0, 0, 2 ** 31 - 1), d.get("sid"), _actor(), _ip())
    return _ok(message=msg) if ok else _err(msg)


# ── API: cases ───────────────────────────────────────────────────────
@soc_bp.route("/soc/api/cases")
@soc_required("viewer")
def api_cases():
    return _ok(cases=soc_queries.cases(request.args.get("status") or None))


@soc_bp.route("/soc/api/cases/<int:case_id>")
@soc_required("viewer")
def api_case(case_id):
    c = soc_queries.case_detail(case_id)
    return _ok(case=c) if c else _err("No such case.", 404)


@soc_bp.route("/soc/api/cases", methods=["POST"])
@soc_required("analyst")
def api_case_create():
    d = _body()
    ok, msg, cid = soc_actions.create_case(d.get("title"), d.get("severity"), d.get("source_type"),
                                           d.get("source_event_id"), d.get("summary"), _actor(), _ip())
    return _ok(message=msg, id=cid) if ok else _err(msg)


@soc_bp.route("/soc/api/cases/<int:case_id>", methods=["POST"])
@soc_required("analyst")
def api_case_update(case_id):
    d = _body()
    ok, msg = soc_actions.update_case(case_id, _actor(), _ip(), status=d.get("status"), assignee=d.get("assignee"),
                                      severity=d.get("severity"), resolution=d.get("resolution"))
    return _ok(message=msg) if ok else _err(msg)


@soc_bp.route("/soc/api/cases/<int:case_id>/notes", methods=["POST"])
@soc_required("analyst")
def api_case_note(case_id):
    ok, msg = soc_actions.add_note(case_id, _body().get("body"), _actor(), _ip())
    return _ok(message=msg) if ok else _err(msg)


# ── API: administration ──────────────────────────────────────────────
@soc_bp.route("/soc/api/users")
@soc_required("admin")
def api_users():
    return _ok(users=soc_auth.list_users())


@soc_bp.route("/soc/api/users", methods=["POST"])
@soc_required("admin")
def api_user_create():
    d = _body()
    ok, msg, setup = soc_auth.create_user(d.get("username"), d.get("password"), d.get("role", "viewer"), d.get("email"), _actor())
    if not ok:
        return _err(msg)
    soc_auth.audit(_actor(), "user_created", target=str(d.get("username")).lower(), detail=f"role={d.get('role', 'viewer')}", ip=_ip())
    return _ok(message="Account created. Hand over the password and setup code separately; the code is shown once.",
               setup_code=setup)


@soc_bp.route("/soc/api/users/<int:uid>", methods=["POST"])
@soc_required("admin")
def api_user_update(uid):
    d = _body()
    target = soc_auth.get_user_by_id(uid)
    if not target:
        return _err("No such user.", 404)
    me = soc_auth.current_user()
    action = d.get("action")
    result = {}
    if action == "set_role":
        if target["id"] == me["id"]:
            return _err("You cannot change your own role.")
        if target["role"] == "admin" and d.get("role") != "admin" and soc_auth.active_admin_count() <= 1:
            return _err("That is the last administrator.")
        if not soc_auth.update_user(uid, role=d.get("role")):
            return _err("Unknown role.")
        soc_auth.audit(me["username"], "user_role_set", target=target["username"], detail=str(d.get("role")), ip=_ip())
    elif action in ("deactivate", "activate"):
        if action == "deactivate" and target["id"] == me["id"]:
            return _err("You cannot deactivate your own account.")
        if action == "deactivate" and target["role"] == "admin" and soc_auth.active_admin_count() <= 1:
            return _err("That is the last administrator.")
        soc_auth.update_user(uid, active=(action == "activate"))
        soc_auth.audit(me["username"], f"user_{action}d", target=target["username"], ip=_ip())
    elif action == "unlock":
        soc_auth.unlock(uid)
        soc_auth.audit(me["username"], "user_unlocked", target=target["username"], ip=_ip())
    elif action == "reset_mfa":
        result["setup_code"] = soc_auth.reset_mfa(uid)
        soc_auth.audit(me["username"], "user_mfa_reset", target=target["username"], ip=_ip())
    elif action == "reset_password":
        ok, err = soc_auth.set_password(uid, d.get("password") or "", target["username"])
        if not ok:
            return _err(err)
        soc_auth.audit(me["username"], "user_password_reset", target=target["username"], ip=_ip())
    else:
        return _err("Unknown action.")
    return _ok(**result)


@soc_bp.route("/soc/api/audit")
@soc_required("admin")
def api_audit():
    from utils.soc_db import read_cursor
    from utils.soc_util import iso
    before = clamp_int(request.args.get("before"), 2 ** 31 - 1, 1, 2 ** 31 - 1)
    with read_cursor("att_master") as cur:
        cur.execute("SELECT id, actor, action, target, detail, ip, created_at FROM soc_audit_log WHERE id < %s "
                    "ORDER BY id DESC LIMIT 200", (before,))
        rows = [{"id": i, "actor": a, "action": ac, "target": t, "detail": d, "ip": ip, "at": iso(ts)}
                for i, a, ac, t, d, ip, ts in cur.fetchall()]
    return _ok(entries=rows, next_before=rows[-1]["id"] if len(rows) == 200 else None)
