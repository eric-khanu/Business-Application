# inventory/signals.py
"""
Signals for the inventory app.

Responsibilities:
  - Auto-fill `StockMovement.business` from its product.
  - Log a warning when low stock is reached.
  - Detect direct `Product.quantity` edits that bypass services.py.

NOTE: Actual stock mutation lives in `inventory/services.py`, not here.
"""

import logging

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Product, StockMovement

logger = logging.getLogger(__name__)


# =====================================================================
# StockMovement — auto-fill business from product
# =====================================================================
@receiver(
    pre_save,
    sender=StockMovement,
    dispatch_uid="inventory.stock_movement_fill_business",
)
def stock_movement_fill_business(sender, instance, **kwargs):
    """
    Ensure every StockMovement has a `business` matching its product.
    Prevents accidental cross-tenant audit rows.
    """
    if instance.product_id and instance.business_id != instance.product.business_id:
        instance.business_id = instance.product.business_id


# =====================================================================
# StockMovement — validate quantity sign matches movement_type
# =====================================================================
@receiver(
    pre_save,
    sender=StockMovement,
    dispatch_uid="inventory.stock_movement_validate_sign",
)
def stock_movement_validate_sign(sender, instance, **kwargs):
    """
    Enforce sign convention at the DB layer.

    Since we can't raise clean exceptions from a signal without breaking
    the whole save, we coerce invalid signs into the correct form and
    log the issue.
    """
    mt = instance.movement_type
    q = instance.quantity

    if mt == StockMovement.MovementType.IN and q < 0:
        logger.warning(
            "StockMovement with type=in had negative quantity; flipped.",
            extra={"movement_id": instance.pk, "original_quantity": q},
        )
        instance.quantity = abs(q)
    elif mt == StockMovement.MovementType.OUT and q > 0:
        logger.warning(
            "StockMovement with type=out had positive quantity; flipped.",
            extra={"movement_id": instance.pk, "original_quantity": q},
        )
        instance.quantity = -abs(q)


# =====================================================================
# Product — detect direct quantity edits (bypassing services.py)
# =====================================================================
@receiver(
    pre_save,
    sender=Product,
    dispatch_uid="inventory.product_detect_direct_quantity_edit",
)
def product_detect_direct_quantity_edit(sender, instance, **kwargs):
    """
    Warn (don't block) if `quantity` is changed on a Product without an
    accompanying StockMovement.

    This is a DEV safety net — it fires a warning in logs. It doesn't
    raise, because Django admin edits would then 500. If you want to
    hard-block, uncomment the `raise` below.
    """
    if not instance.pk:
        return

    try:
        old = Product.objects.only("quantity").get(pk=instance.pk)
    except Product.DoesNotExist:
        return

    if old.quantity != instance.quantity:
        logger.warning(
            "Product.quantity changed directly — bypasses services.py.",
            extra={
                "product_id": instance.pk,
                "product_sku": instance.sku,
                "before": old.quantity,
                "after": instance.quantity,
            },
        )
        # To hard-block (uncomment):
        # from django.core.exceptions import ValidationError
        # raise ValidationError(
        #     "Do not edit Product.quantity directly. "
        #     "Use inventory.services.adjust_stock()."
        # )


# =====================================================================
# Product — low-stock alert on save
# =====================================================================
@receiver(
    post_save,
    sender=Product,
    dispatch_uid="inventory.product_low_stock_alert",
)
def product_low_stock_alert(sender, instance, created, **kwargs):
    """
    Log a warning when a product transitions into the low-stock zone.

    Only fires when the product is 'now low' AND was 'not low before'
    (or is new). We don't want a warning on every save while it's low.

    NOTE: Proper notifications (email, Slack) should be wired here later.
    """
    if created:
        is_new_low = instance.is_low_stock
    else:
        # We need the previous state. The pre_save hook above already
        # fetched old, but we don't have it here. Refetch is cheap.
        try:
            old = Product.objects.only("quantity", "reorder_level").get(pk=instance.pk)
        except Product.DoesNotExist:
            return
        was_low = old.quantity <= old.reorder_level
        is_new_low = instance.is_low_stock and not was_low
        if not is_new_low:
            return

    if not is_new_low:
        return

    logger.info(
        "Low stock: %s (%s) — qty=%s, reorder=%s",
        instance.name, instance.sku, instance.quantity, instance.reorder_level,
        extra={
            "business_id": instance.business_id,
            "product_id": instance.pk,
            "product_sku": instance.sku,
            "quantity": instance.quantity,
            "reorder_level": instance.reorder_level,
        },
    )

    # TODO (future): send notification
    # from .tasks import notify_low_stock
    # notify_low_stock.delay(product_id=instance.pk)