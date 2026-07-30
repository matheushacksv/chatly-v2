from typing import Self

from django.contrib.auth import get_user_model
from ninja import ModelSchema, Schema
from pydantic import EmailStr, model_validator

User = get_user_model()


class RegisterUserDTO(Schema):
    email: EmailStr
    password: str
    repeat_password: str
    name: str | None = None
    invite: str | None = None

    @model_validator(mode='after')
    def _validate_fields(self) -> Self:
        if self.password != self.repeat_password:
            raise ValueError('passwords do not match')
        if len(self.password) < 8:
            raise ValueError('password must be 8 characters or more')
        return self


class UserOutDTO(ModelSchema):
    class Meta:
        model = User
        fields = ['id', 'email', 'is_staff']


class LoginDTO(Schema):
    email: EmailStr
    password: str


class RefreshDTO(Schema):
    refresh: str


class AccessOutDTO(Schema):
    access: str


class TokenOutDTO(Schema):
    access: str
    refresh: str
    user: UserOutDTO


class ChangePasswordDTO(Schema):
    current_password: str
    new_password: str
    repeat_password: str

    @model_validator(mode='after')
    def _validate_fields(self) -> Self:
        if self.new_password != self.repeat_password:
            raise ValueError('passwords do not match')
        return self


class ForgotPasswordDTO(Schema):
    email: EmailStr


class ResetPasswordDTO(Schema):
    uid: str
    token: str
    new_password: str
