# accounts/api_views.py
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from rest_framework import generics, permissions, status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .permissions import HasBusiness, IsManager
from .serializers import (
    BusinessSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    RegisterSerializer,
    StaffSerializer,
    UserSerializer,
    tokens_for_user,
)

User = get_user_model()


# =====================================================================
# Auth
# =====================================================================
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        tokens = tokens_for_user(
            user, remember_me=serializer.validated_data.get("remember_me", False),
        )
        return Response(
            {"user": UserSerializer(user).data, "tokens": tokens},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response(
                {"detail": "refresh token required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only attempt blacklist if the app is installed.
        if "rest_framework_simplejwt.token_blacklist" in settings.INSTALLED_APPS:
            try:
                RefreshToken(refresh).blacklist()
            except TokenError:
                return Response(
                    {"detail": "Invalid or expired token."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(status=status.HTTP_204_NO_CONTENT)


class RegisterView(generics.CreateAPIView):
    """Create a new Business + Owner in one transaction; return tokens."""
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        tokens = tokens_for_user(user)
        return Response(
            {"user": UserSerializer(user).data, "tokens": tokens},
            status=status.HTTP_201_CREATED,
        )


class MeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH the current user's own profile."""
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_change"

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password updated."})


# =====================================================================
# Business
# =====================================================================
class BusinessDetailView(generics.RetrieveUpdateAPIView):
    """Retrieve/update the current user's business."""
    serializer_class = BusinessSerializer
    permission_classes = [permissions.IsAuthenticated, HasBusiness, IsManager]

    def get_object(self):
        business = self.request.user.business
        if not business:
            raise PermissionDenied("No business linked to this account.")
        return business


# =====================================================================
# Staff (manager/owner CRUD)
# =====================================================================
class StaffListCreateView(generics.ListCreateAPIView):
    serializer_class = StaffSerializer
    permission_classes = [permissions.IsAuthenticated, HasBusiness, IsManager]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["username", "first_name", "last_name", "email", "employee_id"]
    ordering_fields = ["first_name", "last_name", "username", "hired_at"]
    ordering = ["first_name", "last_name", "username"]

    def get_queryset(self):
        return (
            User.objects
            .filter(business=self.request.user.business)
            .select_related("business")
        )


class StaffDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StaffSerializer
    permission_classes = [permissions.IsAuthenticated, HasBusiness, IsManager]

    def get_queryset(self):
        return (
            User.objects
            .filter(business=self.request.user.business)
            .select_related("business")
        )

    def perform_destroy(self, instance):
        actor = self.request.user

        if instance.pk == actor.pk:
            raise PermissionDenied("You can't delete yourself.")

        if instance.is_owner:
            if not actor.is_owner:
                raise PermissionDenied(
                    "Only an owner can delete another owner."
                )
            other_owners = (
                User.objects
                .filter(business=instance.business, role=User.Role.OWNER)
                .exclude(pk=instance.pk)
                .exists()
            )
            if not other_owners:
                raise PermissionDenied(
                    "You can't delete the last owner of this business."
                )

        instance.delete()