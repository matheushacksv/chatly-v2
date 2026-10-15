from datetime import time
from typing import Annotated, Literal

from ninja import Field, ModelSchema, Schema

import json

from apps.agents.models import Agent, AgentTool, Credential, Document

NativeTool = Literal['handoff', 'followup', 'save_field', 'tag', 'pipedrive']
Weekdays = Annotated[list[Annotated[int, Field(ge=0, le=6)]], Field(min_length=1)]  # 0 = segunda


class CredentialInDTO(Schema):
    provider: Literal['anthropic', 'openai']
    api_key: str


class CredentialOutDTO(ModelSchema):
    class Meta:
        model = Credential
        fields = ['id', 'provider']


class CredentialPatchDTO(Schema):
    api_key: str


class AgentInDTO(Schema):
    name: str
    credential_id: int
    model: str
    prompt: str
    description: str = ''
    response_delay: int = Field(0, ge=0)
    split_messages: bool = False
    handoff_resume_hours: int = Field(24, ge=0, le=720)
    ai_hours_start: time | None = None
    ai_hours_end: time | None = None
    ai_days: Weekdays = [0, 1, 2, 3, 4, 5, 6]
    native_tools: list[NativeTool] = ['handoff', 'pipedrive']


class AgentOutDTO(ModelSchema):
    class Meta:
        model = Agent
        fields = [
            'id', 'name', 'description', 'model', 'prompt', 'response_delay', 'split_messages', 'handoff_resume_hours',
            'ai_hours_start', 'ai_hours_end', 'ai_days', 'native_tools', 'created_at',
        ]


class AgentPatchDTO(Schema):
    name: str | None = None
    description: str | None = None
    model: str | None = None
    credential_id: int | None = None
    prompt: str | None = None
    response_delay: int | None = Field(None, ge=0)
    split_messages: bool | None = None
    handoff_resume_hours: int | None = Field(None, ge=0, le=720)
    ai_hours_start: time | None = None
    ai_hours_end: time | None = None
    ai_days: Weekdays | None = None
    native_tools: list[NativeTool] | None = None


class DocumentOutDTO(ModelSchema):
    class Meta:
        model = Document
        fields = ['id', 'name', 'status', 'created_at']


class ToolParamDTO(Schema):
    name: str
    type: Literal['string', 'number', 'boolean'] = 'string'
    description: str = ''
    required: bool = True


class ToolHeaderDTO(Schema):
    key: str
    value: str = ''


class AgentToolInDTO(Schema):
    name: str
    description: str
    url: str
    method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] = 'POST'
    parameters: list[ToolParamDTO] = []
    headers: list[ToolHeaderDTO] = []
    secret_headers: list[ToolHeaderDTO] = []  # valor vazio = mantém o salvo
    body: str = ''
    requires_confirmation: bool = False
    active: bool = True


class AgentToolPatchDTO(Schema):
    name: str | None = None
    description: str | None = None
    url: str | None = None
    method: Literal['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] | None = None
    parameters: list[ToolParamDTO] | None = None
    headers: list[ToolHeaderDTO] | None = None
    secret_headers: list[ToolHeaderDTO] | None = None
    body: str | None = None
    requires_confirmation: bool | None = None
    active: bool | None = None


class AgentToolOutDTO(ModelSchema):
    secret_header_keys: list[str] = []  # só os nomes: o valor nunca sai do backend

    class Meta:
        model = AgentTool
        fields = ['id', 'name', 'description', 'parameters', 'method', 'url', 'headers', 'body', 'requires_confirmation', 'active', 'created_at']

    @staticmethod
    def resolve_secret_header_keys(obj):
        return list(json.loads(obj.secret_headers or '{}'))


class AgentToolTestDTO(Schema):
    arguments: dict[str, str | int | float | bool | None] = {}


class AgentToolTestResultDTO(Schema):
    ok: bool
    result: str


class PlaygroundMessageDTO(Schema):
    role: Literal['user', 'assistant']
    content: str


class PlaygroundInDTO(Schema):
    messages: list[PlaygroundMessageDTO]
    name: str = 'Cliente de teste'
    phone: str = '5511999999999'
    fields: dict[str, str] = {}
    memory: str = ''
    # versão ainda não salva do formulário: testa antes de salvar
    prompt: str | None = None
    native_tools: list[NativeTool] | None = None


class PlaygroundToolCallDTO(Schema):
    name: str
    arguments: dict


class PlaygroundOutDTO(Schema):
    parts: list[str]
    tool_calls: list[PlaygroundToolCallDTO]
    fields: dict[str, str]
