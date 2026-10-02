# inventory/models.py
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


# =====================================================================
# Category
# =====================================================================
class Category(models.Model):
    business = models.ForeignKey(
        "accounts.Business", on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "name"],
                name="unique_category_name_per_business",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "name"]),
        ]

    def __str__(self):
        return self.name


# =====================================================================
# Supplier
# =====================================================================
class Supplier(models.Model):
    business = models.ForeignKey(
        "accounts.Business", on_delete=models.CASCADE,
        related_name="suppliers",
    )
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["business", "name"]),
            models.Index(fields=["business", "is_active"]),
        ]

    def __str__(self):
        return self.name


# =====================================================================
# Product
# =====================================================================
class Product(models.Model):
    business = models.ForeignKey(
        "accounts.Business", on_delete=models.CASCADE,
        related_name="products",
    )
    sku = models.CharField(max_length=50)
    barcode = models.CharField(max_length=50, blank=True, null=True)
    name = models.CharField(max_length=200)

    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="products",
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="products",
    )

    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="products/", blank=True, null=True)

    cost_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    selling_price = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    quantity = models.PositiveIntegerField(default=0)
    reorder_level = models.PositiveIntegerField(default=5)
    unit = models.CharField(max_length=20, default="pcs")

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "sku"],
                name="unique_sku_per_business",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "name"]),
            models.Index(fields=["business", "sku"]),
            models.Index(fields=["business", "barcode"]),
            models.Index(fields=["business", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.sku})"

    # ---- Computed properties ----
    @property
    def is_low_stock(self):
        return self.quantity <= self.reorder_level

    @property
    def margin(self):
        if not self.selling_price:
            return 0
        return self.selling_price - self.cost_price

    @property
    def margin_percent(self):
        if not self.cost_price:
            return 0
        return round((self.margin / self.cost_price) * 100, 2)

    @property
    def stock_value(self):
        return self.cost_price * self.quantity


# =====================================================================
# StockMovement
# =====================================================================
class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        IN = "in", "Stock In"
        OUT = "out", "Stock Out"
        ADJUST = "adjust", "Adjustment"

    business = models.ForeignKey(
        "accounts.Business", on_delete=models.CASCADE,
        related_name="stock_movements",
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT,
        related_name="movements",
    )

    movement_type = models.CharField(
        max_length=10,
        choices=MovementType.choices,
    )
    quantity = models.IntegerField(
        help_text="Positive = increase, negative = decrease",
    )
    quantity_before = models.IntegerField(default=0)
    quantity_after = models.IntegerField(default=0)

    reason = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stock_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business", "-created_at"]),
            models.Index(fields=["product", "-created_at"]),
            models.Index(fields=["business", "movement_type"]),
        ]

    def __str__(self):
        return (
            f"{self.get_movement_type_display()} "
            f"{self.quantity} × {self.product.name}"
        )

    def clean(self):
        """Validate quantity sign matches movement_type."""
        if self.movement_type == self.MovementType.IN and self.quantity <= 0:
            raise ValidationError(
                {"quantity": "Stock In must be a positive quantity."}
            )
        if self.movement_type == self.MovementType.OUT and self.quantity >= 0:
            raise ValidationError(
                {"quantity": "Stock Out must be a negative quantity."}
            )