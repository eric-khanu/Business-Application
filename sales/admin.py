# sales/admin.py
from django.contrib import admin

from .models import Sale, SaleItem


# =====================================================================
# Inline
# =====================================================================
class SaleItemInline(admin.TabularInline):
    """Read-only inline showing the sale's line items."""
    model = SaleItem
    extra = 0
    can_delete = False
    fields = (
        "product", "product_name", "product_sku",
        "quantity", "unit_price", "line_total",
    )
    readonly_fields = fields


# =====================================================================
# Sale
# =====================================================================
@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "business", "cashier",
        "total", "payment_method", "status", "created_at",
    )
    list_filter = (
        "status",
        "payment_method",
        ("business", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = (
        "reference",
        "cashier__username",
        "cashier__first_name",
        "cashier__last_name",
    )
    date_hierarchy = "created_at"
    list_select_related = ("business", "cashier")
    list_per_page = 50
    save_on_top = True
    inlines = [SaleItemInline]

    readonly_fields = (
        "reference",
        "business", "cashier",
        "subtotal", "tax_rate", "tax", "discount", "total",
        "payment_method", "amount_paid", "change_due",
        "status",
        "voided_at", "voided_by", "void_reason",
        "notes",
        "created_at", "updated_at",
    )

    fieldsets = (
        ("Reference", {
            "fields": ("reference", "business", "cashier", "created_at"),
        }),
        ("Money", {
            "fields": (
                "subtotal", "tax_rate", "tax", "discount",
                "total", "amount_paid", "change_due",
            ),
        }),
        ("Payment", {
            "fields": ("payment_method",),
        }),
        ("Status", {
            "fields": ("status", "notes"),
        }),
        ("Void (if applicable)", {
            "classes": ("collapse",),
            "fields": ("voided_at", "voided_by", "void_reason"),
        }),
        ("Timestamps", {
            "classes": ("collapse",),
            "fields": ("updated_at",),
        }),
    )

    # ---- Write protection ----
    def has_add_permission(self, request):
        # Sales are created only via POS / API
        return False

    def has_delete_permission(self, request, obj=None):
        # Sales are voided, not deleted
        return False


# =====================================================================
# SaleItem — read-only global view
# =====================================================================
@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = (
        "sale", "product_name", "quantity",
        "unit_price", "line_total",
    )
    search_fields = ("sale__reference", "product_name", "product_sku")
    list_select_related = ("sale", "product")
    list_per_page = 50

    readonly_fields = (
        "sale", "product", "product_name", "product_sku",
        "quantity", "unit_price", "line_total",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Allow read-only viewing
        return False