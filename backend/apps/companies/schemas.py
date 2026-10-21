from datetime import datetime

from ninja import ModelSchema, Schema

from apps.companies import plans
from apps.companies.models import Company


class CompanyInDTO(Schema):
    name: str


class CompanyOutDTO(ModelSchema):
    role: str | None = None
    plan_active: bool

    class Meta:
        model = Company
        fields = ['id', 'name', 'created_at', 'plan', 'plan_until']

    @staticmethod
    def resolve_plan_active(obj) -> bool:
        return plans.is_active(obj)


class CompanyPatchDTO(Schema):
    name: str


class MemberOutDTO(Schema):
    id: int
    name: str | None = None
    email: str
    role: str


class InviteOutDTO(Schema):
    id: int
    token: str
    created_at: datetime
