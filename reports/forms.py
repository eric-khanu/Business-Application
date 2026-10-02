# reports/forms.py
from django import forms
from django.contrib.auth import get_user_model

from accounts.forms import TailwindMixin
from sales.models import Sale

User = get_user_model()


class ReportFilterForm(TailwindMixin, forms.Form):
    """Filters shared by every report view."""

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    cashier = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="All staff",
    )
    payment_method = forms.ChoiceField(
        choices=[("", "All methods")] + list(Sale.Payment.choices),
        required=False,
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business

        if business:
            self.fields["cashier"].queryset = (
                business.users
                .filter(
                    role__in=[
                        "salesperson", "manager", "owner",
                    ],
                    is_active=True,
                    is_active_staff=True,
                )
                .order_by("first_name", "last_name", "username")
            )

    def clean(self):
        cleaned = super().clean()
        df = cleaned.get("date_from")
        dt = cleaned.get("date_to")
        if df and dt and df > dt:
            self.add_error("date_to", "'To' must be on or after 'From'.")
        return cleaned

    def apply(self, queryset):
        """Apply the cleaned filters to a Sale queryset."""
        data = self.cleaned_data

        if data.get("date_from"):
            queryset = queryset.filter(
                created_at__date__gte=data["date_from"],
            )
        if data.get("date_to"):
            queryset = queryset.filter(
                created_at__date__lte=data["date_to"],
            )
        if data.get("cashier"):
            queryset = queryset.filter(cashier=data["cashier"])
        if data.get("payment_method"):
            queryset = queryset.filter(
                payment_method=data["payment_method"],
            )
        return queryset