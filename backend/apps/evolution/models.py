from uuid import uuid4

from django.conf import settings
from django.db import models


def evolution_media_path(instance, filename):
    return f'evolution/{uuid4().hex}/{filename}'


class EvolutionAccount(models.Model):
    class Status(models.TextChoices):
        CONNECTING = 'connecting', 'Conectando'
        CONNECTED = 'connected', 'Conectado'
        DISCONNECTED = 'disconnected', 'Desconectado'

    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='evolution_accounts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='evolution_accounts')
    instance_name = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=50, choices=Status.choices, default=Status.CONNECTING)
    phone_number = models.CharField(max_length=50, blank=True, default='')
    instance_api_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    last_logged_out_reason = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'user'], name='unique_evolution_account_per_user')]

    def __str__(self):
        return self.instance_name


class EvolutionContact(models.Model):
    account = models.ForeignKey(EvolutionAccount, on_delete=models.CASCADE, related_name='contacts')
    wa_id = models.CharField(max_length=100)
    name = models.CharField(max_length=100, blank=True)
    avatar_url = models.TextField(blank=True, default='')
    avatar_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'wa_id'], name='unique_evolution_contact_per_account')]

    def __str__(self):
        return f'{self.name} - {self.wa_id}'


class EvolutionMessage(models.Model):
    class Direction(models.TextChoices):
        IN = 'in', 'Recebida'
        OUT = 'out', 'Enviada'

    class Status(models.TextChoices):
        RECEIVED = 'received', 'Recebida'
        SENT = 'sent', 'Enviada'
        DELIVERED = 'delivered', 'Entregue'
        READ = 'read', 'Lida'
        FAILED = 'failed', 'Falha'

    class Type(models.TextChoices):
        TEXT = (
            'text',
            'Texto',
        )
        IMAGE = 'image', 'Imagem'
        AUDIO = 'audio', 'Áudio'
        VIDEO = 'video', 'Vídeo'
        DOCUMENT = 'document', 'Documento'
        STICKER = 'sticker', 'Figurinha'

    contact = models.ForeignKey(EvolutionContact, on_delete=models.CASCADE, related_name='messages')
    direction = models.CharField(max_length=10, choices=Direction.choices)
    content = models.TextField()
    message_type = models.CharField(max_length=10, choices=Type.choices, default=Type.TEXT)
    media_file = models.FileField(upload_to=evolution_media_path, blank=True, null=True)
    mime_type = models.CharField(max_length=100, blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEIVED)
    error_detail = models.CharField(max_length=255, blank=True, null=True)
    wa_message_id = models.CharField(max_length=128, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['contact', 'created_at'])]
