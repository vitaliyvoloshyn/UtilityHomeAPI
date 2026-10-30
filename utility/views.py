from datetime import timedelta
from decimal import Decimal
from django_filters import rest_framework as filters

from django.db import transaction
from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import (
    IsAdminUser,
    IsAuthenticated,
)
from rest_framework.request import Request
from rest_framework.response import Response

from .models import (
    Benefit,
    Category,
    Invoice,
    Meter,
    MeterReading,
    Property,
    Provider,
    ServiceAccount,
    ServiceAccountRecalculation,
    Tariff, Payment,
)
from .serializers import (
    BenefitSerializer,
    CategorySerializer,
    CreateRecalculationSerializer,
    InvoiceSerializer,
    MeterReadingSerializer,
    MeterSerializer,
    PropertySerializer,
    ProviderSerializer,
    ServiceAccountSerializer,
    TariffSerializer,
    TopUpSerializer,
    UpdateTariffRateSerializer, PaymentSerializer,
)
from .services import BillingService


class PropertyViewSet(viewsets.ModelViewSet):
    """ViewSet для керування нерухомістю (Адресами)"""

    serializer_class = PropertySerializer
    # Закриваємо ендпоінт — доступ тільки для авторизованих користувачів
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):  # type: ignore
        """Фішка безпеки: повертаємо ТІЛЬКИ нерухомість поточного користувача"""
        return Property.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        """Автоматично прив'язуємо створену нерухомість до поточного юзера"""
        serializer.save(user=self.request.user)


class ProviderViewSet(viewsets.ModelViewSet):
    """ViewSet для перегляду та додавання провайдерів (наприклад: Обленерго)"""

    # Списком провайдерів можуть користуватися всі авторизовані юзери
    queryset = Provider.objects.all()
    serializer_class = ProviderSerializer
    permission_classes = (IsAuthenticated,)


class ServiceAccountViewSet(viewsets.ModelViewSet):
    """ViewSet для керування особовими рахунками"""

    serializer_class = ServiceAccountSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):  # type: ignore
        """Повертаємо тільки ті рахунки, які належать нерухомості ПОТОЧНОГО користувача"""
        self.request: Request
        # 1. Беремо базовий кверісет (тільки рахунки поточного користувача для безпеки)
        queryset = ServiceAccount.objects.filter(property__user=self.request.user)

        # 2. Перевіряємо, чи передав користувач в URL параметр ?property_id=...
        property_id = self.request.query_params.get("property_id")

        if property_id is not None:
            # 3. Якщо передав — додатково фільтруємо список за цією нерухомістю
            queryset = queryset.filter(property_id=property_id)

        return queryset

    def get_serializer_class(self):  # type: ignore
        """Динамічно підміняє серіалізатори для створення ідеальних HTML-форм"""
        if self.action == "top_up":
            return TopUpSerializer
        elif self.action == "recalculate":
            return CreateRecalculationSerializer  # Намалює форму для перерахунку
        return super().get_serializer_class()

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAuthenticated],
        url_path="top-up",
    )
    def top_up(self, request, pk=None):
        """Поповнення особового рахунка
        POST /api/service-accounts/{id}/top-up/"""
        account = self.get_object()
        amount_str = request.data.get("amount")

        if not amount_str:
            return Response(
                {"error": "Поле amount обов'язкове"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            amount = Decimal(str(amount_str))
            if amount <= 0:
                return Response(
                    {"error": "Сума поповнення повинна бути більшою за нуль"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with transaction.atomic():
                # 1. Зараховуємо гроші на загальний баланс рахунку
                account.balance += amount
                account.save()

                # === НОВИЙ РЯДОК: ФІКСУЄМО ПЛАТІЖ В ІСТОРІЇ ===
                from .models import Payment

                Payment.objects.create(account=account, amount=amount)

                # 2. Розподіляємо суму по квитанціях
                from .services import PaymentService

                PaymentService.distribute_payment(account, amount)

            # Перезавантажуємо об'єкт з бази, щоб побачити фінальний залишок на балансі
            account.refresh_from_db()

            return Response(
                {
                    "status": "success",
                    "message": "Кошти успішно внесені. Система автоматично перерозподілила їх на закриття заборгованостей.",
                    "remaining_balance": str(account.balance),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:  # noqa: BLE001
            return Response(
                {"error": f"Помилка обробки платежу: {e!s}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # === ЕНДПОІНТ ДЛЯ СТВОРЕННЯ ПЕРЕРАХУНКУ ===
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAdminUser],  # Тільки для адмінів/менеджерів
        url_path="recalculate",
    )
    def recalculate(self, request, pk=None):
        """
        POST /api/accounts/{id}/recalculate/
        Створює запис корекції, який автоматично спишеться/донарахується в найближчому білінгу.
        """
        account = self.get_object()

        # Валідуємо отримані дані з форми
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Створюємо запис у черзі перерахунків
        recalculation = ServiceAccountRecalculation.objects.create(
            account=account,
            amount=serializer.validated_data["amount"],
            description=serializer.validated_data["description"],
            billing_period=serializer.validated_data["billing_period"],
            is_applied=False,  # буде використано при наступному запуску generate_invoices
        )

        return Response(
            {
                "status": "success",
                "message": f"Перерахунок на суму {recalculation.amount} грн успішно додано в чергу за період {recalculation.billing_period}.",
                "id": recalculation.id,
            },
            status=status.HTTP_201_CREATED,
        )


class MeterViewSet(viewsets.ModelViewSet):
    """ViewSet для лічильників, прив'язаних до нерухомості (квартири)"""

    serializer_class = MeterSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):  # type: ignore
        user = self.request.user
        # Починаємо з вибірки лічильників нерухомості поточного юзера
        queryset = (
            Meter.objects.all().select_related("property").prefetch_related("readings")
        )

        # Додаємо фільтрацію за конкретною квартирою, якщо вона передана в URL
        property_id = self.request.query_params.get("property_id")  # type: ignore
        if property_id is not None:
            # Перевіряємо, чи належить ця квартира поточному користувачу (захист безпеки)
            if not Property.objects.filter(id=property_id, user=user).exists():
                raise ValidationError(
                    {
                        "property_id": "Цей об'єкт нерухомості не належить вашому акаунту."
                    }
                )
            queryset = queryset.filter(property_id=property_id)

        return queryset

    def perform_create(self, serializer):
        """Захист при POST-запиті: перевіряємо, чи створює юзер лічильник у своїй квартирі"""
        target_property = serializer.validated_data["property"]
        if target_property.user != self.request.user:
            raise ValidationError(
                {"property": "Ви не можете додавати лічильники до чужої нерухомості."}
            )
        serializer.save()


class MeterReadingViewSet(viewsets.ModelViewSet):
    """ViewSet для показників лічильників"""

    serializer_class = MeterReadingSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):  # type: ignore
        """Повертаємо показники тільки тих лічильників, які належать користувачу"""
        self.request: Request
        queryset = MeterReading.objects.filter(meter__property__user=self.request.user)

        # Твій улюблений фільтр через URL: ?meter_id=...
        meter_id = self.request.query_params.get("meter_id")
        if meter_id is not None:
            queryset = queryset.filter(meter_id=meter_id)

        return queryset


class CategoryViewSet(viewsets.ModelViewSet):
    """Перегляд та створення категорій комунальних послуг"""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = (IsAuthenticated,)


class TariffViewSet(viewsets.ModelViewSet):
    """Перегляд та створення тарифів з можливістю фільтрації за об'єктом нерухомості"""

    serializer_class = TariffSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):  # type: ignore
        user = self.request.user

        # Базовий QuerySet: тільки тарифи особових рахунків поточного користувача
        # Оптимізуємо запити через select_related для мінімізації навантаження на БД (проблема N+1)
        queryset = (
            Tariff.objects.filter(account__property__user=user)
            .select_related("account__property", "meter")
            .distinct()
        )

        # Додаємо фільтрацію за конкретною нерухомістю (квартирою), якщо вона передана в URL
        property_id = self.request.query_params.get("property_id")  # type: ignore
        if property_id is not None:
            # Захист безпеки: перевіряємо, чи належить ця квартира поточному користувачу
            if not Property.objects.filter(id=property_id, user=user).exists():
                raise ValidationError(
                    {
                        "property_id": "Цей об'єкт нерухомості не належить вашому акаунту."
                    }
                )

            # Фільтруємо тарифи через ланцюжок зв'язків: Тариф -> Особовий рахунок -> Нерухомість
            queryset = queryset.filter(account__property_id=property_id)

        return queryset

    @action(detail=True, methods=["post"], url_path="update-rate")
    def update_rate(self, request, pk=None):
        """
        POST /api/tariffs/{id}/update-rate/
        Body JSON: {"new_price": "45.00", "effective_from": "2026-10-01"} [Context]
        Закриває поточний тариф і створює новий з новою ціною в рамках однієї транзакції.
        """
        old_tariff = self.get_object()
        serializer = UpdateTariffRateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        new_price = serializer.validated_data["new_price"]
        start_date = serializer.validated_data["effective_from"]

        # Дата закінчення старого тарифу — це строго за 1 день до старту нового
        end_date_for_old = start_date - timedelta(days=1)

        if end_date_for_old < old_tariff.effective_from:
            return Response(
                {
                    "error": "Дата старту нового тарифу не може бути меншою за дату початку старого тарифу."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # 1. Обмежуємо час дії старого тарифу
            old_tariff.effective_to = end_date_for_old
            old_tariff.save()

            # 2. Клонуємо тариф із новою ціною та новим періодом
            new_tariff = Tariff.objects.create(
                account=old_tariff.account,
                meter=old_tariff.meter,
                name=f"{old_tariff.name}",
                charge_type=old_tariff.charge_type,
                price_per_unit=new_price,
                effective_from=start_date,
                effective_to=None,  # безстроковий, поки знову не зміниться
            )

        return Response(
            {
                "status": "success",
                "message": f"Тариф успішно оновлено. Стара ціна діє до {old_tariff.effective_to}. Нова ціна діє з {new_tariff.effective_from}.",
                "old_tariff_id": old_tariff.id,
                "new_tariff_id": new_tariff.id,
            },
            status=status.HTTP_200_OK,
        )


class BenefitViewSet(viewsets.ModelViewSet):
    """Керування пільгами конкретного користувача"""

    serializer_class = BenefitSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):  # type: ignore
        """Користувач бачить тільки СВОЇ пільги"""
        return Benefit.objects.filter(account__property__user=self.request.user)


class InvoiceViewSet(viewsets.ModelViewSet):
    """API ViewSet для перегляду та видалення квитанцій."""

    serializer_class = InvoiceSerializer
    permission_classes = (IsAuthenticated,)
    filter_backends = (DjangoFilterBackend,)

    def get_queryset(self):  # type: ignore
        if self.request.user.is_staff:
            queryset = Invoice.objects.all()
        else:
            queryset = Invoice.objects.filter(account__property__user=self.request.user)

        # Обов'язково додаємо .order_by("-id") або "-created_at",
        # щоб "останні" інвойси завжди йшли першими у списку
        queryset = (
            queryset.select_related("account__provider")
            .prefetch_related("items__tariff__meter")
            .order_by("-billing_period")
            .distinct()
        )

        statuses = self.request.query_params.getlist("status")  # type: ignore
        account_id = self.request.query_params.get("account_id")  # type: ignore

        filters = Q()
        if account_id:
            filters &= Q(account_id=account_id)
        if statuses:
            filters &= Q(status__in=statuses)

        return queryset.filter(filters)

    def filter_queryset(self, queryset):
        """Додаткове обмеження кількості після застосування всіх фільтрів."""
        queryset = super().filter_queryset(queryset)

        # Перевіряємо, чи передано параметр для отримання останніх 12 записів
        # Наприклад: /api/invoices/?limit_twelve=true
        limit_twelve = self.request.query_params.get("limit_twelve")  # type: ignore

        if limit_twelve == "true":
            # Беремо ID перших 12 елементів (оскільки сортування від нових до старих вже є)
            # Використовуємо подвійний запит через values_list, щоб обійти обмеження distinct() + slice
            top_12_ids = queryset.values_list("id", flat=True)[:12]
            queryset = queryset.filter(id__in=list(top_12_ids))

        return queryset

    # 2. НОВИЙ МЕТОД ДЛЯ ВИДАЛЕННЯ (DELETE /api/invoices/{id}/)
    def destroy(self, request, *args, **kwargs):
        """Видаляє інвойс та повертає його вартість на баланс особового рахунку."""
        # Перевірка прав: видаляти інвойси мають право лише адміни
        # if not request.user.is_staff:
        #     return Response(
        #         {"detail": "У вас немає прав для видалення фінансових документів."},
        #         status=status.HTTP_403_FORBIDDEN,
        #     )

        instance = self.get_object()

        self.perform_destroy(instance)  # Сигнал підхопить це видалення автоматично!
        return Response(status=status.HTTP_204_NO_CONTENT)

    # === ЕНДПОІНТ ДЛЯ ГЕНЕРАЦІЇ ВСІХ РАХУНКІВ ===
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAdminUser],
        url_path="generate",
    )
    def generate(self, request):
        """POST /api/invoices/generate/"""
        try:
            BillingService.generate_monthly_invoices()
            return Response(
                {
                    "status": "success",
                    "message": "Рахунки успішно згенеровано за всі пропущені періоди.",
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {"status": "error", "message": f"Помилка під час генерації: {e!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # === НОВИЙ ЕНДПОІНТ ДЛЯ ПЕРЕФОРМУВАННЯ ОДНОГО РАХУНКУ ===
    @action(
        detail=True,  # True додає {id} в URL: /api/invoices/{id}/regenerate/
        methods=["post"],
        permission_classes=[IsAdminUser],
        url_path="regenerate",
    )
    def regenerate(self, request, pk=None):
        """
        POST /api/invoices/{id}/regenerate/
        Видаляє поточний інвойс і наново перераховує його для цього ж особового рахунку та періоду.
        """
        try:
            # 1. Отримуємо поточний інвойс
            current_invoice = self.get_object()
            account = current_invoice.account
            billing_period = current_invoice.billing_period

            # Огортаємо видалення та створення в одну транзакцію
            with transaction.atomic():
                # 2. Видаляємо інвойс.
                current_invoice.delete()
                account.refresh_from_db()

                # 3. Запускаємо генерацію інвойсу ТІЛЬКИ для цього рахунку за той самий період
                BillingService._process_single_invoice(account, billing_period)

            return Response(
                {
                    "status": "success",
                    "message": f"Рахунок за період {billing_period} успішно переформовано.",
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {
                    "status": "error",
                    "message": f"Помилка під час переформування: {e!s}",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PaymentFilter(filters.FilterSet):
    property_id = filters.NumberFilter(field_name="account__property_id")
    account_id = filters.NumberFilter(field_name="account_id")#можна і не вказувати,

    class Meta:
        model = Payment
        fields = ['account_id', 'property_id']


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PaymentSerializer
    filter_backends = (DjangoFilterBackend,)  # вмикаємо фільтр по параметрам в url
    filterset_class = PaymentFilter  # вказуємо перелік параметрів в url ".../payments/?account_id=1
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        queryset = Payment.objects.filter(account__property__user=self.request.user)
        return queryset
