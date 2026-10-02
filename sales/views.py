# sales/views.py
"""
Web views for the sales app.

Structure:
  - Helpers
  - POS
  - Sales lists (mine / all)
  - Detail & receipt
  - Void
  - PDF
"""

import json
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import manager_required, salesperson_required

from .forms import CartSubmissionForm, SaleFilterForm, VoidSaleForm
from .models import Sale
from .receipts import generate_receipt_pdf
from .services import (
    InsufficientStock,
    SaleAlreadyVoided,
    SaleNotVoidable,
    create_sale,
    void_sale,
)


# =====================================================================
# Helpers
# =====================================================================
def _business(request):
    if not request.user.business:
        raise PermissionDenied("Your account is not linked to a business.")
    return request.user.business


def _guard_own_sale(request, sale):
    """
    Salespersons can only view their own sales; managers/owners see all.
    Uses Http404 (not 403) so salespersons can't probe for other sales.
    """
    if request.user.is_salesperson and sale.cashier_id != request.user.id:
        raise Http404("Sale not found")


# =====================================================================
# POS
# =====================================================================
# sales/views.py
import json
from decimal import Decimal

from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import redirect, render

from accounts.permissions import salesperson_required

from .forms import CartSubmissionForm
from .services import (
    InsufficientStock,
    create_sale,
)


# sales/views.py
import json
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.permissions import salesperson_required

from .forms import CartSubmissionForm
from .services import (
    InsufficientStock,
    create_sale,
)


@salesperson_required
def pos(request):
    business = request.user.business

    # ------------------------------------------------------------------
    # 1. Fetch products as a plain list.
    # ------------------------------------------------------------------
    products = list(
        business.products
        .filter(is_active=True)
        .order_by("name")
    )

    # ------------------------------------------------------------------
    # 2. Form (handles both GET and POST)
    # ------------------------------------------------------------------
    form = CartSubmissionForm(
        request.POST or None,
        business=business,
    )

    # ------------------------------------------------------------------
    # 3. Handle sale submission
    # ------------------------------------------------------------------
    if request.method == "POST" and form.is_valid():
        try:
            sale = create_sale(
                business=business,
                cashier=request.user,
                items_data=form.items_data,
                payment_method=form.cleaned_data["payment_method"],
                discount=form.cleaned_data.get("discount") or Decimal("0"),
                amount_paid=form.cleaned_data.get("amount_paid"),
                notes=form.cleaned_data.get("notes", ""),
            )
        except (InsufficientStock, ValueError) as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f"Sale {sale.reference} completed.")
            return redirect("sales:receipt", pk=sale.pk)

    elif request.method == "POST":
        for field, errs in form.errors.items():
            for err in errs:
                if field == "__all__":
                    messages.error(request, err)
                else:
                    label = (
                        form.fields[field].label
                        if field in form.fields else field
                    ) or field
                    messages.error(request, f"{label}: {err}")

    # ------------------------------------------------------------------
    # 4. Build products payload for Alpine.
    #    ⚠️ Do NOT call json.dumps() — Django's json_script filter will
    #    serialize this list to JSON exactly once.
    # ------------------------------------------------------------------
    products_payload = [
        {
            "id": p.pk,
            "name": p.name,
            "sku": p.sku or "",
            "selling_price": str(p.selling_price),  # Decimal → str
            "quantity": p.quantity,
            "reorder_level": p.reorder_level,
        }
        for p in products
    ]

    # ------------------------------------------------------------------
    # 5. Preserve cart on POST error (also a Python list — not JSON string)
    # ------------------------------------------------------------------
    initial_cart = []
    if request.method == "POST":
        raw = request.POST.get("cart") or "[]"
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                initial_cart = parsed
        except (TypeError, ValueError):
            initial_cart = []

    # ------------------------------------------------------------------
    # 6. Render
    # ------------------------------------------------------------------
    return render(request, "sales/pos.html", {
        "products": products,               # for {% if not products %}
        "products_payload": products_payload,  # Python list for json_script
        "initial_cart": initial_cart,       # Python list for json_script
        "form": form,
    })


# =====================================================================
# Sales lists
# =====================================================================
@salesperson_required
def my_sales(request):
    """Sales made by the current user."""
    b = _business(request)
    qs = (
        Sale.objects
        .filter(business=b, cashier=request.user)
        .select_related("cashier")
        .order_by("-created_at")
    )

    today = timezone.now().date()
    today_total = (
        qs.filter(
            created_at__date=today,
            status=Sale.Status.COMPLETED,
        ).aggregate(t=Sum("total"))["t"]
        or Decimal("0")
    )

    page = Paginator(qs, 25).get_page(request.GET.get("page"))

    return render(request, "sales/all_sales.html", {
        "page": page,
        "today_total": today_total,
        "form": None,
        "scope": "mine",
        "can_void": request.user.is_manager,
        "show_cashier_column": False,
        "page_title": "My sales",
    })


@manager_required
def all_sales(request):
    """All sales in the business (managers+ only)."""
    b = _business(request)
    base_qs = (
        Sale.objects
        .filter(business=b)
        .select_related("cashier")
        .order_by("-created_at")
    )

    form = SaleFilterForm(request.GET or None, business=b)
    qs = form.apply(base_qs) if form.is_valid() else base_qs

    # Totals — completed sales only (revenue, not "billed")
    totals = qs.aggregate(
        total=Sum("total", filter=Q(status=Sale.Status.COMPLETED)),
        count=Count("id"),
        completed_count=Count(
            "id", filter=Q(status=Sale.Status.COMPLETED),
        ),
    )

    page = Paginator(qs, 25).get_page(request.GET.get("page"))

    return render(request, "sales/all_sales.html", {
        "page": page,
        "totals": totals,
        "form": form,
        "scope": "all",
        "can_void": True,
        "show_cashier_column": True,
        "page_title": "All sales",
    })


# =====================================================================
# Detail & receipt
# =====================================================================
@salesperson_required
def sale_detail(request, pk):
    b = _business(request)
    sale = get_object_or_404(
        Sale.objects
        .select_related("cashier", "voided_by")
        .prefetch_related("items__product"),
        pk=pk,
        business=b,
    )
    _guard_own_sale(request, sale)

    return render(request, "sales/sale_detail.html", {
        "sale": sale,
        "items": sale.items.all(),
        "can_void": (
            request.user.is_manager
            and sale.status == Sale.Status.COMPLETED
        ),
        "can_manage": request.user.is_manager,
    })


@salesperson_required
def receipt(request, pk):
    b = _business(request)
    sale = get_object_or_404(
        Sale.objects
        .select_related("cashier", "voided_by")
        .prefetch_related("items"),
        pk=pk,
        business=b,
    )
    _guard_own_sale(request, sale)

    return render(request, "sales/receipt.html", {
        "sale": sale,
        "business": b,
        "items": sale.items.all(),
        "generated_at": timezone.now(),
        "is_void": sale.status == Sale.Status.VOID,
    })


# =====================================================================
# Void
# =====================================================================
@manager_required
@require_http_methods(["GET", "POST"])
def sale_void(request, pk):
    b = _business(request)
    sale = get_object_or_404(Sale, pk=pk, business=b)

    if sale.status == Sale.Status.VOID:
        messages.info(
            request, f"Sale {sale.reference} is already voided.",
        )
        return redirect("sales:sale_detail", pk=sale.pk)

    if sale.status != Sale.Status.COMPLETED:
        messages.error(request, "Only completed sales can be voided.")
        return redirect("sales:sale_detail", pk=sale.pk)

    if request.method == "POST":
        form = VoidSaleForm(request.POST)
        if form.is_valid():
            try:
                void_sale(
                    sale=sale,
                    user=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except SaleAlreadyVoided as e:
                messages.warning(request, str(e))
                return redirect("sales:sale_detail", pk=sale.pk)
            except (SaleNotVoidable, ValueError) as e:
                messages.error(request, str(e))
                # fall through to re-render the form
            else:
                messages.success(
                    request, f"Sale {sale.reference} voided.",
                )
                return redirect("sales:all_sales")
    else:
        form = VoidSaleForm()

    return render(request, "sales/sale_confirm_void.html", {
        "sale": sale,
        "form": form,
    })


# =====================================================================
# PDF
# =====================================================================
@salesperson_required
def receipt_pdf(request, pk):
    sale = get_object_or_404(
        Sale, pk=pk, business=request.user.business,
    )
    _guard_own_sale(request, sale)
    return generate_receipt_pdf(sale)