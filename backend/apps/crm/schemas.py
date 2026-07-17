from ninja import Schema, ModelSchema
from apps.crm.models import PipedriveAccount
from typing import Literal

class PipedriveConnectDTO(Schema):
    api_key: str

class PipedriveDefaultsDTO(Schema):
    pipeline_id: int | None = None
    stage_id: int | None = None

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
    class Meta:
        model = PipedriveAccount
        fields = ['id', 'default_pipeline_id', 'default_stage_id', 'created_at']

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

