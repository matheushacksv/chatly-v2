from ninja import ModelSchema, Schema

from apps.evolution.models import EvolutionAccount, EvolutionContact, EvolutionMessage


class EvolutionAccountOutDTO(ModelSchema):
    user_email: str

    class Meta:
        model = EvolutionAccount
        fields = ['id', 'instance_name', 'status', 'phone_number', 'created_at']

    @staticmethod
    def resolve_user_email(obj):
        return obj.user.email


class EvolutionConnectDTO(Schema):
    phone: str = ''


class EvolutionConnectOutDTO(Schema):
    qrcode: str = ''
    pair_code: str = ''


class EvolutionQRCodeOutDTO(Schema):
    qrcode: str


class EvolutionContactOutDTO(ModelSchema):
    avatar_url: str

    class Meta:
        model = EvolutionContact
        fields = ['id', 'wa_id', 'name', 'created_at']


class EvolutionMessageOutDTO(ModelSchema):
    media_url: str | None = None

    class Meta:
        model = EvolutionMessage
        fields = ['id', 'direction', 'content', 'message_type', 'status', 'error_detail', 'wa_message_id', 'created_at']

    @staticmethod
    def resolve_media_url(obj):
        return obj.media_file.url if obj.media_file else None


class EvolutionSendMessageDTO(Schema):
    content: str


class EvolutionDirectoryEntryDTO(Schema):
    wa_id: str
    name: str = ''
    source: str


class EvolutionStartConversationDTO(Schema):
    phone: str
    name: str = ''
