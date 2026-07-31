import secrets

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
    created_at = models.DateTimeField(auto_now_add=True)
