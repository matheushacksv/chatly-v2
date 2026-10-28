from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

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
    ai_enabled = models.BooleanField(default=True)  # desligada: mensagens chegam, só a equipe responde
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.company.name} - {self.phone_number_id}'


class Tag(models.Model):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='tags')
    name = models.CharField(max_length=40)
    color = models.CharField(max_length=20, default='neutral')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower('name'), 'company', name='unique_tag_name_per_company')]

    def __str__(self):
        return self.name


class ContactField(models.Model):
    """Campo personalizado do contato. O valor fica em Contact.custom_fields[key] e vira a variável {{key}}."""

    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='contact_fields')
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'key'], name='unique_contact_field_key_per_company')]

    def __str__(self):
        return self.label


class Contact(models.Model):
    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name='contacts')
    wa_id = models.CharField(max_length=32)
    name = models.CharField(max_length=255, blank=True)
    memory = models.TextField(blank=True, default='')
    ai_paused = models.BooleanField(default=False)
    # veio de campanha: fora do horário da IA a mensagem fica pra equipe, sem resposta atrasada quando o
    # horário abrir. Fluxo e clique em botão não limpam; limpa quando a IA responde de fato
    ai_no_catchup = models.BooleanField(default=False)
    ai_resume_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_contacts'
    )
    pipedrive_person_id = models.IntegerField(null=True, blank=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name='contacts')
    custom_fields = models.JSONField(default=dict, blank=True)  # {ContactField.key: valor}
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
    button_id = models.CharField(max_length=64, blank=True, default='')
    from_human = models.BooleanField(default=False)
    campaign = models.ForeignKey('Campaign', on_delete=models.SET_NULL, null=True, blank=True, related_name='messages')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['contact', 'created_at'])]


class Sticker(models.Model):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='stickers')
    file = models.FileField(upload_to=chat_media_path)
    # wa_message_id da mensagem de onde foi salva (qualquer canal: o id é global, não colide como PK);
    # é o que marca a figurinha como "já salva" no chat e evita salvar duas vezes
    source_wa_message_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Rascunho'
        SCHEDULED = 'scheduled', 'Agendada'
        SENDING = 'sending', 'Enviando'
        DONE = 'done', 'Concluída'
        FAILED = 'failed', 'Falha'
        CANCELED = 'canceled', 'Cancelada'

    account = models.ForeignKey(WhatsAppAccount, on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=255)
    template_name = models.CharField(max_length=255)
    template_language = models.CharField(max_length=16)
    body_params = models.JSONField(default=list)
    header_media_id = models.CharField(max_length=128, blank=True, default='')
    header_media_type = models.CharField(max_length=16, blank=True, default='')
    # clique num botão do template dispara esse fluxo (gatilho 'campaign')
    flow = models.ForeignKey('automations.Flow', on_delete=models.SET_NULL, null=True, blank=True, related_name='campaigns')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENDING)
    total = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    # agendada: dispara quando passar desse horário (dispatch_scheduled_campaigns, a cada minuto)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    # destinatários calculados na criação; o envio agendado usa esta lista
    recipient_ids = models.JSONField(default=list, blank=True)
    # rascunho: o formulário como o front deixou (template, público, agendamento); nunca dispara sozinho
    draft = models.JSONField(default=dict, blank=True)
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

class ToolCall(models.Model):
    """Registro de cada ferramenta que a IA usou na conversa: o atendente vê o que ela fez."""

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='tool_calls')
    name = models.CharField(max_length=64)
    arguments = models.JSONField(default=dict)
    result = models.TextField(blank=True, default='')
    ok = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class FollowUp(models.Model):
    """Retomada agendada pela IA ("me chama segunda"). dispatch_followups envia quando vencer."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Agendada'
        SENT = 'sent', 'Enviada'
        CANCELED = 'canceled', 'Cancelada'
        FAILED = 'failed', 'Falhou'

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='followups')
    send_at = models.DateTimeField()
    text = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class QuickReply(models.Model):
    """Resposta pronta do atendente: digita /atalho no chat e o texto entra preenchido com os dados do contato."""

    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='quick_replies')
    shortcut = models.CharField(max_length=40)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower('shortcut'), 'company', name='unique_quick_reply_shortcut_per_company')]

    def __str__(self):
        return self.shortcut
