from django.contrib.auth import get_user_model
from rest_framework import serializers

from users.utils import send_verification_mail

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Серіалізатор для безпечного читання даних користувача"""

    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "full_name", "email", "phone", "is_verified", "date_joined")
        read_only_fields = ("id", "is_verified", "date_joined")

    def get_full_name(self, obj):
        """Об'єднуємо first_name та last_name, які є в AbstractUser"""
        return obj.get_full_name() or obj.username


class RegisterSerializer(serializers.ModelSerializer):
    """Серіалізатор для реєстрації нового користувача"""

    password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    first_name = serializers.CharField(required=True, max_length=150)
    last_name = serializers.CharField(required=True, max_length=150)

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "phone", "password")

    def validate_email(self, value):
        """Додаткова валідація емейлу (на всяк випадок)"""
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Користувач з таким email вже існує.")
        return value

    def create(self, validated_data):
        # Створюємо користувача через менеджер
        user = User.objects.create_user(
            username=f"{validated_data['first_name']} {validated_data['last_name']}",
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            phone=validated_data.get("phone", ""),
        )

        # Безпечно викликаємо відправку пошти.
        # Якщо пошта впаде, це не повинно ламати реєстрацію користувача в базі.
        try:
            send_verification_mail(user)
        except Exception as e:  # noqa: BLE001
            # Логуємо помилку в консоль сервера, щоб ви бачили її під час розробки
            print(f"Помилка відправки листа: {e}")

        # ОБОВ'ЯЗКОВО ПОВЕРТАЄМО ОБ'ЄКТ USER
        return user
