# sales/forms.py
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from accounts.forms import TailwindMixin
from inventory.models import Product

from .models import Sale, SaleItem

User = get_user_model()


# =====================================================================
# POS: single-item quick sale (server fallback + tests)
# =====================================================================
class QuickSaleForm(TailwindMixin, forms.Form):
    """Single-item sale. Used when POS JS is unavailable."""

    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        widget=forms.Select(),
    )
    quantity = forms.IntegerField(min_value=1, initial=1)
    unit_price = forms.DecimalField(
        min_value=Decimal("0"), max_digits=12, decimal_places=2,
        required=False,
        help_text="Leave blank to use the product selling price.",
    )
    discount = forms.DecimalField(
        min_value=Decimal("0"), max_digits=12, decimal_places=2,
        required=False, initial=Decimal("0"),
    )
    payment_method = forms.ChoiceField(
        choices=Sale.Payment.choices,
        initial=Sale.Payment.CASH,
    )
    amount_paid = forms.DecimalField(
        min_value=Decimal("0"), max_digits=12, decimal_places=2,
        required=False,
        help_text="Required for cash payments.",
    )
    notes = forms.CharField(
        max_length=500, required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        if business:
            self.fields["product"].queryset = (
                Product.objects
                .filter(business=business, is_active=True, quantity__gt=0)
                .order_by("name")
            )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        qty = cleaned.get("quantity")
        method = cleaned.get("payment_method")
        amount_paid = cleaned.get("amount_paid")
        unit_price = cleaned.get("unit_price")
        discount = cleaned.get("discount") or Decimal("0")

        if product and qty:
            if qty > product.quantity:
                self.add_error(
                    "quantity",
                    f"Only {product.quantity} {product.unit} in stock.",
                )

            price = unit_price if unit_price is not None else product.selling_price
            subtotal = price * qty
            tax_rate = (
                (self.business.tax_rate if self.business else Decimal("0"))
                or Decimal("0")
            )
            tax = (subtotal * tax_rate / Decimal("100")).quantize(Decimal("0.01"))
            total = (subtotal + tax - discount).quantize(Decimal("0.01"))

            if discount > subtotal + tax:
                self.add_error("discount", "Discount cannot exceed the total.")

            if method == Sale.Payment.CASH:
                if amount_paid is None:
                    self.add_error(
                        "amount_paid", "Enter the cash amount tendered.",
                    )
                elif amount_paid < total:
                    self.add_error(
                        "amount_paid",
                        f"Cash tendered ({amount_paid}) is less than total ({total}).",
                    )

            cleaned["_subtotal"] = subtotal
            cleaned["_tax"] = tax
            cleaned["_total"] = total
            cleaned["_unit_price"] = price

        return cleaned

    def to_items_data(self):
        """Convert cleaned data into the `items_data` list the service expects."""
        return [{
            "product_id": self.cleaned_data["product"].id,
            "quantity": self.cleaned_data["quantity"],
            "unit_price": str(self.cleaned_data["_unit_price"]),
        }]


# =====================================================================
# POS: cart submission (validates the JSON payload server-side)
# =====================================================================
class CartSubmissionForm(TailwindMixin, forms.Form):
    """
    Validates the JSON `cart` posted from the POS page.
    The cart is `[{'product_id', 'quantity', 'unit_price'}, ...]`.
    """

    cart = forms.CharField(widget=forms.HiddenInput())
    payment_method = forms.ChoiceField(
        choices=Sale.Payment.choices,
        initial=Sale.Payment.CASH,
    )
    discount = forms.DecimalField(
        min_value=Decimal("0"), max_digits=12, decimal_places=2,
        required=False, initial=Decimal("0"),
    )
    amount_paid = forms.DecimalField(
        min_value=Decimal("0"), max_digits=12, decimal_places=2,
        required=False,
    )
    notes = forms.CharField(max_length=500, required=False)

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self._parsed_items = []
        self._computed = {}

    def clean_cart(self):
        import json

        raw = self.cleaned_data["cart"]
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise ValidationError("Invalid cart payload.")

        if not isinstance(data, list) or not data:
            raise ValidationError("Cart is empty.")

        business = self.business
        if not business:
            raise ValidationError("No business associated.")

        parsed = []
        for idx, row in enumerate(data):
            if not isinstance(row, dict):
                raise ValidationError(f"Row {idx}: invalid format.")
            pid = row.get("product_id")
            qty = row.get("quantity")
            price = row.get("unit_price")

            if not pid or not qty or int(qty) <= 0:
                raise ValidationError(f"Row {idx}: quantity must be > 0.")

            try:
                product = Product.objects.get(
                    id=pid, business=business, is_active=True,
                )
            except Product.DoesNotExist:
                raise ValidationError(f"Row {idx}: product not found.")

            if int(qty) > product.quantity:
                raise ValidationError(
                    f"Row {idx}: only {product.quantity} of {product.name} in stock."
                )

            parsed.append({
                "product_id": product.id,
                "quantity": int(qty),
                "unit_price": str(
                    price if price is not None else product.selling_price
                ),
            })

        self._parsed_items = parsed
        return raw

    def clean(self):
        cleaned = super().clean()
        if not self._parsed_items:
            return cleaned

        subtotal = sum(
            Decimal(i["unit_price"]) * i["quantity"] for i in self._parsed_items
        )
        tax_rate = (
            (self.business.tax_rate if self.business else Decimal("0"))
            or Decimal("0")
        )
        tax = (subtotal * tax_rate / Decimal("100")).quantize(Decimal("0.01"))
        discount = cleaned.get("discount") or Decimal("0")
        total = (subtotal + tax - discount).quantize(Decimal("0.01"))

        if discount > subtotal + tax:
            self.add_error("discount", "Discount cannot exceed the total.")

        method = cleaned.get("payment_method")
        amount_paid = cleaned.get("amount_paid")
        if method == Sale.Payment.CASH:
            if amount_paid is None:
                self.add_error("amount_paid", "Enter cash amount tendered.")
            elif amount_paid < total:
                self.add_error(
                    "amount_paid",
                    f"Cash tendered ({amount_paid}) is less than total ({total}).",
                )

        self._computed = {
            "subtotal": subtotal, "tax": tax,
            "discount": discount, "total": total,
        }
        return cleaned

    @property
    def items_data(self):
        return self._parsed_items

    @property
    def computed(self):
        return self._computed


# =====================================================================
# Void sale
# =====================================================================
class VoidSaleForm(TailwindMixin, forms.Form):
    reason = forms.CharField(
        max_length=255, required=True,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Why is this sale being voided?",
        }),
    )


# =====================================================================
# Sale list filters (managers)
# =====================================================================
class SaleFilterForm(TailwindMixin, forms.Form):
    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Search reference…"}),
    )
    cashier = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False, empty_label="All staff",
    )
    status = forms.ChoiceField(
        choices=[("", "All statuses")] + list(Sale.Status.choices),
        required=False,
    )
    payment_method = forms.ChoiceField(
        choices=[("", "All methods")] + list(Sale.Payment.choices),
        required=False,
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business:
            self.fields["cashier"].queryset = (
                business.users
                .filter(
                    role__in=[
                        "salesperson", "manager", "owner",
                    ],
                    is_active_staff=True,
                )
                .order_by("first_name", "last_name", "username")
            )

    def clean(self):
        cleaned = super().clean()
        df, dt = cleaned.get("date_from"), cleaned.get("date_to")
        if df and dt and df > dt:
            self.add_error(
                "date_to", '"To" date must be on or after "From" date.',
            )
        return cleaned

    def apply(self, queryset):
        """Apply the cleaned filters to a Sale queryset."""
        data = self.cleaned_data
        if data.get("q"):
            queryset = queryset.filter(reference__icontains=data["q"])
        if data.get("cashier"):
            queryset = queryset.filter(cashier=data["cashier"])
        if data.get("status"):
            queryset = queryset.filter(status=data["status"])
        if data.get("payment_method"):
            queryset = queryset.filter(payment_method=data["payment_method"])
        if data.get("date_from"):
            queryset = queryset.filter(created_at__date__gte=data["date_from"])
        if data.get("date_to"):
            queryset = queryset.filter(created_at__date__lte=data["date_to"])
        return queryset


# =====================================================================
# Optional: pending-sale item editing
# =====================================================================
class SaleItemForm(TailwindMixin, forms.ModelForm):
    """Edit a line item on a *pending* sale only."""

    class Meta:
        model = SaleItem
        fields = ["product", "quantity", "unit_price"]

    def __init__(self, *args, business=None, sale=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.sale = sale
        if business:
            self.fields["product"].queryset = Product.objects.filter(
                business=business, is_active=True,
            )

    def clean(self):
        cleaned = super().clean()
        if self.sale and self.sale.status != Sale.Status.PENDING:
            raise ValidationError(
                "Only pending sales can have their items edited."
            )
        product = cleaned.get("product")
        qty = cleaned.get("quantity")
        if product and qty and qty > product.quantity:
            self.add_error(
                "quantity",
                f"Only {product.quantity} {product.unit} in stock.",
            )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.product:
            instance.product_name = instance.product.name
            instance.product_sku = instance.product.sku
        instance.line_total = instance.quantity * instance.unit_price
        if commit:
            instance.save()
        return instance