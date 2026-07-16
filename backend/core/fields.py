from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


def _fernet() -> Fernet:
    return Fernet(settings.FIELD_ENCRYPTION_KEY)


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value: str | None) -> str | None:
        if value is None or value == '':
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value: str | None, expression, connection) -> str | None:
        if value is None or value == '':
            return value
        return _fernet().decrypt(value.encode()).decode()
