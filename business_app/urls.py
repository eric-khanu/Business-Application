from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from accounts import views as account_views   # ← add

urlpatterns = [
    path('', account_views.root_redirect, name='root'),   # ← add this FIRST

    path('', include('accounts.urls')),

    path('admin/', admin.site.urls),
    path('api/', include('accounts.api_urls')),
    path('inventory/', include('inventory.urls')),
    path('api/inventory/', include('inventory.api_urls')),
    path('sales/', include('sales.urls')),
    path('api/sales/', include('sales.api_urls')),
    path('reports/', include('reports.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)