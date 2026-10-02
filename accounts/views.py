# accounts/views.py
"""
Web views for the accounts app.

Structure:
  - Helpers
  - Auth (login, logout, register)
  - Staff CRUD (list, create, edit, toggle, delete)
  - Manager-initiated password reset
  - Profile & password
  - Business settings
  - Root dispatcher
"""

import json

from django.contrib import messages
from django.contrib.auth import (
    get_user_model, login, logout, update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    BusinessForm, CustomPasswordChangeForm, LoginForm, ProfileForm,
    RegisterForm, StaffCreateForm, StaffEditForm,
)
from .permissions import manager_required, owner_required
from .services import clear_password_reset_flag, reset_user_password

User = get_user_model()


# =====================================================================
# Helpers
# =====================================================================
def post_login_redirect(user) -> str:
    """Send each role to the right landing page, with safe fallbacks."""
    if user.is_manager_or_above:
        try:
            return reverse("reports:dashboard")
        except NoReverseMatch:
            pass
    try:
        return reverse("sales:pos")
    except NoReverseMatch:
        pass
    return reverse("accounts:profile")


def _safe_next(request, fallback: str) -> str:
    """Prevent open redirect via ?next=..."""
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


# =====================================================================
# Auth
# =====================================================================
def login_view(request):
    if request.user.is_authenticated:
        return redirect(post_login_redirect(request.user))

    form = LoginForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        if not form.cleaned_data.get("remember_me"):
            request.session.set_expiry(0)
        messages.success(
            request,
            f"Welcome back, {user.get_full_name() or user.username}!",
        )
        return redirect(_safe_next(request, post_login_redirect(user)))

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("accounts:login")


def register_view(request):
    if request.user.is_authenticated:
        return redirect(post_login_redirect(request.user))

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save()
        except IntegrityError:
            messages.error(
                request,
                "Something went wrong creating your account. Please try again.",
            )
        else:
            login(request, user)
            messages.success(
                request,
                f"Welcome, {user.first_name or user.username}! "
                f"Your business is ready.",
            )
            return redirect(post_login_redirect(user))

    return render(request, "accounts/register.html", {"form": form})


# =====================================================================
# Staff management
# =====================================================================
@manager_required
def staff_list(request):
    biz = request.user.business
    qs = (
        User.objects
        .filter(business=biz)
        .order_by("first_name", "last_name", "username")
    )

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(employee_id__icontains=q)
        )

    role = request.GET.get("role", "").strip()
    if role:
        qs = qs.filter(role=role)

    status = request.GET.get("status", "").strip()
    if status == "active":
        qs = qs.filter(is_active_staff=True)
    elif status == "inactive":
        qs = qs.filter(is_active_staff=False)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "staff": page_obj,  # template iterates the page, not the whole queryset
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "q": q,
        "role_filter": role,
        "status_filter": status,
        "role_choices": User.Role.choices,
        "counts": {
            "all": User.objects.filter(business=biz).count(),
            "active": User.objects.filter(
                business=biz, is_active_staff=True,
            ).count(),
            "salespersons": User.objects.filter(
                business=biz, role=User.Role.SALESPERSON,
            ).count(),
        },
    }
    return render(request, "accounts/staff_list.html", context)


@manager_required
def staff_create(request):
    form = StaffCreateForm(
        request.POST or None, request.FILES or None, actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        # Defense-in-depth: form restricts this too.
        if (
            not request.user.is_owner
            and form.cleaned_data.get("role") == User.Role.OWNER
        ):
            raise PermissionDenied("Only an owner can create another owner.")

        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.business = request.user.business
                user.save()
        except IntegrityError:
            messages.error(
                request,
                "That employee ID is already used. Please try again.",
            )
        else:
            messages.success(request, f'Staff member "{user.username}" added.')
            return redirect("accounts:staff_list")

    return render(request, "accounts/staff_form.html", {
        "form": form,
        "title": "Add staff",
        "submit_label": "Create staff",
    })


@manager_required
def staff_edit(request, pk):
    staff = get_object_or_404(User, pk=pk, business=request.user.business)

    if staff.is_owner and not request.user.is_owner:
        raise PermissionDenied("Only an owner can edit another owner.")

    form = StaffEditForm(
        request.POST or None, request.FILES or None,
        instance=staff, actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        if (
            not request.user.is_owner
            and form.cleaned_data.get("role") == User.Role.OWNER
        ):
            raise PermissionDenied("Only an owner can assign the owner role.")

        try:
            with transaction.atomic():
                updated = form.save()
        except IntegrityError:
            messages.error(
                request,
                "That employee ID is already used. Please try again.",
            )
        else:
            messages.success(request, f'"{updated.username}" updated.')
            return redirect("accounts:staff_list")

    return render(request, "accounts/staff_form.html", {
        "form": form,
        "staff": staff,
        "title": f"Edit {staff.get_full_name() or staff.username}",
        "submit_label": "Save changes",
    })


@manager_required
@require_POST
def staff_toggle_active(request, pk):
    staff = get_object_or_404(User, pk=pk, business=request.user.business)

    if staff.pk == request.user.pk:
        messages.error(request, "You can't deactivate yourself.")
        return redirect("accounts:staff_list")

    if staff.is_owner and not request.user.is_owner:
        raise PermissionDenied("Only an owner can toggle another owner.")

    if staff.is_active_staff:
        staff.deactivate()
        state = "deactivated"
    else:
        staff.reactivate()
        state = "activated"

    # HTMX-aware response (no full page reload)
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Trigger"] = json.dumps({
            "app:message": {
                "message": f'"{staff.username}" {state}.',
                "level": "success",
            }
        })
        return response

    messages.success(request, f'"{staff.username}" {state}.')
    return redirect("accounts:staff_list")


@manager_required
def staff_delete(request, pk):
    staff = get_object_or_404(User, pk=pk, business=request.user.business)

    if staff.pk == request.user.pk:
        messages.error(request, "You can't delete yourself.")
        return redirect("accounts:staff_list")

    if staff.is_owner and not request.user.is_owner:
        raise PermissionDenied("Only an owner can delete another owner.")

    # Block deleting the last owner.
    if staff.is_owner:
        other_owners = (
            User.objects
            .filter(business=staff.business, role=User.Role.OWNER)
            .exclude(pk=staff.pk)
            .exists()
        )
        if not other_owners:
            messages.error(request, "You can't delete the last owner.")
            return redirect("accounts:staff_list")

    if request.method == "POST":
        username = staff.username
        staff.delete()
        messages.success(request, f'Staff "{username}" deleted.')
        return redirect("accounts:staff_list")

    return render(request, "accounts/staff_confirm_delete.html", {
        "staff": staff,
    })


# =====================================================================
# Manager-initiated password reset
# =====================================================================
@manager_required
@require_POST
def staff_reset_password(request, pk):
    """Generate a temp password, flag forced change, and redirect to display."""
    staff = get_object_or_404(User, pk=pk, business=request.user.business)

    if staff.pk == request.user.pk:
        messages.error(
            request,
            "Use 'Change password' in your profile instead.",
        )
        return redirect("accounts:staff_list")

    if staff.is_owner and not request.user.is_owner:
        raise PermissionDenied(
            "Only an owner can reset another owner's password."
        )

    temp_password = reset_user_password(staff, actor=request.user)

    messages.success(
        request,
        f"Temporary password generated for {staff.username}. "
        f"Share it securely — it will only be shown once.",
    )

    # Flash the temp password in the session; consumed on next render.
    request.session["_temp_password_for"] = staff.pk
    request.session["_temp_password_value"] = temp_password

    return redirect("accounts:staff_reset_password_done", pk=staff.pk)


@manager_required
def staff_reset_password_done(request, pk):
    """Display the temp password ONCE."""
    staff = get_object_or_404(User, pk=pk, business=request.user.business)

    if request.session.get("_temp_password_for") != staff.pk:
        messages.warning(
            request,
            "That temporary password is no longer available to display. "
            "Reset again if needed.",
        )
        return redirect("accounts:staff_list")

    temp_password = request.session.pop("_temp_password_value", None)
    request.session.pop("_temp_password_for", None)

    return render(request, "accounts/staff_reset_password_done.html", {
        "staff": staff,
        "temp_password": temp_password,
    })


# =====================================================================
# Profile & password
# =====================================================================
@login_required
def profile(request):
    form = ProfileForm(
        request.POST or None, request.FILES or None, instance=request.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")

    return render(request, "accounts/profile.html", {
        "form": form,
        "user_obj": request.user,
    })


@login_required
def password_change(request):
    form = CustomPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)

        if getattr(user, "must_change_password", False):
            clear_password_reset_flag(user)
            messages.success(request, "Password set. Welcome back!")
        else:
            messages.success(request, "Password updated successfully.")

        return redirect("accounts:profile")

    return render(request, "accounts/password_change.html", {"form": form})


# =====================================================================
# Business settings
# =====================================================================
@owner_required
def business_settings(request):
    business = request.user.business
    form = BusinessForm(
        request.POST or None, request.FILES or None, instance=business,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Business settings updated.")
        return redirect("accounts:business_settings")

    return render(request, "accounts/business_settings.html", {
        "form": form,
        "business": business,
    })


# =====================================================================
# Root dispatcher
# =====================================================================
@require_GET
def root_redirect(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    return redirect(post_login_redirect(request.user))