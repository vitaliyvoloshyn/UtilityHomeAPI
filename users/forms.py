from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError


class CliAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        """
        Цей метод викликається Django автоматично після перевірки пароля.
        """
        super().confirm_login_allowed(user)  # Спочатку стандартна перевірка (is_active)

        # ЗАМІНІТЬ 'is_verified' на назву вашого поля верифікації в моделі User
        if hasattr(user, 'is_verified') and not user.is_verified:
            raise ValidationError(
                "Ваш акаунт не верифіковано. Будь ласка, підтвердіть ваш Email.",
                code="unverified",
            )
