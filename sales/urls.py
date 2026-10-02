# sales/urls.py
from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    # POS
    path("pos/", views.pos, name="pos"),

    # Sales lists
    path("my/", views.my_sales, name="my_sales"),
    path("all/", views.all_sales, name="all_sales"),

    # Detail / receipt
    path("<int:pk>/", views.sale_detail, name="sale_detail"),
    path("<int:pk>/receipt/", views.receipt, name="receipt"),
    path("<int:pk>/receipt.pdf", views.receipt_pdf, name="receipt_pdf"),

    # Void
    path("<int:pk>/void/", views.sale_void, name="sale_void"),
]