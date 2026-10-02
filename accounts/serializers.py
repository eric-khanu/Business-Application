# accounts/serializers.py — change the docstring
"""
Serializers for the accounts app.

Structure:
  - BusinessSerializers: Business, BusinessMini
  - UserSerializers:     User, UserMini
  - StaffSerializers:    Staff (manager CRUD)
  - AuthSerializers:     Login, Register, ChangePassword
  - token helper:        tokens_for_user
"""

from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model, password_validation
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Business

User = get_user_model()


# =====================================================================
# Helpers
# =====================================================================
def _get_actor(context) -> User:
    """Return the authenticated actor or raise a DRF-friendly error."""
    request = context.get("request")
    actor = getattr(request, "user", None)
    if not actor or not actor.is_authenticated:
        raise serializers.ValidationError("Authentication required.")
    return actor


def _get_actor_business(context) -> Business:
    actor = _get_actor(context)
    if not actor.business_id:
        raise serializers.ValidationError(
            "Your account is not linked to a business."
        )
    return actor.business


# =====================================================================
# Business
# =====================================================================
class BusinessMiniSerializer(serializers.ModelSerializer):
    """Lightweight — embedded in user responses."""

    class Meta:
        model = Business
        fields = ["id", "name", "slug", "currency", "logo"]
        read_only_fields = fields


class BusinessSerializer(serializers.ModelSerializer):
    """Read/update for a tenant's own business profile."""

    logo = serializers.ImageField(required=False, allow_null=True)
    owner = serializers.SerializerMethodField()

    class Meta:
        model = Business
        fields = [
            "id", "name", "slug", "address",
            "phone1", "phone2", "email", "logo",
            "tax_rate", "currency", "created_at", "updated_at",
            "owner",
        ]
        read_only_fields = ["id", "slug", "created_at", "updated_at", "owner"]

    def get_owner(self, obj):
        # Use the FK — no extra query thanks to select_related in the view.
        owner = obj.owner
        if not owner:
            return None
        return {
            "id": owner.id,
            "username": owner.username,
            "full_name": owner.get_full_name() or owner.username,
            "email": owner.email,
        }

    def validate_tax_rate(self, value):
        if value is None:
            return value
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                "Tax rate must be between 0 and 100."
            )
        return value

    def validate_currency(self, value):
        value = (value or "").strip().upper()
        if len(value) != 3:
            raise serializers.ValidationError(
                "Use a 3-letter ISO currency code, e.g. USD."
            )
        return value


# =====================================================================
# User
# =====================================================================
class UserMiniSerializer(serializers.ModelSerializer):
    """Minimal representation for embedding (sales, invoices, etc.)."""

    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "role", "profile_picture"]
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class UserSerializer(serializers.ModelSerializer):
    """
    Read/write serializer for a user's OWN profile.

    `role`, `business`, `is_active_staff`, `employee_id`, and `username`
    are read-only here — they can only be changed by a manager via
    StaffSerializer.
    """

    business = BusinessMiniSerializer(read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    full_name = serializers.SerializerMethodField()
    profile_picture = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name",
            "full_name", "role", "role_display", "business",
            "address", "phone1", "phone2", "profile_picture",
            "employee_id", "is_active_staff", "hired_at",
            "date_joined", "last_login",
        ]
        read_only_fields = [
            "id", "username", "role", "business",
            "is_active_staff", "employee_id",
            "date_joined", "last_login",
        ]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if not value:
            return value
        qs = User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "Another account already uses this email."
            )
        return value


# =====================================================================
# Staff (manager-facing CRUD)
# =====================================================================
class StaffSerializer(serializers.ModelSerializer):
    """
    Manager/Owner-facing serializer: create + edit staff within their
    own business. Enforces:
      - role escalation rules
      - per-business employee_id uniqueness
      - self-protection (no self-demote, no self-deactivate)
      - password pairing + AUTH_PASSWORD_VALIDATORS
    """

    password = serializers.CharField(
        write_only=True, required=False, allow_blank=False, min_length=8,
    )
    password_confirm = serializers.CharField(write_only=True, required=False)
    business = BusinessMiniSerializer(read_only=True)
    full_name = serializers.SerializerMethodField()
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "last_name", "full_name",
            "email", "address", "phone1", "phone2",
            "profile_picture", "employee_id",
            "role", "role_display", "is_active_staff", "hired_at",
            "business", "password", "password_confirm",
        ]
        read_only_fields = ["id", "business", "role_display"]

    # ---- Field-level validation ----
    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def validate_username(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Username is required.")
        qs = User.objects.filter(username__iexact=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("That username is taken.")
        return value

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if not value:
            return value
        qs = User.objects.filter(email__iexact=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "Another account already uses this email."
            )
        return value

    def validate_employee_id(self, value):
        value = (value or "").strip() or None
        if not value:
            return value
        business = self._business()
        if not business:
            return value
        qs = User.objects.filter(business=business, employee_id=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "That employee ID is already used in your business."
            )
        return value

    def validate_role(self, value):
        actor = _get_actor(self.context)
        if value == User.Role.OWNER and not actor.is_owner:
            raise serializers.ValidationError(
                "Only an owner can assign the owner role."
            )
        return value

    # ---- Object-level validation ----
    def validate(self, attrs):
        actor = _get_actor(self.context)
        instance = self.instance

        if instance and instance.pk:
            # Self-protection
            if instance.pk == actor.pk:
                if "role" in attrs and attrs["role"] != instance.role:
                    raise serializers.ValidationError(
                        {"role": "You can't change your own role."}
                    )
                if attrs.get("is_active_staff") is False:
                    raise serializers.ValidationError(
                        {"is_active_staff": "You can't deactivate yourself."}
                    )

            # Managers cannot modify owners
            if instance.is_owner and not actor.is_owner:
                raise serializers.ValidationError(
                    "Only an owner can modify another owner."
                )

            # Prevent demoting the last owner
            if (
                instance.is_owner
                and attrs.get("role", instance.role) != User.Role.OWNER
            ):
                other_owners = (
                    User.objects
                    .filter(business=instance.business, role=User.Role.OWNER)
                    .exclude(pk=instance.pk)
                    .exists()
                )
                if not other_owners:
                    raise serializers.ValidationError(
                        {"role": "You can't demote the last owner."}
                    )

        # Password rules
        p1 = attrs.get("password")
        p2 = attrs.get("password_confirm")

        if instance is None:
            if not p1:
                raise serializers.ValidationError(
                    {"password": "Password is required."}
                )
            if p1 != p2:
                raise serializers.ValidationError(
                    {"password_confirm": "Passwords do not match."}
                )
            password_validation.validate_password(p1)
        else:
            if p1 or p2:
                if p1 != p2:
                    raise serializers.ValidationError(
                        {"password_confirm": "Passwords do not match."}
                    )
                password_validation.validate_password(p1, user=instance)

        return attrs

    # ---- Helpers ----
    def _business(self):
        if self.instance and self.instance.business_id:
            return self.instance.business
        return _get_actor_business(self.context)

    # ---- Persistence ----
    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop("password_confirm", None)
        password = validated_data.pop("password")
        business = _get_actor_business(self.context)

        # Actor can only create within their own business
        if self.instance and self.instance.business_id != business.id:
            raise serializers.ValidationError(
                "You can only create staff within your own business."
            )

        user = User(**validated_data, business=business)
        user.set_password(password)
        try:
            user.save()
        except IntegrityError as exc:
            if "unique_employee_id_per_business" in str(exc):
                raise serializers.ValidationError(
                    {"employee_id": "That employee ID is already used."}
                )
            raise
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        validated_data.pop("password_confirm", None)
        password = validated_data.pop("password", None)

        for field, value in validated_data.items():
            setattr(instance, field, value)

        # Keep Django's login gate in sync with is_active_staff
        if "is_active_staff" in validated_data:
            instance.is_active = validated_data["is_active_staff"]

        if password:
            instance.set_password(password)

        try:
            instance.save()
        except IntegrityError as exc:
            if "unique_employee_id_per_business" in str(exc):
                raise serializers.ValidationError(
                    {"employee_id": "That employee ID is already used."}
                )
            raise
        return instance


# =====================================================================
# Auth / JWT
# =====================================================================
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"},
    )
    remember_me = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        user = authenticate(
            username=attrs["username"].strip(),
            password=attrs["password"],
        )
        # Same message for every failure mode → no user enumeration.
        if (
            not user
            or not user.is_active
            or not user.is_active_staff
            or not user.business_id
        ):
            raise AuthenticationFailed("Invalid username or password.")
        attrs["user"] = user
        return attrs


class RegisterSerializer(serializers.ModelSerializer):
    """
    Self-signup: creates a Business AND the Owner account in one
    transaction, then links them via Business.owner.
    """

    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)
    business_name = serializers.CharField(max_length=200)
    business_phone = serializers.CharField(
        max_length=20, required=False, allow_blank=True,
    )

    class Meta:
        model = User
        fields = [
            "username", "email", "password", "password_confirm",
            "first_name", "last_name",
            "business_name", "business_phone",
        ]

    def validate_username(self, value):
        value = (value or "").strip()
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("That username is taken.")
        return value

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "Another account already uses this email."
            )
        return value

    def validate_business_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Business name is required.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        password_validation.validate_password(attrs["password"])
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop("password_confirm")
        business_name = validated_data.pop("business_name")
        business_phone = validated_data.pop("business_phone", "")
        password = validated_data.pop("password")

        business = Business.objects.create(
            name=business_name, phone1=business_phone,
        )
        user = User(
            **validated_data,
            business=business,
            role=User.Role.OWNER,
        )
        user.set_password(password)
        user.save()

        business.owner = user
        business.save(update_fields=["owner", "updated_at"])
        return user


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "Passwords do not match."}
            )
        password_validation.validate_password(
            attrs["new_password"], user=self.context["request"].user,
        )
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return user

# =====================================================================
# Token helper
# =====================================================================
from datetime import timedelta


def tokens_for_user(user, remember_me: bool = False) -> dict:
    """
    Issue a fresh access + refresh pair.

    If `remember_me` is False, the refresh token is short-lived (1 day).
    Otherwise, it uses the default SIMPLE_JWT REFRESH_TOKEN_LIFETIME.
    """
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role
    refresh["business_id"] = user.business_id

    if not remember_me:
        refresh.set_exp(lifetime=timedelta(days=1))

    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "access_expires_in": int(refresh.access_token.lifetime.total_seconds()),
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "business_id": user.business_id,
        },
    }