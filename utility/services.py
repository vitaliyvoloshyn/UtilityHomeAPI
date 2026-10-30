from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import (
    Benefit,
    Invoice,
    InvoiceItem,
    MeterReading,
    ServiceAccount,
    ServiceAccountRecalculation,
)


class BillingService:
    @classmethod
    @transaction.atomic
    def generate_monthly_invoices(cls):
        """
        Сканує систему, знаходить пропущені періоди для кожного особового рахунку
        і послідовно генерує всі відсутні рахунки, враховуючи активні пільги.
        """
        today = date.today()
        current_month_start = today.replace(day=1)
        accounts = ServiceAccount.objects.all()

        for account in accounts:
            last_invoice = (
                Invoice.objects.filter(account=account)
                .order_by("billing_period")
                .last()
            )

            if last_invoice:
                start_period = (
                    last_invoice.billing_period + timedelta(days=32)
                ).replace(day=1)
            else:
                SYSTEM_START_DATE = date(2026, 9, 1)
                start_period = SYSTEM_START_DATE

            while start_period < current_month_start:
                if Invoice.objects.filter(
                    account=account, billing_period=start_period
                ).exists():
                    start_period = (start_period + timedelta(days=32)).replace(day=1)
                    continue

                cls._process_single_invoice(account, start_period)
                start_period = (start_period + timedelta(days=32)).replace(day=1)

    @classmethod
    def _process_single_invoice(cls, account, billing_period):
        """Розраховує та створює один рахунок, враховуючи історичні періоди дії тарифів"""
        from django.db.models import Q

        # Робот бере ТІЛЬКИ ті тарифи, які були АКТИВНИМИ у звітній розрахунковий місяць (billing_period)
        tariffs = account.tariffs.filter(
            # Умова для постійних тарифів (діють за лічильником або фіксовано)
            Q(
                charge_type__in=["fixed_monthly", "by_meter"],
                effective_from__lte=billing_period,
            )
            & (Q(effective_to__gte=billing_period) | Q(effective_to__isnull=True))
            |
            # Умова для одноразових тарифів
            Q(
                charge_type="one_time",
                effective_from__month=billing_period.month,
                effective_from__year=billing_period.year,
            )
        )

        if not tariffs.exists():
            return

        due_date = (billing_period + timedelta(days=32)).replace(day=20)

        # 1. Пошук пільги
        active_benefit = (
            Benefit.objects.filter(
                account=account, is_active=True, effective_from__lte=billing_period
            )
            .filter(Q(effective_to__gte=billing_period) | Q(effective_to__isnull=True))
            .first()
        )

        total_base = Decimal("0.00")
        total_discount = Decimal("0.00")
        calculated_items = []

        # 2. Розрахунок поточних послуг
        for tariff in tariffs:
            prev_val = None
            curr_val = None
            consumption = Decimal("0.00")
            tariff_price = getattr(tariff, "price_per_unit", Decimal("0.00"))

            if tariff.meter:
                prev_reading_obj, curr_reading_obj = cls._get_period_readings(
                    tariff.meter, billing_period
                )
                if curr_reading_obj:
                    curr_val = Decimal(curr_reading_obj.reading_value)
                    if prev_reading_obj:
                        prev_val = Decimal(prev_reading_obj.reading_value)
                        consumption = max(Decimal("0.00"), curr_val - prev_val)

            base_amount = consumption * tariff_price if tariff.meter else tariff_price

            benefit_discount = Decimal("0.00")
            if active_benefit and active_benefit.benefit_type == "percentage":
                discount_pct = Decimal(active_benefit.discount_value) / 100
                benefit_discount = base_amount * discount_pct

            total_base += base_amount
            total_discount += benefit_discount

            calculated_items.append(
                {
                    "tariff": tariff,
                    "previous_reading": prev_val,
                    "current_reading": curr_val,
                    "consumption": consumption,
                    "tariff_price_at_billing": tariff_price,
                    "base_amount": base_amount,
                    "benefit_discount": benefit_discount,
                }
            )

        # 3. Ізольовані перерахунки
        pending_recalcs = ServiceAccountRecalculation.objects.filter(
            account=account, billing_period=billing_period, is_applied=False
        )
        recalculation_total = Decimal("0.00")
        recalc_items_to_create = []

        for recalc in pending_recalcs:
            recalculation_total += Decimal(recalc.amount)
            recalc_items_to_create.append(
                {
                    "description": f"Перерахунок: {recalc.description}",
                    "amount": Decimal(recalc.amount),
                }
            )
            recalc.is_applied = True
            recalc.save()

        if active_benefit and active_benefit.benefit_type == "fixed_amount":
            total_discount = Decimal(active_benefit.discount_value)
            total_discount = min(total_discount, total_base)

        # ВАЖЛИВО ДЛЯ ВАРІАНТУ 2:
        # Оскільки у нас єдиний баланс на рахунку, нам БІЛЬШЕ НЕ ПОТРІБНО вираховувати previous_balance через Sum('amount_due') минулих інвойсів!
        # Борг або переплата вже ПРИРОДНО зберігаються в account.balance.
        # Тому чиста сума цієї нової квитанції дорівнює:
        current_invoice_cost = (total_base - total_discount) + recalculation_total
        current_invoice_cost = max(Decimal("0.00"), current_invoice_cost)

        # =========================================================================
        # 4. АВТОМАТИЧНЕ СПИСАННЯ З ЄДИНОГО БАЛАНСУ (ВИПРАВЛЕНО ТА ОПТИМІЗОВАНО)
        # =========================================================================
        amount_paid = Decimal("0.00")

        # Перевіряємо, чи є на балансі гроші (аванс або кошти після видалення старих інвойсів)
        if account.balance > 0:
            if account.balance >= current_invoice_cost:
                # ВАРІАНТ А: Грошей більше або рівно вартості інвойсу -> закриваємо повністю
                amount_paid = current_invoice_cost
                account.balance -= (
                    current_invoice_cost  # Залишок авансу залишається на балансі
                )
            else:
                # ВАРІАНТ Б: Грошей менше, ніж коштує інвойс -> забираємо все, що є
                amount_paid = account.balance
                account.balance = Decimal(
                    "0.00"
                )  # Баланс обнуляється, бо все пішло на цей інвойс

        # ВАРІАНТ В: Якщо грошей не було зовсім (баланс <= 0) або авансу не вистачило:
        # Решту вартості інвойсу (або всю вартість) заганяємо в мінус особового рахунку (формуємо борг)
        if amount_paid < current_invoice_cost:
            shortfall = current_invoice_cost - amount_paid
            account.balance -= shortfall  # Баланс стає від'ємним (чистий борг)

        account.save()  # Фіксуємо оновлений баланс особового рахунку в базі даних!

        # Визначаємо статус квитанції на основі реальної суми списання з авансу
        if amount_paid == current_invoice_cost:
            invoice_status = Invoice.Status.PAID  # Оплачено повністю
        elif amount_paid > 0:
            invoice_status = Invoice.Status.PARTIALLY_PAID  # Оплачено частково
        else:
            invoice_status = Invoice.Status.UNPAID  # Неоплачено (чистий борг)

        # =========================================================================
        # 5. СТВОРЮЄМО ГОЛОВНИЙ РАХУНОК (Зберігаємо результат списання)
        # =========================================================================
        invoice = Invoice.objects.create(
            account=account,
            billing_period=billing_period,
            benefit=active_benefit,
            total_base_amount=total_base,
            total_benefit_discount=total_discount,
            previous_balance=Decimal("0.00"),  # Борг лежить суто в балансі рахунку
            amount_due=current_invoice_cost,  # Чиста вартість цієї квитанції
            amount_paid=amount_paid,  # Скільки грошей списано з балансу (авансу) в момент створення
            due_date=due_date,
            status=invoice_status,
        )

        # 6. Записуємо рядки послуг та перерахунків
        for item in calculated_items:
            InvoiceItem.objects.create(
                invoice=invoice,
                tariff=item["tariff"],
                previous_reading=item["previous_reading"],
                current_reading=item["current_reading"],
                consumption=item["consumption"],
                tariff_price_at_billing=item["tariff_price_at_billing"],
                base_amount=item["base_amount"],
                benefit_discount=item["benefit_discount"],
            )
        for r_item in recalc_items_to_create:
            InvoiceItem.objects.create(
                invoice=invoice,
                tariff=None,
                comment=r_item["description"],
                base_amount=r_item["amount"],
                benefit_discount=Decimal("0.00"),
                consumption=Decimal("0.00"),
            )

    @classmethod
    def _get_period_readings(cls, meter, billing_period):
        next_month_start = (billing_period + timedelta(days=32)).replace(day=1)
        prev_reading = (
            MeterReading.objects.filter(meter=meter, reading_date__lt=billing_period)
            .order_by("reading_date")
            .last()
        )
        curr_reading = (
            MeterReading.objects.filter(
                meter=meter,
                reading_date__gte=billing_period,
                reading_date__lt=next_month_start,
            )
            .order_by("reading_date")
            .last()
        )
        return prev_reading, curr_reading

    @classmethod
    def _calculate_previous_account_balance(cls, account, billing_period):
        past_invoices = Invoice.objects.filter(
            account=account, billing_period__lt=billing_period
        )
        total_due = past_invoices.aggregate(Sum("amount_due"))[
            "amount_due__sum"
        ] or Decimal("0.00")
        total_paid = past_invoices.aggregate(Sum("amount_paid"))[
            "amount_paid__sum"
        ] or Decimal("0.00")
        return total_due - total_paid


class PaymentService:
    @classmethod
    @transaction.atomic
    def distribute_payment(cls, account, amount):
        """
        Розподіляє ЖИВУ СУМУ поповнення (amount) по старих квитанціях користувача.
        Працює ідеально, навіть якщо загальний баланс рахунку глибоко мінусовий!
        """
        # Скільки грошей у нас є на розподіл прямо зараз
        available_cash = Decimal(str(amount))

        if available_cash <= 0:
            return

        # Шукаємо всі квитанції, які ще не оплачені повністю (від найстаріших)
        unpaid_invoices = Invoice.objects.filter(
            account=account,
            status__in=[Invoice.Status.UNPAID, Invoice.Status.PARTIALLY_PAID],
        ).order_by("billing_period")

        for invoice in unpaid_invoices:
            # Якщо внесені гроші закінчилися — зупиняємо розподіл
            if available_cash <= 0:
                break

            # Скільки залишилося доплатити за цей конкретний місяць
            remaining_to_pay = invoice.amount_due - invoice.amount_paid

            if available_cash >= remaining_to_pay:
                # Цих грошей вистачає повністю закрити цей інвойс
                invoice.amount_paid += remaining_to_pay
                invoice.status = Invoice.Status.PAID
                available_cash -= remaining_to_pay  # зменшуємо наш гаманець поповнення
                invoice.save()
            else:
                # Грошей вистачає лише частково покрити інвойс
                invoice.amount_paid += available_cash
                invoice.status = Invoice.Status.PARTIALLY_PAID
                available_cash = Decimal("0.00")  # всі внесені гроші пішли в цей інвойс
                invoice.save()
