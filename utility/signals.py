# utility/signals.py
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Invoice, ServiceAccountRecalculation


@receiver(post_delete, sender=Invoice)
def update_account_balance_on_invoice_delete(sender, instance, **kwargs):
    """Автоматично коригує баланс особового рахунку при видаленні інвойсу."""
    account = instance.account

    with transaction.atomic():
        # Блокуємо рахунок від конкурентних запитів
        account_to_update = account.__class__.objects.select_for_update().get(
            pk=account.pk
        )

        # 1. Повертаємо суму інвойсу на баланс
        account_to_update.balance += instance.amount_due
        account_to_update.save()

        # 2. Повертаємо перерахунки в активний стан
        ServiceAccountRecalculation.objects.filter(
            account=account,
            billing_period=instance.billing_period,
            is_applied=True,
        ).update(is_applied=False)
