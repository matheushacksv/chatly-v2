from ninja import ModelSchema, Schema

from apps.automations.models import Flow, FlowRun, LeadSource


class FlowInDTO(Schema):
    name: str
    account_id: int
    trigger: str
    trigger_config: dict = {}
    graph: dict = {}


class FlowActivateDTO(Schema):
    active: bool


class LeadSourceInDTO(Schema):
    name: str
    account_id: int


class LeadInDTO(Schema):
    telefone: str
    nome: str = ''
    email: str = ''
    origem: str = ''
    extras: dict = {}
    campos: dict = {}  # chave do campo personalizado -> valor (grava no contato)


class FlowOutDTO(ModelSchema):
    account: int
    run_count: int = 0

    class Meta:
        model = Flow
        fields = ['id', 'name', 'account', 'trigger', 'trigger_config', 'graph', 'active', 'updated_at']

    @staticmethod
    def resolve_account(obj):
        return obj.account_id


class FlowRunOutDTO(ModelSchema):
    contact_id: int
    contact_name: str

    class Meta:
        model = FlowRun
        fields = ['id', 'status', 'node_id', 'steps', 'created_at', 'updated_at']

    @staticmethod
    def resolve_contact_name(obj):
        return obj.contact.name or obj.contact.wa_id


class LeadSourceOutDTO(ModelSchema):
    account: int

    class Meta:
        model = LeadSource
        fields = ['id', 'name', 'account', 'token', 'active', 'created_at']

    @staticmethod
    def resolve_account(obj):
        return obj.account_id


class LeadAcceptedDTO(Schema):
    contact_id: int
    flow_started: bool
    ignored_fields: list[str] = []
