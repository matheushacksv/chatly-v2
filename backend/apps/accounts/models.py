from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager['User']):
    def create_user(self, email, password, **extra_fields) -> User:
        user = User(email=self.normalize_email(email), **extra_fields)

        if password:
            user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields) -> User:
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    avatar = models.FileField(upload_to='avatars/', null=True, blank=True)

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    def __str__(self):
        return self.name if self.name else self.email

class SiteConfig(models.Model):
    registration_open = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Configuração do site'
        verbose_name_plural = 'Configuração do site'

    def __str__(self):
        return 'Configuração do site'

    @classmethod
    def registration_is_open(cls):
        obj = cls.objects.first()
        return obj.registration_open if obj else True
