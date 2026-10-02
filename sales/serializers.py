# sales/serializers.py
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import serializers

from inventory.models import Product

from .models import Sale, SaleItem
from .services import (
    InsufficientStock,
    create_sale,
    void_sale,
)

User = get_user_model()


# =====================================================================
# Helpers
# =====================================================================
def _q2(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


class BusinessScopedMixin:
    """Injects `business` from request.user — never trusts client input."""

    def get_business(self):
        request = self.context.get("request")
        if (
            not request
            or not request.user.is_authenticated
            or not request.user.business
        ):
            raise serializers.ValidationError("No business associated.")
        return request.user.business


# =====================================================================
# SaleItem — read-only
# =====================================================================
class SaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = [
            "id", "product", "product_name", "product_sku",
            "quantity", "unit_price", "line_total",
        ]
        read_only_fields = fields


# =====================================================================
# Sale — read
# =====================================================================
class SaleListSerializer(serializers.ModelSerializer):
    """Lightweight, no items. Used for list endpoints."""

    cashier_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(
        source="get_status_display", read_only=True,
    )
    payment_method_display = serializers.CharField(
        source="get_payment_method_display", read_only=True,
    )

    class Meta:
        model = Sale
        fields = [
            "id", "reference",
            "cashier", "cashier_name",
            "subtotal", "tax_rate", "tax", "discount", "total",
            "payment_method", "payment_method_display",
            "amount_paid", "change_due",
            "status", "status_display",
            "created_at",
        ]
        read_only_fields = fields

    def get_cashier_name(self, obj):
        if not obj.cashier:
            return None
        return obj.cashier.get_full_name() or obj.cashier.username


class SaleDetailSerializer(SaleListSerializer):
    """Full detail with nested items + void metadata."""

    items = SaleItemSerializer(many=True, read_only=True)
    voided_by_name = serializers.SerializerMethodField()

    class Meta(SaleListSerializer.Meta):
        fields = SaleListSerializer.Meta.fields + [
            "items", "notes",
            "voided_at", "voided_by", "voided_by_name", "void_reason",
        ]
        read_only_fields = fields

    def get_voided_by_name(self, obj):
        if not obj.voided_by:
            return None
        return obj.voided_by.get_full_name() or obj.voided_by.username


# =====================================================================
# Sale — create
# =====================================================================
class SaleItemInputSerializer(serializers.Serializer):
    """One row of the cart payload."""

    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False,
        min_value=Decimal("0"),
        help_text="Optional. Falls back to the product selling price.",
    )


class SaleCreateSerializer(BusinessScopedMixin, serializers.Serializer):
    """
    Validate a POS cart, then delegate to `services.create_sale`.
    The service remains the single source of truth for totals/stock.
    """

    items = SaleItemInputSerializer(many=True, allow_empty=False)
    payment_method = serializers.ChoiceField(
        choices=Sale.Payment.choices,
        default=Sale.Payment.CASH,
    )
    discount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False,
        min_value=Decimal("0"), default=Decimal("0"),
    )
    amount_paid = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False,
        min_value=Decimal("0"),
    )
    notes = serializers.CharField(
        max_length=500, required=False, allow_blank=True,
    )

    def validate_items(self, rows):
        """Pre-validate against current stock. The service re-checks under lock."""
        business = self.get_business()
        seen = set()
        products_by_id = {}

        # Bulk-fetch products once — no N+1
        product_ids = [row["product_id"] for row in rows]
        products_by_id = {
            p.id: p
            for p in Product.objects.filter(
                id__in=product_ids, business=business, is_active=True,
            )
        }

        for row in rows:
            pid = row["product_id"]
            qty = row["quantity"]

            if pid in seen:
                raise serializers.ValidationError(
                    f"Product {pid} appears more than once. Combine quantities."
                )
            seen.add(pid)

            product = products_by_id.get(pid)
            if not product:
                raise serializers.ValidationError(
                    f"Product {pid} not found in this business."
                )
            if qty > product.quantity:
                raise serializers.ValidationError(
                    f"Only {product.quantity} of {product.name} in stock."
                )

        return rows

    def validate(self, attrs):
        business = self.get_business()
        rows = attrs["items"]

        # Fetch all products in one query
        product_ids = [row["product_id"] for row in rows]
        products_by_id = {
            p.id: p
            for p in Product.objects.filter(
                id__in=product_ids, business=business,
            )
        }

        subtotal = Decimal("0")
        for row in rows:
            product = products_by_id.get(row["product_id"])
            if not product:
                continue
            price = row.get("unit_price") or product.selling_price
            subtotal += Decimal(price) * row["quantity"]

        tax_rate = business.tax_rate or Decimal("0")
        tax = _q2(subtotal * tax_rate / Decimal("100"))
        discount = attrs.get("discount") or Decimal("0")
        total = _q2(subtotal + tax - discount)

        if discount > subtotal + tax:
            raise serializers.ValidationError(
                {"discount": "Discount cannot exceed the total."}
            )

        if attrs["payment_method"] == Sale.Payment.CASH:
            paid = attrs.get("amount_paid")
            if paid is None:
                raise serializers.ValidationError(
                    {"amount_paid": "Required for cash payments."}
                )
            if paid < total:
                raise serializers.ValidationError(
                    {"amount_paid":
                     f"Cash tendered ({paid}) is less than total ({total})."}
                )

        # Stash computed total for create()
        attrs["_computed_total"] = total
        attrs["_products_by_id"] = products_by_id
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        business = self.get_business()
        products_by_id = validated_data["_products_by_id"]

        items_data = []
        for row in validated_data["items"]:
            product = products_by_id[row["product_id"]]
            items_data.append({
                "product_id": row["product_id"],
                "quantity": row["quantity"],
                "unit_price": str(
                    row.get("unit_price") or product.selling_price
                ),
            })

        try:
            sale = create_sale(
                business=business,
                cashier=request.user,
                items_data=items_data,
                payment_method=validated_data["payment_method"],
                discount=validated_data.get("discount") or Decimal("0"),
                amount_paid=validated_data.get("amount_paid"),
                notes=validated_data.get("notes", ""),
            )
        except InsufficientStock as e:
            raise serializers.ValidationError({"items": str(e)})
        except ValueError as e:
            raise serializers.ValidationError({"detail": str(e)})

        return sale

    def to_representation(self, instance):
        return SaleDetailSerializer(instance, context=self.context).data


# =====================================================================
# Sale — void
# =====================================================================
class SaleVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)

    def save(self, **kwargs):
        sale = self.context["sale"]
        user = self.context["request"].user
        return void_sale(
            sale=sale,
            user=user,
            reason=self.validated_data["reason"],
        )


# =====================================================================
# Staff summary (for filter dropdowns)
# =====================================================================
class StaffSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "role"]
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username