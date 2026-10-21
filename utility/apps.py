from django.apps import AppConfig


class UtilityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "utility"  # назва вашого додатку

    def ready(self):
        # Обовязково імпортуємо сигнали саме тут!
        import utility.signals  # noqa: F401
