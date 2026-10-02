# inventory/services.py
"""
Central place for stock mutations.

NEVER modify `Product.quantity` directly — always use these functions,
so every change is:
  1. Atomic (transaction-wrapped)
  2. Audited (StockMovement row created)
  3. Validated (won't go negative)
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Product, StockMovement


class InsufficientStock(Exception):
    """Raised when a stock decrease would leave quantity < 0."""
    pass


# =====================================================================
# Public API
# =====================================================================
@transaction.atomic
def adjust_stock(
    *,
    product: Product,
    quantity_delta: int,
    movement_type: str,
    reason: str = "",
    reference: str = "",
    actor=None,
) -> StockMovement:
    """
    Adjust a product's stock by `quantity_delta` (positive or negative).

    Returns the StockMovement record.

    Raises InsufficientStock if the resulting quantity would be negative.
    """
    # Lock the row so concurrent sales / restocks don't race
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    before = locked_product.quantity
    after = before + quantity_delta

    if after < 0:
        raise InsufficientStock(
            f"Not enough stock for {locked_product.name}. "
            f"Available: {before}, requested: {abs(quantity_delta)}."
        )

    locked_product.quantity = after
    locked_product.save(update_fields=["quantity", "updated_at"])

    movement = StockMovement.objects.create(
        business=locked_product.business,
        product=locked_product,
        movement_type=movement_type,
        quantity=quantity_delta,
        quantity_before=before,
        quantity_after=after,
        reason=reason,
        reference=reference,
        created_by=actor,
    )
    return movement


@transaction.atomic
def receive_stock(
    *,
    product: Product,
    quantity: int,
    reason: str = "",
    reference: str = "",
    actor=None,
) -> StockMovement:
    """Increase stock (goods received, purchase)."""
    return adjust_stock(
        product=product,
        quantity_delta=abs(quantity),
        movement_type=StockMovement.MovementType.IN,
        reason=reason or "Stock received",
        reference=reference,
        actor=actor,
    )


@transaction.atomic
def remove_stock(
    *,
    product: Product,
    quantity: int,
    reason: str = "",
    reference: str = "",
    actor=None,
) -> StockMovement:
    """Decrease stock (loss, damage, internal use)."""
    return adjust_stock(
        product=product,
        quantity_delta=-abs(quantity),
        movement_type=StockMovement.MovementType.OUT,
        reason=reason or "Stock removed",
        reference=reference,
        actor=actor,
    )


@transaction.atomic
def set_stock(
    *,
    product: Product,
    new_quantity: int,
    reason: str = "",
    reference: str = "",
    actor=None,
) -> StockMovement:
    """Set absolute stock level (stock count). Creates an adjustment."""
    locked_product = Product.objects.select_for_update().get(pk=product.pk)
    delta = new_quantity - locked_product.quantity
    return adjust_stock(
        product=locked_product,
        quantity_delta=delta,
        movement_type=StockMovement.MovementType.ADJUST,
        reason=reason or "Stock count adjustment",
        reference=reference,
        actor=actor,
    )

# inventory/services.py — add this function

from django.db.models import F


def low_stock_products(business):
    """
    Return queryset of active products where quantity <= reorder_level.
    Ordered by how urgently they need restocking (lowest ratio first).
    """
    return (
        Product.objects
        .filter(
            business=business,
            is_active=True,
            quantity__lte=F("reorder_level"),
        )
        .select_related("category", "supplier")
        .order_by("quantity")
    )