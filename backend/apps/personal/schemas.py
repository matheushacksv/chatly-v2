from ninja import ModelSchema, Schema

from apps.personal.models import PersonalAccount, PersonalContact, PersonalMessage


class PersonalAccountOutDTO(ModelSchema):
    user_email: str

    class Meta:
        model = PersonalAccount
        fields = ['id', 'session_name', 'status', 'phone_number', 'created_at']

    @staticmethod
    def resolve_user_email(obj):
        return obj.user.email


class PersonalConnectDTO(Schema):
    phone: str = ''


class PersonalConnectOutDTO(Schema):
    qrcode: str = ''
    pair_code: str = ''


class PersonalQRCodeOutDTO(Schema):
    qrcode: str


class PersonalContactOutDTO(ModelSchema):
    avatar_url: str

    class Meta:
        model = PersonalContact
        fields = ['id', 'wa_id', 'name', 'created_at']


class PersonalMessageOutDTO(ModelSchema):
    media_url: str | None = None
    # anotado no list_messages; nos endpoints de envio a mensagem vem sem o atributo e fica False
    sticker_saved: bool = False

    class Meta:
        model = PersonalMessage
        fields = ['id', 'direction', 'content', 'message_type', 'status', 'error_detail', 'wa_message_id', 'created_at']

    @staticmethod
    def resolve_media_url(obj):
        return obj.media_file.url if obj.media_file else None


class PersonalSendMessageDTO(Schema):
    content: str


class PersonalDirectoryEntryDTO(Schema):
    wa_id: str
    name: str = ''
    source: str


class PersonalStartConversationDTO(Schema):
    phone: str
    name: str = ''
