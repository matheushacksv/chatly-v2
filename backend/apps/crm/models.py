import secrets

from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.fields import EncryptedTextField


def new_webhook_token() -> str:
    return secrets.token_urlsafe(32)


class PipedriveAccount(models.Model):
    company = models.OneToOneField('companies.Company', on_delete=models.CASCADE, related_name='pipedrive_account')
    api_key = EncryptedTextField()
    default_pipeline_id = models.IntegerField(null=True, blank=True)
    default_stage_id = models.IntegerField(null=True, blank=True)
    webhook_token = models.CharField(max_length=64, db_index=True, default=new_webhook_token)
    webhook_ids = models.JSONField(default=list)
    lead_account = models.ForeignKey('chats.WhatsAppAccount', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # negócio que entra numa dessas etapas recebe o template de abertura e a IA cuida das respostas
    auto_stage_ids = ArrayField(models.IntegerField(), default=list, blank=True)
    auto_template = models.JSONField(default=dict, blank=True)  # {template_name, language, body_params, header_media_id}
    auto_only_in_hours = models.BooleanField(default=True)  # fora do horário da IA o negócio fica de fora (não espera)
    # template por valor de campo do contato, primeira regra que casa; nenhuma casou: auto_template
    auto_rules = models.JSONField(default=list, blank=True)  # [{field, values: [...], template: {...}}]
    # campo personalizado do Pipedrive (negócio ou pessoa) copiado pro campo do contato a cada evento de negócio
    field_map = models.JSONField(default=list, blank=True)  # [{source: deal|person, key, name, options: {id: label}, contact_field}]
    auto_delay_minutes = models.PositiveIntegerField(default=0)  # espera antes da abertura; 0 = na hora
    # template pra quem não respondeu a abertura até after_hours depois; sem template_name = sem follow-up
    auto_followup = models.JSONField(default=dict, blank=True)  # {template_name, language, body_params, header_media_id, after_hours}
    created_at = models.DateTimeField(auto_now_add=True)


class DealOpening(models.Model):
    """Negócio que já recebeu o template de abertura: um envio por negócio, mesmo mudando de etapa."""

    pipedrive_account = models.ForeignKey(PipedriveAccount, on_delete=models.CASCADE, related_name='openings')
    deal_id = models.IntegerField()
    contact = models.ForeignKey('chats.Contact', on_delete=models.CASCADE, related_name='+')
    send_at = models.DateTimeField(null=True, blank=True)  # abertura esperando o atraso; null = enviada ou descartada
    sent_at = models.DateTimeField(null=True, blank=True)
    followup_at = models.DateTimeField(null=True, blank=True)  # follow-up pendente, se o lead não responder até lá
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['pipedrive_account', 'deal_id'], name='unique_opening_per_deal')]
