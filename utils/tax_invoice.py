# -*- coding: utf-8 -*-
"""GST tax invoices for every successful payment a company makes to HRZEST
(signup, seat top-up, monthly Auto-Debit or overdue charge).

  on_payment_completed(kind_key, row_id)
      Called right after a payment is recorded as paid. Issues the invoice
      (once per payment), emails it as a PDF to the company's admins and
      tells the platform (super) admins. Never raises: the payment itself is
      already safely recorded, so an invoice/mail problem is only logged.

  issue_invoice(kind_key, row_id) -> dict | None
      The frozen invoice record, issuing it first if needed. Used by the PDF
      download routes too, so payments made before invoices existed still
      get one on first download.

  build_tax_invoice_pdf(inv) -> bytes

kind_key / row_id are utils/payment_history.py's: "signup" (payment_orders),
"seat_topup" (seat_topup_orders), "invoice" (monthly_invoices).

Prices are GST-inclusive: the amount the customer paid is split into the
taxable value and GST (CGST + SGST inside the seller's state, IGST outside).
The seller's own details come from the INVOICE_SELLER_* settings (see
.env.example) and are frozen into each invoice when it's issued.
"""
import datetime
import json
import os
from html import escape

from flask import g as _g

from extensions import app_log
from database import get_master_db, get_db_connection

KINDS = ("signup", "seat_topup", "invoice")

# GST state codes (first two digits of a GSTIN).
GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}
_STATE_NAME_TO_CODE = {v.lower(): k for k, v in GST_STATE_CODES.items()}


# ── settings ─────────────────────────────────────────────────────────────────
def _env(name, default=""):
    return (os.environ.get(name) or "").strip() or default


SELLER_ADDRESS = "301, Rensri Arcade, Bhagyanagar Colony|KPHB, Kukatpally, Hyderabad|Telangana - 500072"
SELLER_GSTIN = "36AALCG8469R1ZE"


def seller_details():
    """HRZEST's own details as printed on every invoice. The defaults are
    the real ones; INVOICE_SELLER_* only overrides them (the address takes
    "|" between lines)."""
    state_code = _env("INVOICE_SELLER_STATE_CODE", "36")
    return {
        "brand": "HRZEST",
        "name": _env("INVOICE_SELLER_NAME", "HRZEST"),
        "address_lines": [ln.strip() for ln in _env("INVOICE_SELLER_ADDRESS", SELLER_ADDRESS).split("|") if ln.strip()],
        "gstin": _env("INVOICE_SELLER_GSTIN", SELLER_GSTIN).upper(),
        "state": _env("INVOICE_SELLER_STATE", GST_STATE_CODES.get(state_code, "Telangana")),
        "state_code": state_code,
        "email": _env("INVOICE_SELLER_EMAIL", "contact@hrzest.com"),
        "phone": _env("INVOICE_SELLER_PHONE", "+91 89199 01489"),
        "website": "hrzest.com",
        "signatory": _env("INVOICE_SIGNATORY", "Peddala Ranjith Kumar"),
    }


def gst_percent():
    try:
        return max(0, int(_env("INVOICE_GST_PERCENT", "18")))
    except ValueError:
        return 18


def sac_code():
    # 997331: licensing services for the right to use computer software.
    return _env("INVOICE_SAC_CODE", "997331")


# ── amounts ──────────────────────────────────────────────────────────────────
def split_gst(total_paise, percent, intra_state):
    """GST-inclusive total -> (taxable, cgst, sgst, igst), all in paise and
    adding back up to total_paise exactly."""
    total_paise = int(total_paise or 0)
    taxable = round(total_paise * 100 / (100 + percent)) if percent else total_paise
    tax = total_paise - taxable
    if intra_state:
        cgst = tax // 2
        return taxable, cgst, tax - cgst, 0
    return taxable, 0, 0, tax


def rupees(paise):
    """12345 -> "123.45" (Indian digit grouping)."""
    paise = int(paise or 0)
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}{s}.{frac:02d}"


_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
         "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _words_below_1000(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else ""))
    elif n:
        out.append(_ONES[n])
    return " ".join(out)


def amount_in_words(paise):
    """363800 -> "Three Thousand Six Hundred Thirty Eight Rupees Only" (Indian
    system: thousand, lakh, crore)."""
    whole, frac = divmod(int(paise or 0), 100)
    if whole == 0 and frac == 0:
        return "Zero Rupees Only"
    parts = []
    for size, name in ((10 ** 7, "Crore"), (10 ** 5, "Lakh"), (1000, "Thousand")):
        if whole >= size:
            parts.append(_words_below_1000(whole // size) if whole // size < 1000
                         else amount_in_words((whole // size) * 100).replace(" Rupees Only", ""))
            parts[-1] += " " + name
            whole %= size
    if whole:
        parts.append(_words_below_1000(whole))
    text = " ".join(p for p in parts if p)
    text = (text + " Rupees") if text else ""
    if frac:
        text = (text + " and " if text else "") + _words_below_1000(frac) + " Paise"
    return text + " Only"


def financial_year(d):
    """date -> "2026-27" (April to March)."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _month_end(d):
    return (d.replace(day=1) + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)


def _period_label(start, end):
    """"November 2026" for a calendar month, else "10 Nov 2026 - 9 Dec 2026"."""
    if start.day == 1 and end == _month_end(start):
        return start.strftime("%B %Y")
    return f"{start.strftime('%d %b %Y')} - {end.strftime('%d %b %Y')}"


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ── the payment and the buyer ────────────────────────────────────────────────
def _load_payment(kind_key, row_id):
    """The paid payment row, normalized, or None (unknown or not paid)."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    try:
        if kind_key == "signup":
            cur.execute(
                "SELECT p.amount_paise, p.employee_count, COALESCE(p.paid_at, p.created_at), p.razorpay_order_id, "
                "p.razorpay_payment_id, t.db_name, COALESCE(t.company_name, p.company_name), p.admin_email "
                "FROM payment_orders p LEFT JOIN tenants t ON t.id = p.tenant_id "
                "WHERE p.id=%s AND p.status IN ('paid','provisioned')", (row_id,))
        elif kind_key == "seat_topup":
            cur.execute(
                "SELECT s.amount_paise, s.seats_purchased, COALESCE(s.paid_at, s.created_at), s.razorpay_order_id, "
                "s.razorpay_payment_id, s.tenant_schema, COALESCE(t.company_name, s.company_name), t.admin_email "
                "FROM seat_topup_orders s LEFT JOIN tenants t ON t.db_name = s.tenant_schema "
                "WHERE s.id=%s AND s.status='paid'", (row_id,))
        elif kind_key == "invoice":
            cur.execute(
                "SELECT m.amount_paise, m.employee_count, m.created_at, m.razorpay_order_id, "
                "m.razorpay_payment_id, m.tenant_schema, COALESCE(t.company_name, m.company_name), t.admin_email, "
                "m.billing_period, m.razorpay_subscription_id "
                "FROM monthly_invoices m LEFT JOIN tenants t ON t.db_name = m.tenant_schema "
                "WHERE m.id=%s AND m.status='paid'", (row_id,))
        else:
            return None
        r = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not r:
        return None
    p = {"amount_paise": int(r[0] or 0), "qty": max(1, int(r[1] or 1)), "paid_at": r[2],
         "order_id": r[3], "payment_id": r[4], "tenant_schema": r[5], "company_name": r[6],
         "admin_email": r[7]}
    paid_on = _as_date(p["paid_at"]) or datetime.date.today()
    ref = p["payment_id"] or p["order_id"] or ""
    is_test = ref.startswith("demo_")
    from utils.billing_cycle import tenant_anchor, period_end as cycle_period_end
    try:  # the company's plan date sets the period end; month ends if it can't be read
        anchor = tenant_anchor(p["tenant_schema"])[0] if p["tenant_schema"] else None
    except Exception:
        anchor = None
    if kind_key == "invoice":
        start = _as_date(r[8]) or paid_on.replace(day=1)
        end = cycle_period_end(anchor, start) if anchor and start >= anchor else _month_end(start)
        p.update(period_start=start, period_end=end,
                 description=f"HRZEST subscription for {_period_label(start, end)} "
                             f"(per employee per month, {p['qty']} employees)",
                 mode="Auto-Debit (Razorpay)" if r[9] and not p["order_id"] else "Online (Razorpay)")
    elif kind_key == "signup":
        end = cycle_period_end(anchor, paid_on) if anchor and paid_on >= anchor else _month_end(paid_on)
        p.update(period_start=paid_on, period_end=end,
                 description=f"HRZEST subscription, first month (per employee, {p['qty']} employees)",
                 mode="Online (Razorpay)")
    else:
        p.update(period_start=None, period_end=None,
                 description=f"HRZEST additional employee seats ({p['qty']})",
                 mode="Online (Razorpay)")
    if is_test:
        p["mode"] = "Test mode (no real payment)"
    return p


def _buyer_details(tenant_schema, company_name, admin_email):
    """The paying company as of now: name and GSTIN from the registry,
    address and contacts from its own Organization settings."""
    buyer = {"name": company_name or "—", "address_lines": [], "gstin": "", "state": "", "state_code": "",
             "email": admin_email or "", "phone": ""}
    if not tenant_schema:
        return buyer
    try:
        conn = get_master_db()
        cur = conn.cursor(buffered=True)
        cur.execute("SELECT gst_number, admin_email FROM tenants WHERE db_name=%s", (tenant_schema,))
        r = cur.fetchone()
        cur.close()
        conn.close()
        if r:
            buyer["gstin"] = (r[0] or "").strip().upper()
            buyer["email"] = buyer["email"] or (r[1] or "")
    except Exception as exc:
        app_log.warning("tax_invoice: tenant lookup failed for %s: %s", tenant_schema, exc)
    prev = getattr(_g, "tenant_db", None)
    try:
        _g.tenant_db = tenant_schema
        db = get_db_connection()
        cur = db.cursor(buffered=True)
        cur.execute("SELECT address, city, state, postal_code, country, contact_phone, contact_email "
                    "FROM company_settings LIMIT 1")
        r = cur.fetchone()
        cur.close()
        db.close()
        if r:
            address, city, state, pin, country, phone, email = (x.strip() if isinstance(x, str) else x for x in r)
            lines = [ln.strip() for ln in (address or "").replace("\r", "").split("\n") if ln.strip()]
            tail = ", ".join(x for x in (city, state) if x)
            if pin:
                tail = f"{tail} - {pin}" if tail else pin
            if tail:
                lines.append(tail)
            if country and country.lower() != "india":
                lines.append(country)
            buyer.update(address_lines=lines, state=state or "", phone=phone or "",
                         email=email or buyer["email"])
    except Exception as exc:
        app_log.warning("tax_invoice: company settings lookup failed for %s: %s", tenant_schema, exc)
    finally:
        _g.tenant_db = prev
    code = buyer["gstin"][:2] if len(buyer["gstin"]) == 15 and buyer["gstin"][:2] in GST_STATE_CODES else ""
    if not code and buyer["state"]:
        code = _STATE_NAME_TO_CODE.get(buyer["state"].lower(), "")
    buyer["state_code"] = code
    if code and not buyer["state"]:
        buyer["state"] = GST_STATE_CODES[code]
    return buyer


def _build_data(kind_key, row_id, invoice_no, issued_on, p):
    seller = seller_details()
    buyer = _buyer_details(p["tenant_schema"], p["company_name"], p["admin_email"])
    pct = gst_percent()
    # Place of supply is the buyer's state when known, else the seller's.
    pos_code = buyer["state_code"] or seller["state_code"]
    intra = pos_code == seller["state_code"]
    taxable, cgst, sgst, igst = split_gst(p["amount_paise"], pct, intra)
    return {
        "invoice_no": invoice_no, "invoice_date": issued_on.isoformat(), "kind_key": kind_key, "source_id": row_id,
        "seller": seller, "buyer": buyer,
        "place_of_supply": f"{GST_STATE_CODES.get(pos_code, buyer['state'] or seller['state'])} ({pos_code})",
        "intra_state": intra, "gst_percent": pct,
        "payment": {"mode": p["mode"], "reference": p["payment_id"] or "", "order_no": p["order_id"] or "",
                    "paid_on": (_as_date(p["paid_at"]) or issued_on).isoformat()},
        "period": ({"start": p["period_start"].isoformat(), "end": p["period_end"].isoformat()}
                   if p.get("period_start") else None),
        "items": [{
            "description": p["description"], "sac": sac_code(), "qty": p["qty"],
            "rate_paise": round(taxable / p["qty"]), "taxable_paise": taxable,
            "cgst_paise": cgst, "sgst_paise": sgst, "igst_paise": igst, "total_paise": p["amount_paise"],
        }],
        "totals": {"taxable_paise": taxable, "cgst_paise": cgst, "sgst_paise": sgst, "igst_paise": igst,
                   "tax_paise": cgst + sgst + igst, "total_paise": p["amount_paise"]},
        "amount_words": amount_in_words(p["amount_paise"]),
    }


# ── issuing ──────────────────────────────────────────────────────────────────
def _row_to_invoice(row):
    data = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    data["id"] = row[0]
    data["tenant_schema"] = row[2]
    return data


def get_invoice(kind_key, row_id):
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT id, data, tenant_schema FROM tax_invoices WHERE kind_key=%s AND source_id=%s",
                (kind_key, row_id))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return _row_to_invoice(row) if row else None


def issue_invoice(kind_key, row_id):
    """The invoice for one paid payment, issuing it now if it has none.
    None if the payment doesn't exist or isn't paid. The counter row is
    locked by its UPDATE until commit, so concurrent issues queue up; a
    loser of the per-payment race rolls back, counter included, so invoice
    numbers have no gaps."""
    if kind_key not in KINDS:
        return None
    existing = get_invoice(kind_key, row_id)
    if existing:
        return existing
    p = _load_payment(kind_key, row_id)
    if not p:
        return None
    issued_on = _as_date(p["paid_at"]) or datetime.date.today()
    fy = financial_year(issued_on)
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    try:
        cur.execute(
            "INSERT INTO invoice_counters (financial_year, last_no) VALUES (%s, 1) "
            "ON CONFLICT (financial_year) DO UPDATE SET last_no = invoice_counters.last_no + 1 RETURNING last_no",
            (fy,))
        number = cur.fetchone()[0]
        invoice_no = f"{_env('INVOICE_NUMBER_PREFIX', 'HRZ')}/{fy}/{number:05d}"
        data = _build_data(kind_key, row_id, invoice_no, issued_on, p)
        cur.execute(
            "INSERT INTO tax_invoices (invoice_no, kind_key, source_id, tenant_schema, data) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (kind_key, source_id) DO NOTHING",
            (invoice_no, kind_key, row_id, p["tenant_schema"], json.dumps(data)))
        if cur.rowcount == 1:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return get_invoice(kind_key, row_id)


def tenant_invoices(tenant_schema):
    """{(kind_key, source_id): invoice_no} for one company's issued invoices."""
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT kind_key, source_id, invoice_no FROM tax_invoices WHERE tenant_schema=%s", (tenant_schema,))
    out = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    cur.close()
    conn.close()
    return out


def invoice_filename(inv):
    return "Invoice_" + inv["invoice_no"].replace("/", "-") + ".pdf"


# ── PDF ──────────────────────────────────────────────────────────────────────
_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
_FONTS_READY = False


def _fonts():
    """DejaVu (bundled in static/fonts) prints the ₹ sign, which the
    built-in PDF fonts can't. Falls back to Helvetica."""
    global _FONTS_READY
    if not _FONTS_READY:
        try:
            from reportlab.lib.fonts import addMapping
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            pdfmetrics.registerFont(TTFont("HZSans", os.path.join(_STATIC, "fonts", "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("HZSans-Bold", os.path.join(_STATIC, "fonts", "DejaVuSans-Bold.ttf")))
            # So <b> inside a paragraph switches to the bold face.
            for bold, italic, face in ((0, 0, "HZSans"), (1, 0, "HZSans-Bold"), (0, 1, "HZSans"), (1, 1, "HZSans-Bold")):
                addMapping("HZSans", bold, italic, face)
            _FONTS_READY = "HZSans"
        except Exception as exc:
            app_log.warning("tax_invoice: DejaVu fonts unavailable, using Helvetica: %s", exc)
            _FONTS_READY = "Helvetica"
    return (_FONTS_READY, _FONTS_READY + "-Bold")


def _fmt_date(iso):
    d = _as_date(iso)
    return d.strftime("%d-%m-%Y") if d else "—"


def build_tax_invoice_pdf(inv):
    """A4 tax invoice in the classic Indian layout: seller/invoice details,
    bill-to, line items with the GST split, totals with the amount in words,
    and the signatory block with the HRZEST logo."""
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table

    regular, bold = _fonts()
    ink = colors.HexColor("#111827")
    muted = colors.HexColor("#4b5563")
    line = colors.HexColor("#9ca3af")
    head_bg = colors.HexColor("#eef2ff")
    brand = colors.HexColor("#1d4ed8")

    def st(name, size=8, font=None, color=ink, align=None, leading=None):
        kw = dict(fontName=font or regular, fontSize=size, leading=leading or size * 1.3, textColor=color)
        if align is not None:
            kw["alignment"] = align
        return ParagraphStyle(name, **kw)

    s = st("s")
    s_small = st("small", 7, color=muted)
    s_r = st("r", align=TA_RIGHT)
    s_rb = st("rb", font=bold, align=TA_RIGHT)
    s_c = st("c", align=TA_CENTER)
    s_cb = st("cb", font=bold, align=TA_CENTER)

    def P(text, style=s):
        return Paragraph(text, style)

    def esc(v):
        return escape(str(v or ""))

    def cell(label, value):
        return P(f"{label}<br/><b>{value}</b>")

    # Every row below is 182 mm wide: A4 minus the 14 mm margins.
    seller, buyer, t = inv["seller"], inv["buyer"], inv["totals"]
    intra = inv.get("intra_state", True)
    pct = inv.get("gst_percent", 18)

    logo_path = os.path.join(_STATIC, "brand", "hrzest-mark.png")
    logo = Image(logo_path, width=16 * mm, height=16 * mm) if os.path.exists(logo_path) else P("")

    # ── title row
    title = Table([[
        Table([[logo, P(f"<b>{esc(seller['brand'])}</b>", st("brand", 16, bold, brand))]],
              colWidths=[18 * mm, 45 * mm], style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]),
        P("Tax Invoice", st("t", 12, bold, align=TA_CENTER)),
        P("ORIGINAL", st("o", 10, bold, align=TA_RIGHT)),
    ]], colWidths=[70 * mm, 52 * mm, 60 * mm],
        style=[("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
               ("RIGHTPADDING", (0, 0), (-1, -1), 0)])

    # ── seller + invoice details
    seller_lines = [f"<b>{esc(seller['name'])}</b>"] + [esc(x) for x in seller["address_lines"]]
    seller_lines.append(f"State Name: {esc(seller['state'])}, Code: {esc(seller['state_code'])}")
    if seller["gstin"]:
        seller_lines.append(f"<b>GSTIN:</b> {esc(seller['gstin'])}")
    seller_lines.append(f"Email: {esc(seller['email'])}")
    seller_lines.append(f"Phone: {esc(seller['phone'])} &nbsp; Web: {esc(seller['website'])}")
    pay = inv["payment"]
    top = Table([
        [P("<b>Bill By:</b><br/>" + "<br/>".join(seller_lines)),
         cell("Invoice No:", esc(inv["invoice_no"])), cell("Invoice Date:", _fmt_date(inv["invoice_date"]))],
        ["", cell("Payment Ref:", esc(pay["reference"] or "—")), cell("Payment Mode:", esc(pay["mode"]))],
        ["", cell("Order No:", esc(pay["order_no"] or "—")), cell("Paid On:", _fmt_date(pay["paid_on"]))],
    ], colWidths=[88 * mm, 50 * mm, 44 * mm], style=[
        ("SPAN", (0, 0), (0, 2)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.6, line), ("BOX", (0, 0), (-1, -1), 0.8, ink),
    ])

    # ── buyer + supply details
    buyer_lines = [f"<b>{esc(buyer['name'])}</b>"] + [esc(x) for x in buyer["address_lines"]]
    if buyer.get("phone"):
        buyer_lines.append(f"<b>Phone:</b> {esc(buyer['phone'])}")
    if buyer.get("email"):
        buyer_lines.append(f"<b>Email:</b> {esc(buyer['email'])}")
    buyer_lines.append(f"<b>GSTIN:</b> {esc(buyer['gstin']) or 'Unregistered'}")
    if buyer.get("state"):
        buyer_lines.append(f"<b>State Name:</b> {esc(buyer['state'])}"
                           + (f" &nbsp; <b>Code:</b> {esc(buyer['state_code'])}" if buyer.get("state_code") else "")
                           + " &nbsp; <b>Country:</b> India")
    period = inv.get("period")
    right = [cell("Date of Supply:", _fmt_date(pay["paid_on"])), cell("Place of Supply:", esc(inv["place_of_supply"]))]
    if period:
        right.append(cell("Plan Period:", f"{_fmt_date(period['start'])} to {_fmt_date(period['end'])}"))
    middle = Table([[P("<b>BILL TO PARTY:</b><br/>" + "<br/>".join(buyer_lines)),
                     Table([[r] for r in right], colWidths=[52 * mm],
                           style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)])]],
                   colWidths=[128 * mm, 54 * mm],
                   style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.6, line),
                          ("BOX", (0, 0), (-1, -1), 0.8, ink)])

    # ── items
    if intra:
        head = ["#", "ITEM / SERVICE", "SAC", "QTY", "RATE (₹)", "TAXABLE (₹)", "GST %",
                "CGST (₹)", "SGST (₹)", "TOTAL (₹)"]
        widths = [7, 50, 16, 10, 17, 19, 10, 16, 16, 21]
    else:
        head = ["#", "ITEM / SERVICE", "SAC", "QTY", "RATE (₹)", "TAXABLE (₹)", "GST %", "IGST (₹)", "TOTAL (₹)"]
        widths = [7, 66, 16, 10, 17, 19, 10, 16, 21]
    rows = [[P(h, st("h", 7, bold, align=TA_CENTER)) for h in head]]
    qty_total = 0
    for i, it in enumerate(inv["items"], 1):
        qty_total += it["qty"]
        r = [P(str(i), s_c), P(esc(it["description"])), P(esc(it["sac"]), s_c), P(str(it["qty"]), s_c),
             P(rupees(it["rate_paise"]), s_r), P(rupees(it["taxable_paise"]), s_r), P(f"{pct}", s_c)]
        r += ([P(rupees(it["cgst_paise"]), s_r), P(rupees(it["sgst_paise"]), s_r)] if intra
              else [P(rupees(it["igst_paise"]), s_r)])
        r.append(P(rupees(it["total_paise"]), s_r))
        rows.append(r)
    total_row = [P(""), P("<b>TOTAL</b>"), P(""), P(f"<b>{qty_total}</b>", s_c), P(""),
                 P(rupees(t["taxable_paise"]), s_rb), P("")]
    total_row += ([P(rupees(t["cgst_paise"]), s_rb), P(rupees(t["sgst_paise"]), s_rb)] if intra
                  else [P(rupees(t["igst_paise"]), s_rb)])
    total_row.append(P(rupees(t["total_paise"]), s_rb))
    rows.append(total_row)
    items = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1, style=[
        ("BACKGROUND", (0, 0), (-1, 0), head_bg), ("BACKGROUND", (0, -1), (-1, -1), head_bg),
        ("GRID", (0, 0), (-1, -1), 0.6, line), ("BOX", (0, 0), (-1, -1), 0.8, ink),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ])

    # ── totals
    sums = [["Total amount before Tax (₹)", rupees(t["taxable_paise"])]]
    if intra:
        half = pct / 2
        sums += [[f"CGST @ {half:g}% (₹)", rupees(t["cgst_paise"])], [f"SGST @ {half:g}% (₹)", rupees(t["sgst_paise"])]]
    else:
        sums.append([f"IGST @ {pct}% (₹)", rupees(t["igst_paise"])])
    sums += [["Total Tax amount (₹)", rupees(t["tax_paise"])], ["Total amount after Tax (₹)", rupees(t["total_paise"])]]
    sums_tbl = Table([[P(a), P(b, s_r)] for a, b in sums] + [[P("<b>TOTAL (₹)</b>"), P(rupees(t["total_paise"]), s_rb)]],
                     colWidths=[52 * mm, 28 * mm],
                     style=[("GRID", (0, 0), (-1, -1), 0.6, line), ("BACKGROUND", (0, -1), (-1, -1), head_bg)])
    terms = ("<b>Terms and Conditions</b><br/>Declaration: We declare that this invoice shows the actual price of "
             "the services described and that all particulars are true and correct. Prices are inclusive of GST. "
             "Subscription fees once paid are not refundable. Subject to Hyderabad jurisdiction.")
    bottom = Table([[
        Table([[P("<b>Total Invoice Amount in Words</b><br/>" + esc(inv["amount_words"]))],
               [P(terms, st("terms", 7.5))]], colWidths=[100 * mm],
              style=[("LINEBELOW", (0, 0), (-1, 0), 0.6, line), ("LEFTPADDING", (0, 0), (-1, -1), 2)]),
        sums_tbl,
    ]], colWidths=[102 * mm, 80 * mm], style=[("VALIGN", (0, 0), (-1, -1), "TOP"),
                                              ("BOX", (0, 0), (-1, -1), 0.8, ink),
                                              ("LEFTPADDING", (1, 0), (1, 0), 0), ("RIGHTPADDING", (1, 0), (1, 0), 0),
                                              ("TOPPADDING", (1, 0), (1, 0), 0),
                                              ("LINEAFTER", (0, 0), (0, 0), 0.6, line)])

    # ── footer: E&OE, PAID stamp, signatory with the logo
    stamp = Table([[P("PAID", st("paid", 14, bold, colors.HexColor("#1e3a8a"), TA_CENTER, 16))]],
                  colWidths=[26 * mm], rowHeights=[10 * mm],
                  style=[("BOX", (0, 0), (-1, -1), 1.6, colors.HexColor("#1e3a8a")),
                         ("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
    sign_logo = (Image(logo_path, width=12 * mm, height=12 * mm) if os.path.exists(logo_path) else P(""))
    sign = Table([[P(f"<b>For, {esc(seller['name'])}</b>", s_cb)], [sign_logo],
                  [P(f"<b>{esc(seller['signatory'])}</b>", s_c)], [P("Authorised Signatory", s_small)]],
                 colWidths=[62 * mm], style=[("ALIGN", (0, 0), (-1, -1), "CENTER")])
    footer = Table([[P("<b>E. &amp; O.E</b>"), stamp, sign]], colWidths=[60 * mm, 60 * mm, 62 * mm],
                   style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "CENTER"),
                          ("BOX", (0, 0), (-1, -1), 0.8, ink)])

    note = P("This is a computer-generated invoice. Payment received with thanks.", st("n", 7, color=muted, align=TA_CENTER))

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=f"Tax Invoice {inv['invoice_no']}", author=seller["name"])
    for tbl in (top, middle, items, bottom, footer):
        tbl.hAlign = "CENTER"
    doc.build([title, Spacer(1, 3 * mm), top, middle, items, bottom, footer, Spacer(1, 3 * mm), note])
    return buf.getvalue()


# ── after a payment ──────────────────────────────────────────────────────────
def _platform_admin_emails():
    conn = get_master_db()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT email FROM platform_admins WHERE email IS NOT NULL AND email != ''")
    emails = sorted({r[0].strip() for r in cur.fetchall() if r[0] and r[0].strip()})
    cur.close()
    conn.close()
    return emails


def _summary_rows(inv):
    t = inv["totals"]
    rows = [("Invoice No", inv["invoice_no"]), ("Amount paid", f"₹{rupees(t['total_paise'])} (incl. GST ₹{rupees(t['tax_paise'])})"),
            ("For", inv["items"][0]["description"]), ("Paid on", _fmt_date(inv["payment"]["paid_on"])),
            ("Payment mode", inv["payment"]["mode"])]
    if inv.get("period"):
        rows.append(("Plan period", f"{_fmt_date(inv['period']['start'])} to {_fmt_date(inv['period']['end'])}"))
    return "".join(
        f'<tr><td style="padding:6px 10px;color:#64748b;">{escape(a)}</td>'
        f'<td style="padding:6px 10px;font-weight:600;">{escape(str(b))}</td></tr>' for a, b in rows)


def _send_invoice_emails(inv, pdf_bytes, slug):
    from utils.billing_notices import company_link, email_shell
    from utils.email_utils import get_billing_emails, get_email_config, send_email_async

    filename = invoice_filename(inv)
    company = inv["buyer"]["name"]
    amount = rupees(inv["totals"]["total_paise"])
    table = f'<table style="width:100%;border-collapse:collapse;font-size:14px;">{_summary_rows(inv)}</table>'
    prev = getattr(_g, "tenant_db", None)
    try:
        # The company's admins (not HR/managers), from its own schema.
        if inv.get("tenant_schema"):
            _g.tenant_db = inv["tenant_schema"]
            config = get_email_config()
            if config:
                history = company_link(slug, "/profile/payments") if slug else None
                body = (f"<p>Thank you! We've received your payment of <strong>₹{amount}</strong> for "
                        f"<strong>{escape(company)}</strong>. Your tax invoice is attached.</p>{table}"
                        + (f'<p style="margin-top:18px;"><a href="{escape(history)}" style="background:#1d4ed8;'
                           f'color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;">'
                           f'View payment history</a></p>' if history else ""))
                html_body = email_shell(body, "Payment received")
                for email in get_billing_emails():
                    send_email_async(email, f"Payment received — Invoice {inv['invoice_no']}", html_body, config,
                                     attachment_bytes=pdf_bytes, attachment_filename=filename)
        # The platform's own (super) admins.
        _g.tenant_db = None
        config = get_email_config()
        if config:
            body = (f"<p><strong>{escape(company)}</strong> just paid <strong>₹{amount}</strong>.</p>{table}"
                    f'<p style="color:#64748b;">Company: {escape(slug or inv.get("tenant_schema") or "—")}</p>')
            html_body = email_shell(body, "New payment")
            for email in _platform_admin_emails():
                send_email_async(email, f"[HRZEST] Payment ₹{amount} from {company} — {inv['invoice_no']}",
                                 html_body, config, attachment_bytes=pdf_bytes, attachment_filename=filename)
    finally:
        _g.tenant_db = prev


def on_payment_completed(kind_key, row_id):
    """Issue the invoice for a payment that was just recorded as paid and
    email it to the company's admins and the platform admins. Never raises."""
    try:
        inv = issue_invoice(kind_key, row_id)
        if not inv:
            app_log.warning("tax_invoice: no paid %s payment %s to invoice", kind_key, row_id)
            return None
        slug = None
        if inv.get("tenant_schema"):
            conn = get_master_db()
            cur = conn.cursor(buffered=True)
            cur.execute("SELECT subdomain FROM tenants WHERE db_name=%s", (inv["tenant_schema"],))
            r = cur.fetchone()
            cur.close()
            conn.close()
            slug = r[0] if r else None
        _send_invoice_emails(inv, build_tax_invoice_pdf(inv), slug)
        return inv
    except Exception as exc:
        app_log.error("tax_invoice: invoicing %s payment %s failed: %s", kind_key, row_id, exc, exc_info=True)
        return None
