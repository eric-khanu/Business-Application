# accounts/api_urls.py
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from . import api_views

app_name = "accounts_api"

urlpatterns = [
    # Auth
    path("auth/login/", api_views.LoginView.as_view(), name="login"),
    path("auth/logout/", api_views.LogoutView.as_view(), name="logout"),
    path("auth/register/", api_views.RegisterView.as_view(), name="register"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("auth/me/", api_views.MeView.as_view(), name="me"),
    path(
        "auth/change-password/",
        api_views.ChangePasswordView.as_view(),
        name="change_password",
    ),

    # Business
    path("business/", api_views.BusinessDetailView.as_view(), name="business"),

    # Staff
    path("staff/", api_views.StaffListCreateView.as_view(), name="staff_list"),
    path(
        "staff/<int:pk>/",
        api_views.StaffDetailView.as_view(),
        name="staff_detail",
    ),
]