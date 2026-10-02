# inventory/api_urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api_views

app_name = "inventory_api"

router = DefaultRouter()
router.register("products", api_views.ProductViewSet, basename="product")
router.register("categories", api_views.CategoryViewSet, basename="category")
router.register("suppliers", api_views.SupplierViewSet, basename="supplier")
router.register(
    "stock-movements",
    api_views.StockMovementViewSet,
    basename="stock-movement",
)

urlpatterns = [
    path("", include(router.urls)),
]