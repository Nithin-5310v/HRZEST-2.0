# -*- coding: utf-8 -*-
"""employee_portal: mobile check-in, offline punch sync, auth config, QR+face check-in"""
import datetime
import os
from database import get_db_connection
from extensions import app_log, limiter, log_security_event
from flask import jsonify, request, session
from utils import punch_integrity as pi
from utils.attendance_utils import (
    classify_by_worked_minutes,
    classify_login,
    classify_logout,
    compute_session_worked_minutes,
    detect_overtime,
    fetch_employee_work_location,
    geofence_check_error,
    get_employee_shift,
    is_within_office_range,
    is_within_range,
    record_attendance_failure,
)
from utils.auth import employee_api_required
from utils.face_utils import _face_recognition_available, _get_known_face_encoding, face_recognition
from utils.helpers import (
    _audit,
    allowed_checkin_combos,
    app_gps_checkin_allowed,
    company_now,
    company_today,
    get_auth_config,
)
from utils.storage import decrypted_stream, save_image
from utils.tenant_files import face_logs_dir
from utils.webauthn_utils import _mobile_biometric_recently_verified, _wa_fingerprint_recently_verified
from . import employee_portal_bp


def _mobile_location_enforced(auth_cfg):
    """App check-ins (live and offline sync) must carry coordinates when the
    company enforces location -- on unless the company turned it off (QA
    SEC-08). MOBILE_REQUIRE_LOCATION=false is the deployment-wide opt-out
    from the security-hardening work."""
    if os.environ.get("MOBILE_REQUIRE_LOCATION", "true").strip().lower() in ("false", "0", "no"):
        return False
    return bool(auth_cfg.get("location_enabled", True))


@employee_portal_bp.route("/api/employee/checkin", methods=["POST"])
@employee_api_required
def api_employee_checkin():
    from flask import g as _g
    emp_id = _g.api_emp_id
    data = request.get_json() or {}
    lat = data.get("lat")
    lon = data.get("lon")

    # Login token + GPS only: allowed when the company's check-in policy
    # permits it, not as a way around the QR or face check it requires
    # (QA NV-18). The app's check-in buttons use /api/employee/qr-face-checkin.
    auth_cfg = get_auth_config()
    if not app_gps_checkin_allowed(auth_cfg):
        return jsonify({"ok": False, "msg": "Please check in with QR or face verification. "
                                            "Location-only check-in is turned off for your organisation."}), 403

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    result = fetch_employee_work_location(cursor, emp_id)
    if not result:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    employee_name, work_mode, work_lat, work_lon = result

    ctx = pi.build_context(emp_id, "mobile", "mobile_checkin", request.remote_addr, data)

    def _reject(decision):
        pi.record_evidence(ctx, cursor, outcome="rejected")
        cursor.close()
        db.close()
        if decision.count_as_failure:
            record_attendance_failure(emp_id, company_today(), decision.msg)
        return jsonify({"ok": False, "msg": decision.msg, "code": decision.code}), decision.status

    _d = pi.check_mock_location(ctx, log_security_event)
    if not _d.allow:
        return _reject(_d)
    _d = pi.check_office_network(ctx, cursor, work_mode, log_security_event)
    if not _d.allow:
        return _reject(_d)
    # Same rule as the kiosk (/attendance): with location enforced, a request
    # that leaves the coordinates out is refused rather than skipping the
    # geofence (QA SEC-08).
    if _mobile_location_enforced(auth_cfg) and not (lat and lon):
        ctx.flag("location_missing")
        return _reject(pi.Decision(False, 400,
                                   "Location not captured. Please allow location access and try again.",
                                   "location_required"))

    if lat and lon:
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (ValueError, TypeError):
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Invalid lat/lon values."}), 400
        if work_mode == 'wfh':
            if work_lat and work_lon:
                if not is_within_range(lat_f, lon_f, float(work_lat), float(work_lon)):
                    cursor.close()
                    db.close()
                    return jsonify({"ok": False, "msg": "You are outside your registered home location."})
        else:
            if not is_within_office_range(lat_f, lon_f):
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "You are outside the office premises."})

    # The punch time is always the server's: a client-supplied punched_at
    # was accepted here up to 24 h back, so an employee arriving at 11:00
    # could submit 08:55 and avoid the late/half-day mark (QA SEC-09). The
    # app never sends it here; queued offline punches go through
    # /api/employee/sync_punches.
    now = company_now()
    # The client's punched_at never sets the time (above), but an old one
    # still means the app queued this punch while offline -- flag it for HR
    # review in the punch evidence.
    punched_at_str = str(data.get("punched_at") or "")
    if punched_at_str:
        try:
            _pt = datetime.datetime.fromisoformat(punched_at_str.replace("Z", "+00:00"))
            _pt = _pt.astimezone(now.tzinfo) if _pt.tzinfo is not None else now.tzinfo.localize(_pt)
            if (now - _pt).total_seconds() > 600:
                ctx.flag("offline_punch")
        except (ValueError, TypeError) as exc:
            app_log.debug("Unparseable punched_at=%r: %s", punched_at_str, exc)

    pi.record_evidence(ctx, cursor, outcome="accepted")

    today = now.date()
    current_time = now.time()

    cursor.execute(
        "SELECT login_time, logout_time, status, worked_minutes, last_relogin "
        "FROM attendance WHERE employee_id=%s AND date=%s",
        (emp_id, today)
    )
    record = cursor.fetchone()
    login_time = record[0] if record else None
    logout_time = record[1] if record else None
    login_status = record[2] if record else None
    worked_mins_stored = (record[3] or 0) if record else 0
    last_relogin_stored = record[4] if record else None
    s_start, s_half, s_end, _shift_name = get_employee_shift(emp_id, cursor)

    if not login_time:
        status = classify_login(current_time, s_start, s_half)
        cursor.execute(
            "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
            (emp_id, today, current_time, status)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "login", "name": employee_name,
                        "status": status, "time": current_time.strftime("%H:%M:%S")})
    elif not logout_time:
        total_m = compute_session_worked_minutes(current_time, today, login_time, last_relogin_stored, worked_mins_stored)
        out_status = classify_logout(current_time, s_half, s_end)
        att_type = classify_by_worked_minutes(login_status, total_m, s_start, s_end)
        cursor.execute(
            "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, out_status, att_type, total_m, emp_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        detect_overtime(emp_id, today, current_time)
        return jsonify({"ok": True, "action": "logout", "name": employee_name,
                        "status": out_status, "att_type": att_type,
                        "time": current_time.strftime("%H:%M:%S")})
    else:
        cursor.execute(
            "UPDATE attendance SET logout_time=NULL, last_relogin=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, emp_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "relogin", "name": employee_name,
                        "status": "Re-Login", "time": current_time.strftime("%H:%M:%S")})


@employee_portal_bp.route("/api/employee/sync_punches", methods=["POST"])
@employee_api_required
def api_employee_sync_punches():
    """Batch-submit offline punches queued on the device when there was no connectivity."""
    from flask import g as _g
    emp_id = _g.api_emp_id
    payload = request.get_json() or {}
    punches = payload.get("punches", [])
    if not punches:
        return jsonify({"ok": True, "results": []})

    db2 = get_db_connection()
    cur2 = db2.cursor(buffered=True)
    _emp_row = fetch_employee_work_location(cur2, emp_id)
    if not _emp_row:
        cur2.close()
        db2.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    _work_mode, _work_lat, _work_lon = _emp_row[1], _emp_row[2], _emp_row[3]
    s_start, s_half, s_end, _shift_name = get_employee_shift(emp_id, cur2)
    location_enforced = _mobile_location_enforced(get_auth_config())
    _device_id = pi.sanitize_device_id(payload.get("device_id"))

    results = []
    for punch in punches[:200]:     # a day of offline punches is a handful; cap abuse
        punched_at_str = punch.get("punched_at", "")
        ctx = pi.build_context(emp_id, "mobile", "offline_sync", request.remote_addr, punch)
        ctx.device_id = ctx.device_id or _device_id
        _d = pi.check_mock_location(ctx, log_security_event)
        if not _d.allow:
            pi.record_evidence(ctx, cur2, outcome="rejected")
            results.append({"id": punch.get("id"), "ok": False, "msg": _d.msg})
            continue
        lat = punch.get("lat")
        lon = punch.get("lon")
        # With location enforced, a punch without coordinates is refused,
        # exactly like the live route (QA SEC-08).
        if location_enforced and not (lat and lon):
            ctx.flag("location_missing")
            pi.record_evidence(ctx, cur2, outcome="rejected")
            results.append({"id": punch.get("id"), "ok": False,
                            "msg": "Location not captured. Please allow location access."})
            continue
        # "received", not "accepted": the geofence/timestamp checks below can
        # still reject this punch after the integrity layer has seen it.
        pi.record_evidence(ctx, cur2, outcome="received")
        # Same geo-fence check as the live check-in route (is_within_range
        # against work_lat/work_lon for wfh, else is_within_office_range).
        if lat and lon:
            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (ValueError, TypeError):
                results.append({"id": punch.get("id"), "ok": False, "msg": "Invalid lat/lon values."})
                continue
            if _work_mode == 'wfh':
                if _work_lat and _work_lon and not is_within_range(lat_f, lon_f, float(_work_lat), float(_work_lon)):
                    results.append({"id": punch.get("id"), "ok": False, "msg": "Outside registered home location."})
                    continue
            else:
                if not is_within_office_range(lat_f, lon_f):
                    results.append({"id": punch.get("id"), "ok": False, "msg": "Outside office premises."})
                    continue
        try:
            _pt = datetime.datetime.fromisoformat(punched_at_str.replace("Z", "+00:00"))
            _now = company_now()
            if _pt.tzinfo is not None:
                # See api_employee_checkin's punched_at handling above --
                # mobile always sends UTC ("...Z"); convert to the tenant's
                # configured timezone instead of discarding the offset and
                # treating the raw UTC digits as local.
                _pt = _pt.astimezone(_now.tzinfo)
            else:
                _pt = _now.tzinfo.localize(_pt)
            age = (_now - _pt).total_seconds()
            if age > 86400:
                results.append({"id": punch.get("id"), "ok": False, "msg": "Too old (>24 h)"})
                continue
            if _pt > _now + datetime.timedelta(minutes=5):
                results.append({"id": punch.get("id"), "ok": False, "msg": "Future timestamp rejected"})
                continue
        except (ValueError, TypeError):
            results.append({"id": punch.get("id"), "ok": False, "msg": "Invalid timestamp"})
            continue

        punch_date = _pt.date()
        punch_time = _pt.time()
        cur2.execute(
            "SELECT login_time, logout_time, status, worked_minutes, last_relogin "
            "FROM attendance WHERE employee_id=%s AND date=%s",
            (emp_id, punch_date)
        )
        rec = cur2.fetchone()
        login_time = rec[0] if rec else None
        logout_time = rec[1] if rec else None
        login_status = rec[2] if rec else None
        worked_mins = (rec[3] or 0) if rec else 0
        last_relogin = rec[4] if rec else None

        if not login_time:
            status = classify_login(punch_time, s_start, s_half)
            cur2.execute(
                "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
                (emp_id, punch_date, punch_time, status)
            )
            db2.commit()
            results.append({"id": punch.get("id"), "ok": True, "action": "login", "status": status})
        elif not logout_time:
            total_m = compute_session_worked_minutes(punch_time, punch_date, login_time, last_relogin, worked_mins)
            out_status = classify_logout(punch_time, s_half, s_end)
            att_type = classify_by_worked_minutes(login_status, total_m, s_start, s_end)
            cur2.execute(
                "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
                "WHERE employee_id=%s AND date=%s",
                (punch_time, out_status, att_type, total_m, emp_id, punch_date)
            )
            db2.commit()
            results.append({"id": punch.get("id"), "ok": True, "action": "logout", "status": out_status})
        else:
            results.append({"id": punch.get("id"), "ok": False, "msg": "Duplicate -- day already complete"})

    cur2.close()
    db2.close()
    _audit("sync_punches", "attendance", emp_id, f"Synced {len([r for r in results if r['ok']])} offline punches")
    return jsonify({"ok": True, "results": results})


@employee_portal_bp.route("/api/employee/auth-config", methods=["GET"])
def api_employee_auth_config():
    """Return all authentication method flags (public, no token required)."""
    return jsonify({"ok": True, **get_auth_config()})


@employee_portal_bp.route("/api/employee/qr-face-checkin", methods=["POST"])
@limiter.limit("20 per minute")
def api_employee_qr_face_checkin():
    """Kiosk endpoint -- supports auth_combo: qr_face | qr_fingerprint | face_fingerprint.

    Also serves the logged-in employee's own dashboard "Mark Attendance"
    widget: when an employee session exists, its employee_id always wins
    over whatever was posted, so a logged-in employee can never mark
    attendance under a different employee_id. With no session (the shared
    kiosk device, which is never logged in), falls back to the posted
    employee_id exactly as before -- unauthenticated by design there, since
    the QR/face/fingerprint combo itself is the proof of identity."""
    lat = request.form.get("lat")
    lon = request.form.get("lon")
    face_photo = request.files.get("face_photo")
    auth_combo = request.form.get("auth_combo", "qr_face")

    if auth_combo not in ("qr_face", "qr_fingerprint", "face_fingerprint"):
        return jsonify({"ok": False, "msg": "Invalid auth_combo"}), 400

    _session_emp_id = session.get("employee_id")
    if _session_emp_id:
        employee_id = _session_emp_id.strip().upper()
    elif auth_combo in ("qr_face", "qr_fingerprint"):
        # Shared, unauthenticated kiosk device: the posted "employee_id" is
        # really whatever text was scanned off the QR code, which must be
        # this employee's signed "<id>.<hmac>" value (qr_generator.py), not
        # a bare ID -- otherwise the QR/fingerprint combo below would be
        # checking the RIGHT biometric against a WRONG (attacker-typed)
        # employee_id if the two auth factors weren't cryptographically
        # bound to the same source.
        from qr_generator import verify_qr_value
        _verified_id, _qr_valid = verify_qr_value(request.form.get("employee_id", "").strip())
        if not _qr_valid:
            return jsonify({"ok": False, "msg": "Invalid or unrecognized QR code. Please rescan."}), 400
        employee_id = _verified_id.upper()
    else:
        # face_fingerprint has no QR component -- the ID is typed/pre-known
        # at the kiosk, and a real face/fingerprint match against THAT ID's
        # stored credential is what actually gates identity.
        employee_id = request.form.get("employee_id", "").strip().upper()

    if not employee_id:
        return jsonify({"ok": False, "msg": "employee_id required"}), 400

    auth_cfg = get_auth_config()

    # Same server-side policy as the web kiosk (/attendance): the client's
    # auth_combo can only select among methods the settings permit, so it
    # can't be used to skip face verification while face is enabled.
    if auth_combo not in allowed_checkin_combos(auth_cfg):
        return jsonify({"ok": False, "msg": "This check-in method is not enabled for your organisation."}), 403

    if auth_combo in ("qr_face", "qr_fingerprint") and not auth_cfg["qr_enabled"]:
        return jsonify({"ok": False, "msg": "QR code authentication is not enabled"}), 403
    if auth_combo in ("qr_face", "face_fingerprint") and not auth_cfg["face_enabled"]:
        return jsonify({"ok": False, "msg": "Face recognition authentication is not enabled"}), 403
    if auth_combo in ("qr_fingerprint", "face_fingerprint"):
        if not auth_cfg["fingerprint_enabled"]:
            return jsonify({"ok": False, "msg": "Fingerprint authentication is not enabled"}), 403
        # Real, server-verified, one-time, employee-bound proof from either
        # /api/employee/webauthn-verify-challenge (web kiosk, session-based)
        # or /api/employee/mobile-biometric-attest (mobile app, Bearer-token-
        # bound) -- never a raw client-supplied flag.
        if not (_wa_fingerprint_recently_verified(employee_id)
                or _mobile_biometric_recently_verified(employee_id)):
            return jsonify({"ok": False, "msg": "Fingerprint verification failed. Please try again."}), 401

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    result = fetch_employee_work_location(cursor, employee_id, include_face=True)
    if not result:
        cursor.close()
        db.close()
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    employee_name, work_mode, work_lat, work_lon, registered_face = result

    # Integrity layer (utils/punch_integrity.py). channel is informational
    # evidence only; nothing below trusts it for a security decision.
    _channel = request.form.get("channel") if request.form.get("channel") in ("kiosk", "portal", "mobile") else (
        "mobile" if request.headers.get("Authorization", "").startswith("Bearer ") else "kiosk")
    ctx = pi.build_context(employee_id, _channel, auth_combo, request.remote_addr, request.form)

    def _reject(decision):
        pi.record_evidence(ctx, cursor, outcome="rejected")
        cursor.close()
        db.close()
        if decision.count_as_failure:
            record_attendance_failure(employee_id, company_today(), decision.msg)
        return jsonify({"ok": False, "msg": decision.msg, "code": decision.code}), decision.status

    _d = pi.check_mock_location(ctx, log_security_event)
    if not _d.allow:
        return _reject(_d)
    _d = pi.check_office_network(ctx, cursor, work_mode, log_security_event)
    if not _d.allow:
        return _reject(_d)
    if auth_combo in ("qr_face", "face_fingerprint"):
        _d = pi.check_biometric_consent(ctx, cursor, log_security_event)
        if not _d.allow:
            return _reject(_d)

    if lat and lon:
        try:
            geofence_err = geofence_check_error(work_mode, work_lat, work_lon, lat, lon)
        except (ValueError, TypeError):
            geofence_err = None
        if geofence_err:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": geofence_err})

    needs_face = auth_combo in ("qr_face", "face_fingerprint")
    if needs_face:
        if not face_photo:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face photo required for this authentication method."}), 400
        if not _face_recognition_available:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face recognition is currently unavailable on this server. Contact your admin."}), 503
        if not registered_face or not os.path.exists(registered_face):
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "No registered face found. Please contact your admin."}), 400
        try:
            from PIL import Image as _PILImage
            face_dir = face_logs_dir()
            os.makedirs(face_dir, exist_ok=True)
            ts = company_now().strftime("%Y%m%d_%H%M%S")
            face_path = os.path.join(face_dir, f"{employee_id}_{ts}.jpg")
            img = _PILImage.open(face_photo.stream).convert("RGB")
            save_image(img, face_path, "JPEG", quality=80)

            known_enc = _get_known_face_encoding(employee_id, registered_face)
            test_img_data = face_recognition.load_image_file(decrypted_stream(face_path))
            test_encs = face_recognition.face_encodings(test_img_data)
            if known_enc is None or not test_encs:
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "Face not detected clearly. Please retake the photo."}), 400
            if not face_recognition.compare_faces([known_enc], test_encs[0], tolerance=0.5)[0]:
                cursor.close()
                db.close()
                return jsonify({"ok": False, "msg": "Face did not match. Please try again."}), 401

            # Identity matched -- replay + liveness, same policy as /attendance.
            face_photo.stream.seek(0)
            _photo_bytes = face_photo.stream.read(pi.liveness.MAX_FRAME_BYTES * 4)
            try:
                ctx.photo_sha256, ctx.photo_dhash = pi.fingerprint_photo(_photo_bytes)
            except Exception:
                app_log.warning("photo fingerprint failed for %s", employee_id, exc_info=True)
            _blobs = [f.read(pi.liveness.MAX_FRAME_BYTES + 1) for f in request.files.getlist("liveness_frames")]
            _frames, _kept = pi.decode_frame_blobs(_blobs)
            _d = pi.check_photo_replay(ctx, cursor, log_security_event)
            if not _d.allow:
                return _reject(_d)
            _d = pi.check_liveness(
                ctx, cursor, frames=_frames,
                challenge_id=request.form.get("challenge_id"), nonce=request.form.get("challenge_nonce"),
                known_encoding=known_enc, face_recognition_mod=face_recognition,
                log_event=log_security_event)
            if not _d.allow:
                return _reject(_d)
        except Exception:
            app_log.error("Face verification error", exc_info=True)
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Face verification failed. Please retake the photo."}), 500
    elif face_photo:
        try:
            from PIL import Image as _PILImage
            face_dir = face_logs_dir()
            os.makedirs(face_dir, exist_ok=True)
            ts = company_now().strftime("%Y%m%d_%H%M%S")
            face_path = os.path.join(face_dir, f"{employee_id}_{ts}.jpg")
            img = _PILImage.open(face_photo.stream).convert("RGB")
            save_image(img, face_path, "JPEG", quality=80)
        except Exception as exc:
            # Best-effort audit-log attachment -- must not block attendance
            # marking, but silently missing the face-log photo is worth
            # knowing about.
            app_log.warning("Face-log photo save failed for %s: %s", employee_id, exc, exc_info=True)

    pi.record_evidence(ctx, cursor, outcome="accepted")

    now = company_now()
    today = now.date()
    current_time = now.time()

    cursor.execute(
        "SELECT login_time, logout_time, status, worked_minutes, last_relogin "
        "FROM attendance WHERE employee_id=%s AND date=%s",
        (employee_id, today)
    )
    record = cursor.fetchone()
    login_time = record[0] if record else None
    logout_time = record[1] if record else None
    login_status = record[2] if record else None
    worked_mins_stored = (record[3] or 0) if record else 0
    last_relogin_stored = record[4] if record else None
    s_start, s_half, s_end, _shift_name = get_employee_shift(employee_id, cursor)

    if not login_time:
        status = classify_login(current_time, s_start, s_half)
        cursor.execute(
            "INSERT INTO attendance (employee_id, date, login_time, status) VALUES (%s,%s,%s,%s)",
            (employee_id, today, current_time, status)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "login", "name": employee_name,
                        "status": status, "time": current_time.strftime("%H:%M:%S")})
    elif not logout_time:
        total_m = compute_session_worked_minutes(current_time, today, login_time, last_relogin_stored, worked_mins_stored)
        out_status = classify_logout(current_time, s_half, s_end)
        att_type = classify_by_worked_minutes(login_status, total_m, s_start, s_end)
        cursor.execute(
            "UPDATE attendance SET logout_time=%s, logout_status=%s, attendance_type=%s, worked_minutes=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, out_status, att_type, total_m, employee_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        detect_overtime(employee_id, today, current_time)
        return jsonify({"ok": True, "action": "logout", "name": employee_name,
                        "status": out_status, "att_type": att_type,
                        "time": current_time.strftime("%H:%M:%S")})
    else:
        cursor.execute(
            "UPDATE attendance SET logout_time=NULL, last_relogin=%s "
            "WHERE employee_id=%s AND date=%s",
            (current_time, employee_id, today)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"ok": True, "action": "relogin", "name": employee_name,
                        "status": "Re-Login", "time": current_time.strftime("%H:%M:%S")})
