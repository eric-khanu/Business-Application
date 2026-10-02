# accounts/forms.py
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from .models import Business

User = get_user_model()


# =====================================================================
# Styling mixin — Tailwind
# =====================================================================
class TailwindMixin:
    """Adds Tailwind classes to every widget."""

    INPUT = "input"
    CHECKBOX = (
        "h-4 w-4 rounded border-slate-300 text-brand-600 "
        "focus:ring-brand-500 dark:border-slate-600 dark:bg-slate-800"
    )
    TEXTAREA = "input min-h-[80px]"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget

            if isinstance(widget, forms.CheckboxInput):
                css = self.CHECKBOX
            elif isinstance(widget, forms.Textarea):
                css = self.TEXTAREA
            else:
                css = self.INPUT

            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css}".strip()

            if name in self.errors:
                widget.attrs["aria-invalid"] = "true"

            if field.required and not isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("aria-required", "true")


# =====================================================================
# Business
# =====================================================================
class BusinessForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = Business
        fields = ["name", "address", "phone1", "phone2", "email",
                  "logo", "tax_rate", "currency"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


# =====================================================================
# Authentication
# =====================================================================
class LoginForm(TailwindMixin, AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            "autofocus": True,
            "placeholder": "Username",
            "autocomplete": "username",
        }),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "placeholder": "Password",
            "autocomplete": "current-password",
        }),
    )
    remember_me = forms.BooleanField(required=False, initial=True)


# =====================================================================
# Staff — shared base
# =====================================================================
class BaseStaffForm(TailwindMixin, forms.ModelForm):
    """Shared logic for StaffCreateForm and StaffEditForm."""

    ACCOUNT_FIELDS = (
        "username", "first_name", "last_name", "email",
        "password1", "password2", "role", "is_active_staff",
        "hired_at", "employee_id",
    )
    PROFILE_FIELDS = (
        "phone1", "phone2", "address", "profile_picture",
    )

    class Meta:
        model = User
        fields = [
            "username", "first_name", "last_name", "email",
            "phone1", "phone2", "address", "employee_id",
            "profile_picture", "role", "is_active_staff", "hired_at",
        ]
        widgets = {
            "hired_at": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        self._apply_role_restrictions()

    # ---- Template helpers ----
    def account_fields(self):
        return [self[n] for n in self.ACCOUNT_FIELDS if n in self.fields]

    def profile_fields(self):
        return [self[n] for n in self.PROFILE_FIELDS if n in self.fields]

    # ---- Role restrictions ----
    def _apply_role_restrictions(self):
        actor = self.actor
        if not actor or actor.is_owner:
            return

        self.fields["role"].choices = [
            c for c in User.Role.choices if c[0] != User.Role.OWNER
        ]
        self.fields["role"].initial = User.Role.SALESPERSON

        if self.instance.pk and self.instance.is_owner:
            self.fields["role"].disabled = True
            self.fields["is_active_staff"].disabled = True

    # ---- Validation ----
    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        qs = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email:
            qs = User.objects.filter(email__iexact=email)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "Another account already uses this email."
                )
        return email

    def clean_role(self):
        role = self.cleaned_data.get("role")
        instance = self.instance
        if (instance.pk and instance.role == User.Role.OWNER
                and role != User.Role.OWNER):
            other_owners = (
                User.objects
                .filter(business=instance.business, role=User.Role.OWNER)
                .exclude(pk=instance.pk)
                .exists()
            )
            if not other_owners:
                raise forms.ValidationError(
                    "You can't demote the last owner of this business."
                )
        return role

    def clean_is_active_staff(self):
        value = self.cleaned_data.get("is_active_staff")
        if (self.actor and self.instance.pk
                and self.instance.pk == self.actor.pk and not value):
            raise forms.ValidationError("You can't deactivate yourself.")
        return value


class StaffCreateForm(BaseStaffForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
        help_text="At least 8 characters. Cannot be entirely numeric.",
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
    )

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")

        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")

        if p1:
            try:
                validate_password(p1, user=self.instance)
            except forms.ValidationError as e:
                self.add_error("password1", e)

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class StaffEditForm(BaseStaffForm):
    """No password fields. Managers use staff_reset_password for password changes."""


# =====================================================================
# Profile / password
# =====================================================================
class ProfileForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone1", "phone2",
                  "address", "profile_picture"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


class CustomPasswordChangeForm(TailwindMixin, PasswordChangeForm):
    pass


# =====================================================================
# Register (self-signup: business + owner)
# =====================================================================
class RegisterForm(TailwindMixin, forms.Form):
    """Creates a Business + Owner in one transaction."""

    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name", required=False)
    email = forms.EmailField(label="Work email")
    username = forms.CharField(
        max_length=150,
        label="Username",
        help_text="Used to sign in. Letters, digits, and @/./+/-/_ only.",
    )

    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        strip=False,
    )

    business_name = forms.CharField(
        max_length=200,
        label="Business name",
        help_text="Shown on receipts and invoices.",
    )
    business_phone = forms.CharField(
        max_length=20, required=False, label="Business phone",
    )

    accept_terms = forms.BooleanField(
        required=True,
        label="I agree to the Terms of Service and Privacy Policy.",
    )

    def clean_username(self):
        value = (self.cleaned_data["username"] or "").strip()
        if User.objects.filter(username__iexact=value).exists():
            raise forms.ValidationError("That username is already taken.")
        return value

    def clean_email(self):
        value = (self.cleaned_data["email"] or "").strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return value

    def clean_business_name(self):
        value = (self.cleaned_data["business_name"] or "").strip()
        if not value:
            raise forms.ValidationError("Business name is required.")
        return value

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")

        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")

        if p1:
            try:
                probe = User(
                    username=cleaned.get("username", ""),
                    email=cleaned.get("email", ""),
                    first_name=cleaned.get("first_name", ""),
                )
                validate_password(p1, user=probe)
            except forms.ValidationError as e:
                self.add_error("password1", e)

        return cleaned

    @transaction.atomic
    def save(self):
        data = self.cleaned_data

        business = Business.objects.create(
            name=data["business_name"],
            phone1=data.get("business_phone", ""),
            email=data["email"],
        )

        user = User(
            username=data["username"],
            email=data["email"],
            first_name=data["first_name"],
            last_name=data.get("last_name", ""),
            business=business,
            role=User.Role.OWNER,
        )
        user.set_password(data["password1"])
        user.save()

        business.owner = user
        business.save(update_fields=["owner", "updated_at"])

        return user