# accounts/permissions.py
from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Business, User


# =====================================================================
# Django view decorators
# =====================================================================
def role_required(*allowed_roles):
    """
    Restrict a view to specific roles.

    Behaviour:
      - unauthenticated         -> redirect to login (via login_required)
      - authenticated, no biz   -> logout + message + redirect
      - wrong role              -> 403
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if not user.business_id:
                logout(request)
                messages.error(
                    request,
                    "Your account is not linked to a business. "
                    "Please contact your administrator.",
                )
                return redirect("accounts:login")
            if user.role not in allowed_roles:
                raise PermissionDenied(
                    "You do not have permission for this action."
                )
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def owner_required(view_func):
    return role_required(User.Role.OWNER)(view_func)


def manager_required(view_func):
    return role_required(User.Role.OWNER, User.Role.MANAGER)(view_func)


def salesperson_required(view_func):
    return role_required(
        User.Role.OWNER, User.Role.MANAGER, User.Role.SALESPERSON,
    )(view_func)


# =====================================================================
# DRF permission classes
# =====================================================================
class HasBusiness(BasePermission):
    """Request user must be authenticated AND belong to a business."""
    message = "You must belong to a business."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.business_id)


class IsOwner(BasePermission):
    message = "Owner access required."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_owner)


class IsManager(BasePermission):
    """Owners and managers."""
    message = "Manager access required."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_manager)


class CanSell(BasePermission):
    """Anyone who can record a sale (salesperson + manager + owner)."""
    message = "You do not have permission to perform sales actions."

    def has_permission(self, request, view):
        u = request.user
        return bool(
            u and u.is_authenticated
            and (u.is_manager or u.is_salesperson)
        )


# Backwards-compatible alias (in case other modules import the old name)
IsSalesperson = CanSell


class IsSelfOrManager(BasePermission):
    """
    Object-level: allow if the target user is the requester,
    or if the requester is a manager+.
    """
    message = "You can only modify your own profile."

    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if u.is_manager:
            return True
        target = getattr(obj, "user", obj)
        return target == u


class IsSameBusiness(BasePermission):
    """
    Object-level tenant guard.
    Ensures the requested object belongs to the requester's business.
    """
    message = "You do not have access to this resource."

    def has_object_permission(self, request, view, obj):
        u = request.user
        if not u or not u.is_authenticated or not u.business_id:
            return False
        if u.is_superuser:
            return True

        if isinstance(obj, Business):
            return obj.pk == u.business_id

        return getattr(obj, "business_id", None) == u.business_id


class ReadOnlyOrManager(BasePermission):
    """
    Anyone in the business can read; only managers+ can write.
    Combine with HasBusiness.
    """
    message = "Manager access required for write operations."

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return u.is_manager