# reports/views.py
import csv
import json
from datetime import timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render

from accounts.permissions import manager_required
from inventory.models import Product
from sales.models import Sale

from . import services
from .forms import ReportFilterForm


# =====================================================================
# Helpers
# =====================================================================
def _business(request):
    b = request.user.business
    if not b:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Your account is not linked to a business.")
    return b


def _resolve_dates(form):
    """Return (start, end) — respects partial filters."""
    start = end = None
    if form.is_valid():
        start = form.cleaned_data.get("date_from")
        end = form.cleaned_data.get("date_to")

    default_start, default_end = services.date_range("month")

    if not start and not end:
        return default_start, default_end
    return start or default_start, end or default_end


def _form_filters(form):
    """Return (cashier, payment_method) from a validated form."""
    if form.is_valid():
        return (
            form.cleaned_data.get("cashier"),
            form.cleaned_data.get("payment_method"),
        )
    return None, None


# =====================================================================
# Chart payloads
# =====================================================================
def _sales_chart_payload(rows):
    labels = [
        r["bucket"].strftime("%b %d")
        if hasattr(r["bucket"], "strftime")
        else str(r["bucket"])
        for r in rows
    ]
    revenue = [float(r.get("revenue") or 0) for r in rows]
    orders = [int(r.get("orders") or 0) for r in rows]

    return {
        "labels": labels,
        "datasets": [
            {
                "label": "Revenue",
                "data": revenue,
                "borderColor": "#2563eb",
                "backgroundColor": "rgba(37, 99, 235, 0.1)",
                "tension": 0.35,
                "fill": True,
                "yAxisID": "y",
            },
            {
                "label": "Orders",
                "data": orders,
                "borderColor": "#10b981",
                "backgroundColor": "rgba(16, 185, 129, 0.1)",
                "tension": 0.35,
                "fill": False,
                "yAxisID": "y1",
            },
        ],
    }


def _payment_chart_payload(rows):
    label_map = dict(Sale.Payment.choices)
    labels = [label_map.get(r["payment_method"], r["payment_method"]) for r in rows]
    data = [float(r.get("revenue") or 0) for r in rows]

    colors = {
        "cash": "#10b981",     # emerald-500
        "card": "#2563eb",     # brand-600
        "mobile": "#f59e0b",   # amber-500
        "credit": "#8b5cf6",   # violet-500
    }
    bg = [colors.get(r["payment_method"], "#94a3b8") for r in rows]

    return {
        "labels": labels,
        "datasets": [{
            "data": data,
            "backgroundColor": bg,
            "borderWidth": 0,
        }],
    }


# =====================================================================
# Dashboard
# =====================================================================
@manager_required
def dashboard(request):
    b = _business(request)
    t = services.today()

    today_kpi = services.kpi_with_change(b, "today")
    month_start, month_end = services.date_range("month")
    month_kpi = services.kpis_for_range(b, month_start, month_end)

    chart_start = t - timedelta(days=13)
    sales_rows = services.sales_timeseries(b, "daily", chart_start, t)
    payment_rows = services.payment_method_breakdown(b, t, t)

    top_products = services.top_products(b, t - timedelta(days=30), t)
    top_salespersons = services.top_salespersons(b, t - timedelta(days=30), t)

    recent_sales = (
        Sale.objects
        .filter(business=b)
        .select_related("cashier")
        .order_by("-created_at")[:10]
    )

    context = {
        "today_kpi": today_kpi,
        "month_kpi": month_kpi,
        "low_stock_count": services.low_stock_count(b),
        "inventory_summary": services.inventory_summary(b),
        "sales_chart_json": json.dumps(_sales_chart_payload(sales_rows)),
        "payment_chart_json": json.dumps(_payment_chart_payload(payment_rows)),
        "top_products": top_products,
        "top_salespersons": top_salespersons,
        "recent_sales": recent_sales,
    }
    return render(request, "reports/dashboard.html", context)


# =====================================================================
# Chart data (JSON for AJAX)
# =====================================================================
@manager_required
def chart_data_sales(request):
    b = _business(request)
    period = request.GET.get("period", "daily")
    try:
        days = int(request.GET.get("days", 14))
    except (TypeError, ValueError):
        days = 14

    start = services.today() - timedelta(days=days - 1)
    end = services.today()
    rows = services.sales_timeseries(b, period=period, start=start, end=end)
    return JsonResponse(_sales_chart_payload(rows))


# =====================================================================
# Sales report
# =====================================================================
@manager_required
def sales_report(request):
    b = _business(request)
    form = ReportFilterForm(request.GET or None, business=b)
    period = request.GET.get("period", "daily")
    start, end = _resolve_dates(form)
    cashier, payment_method = _form_filters(form)

    rows = services.sales_timeseries(
        b, period=period, start=start, end=end,
        cashier=cashier, payment_method=payment_method,
    )
    totals = services.kpis_for_range(b, start, end)

    return render(request, "reports/sales_report.html", {
        "form": form,
        "period": period,
        "rows": rows,
        "totals": totals,
        "start": start,
        "end": end,
    })


# =====================================================================
# Salesperson report
# =====================================================================
@manager_required
def salesperson_report(request):
    b = _business(request)
    form = ReportFilterForm(request.GET or None, business=b)
    start, end = _resolve_dates(form)
    period = request.GET.get("period", "monthly")
    cashier, _ = _form_filters(form)

    return render(request, "reports/salesperson_report.html", {
        "form": form,
        "leaderboard": services.top_salespersons(b, start, end, limit=50),
        "per_period": services.sales_timeseries(
            b, period=period, start=start, end=end, cashier=cashier,
        ),
        "start": start,
        "end": end,
    })


# =====================================================================
# Inventory reports
# =====================================================================
@manager_required
def inventory_report(request):
    b = _business(request)
    products = (
        Product.objects
        .filter(business=b)
        .select_related("category", "supplier")
        .order_by("quantity")
    )
    page = Paginator(products, 50).get_page(request.GET.get("page"))

    return render(request, "reports/inventory_report.html", {
        "summary": services.inventory_summary(b),
        "page": page,
        "is_paginated": page.has_other_pages(),
    })


@manager_required
def low_stock_report(request):
    b = _business(request)
    products = (
        Product.objects
        .filter(business=b, is_active=True, quantity__lte=F("reorder_level"))
        .select_related("category", "supplier")
        .order_by("quantity")
    )
    page = Paginator(products, 50).get_page(request.GET.get("page"))

    return render(request, "reports/low_stock_report.html", {
        "page": page,
        "is_paginated": page.has_other_pages(),
    })


# =====================================================================
# CSV export
# =====================================================================
@manager_required
def sales_report_csv(request):
    b = _business(request)
    form = ReportFilterForm(request.GET or None, business=b)
    start, end = _resolve_dates(form)
    cashier, payment_method = _form_filters(form)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="sales_{start}_to_{end}.csv"'
    )

    w = csv.writer(response)
    w.writerow(["Date", "Orders", "Revenue", "Tax", "Discount"])

    rows = services.sales_timeseries(
        b, "daily", start, end,
        cashier=cashier, payment_method=payment_method,
    )
    for row in rows:
        w.writerow([
            row["bucket"].strftime("%Y-%m-%d"),
            row["orders"],
            f"{row['revenue']:.2f}",
            f"{row['tax']:.2f}",
            f"{row['discount']:.2f}",
        ])
    return response