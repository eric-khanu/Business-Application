# accounts/signals.py
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)
User = get_user_model()

EMP_PREFIX = "EMP-"


@receiver(
    pre_save, sender=User,
    dispatch_uid="accounts.autogenerate_employee_id",
)
def autogenerate_employee_id(sender, instance, **kwargs):
    """Assign the next employee ID within the user's business."""
    if instance.employee_id or not instance.business_id:
        return

    from .models import Business
    with transaction.atomic():
        biz = Business.objects.select_for_update().get(pk=instance.business_id)
        instance.employee_id = f"{EMP_PREFIX}{biz.next_employee_number:04d}"
        biz.next_employee_number += 1
        biz.save(update_fields=["next_employee_number"])


@receiver(
    post_delete, sender=User,
    dispatch_uid="accounts.warn_last_owner_deleted",
)
def warn_last_owner_deleted(sender, instance, **kwargs):
    """Log a warning if the last owner of a business is deleted."""
    if instance.role != User.Role.OWNER or not instance.business_id:
        return

    still_has_owner = User.objects.filter(
        business_id=instance.business_id,
        role=User.Role.OWNER,
    ).exists()

    if not still_has_owner:
        logger.warning(
            "Business has no owner after user deletion",
            extra={
                "business_id": instance.business_id,
                "deleted_user_id": instance.pk,
                "deleted_username": instance.username,
            },
        )