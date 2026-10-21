from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import format_html

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
    ServiceAccountRecalculation,
    Tariff,
)
from .services import BillingService  # Сервіс для генерації рахунків

admin.site.register(
    (
        Property,
        Provider,
        Benefit,
        Category,
        ServiceAccountRecalculation,
    )
)
from django.contrib import admin

from .models import Payment


@admin.register(Meter)
class MeterAdmin(admin.ModelAdmin):
    list_display = ("name", "unit_of_measure", "property")
    list_filter = ("property",)
    ordering = ("property",)


# 1. Створюємо Inline-таблицю для відображення всередині картки рахунку
class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 1


# 3. Реєструємо платежі окремим загальним списком (для зручного глобального пошуку по касі)
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("account", "amount", "created_at")
    list_filter = ("created_at", "account__provider")
    search_fields = ("account__account_number",)


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = (
        "meter",
        "reading_value",
        "reading_date",
        "is_estimated",
    )
    ordering = (
        "meter",
        "-reading_date",
    )
    list_filter = ("meter", "reading_date", "is_estimated")


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "account",
        "meter",
        "charge_type",
        "price_per_unit",
        "effective_from",
        "effective_to",
    )
    ordering = ("charge_type",)


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0  # Прибирає порожні додаткові рядки для створення вручну
    # Робимо поля рядків доступними лише для читання в адмінці, щоб менеджер випадково не зламав розрахунки
    readonly_fields = (
        "tariff",
        "previous_reading",
        "current_reading",
        "consumption_formatted",  # змінено тут
        "tariff_price_at_billing",
        "base_amount",
        "benefit_discount",
    )

    # 2. Обов'язково додаємо fields, щоб Django знав, у якому порядку виводити стовпчики в таблиці
    fields = readonly_fields

    # 3. Сам метод з унікальним ім'ям та явним описом стовпчика
    @admin.display(description="Споживання")
    def consumption_formatted(self, obj):
        val = obj.consumption or 0
        return f"{val:.2f}"


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "account",
        "billing_period",
        "amount_due_formatted",
        "amount_paid_formatted",
        "status",
    )
    list_filter = ("status", "billing_period", "account", "account__property")
    search_fields = ("account__account_number",)

    # 1. Залишаємо кнопку в списку полів лише для читання
    readonly_fields = ("regenerate_button",)

    # 2. Повертаємо ваш оригінальний inline (розкоментуйте його!)
    inlines = (InvoiceItemInline,)

    # ПРИБРАЛИ КОРТЕЖ fields = (...), щоб Django виводив усі поля автоматично разом із вбудованими inlines!

    @admin.display(description="Всього до сплати", ordering="amount_due")
    def amount_due_formatted(self, obj):
        val = obj.amount_due or 0
        return f"{val:.2f}"

    @admin.display(description="Фактично сплачено", ordering="amount_paid")
    def amount_paid_formatted(self, obj):
        val = obj.amount_paid or 0
        return f"{val:.2f}"

    # 3. HTML-код кнопки (вона автоматично з'явиться в самому низу стандартних полів форми)
    @admin.display(description="Дії з рахунком")
    def regenerate_button(self, obj):
        if not obj.pk:
            return "-"

        app_label = obj._meta.app_label
        model_name = obj._meta.model_name
        url = f"/admin/{app_label}/{model_name}/{obj.pk}/regenerate/"

        return format_html(
            '<a class="button" style="background: #264b5c; color: white; padding: 10px 15px; '
            'font-weight: bold; border-radius: 4px; text-decoration: none;" '
            'href="{}">🔄 Переформувати рахунок на оплату</a>',
            url,
        )

    # 4. Реєстрація кастомного URL
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/regenerate/",
                self.admin_site.admin_view(self.regenerate_invoice_view),
                name="invoice-regenerate",
            ),
        ]
        return custom_urls + urls

    # 5. Логіка переформування
    def regenerate_invoice_view(self, request, object_id):
        current_invoice = get_object_or_404(Invoice, pk=object_id)
        account = current_invoice.account
        billing_period = current_invoice.billing_period

        try:
            with transaction.atomic():
                current_invoice.delete()
                account.refresh_from_db()
                BillingService._process_single_invoice(account, billing_period)

            self.message_user(
                request,
                f"Рахунок для {account} за період {billing_period} успішно переформовано.",
                messages.SUCCESS,
            )
        except Exception as e:  # noqa: BLE001
            self.message_user(
                request,
                f"Помилка під час переформування рахунку: {e!s}",
                messages.ERROR,
            )

        return HttpResponseRedirect("../../")

    # Підрахунок суми вгорі сторінки
    def changelist_view(self, request, extra_context=None):
        cl = self.get_changelist_instance(request)
        queryset = cl.get_queryset(request)
        totals = queryset.aggregate(due=Sum("amount_due"), paid=Sum("amount_paid"))
        sum_due = totals["due"] or 0
        sum_paid = totals["paid"] or 0

        self.message_user(
            request,
            f"Підсумок сторінки — Всього до сплати: {sum_due:.2f} | Фактично сплачено: {sum_paid:.2f}",
        )
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(ServiceAccount)
class ServiceAccountAdmin(admin.ModelAdmin):
    # 1. Налаштування відображення у списку таблиці
    list_display = ("account_number", "provider", "balance_display", "created_at")
    search_fields = ("account_number",)
    list_filter = ("provider", "created_at", "property")

    # 2. МАСОВА ДІЯ (Варіант 1): Генерація рахунків із випадаючого списку
    actions = ("trigger_mass_billing",)

    # Інтегруємо таблицю платежів всередину картки рахунку
    inlines = (PaymentInline,)

    @admin.action(description="Запустити генерацію рахунків за пропущені періоди")
    def trigger_mass_billing(self, request, queryset):
        """Виконується при виборі дії у списку особових рахунків"""
        try:
            BillingService.generate_monthly_invoices()
            self.message_user(
                request,
                "Рахунки успішно згенеровано за всі пропущені періоди для активних особових рахунків.",
                messages.SUCCESS,
            )
        except Exception as e:  # noqa: BLE001
            self.message_user(request, f"Помилка генерації: {e!s}", messages.ERROR)

    # 3. ТОЧКОВІ КНОПКИ (Варіант 2): Посилання на форми API всередині картки об'єкта
    readonly_fields = (
        "balance",
        "created_at",
        "balance_top_up_button",
        "create_recalculation_button",
    )

    # Організовуємо структуру полів на сторінці редагування рахунку
    fieldsets = (
        (
            "Основна інформація",
            {
                "fields": (
                    "account_number",
                    "provider",
                    "property",
                    "created_at",
                )  # <--- ДОДАНО СЮДИ
            },
        ),
        ("Фінансовий баланс", {"fields": ("balance", "balance_top_up_button")}),
        ("Коригування та перерахунки", {"fields": ("create_recalculation_button",)}),
    )

    def balance_display(self, obj):
        """Красиве відображення балансу в списку (зелений якщо плюс, червоний якщо борг)"""
        if obj.balance >= 0:
            return format_html(
                '<span style="color: green; font-weight: bold;">{} грн</span>',
                obj.balance,
            )
        return format_html(
            '<span style="color: red; font-weight: bold;">{} грн</span>', obj.balance
        )

    balance_display.short_description = "Поточний баланс"  # type: ignore

    def balance_top_up_button(self, obj):
        """Кнопка швидкого поповнення"""
        if obj.id:
            url = f"/api/accounts/{obj.id}/top-up/"
            return format_html(
                '<a class="button" href="{}" target="_blank" style="background-color: #417690; color: white; padding: 6px 15px; text-decoration: none; border-radius: 4px; font-weight: bold;">Внести кошти / Поповнити баланс ↗</a>',
                url,
            )
        return "-"

    balance_top_up_button.short_description = "Дія з балансом"  # type: ignore

    def create_recalculation_button(self, obj):
        """Кнопка створення перерахунку"""
        if obj.id:
            url = f"/api/accounts/{obj.id}/recalculate/"
            return format_html(
                '<a class="button" href="{}" target="_blank" style="background-color: #ba2121; color: white; padding: 6px 15px; text-decoration: none; border-radius: 4px; font-weight: bold;">Створити перерахунок (Корекцію суми) ↗</a>',
                url,
            )
        return "-"

    create_recalculation_button.short_description = "Дія з тарифом"  # type: ignore
