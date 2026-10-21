from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    username = models.CharField(max_length=150, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)

    email = models.EmailField(unique=True, max_length=100)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def save(self, *args, **kwargs):
        # Якщо username не передано, автоматично заповнюємо його імейлом,
        # оскільки AbstractUser вимагає унікального або заповненого username на деяких етапах
        if not self.username and self.email:
            self.username = self.email
        super().save(*args, **kwargs)

    def soft_delete(self):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.is_active = False
        self.save()

    def __str__(self):
        return f"{self.get_full_name()} ({self.email})"
