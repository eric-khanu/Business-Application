# sales/receipts.py
"""
Receipt PDF generation for sales.

Uses ReportLab — pure Python, no system dependencies.
Produces A5 portrait PDFs that print cleanly on A4 paper
and most thermal receipt printers.

Public API:
  - build_receipt_pdf_bytes(sale) -> bytes
  - generate_receipt_pdf(sale)    -> FileResponse
"""

import logging
import textwrap
from decimal import Decimal
from io import BytesIO

from django.http import FileResponse
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .models import Sale

logger = logging.getLogger(__name__)


# =====================================================================
# Helpers
# =====================================================================
def _fmt(value, decimals: int = 2) -> str:
    """Safe money formatting — never crashes on None or bad values."""
    if value is None:
        return "0.00"
    try:
        return f"{Decimal(value):.{decimals}f}"
    except Exception:
        return str(value)


def _safe_filename(reference) -> str:
    """Strip characters that break Content-Disposition headers."""
    return "".join(
        c for c in str(reference) if c.isalnum() or c in "-_."
    )


# =====================================================================
# PDF builder — pure bytes, no HTTP concerns
# =====================================================================
def build_receipt_pdf_bytes(sale: Sale) -> bytes:
    """
    Build a receipt PDF for `sale` and return raw bytes.

    Uses snapshot fields (`product_name`, `product_sku`) so historical
    receipts stay intact even if the product is renamed or deleted.
    """
    business = sale.business
    items = list(sale.items.all())

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A5)
    width, height = A5

    # Geometry
    LEFT = 15 * mm
    RIGHT = width - 15 * mm
    QTY_X = 100 * mm
    PRICE_X = 122 * mm
    TOTAL_X = RIGHT

    # ---------------------------------------------------------------
    # Inner drawing helpers
    # ---------------------------------------------------------------
    def draw_void_watermark():
        if sale.status != Sale.Status.VOID:
            return
        p.saveState()
        p.setFillColorRGB(0.85, 0.15, 0.15)
        p.setFont("Helvetica-Bold", 80)
        p.translate(width / 2, height / 2)
        p.rotate(35)
        p.drawCentredString(0, 0, "VOID")
        p.restoreState()

    def draw_page_header():
        """Compact header for pages 2+."""
        nonlocal y
        p.setFont("Helvetica-Bold", 10)
        p.drawString(LEFT, y, business.name[:60])
        p.setFont("Helvetica", 8)
        p.drawRightString(RIGHT, y, sale.reference)
        y -= 5 * mm
        p.line(LEFT, y, RIGHT, y)
        y -= 5 * mm

    def draw_items_header():
        nonlocal y
        p.setFont("Helvetica-Bold", 9)
        p.drawString(LEFT, y, "Item")
        p.drawRightString(QTY_X, y, "Qty")
        p.drawRightString(PRICE_X, y, "Price")
        p.drawRightString(TOTAL_X, y, "Total")
        y -= 3 * mm
        p.line(LEFT, y, RIGHT, y)
        y -= 5 * mm
        p.setFont("Helvetica", 9)

    def draw_logo():
        """Draw the business logo top-right if available."""
        if not business.logo:
            return
        try:
            p.drawImage(
                business.logo.path,
                RIGHT - 28 * mm, height - 32 * mm,
                width=25 * mm, height=25 * mm,
                preserveAspectRatio=True,
                anchor="ne",
                mask="auto",
            )
        except Exception:
            # Logo file may be missing, permission-denied, or an
            # unsupported format — never fail the whole receipt.
            logger.warning(
                "Could not render logo for business %s",
                business.pk, exc_info=True,
            )

    # ===============================================================
    # First-page business block
    # ===============================================================
    y = height - 20 * mm

    draw_logo()

    p.setFont("Helvetica-Bold", 14)
    p.drawString(LEFT, y, business.name[:60])
    y -= 6 * mm

    p.setFont("Helvetica", 8)
    if business.address:
        for line in business.address.splitlines():
            p.drawString(LEFT, y, line[:80])
            y -= 4 * mm
    if business.phone1:
        p.drawString(LEFT, y, f"Tel: {business.phone1}")
        y -= 4 * mm
    if business.email:
        p.drawString(LEFT, y, business.email)
        y -= 4 * mm
    y -= 2 * mm

    # ---------------------------------------------------------------
    # Meta
    # ---------------------------------------------------------------
    p.setFont("Helvetica-Bold", 10)
    p.drawString(LEFT, y, f"Receipt: {sale.reference}")
    p.drawRightString(
        RIGHT, y, sale.created_at.strftime("%Y-%m-%d %H:%M"),
    )
    y -= 5 * mm

    cashier_name = "—"
    if sale.cashier:
        cashier_name = (
            sale.cashier.get_full_name() or sale.cashier.username
        )
    p.setFont("Helvetica", 9)
    p.drawString(LEFT, y, f"Cashier: {cashier_name}")
    p.drawRightString(RIGHT, y, sale.get_payment_method_display())
    y -= 6 * mm

    # ---------------------------------------------------------------
    # Items
    # ---------------------------------------------------------------
    draw_items_header()

    for item in items:
        # Defend against missing snapshot name
        product_label = item.product_name or (
            item.product.name if item.product_id else "Item"
        )

        name_lines = textwrap.wrap(product_label, width=32) or [""]

        for i, line in enumerate(name_lines):
            p.drawString(LEFT, y, line)
            if i == 0:
                p.drawRightString(QTY_X, y, str(item.quantity))
                p.drawRightString(PRICE_X, y, _fmt(item.unit_price))
                p.drawRightString(TOTAL_X, y, _fmt(item.line_total))
            y -= 5 * mm

        # SKU in small grey text
        if item.product_sku:
            p.saveState()
            p.setFont("Helvetica-Oblique", 7)
            p.setFillColorRGB(0.45, 0.45, 0.45)
            p.drawString(LEFT, y, item.product_sku[:40])
            p.restoreState()
            y -= 4 * mm

        # Page break
        if y < 50 * mm:
            draw_void_watermark()
            p.showPage()
            y = height - 20 * mm
            draw_page_header()
            draw_items_header()

    # ---------------------------------------------------------------
    # Totals
    # ---------------------------------------------------------------
    y -= 2 * mm
    p.line(LEFT, y, RIGHT, y)
    y -= 6 * mm

    def row(label, value, bold=False, negative=False):
        nonlocal y
        p.setFont("Helvetica-Bold" if bold else "Helvetica", 9)
        p.drawRightString(PRICE_X, y, label)
        text = _fmt(value)
        if negative:
            text = f"-{text}"
        p.drawRightString(TOTAL_X, y, text)
        y -= 5 * mm

    row("Subtotal", sale.subtotal)
    if sale.tax_rate:
        row(f"Tax ({_fmt(sale.tax_rate)}%)", sale.tax)
    if sale.discount:
        row("Discount", sale.discount, negative=True)
    row("TOTAL", sale.total, bold=True)

    if sale.payment_method == Sale.Payment.CASH and sale.amount_paid:
        row("Paid", sale.amount_paid)
        if sale.change_due:
            row("Change", sale.change_due)

    # ---------------------------------------------------------------
    # Footer
    # ---------------------------------------------------------------
    y -= 4 * mm
    p.setFont("Helvetica-Oblique", 8)

    if sale.status == Sale.Status.VOID:
        p.setFillColorRGB(0.7, 0.1, 0.1)
        void_line = f"** VOIDED ** {sale.void_reason or ''}".strip()
        p.drawString(LEFT, y, void_line)
        p.setFillColorRGB(0, 0, 0)
        y -= 4 * mm

    p.drawString(LEFT, y, "Thank you for your business!")

    # ---------------------------------------------------------------
    # Watermark & save
    # ---------------------------------------------------------------
    draw_void_watermark()
    p.showPage()
    p.save()
    buffer.seek(0)

    pdf_bytes = buffer.read()
    logger.info(
        "Generated receipt PDF | sale=%s bytes=%s items=%s",
        sale.reference, len(pdf_bytes), len(items),
    )
    return pdf_bytes


# =====================================================================
# HTTP wrapper — what views call
# =====================================================================
def generate_receipt_pdf(
    sale: Sale, *, as_attachment: bool = True,
) -> FileResponse:
    """
    Return a FileResponse containing the sale's PDF.
    Set `as_attachment=False` to render inline in the browser.
    """
    pdf_bytes = build_receipt_pdf_bytes(sale)
    filename = f"{_safe_filename(sale.reference)}.pdf"

    return FileResponse(
        BytesIO(pdf_bytes),
        as_attachment=as_attachment,
        filename=filename,
        content_type="application/pdf",
    )