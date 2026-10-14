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


# ferramentas embutidas que o dono liga/desliga por agente (apps/chats/ai_tools.py)
NATIVE_TOOLS = ('handoff', 'followup', 'save_field', 'tag', 'pipedrive')


def default_native_tools() -> list[str]:
    return ['handoff', 'pipedrive']  # o que já existia antes das ferramentas configuráveis


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
    native_tools = ArrayField(models.CharField(max_length=30), default=default_native_tools)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class AgentTool(models.Model):
    """Ferramenta HTTP criada pelo dono: a IA chama quando a descrição bate com a conversa."""

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='tools')
    name = models.CharField(max_length=64)  # nome da função que a IA enxerga
    description = models.TextField()  # quando usar: é o que a IA lê pra decidir
    parameters = models.JSONField(default=list)  # [{name, type, description, required}] preenchidos pela IA
    method = models.CharField(max_length=10, default='POST')
    url = models.CharField(max_length=500)
    headers = models.JSONField(default=list)  # [{key, value}] visíveis
    secret_headers = EncryptedTextField(blank=True, default='')  # JSON {key: value}, nunca volta pro front
    body = models.TextField(blank=True, default='')
    requires_confirmation = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['agent', 'name'], name='unique_tool_name_per_agent')]

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
