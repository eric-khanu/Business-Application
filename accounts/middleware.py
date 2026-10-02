# accounts/middleware.py
"""
Request-level guards for the accounts app.

1. RequireBusinessMiddleware
   - Anonymous requests: no-op
   - Authenticated + no business + non-exempt path:
       * logout the user
       * HTML request   -> redirect to login (302)
       * JSON/AJAX      -> 403 JSON
   - Attaches `request.business` when available.

Exempt prefixes (never touched):
   /accounts/login/, /accounts/logout/, /admin/, /static/, /media/,
   /api/ (DRF handles its own auth), /favicon.ico
"""

import logging

from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse

logger = logging.getLogger(__name__)


class RequireBusinessMiddleware:
    """
    Ensure every authenticated non-exempt request belongs to a business.
    Orphan users are logged out and sent to login.
    """

    # Paths that bypass the check entirely.
    # NOTE: order doesn't matter — we use startswith().
    EXEMPT_PREFIXES = (
        "/accounts/login/",
        "/accounts/logout/",
        "/admin/",
        "/static/",
        "/media/",
        "/api/",          # DRF enforces auth via permissions
        "/favicon.ico",
        "/robots.txt",
    )

    # If True, superusers without a business are still blocked from
    # non-admin pages. Set False to let superusers roam freely.
    BLOCK_SUPERUSERS_WITHOUT_BUSINESS = True

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        # --- Anonymous: nothing to guard, just attach None for templates ---
        if not user or not user.is_authenticated:
            request.business = None
            return self.get_response(request)

        # --- Attach request.business for convenience (cached FK) ---
        business = user.business if user.business_id else None
        request.business = business

        # --- Exempt paths pass through ---
        if self._is_exempt(request.path):
            return self.get_response(request)

        # --- Optional: let superusers bypass ---
        if user.is_superuser and not self.BLOCK_SUPERUSERS_WITHOUT_BUSINESS:
            return self.get_response(request)

        # --- Orphan user: log out and redirect ---
        if not user.business_id:
            logger.warning(
                "Orphan user hit protected path",
                extra={"user_id": user.pk, "path": request.path},
            )
            logout(request)

            # API/AJAX clients get JSON, browsers get a redirect.
            if self._wants_json(request):
                return JsonResponse(
                    {
                        "detail": (
                            "Your account is not linked to a business. "
                            "Please contact your administrator."
                        )
                    },
                    status=403,
                )

            return self._redirect_to_login(request)

        return self.get_response(request)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _is_exempt(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)

    @staticmethod
    def _wants_json(request) -> bool:
        """Detect AJAX / JSON clients via Accept or X-Requested-With."""
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return True
        accept = request.headers.get("accept", "")
        return "application/json" in accept and "text/html" not in accept

    @staticmethod
    def _redirect_to_login(request):
        try:
            login_url = reverse("accounts:login")
        except NoReverseMatch:
            login_url = "/accounts/login/"
        try:
            return redirect(f"{login_url}?next={request.get_full_path()}")
        except Exception:
            return redirect(login_url)

# accounts/middleware.py — add a second middleware class

class ForcePasswordChangeMiddleware:
    """
    If user.must_change_password is True, redirect every request to
    the change-password page — except the change-password page itself,
    login, logout, and static/admin.

    This ensures a temp password cannot be used for anything except
    changing the password.
    """

    ALLOWED_PATHS = (
        "/accounts/password/",       # change password view
        "/accounts/login/",
        "/accounts/logout/",
        "/accounts/password-change-required/",  # optional interstitial
        "/admin/",
        "/static/",
        "/media/",
        "/favicon.ico",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (
            user
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
        ):
            path = request.path
            if not any(path.startswith(p) for p in self.ALLOWED_PATHS):
                from django.shortcuts import redirect
                from django.contrib import messages
                messages.warning(
                    request,
                    "You must change your password before continuing.",
                )
                return redirect("accounts:password_change")

        return self.get_response(request)


# accounts/middleware.py — add this class

class ForcePasswordChangeMiddleware:
    """
    If user.must_change_password is True, redirect every request to
    the change-password page — except allowed paths.
    """

    ALLOWED_PATHS = (
        "/accounts/password/",
        "/accounts/login/",
        "/accounts/logout/",
        "/admin/",
        "/static/",
        "/media/",
        "/favicon.ico",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (
            user
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
        ):
            path = request.path
            if not any(path.startswith(p) for p in self.ALLOWED_PATHS):
                from django.contrib import messages
                from django.shortcuts import redirect
                messages.warning(
                    request,
                    "You must change your password before continuing.",
                )
                return redirect("accounts:password_change")

        return self.get_response(request)