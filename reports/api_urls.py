from django.urls import path
from . import api_views

app_name = 'reports_api'

urlpatterns = [
    path('dashboard/', api_views.DashboardAPIView.as_view(), name='dashboard'),
    path('sales/timeseries/', api_views.SalesTimeseriesAPIView.as_view(),
         name='sales_timeseries'),
]