"""
InvoiceIQ — PDF Invoice Renderer
---------------------------------
Builds a clean, professional, review-ready invoice PDF from approved line
items using reportlab. Also exposes an HTML render used for the in-app
on-screen preview so what you approve is what you download.
"""

from datetime import date
from io import BytesIO
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                 Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER

BRAND_COLOR = colors.HexColor("#1F4E78")
LIGHT_GREY = colors.HexColor("#F2F5F7")


def build_invoice_pdf(
    invoice_number: str,
    invoice_date: date,
    due_date: date,
    company: dict,
    customer: dict,
    line_items: List[dict],
    notes: Optional[str],
    currency_symbol: str = "₹",
    discount_percent: float = 0.0,
) -> bytes:
    """
    line_items: [{service_name, unit, quantity, unit_price, tax_rate, line_total}]
    Returns raw PDF bytes.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                             topMargin=18 * mm, bottomMargin=18 * mm,
                             leftMargin=16 * mm, rightMargin=16 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleBrand", parent=styles["Title"],
                                  textColor=BRAND_COLOR, fontSize=22, alignment=TA_LEFT)
    normal = ParagraphStyle("NormalSmall", parent=styles["Normal"], fontSize=9.5, leading=13)
    normal_right = ParagraphStyle("NormalRight", parent=normal, alignment=TA_RIGHT)
    section_header = ParagraphStyle("SectionHeader", parent=styles["Heading3"],
                                     textColor=BRAND_COLOR, fontSize=10.5, spaceAfter=4)

    elements = []

    # ---- Header: company + invoice meta ----
    header_data = [[
        Paragraph(f"<b>{company.get('name', 'Your Company')}</b><br/>"
                  f"{company.get('address', '')}<br/>"
                  f"{company.get('email', '')} | {company.get('phone', '')}<br/>"
                  f"GSTIN: {company.get('gstin', 'N/A')}", normal),
        Paragraph(f"<b><font size=16 color='#1F4E78'>INVOICE</font></b><br/>"
                  f"Invoice #: <b>{invoice_number}</b><br/>"
                  f"Date: {invoice_date.strftime('%d %b %Y')}<br/>"
                  f"Due: {due_date.strftime('%d %b %Y')}", normal_right),
    ]]
    header_table = Table(header_data, colWidths=[100 * mm, 74 * mm])
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(header_table)
    elements.append(Spacer(1, 6 * mm))
    elements.append(HRFlowable(width="100%", thickness=1.2, color=BRAND_COLOR))
    elements.append(Spacer(1, 4 * mm))

    # ---- Bill To ----
    elements.append(Paragraph("BILL TO", section_header))
    bill_to = (f"<b>{customer.get('name') or 'N/A'}</b><br/>"
               f"{customer.get('email') or ''}<br/>"
               f"{customer.get('phone') or ''}")
    elements.append(Paragraph(bill_to, normal))
    elements.append(Spacer(1, 6 * mm))

    # ---- Line items table ----
    table_head = ["#", "Service", "Unit", "Qty", f"Unit Price ({currency_symbol})",
                   "Tax %", f"Line Total ({currency_symbol})"]
    data = [table_head]
    subtotal = 0.0
    total_tax = 0.0
    for i, item in enumerate(line_items, start=1):
        qty = float(item["quantity"])
        price = float(item["unit_price"])
        tax_rate = float(item.get("tax_rate", 0) or 0)
        line_sub = qty * price
        line_tax = line_sub * tax_rate / 100
        subtotal += line_sub
        total_tax += line_tax
        data.append([
            str(i),
            Paragraph(item["service_name"], normal),
            item.get("unit", "-"),
            f"{qty:g}",
            f"{price:,.2f}",
            f"{tax_rate:g}%",
            f"{line_sub:,.2f}",
        ])

    col_widths = [8 * mm, 62 * mm, 20 * mm, 14 * mm, 28 * mm, 16 * mm, 26 * mm]
    items_table = Table(data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_COLOR),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("ALIGN", (4, 1), (4, -1), "RIGHT"),
        ("ALIGN", (6, 1), (6, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E0")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 5 * mm))

    discount_amt = subtotal * discount_percent / 100
    grand_total = subtotal - discount_amt + total_tax

    summary_data = [["Subtotal", f"{currency_symbol}{subtotal:,.2f}"]]
    if discount_percent:
        summary_data.append([f"Discount ({discount_percent:g}%)", f"-{currency_symbol}{discount_amt:,.2f}"])
    summary_data.append(["Tax (GST)", f"{currency_symbol}{total_tax:,.2f}"])
    summary_data.append(["TOTAL DUE", f"{currency_symbol}{grand_total:,.2f}"])

    summary_table = Table(summary_data, colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
    summary_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, BRAND_COLOR),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 12),
        ("TEXTCOLOR", (0, -1), (-1, -1), BRAND_COLOR),
        ("TOPPADDING", (0, -1), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 8 * mm))

    if notes:
        elements.append(Paragraph("NOTES", section_header))
        elements.append(Paragraph(notes, normal))
        elements.append(Spacer(1, 6 * mm))

    elements.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#CBD5E0")))
    elements.append(Spacer(1, 3 * mm))
    footer_style = ParagraphStyle("Footer", parent=normal, alignment=TA_CENTER,
                                   textColor=colors.HexColor("#6B7280"), fontSize=8.5)
    elements.append(Paragraph(
        "This invoice was drafted with AI assistance (InvoiceIQ) and reviewed &amp; approved by a human "
        "before issue. Payment terms: due within 15 days unless stated otherwise.", footer_style))

    doc.build(elements)
    return buf.getvalue()
