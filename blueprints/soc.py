# -*- coding: utf-8 -*-
"""SOC console -- /socperson.

A standalone security-operations console for the platform operator. It is
deliberately not part of any other dashboard: nothing links to it, it has its
own accounts and session (utils/soc_auth.py), it is excluded from tenant URL
routing (utils/tenant_routing.RESERVED_PATH_SEGMENTS), and every response is
marked no-store / noindex.

  /socperson/login      email address -> 6-digit code emailed to it (SOC_LOGIN_EMAILS only)
  /socperson            the console (single page; data over /socperson/api/*)
  /socperson/api/*      JSON, role-gated: viewer < analyst < admin

Data comes from what the product already records (see utils/soc_queries.py);
actions are in utils/soc_actions.py; log analysis is utils/soc_loganalysis.py.
"""
import datetime
import json
import os

from flask import Blueprint, jsonify, make_response, redirect, render_template, request, send_from_directory, session

from extensions import app_log, limiter
from utils import soc_actions, soc_auth, soc_passkeys, soc_queries
from utils import soc_loganalysis as loganalysis
from utils.soc_auth import soc_required
from utils.soc_util import clamp_int, to_csv

soc_bp = Blueprint("soc", __name__)

# The last audit-chain verification is read back from soc_audit_log (the row the
# verify route already writes), not kept in process memory: every worker sees the
# same result and it survives a restart.
_INTEGRITY_ACTION = "verify_audit_chain"
_DETAIL_MAX = 2000      # soc_auth.audit() caps detail at this length


def _integrity_detail(report):
    """Compact JSON of a verification report that fits the audit detail column.
    Long lists (e.g. violations) are trimmed, keeping their full length in
    `<name>_total`, so the stored JSON is never cut mid-way."""
    for keep in (None, 20, 5, 0):
        rec = {}
        for k, v in report.items():
            if isinstance(v, list) and keep is not None and len(v) > keep:
                rec[k], rec[k + "_total"] = v[:keep], len(v)
            else:
                rec[k] = v
        text = json.dumps(rec, default=str, separators=(",", ":"))
        if len(text) <= _DETAIL_MAX:
            return text
    return json.dumps({"ok": report.get("ok")})


def _latest_integrity():
    """-> (report, at, by) of the newest recorded verification, or (None, None, None)."""
    from utils.soc_db import read_cursor
    from utils.soc_util import iso
    with read_cursor("att_master") as cur:
        cur.execute("SELECT actor, detail, created_at FROM soc_audit_log WHERE action=%s ORDER BY id DESC LIMIT 1",
                    (_INTEGRITY_ACTION,))
        row = cur.fetchone()
    if not row:
        return None, None, None
    try:
        report = json.loads(row[1] or "")
    except ValueError:          # rows written before results were stored as JSON ("ok=True")
        report = None
    if not isinstance(report, dict):
        return None, None, None
    return report, iso(row[2]), row[0]


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
# An email address, the 6-digit code sent to it, then a passkey (utils/
# soc_passkeys.py; created at the first sign-in). Whether the address is
# authorised never changes what the email and code steps say -- an unlisted
# one simply never receives a code (utils/soc_auth.request_login_code).
_LOCKED_MSG = "Too many failed attempts. Try again in 15 minutes."
_PASSKEY_EXPIRED = "Your sign-in expired. Start again."


def _render_login(step, email="", error=None, notice=None, status=200, mode=None):
    return render_template("soc/login.html", step=step, email=email, error=error, notice=notice, mode=mode,
                           minutes=soc_auth.LOGIN_CODE_TTL_SECONDS // 60), status


def _render_passkey_step(user, error=None, status=200):
    if not soc_passkeys.available():
        error = error or ("Passkeys are not available on this server. An operator can set SOC_PASSKEY=off "
                          "to sign in with the emailed code alone.")
    mode = "verify" if soc_passkeys.credentials(user["id"]) else "register"
    return _render_login("passkey", user.get("email") or user["username"], error=error, status=status, mode=mode)


@soc_bp.route("/socperson/login", methods=["GET", "POST"])
@limiter.limit("20 per 15 minutes", methods=["POST"])
def login():
    blocked = _guard_network()
    if blocked:
        return blocked
    if soc_auth.current_user():
        return redirect("/socperson")
    pending = soc_auth.pending_email()
    if request.method == "GET":
        pk_user = soc_auth.passkey_step_user()
        if pk_user:
            return _render_passkey_step(pk_user)
        return _render_login("code", pending) if pending else _render_login("email")

    action = request.form.get("action", "")
    ip = _ip()
    if action == "restart":
        soc_auth.end_session()
        return redirect("/socperson/login", 303)

    if action == "email":
        email = soc_auth.normalize_email(request.form.get("email", ""))
        if not soc_auth.looks_like_email(email):
            return _render_login("email", error="Enter a valid email address.", status=400)
        soc_auth.request_login_code(email, ip)
        soc_auth.start_pending(email)
        return redirect("/socperson/login", 303)

    if pending is None:             # no code requested in this browser, or the request expired
        return _render_login("email", error="That sign-in request expired. Enter your email again.", status=401)

    if action == "resend":
        soc_auth.request_login_code(pending, ip)
        soc_auth.start_pending(pending)
        return _render_login("code", pending, notice="If this address may sign in, a new code is on its way. "
                                                     "A code can be sent once a minute.")

    if action == "code":
        user, status = soc_auth.verify_login_code(pending, request.form.get("code", ""), ip)
        if status == "ok":
            if soc_passkeys.required():
                soc_auth.start_passkey_step(user)           # the console stays closed until the passkey
                return redirect("/socperson/login", 303)
            soc_auth.establish(user, ip)
            return redirect("/socperson", 303)
        if status == "locked":
            soc_auth.end_session()
            return _render_login("email", error=_LOCKED_MSG, status=429)
        return _render_login("code", pending, error="That code is not valid or has expired.", status=401)

    return redirect("/socperson/login", 303)


@soc_bp.route("/socperson/login/passkey/options", methods=["POST"])
@limiter.limit("20 per 15 minutes")
def login_passkey_options():
    """Options for navigator.credentials: create a first passkey, or use one."""
    blocked = _guard_network()
    if blocked:
        return blocked
    user = soc_auth.passkey_step_user()
    if user is None:
        return jsonify({"ok": False, "msg": _PASSKEY_EXPIRED, "restart": True}), 401
    if not soc_passkeys.available():
        return _err("Passkeys are not available on this server.", 503)
    problem = soc_passkeys.rp_problem()
    if problem:
        return _err(problem, 422)
    existing = soc_passkeys.credentials(user["id"])
    if existing:
        options, challenge = soc_passkeys.authentication_options(existing)
        mode = "verify"
    else:
        options, challenge = soc_passkeys.registration_options(user, existing)
        mode = "register"
    session["soc_pk_challenge"] = challenge
    session["soc_pk_mode"] = mode
    return _ok(mode=mode, options=options)


@soc_bp.route("/socperson/login/passkey/verify", methods=["POST"])
@limiter.limit("20 per 15 minutes")
def login_passkey_verify():
    """Check the passkey; on success this is where the session starts."""
    blocked = _guard_network()
    if blocked:
        return blocked
    ip = _ip()
    user = soc_auth.passkey_step_user()
    challenge, mode = session.pop("soc_pk_challenge", None), session.pop("soc_pk_mode", None)  # single use
    if user is None:
        return jsonify({"ok": False, "msg": _PASSKEY_EXPIRED, "restart": True}), 401
    credential = _body().get("credential")
    if not challenge or not isinstance(credential, dict):
        return _err("Start the passkey step again.", 400)
    existing = soc_passkeys.credentials(user["id"])
    ok, enrolled = False, False
    try:
        if mode == "register" and not existing:
            ok = enrolled = soc_passkeys.verify_registration(user, credential, challenge)
        elif mode == "verify":
            ok = soc_passkeys.verify_authentication(user, credential, challenge, existing)
    except Exception as exc:
        app_log.warning("SOC passkey %s failed for %s: %s", mode, user["username"], exc)
    if ok:
        soc_auth.establish(soc_auth.get_user_by_id(user["id"]), ip)
        if enrolled:
            soc_auth.audit(user["username"], "passkey_enrolled", ip=ip)
        return _ok()
    if soc_auth.register_failure(user, ip, "passkey not verified"):
        soc_auth.end_session()
        return jsonify({"ok": False, "msg": _LOCKED_MSG, "restart": True}), 429
    return _err("That passkey could not be verified. Try again.", 401)


@soc_bp.route("/socperson/logout", methods=["POST"])
def logout():
    blocked = _guard_network()
    if blocked:
        return blocked
    who = soc_auth.end_session()
    if who:
        soc_auth.audit(who, "logout", ip=_ip())
    return redirect("/socperson/login", 303)


# ── assets ───────────────────────────────────────────────────────────
# The console's own JS/CSS live in soc_ui/ (not static/) and are served only to a
# signed-in session, so the page code -- which documents every API route -- is
# not downloadable by anonymous visitors. Only the login page's own stylesheet and
# its passkey script (which knows nothing beyond /socperson/login/passkey/*) are public.
_ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soc_ui")
_PUBLIC_ASSETS = frozenset({"login.css", "login.js"})
_PRIVATE_ASSETS = frozenset({"soc.css", "soc-core.js", "soc-views.js", "soc-analysis.js", "soc-banreview.js"})


@soc_bp.route("/socperson/assets/<name>")
def asset(name):
    blocked = _guard_network()
    if blocked:
        return blocked
    if name not in _PUBLIC_ASSETS and not (name in _PRIVATE_ASSETS and soc_auth.current_user()):
        return "Not found", 404
    return send_from_directory(_ASSET_DIR, name)


# ── console shell ────────────────────────────────────────────────────
@soc_bp.route("/socperson", strict_slashes=False)
@soc_required("viewer")
def console():
    u = soc_auth.current_user()
    return render_template("soc/console.html", username=u["username"], role=u["role"],
                           idle_seconds=soc_auth.idle_seconds(), loki=soc_queries.loki_status()["configured"])


# ── API: session ─────────────────────────────────────────────────────
@soc_bp.route("/socperson/api/me")
@soc_required("viewer")
def api_me():
    u = soc_auth.current_user()
    return _ok(user=soc_auth.public_user(u), idle_seconds=soc_auth.idle_seconds(), loki=soc_queries.loki_status()["configured"])


# ── API: read ────────────────────────────────────────────────────────
@soc_bp.route("/socperson/api/pulse")
@soc_required("viewer")
def api_pulse():
    return _ok(**soc_queries.pulse())


@soc_bp.route("/socperson/api/overview")
@soc_required("viewer")
def api_overview():
    return _ok(**soc_queries.overview(request.args.get("hours")))


def _event_filters():
    a = request.args
    return dict(q=a.get("q") or None, levels=[x for x in (a.get("level") or "").split(",") if x] or None,
                family=a.get("family") or None, event_type=a.get("event_type") or None, ip=a.get("ip") or None,
                identifier=a.get("identifier") or None, tenant=a.get("tenant") or None,
                alerts_only=a.get("alerts") == "1", hours=a.get("hours"))


@soc_bp.route("/socperson/api/events")
@soc_required("viewer")
def api_events():
    return _ok(**soc_queries.search_events(cursor=request.args.get("cursor"), limit=request.args.get("limit", 100),
                                           since=request.args.get("since"), **_event_filters()))


@soc_bp.route("/socperson/api/events/<int:event_id>")
@soc_required("viewer")
def api_event(event_id):
    ev = soc_queries.event_detail(event_id)
    return _ok(event=ev) if ev else _err("No such event.", 404)


@soc_bp.route("/socperson/api/events.csv")
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


@soc_bp.route("/socperson/api/detections")
@soc_required("viewer")
def api_detections():
    return _ok(**soc_queries.detections(request.args.get("hours")))


@soc_bp.route("/socperson/api/entity/ip/<path:ip>")
@soc_required("viewer")
def api_entity_ip(ip):
    from utils.soc_util import looks_like_ip
    if not looks_like_ip(ip):
        return _err("That is not a valid IP address.")
    return _ok(entity=soc_queries.entity_ip(ip, request.args.get("days")))


@soc_bp.route("/socperson/api/brief")
@soc_required("viewer")
def api_brief():
    kind = request.args.get("kind", "")
    if kind not in soc_queries.BRIEF_KINDS:
        return _err("Unknown selection.")
    try:
        return _ok(brief=soc_queries.brief(kind, (request.args.get("value") or "")[:80], request.args.get("hours")))
    except ValueError as exc:
        return _err(str(exc))


@soc_bp.route("/socperson/api/entity/identifier")
@soc_required("viewer")
def api_entity_identifier():
    value = (request.args.get("value") or "").strip()
    if not value or len(value) > 150:
        return _err("Give an account identifier.")
    return _ok(entity=soc_queries.entity_identifier(value, request.args.get("days")))


@soc_bp.route("/socperson/api/bans")
@soc_required("viewer")
def api_bans():
    return _ok(**soc_queries.bans())


@soc_bp.route("/socperson/api/sessions")
@soc_required("viewer")
def api_sessions():
    data = soc_queries.risky_sessions()
    # The raw session id is only needed to revoke; analysts get it only with the
    # role that can act on it.
    if soc_auth.ROLE_RANK[soc_auth.current_user()["role"]] < soc_auth.ROLE_RANK["analyst"]:
        data = {**data, "sessions": [{k: v for k, v in s.items() if k != "sid"} for s in data["sessions"]]}
    return _ok(**data)


@soc_bp.route("/socperson/api/tenants")
@soc_required("viewer")
def api_tenants():
    return _ok(**soc_queries.tenants_overview(request.args.get("hours")))


@soc_bp.route("/socperson/api/attendance")
@soc_required("viewer")
def api_attendance():
    return _ok(**soc_queries.attendance_integrity(request.args.get("days")))


@soc_bp.route("/socperson/api/posture")
@soc_required("viewer")
def api_posture():
    return _ok(**soc_queries.posture())


@soc_bp.route("/socperson/api/integrity")
@soc_required("viewer")
def api_integrity_last():
    report, at, by = _latest_integrity()
    return _ok(report=report, at=at, by=by)


@soc_bp.route("/socperson/api/integrity/verify", methods=["POST"])
@soc_required("analyst")
@limiter.limit("6 per hour")
def api_integrity_verify():
    try:
        report = soc_queries.audit_integrity()
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"Verification could not complete ({type(exc).__name__}). Try a quieter moment.", 503)
    stored_at = soc_auth.audit(_actor(), _INTEGRITY_ACTION, detail=_integrity_detail(report), ip=_ip())
    # `at` is THIS run's stored row time (same clock as GET /integrity). Reading
    # back "the newest row" could return an older or a concurrent run's result.
    # stored=False tells the UI the result was not saved and won't show on reload.
    from utils.soc_util import iso
    at = iso(stored_at) if stored_at else datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    return _ok(report=report, at=at, by=_actor(), stored=stored_at is not None)


# ── API: log analysis ────────────────────────────────────────────────
@soc_bp.route("/socperson/api/loganalysis", methods=["POST"])
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


@soc_bp.route("/socperson/api/loganalysis/platform", methods=["POST"])
@soc_required("analyst")
@limiter.limit("30 per hour")
def api_loganalysis_platform():
    """Run the log analyser over the security events the platform itself
    recorded in the window -- no upload and no Loki needed."""
    text, src = soc_queries.platform_log_text(_body().get("hours"))
    report = loganalysis.analyze(text)
    report["filename"] = f"platform-activity-{src['hours']}h"
    report["source"] = {"kind": "platform", **src}
    soc_auth.audit(_actor(), "log_analysis", target=report["filename"],
                   detail=f"{src['events']} events, {report['summary']['findings']} findings", ip=_ip())
    return _ok(report=report)


@soc_bp.route("/socperson/api/loki/status")
@soc_required("viewer")
def api_loki_status():
    return _ok(**soc_queries.loki_status())


@soc_bp.route("/socperson/api/loki/query", methods=["POST"])
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
@soc_bp.route("/socperson/api/bans", methods=["POST"])
@soc_required("analyst")
def api_ban():
    d = _body()
    ok, msg = soc_actions.ban_ip(d.get("ip"), d.get("reason"), d.get("minutes"), _actor(), _ip())
    return _ok(message=msg) if ok else _err(msg)


@soc_bp.route("/socperson/api/bans/remove", methods=["POST"])
@soc_required("analyst")
def api_unban():
    d = _body()
    tenant_id = d.get("tenant_id")
    tenant_id = None if tenant_id in (None, "") else clamp_int(tenant_id, 0, 0, 2 ** 31 - 1)
    ok, msg = soc_actions.unban_ip(d.get("ip"), _actor(), _ip(), tenant_id=tenant_id)
    return _ok(message=msg) if ok else _err(msg)


@soc_bp.route("/socperson/api/bans/bulk", methods=["POST"])
@soc_required("analyst")
def api_ban_bulk():
    d = _body()
    ips = d.get("ips")
    if not isinstance(ips, list) or not ips:
        return _err("Give a list of addresses.")
    return _ok(results=soc_actions.bulk_ban(ips, d.get("reason"), d.get("minutes"), _actor(), _ip()))


@soc_bp.route("/socperson/api/sessions/revoke", methods=["POST"])
@soc_required("analyst")
def api_revoke_session():
    d = _body()
    ok, msg = soc_actions.revoke_session(clamp_int(d.get("tenant_id"), 0, 0, 2 ** 31 - 1), d.get("sid"), _actor(), _ip())
    return _ok(message=msg) if ok else _err(msg)


# ── API: cases ───────────────────────────────────────────────────────
@soc_bp.route("/socperson/api/cases")
@soc_required("viewer")
def api_cases():
    return _ok(cases=soc_queries.cases(request.args.get("status") or None, assignee=request.args.get("assignee") or None))


@soc_bp.route("/socperson/api/assignees")
@soc_required("viewer")
def api_assignees():
    """Who a case can be assigned to (active analysts and admins)."""
    return _ok(assignees=soc_queries.assignees())


@soc_bp.route("/socperson/api/cases/<int:case_id>")
@soc_required("viewer")
def api_case(case_id):
    c = soc_queries.case_detail(case_id)
    return _ok(case=c) if c else _err("No such case.", 404)


@soc_bp.route("/socperson/api/cases", methods=["POST"])
@soc_required("analyst")
def api_case_create():
    d = _body()
    ok, msg, cid = soc_actions.create_case(d.get("title"), d.get("severity"), d.get("source_type"),
                                           d.get("source_event_id"), d.get("summary"), _actor(), _ip())
    return _ok(message=msg, id=cid) if ok else _err(msg)


@soc_bp.route("/socperson/api/cases/<int:case_id>", methods=["POST"])
@soc_required("analyst")
def api_case_update(case_id):
    d = _body()
    ok, msg = soc_actions.update_case(case_id, _actor(), _ip(), status=d.get("status"), assignee=d.get("assignee"),
                                      severity=d.get("severity"), resolution=d.get("resolution"))
    return _ok(message=msg) if ok else _err(msg)


@soc_bp.route("/socperson/api/cases/<int:case_id>/notes", methods=["POST"])
@soc_required("analyst")
def api_case_note(case_id):
    ok, msg = soc_actions.add_note(case_id, _body().get("body"), _actor(), _ip())
    return _ok(message=msg) if ok else _err(msg)


# ── API: administration ──────────────────────────────────────────────
@soc_bp.route("/socperson/api/users")
@soc_required("admin")
def api_users():
    users, passkeys = soc_auth.list_users(), soc_passkeys.counts()
    for u in users:
        u["passkeys"] = passkeys.get(u["id"], 0)
    return _ok(users=users, passkey_required=soc_passkeys.required())


# Who may sign in, and with which role, is SOC_LOGIN_EMAILS on the server -- not
# something the console can change. What stays here is the emergency switch
# (disable an account at once, without a deploy) and clearing a lockout.
@soc_bp.route("/socperson/api/users/<int:uid>", methods=["POST"])
@soc_required("admin")
def api_user_update(uid):
    d = _body()
    target = soc_auth.get_user_by_id(uid)
    if not target:
        return _err("No such user.", 404)
    me = soc_auth.current_user()
    action = d.get("action")
    if action in ("deactivate", "activate"):
        if action == "deactivate" and target["id"] == me["id"]:
            return _err("You cannot deactivate your own account.")
        if action == "deactivate" and target["role"] == "admin" and soc_auth.active_admin_count() <= 1:
            return _err("That is the last administrator.")
        soc_auth.update_user(uid, active=(action == "activate"))
        soc_auth.audit(me["username"], f"user_{action}d", target=target["username"], ip=_ip())
    elif action == "unlock":
        soc_auth.unlock(uid)
        soc_auth.audit(me["username"], "user_unlocked", target=target["username"], ip=_ip())
    elif action == "reset_passkey":
        # For a lost device: the person creates a new passkey at their next sign-in.
        # Not your own -- that is a second person's decision (or SOC_PASSKEY=off).
        if target["id"] == me["id"]:
            return _err("Another admin has to reset your passkey.")
        removed = soc_passkeys.reset(uid)
        soc_auth.audit(me["username"], "user_passkey_reset", target=target["username"], detail=f"{removed} removed", ip=_ip())
    else:
        return _err("Unknown action.")
    return _ok()


@soc_bp.route("/socperson/api/audit")
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


# ── ban review: automated detections waiting for an analyst ──────────
# (utils/soc_ban_review.py; the console view is soc_ui/soc-banreview.js)
@soc_bp.route("/socperson/api/ban-reviews")
@soc_required("viewer")
def api_ban_reviews():
    from utils import soc_ban_review
    return _ok(**soc_ban_review.list_reviews(request.args.get("status", "pending")))


@soc_bp.route("/socperson/api/ban-reviews/decide", methods=["POST"])
@soc_required("analyst")
def api_ban_review_decide():
    from utils import soc_ban_review
    d = _body()
    ok, msg = soc_ban_review.decide(d.get("ip"), d.get("action"), _actor(), _ip(),
                                    minutes=d.get("minutes"), days=d.get("days"), note=d.get("note"))
    return _ok(message=msg) if ok else _err(msg)
