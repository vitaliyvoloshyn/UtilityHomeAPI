from django.core.management.base import BaseCommand

from ...services import BillingService


class Command(BaseCommand):
    help = "Автоматично генерує рахунки за комунальні послуги за поточний місяць"

    def handle(self, *args, **options):
        self.stdout.write("Запуск генерації рахунків...")
        try:
            BillingService.generate_monthly_invoices()
            self.stdout.write(self.style.SUCCESS("Рахунки успішно згенеровано!"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Помилка при генерації: {e}"))
