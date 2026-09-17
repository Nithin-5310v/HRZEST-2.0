"""A simple downloadable payment receipt, mirroring the ReportLab pattern
blueprints/onboarding.py's _generate_offer_letter_pdf() already uses for
offer letters -- deliberately much smaller: one payment's worth of facts,
not a formatted letter body."""


def build_receipt_pdf(company_name, amount_paise, paid_at, reference_id, kind_label):
    """Returns PDF bytes for a single payment. Caller is responsible for
    having already re-verified the payment belongs to the requesting
    tenant -- this function only renders whatever it's given."""
    from io import BytesIO
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable
    from reportlab.lib.enums import TA_CENTER

    from utils.plan_limits import format_price_inr

    BLUE = rl_colors.HexColor("#1d4ed8")
    DARK = rl_colors.HexColor("#111827")
    GRAY = rl_colors.HexColor("#6b7280")
    LIGHT = rl_colors.HexColor("#f3f4f6")

    def ps(name, **kw):
        base = dict(fontName="Helvetica", fontSize=10, leading=14, textColor=DARK)
        base.update(kw)
        return ParagraphStyle(name, **base)

    s_normal = ps("normal")
    s_small = ps("small", fontSize=8, textColor=GRAY)
    s_title = ps("title", fontSize=18, fontName="Helvetica-Bold", textColor=BLUE, alignment=TA_CENTER)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=25 * mm, rightMargin=25 * mm, topMargin=25 * mm, bottomMargin=25 * mm,
    )
    story = [
        HRFlowable(width="100%", thickness=3, color=BLUE, spaceAfter=14),
        Paragraph("Payment Receipt", s_title),
        Spacer(1, 4 * mm),
        Paragraph("HRZEST", ps("brand", fontSize=11, fontName="Helvetica-Bold", alignment=TA_CENTER)),
        Spacer(1, 10 * mm),
        Table(
            [
                ["Company", company_name or "—"],
                ["Payment for", kind_label],
                ["Amount", format_price_inr(amount_paise)],
                ["Date", paid_at.strftime("%d %B %Y") if paid_at else "—"],
                ["Reference ID", reference_id or "—"],
            ],
            colWidths=[45 * mm, 110 * mm],
            style=TableStyle([
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (0, -1), GRAY),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("TEXTCOLOR", (1, 0), (1, -1), DARK),
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, GRAY),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, rl_colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ]),
        ),
        Spacer(1, 12 * mm),
        Paragraph(
            "This is a system-generated receipt and does not require a signature.",
            s_small,
        ),
    ]
    doc.build(story)
    return buf.getvalue()
