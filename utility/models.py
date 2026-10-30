import datetime
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Property(models.Model):
    """Об'єкти нерухомості (Адреси користувача)"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="properties"
    )
    address = models.TextField()
    name = models.CharField(max_length=100, default="Моя квартира")

    class Meta:
        ordering = ("name",)
        verbose_name = "Об'єкт нерухомості"
        verbose_name_plural = "Об'єкти нерухомості"
        # Додаємо обмеження унікальності
        constraints = (
            models.UniqueConstraint(
                fields=["name", "address"], name="unique_name_address"
            ),
        )

    def __str__(self):
        return f"{self.name} — {self.address[:30]}"


class Provider(models.Model):
    """Провайдери комунальних послуг"""

    name = models.CharField(max_length=255)
    edrpou = models.CharField(max_length=10, unique=True, blank=True, null=True)
    contact_info = models.TextField(blank=True, null=True)

    category = models.ForeignKey(
        "Category",
        on_delete=models.PROTECT,
        related_name="providers",
        verbose_name="Категорія послуги",
    )

    class Meta:
        ordering = ("name",)
        verbose_name = "Постачальник послуг"
        verbose_name_plural = "Постачальники послуг"

    def __str__(self):
        return f"{self.name} ({self.category.name})"


class ServiceAccount(models.Model):
    """Особові рахунки"""

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="accounts"
    )
    provider = models.ForeignKey(
        Provider, on_delete=models.RESTRICT, related_name="accounts"
    )
    account_number = models.CharField(max_length=50)
    balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Поточний баланс (сальдо)",
    )
    created_at = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        verbose_name = "Особовий рахунок"
        verbose_name_plural = "Особові рахунки"
        unique_together = ("provider", "account_number")

    def __str__(self):
        return f"{self.account_number} ({self.provider.name})"


class Invoice(models.Model):
    """Головна квитанція (Один рахунок на весь особовий рахунок за місяць)"""

    account = models.ForeignKey(
        "ServiceAccount",
        on_delete=models.CASCADE,
        related_name="invoices",
        verbose_name="Особовий рахунок",
    )
    billing_period = models.DateField(
        verbose_name="Розрахунковий період"
    )  # 1-ше число місяця

    # Загальні суми, які агрегуються (плюсуються) з усіх рядків послуг
    total_base_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Нараховано всього за поточний місяць",
    )
    total_benefit_discount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Загальна знижка (пільги)",
    )
    previous_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Борг/Переплата з минулого місяця",
    )
    amount_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Всього до сплати (з урахуванням сальдо)",
    )
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Фактично сплачено",
    )

    benefit = models.ForeignKey(
        "Benefit",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="invoices",
        verbose_name="Застосована пільга",
    )

    class Status(models.TextChoices):
        UNPAID = "unpaid", "Неоплачено"  # noqa E501
        PARTIALLY_PAID = "partially_paid", "Частково оплачено" # noqa E501
        PAID = "paid", "Оплачено" # noqa E501
        OVERDUE = "overdue", "Протерміновано" # noqa E501

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNPAID,
        verbose_name="Статус",
    )
    due_date = models.DateField(verbose_name="Граничний термін оплати")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-billing_period",)
        verbose_name = "Рахунок до сплати"
        verbose_name_plural = "Рахунки до сплати"

    def __str__(self):
        return f"Єдина квитанція за {self.billing_period.strftime('%m/%Y')} — {self.amount_due} грн"


class InvoiceItem(models.Model):
    """Деталізація рахунку (Конкретна послуга / рядок у квитанції)"""

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="items", verbose_name="Рахунок"
    )
    tariff = models.ForeignKey(
        "Tariff",
        on_delete=models.RESTRICT,
        related_name="invoice_items",
        verbose_name="Тариф",
        null=True,
        blank=True,
    )

    # Специфічні дані лічильника саме для ЦІЄЇ послуги
    previous_reading = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    current_reading = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    consumption = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0.00")
    )
    tariff_price_at_billing = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )

    # Фінанси конкретного рядка
    base_amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0.0)]
    )
    benefit_discount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    comment = models.CharField(
        max_length=255, blank=True, null=True, verbose_name="Коментар/Опис"
    )

    class Meta:
        verbose_name = "Рядок рахунку"
        verbose_name_plural = "Рядки рахунку"


class Payment(models.Model):
    """Історія поповнень та платежів особового рахунку"""

    account = models.ForeignKey(
        "ServiceAccount",
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Особовий рахунок",
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="Сума поповнення (грн)"
    )
    created_at = models.DateTimeField(
        default=datetime.datetime.today(),  # noqa: DTZ002
        verbose_name="Дата та час внесення",
    )

    class Meta:
        ordering = ("-created_at",)  # Сортуємо: нові платежі зверху
        verbose_name = "Платіж"
        verbose_name_plural = "Платежі"

    def __str__(self):
        return f"Платіж на {self.amount} грн від {self.created_at.strftime('%d.%m.%Y %H:%M')}"


class Meter(models.Model):
    name = models.CharField(max_length=150)

    unit_of_measure = models.CharField(
        max_length=20, blank=True, null=True
    )  # м³, кВт-год тощо

    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="meters"
    )

    class Meta:
        ordering = ("name",)
        verbose_name = "Лічильник"
        verbose_name_plural = "Лічильники"

    def __str__(self):
        return f"{self.name} ({self.unit_of_measure or 'без виміру'})"


class MeterReading(models.Model):
    """Показники лічильників"""

    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name="readings")
    reading_value = models.DecimalField(max_digits=12, decimal_places=3)
    reading_date = models.DateField()
    is_estimated = models.BooleanField(
        default=False
    )  # чи показник розрахунковий (внесений системою)

    class Meta:
        ordering = ("-reading_date",)
        verbose_name = "Показники лічильників"
        verbose_name_plural = "Показники лічильників"
        unique_together = ("meter", "reading_date")

    @property
    def consumption(self):
        """Динамічно вираховує споживання порівняно з попереднім записом"""
        # Шукаємо в базі попередній запис для цього ж лічильника, який був створений ДО поточної дати
        previous_reading = (
            MeterReading.objects.filter(
                meter=self.meter, reading_date__lt=self.reading_date
            )
            .order_by("-reading_date")
            .first()
        )

        # Якщо попередній запис знайшли — віднімаємо від поточного значення попереднє
        if previous_reading:
            diff = self.reading_value - previous_reading.reading_value
            return max(0, diff)

        # Якщо це самий перший запис для цього лічильника, споживання вважаємо рівним 0
        return 0

    def __str__(self):
        return f"{self.meter} від {self.reading_date}: {self.reading_value}"


class Category(models.Model):
    """Категорії послуг (наприклад: Газ, Водопостачання, Електроенергія)"""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Категорія послуг"
        verbose_name_plural = "Категорії послуг"

    def __str__(self):
        return self.name


class Tariff(models.Model):
    """Тарифи (Тепер прив'язані до конкретного особового рахунку)"""

    # НОВИЙ ЗВ'ЯЗОК: Замість provider посилаємося на ServiceAccount
    account = models.ForeignKey(
        "ServiceAccount",
        on_delete=models.CASCADE,
        related_name="tariffs",
        verbose_name="Особовий рахунок",
    )
    meter = models.ForeignKey(
        "Meter",
        on_delete=models.RESTRICT,
        related_name="tariffs",
        verbose_name="Лічильник",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=150)

    CHARGE_TYPE_CHOICES = (
        ("fixed_monthly", "Фіксована місячна плата"),
        ("by_meter", "За лічильником"),
        ("one_time", "Одноразовий платіж"),
    )
    charge_type = models.CharField(max_length=50, choices=CHARGE_TYPE_CHOICES)
    price_per_unit = models.DecimalField(
        max_digits=10, decimal_places=5, validators=[MinValueValidator(0.0)]
    )
    effective_from = models.DateField()
    effective_to = models.DateField(blank=True, null=True)

    def __str__(self):
        return f"{self.name}: {self.price_per_unit} грн ({self.account.account_number})"


class Benefit(models.Model):
    """Пільги та Субсидії (Нараховуються на конкретний особовий рахунок)"""

    account = models.ForeignKey(
        "ServiceAccount", on_delete=models.CASCADE, related_name="benefits"
    )
    name = models.CharField(max_length=150)

    BENEFIT_TYPE_CHOICES = (
        ("percentage", "Відсоток знижки"),
        ("fixed_amount", "Фіксована сума"),
    )
    benefit_type = models.CharField(max_length=50, choices=BENEFIT_TYPE_CHOICES)
    discount_value = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0.0)]
    )
    effective_from = models.DateField()
    effective_to = models.DateField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        suffix = "%" if self.benefit_type == "percentage" else " грн"
        return f"{self.name} ({self.discount_value}{suffix})"


class ServiceAccountRecalculation(models.Model):
    """Черга перерахунків (Чекають на включення в найближчий рахунок)"""

    account = models.ForeignKey(
        "ServiceAccount",
        on_delete=models.CASCADE,
        related_name="pending_recalculations",
    )
    description = models.CharField(
        max_length=255, verbose_name="Опис/Причина перерахунку"
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Сума корекції (може бути мінусовою)",
    )
    is_applied = models.BooleanField(
        default=False, verbose_name="Чи застосовано в рахунку?"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    billing_period = models.DateField(
        verbose_name="Цільовий розрахунковий період",
        help_text="Перше число місяця, в квитанцію якого піде цей перерахунок (наприклад, 2026-08-01)",
    )

    class Meta:
        verbose_name = "Перерахунок рахунку"
        verbose_name_plural = "Перерахунки рахунків"

    def __str__(self):
        return f"Перерахунок для {self.account}: {self.amount} грн ({self.description})"
