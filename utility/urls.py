from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BenefitViewSet,
    CategoryViewSet,
    InvoiceViewSet,
    MeterReadingViewSet,
    MeterViewSet,
    PropertyViewSet,
    ProviderViewSet,
    ServiceAccountViewSet,
    TariffViewSet,
    PaymentViewSet
)

router = DefaultRouter()
router.register(r"my-properties", PropertyViewSet, basename="property")
router.register(r"providers", ProviderViewSet, basename="provider")
router.register(r"accounts", ServiceAccountViewSet, basename="accounts")
router.register(r"meters", MeterViewSet, basename="meter")
router.register(r"readings", MeterReadingViewSet, basename="meter-reading")
router.register(r"categories", CategoryViewSet, basename="category")
router.register(r"tariffs", TariffViewSet, basename="tariff")
router.register(r"benefits", BenefitViewSet, basename="benefit")
router.register(r"invoices", InvoiceViewSet, basename="invoice")
router.register(r"payments", PaymentViewSet, basename="payment")

# 2. Об'єднання маршрутів API та сторінок документації
urlpatterns = [
    path("", include(router.urls)),
]
