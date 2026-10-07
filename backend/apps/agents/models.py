from django.contrib.postgres.fields import ArrayField
from django.db import models
from pgvector.django import VectorField

from core.fields import EncryptedTextField


class Credential(models.Model):
    class Providers(models.TextChoices):
        ANTHROPIC = 'anthropic', 'Anthropic'
        OPENAI = 'openai', 'OpenAI'

    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='credentials')
    provider = models.CharField(max_length=50, choices=Providers.choices)
    api_key = EncryptedTextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'provider'], name='unique_provider_per_company')]

    def __str__(self):
        return f'{self.company} - {self.provider}'


def all_days() -> list[int]:
    return list(range(7))


class Agent(models.Model):
    company = models.ForeignKey('companies.Company', on_delete=models.CASCADE, related_name='agents')
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=100)
    response_delay = models.PositiveSmallIntegerField(default=0)
    credential = models.ForeignKey(Credential, on_delete=models.PROTECT, related_name='agents')
    prompt = models.TextField()
    split_messages = models.BooleanField(default=False)
    # IA pausada (handoff ou atendente) volta sozinha depois disso; 0 = só volta manualmente
    handoff_resume_hours = models.PositiveSmallIntegerField(default=24)
    # horário em que a IA atende; sem início/fim = sempre. Fim <= início vira a meia-noite (18h-09h)
    ai_hours_start = models.TimeField(null=True, blank=True)
    ai_hours_end = models.TimeField(null=True, blank=True)
    ai_days = ArrayField(models.PositiveSmallIntegerField(), default=all_days)  # 0 = segunda
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Document(models.Model):
    class Status(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        READY = 'ready', 'Ready'
        FAILED = 'failed', 'Failed'

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=255)
    content = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    created_at = models.DateTimeField(auto_now_add=True)


class DocumentChunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='chunks')
    content = models.TextField()
    embedding = VectorField(dimensions=1536)
