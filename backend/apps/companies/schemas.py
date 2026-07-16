from datetime import datetime

from ninja import ModelSchema, Schema

from apps.companies.models import Company


class CompanyInDTO(Schema):
    name: str


class CompanyOutDTO(ModelSchema):
    role: str | None = None

    class Meta:
        model = Company
        fields = ['id', 'name', 'created_at']


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
