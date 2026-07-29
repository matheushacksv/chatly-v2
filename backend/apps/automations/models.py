import secrets

from django.db import models


def new_source_token() -> str:
    return secrets.token_urlsafe(32)


class LeadSource(models.Model):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='lead_sources')
    account = models.ForeignKey('chats.WhatsAppAccount', on_delete=models.CASCADE, related_name='lead_sources')
    name = models.CharField(max_length=100)
    token = models.CharField(max_length=64, unique=True, db_index=True, default=new_source_token)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Flow(models.Model):
    class Trigger(models.TextChoices):
        LEAD = 'lead', 'Lead recebido'
        FIRST_MESSAGE = 'first_message', 'Primeira mensagem'
        KEYWORD = 'keyword', 'Palavra-chave'

    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='flows')
    account = models.ForeignKey('chats.WhatsAppAccount', on_delete=models.CASCADE, related_name='flows')
    name = models.CharField(max_length=100)
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    trigger_config = models.JSONField(default=dict)
    graph = models.JSONField(default=dict)
    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['company', 'active', 'trigger'])]

    def __str__(self):
        return self.name


class FlowRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = 'running', 'Rodando'
        WAITING = 'waiting', 'Aguardando'
        DONE = 'done', 'Concluída'
        FAILED = 'failed', 'Falhou'

    ACTIVE_STATUSES = (Status.RUNNING, Status.WAITING)

    flow = models.ForeignKey(Flow, on_delete=models.CASCADE, related_name='runs')
    contact = models.ForeignKey('chats.Contact', on_delete=models.CASCADE, related_name='flow_runs')
    node_id = models.CharField(max_length=64, blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    context = models.JSONField(default=dict)
    steps = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['contact', 'status'])]
        constraints = [
            models.UniqueConstraint(
                fields=['flow', 'contact'], condition=models.Q(status__in=('running', 'waiting')), name='unique_active_run_per_contact'
            )
        ]
