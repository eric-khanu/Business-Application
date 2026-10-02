# accounts/urls.py
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Root
    path("", views.root_redirect, name="root"),

    # Auth
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),

    # Staff
    path("staff/", views.staff_list, name="staff_list"),
    path("staff/add/", views.staff_create, name="staff_create"),
    path("staff/<int:pk>/edit/", views.staff_edit, name="staff_edit"),
    path("staff/<int:pk>/toggle/", views.staff_toggle_active, name="staff_toggle"),
    path("staff/<int:pk>/delete/", views.staff_delete, name="staff_delete"),
    path("staff/<int:pk>/reset-password/",
         views.staff_reset_password, name="staff_reset_password"),
    path("staff/<int:pk>/reset-password/done/",
         views.staff_reset_password_done, name="staff_reset_password_done"),

    # Profile
    path("profile/", views.profile, name="profile"),
    path("password/", views.password_change, name="password_change"),

    # Business
    path("business/", views.business_settings, name="business_settings"),
]