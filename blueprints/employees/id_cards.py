# -*- coding: utf-8 -*-
"""employees: ID card rendering (default and custom templates) and routes"""
import json
import os
from database import get_db_connection
from extensions import app, app_log
from flask import jsonify
from io import BytesIO
from qr_generator import generate_qr
from utils.attendance_utils import _td_to_time
from utils.auth import HR_ROLE, admin_required, api_required, api_role_required
from utils.helpers import decrypt_pii, get_company_settings
from utils.storage import open_image, open_private
from utils.tenant_files import resolve_face_photo
from . import employees_bp


_IDC_DARK = (15, 40, 100)
_IDC_BLUE = (30, 58, 138)
_IDC_MID = (37, 99, 235)
_IDC_PALE = (219, 234, 254)
_IDC_WHITE = (255, 255, 255)
_IDC_LGRAY = (241, 245, 249)
_IDC_MGRAY = (100, 116, 139)
_IDC_DGRAY = (15, 23, 42)
_IDC_GOLD = (251, 191, 36)
_IDC_RED = (220, 38, 38)


def _idc_blood_drop(draw, x, y, w, h, color):
    """Draw a small blood-drop (teardrop) icon inside box (x, y, w, h) --
    vector-drawn rather than an emoji glyph, since the PIL fonts available
    in this environment can't render color emoji (they'd just show as a
    missing-glyph box)."""
    r = w / 2.0
    cx = x + r
    circle_bottom = y + h
    circle_top = circle_bottom - 2 * r
    draw.polygon([(cx, y), (x, circle_top + r * 0.2), (x + w, circle_top + r * 0.2)], fill=color)
    draw.ellipse([(cx - r, circle_top), (cx + r, circle_bottom)], fill=color)


def _idc_font(size, bold=False):
    """Resolves a TrueType font for ID-card rendering. The bundled
    static/fonts/DejaVuSans[-Bold].ttf (freely-licensed, see
    static/fonts/LICENSE_DEJAVU.txt) is tried FIRST and is the only path
    guaranteed to exist regardless of host OS -- the old version of this
    function only ever looked at OS-installed font locations
    (C:/Windows/Fonts/... on Windows, nothing bundled), which works by
    accident on a dev machine with Windows/Office installed but silently
    degrades to PIL's tiny built-in bitmap font on a minimal Linux
    container that has no fontconfig/dejavu packages -- ID cards would
    render with visibly different, worse typography in production than
    whatever was tested locally. The OS-path candidates are kept below
    only as a secondary preference for a nicer-looking installed font
    when one happens to be present; they are never required."""
    from PIL import ImageFont
    # app (the real Flask object, imported at module level above), not
    # current_app -- this is called from tests/test_id_card_templates.py's
    # rendering tests with no active request/app context, where
    # current_app's LocalProxy would raise "Working outside of
    # application context" on first attribute access.
    bundled = os.path.join(app.root_path, "static", "fonts",
                           "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    os_candidates = (
        ["C:/Windows/Fonts/segoeuib.ttf",
         "C:/Windows/Fonts/arialbd.ttf",
         "C:/Windows/Fonts/calibrib.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["C:/Windows/Fonts/segoeui.ttf",
         "C:/Windows/Fonts/arial.ttf",
         "C:/Windows/Fonts/calibri.ttf",
         "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for p in [bundled] + os_candidates:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    # Reaching here means even the bundled static/fonts/DejaVuSans*.ttf
    # couldn't be loaded (missing/corrupted install) -- ID cards will
    # render with PIL's tiny built-in bitmap font, a real visual
    # regression worth flagging rather than discovering from a support
    # ticket about ugly ID cards.
    app_log.warning("_idc_font: no TrueType font resolved (bundled path=%s) -- falling back to PIL's bitmap default", bundled)
    return ImageFont.load_default()


def _idc_safe_text(text):
    try:
        text.encode('latin-1')
        return text
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text.encode('ascii', 'replace').decode('ascii')


def _idc_text_width(draw, text, font):
    bb = draw.textbbox((0, 0), _idc_safe_text(text), font=font)
    return bb[2] - bb[0]


_IDC_WORK_MODE_LABELS = {"office": "Office", "wfh": "Work From Home"}


def _idc_fmt_shift_time(t):
    t = _td_to_time(t)
    return t.strftime("%I:%M %p") if t else None


def _idc_shift_timing_text(shift_start, shift_end):
    s, e = _idc_fmt_shift_time(shift_start), _idc_fmt_shift_time(shift_end)
    return f"{s} - {e}" if s and e else "-"


def _idc_work_mode_text(work_mode):
    return _IDC_WORK_MODE_LABELS.get(work_mode, work_mode or "-")


def _idc_center_text(draw, text, font, card_w, y, color):
    t = _idc_safe_text(text)
    draw.text(((card_w - _idc_text_width(draw, t, font)) // 2, y), t, font=font, fill=color)


def _idc_wrap_text(draw, text, font, max_width, max_lines=2):
    """Greedy word-wrap `text` to fit within `max_width` px, capped at
    `max_lines` lines -- used for the default card's company address, which
    is free-form text unlike the other fixed single-line fields. Anything
    past `max_lines` is dropped, with the last line ellipsized."""
    words = _idc_safe_text(text).split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if not current or _idc_text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return lines

    kept = lines[:max_lines]
    last = kept[-1]
    while len(last) > 1 and _idc_text_width(draw, last + "…", font) > max_width:
        last = last[:-1]
    kept[-1] = last + "…"
    return kept


def _idc_header_logo_and_name(draw, img, bar_h, company_name, logo_path, fallback_title, fallback_subtitle):
    """Draw the default card's header as a logo+company-name lockup when the
    employee's company has branding on file, or the generic fixed title
    otherwise -- shared by both the front and back default layouts so they
    stay visually identical instead of drifting apart."""
    from PIL import Image

    CW = img.width
    if not company_name and not logo_path:
        _idc_center_text(draw, fallback_title, _idc_font(18, bold=True), CW, 18, _IDC_WHITE)
        _idc_center_text(draw, fallback_subtitle, _idc_font(11), CW, 52, _IDC_PALE)
        return

    logo_img = None
    if logo_path:
        try:
            logo_img = Image.open(_idc_static_path(logo_path)).convert("RGBA").resize((56, 56), Image.LANCZOS)
        except Exception:
            logo_img = None

    circle_margin = 120  # leave room for the decorative circle at top-right
    font = _idc_font(22, bold=True)
    name = _idc_safe_text(company_name) if company_name else ""
    logo_block_w = 60 + 14 if logo_img else 0
    if name:
        max_name_w = max(20, CW - circle_margin - 14 - logo_block_w)
        while len(name) > 1 and _idc_text_width(draw, name, font) > max_name_w:
            name = name[:-1]
    name_w = _idc_text_width(draw, name, font) if name else 0
    total_w = logo_block_w + name_w
    start_x = max(14, (CW - circle_margin - total_w) // 2)

    if logo_img:
        box_y = (bar_h - 60) // 2
        draw.rounded_rectangle([(start_x, box_y), (start_x + 60, box_y + 60)], radius=8, fill=_IDC_WHITE)
        img.paste(logo_img, (start_x + 2, box_y + 2), logo_img)
        text_x = start_x + 60 + 14
    else:
        text_x = start_x

    if name:
        text_y = (bar_h - 26) // 2
        draw.text((text_x, text_y), name, font=font, fill=_IDC_WHITE)


def _idc_footer_contact(draw, cw, ch, company_name, company_website, company_phone):
    """Draw the company's identity (name, then website/toll-free) as up to
    two compact lines centered in the card's bottom footer bar -- the same
    spot where a generic disclaimer used to sit -- shared by front and back
    so they stay in sync. Draws nothing when the company has none of these
    on file, preserving today's look for uncustomized companies."""
    contact_parts = [p for p in (
        company_website, f"Toll-Free: {company_phone}" if company_phone else None,
    ) if p]
    if not company_name and not contact_parts:
        return

    def _fit(text, font, max_w):
        t = text
        while len(t) > 1 and _idc_text_width(draw, t, font) > max_w:
            t = t[:-1]
        return t

    max_w = cw - 40
    if company_name and contact_parts:
        _idc_center_text(draw, _fit(company_name, _idc_font(11, bold=True), max_w),
                         _idc_font(11, bold=True), cw, ch - 44, _IDC_WHITE)
        line2 = "  |  ".join(contact_parts)
        _idc_center_text(draw, _fit(line2, _idc_font(9), max_w), _idc_font(9), cw, ch - 26, (200, 210, 225))
    elif company_name:
        _idc_center_text(draw, _fit(company_name, _idc_font(12, bold=True), max_w),
                         _idc_font(12, bold=True), cw, ch - 34, _IDC_WHITE)
    else:
        line = "  |  ".join(contact_parts)
        _idc_center_text(draw, _fit(line, _idc_font(10), max_w), _idc_font(10), cw, ch - 34, _IDC_WHITE)


def _idc_box_text(draw, text, box, color, font_size=14, bold=False, align="center"):
    """Draw text truncated to fit inside a pixel box (x, y, w, h) -- used by
    custom-template rendering, where fields sit at admin-chosen positions
    rather than the fixed default layout's hardcoded coordinates."""
    font = _idc_font(font_size, bold=bold)
    x, y, w, h = box
    t = _idc_safe_text(str(text))
    while len(t) > 1 and _idc_text_width(draw, t, font) > w:
        t = t[:-1]
    tw_ = _idc_text_width(draw, t, font)
    tx = x + max(0, (w - tw_) // 2) if align == "center" else x
    ty = y + max(0, (h - font_size) // 2)
    draw.text((tx, ty), t, font=font, fill=color)


def _idc_static_path(rel_path):
    return os.path.join(app.root_path, "static", rel_path)


def _idc_qr_image(emp_id):
    """PIL Image of emp_id's attendance QR, for pasting into a rendered ID
    card. generate_qr() now saves to private storage (see its own
    docstring for why -- the QR payload is a bearer credential, never
    world-readable), so this always regenerates fresh and reads it back
    through open_private() rather than assuming a bare local path the way
    the two call sites below both used to."""
    from PIL import Image
    stored_ref = generate_qr(emp_id)
    return Image.open(BytesIO(open_private(stored_ref)))


def _idc_box_bg_color(img, box):
    """Best-guess background fill color for a text field's box, sampled from
    the (unmodified) template image at each edge's midpoint. Custom templates
    often have their own placeholder wording baked into the pixels ("YOUR
    NAME", "JOB POSITION") on a pill/rounded-rect background -- the box's
    actual corners usually land outside a *rounded* pill (in whatever
    surrounds it), while edge midpoints reliably land on the pill's fill and
    rarely on a glyph stroke (text is centered with padding from the edges).
    Filling with this color before drawing the real value covers the
    placeholder text instead of drawing over it."""
    x, y, w, h = box
    inset = max(2, min(w, h) // 8)
    points = [
        (x + inset, y + h // 2),
        (x + w - inset, y + h // 2),
        (x + w // 2, y + inset),
        (x + w // 2, y + h - inset),
    ]
    samples = []
    for px, py in points:
        px = min(max(0, px), img.width - 1)
        py = min(max(0, py), img.height - 1)
        samples.append(img.getpixel((px, py)))
    counts = {}
    for s in samples:
        counts[s] = counts.get(s, 0) + 1
    best = max(counts.items(), key=lambda kv: kv[1])
    if best[1] > 1:
        return best[0]
    return tuple(sum(c[i] for c in samples) // len(samples) for i in range(3))


def _idc_contrast_color(bg_rgb):
    """Pick white or dark-navy text, whichever reads better against `bg_rgb`,
    using perceptual (WCAG-style) relative luminance. This is the fallback
    used whenever a custom-template field doesn't pin an explicit text
    color -- so a field placed over a dark pill (navy header, photo overlay)
    or a light one (white card body) is legible by default instead of always
    rendering the same fixed gray regardless of what's underneath."""
    def _chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = bg_rgb
    luminance = 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)
    return _IDC_WHITE if luminance < 0.5 else _IDC_DGRAY


def _idc_parse_color(hex_str, default):
    """Parse a '#rrggbb' string (from a custom template field's saved color)
    into an (r,g,b) tuple, falling back to `default` for anything invalid --
    an admin-placed field over a dark pill background needs light text, so
    this can't be a single hardcoded color for every template."""
    if not hex_str or not isinstance(hex_str, str):
        return default
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return default
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _idc_combine(front_img, back_img):
    from PIL import Image, ImageDraw
    gap, lbl_h = 40, 24
    bgcol = (215, 225, 240)
    total_w = front_img.width + gap + back_img.width
    total_h = max(front_img.height, back_img.height) + lbl_h
    total = Image.new("RGB", (total_w, total_h), bgcol)
    td = ImageDraw.Draw(total)
    td.text((10, 4), "FRONT", font=_idc_font(13, bold=True), fill=_IDC_BLUE)
    td.text((front_img.width + gap + 10, 4), "BACK", font=_idc_font(13, bold=True), fill=_IDC_BLUE)
    total.paste(front_img, (0, lbl_h))
    total.paste(back_img, (front_img.width + gap, lbl_h))
    return total


def _render_default_front(emp_id, row, company_name=None, logo_path=None, company_address=None, department=None,
                          company_website=None, company_phone=None):
    """Today's fixed ID-card front layout, now showing the employee's company
    name/logo in the header when set (falls back to the generic subtitle for
    companies with no branding on file -- no visual change for those)."""
    from PIL import Image, ImageDraw

    CW, CH = 500, 820
    front = Image.new("RGB", (CW, CH), _IDC_WHITE)
    fd = ImageDraw.Draw(front)

    fd.rectangle([(0, 0), (CW, 110)], fill=_IDC_BLUE)
    fd.ellipse([(CW - 100, -60), (CW + 60, 100)], fill=_IDC_MID)
    _idc_header_logo_and_name(fd, front, 110, company_name, logo_path,
                              "EMPLOYEE ID CARD", "Attendance Management System")
    fd.rectangle([(0, 108), (CW, 113)], fill=_IDC_GOLD)

    # PH_H trimmed from the original 190 (and everything below shifted up to
    # match) to help make room for the front QR's new, much larger size
    # further down -- still a clearly recognizable ID photo, just not as tall.
    fd.rectangle([(0, 113), (CW, 340)], fill=_IDC_LGRAY)
    PH_W, PH_H = 160, 160
    PH_X = CW // 2 - PH_W // 2
    PH_Y = 128
    fd.rounded_rectangle([(PH_X - 5, PH_Y - 5), (PH_X + PH_W + 5, PH_Y + PH_H + 5)], radius=8, fill=_IDC_GOLD)
    fd.rounded_rectangle([(PH_X - 2, PH_Y - 2), (PH_X + PH_W + 2, PH_Y + PH_H + 2)], radius=6, fill=_IDC_WHITE)
    photo_path = resolve_face_photo(emp_id) or ""
    try:
        ph = open_image(photo_path).convert("RGB").resize((PH_W, PH_H), Image.LANCZOS)
        front.paste(ph, (PH_X, PH_Y))
    except Exception:
        fd.rounded_rectangle([(PH_X, PH_Y), (PH_X + PH_W, PH_Y + PH_H)], radius=4, fill=_IDC_MID)
        ini = row[1][0].upper() if row and row[1] else "?"
        _idc_center_text(fd, ini, _idc_font(56, bold=True), CW, PH_Y + PH_H // 2 - 38, _IDC_WHITE)

    _idc_center_text(fd, (row[1] or "Unknown")[:24], _idc_font(18, bold=True), CW, 298, _IDC_DGRAY)
    _idc_center_text(fd, (row[2] or "Employee")[:28], _idc_font(12), CW, 322, _IDC_MGRAY)
    fd.rectangle([(40, 342), (CW - 40, 344)], fill=_IDC_PALE)

    # "Blood Group" used to also appear as a plain row here AND as the red
    # badge below (both reading the same row[7]) -- dropped from this list
    # (the badge stays) both to stop showing it twice and to free up the
    # room the much bigger QR below now needs.
    info_rows = [
        ("Employee ID", row[0] if row else "-"),
        ("Department", department or "-"),
        ("Email", row[3] if row and row[3] else "-"),
        ("Phone", row[8] if row and row[8] else "-"),
    ]
    ROW_H = 38
    y = 368
    for i, (lbl, val) in enumerate(info_rows):
        if i % 2 == 0:
            fd.rectangle([(0, y - 4), (CW, y + ROW_H - 6)], fill=_IDC_LGRAY)
        _idc_center_text(fd, lbl, _idc_font(10), CW, y + 2, _IDC_MGRAY)
        _idc_center_text(fd, str(val)[:34], _idc_font(13, bold=True), CW, y + 17, _IDC_DGRAY)
        y += ROW_H

    # The front QR is now the card's ONLY QR (the back's copy was removed --
    # see _render_default_back) and sized for real phone-camera scanning
    # (~2x the old convenience-copy size), so it needs to be guaranteed a
    # fixed amount of room at the bottom -- reserved below via max_qr_y --
    # rather than just trailing whatever optional content (blood group,
    # office address) happened to be on file. The office address is only
    # drawn when it actually fits above that reserved zone; a long address
    # on an employee who also has a blood group on file is the one
    # combination tight enough that it may be omitted here (still shown in
    # full on /employee_detail and everywhere else -- this is a printed
    # card's limited space, not the record itself).
    QS_SMALL = 120
    QR_MARGIN = 12
    qr_block_h = QS_SMALL + 2 * QR_MARGIN
    max_qr_y = (CH - 64) - qr_block_h

    bg_val = row[7] if row and row[7] else None
    addr_y = y + 6
    if bg_val:
        font_bg = _idc_font(13, bold=True)
        text_w = _idc_text_width(fd, bg_val, font_bg)
        icon_w, icon_h, gap = 13, 18, 6
        bw = 14 + icon_w + gap + text_w + 14
        bx = (CW - bw) // 2
        by = addr_y
        fd.rounded_rectangle([(bx, by), (bx + bw, by + 32)], radius=16, fill=_IDC_RED)
        icon_x, icon_y = bx + 14, by + (32 - icon_h) // 2
        _idc_blood_drop(fd, icon_x, icon_y, icon_w, icon_h, _IDC_WHITE)
        fd.text((icon_x + icon_w + gap, by + 8), _idc_safe_text(bg_val), font=font_bg, fill=_IDC_WHITE)
        addr_y = by + 40

    if company_address:
        addr_font = _idc_font(11)
        addr_lines = _idc_wrap_text(fd, company_address, addr_font, CW - 80, max_lines=2)
        addr_block_h = 10 + 12 + 16 * len(addr_lines)
        if addr_lines and addr_y + addr_block_h + 6 <= max_qr_y:
            addr_y += 10
            fd.rectangle([(60, addr_y), (CW - 60, addr_y + 2)], fill=_IDC_PALE)
            addr_y += 12
            _idc_center_text(fd, "OFFICE ADDRESS", _idc_font(9, bold=True), CW, addr_y, _IDC_MGRAY)
            addr_y += 16
            for line in addr_lines:
                _idc_center_text(fd, line, addr_font, CW, addr_y, _IDC_DGRAY)
                addr_y += 16

    qr_x = CW // 2 - QS_SMALL // 2
    qr_y = min(addr_y + 6, max_qr_y)
    try:
        fd.rounded_rectangle([(qr_x - QR_MARGIN, qr_y - QR_MARGIN), (qr_x + QS_SMALL + QR_MARGIN, qr_y + QS_SMALL + QR_MARGIN)], radius=10, fill=_IDC_WHITE)
        qr_img = _idc_qr_image(emp_id).convert("RGB").resize((QS_SMALL, QS_SMALL), Image.LANCZOS)
        front.paste(qr_img, (qr_x, qr_y))
    except Exception as exc:
        # Card still renders without the front QR -- not fatal (this is now
        # the card's only QR, so worth a trace if someone reports one missing).
        app_log.warning("ID card front-QR paste failed for %s: %s", emp_id, exc, exc_info=True)

    fd.rectangle([(0, CH - 60), (CW, CH)], fill=_IDC_BLUE)
    fd.rectangle([(0, CH - 62), (CW, CH - 60)], fill=_IDC_GOLD)
    _idc_footer_contact(fd, CW, CH, company_name, company_website, company_phone)
    return front


def _render_default_back(emp_id, row, logo_path=None, emergency_name=None, emergency_phone=None,
                         emergency_relation=None, company_name=None, manager_name=None,
                         shift_start=None, shift_end=None, work_mode=None,
                         company_website=None, company_phone=None):
    """Today's fixed ID-card back layout, now also showing the company logo
    and name in the header (matching the front) and the employee's emergency
    contact in place of the generic "return to HR" line when one is on file."""
    from PIL import Image, ImageDraw

    CW, CH = 500, 820
    back = Image.new("RGB", (CW, CH), _IDC_LGRAY)
    bd = ImageDraw.Draw(back)

    bd.rectangle([(0, 0), (CW, 110)], fill=_IDC_BLUE)
    bd.ellipse([(CW - 100, -60), (CW + 60, 100)], fill=_IDC_MID)
    _idc_header_logo_and_name(bd, back, 110, company_name, logo_path,
                              "ATTENDANCE MANAGEMENT SYSTEM", "Employee Attendance Card")
    bd.rectangle([(0, 108), (CW, 113)], fill=_IDC_GOLD)

    # No QR on the back anymore -- the front's own QR (see _render_default_front,
    # now sized for easy phone-camera scanning) is the card's only one. The
    # info block below is centered in the space between the header and footer
    # bars that the QR + "Scan to Mark Attendance" caption used to occupy,
    # rather than left pinned to the top with a big empty gap above the footer.
    sub_info = [
        ("Name", (row[1] or "-")[:26] if row else "-"),
        ("Designation", (row[2] or "-")[:26] if row else "-"),
        ("Shift", (row[6] or "-")[:26] if row and len(row) > 6 else "-"),
        ("Shift Timing", _idc_shift_timing_text(shift_start, shift_end)),
        ("Work Mode", _idc_work_mode_text(work_mode)),
        ("Reporting Manager", (manager_name or "-")[:26]),
        ("Blood Group", (row[7] or "-") if row else "-"),
    ]
    _content_h = len(sub_info) * 30 + 68  # 68 = divider + emergency/return-to-HR block below
    _zone_top, _zone_bottom = 123, CH - 110
    sy = _zone_top + max(0, (_zone_bottom - _zone_top - _content_h) // 2)
    for lbl2, val2 in sub_info:
        _idc_center_text(bd, lbl2, _idc_font(10), CW, sy, _IDC_MGRAY)
        if lbl2 == "Blood Group" and val2 and val2 != "-":
            font_bg = _idc_font(12, bold=True)
            text_w = _idc_text_width(bd, val2, font_bg)
            icon_w, icon_h, gap = 11, 15, 5
            start_x = (CW - (icon_w + gap + text_w)) // 2
            _idc_blood_drop(bd, start_x, sy + 15, icon_w, icon_h, _IDC_RED)
            bd.text((start_x + icon_w + gap, sy + 14), _idc_safe_text(val2), font=font_bg, fill=_IDC_DGRAY)
        else:
            _idc_center_text(bd, val2, _idc_font(12, bold=True), CW, sy + 14, _IDC_DGRAY)
        sy += 30

    bd.rectangle([(36, sy + 8), (CW - 36, sy + 10)], fill=(203, 213, 225))
    if emergency_name:
        rel_suffix = f" ({emergency_relation})" if emergency_relation else ""
        _idc_center_text(bd, "Emergency Contact:", _idc_font(10), CW, sy + 18, _IDC_MGRAY)
        _idc_center_text(bd, (emergency_name + rel_suffix)[:34], _idc_font(12, bold=True), CW, sy + 34, _IDC_BLUE)
        if emergency_phone:
            _idc_center_text(bd, emergency_phone[:34], _idc_font(10), CW, sy + 54, _IDC_MGRAY)
    else:
        _idc_center_text(bd, "If found, please return to:", _idc_font(10), CW, sy + 18, _IDC_MGRAY)
        _idc_center_text(bd, "HR Department", _idc_font(12, bold=True), CW, sy + 34, _IDC_BLUE)
        if row and row[3]:
            _idc_center_text(bd, row[3][:34], _idc_font(10), CW, sy + 54, _IDC_MGRAY)

    bd.rectangle([(0, CH - 100), (CW, CH - 68)], fill=_IDC_DARK)
    bd.rectangle([(0, CH - 60), (CW, CH)], fill=_IDC_BLUE)
    bd.rectangle([(0, CH - 62), (CW, CH - 60)], fill=_IDC_GOLD)
    _idc_footer_contact(bd, CW, CH, company_name, company_website, company_phone)
    return back


_ID_CARD_TEXT_FIELDS = {
    "name", "employee_id", "designation", "email", "phone", "blood_group",
    "date_of_joining", "company_address", "website",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relation",
    "department", "shift", "reporting_manager", "shift_timing", "work_mode", "company_phone",
}


def _render_custom_side(image_path, fields, side, emp_id, row, logo_path, company_address=None,
                        company_website=None, company_phone=None):
    """Render one side of an admin-uploaded custom ID card template: opens
    the template image at its own native size and pastes/draws each
    admin-placed field (photo/logo/qr/text) at its saved normalized (0-1)
    position, converted to that image's actual pixel dimensions."""
    from PIL import Image, ImageDraw

    img = Image.open(_idc_static_path(image_path)).convert("RGB")
    original = img.copy()
    draw = ImageDraw.Draw(img)
    W, H = img.size
    joined = row[5]

    def _at(idx):
        return row[idx] if len(row) > idx else None

    values = {
        "name": row[1] or "-",
        "employee_id": row[0] or "-",
        "designation": row[2] or "-",
        "email": row[3] or "-",
        "phone": row[8] or "-",
        "blood_group": row[7] or "-",
        "date_of_joining": joined.strftime("%d-%m-%Y") if joined else "-",
        "company_address": company_address or "-",
        "website": company_website or "-",
        "company_phone": company_phone or "-",
        "department": _at(9) or "-",
        "reporting_manager": _at(10) or "-",
        "emergency_contact_name": _at(11) or "-",
        "emergency_contact_phone": _at(12) or "-",
        "emergency_contact_relation": _at(13) or "-",
        "shift": _at(6) or "-",
        "shift_timing": _idc_shift_timing_text(_at(14), _at(15)),
        "work_mode": _idc_work_mode_text(_at(16)),
    }

    for key, box in fields.items():
        if (box.get("side") or "front") != side:
            continue
        try:
            x = int(round(box["x"] * W))
            y = int(round(box["y"] * H))
            w = int(round(box["w"] * W))
            h = int(round(box["h"] * H))
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue

        if key == "photo":
            photo_path = resolve_face_photo(emp_id) or ""
            try:
                ph = open_image(photo_path).convert("RGB").resize((w, h), Image.LANCZOS)
                img.paste(ph, (x, y))
            except Exception:
                draw.rectangle([(x, y), (x + w, y + h)], fill=_IDC_MID)
                ini = row[1][0].upper() if row and row[1] else "?"
                _idc_box_text(draw, ini, (x, y, w, h), _IDC_WHITE, font_size=max(10, min(w, h) // 2), bold=True)
        elif key == "logo":
            if logo_path:
                try:
                    from PIL import ImageOps
                    logo_img = Image.open(_idc_static_path(logo_path)).convert("RGBA")
                    fitted = ImageOps.contain(logo_img, (w, h), Image.LANCZOS)
                    bg = _idc_parse_color(box.get("bg_color"), None) or _idc_box_bg_color(original, (x, y, w, h))
                    off_x = (w - fitted.width) // 2
                    off_y = (h - fitted.height) // 2
                    if box.get("round"):
                        mask = Image.new("L", (w, h), 0)
                        ImageDraw.Draw(mask).ellipse([(0, 0), (w, h)], fill=255)
                        canvas = Image.new("RGBA", (w, h), bg + (255,))
                        canvas.paste(fitted, (off_x, off_y), fitted)
                        canvas.putalpha(mask)
                        img.paste(canvas, (x, y), canvas)
                    else:
                        draw.rectangle([(x, y), (x + w, y + h)], fill=bg)
                        img.paste(fitted, (x + off_x, y + off_y), fitted)
                except Exception as exc:
                    app_log.warning("ID card custom-template logo paste failed for %s: %s", emp_id, exc, exc_info=True)
        elif key == "qr":
            try:
                qr_img = _idc_qr_image(emp_id).convert("RGB").resize((w, h), Image.LANCZOS)
                img.paste(qr_img, (x, y))
            except Exception as exc:
                app_log.warning("ID card custom-template QR paste failed for %s: %s", emp_id, exc, exc_info=True)
        elif key in _ID_CARD_TEXT_FIELDS:
            font_size = box.get("font_size", 14)
            bg = _idc_parse_color(box.get("bg_color"), None) or _idc_box_bg_color(original, (x, y, w, h))
            color = _idc_parse_color(box.get("color"), None) or _idc_contrast_color(bg)
            if box.get("square"):
                draw.rectangle([(x, y), (x + w, y + h)], fill=bg)
            else:
                radius = max(0, min(w, h) // 2 - 1)
                try:
                    draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=radius, fill=bg)
                except ValueError:
                    draw.rectangle([(x, y), (x + w, y + h)], fill=bg)
            _idc_box_text(draw, values.get(key, "-"), (x, y, w, h), color,
                          font_size=font_size, bold=bool(box.get("bold")))
    return img


def _build_id_card_buf(emp_id):
    """Generate the front+back ID card PNG and return a BytesIO buffer, or
    None if not found. Renders the employee's company's custom template when
    one is configured, else the default generated design (company-branded
    when the company has a logo/name on file)."""
    import io as _io2

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT e.employee_id, e.name, e.role, e.email, e.face_image, e.date_of_joining,
               sh.name AS shift_name, e.blood_group, e.phone,
               COALESCE(e.department,''), COALESCE(e.manager_name,''),
               e.emergency_contact_name, e.emergency_contact_phone, e.emergency_contact_relation,
               sh.start_time, sh.end_time, e.work_mode,
               c.name, COALESCE(c.logo_path,''), t.front_image, t.back_image, t.fields,
               COALESCE(c.address,''), COALESCE(c.website,''), COALESCE(c.phone,'')
        FROM employees e
        LEFT JOIN shifts sh ON e.shift_id = sh.id
        LEFT JOIN companies c ON e.company_id = c.id
        LEFT JOIN id_card_templates t ON t.company_id = c.id
        WHERE e.employee_id = %s
    """, (emp_id,))
    full_row = cursor.fetchone()
    if not full_row:
        cursor.execute("""
            SELECT e.employee_id, e.name, e.role, e.email, e.face_image, e.date_of_joining,
                   NULL, e.blood_group, e.phone,
                   COALESCE(e.department,''), COALESCE(e.manager_name,''),
                   e.emergency_contact_name, e.emergency_contact_phone, e.emergency_contact_relation,
                   NULL, NULL, e.work_mode,
                   c.name, COALESCE(c.logo_path,''), t.front_image, t.back_image, t.fields,
                   COALESCE(c.address,''), COALESCE(c.website,''), COALESCE(c.phone,'')
            FROM employees e
            LEFT JOIN companies c ON e.company_id = c.id
            LEFT JOIN id_card_templates t ON t.company_id = c.id
            WHERE e.employee_id=%s
        """, (emp_id,))
        full_row = cursor.fetchone()
    cursor.close()
    db.close()

    if not full_row:
        return None

    row = full_row[:17]
    row = (row[:7] + (decrypt_pii(row[7]),) + row[8:11]
           + tuple(decrypt_pii(v) for v in row[11:14]) + row[14:17])
    # [7]=blood_group, [11:14]=emergency contact, [14:17]=shift_start,shift_end,work_mode
    (company_name, logo_path_raw, front_image, back_image, fields_raw,
     company_address_raw, company_website_raw, company_phone_raw) = full_row[17:25]
    logo_path = logo_path_raw or None
    company_address = company_address_raw or None
    company_website = company_website_raw or None
    company_phone = company_phone_raw or None
    if not company_name or not logo_path:
        # Most tenants never populate the multi-company `companies` table
        # (that's an opt-in feature) -- their real org name/logo live in
        # company_settings instead (set from the Settings page), so fall
        # back to that before giving up and using the generic header.
        _co = get_company_settings()
        if not company_name:
            _co_name = _co.get("company_name")
            company_name = _co_name if _co_name and _co_name != "My Company" else company_name
        if not logo_path:
            _co_logo = (_co.get("logo_url") or "").split("?")[0].strip().lstrip("/")
            if _co_logo.startswith("static/"):
                _co_logo = _co_logo[len("static/"):]
            logo_path = _co_logo or None
    department, manager_name = row[9], row[10]
    emergency_name, emergency_phone, emergency_relation = row[11], row[12], row[13]
    shift_start, shift_end, work_mode = row[14], row[15], row[16]

    try:
        fields = json.loads(fields_raw) if fields_raw else {}
    except (ValueError, TypeError):
        fields = {}

    if front_image:
        front = _render_custom_side(front_image, fields, "front", emp_id, row, logo_path, company_address,
                                    company_website, company_phone)
    else:
        front = _render_default_front(emp_id, row, company_name, logo_path, company_address, department,
                                      company_website, company_phone)

    if back_image:
        back = _render_custom_side(back_image, fields, "back", emp_id, row, logo_path, company_address,
                                   company_website, company_phone)
    else:
        back = _render_default_back(emp_id, row, logo_path, emergency_name, emergency_phone,
                                    emergency_relation, company_name, manager_name,
                                    shift_start, shift_end, work_mode, company_website, company_phone)

    total = _idc_combine(front, back)
    buf = _io2.BytesIO()
    total.save(buf, format="PNG", dpi=(200, 200))
    buf.seek(0)
    return buf


@employees_bp.route("/admin_id_card/<emp_id>")
@admin_required
def admin_id_card(emp_id):
    from flask import send_file
    buf = _build_id_card_buf(emp_id)
    if buf is None:
        return "Employee not found", 404
    return send_file(buf, as_attachment=True,
                     download_name=f"IDCard_{emp_id}.png",
                     mimetype="image/png")


@employees_bp.route("/admin_view_id_card/<emp_id>")
@admin_required
def admin_view_id_card(emp_id):
    from flask import send_file
    buf = _build_id_card_buf(emp_id)
    if buf is None:
        return "Employee not found", 404
    return send_file(buf, as_attachment=False,
                     download_name=f"IDCard_{emp_id}.png",
                     mimetype="image/png")


@employees_bp.route("/api/employees/<emp_id>/id_card")
@api_required
@api_role_required("admin", HR_ROLE)
def api_employee_id_card(emp_id):
    """Bearer-token twin of admin_view_id_card() above, for the mobile
    app -- admin_required/employee_required are session-cookie-only (see
    their own definitions in utils/auth.py), which a token-only mobile
    client never has, so neither existing route is reachable from mobile
    as-is. HR-scoped the same way every other HR-facing endpoint added
    this session is: an HR token may only fetch a card for an employee
    actually assigned to that HR account (assigned_hr_username), never
    any employee in the company. api_role_required above already
    confirmed the caller is a real, active admin or HR account.

    Returns base64 JSON, not a raw image stream -- axios in React Native
    has no reliable blob/arraybuffer download path (see
    fetchSalaryReportExport's comment in mobile/src/api/client.js, the
    established precedent for exactly this problem); the mobile client
    writes this to a local file via expo-file-system and either displays
    or shares it from there."""
    import base64
    from flask import g as _flask_g

    db = get_db_connection()
    cursor = db.cursor(buffered=True)
    cursor.execute("SELECT role FROM admin_users WHERE username=%s", (_flask_g.api_user,))
    role_row = cursor.fetchone()
    if role_row and role_row[0] == HR_ROLE:
        cursor.execute("SELECT assigned_hr_username FROM employees WHERE employee_id=%s", (emp_id,))
        emp_row = cursor.fetchone()
        if not emp_row or emp_row[0] != _flask_g.api_user:
            cursor.close()
            db.close()
            return jsonify({"ok": False, "msg": "Employee not found"}), 404
    cursor.close()
    db.close()

    buf = _build_id_card_buf(emp_id)
    if buf is None:
        return jsonify({"ok": False, "msg": "Employee not found"}), 404
    return jsonify({"ok": True, "image_base64": base64.b64encode(buf.read()).decode("ascii")})
