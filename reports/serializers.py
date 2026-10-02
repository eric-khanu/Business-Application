# reports/serializers.py
from rest_framework import serializers


class KPISerializer(serializers.Serializer):
    """KPI totals for a period."""
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    orders = serializers.IntegerField()
    tax = serializers.DecimalField(max_digits=14, decimal_places=2)
    discount = serializers.DecimalField(max_digits=14, decimal_places=2)
    avg_sale = serializers.DecimalField(max_digits=14, decimal_places=2)


class TimeseriesRowSerializer(serializers.Serializer):
    """One row in a timeseries chart (day or hour bucket)."""
    bucket = serializers.DateField()          # ← use DateField for TruncDate
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    orders = serializers.IntegerField()


class PaymentMethodBreakdownSerializer(serializers.Serializer):
    """Revenue and order count per payment method."""
    payment_method = serializers.CharField()
    payment_method_display = serializers.CharField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    orders = serializers.IntegerField()


class TopProductSerializer(serializers.Serializer):
    """Top-selling product in a period."""
    product_id = serializers.IntegerField(allow_null=True)
    product_name = serializers.CharField()
    product_sku = serializers.CharField(allow_blank=True)
    qty = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)


class TopSalespersonSerializer(serializers.Serializer):
    """Top-performing staff member in a period."""
    cashier_id = serializers.IntegerField(allow_null=True)
    cashier_name = serializers.CharField()
    orders = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)