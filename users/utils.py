from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from home_utility.settings import DEFAULT_FROM_EMAIL

user = get_user_model()

# Передаємо в функцію аргумент 'user_instance' — це конкретний об'єкт юзера з бази
def send_verification_mail(user_instance):
    # 1. Генеруємо безпечний UID та токен для КОНКРЕТНОГО користувача
    uid = urlsafe_base64_encode(force_bytes(user_instance.pk))
    token = default_token_generator.make_token(user_instance)

    # 2. Формуємо правильне посилання для верифікації
    verification_url = f"http://127.0.0.1:8000/api/users/verify/{uid}/{token}/"

    # 3. Використовуємо дані конкретного екземпляра
    send_mail(
        subject="Confirm your registration",
        message=f"Welcome, {user_instance.get_full_name() or user_instance.email}!",
        from_email=DEFAULT_FROM_EMAIL,
        recipient_list=[user_instance.email], # Тепер тут буде рядок "test@email.com", а не DeferredAttribute
        fail_silently=False,
    )

    # 4. Виводимо посилання в консоль сервера (дуже зручно для розробки)
    print(f"\n🚀 КЛІКНИ СЮДИ ДЛЯ АКТИВАЦІЇ: {verification_url}\n")