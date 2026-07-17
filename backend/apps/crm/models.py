from django.db import models
from core.fields import EncryptedTextField

class PipedriveAccount(models.Model):
    company = models.OneToOneField('companies.Company', on_delete=models.CASCADE, related_name='pipedrive_account')
    api_key = EncryptedTextField()
    default_pipeline_id = models.IntegerField(null=True, blank=True)
    default_stage_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
