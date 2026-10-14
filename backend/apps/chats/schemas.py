from datetime import datetime
from typing import Literal

from ninja import ModelSchema, Schema
from django.utils import timezone
from pydantic import Field

from apps.agents.services import next_ai_opening
from apps.chats.models import Campaign, Contact, ContactField, FollowUp, ToolCall, Message, Sticker, Tag, WhatsAppAccount


class WhatsAppAccountInDTO(Schema):
    agent_id: int
    phone_number_id: str
    waba_id: str
    access_token: str
    app_secret: str
    verify_token: str
    app_id: str = ''


class WhatsAppAccountOutDTO(ModelSchema):
    class Meta:
        model = WhatsAppAccount
        fields = ['id', 'agent', 'phone_number_id', 'waba_id', 'verify_token', 'app_id']


class WhatsAppAccountPatchDTO(Schema):
    agent_id: int | None = None
    waba_id: str | None = None
    access_token: str | None = None
    app_secret: str | None = None
    verify_token: str | None = None
    app_id: str | None = None


class WhatsAppRegisterDTO(Schema):
    pin: str = Field(pattern=r'^\d{6}$')


TAG_COLORS = Literal['neutral', 'primary', 'success', 'info', 'warning', 'error']


class TagOutDTO(ModelSchema):
    class Meta:
        model = Tag
        fields = ['id', 'name', 'color']


class ContactFieldOutDTO(ModelSchema):
    class Meta:
        model = ContactField
        fields = ['id', 'key', 'label']


class ContactFieldInDTO(Schema):
    label: str = Field(min_length=1, max_length=60)


class TagInDTO(Schema):
    name: str = Field(min_length=1, max_length=40)
    color: TAG_COLORS = 'neutral'


class ContactOutDTO(ModelSchema):
    tags: list[TagOutDTO] = []
    last_message: str | None = None
    last_message_at: datetime | None = None
    last_inbound_at: datetime | None = None
    ai_opens_at: datetime | None = None  # fora do horário da IA: quando ela volta a responder

    class Meta:
        model = Contact
        fields = ['id', 'wa_id', 'name', 'memory', 'ai_paused', 'ai_resume_at', 'assigned_to', 'custom_fields', 'created_at']

    @staticmethod
    def resolve_tags(obj):
        return list(obj.tags.all())

    @staticmethod
    def resolve_ai_opens_at(obj):
        return next_ai_opening(obj.account.agent, timezone.now())


class ContactPatchDTO(Schema):
    ai_paused: bool | None = None
    custom_fields: dict[str, str] | None = None  # mescla; valor vazio apaga o campo
    assigned_to: int | None = None  # null explícito desatribui
    name: str | None = None


class ContactCreateDTO(Schema):
    number: str
    name: str = ''


class ManualMessageDTO(Schema):
    content: str


class MessageOutDTO(ModelSchema):
    media_url: str | None = None
    sticker_saved: bool = False

    class Meta:
        model = Message
        fields = ['id', 'contact', 'direction', 'content', 'message_type', 'wa_message_id', 'from_human', 'status', 'error_detail', 'created_at']

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
    id: str
    name: str
    language: str
    status: str
    category: str
    header_format: str
    has_header_media: bool
    body_text: str
    param_count: int


class MediaUploadDTO(Schema):
    media_id: str


class SendTemplateDTO(Schema):
    template_name: str
    language: str
    params: list[str] = []
    header_media_id: str = ''


class CampaignRecipientDTO(Schema):
    number: str
    name: str = ''
    fields: dict[str, str] = {}  # colunas extras do CSV: {ContactField.key: valor}


class ContactImportDTO(Schema):
    numbers: list[CampaignRecipientDTO]
    tag_ids: list[int] = []  # etiquetas aplicadas em todos os importados


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
    header_media_id: str = ''
    flow_id: int | None = None
    tag_groups: list[list[int]] = []  # só no modo todos: OU entre grupos, E dentro do grupo
    scheduled_at: datetime | None = None  # vazio = dispara agora
    import_tag_ids: list[int] = []  # etiquetas aplicadas nos contatos do CSV
    exclude_tag_ids: list[int] = []


class CampaignAudienceInDTO(Schema):
    account_id: int
    tag_groups: list[list[int]] = []
    exclude_tag_ids: list[int] = []


class CampaignAudienceOutDTO(Schema):
    count: int
    sample: list[str]


class CampaignOutDTO(ModelSchema):
    delivered: int = 0
    read: int = 0

    class Meta:
        model = Campaign
        fields = ['id', 'name', 'template_name', 'template_language', 'flow', 'status', 'total', 'sent', 'failed', 'scheduled_at', 'created_at']


class HeaderDTO(Schema):
    format: Literal['NONE', 'TEXT', 'IMAGE', 'VIDEO', 'DOCUMENT'] = 'NONE'
    text: str = ''
    example: str = ''
    handle: str = ''
    media_id: str = ''


class TemplateMediaDTO(Schema):
    handle: str
    media_id: str


class ButtonDTO(Schema):
    type: Literal['QUICK_REPLY', 'URL', 'PHONE_NUMBER']
    text: str
    url: str = ''
    phone_number: str = ''


class TemplateInDTO(Schema):
    name: str
    language: str
    category: Literal['MARKETING', 'UTILITY']
    header: HeaderDTO | None = None
    body_text: str
    body_examples: list[str] = []
    footer_text: str = ''
    buttons: list[ButtonDTO] = []


class TemplateCreatedDTO(Schema):
    id: str
    status: str
    category: str


class TemplateEditedDTO(Schema):
    success: bool


class TemplateDetailDTO(Schema):
    id: str
    name: str
    language: str
    category: str
    status: str
    header: HeaderDTO | None = None
    has_header_media: bool
    body_text: str
    body_examples: list[str] = []
    footer_text: str = ''
    buttons: list[ButtonDTO] = []


class ToolCallOutDTO(ModelSchema):
    class Meta:
        model = ToolCall
        fields = ['id', 'name', 'arguments', 'result', 'ok', 'created_at']


class FollowUpOutDTO(ModelSchema):
    class Meta:
        model = FollowUp
        fields = ['id', 'send_at', 'text', 'status', 'error', 'created_at']


class ContactAIActivityDTO(Schema):
    tool_calls: list[ToolCallOutDTO]
    followups: list[FollowUpOutDTO]
