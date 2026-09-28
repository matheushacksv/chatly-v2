from typing import Literal

from ninja import Field, ModelSchema, Schema

from apps.agents.models import Agent, Credential, Document


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


class AgentOutDTO(ModelSchema):
    class Meta:
        model = Agent
        fields = ['id', 'name', 'description', 'model', 'prompt', 'response_delay', 'split_messages', 'handoff_resume_hours', 'created_at']


class AgentPatchDTO(Schema):
    name: str | None = None
    description: str | None = None
    model: str | None = None
    credential_id: int | None = None
    prompt: str | None = None
    response_delay: int | None = Field(None, ge=0)
    split_messages: bool | None = None
    handoff_resume_hours: int | None = Field(None, ge=0, le=720)


class DocumentOutDTO(ModelSchema):
    class Meta:
        model = Document
        fields = ['id', 'name', 'status', 'created_at']
