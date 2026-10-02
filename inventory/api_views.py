# inventory/api_views.py
from django.db.models import Count, F, Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import HasBusiness, IsManager, IsSalesperson

from .models import Category, Product, StockMovement, Supplier
from .serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    ProductWriteSerializer,
    StockAdjustSerializer,
    StockMovementSerializer,
    SupplierSerializer,
)
from .services import (
    InsufficientStock,
    adjust_stock,
    receive_stock,
    remove_stock,
    set_stock,
)


# =====================================================================
# Base mixin
# =====================================================================
class BusinessScopedMixin:
    """Every queryset is scoped to request.user.business."""

    permission_classes = [IsAuthenticated, HasBusiness]

    def get_business(self):
        return self.request.user.business


# =====================================================================
# Categories
# =====================================================================
class CategoryViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = CategorySerializer

    def get_queryset(self):
        qs = (
            Category.objects
            .filter(business=self.get_business())
            .annotate(product_count=Count("products"))
        )
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(name__icontains=q)
        return qs

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated(), HasBusiness(), IsManager()]
        return super().get_permissions()


# =====================================================================
# Suppliers
# =====================================================================
class SupplierViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    serializer_class = SupplierSerializer

    def get_queryset(self):
        qs = Supplier.objects.filter(business=self.get_business())
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(contact_person__icontains=q)
                | Q(email__icontains=q)
            )
        if self.request.query_params.get("active") == "1":
            qs = qs.filter(is_active=True)
        return qs

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated(), HasBusiness(), IsManager()]
        return super().get_permissions()


# =====================================================================
# Products
# =====================================================================
class ProductViewSet(BusinessScopedMixin, viewsets.ModelViewSet):
    def get_queryset(self):
        qs = (
            Product.objects
            .filter(business=self.get_business())
            .select_related("category", "supplier")
        )

        # Search: name, sku, barcode
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(sku__icontains=q)
                | Q(barcode__icontains=q)
            )

        # Filter: category
        cat = self.request.query_params.get("category")
        if cat:
            qs = qs.filter(category_id=cat)

        # Filter: supplier
        sup = self.request.query_params.get("supplier")
        if sup:
            qs = qs.filter(supplier_id=sup)

        # Filter: low stock
        if self.request.query_params.get("low_stock") == "1":
            qs = qs.filter(quantity__lte=F("reorder_level"))

        # Filter: out of stock
        if self.request.query_params.get("out_of_stock") == "1":
            qs = qs.filter(quantity=0)

        # Filter: active (default to active only unless explicitly asked)
        if self.request.query_params.get("inactive") != "1":
            qs = qs.filter(is_active=True)

        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return ProductListSerializer
        if self.action in ("create", "update", "partial_update"):
            return ProductWriteSerializer
        return ProductDetailSerializer

    def get_permissions(self):
        # Custom actions and writes need manager+; reads are fine for anyone
        if self.action in (
            "create", "update", "partial_update", "destroy",
            "adjust_stock_action",
        ):
            return [IsAuthenticated(), HasBusiness(), IsManager()]
        return [IsAuthenticated(), HasBusiness(), IsSalesperson()]

    def perform_destroy(self, instance):
        """
        Soft-delete if the product has any stock movement history;
        hard-delete otherwise.
        """
        if instance.movements.exists():
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])
        else:
            instance.delete()

    # ---- Custom actions ----
    @action(detail=True, methods=["get"], url_path="movements")
    def movements(self, request, pk=None):
        product = self.get_object()
        qs = (
            product.movements
            .select_related("created_by")
            .order_by("-created_at")[:100]
        )
        return Response(
            StockMovementSerializer(qs, many=True).data
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="adjust-stock",
        permission_classes=[IsAuthenticated, HasBusiness, IsManager],
    )
    def adjust_stock_action(self, request, pk=None):
        product = self.get_object()
        serializer = StockAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            if data["mode"] == "add":
                receive_stock(
                    product=product,
                    quantity=data["quantity"],
                    reason=data.get("reason", ""),
                    reference=data.get("reference", ""),
                    actor=request.user,
                )
            elif data["mode"] == "remove":
                remove_stock(
                    product=product,
                    quantity=data["quantity"],
                    reason=data.get("reason", ""),
                    reference=data.get("reference", ""),
                    actor=request.user,
                )
            else:  # "set"
                set_stock(
                    product=product,
                    new_quantity=data["quantity"],
                    reason=data.get("reason", "Stock count"),
                    reference=data.get("reference", ""),
                    actor=request.user,
                )
        except InsufficientStock as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        product.refresh_from_db()
        return Response(
            ProductDetailSerializer(
                product, context={"request": request},
            ).data
        )


# =====================================================================
# Stock movements (read-only audit log)
# =====================================================================
class StockMovementViewSet(BusinessScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated, HasBusiness, IsSalesperson]

    def get_queryset(self):
        qs = (
            StockMovement.objects
            .filter(business=self.get_business())
            .select_related("product", "created_by")
        )

        pid = self.request.query_params.get("product")
        if pid:
            qs = qs.filter(product_id=pid)

        mtype = self.request.query_params.get("type")
        if mtype and mtype in StockMovement.MovementType.values:
            qs = qs.filter(movement_type=mtype)

        return qs