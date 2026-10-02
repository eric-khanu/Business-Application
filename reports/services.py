# reports/services.py
"""
Read-only aggregation helpers for the reports app.

Pure functions: no models, no side effects.
All money values are Decimal; all return dicts have defined keys.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import (
    Abs, TruncDay, TruncHour, TruncMonth, TruncWeek,
)
from django.utils import timezone

from inventory.models import Product, StockMovement
from sales.models import Sale, SaleItem


# =====================================================================
# Date helpers
# =====================================================================
def today() -> date:
    return timezone.localdate()


def date_range(period: str, anchor: date = None):
    """Return (start_date, end_date) — inclusive — for a named period."""
    anchor = anchor or today()

    if period == "today":
        return anchor, anchor
    if period == "yesterday":
        y = anchor - timedelta(days=1)
        return y, y
    if period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = anchor.replace(day=1)
        # Last day of this month
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    if period == "year":
        return anchor.replace(month=1, day=1), anchor.replace(month=12, day=31)
    return anchor, anchor


def previous_period(start: date, end: date):
    """Return the immediately preceding period of the same length."""
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end


# =====================================================================
# Sales querysets
# =====================================================================
def _sales_qs(business, start, end, include_void: bool = False):
    qs = Sale.objects.filter(
        business=business,
        created_at__date__gte=start,
        created_at__date__lte=end,
    )
    if not include_void:
        qs = qs.filter(status=Sale.Status.COMPLETED)
    return qs


def _agg(qs):
    """Aggregate a Sale queryset into a KPI dict."""
    data = qs.aggregate(
        revenue=Sum("total"),
        tax=Sum("tax"),
        discount=Sum("discount"),
        orders=Count("id"),
    )
    revenue = data["revenue"] or Decimal("0")
    orders = data["orders"] or 0
    avg = (
        (revenue / orders).quantize(Decimal("0.01"))
        if orders else Decimal("0.00")
    )
    return {
        "revenue": revenue,
        "tax": data["tax"] or Decimal("0"),
        "discount": data["discount"] or Decimal("0"),
        "orders": orders,
        "avg_sale": avg,
    }


# =====================================================================
# KPIs
# =====================================================================
def kpis_for_range(business, start, end):
    return _agg(_sales_qs(business, start, end))


def kpi_with_change(business, period: str = "today"):
    """KPIs for `period` + % change vs. the same-length previous period."""
    start, end = date_range(period)
    prev_start, prev_end = previous_period(start, end)

    current = kpis_for_range(business, start, end)
    previous = kpis_for_range(business, prev_start, prev_end)

    def pct(new, old):
        if not old:
            return None
        return round(float((Decimal(new) - Decimal(old)) / Decimal(old)) * 100, 1)

    current["change"] = {
        "revenue": pct(current["revenue"], previous["revenue"]),
        "orders": pct(current["orders"], previous["orders"]),
        "avg_sale": pct(current["avg_sale"], previous["avg_sale"]),
    }
    current["previous"] = previous
    return current


# =====================================================================
# Timeseries
# =====================================================================
TRUNC_MAP = {
    "hourly": TruncHour,
    "daily": TruncDay,
    "weekly": TruncWeek,
    "monthly": TruncMonth,
}


def sales_timeseries(
    business, period: str = "daily",
    start=None, end=None,
    cashier=None, payment_method=None,
):
    trunc = TRUNC_MAP.get(period, TruncDay)
    qs = Sale.objects.filter(business=business, status=Sale.Status.COMPLETED)

    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)
    if cashier:
        qs = qs.filter(cashier_id=cashier)
    if payment_method:
        qs = qs.filter(payment_method=payment_method)

    return (
        qs.annotate(bucket=trunc("created_at"))
          .values("bucket")
          .annotate(
              revenue=Sum("total"),
              orders=Count("id"),
              tax=Sum("tax"),
              discount=Sum("discount"),
          )
          .order_by("bucket")
    )


# =====================================================================
# Leaderboards
# =====================================================================
def top_products(business, start, end, limit: int = 10):
    from django.db.models import Max

    return (
        SaleItem.objects
        .filter(
            sale__business=business,
            sale__status=Sale.Status.COMPLETED,
            sale__created_at__date__gte=start,
            sale__created_at__date__lte=end,
        )
        .values("product_id")
        .annotate(
            product_name=Max("product_name"),
            product_sku=Max("product_sku"),
            qty=Sum("quantity"),
            revenue=Sum("line_total"),
        )
        .order_by("-revenue")[:limit]
    )


def top_salespersons(business, start, end, limit: int = 10):
    return (
        Sale.objects
        .filter(
            business=business,
            status=Sale.Status.COMPLETED,
            created_at__date__gte=start,
            created_at__date__lte=end,
            cashier__isnull=False,
        )
        .values(
            "cashier_id",
            "cashier__first_name",
            "cashier__last_name",
            "cashier__username",
        )
        .annotate(
            revenue=Sum("total"),
            orders=Count("id"),
            avg_sale=Avg("total"),
        )
        .order_by("-revenue")[:limit]
    )


def payment_method_breakdown(business, start, end):
    return (
        Sale.objects
        .filter(
            business=business,
            status=Sale.Status.COMPLETED,
            created_at__date__gte=start,
            created_at__date__lte=end,
        )
        .values("payment_method")
        .annotate(revenue=Sum("total"), orders=Count("id"))
        .order_by("-revenue")
    )


# =====================================================================
# Inventory
# =====================================================================
def inventory_summary(business):
    agg = (
        Product.objects
        .filter(business=business, is_active=True)
        .aggregate(
            products=Count("id"),
            total_units=Sum("quantity"),
            stock_value=Sum(F("quantity") * F("cost_price")),
            retail_value=Sum(F("quantity") * F("selling_price")),
        )
    )
    return {
        "products": agg["products"] or 0,
        "total_units": agg["total_units"] or 0,
        "stock_value": agg["stock_value"] or Decimal("0"),
        "retail_value": agg["retail_value"] or Decimal("0"),
    }


def low_stock_count(business):
    return (
        Product.objects
        .filter(
            business=business,
            is_active=True,
            quantity__lte=F("reorder_level"),
        )
        .count()
    )


def top_movers(business, start, end, limit: int = 10):
    """Products with the most gross unit movement (in + out) in period."""
    return (
        StockMovement.objects
        .filter(
            business=business,
            created_at__date__gte=start,
            created_at__date__lte=end,
        )
        .values("product_id", "product__name", "product__sku")
        .annotate(units=Sum(Abs("quantity")))   # gross volume
        .order_by("-units")[:limit]
    )


def dead_stock(business, days: int = 90):
    """Active products with zero completed sales in the last `days` days."""
    cutoff = today() - timedelta(days=days)
    sold_ids = (
        SaleItem.objects
        .filter(
            sale__business=business,
            sale__status=Sale.Status.COMPLETED,   # ← exclude voided
            sale__created_at__date__gte=cutoff,
        )
        .values_list("product_id", flat=True)
        .distinct()
    )
    return (
        Product.objects
        .filter(business=business, is_active=True, quantity__gt=0)
        .exclude(id__in=sold_ids)
        .order_by("-quantity")
    )