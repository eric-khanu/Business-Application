from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasBusiness, IsManager
from . import services
from .serializers import KPISerializer, TimeseriesRowSerializer


class DashboardAPIView(APIView):
    permission_classes = [HasBusiness, IsManager]

    def get(self, request):
        b = request.user.business
        return Response({
            'today': KPISerializer(services.kpi_with_change(b, 'today')).data,
            'low_stock_count': services.low_stock_count(b),
        })


class SalesTimeseriesAPIView(APIView):
    permission_classes = [HasBusiness, IsManager]

    def get(self, request):
        b = request.user.business
        start, end = services.date_range(request.GET.get('period', 'month'))
        rows = services.sales_timeseries(b, request.GET.get('period', 'daily'),
                                         start, end)
        return Response(TimeseriesRowSerializer(rows, many=True).data)