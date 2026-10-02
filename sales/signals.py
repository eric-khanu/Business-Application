# sales/signals.py
"""
Signals for the sales app.

  - Log every new sale
  - Optionally email managers when a large sale is recorded
  - Log + optionally email when a sale is voided

NOTE: Business logic belongs in `sales.services`. Signals are for
side effects only (logging, notifications, cache invalidation).
"""

import logging
from threading import Thread

from django.conf import settings
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Sale

logger = logging.getLogger(__name__)


# =====================================================================
# Helpers
# =====================================================================
def _async_send_mail(subject, message, from_email, recipients):
    """Send email in a background thread so the request isn't blocked."""
    def _worker():
        try:
            send_mail(
                subject, message, from_email, recipients,
                fail_silently=True,
            )
        except Exception:
            logger.exception("Async email send failed")

    Thread(target=_worker, daemon=True).start()


def _manager_emails(business, *, owners_only=False):
    """Return manager/owner emails for a business."""
    roles = ("owner",) if owners_only else ("owner", "manager")
    return list(
        business.users
        .filter(role__in=roles, is_active_staff=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )


def _sales_emails_enabled() -> bool:
    return getattr(settings, "ENABLE_SALE_EMAILS", False)


# =====================================================================
# 1. Log every new sale
# =====================================================================
@receiver(
    post_save, sender=Sale,
    dispatch_uid="sales.log_new_sale",
)
def log_new_sale(sender, instance, created, **kwargs):
    if not created:
        return
    logger.info(
        "SALE %s | business=%s cashier=%s total=%s method=%s",
        instance.reference,
        instance.business_id,
        instance.cashier_id,
        instance.total,
        instance.payment_method,
    )


# =====================================================================
# 2. Alert on large sales
# =====================================================================
@receiver(
    post_save, sender=Sale,
    dispatch_uid="sales.alert_large_sale",
)
def alert_large_sale(sender, instance, created, **kwargs):
    if not created or not _sales_emails_enabled():
        return

    threshold = getattr(settings, "LARGE_SALE_ALERT_THRESHOLD", 0)
    if not threshold or instance.total < threshold:
        return

    recipients = _manager_emails(instance.business)
    if not recipients:
        return

    cashier_name = "Unknown"
    if instance.cashier:
        cashier_name = (
            instance.cashier.get_full_name() or instance.cashier.username
        )

    _async_send_mail(
        subject=f"[Large Sale] {instance.reference} — {instance.total}",
        message=(
            f"A sale of {instance.total} {instance.business.currency} "
            f"was recorded by {cashier_name}.\n\n"
            f"Reference: {instance.reference}\n"
            f"Payment method: {instance.get_payment_method_display()}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipients=recipients,
    )


# =====================================================================
# 3. Log + notify on void
# =====================================================================
@receiver(
    post_save, sender=Sale,
    dispatch_uid="sales.notify_void",
)
def notify_void(sender, instance, created, **kwargs):
    if created:
        return
    if instance.status != Sale.Status.VOID:
        return
    if not instance.voided_at:
        return  # not fully voided yet

    logger.warning(
        "VOID %s | by=%s reason=%r total=%s business=%s",
        instance.reference,
        instance.voided_by_id,
        instance.void_reason,
        instance.total,
        instance.business_id,
    )

    if not _sales_emails_enabled():
        return

    recipients = _manager_emails(instance.business, owners_only=True)
    if not recipients:
        return

    voided_by_name = "Unknown"
    if instance.voided_by:
        voided_by_name = (
            instance.voided_by.get_full_name() or instance.voided_by.username
        )

    _async_send_mail(
        subject=f"[Void] Sale {instance.reference}",
        message=(
            f"Sale {instance.reference} was voided by {voided_by_name}.\n\n"
            f"Reason: {instance.void_reason or '—'}\n"
            f"Original total: {instance.total}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipients=recipients,
    )