# inventory/admin.py
from django.contrib import admin

from .models import Category, Product, StockMovement, Supplier


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "product_count", "created_at")
    list_filter = (("business", admin.RelatedOnlyFieldListFilter),)
    search_fields = ("name",)
    list_select_related = ("business",)
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        from django.db.models import Count
        return (
            super().get_queryset(request)
            .select_related("business")
            .annotate(_product_count=Count("products"))
        )

    @admin.display(description="Products", ordering="_product_count")
    def product_count(self, obj):
        return obj._product_count


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "phone", "email", "is_active")
    list_filter = (
        ("business", admin.RelatedOnlyFieldListFilter),
        "is_active",
    )
    search_fields = ("name", "phone", "email", "contact_person")
    list_select_related = ("business",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name", "sku", "business", "category",
        "quantity", "reorder_level", "selling_price", "is_active",
    )
    list_filter = (
        ("business", admin.RelatedOnlyFieldListFilter),
        "category",
        "supplier",
        "is_active",
    )
    search_fields = ("name", "sku", "barcode")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("business", "category", "supplier")
    autocomplete_fields = ("category", "supplier")
    list_per_page = 50
    save_on_top = True


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "product", "movement_type", "quantity",
        "quantity_before", "quantity_after",
        "created_by", "created_at",
    )
    list_filter = (
        "movement_type",
        ("business", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = ("product__name", "product__sku", "reference", "reason")
    readonly_fields = (
        "business", "product", "movement_type",
        "quantity", "quantity_before", "quantity_after",
        "reason", "reference",
        "created_by", "created_at",
    )
    list_select_related = ("product", "created_by", "business")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        # Movements are only created via inventory.services
        return False

    def has_change_permission(self, request, obj=None):
        # Immutable audit log
        return False

    def has_delete_permission(self, request, obj=None):
        # Never delete audit records
        return False