# -*- coding: utf-8 -*-
"""admin_views: per-company custom ID card templates"""
import json
import re
from database import get_db_connection
from extensions import app_log
from flask import abort, flash, redirect, render_template, request
from utils.auth import role_required
from utils.helpers import _validate_image_file, tpath
from . import admin_views_bp
from .companies import _delete_company_image, _save_company_image


# ── Custom ID card templates (per company) ─────────────────────────────────
_ID_CARD_FIELD_KEYS = {
    "photo", "logo", "name", "employee_id", "designation",
    "email", "phone", "blood_group", "qr",
    "date_of_joining", "company_address", "website",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relation",
    "department", "shift", "reporting_manager", "shift_timing", "work_mode", "company_phone",
}


@admin_views_bp.route("/companies/<int:cid>/id_card_template/upload", methods=["POST"])
@role_required("admin")
def id_card_template_upload(cid):
    front_file = request.files.get("front_image")
    back_file = request.files.get("back_image")
    # bool() of a truthiness check, not a numeric-string parse -- nothing
    # here casts user input to float/complex, so NaN injection doesn't apply.
    has_front = front_file is not None and front_file.filename not in (None, "")
    has_back = back_file is not None and back_file.filename not in (None, "")
    if not has_front and not has_back:
        flash("Upload at least a front or back template image.", "error")
        return redirect(tpath("/settings?tab=company"))

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT COUNT(*) FROM companies WHERE id=%s", (cid,))
    if not cursor.fetchone()[0]:
        cursor.close()
        db.close()
        abort(404)

    if has_front:
        ok, err = _validate_image_file(front_file)
        if not ok:
            cursor.close()
            db.close()
            flash(f"Front template: {err}", "error")
            return redirect(tpath("/settings?tab=company"))
    if has_back:
        ok, err = _validate_image_file(back_file)
        if not ok:
            cursor.close()
            db.close()
            flash(f"Back template: {err}", "error")
            return redirect(tpath("/settings?tab=company"))

    cursor.execute(
        "SELECT COALESCE(front_image,''), COALESCE(back_image,'') FROM id_card_templates WHERE company_id=%s", (cid,)
    )
    existing = cursor.fetchone()
    old_front = existing[0] if existing and existing[0] else None
    old_back = existing[1] if existing and existing[1] else None

    new_front = _save_company_image(front_file, cid, "front") if has_front else old_front
    new_back = _save_company_image(back_file, cid, "back") if has_back else old_back

    cursor.execute("""
        INSERT INTO id_card_templates (company_id, front_image, back_image, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (company_id) DO UPDATE SET
            front_image = EXCLUDED.front_image,
            back_image = EXCLUDED.back_image,
            updated_at = CURRENT_TIMESTAMP
    """, (cid, new_front, new_back))
    db.commit()
    cursor.close()
    db.close()

    if has_front and old_front and old_front != new_front:
        _delete_company_image(old_front)
    if has_back and old_back and old_back != new_back:
        _delete_company_image(old_back)

    flash("Template image(s) uploaded. Now place the fields.", "success")
    return redirect(tpath(f"/companies/{cid}/id_card_template/editor"))


@admin_views_bp.route("/companies/<int:cid>/id_card_template/editor")
@role_required("admin")
def id_card_template_editor(cid):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT name FROM companies WHERE id=%s", (cid,))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        db.close()
        abort(404)
    company_name = row[0]
    cursor.execute(
        "SELECT front_image, back_image, fields FROM id_card_templates WHERE company_id=%s", (cid,)
    )
    tpl = cursor.fetchone()
    cursor.close()
    db.close()

    front_image = tpl[0] if tpl else None
    back_image = tpl[1] if tpl else None
    try:
        fields = json.loads(tpl[2]) if tpl and tpl[2] else {}
    except (ValueError, TypeError):
        fields = {}

    return render_template(
        "id_card_template_editor.html",
        cid=cid, company_name=company_name,
        front_image=front_image, back_image=back_image,
        fields=fields,
        active_nav="settings",
    )


@admin_views_bp.route("/companies/<int:cid>/id_card_template/save_positions", methods=["POST"])
@role_required("admin")
def id_card_template_save_positions(cid):
    raw = request.form.get("positions_json", "")
    try:
        positions = json.loads(raw) if raw else {}
    except ValueError:
        positions = None

    if not isinstance(positions, dict):
        flash("Invalid field positions submitted.", "error")
        return redirect(tpath(f"/companies/{cid}/id_card_template/editor"))

    cleaned = {}
    for key, box in positions.items():
        if key not in _ID_CARD_FIELD_KEYS or not isinstance(box, dict):
            continue
        try:
            x, y, w, h = float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(0 <= v <= 1 for v in (x, y, w, h)):
            continue
        side = box.get("side") if box.get("side") in ("front", "back") else "front"
        entry = {"side": side, "x": x, "y": y, "w": w, "h": h}
        if "font_size" in box:
            try:
                entry["font_size"] = max(6, min(72, int(box["font_size"])))
            except (TypeError, ValueError) as exc:
                app_log.debug("ID card template: invalid font_size %r ignored: %s", box.get("font_size"), exc)
        if box.get("bold"):
            entry["bold"] = True
        if box.get("square"):
            entry["square"] = True
        if box.get("round"):
            entry["round"] = True
        color = box.get("color")
        if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            entry["color"] = color
        bg_color = box.get("bg_color")
        if isinstance(bg_color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", bg_color):
            entry["bg_color"] = bg_color
        cleaned[key] = entry

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "UPDATE id_card_templates SET fields=%s, updated_at=CURRENT_TIMESTAMP WHERE company_id=%s",
        (json.dumps(cleaned), cid)
    )
    db.commit()
    cursor.close()
    db.close()
    flash("Field positions saved.", "success")
    return redirect(tpath(f"/companies/{cid}/id_card_template/editor"))


@admin_views_bp.route("/companies/<int:cid>/id_card_template/reset", methods=["POST"])
@role_required("admin")
def id_card_template_reset(cid):
    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute(
        "SELECT COALESCE(front_image,''), COALESCE(back_image,'') FROM id_card_templates WHERE company_id=%s", (cid,)
    )
    row = cursor.fetchone()
    cursor.execute("DELETE FROM id_card_templates WHERE company_id=%s", (cid,))
    db.commit()
    cursor.close()
    db.close()
    if row:
        _delete_company_image(row[0])
        _delete_company_image(row[1])
    flash("ID card template reset to default.", "success")
    return redirect(tpath("/settings?tab=company"))
