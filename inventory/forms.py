# inventory/forms.py
from django import forms
from django.db import models

from .models import Category, Supplier, Product, StockMovement


# =====================================================================
# Styling mixin — Tailwind (matches accounts app)
# =====================================================================
class TailwindMixin:
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
# Category
# =====================================================================
# inventory/forms.py — CategoryForm

class CategoryForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        if self.business:
            qs = Category.objects.filter(business=self.business, name__iexact=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "A category with this name already exists."
                )
        return name

# =====================================================================
# Supplier
# =====================================================================
# inventory/forms.py — SupplierForm

class SupplierForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name", "contact_person", "phone", "email",
            "address", "is_active",
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        if self.business:
            qs = Supplier.objects.filter(business=self.business, name__iexact=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "A supplier with this name already exists."
                )
        return name

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

# =====================================================================
# Product
# =====================================================================
class ProductForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "sku", "barcode", "name", "category", "supplier",
            "description", "image",
            "cost_price", "selling_price",
            "quantity", "reorder_level", "unit", "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    # ---- Template helpers ----
    BASIC_FIELDS = (
        "sku", "barcode", "name", "category", "supplier",
        "description", "image",
    )
    PRICING_FIELDS = ("cost_price", "selling_price")
    STOCK_FIELDS = ("quantity", "reorder_level", "unit")
    STATUS_FIELDS = ("is_active",)

    def basic_fields(self):
        return [self[n] for n in self.BASIC_FIELDS if n in self.fields]

    def pricing_fields(self):
        return [self[n] for n in self.PRICING_FIELDS if n in self.fields]

    def stock_fields(self):
        return [self[n] for n in self.STOCK_FIELDS if n in self.fields]

    def status_fields(self):
        return [self[n] for n in self.STATUS_FIELDS if n in self.fields]

    # ---- Init ----
    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

        if business:
            self.fields["category"].queryset = business.categories.all()
            self.fields["supplier"].queryset = business.suppliers.filter(is_active=True)

        self.fields["category"].empty_label = "— Select category —"
        self.fields["supplier"].empty_label = "— Select supplier —"

        self.fields["category"].required = False
        self.fields["supplier"].required = False
        self.fields["barcode"].required = False

    # ---- Validation ----
    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip()
        if not sku:
            raise forms.ValidationError("SKU is required.")
        if self.business:
            qs = Product.objects.filter(business=self.business, sku__iexact=sku)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "A product with this SKU already exists."
                )
        return sku

    def clean_barcode(self):
        barcode = (self.cleaned_data.get("barcode") or "").strip() or None
        if barcode and self.business:
            qs = Product.objects.filter(business=self.business, barcode=barcode)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "Another product already uses this barcode."
                )
        return barcode

    def clean(self):
        cleaned = super().clean()
        cost = cleaned.get("cost_price")
        sell = cleaned.get("selling_price")

        if cost is not None and sell is not None and sell < cost:
            self.add_error(
                "selling_price",
                "Selling price should not be below cost price.",
            )
        return cleaned


# =====================================================================
# Stock adjustment
# =====================================================================
class StockAdjustForm(TailwindMixin, forms.Form):
    class Mode(models.TextChoices):
        ADD = "add", "Add stock (receive)"
        REMOVE = "remove", "Remove stock (loss/damage)"
        SET = "set", "Set exact quantity (stock count)"

    mode = forms.ChoiceField(choices=Mode.choices, initial=Mode.ADD)
    quantity = forms.IntegerField(min_value=0)
    reason = forms.CharField(max_length=200, required=False)
    reference = forms.CharField(
        max_length=100, required=False,
        help_text="PO number, invoice, etc.",
    )