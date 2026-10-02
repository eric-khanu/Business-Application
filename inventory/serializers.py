from django.db import transaction
from rest_framework import serializers

from accounts.models import Business
from .models import Category, Supplier, Product, StockMovement
from .services import adjust_stock, receive_stock, remove_stock, set_stock


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
class BusinessScopedSerializerMixin:
    """Injects `business` from request.user — never trusts the client."""

    def get_business(self):
        request = self.context.get('request')
        if not request or not request.user.business:
            raise serializers.ValidationError("No business associated.")
        return request.user.business


# ---------------------------------------------------------------
# Category
# ---------------------------------------------------------------
class CategorySerializer(BusinessScopedSerializerMixin,
                         serializers.ModelSerializer):
    product_count = serializers.IntegerField(source='products.count',
                                             read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'product_count', 'created_at']
        read_only_fields = ['id', 'created_at', 'product_count']

    def validate_name(self, value):
        b = self.get_business()
        qs = Category.objects.filter(business=b, name__iexact=value.strip())
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'A category with that name already exists.'
            )
        return value.strip()

    def create(self, validated_data):
        return Category.objects.create(business=self.get_business(),
                                       **validated_data)


# ---------------------------------------------------------------
# Supplier
# ---------------------------------------------------------------
class SupplierSerializer(BusinessScopedSerializerMixin,
                         serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'contact_person', 'phone', 'email',
                  'address', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        return Supplier.objects.create(business=self.get_business(),
                                       **validated_data)


# ---------------------------------------------------------------
# Product
# ---------------------------------------------------------------
class ProductListSerializer(serializers.ModelSerializer):
    """Lightweight for list endpoints."""
    category_name = serializers.CharField(source='category.name',
                                          read_only=True, default=None)
    supplier_name = serializers.CharField(source='supplier.name',
                                          read_only=True, default=None)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'sku', 'name', 'category', 'category_name',
                  'supplier', 'supplier_name', 'image',
                  'selling_price', 'quantity', 'reorder_level',
                  'unit', 'is_active', 'is_low_stock']
        read_only_fields = ['id', 'is_low_stock']


class ProductDetailSerializer(ProductListSerializer):
    """Full detail. Hides cost_price + margins for salespersons."""
    margin = serializers.DecimalField(max_digits=12, decimal_places=2,
                                      read_only=True)
    margin_percent = serializers.DecimalField(max_digits=8, decimal_places=2,
                                              read_only=True)
    stock_value = serializers.DecimalField(max_digits=14, decimal_places=2,
                                           read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + [
            'description', 'cost_price', 'margin', 'margin_percent',
            'stock_value', 'created_at', 'updated_at',
        ]
        read_only_fields = ProductListSerializer.Meta.read_only_fields + [
            'created_at', 'updated_at', 'margin', 'margin_percent',
            'stock_value',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        # Salespersons don't see costs/margins
        if request and request.user.is_salesperson:
            for key in ('cost_price', 'margin', 'margin_percent', 'stock_value'):
                data.pop(key, None)
        return data


class ProductWriteSerializer(BusinessScopedSerializerMixin,
                             serializers.ModelSerializer):
    """Create/update — quantity is NOT writable here.
    Use stock adjustment endpoints instead so all changes are audited."""

    class Meta:
        model = Product
        fields = ['id', 'sku', 'name', 'category', 'supplier', 'description',
                  'image', 'cost_price', 'selling_price',
                  'reorder_level', 'unit', 'is_active']
        read_only_fields = ['id']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        b = self.get_business() if self.context.get('request') else None
        if b:
            self.fields['category'].queryset = b.categories.all()
            self.fields['supplier'].queryset = b.suppliers.filter(is_active=True)

    def validate_sku(self, value):
        b = self.get_business()
        qs = Product.objects.filter(business=b, sku__iexact=value.strip())
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('SKU already exists for this business.')
        return value.strip()

    def validate(self, attrs):
        cost = attrs.get('cost_price', getattr(self.instance, 'cost_price', 0))
        sell = attrs.get('selling_price',
                         getattr(self.instance, 'selling_price', 0))
        if sell is not None and cost is not None and sell < cost:
            raise serializers.ValidationError(
                {'selling_price': 'Selling price cannot be below cost price.'}
            )
        return attrs

    def create(self, validated_data):
        return Product.objects.create(business=self.get_business(),
                                      quantity=0, **validated_data)


# ---------------------------------------------------------------
# Stock movements
# ---------------------------------------------------------------
class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    movement_type_display = serializers.CharField(
        source='get_movement_type_display', read_only=True
    )

    class Meta:
        model = StockMovement
        fields = ['id', 'product', 'product_name', 'product_sku',
                  'movement_type', 'movement_type_display',
                  'quantity', 'quantity_before', 'quantity_after',
                  'reason', 'reference',
                  'created_by', 'created_by_name', 'created_at']
        read_only_fields = fields   # read-only, immutable audit log

    def get_created_by_name(self, obj):
        if not obj.created_by:
            return None
        return obj.created_by.get_full_name() or obj.created_by.username


class StockAdjustSerializer(serializers.Serializer):
    """Write serializer for POST /stock/adjust/<product_id>/."""
    MODE_CHOICES = ['add', 'remove', 'set']

    mode = serializers.ChoiceField(choices=MODE_CHOICES, default='add')
    quantity = serializers.IntegerField(min_value=0)
    reason = serializers.CharField(max_length=200, required=False,
                                   allow_blank=True)
    reference = serializers.CharField(max_length=100, required=False,
                                      allow_blank=True)

    def validate(self, attrs):
        if attrs['mode'] == 'remove' and attrs['quantity'] <= 0:
            raise serializers.ValidationError(
                {'quantity': 'Quantity must be greater than zero.'}
            )
        return attrs

# inventory/serializers.py — CategorySerializer

class CategorySerializer(BusinessScopedSerializerMixin,
                         serializers.ModelSerializer):
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "description", "product_count", "created_at"]
        read_only_fields = ["id", "created_at", "product_count"]

    def get_product_count(self, obj):
        # Use annotation if present (from list view), else count
        count = getattr(obj, "product_count", None)
        if count is not None:
            return count
        return obj.products.count()

    def validate_name(self, value):
        b = self.get_business()
        qs = Category.objects.filter(business=b, name__iexact=value.strip())
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A category with that name already exists."
            )
        return value.strip()

    def create(self, validated_data):
        return Category.objects.create(
            business=self.get_business(),
            **validated_data,
        )