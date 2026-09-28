from uuid import uuid4

from django.conf import settings
from django.db import models


def personal_media_path(instance, filename):
    return f'personal/{uuid4().hex}/{filename}'


class PersonalAccount(models.Model):
    """Número pessoal do atendente, conectado pelo gateway OpenWA (uma sessão por atendente)."""

    class Status(models.TextChoices):
        CONNECTING = 'connecting', 'Conectando'
        CONNECTED = 'connected', 'Conectado'
        DISCONNECTED = 'disconnected', 'Desconectado'

    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='personal_accounts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='personal_accounts')
    # nome é nosso (c{company}u{user}); id é o UUID que o gateway devolve e vem em todo webhook
    session_name = models.CharField(max_length=50, unique=True)
    session_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONNECTING)
    phone_number = models.CharField(max_length=50, blank=True, default='')
    last_logged_out_reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'user'], name='unique_personal_account_per_user')]

    def __str__(self):
        return self.session_name


class PersonalContact(models.Model):
    account = models.ForeignKey(PersonalAccount, on_delete=models.CASCADE, related_name='contacts')
    wa_id = models.CharField(max_length=100)
    name = models.CharField(max_length=100, blank=True)
    avatar_url = models.TextField(blank=True, default='')
    avatar_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'wa_id'], name='unique_personal_contact_per_account')]

    def __str__(self):
        return f'{self.name} - {self.wa_id}'


class PersonalMessage(models.Model):
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
        TEXT = 'text', 'Texto'
        IMAGE = 'image', 'Imagem'
        AUDIO = 'audio', 'Áudio'
        VIDEO = 'video', 'Vídeo'
        DOCUMENT = 'document', 'Documento'
        STICKER = 'sticker', 'Figurinha'

    contact = models.ForeignKey(PersonalContact, on_delete=models.CASCADE, related_name='messages')
    direction = models.CharField(max_length=10, choices=Direction.choices)
    content = models.TextField()
    message_type = models.CharField(max_length=10, choices=Type.choices, default=Type.TEXT)
    media_file = models.FileField(upload_to=personal_media_path, blank=True, null=True)
    mime_type = models.CharField(max_length=100, blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEIVED)
    error_detail = models.CharField(max_length=255, blank=True, null=True)
    # id é do gateway (não nosso): unique é o que casa o eco do webhook com o envio pelo painel
    wa_message_id = models.CharField(max_length=128, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['contact', 'created_at'])]
