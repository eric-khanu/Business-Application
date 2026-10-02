# sales/api_urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api_views

app_name = "sales_api"

router = DefaultRouter()
router.register("sales", api_views.SaleViewSet, basename="sale")

urlpatterns = [
    path("", include(router.urls)),
]