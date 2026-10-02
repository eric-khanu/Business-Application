# sales/api_views.py
"""
DRF API for the sales app.

- Salespersons see and create only their OWN sales.
- Managers/owners see all sales in their business and can void them.
- Creation and voiding go through services — no direct writes here.
"""

from datetime import datetime

from django.db.models import Count, Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import HasBusiness, IsManager, IsSalesperson

from .models import Sale
from .serializers import (
    SaleCreateSerializer,
    SaleDetailSerializer,
    SaleListSerializer,
    SaleVoidSerializer,
)


class SaleViewSet(viewsets.ModelViewSet):
    """
    Sale CRUD + custom actions.

    Endpoints:
      GET    /sales/                 list (paginated)
      POST   /sales/                 create (POS checkout)
      GET    /sales/{id}/            retrieve
      POST   /sales/{id}/void/       void a completed sale (manager+)
      GET    /sales/summary/         aggregate stats
    """

    permission_classes = [IsAuthenticated, HasBusiness, IsSalesperson]
    http_method_names = ["get", "post", "head", "options"]  # no PUT/PATCH/DELETE

    # ---------------------------------------------------------------
    # Queryset — always tenant-scoped, with role-aware sub-scoping
    # ---------------------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        qs = (
            Sale.objects
            .filter(business=user.business)
            .select_related("cashier", "voided_by")
        )

        # Salespeople: only their own sales
        if user.is_salesperson:
            qs = qs.filter(cashier=user)
        else:
            # Managers/owners: optional cashier filter
            cashier_id = self.request.query_params.get("cashier")
            if cashier_id:
                qs = qs.filter(cashier_id=cashier_id)

        # ---- Common filters (with validation) ----

        status_param = self.request.query_params.get("status")
        if status_param:
            if status_param not in Sale.Status.values:
                raise ValidationError({
                    "status": f"Invalid status. Choose from: {Sale.Status.values}",
                })
            qs = qs.filter(status=status_param)

        method = self.request.query_params.get("payment_method")
        if method:
            if method not in Sale.Payment.values:
                raise ValidationError({
                    "payment_method": (
                        f"Invalid method. Choose from: {Sale.Payment.values}"
                    ),
                })
            qs = qs.filter(payment_method=method)

        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(reference__icontains=q)

        date_from = self.request.query_params.get("from")
        if date_from:
            try:
                d = datetime.strptime(date_from, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError({"from": "Use YYYY-MM-DD format."})
            qs = qs.filter(created_at__date__gte=d)

        date_to = self.request.query_params.get("to")
        if date_to:
            try:
                d = datetime.strptime(date_to, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError({"to": "Use YYYY-MM-DD format."})
            qs = qs.filter(created_at__date__lte=d)

        return qs

    # ---------------------------------------------------------------
    # Serializer routing
    # ---------------------------------------------------------------
    def get_serializer_class(self):
        if self.action == "list":
            return SaleListSerializer
        if self.action == "create":
            return SaleCreateSerializer
        if self.action == "void":
            return SaleVoidSerializer
        return SaleDetailSerializer

    # ---------------------------------------------------------------
    # Overrides
    # ---------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()  # delegates to services.create_sale
        return Response(
            serializer.data, status=status.HTTP_201_CREATED,
        )

    def retrieve(self, request, *args, **kwargs):
        sale = self.get_object()  # queryset already enforces ownership
        serializer = self.get_serializer(sale)
        return Response(serializer.data)

    # ---------------------------------------------------------------
    # Custom actions
    # ---------------------------------------------------------------
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated, HasBusiness, IsManager],
    )
    def void(self, request, pk=None):
        """
        Void a completed sale. Restores stock and marks the sale void.
        Only managers/owners can do this.
        """
        sale = self.get_object()
        serializer = SaleVoidSerializer(
            data=request.data,
            context={"request": request, "sale": sale},
        )
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()  # delegates to services.void_sale
        except ValueError as e:
            raise ValidationError({"detail": str(e)})

        sale.refresh_from_db()
        return Response(
            SaleDetailSerializer(sale, context={"request": request}).data,
        )

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """
        Aggregate stats for the current user's filtered queryset.
        Always restricted to COMPLETED sales, regardless of ?status=...
        """
        qs = self.get_queryset().filter(status=Sale.Status.COMPLETED)
        totals = qs.aggregate(
            total=Sum("total"),
            count=Count("id"),
        )
        return Response({
            "count": totals["count"] or 0,
            "total": str(totals["total"] or 0),
        })