import secrets

from django.conf import settings
from django.db import models


class Company(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='companies')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


def generate_invite_token():
    return secrets.token_urlsafe(32)


class Membership(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'user'], name='unique_membership')]

    def __str__(self):
        return f'{self.user} @ {self.company}'


class Invite(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='invites')
    token = models.CharField(max_length=64, unique=True, default=generate_invite_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
