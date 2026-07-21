from uuid import uuid4

from django.db import models

from core.fields import EncryptedTextField


def chat_media_path(instance, filename):
    return f'chat/{uuid4().hex}/{filename}'


class WhatsAppAccount(models.Model):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='whatsapp_accounts')
    agent = models.ForeignKey('agents.Agent', on_delete=models.PROTECT, related_name='whatsapp_accounts')
    phone_number_id = models.CharField(max_length=64, unique=True)
    waba_id = models.CharField(max_length=64)
    access_token = EncryptedTextField()
    app_secret = EncryptedTextField()
    app_id = models.CharField(max_length=32, blank=True, default='')
    verify_token = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.company.name} - {self.phone_number_id}'


class Contact(models.Model):
    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name='contacts')
    wa_id = models.CharField(max_length=32)
    name = models.CharField(max_length=255, blank=True)
    memory = models.TextField(blank=True, default='')
    ai_paused = models.BooleanField(default=False)
    pipedrive_person_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'wa_id'], name='unique_contact_per_account')]

    def __str__(self):
        return self.name or self.wa_id


class Message(models.Model):
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

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='messages')
    direction = models.CharField(max_length=3, choices=Direction.choices)
    content = models.TextField()
    message_type = models.CharField(max_length=10, choices=Type.choices, default=Type.TEXT)
    media_file = models.FileField(upload_to=chat_media_path, blank=True, null=True)
    media_id = models.CharField(max_length=128, blank=True, default='')
    error_detail = models.CharField(max_length=255, blank=True, default='')
    wa_message_id = models.CharField(max_length=128, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    from_human = models.BooleanField(default=False)
    campaign = models.ForeignKey('Campaign', on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['contact', 'created_at'])]


class Sticker(models.Model):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='stickers')
    file = models.FileField(upload_to=chat_media_path)
    created_at = models.DateTimeField(auto_now_add=True)


class Campaign(models.Model):
    class Status(models.TextChoices):
        SENDING = 'sending', 'Enviando'
        DONE = 'done', 'Concluída'
        FAILED = 'failed', 'Falha'

    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=255)
    template_name = models.CharField(max_length=255)
    template_language = models.CharField(max_length=16)
    body_params = models.JSONField(default=list)
    header_media_id = models.CharField(max_length=128, blank=True, default='')
    header_media_type = models.CharField(max_length=16, blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENDING)
    total = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class TemplateHeaderMedia(models.Model):
    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name='template_header_media')
    template_name = models.CharField(max_length=255)
    template_language = models.CharField(max_length=16)
    media_id = models.CharField(max_length=128)
    media_type = models.CharField(max_length=16)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'template_name', 'template_language'], name='unique_template_header_media')
        ]