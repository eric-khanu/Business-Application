# sales/services.py
"""
Sale business logic.

NEVER modify `Product.quantity` directly — always go through
`inventory.services.adjust_stock`, which is transaction-safe, audited,
and prevents negative stock.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from inventory.models import Product, StockMovement
from inventory.services import (
    adjust_stock,
    InsufficientStock as InventoryInsufficientStock,
)

from .models import Sale, SaleItem

logger = logging.getLogger(__name__)


# =====================================================================
# Exceptions
# =====================================================================
class InsufficientStock(Exception):
    """Raised when a sale would leave stock < 0."""
    pass


class SaleAlreadyVoided(Exception):
    pass


class SaleNotVoidable(Exception):
    pass


# =====================================================================
# Helpers
# =====================================================================
def _q2(amount) -> Decimal:
    """Quantize to 2 decimal places, round half up."""
    return Decimal(amount or 0).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP,
    )


# =====================================================================
# create_sale — the ONLY entry point for creating a sale
# =====================================================================
@transaction.atomic
def create_sale(
    *,
    business,
    cashier,
    items_data,
    payment_method: str = "cash",
    discount: Decimal = Decimal("0"),
    amount_paid: Decimal = None,
    notes: str = "",
) -> Sale:
    """
    Create a completed sale, deduct stock, log movements.

    items_data = [
        {"product_id": 12, "quantity": 2, "unit_price": 5.00},
        ...
    ]

    Raises InsufficientStock or ValueError on bad input.
    """
    if not items_data:
        raise ValueError("Cannot create a sale with no items.")

    discount = Decimal(str(discount or "0"))
    if discount < 0:
        raise ValueError("Discount cannot be negative.")

    # ---- 1. Create the sale shell (reference auto-assigned) ----
    sale = Sale.objects.create(
        business=business,
        cashier=cashier,
        payment_method=payment_method,
        discount=discount,
        notes=notes,
    )

    subtotal = Decimal("0")

    # ---- 2. Line items + stock deductions ----
    for row in items_data:
        try:
            product = (
                Product.objects
                .select_for_update()
                .get(id=row["product_id"], business=business, is_active=True)
            )
        except Product.DoesNotExist:
            raise InsufficientStock(
                f"Product {row['product_id']} not found."
            )

        qty = int(row.get("quantity") or 0)
        if qty <= 0:
            raise InsufficientStock(f"Invalid quantity for {product.name}.")

        raw_price = row.get("unit_price")
        unit_price = (
            Decimal(str(raw_price))
            if raw_price is not None
            else product.selling_price
        )
        if unit_price < 0:
            raise ValueError(f"Invalid price for {product.name}.")

        # Create the line item (save() recomputes line_total)
        SaleItem.objects.create(
            sale=sale,
            product=product,
            product_name=product.name,
            product_sku=product.sku,
            quantity=qty,
            unit_price=unit_price,
        )

        subtotal += unit_price * qty

        # Deduct stock via the inventory service — audited & safe
        try:
            adjust_stock(
                product=product,
                quantity_delta=-qty,
                movement_type=StockMovement.MovementType.OUT,
                reason=f"Sale {sale.reference}",
                reference=sale.reference,
                actor=cashier,
            )
        except InventoryInsufficientStock as e:
            # Roll back the whole transaction
            raise InsufficientStock(str(e))

    # ---- 3. Totals ----
    tax_rate = business.tax_rate or Decimal("0")
    tax = _q2(subtotal * tax_rate / Decimal("100"))
    total = _q2(subtotal + tax - discount)

    if total < 0:
        raise ValueError("Discount cannot exceed the total.")

    sale.subtotal = _q2(subtotal)
    sale.tax_rate = tax_rate
    sale.tax = tax
    sale.total = total

    # ---- 4. Payment ----
    if payment_method == Sale.Payment.CASH:
        if amount_paid is None:
            raise ValueError("Cash payment requires an amount_paid value.")
        paid = Decimal(str(amount_paid))
        if paid < total:
            raise ValueError(
                f"Cash paid ({paid}) is less than total ({total})."
            )
        sale.amount_paid = _q2(paid)
        sale.change_due = _q2(paid - total)
    else:
        sale.amount_paid = total
        sale.change_due = Decimal("0")

    sale.save()

    logger.info(
        "SALE created %s | business=%s cashier=%s total=%s items=%s",
        sale.reference,
        business.id,
        cashier.id if cashier else None,
        sale.total,
        len(items_data),
    )
    return sale


# =====================================================================
# void_sale
# =====================================================================
@transaction.atomic
def void_sale(*, sale: Sale, user, reason: str = "") -> Sale:
    """
    Void a completed sale. Restores stock via adjust_stock (audited).
    Only managers/owners should call this (enforced at the view layer).
    """
    sale = Sale.objects.select_for_update().get(pk=sale.pk)

    if sale.status == Sale.Status.VOID:
        raise SaleAlreadyVoided("This sale has already been voided.")
    if sale.status != Sale.Status.COMPLETED:
        raise SaleNotVoidable("Only completed sales can be voided.")

    # ---- Restore stock for each item via the inventory service ----
    for item in sale.items.select_related("product"):
        if not item.product_id:
            continue
        try:
            adjust_stock(
                product=item.product,
                quantity_delta=item.quantity,   # positive = restore
                movement_type=StockMovement.MovementType.IN,
                reason=f"Voided sale {sale.reference}",
                reference=sale.reference,
                actor=user,
            )
        except InventoryInsufficientStock:
            # Should never happen — restoring can only increase stock
            logger.exception(
                "Unexpected InsufficientStock during void",
                extra={"sale_id": sale.pk, "item_id": item.pk},
            )

    sale.status = Sale.Status.VOID
    sale.voided_at = timezone.now()
    sale.voided_by = user
    sale.void_reason = reason or ""
    sale.save(
        update_fields=[
            "status", "voided_at", "voided_by", "void_reason", "updated_at",
        ]
    )

    logger.info(
        "SALE voided %s | by=%s reason=%s",
        sale.reference,
        user.id if user else None,
        reason[:80] if reason else "",
    )
    return sale