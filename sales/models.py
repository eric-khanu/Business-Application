# sales/models.py
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum


# =====================================================================
# Reference generator
# =====================================================================
def generate_reference() -> str:
    """Globally unique, short, human-readable reference like INV-A3F9C20D41."""
    return f"INV-{uuid.uuid4().hex[:10].upper()}"


# =====================================================================
# Sale
# =====================================================================
class Sale(models.Model):
    class Payment(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        MOBILE = "mobile", "Mobile Money"
        CREDIT = "credit", "Credit"

    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        PENDING = "pending", "Pending"
        REFUNDED = "refunded", "Refunded"
        VOID = "void", "Void"

    # Backwards-compatible aliases (so old code keeps working)
    PAYMENT_CASH = Payment.CASH
    PAYMENT_CARD = Payment.CARD
    PAYMENT_MOBILE = Payment.MOBILE
    PAYMENT_CREDIT = Payment.CREDIT
    PAYMENT_METHODS = Payment.choices

    STATUS_COMPLETED = Status.COMPLETED
    STATUS_PENDING = Status.PENDING
    STATUS_REFUNDED = Status.REFUNDED
    STATUS_VOID = Status.VOID
    STATUS_CHOICES = Status.choices

    business = models.ForeignKey(
        "accounts.Business",
        on_delete=models.CASCADE,
        related_name="sales",
    )
    reference = models.CharField(max_length=20, unique=True, editable=False)
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sales_made",
    )

    # ---- Money ----
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # ---- Payment ----
    payment_method = models.CharField(
        max_length=10,
        choices=Payment.choices,
        default=Payment.CASH,
    )
    amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Amount tendered by the customer.",
    )
    change_due = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Change returned to the customer.",
    )

    # ---- Status ----
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.COMPLETED,
    )
    notes = models.TextField(blank=True)

    # ---- Void audit ----
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="sales_voided",
    )
    void_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business", "-created_at"]),
            models.Index(fields=["cashier", "-created_at"]),
            models.Index(fields=["business", "status"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["business", "payment_method"]),
        ]

    def __str__(self):
        return self.reference

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_reference()
        super().save(*args, **kwargs)

    # ---- Convenience ----
    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    @property
    def is_voided(self):
        return self.status == self.Status.VOID

    @property
    def item_count(self):
        """Number of units sold — uses prefetch cache if available."""
        return sum(item.quantity for item in self.items.all())

    def recalculate_totals(self, *, save=True):
        """Recompute subtotal/tax/total from line items."""
        subtotal = (
            self.items.aggregate(s=Sum("line_total"))["s"]
            or Decimal("0")
        )
        tax = (
            subtotal * (self.tax_rate or Decimal("0")) / Decimal("100")
        ).quantize(Decimal("0.01"))
        total = (
            subtotal + tax - (self.discount or Decimal("0"))
        ).quantize(Decimal("0.01"))

        self.subtotal = subtotal
        self.tax = tax
        self.total = total
        if save:
            self.save(update_fields=["subtotal", "tax", "total", "updated_at"])
        return self


# =====================================================================
# SaleItem
# =====================================================================
class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        related_name="sale_items",
    )

    # Snapshots — historical receipts stay intact even if product is renamed
    product_name = models.CharField(max_length=200)
    product_sku = models.CharField(max_length=50)

    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["sale"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.quantity} × {self.product_name}"

    def save(self, *args, **kwargs):
        # Snapshot product fields if not already set
        if self.product_id and (not self.product_name or not self.product_sku):
            self.product_name = self.product.name
            self.product_sku = self.product.sku

        # Always recompute line_total — never trust the caller
        self.line_total = (self.quantity or 0) * (self.unit_price or 0)

        super().save(*args, **kwargs)