# reports/urls.py
from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sales/", views.sales_report, name="sales_report"),
    path("sales.csv", views.sales_report_csv, name="sales_report_csv"),
    path("salespersons/", views.salesperson_report, name="salesperson_report"),
    path("inventory/", views.inventory_report, name="inventory_report"),
    path("low-stock/", views.low_stock_report, name="low_stock_report"),
    path("api/chart/sales/", views.chart_data_sales, name="chart_data_sales"),
]