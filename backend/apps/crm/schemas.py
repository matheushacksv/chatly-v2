from typing import Literal

from ninja import Field, ModelSchema, Schema

from apps.crm.models import PipedriveAccount


class PipedriveConnectDTO(Schema):
    api_key: str


class PipedriveDefaultsDTO(Schema):
    pipeline_id: int | None = None
    stage_id: int | None = None
    lead_account_id: int | None = None


class AutoTemplateDTO(Schema):
    template_name: str = ''
    language: str = ''
    body_params: list[str] = []
    header_media_id: str = ''


class AutoRuleDTO(Schema):
    field: str
    values: list[str]
    template: AutoTemplateDTO


class AutoFollowupDTO(AutoTemplateDTO):
    after_hours: int = Field(24, ge=1, le=720)


class AutoOpenDTO(Schema):
    stage_ids: list[int] = []
    template: AutoTemplateDTO = AutoTemplateDTO()
    rules: list[AutoRuleDTO] = []
    only_in_hours: bool = True
    delay_minutes: int = Field(0, ge=0, le=1440)
    followup: AutoFollowupDTO | None = None


class FieldMapItemDTO(Schema):
    source: Literal['deal', 'person']
    key: str
    contact_field: str


class PipedriveFieldDTO(Schema):
    source: Literal['deal', 'person']
    key: str
    name: str


class PersonLinkDTO(Schema):
    person_id: int


class PersonCreateDTO(Schema):
    name: str


class DealCreateDTO(Schema):
    title: str
    value: float | None = None
    pipeline_id: int | None = None
    stage_id: int | None = None


class DealPatchDTO(Schema):
    stage_id: int | None = None
    status: Literal['open', 'won', 'lost'] | None = None


class NoteInDTO(Schema):
    content: str


class PipedriveAccountOutDTO(ModelSchema):
    lead_account_id: int | None = None
    receiving_events: bool = False

    class Meta:
        model = PipedriveAccount
        fields = ['id', 'default_pipeline_id', 'default_stage_id', 'auto_stage_ids', 'auto_template', 'auto_rules', 'auto_only_in_hours', 'auto_delay_minutes', 'auto_followup', 'field_map', 'created_at']

    @staticmethod
    def resolve_receiving_events(obj):
        return bool(obj.webhook_ids)


class StageBriefDTO(Schema):
    id: int
    name: str


class PipelineOutDTO(Schema):
    id: int
    name: str
    stages: list[StageBriefDTO] = []


class PersonBriefDTO(Schema):
    id: int
    name: str
    phones: list[str] = []

    @staticmethod
    def resolve_phones(obj):
        return [p['value'] if isinstance(p, dict) else p for p in obj.get('phones') or []]


class PersonResolveDTO(Schema):
    person: PersonBriefDTO | None = None
    suggestions: list[PersonBriefDTO] = []


class DealBriefDTO(Schema):
    id: int
    title: str
    value: float | None = None
    currency: str | None = None
    pipeline_id: int | None = None
    stage_id: int | None = None
    status: str | None = None
    update_time: str | None = None


class NoteOutDTO(Schema):
    id: int
    content: str
    add_time: str | None = None


class ActivityOutDTO(Schema):
    id: int
    subject: str | None = None
    type: str | None = None
    due_date: str | None = None
    done: bool = False
