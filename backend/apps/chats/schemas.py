from datetime import datetime

from ninja import ModelSchema, Schema
from pydantic import Field

from apps.chats.models import Campaign, Contact, Message, Sticker, WhatsAppAccount


class WhatsAppAccountInDTO(Schema):
    agent_id: int
    phone_number_id: str
    waba_id: str
    access_token: str
    app_secret: str
    verify_token: str


class WhatsAppAccountOutDTO(ModelSchema):
    class Meta:
        model = WhatsAppAccount
        fields = ['id', 'agent', 'phone_number_id', 'waba_id', 'verify_token']


class WhatsAppAccountPatchDTO(Schema):
    agent_id: int | None = None
    waba_id: str | None = None
    access_token: str | None = None
    app_secret: str | None = None
    verify_token: str | None = None


class ContactOutDTO(ModelSchema):
    last_message: str | None = None
    last_message_at: datetime | None = None
    last_inbound_at: datetime | None = None

    class Meta:
        model = Contact
        fields = ['id', 'wa_id', 'name', 'memory', 'ai_paused', 'created_at']


class ContactPatchDTO(Schema):
    ai_paused: bool | None = None
    name: str | None = None


class ContactCreateDTO(Schema):
    number: str
    name: str = ''


class ManualMessageDTO(Schema):
    content: str


class MessageOutDTO(ModelSchema):
    media_url: str | None = None

    class Meta:
        model = Message
        fields = ['id', 'contact', 'direction', 'content', 'message_type', 'wa_message_id', 'from_human', 'status', 'created_at']

    @staticmethod
    def resolve_media_url(obj):
        return obj.media_file.url if obj.media_file else None


class WebhookVerificationParams(Schema):
    mode: str = Field(alias='hub.mode')
    token: str = Field(alias='hub.verify_token')
    challenge: str = Field(alias='hub.challenge')


class StickerOutDTO(ModelSchema):
    url: str

    class Meta:
        model = Sticker
        fields = ['id', 'created_at']

    @staticmethod
    def resolve_url(obj):
        return obj.file.url


class StickerInDTO(Schema):
    message_id: int


class SendStickerDTO(Schema):
    sticker_id: int


class TemplateOutDTO(Schema):
    name: str
    language: str
    status: str
    category: str
    body_text: str
    param_count: int


class SendTemplateDTO(Schema):
    template_name: str
    language: str
    params: list[str] = []


class CampaignRecipientDTO(Schema):
    number: str
    name: str = ''


class ContactImportDTO(Schema):
    numbers: list[CampaignRecipientDTO]


class ContactImportResultDTO(Schema):
    created: int
    existing: int


class CampaignInDTO(Schema):
    account_id: int
    name: str
    template_name: str
    template_language: str
    body_params: list[str] = []
    contact_ids: list[int] | None = None
    numbers: list[CampaignRecipientDTO] = []


class CampaignOutDTO(ModelSchema):
    delivered: int = 0
    read: int = 0

    class Meta:
        model = Campaign
        fields = ['id', 'name', 'template_name', 'template_language', 'status', 'total', 'sent', 'failed', 'created_at']
