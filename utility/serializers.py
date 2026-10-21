from datetime import date, timedelta

from rest_framework import serializers

from .models import (
    Benefit,
    Category,
    Invoice,
    InvoiceItem,
    Meter,
    MeterReading,
    Property,
    Provider,
    ServiceAccount,
    Tariff,
)


class PropertySerializer(serializers.ModelSerializer):
    """Серіалізатор для об'єктів нерухомості (Адрес)"""

    class Meta:
        model = Property
        fields = ("id", "user", "address", "name")
        # Поле user ми робимо read_only, щоб користувач не міг підставити
        # чужий ID. Його автоматично підставить поточного юзера у View.
        read_only_fields = ("id", "user")


class MeterReadingSerializer(serializers.ModelSerializer):
    """Серіалізатор для показників лічильників"""

    # Вираховані property-поля з моделі, які ми віддаємо клієнту
    consumption = serializers.ReadOnlyField()

    class Meta:
        model = MeterReading
        fields = (
            "id",
            "meter",
            "reading_value",
            "reading_date",
            "is_estimated",
            "consumption",
        )
        read_only_fields = ("id", "consumption")

    def validate(self, data):  # type: ignore
        """Бізнес-валідація: перевіряємо, щоб нові показники не були меншими за попередні"""
        meter = data["meter"]
        reading_value = data["reading_value"]
        reading_date = data["reading_date"]

        # Шукаємо останній за часом запис для цього лічильника ДО поточної дати
        last_reading = (
            MeterReading.objects.filter(meter=meter, reading_date__lt=reading_date)
            .order_by("-reading_date")
            .first()
        )

        if last_reading and reading_value < last_reading.reading_value:
            raise serializers.ValidationError(
                {
                    "reading_value": f"Нові показники ({reading_value}) не можуть бути меншими за попередні ({last_reading.reading_value})."
                }
            )

        return data


class CategorySerializer(serializers.ModelSerializer):
    """Серіалізатор для категорій послуг (напр. Газ, Світло)"""

    class Meta:
        model = Category
        fields = ("id", "name")


class BenefitSerializer(serializers.ModelSerializer):
    """Серіалізатор для пільг та субсидій"""

    benefit_type_display = serializers.CharField(
        source="get_benefit_type_display", read_only=True
    )

    class Meta:
        model = Benefit
        fields = (
            "id",
            "account",
            "name",
            "benefit_type",
            "benefit_type_display",
            "discount_value",
            "effective_from",
            "effective_to",
            "is_active",
        )
        read_only_fields = "id"

    def __init__(self, *args, **kwargs):
        """Захищаємо випадаючий список рахунків: показуємо тільки рахунки поточного юзера"""
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated:
            from .models import ServiceAccount

            self.fields["account"].queryset = ServiceAccount.objects.filter(
                property__user=request.user
            )


class ProviderSerializer(serializers.ModelSerializer):
    """Серіалізатор для комунальних провайдерів (напр. Обленерго)"""

    # Поле для читання (GET) — повертає повний JSON категорії
    category_details = CategorySerializer(source="category", read_only=True)

    # Поле для запису (POST/PUT) — приймає тільки число (ID категорії)
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all())

    class Meta:
        model = Provider
        fields = (
            "id",
            "name",
            "edrpou",
            "contact_info",
            "category",  # Для відправки клієнтом (POST)
            "category_details",  # Для отримання клієнтом (GET)
        )
        read_only_fields = ("id",)


class ServiceAccountSerializer(serializers.ModelSerializer):
    """Серіалізатор для особових рахунків"""

    # Для виведення (GET) використовуємо серіалізатори об'єктів.
    # Зверніть увагу на зміну імен: тепер вони не конфліктують із полями запису.
    property_details = PropertySerializer(source="property", read_only=True)
    provider_details = ProviderSerializer(source="provider", read_only=True)

    # Для запису (POST/PUT) перевизначаємо стандартні поля зв'язку,
    # щоб вони приймали ID (integer), але називаємо їх ТАК САМО, як у моделі!
    property = serializers.PrimaryKeyRelatedField(queryset=Property.objects.all())
    provider = serializers.PrimaryKeyRelatedField(queryset=Provider.objects.all())

    class Meta:
        model = ServiceAccount
        fields = (
            "id",
            "account_number",
            "created_at",
            "property",  # сюди передаємо ID при POST
            "provider",  # сюди передаємо ID при POST
            "property_details",  # звідси отримуємо об'єкт при GET
            "provider_details",  # звідси отримуємо об'єкт при GET
            "balance",
        )
        read_only_fields = ("id", "created_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")

        # Тепер фільтрація спрацює ідеально, бо "property" — це PrimaryKeyRelatedField
        if (
            request
            and request.user
            and request.user.is_authenticated
            and "property" in self.fields
        ):
            self.fields["property"].queryset = Property.objects.filter(
                user=request.user
            )


class MeterSerializer(serializers.ModelSerializer):
    """Серіалізатор для лічильників з динамічною валідацією нерухомості"""

    # Додаємо відображення назви та адреси квартири для GET запитів
    property_name = serializers.CharField(source="property.name", read_only=True)
    property_address = serializers.CharField(source="property.address", read_only=True)
    readings = MeterReadingSerializer(many=True, read_only=True)

    class Meta:
        model = Meter
        fields = (
            "id",
            "name",
            "unit_of_measure",
            "property",  # Поле для POST/PUT зв'язку з квартирою
            "property_name",
            "property_address",
            "readings",
        )
        read_only_fields = ("id",)

    def __init__(self, *args, **kwargs):
        """Захищаємо випадаючий список: дозволяємо обирати ТІЛЬКИ квартири поточного юзера"""
        super().__init__(*args, **kwargs)
        request = self.context.get("request")

        # Обмежуємо список квартир у формі створення
        if (
            request
            and request.user
            and request.user.is_authenticated
            and "property" in self.fields
        ):
            self.fields["property"].queryset = Property.objects.filter(
                user=request.user
            )


class TariffSerializer(serializers.ModelSerializer):
    """Серіалізатор для тарифів особових рахунків із захистом списків на рівні нерухомості"""

    # Текстове відображення типу нарахування замість системного коду
    charge_type_display = serializers.CharField(
        source="get_charge_type_display", read_only=True
    )

    # Деталі особового рахунку для виведення клієнту (GET)
    account_details = ServiceAccountSerializer(source="account", read_only=True)

    # Деталі лічильника для виведення клієнту (GET)
    meter_details = MeterSerializer(source="meter", read_only=True)

    # Чистий ID рахунку для створення тарифу (POST/PUT)
    account = serializers.PrimaryKeyRelatedField(queryset=ServiceAccount.objects.all())

    # Чистий ID лічильника для створення тарифу (POST/PUT), може бути необов'язковим (null=True)
    meter = serializers.PrimaryKeyRelatedField(
        queryset=Meter.objects.all(), required=False, allow_null=True
    )

    is_active = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Tariff
        fields = (
            "id",
            "account",  # Для запису (POST)
            "account_details",  # Для читання (GET)
            "meter",  # Для запису (POST)
            "meter_details",  # Для читання (GET)
            "name",
            "charge_type",
            "charge_type_display",  # Для читання (GET)
            "price_per_unit",
            "effective_from",
            "effective_to",
            "is_active",
        )
        read_only_fields = ("id", "effective_to")

    def __init__(self, *args, **kwargs):
        """Динамічно обмежуємо списки: показуємо рахунки та лічильники ТІЛЬКИ поточної нерухомості юзера"""
        super().__init__(*args, **kwargs)
        request = self.context.get("request")

        if request and request.user and request.user.is_authenticated:
            user = request.user

            # 1. Захист випадаючого списку особових рахунків
            if "account" in self.fields:
                self.fields["account"].queryset = ServiceAccount.objects.filter(
                    property__user=user
                )

            # 2. НОВИЙ ЗАХИСТ: Дозволяємо обирати лічильники ТІЛЬКИ тієї нерухомості, яка належить користувачу
            if "meter" in self.fields:
                self.fields["meter"].queryset = Meter.objects.filter(
                    property__user=user
                )

    def get_is_active(self, obj: Tariff):
        today = date.today()
        start_cur_month = today.replace(day=1)

        end_cur_month = (start_cur_month + timedelta(days=32)).replace(
            day=1
        ) - timedelta(days=1)

        if obj.charge_type == "one_time":
            return start_cur_month <= obj.effective_from <= end_cur_month
        if obj.effective_to is None:
            return obj.effective_from <= end_cur_month
        else:
            return start_cur_month <= obj.effective_to <= end_cur_month


class InvoiceItemSerializer(serializers.ModelSerializer):
    tariff_name = serializers.SerializerMethodField()
    unit_of_measure = serializers.CharField(
        source="tariff.meter.unit_of_measure", read_only=True, default=None
    )

    class Meta:
        model = InvoiceItem
        fields = (
            "id",
            "tariff_name",
            "unit_of_measure",
            "previous_reading",
            "current_reading",
            "consumption",
            "tariff_price_at_billing",
            "base_amount",
            "benefit_discount",
        )

    def get_tariff_name(self, obj):
        # Якщо це рядок перерахунку (у якого немає тарифу) — беремо текст із поля comment
        if obj.tariff is None:
            return obj.comment or "Перерахунок суми"
        # Для звичайних послуг повертаємо назву тарифу
        return obj.tariff.name


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)
    provider_name = serializers.CharField(
        source="account.provider.name", read_only=True, default="Комунальна служба"
    )
    account_number = serializers.CharField(
        source="account.account_number", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    billing_period_display = serializers.SerializerMethodField()

    # НОВЕ ПОЛЕ: Виводить красиву назву пільги (наприклад, "УБД (75%)" або "Субсидія")
    benefit_name = serializers.CharField(
        source="benefit.name", read_only=True, default=None
    )

    class Meta:
        model = Invoice
        fields = (
            "id",
            "provider_name",
            "account_number",
            "billing_period",
            "billing_period_display",
            "benefit_name",  # <--- ДОДАНО В JSON
            "total_base_amount",
            "total_benefit_discount",
            "previous_balance",
            "amount_due",
            "amount_paid",
            "status",
            "status_display",
            "due_date",
            "items",
        )

    def get_billing_period_display(self, obj):
        ukrainian_months = {
            1: "Січень",
            2: "Лютий",
            3: "Березень",
            4: "Квітень",
            5: "Травень",
            6: "Червень",
            7: "Липень",
            8: "Серпень",
            9: "Вересень",
            10: "Жовтень",
            11: "Листопад",
            12: "Грудень",
        }
        month_name = ukrainian_months.get(obj.billing_period.month)
        return f"{month_name} {obj.billing_period.year}"


class TopUpSerializer(serializers.Serializer):
    """Серіалізатор виключно для валідації форми поповнення рахунку"""

    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0.01, label="Сума поповнення (грн)"
    )


class CreateRecalculationSerializer(serializers.Serializer):
    """Серіалізатор для створення перерахунку/корекції через API"""

    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        label="Сума корекції (грн)",
        help_text="Може бути від'ємною для списання (наприклад, -150.00) або додатною",
    )
    description = serializers.CharField(
        max_length=255,
        label="Опис/Причина перерахунку",
        style={
            "placeholder": "Наприклад: Корекція суми згідно з актом повірки лічильника"
        },
    )
    billing_period = serializers.DateField(
        label="Цільовий період (місяць)",
        help_text="Оберіть перше число місяця, в рахунок якого має лягти ця корекція (наприклад, 2026-10-01)",
    )


class UpdateTariffRateSerializer(serializers.Serializer):
    """Серіалізатор для планової зміни вартості тарифу"""

    new_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, label="Нова ціна за одиницю"
    )
    effective_from = serializers.DateField(label="Дата, з якої діє нова ціна")
