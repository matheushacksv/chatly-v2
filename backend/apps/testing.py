from typing import cast

from ninja_jwt.tokens import RefreshToken

from apps.accounts.models import User


def auth_header(user: User) -> dict[str, str]:
    access = cast(RefreshToken, RefreshToken.for_user(user)).access_token
    return {'Authorization': f'Bearer {access}'}
