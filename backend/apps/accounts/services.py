from datetime import timedelta
from typing import cast

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode
from ninja_jwt.exceptions import TokenError
from ninja_jwt.tokens import RefreshToken

from apps.accounts.models import SiteConfig, User
from apps.accounts.tasks import send_reset_email_task
from apps.companies.models import Invite
from core.exceptions import Conflict, Forbidden, Unauthorized, ValidationError


def register_user(email, password, name=None, invite=None, **extra_fields):
    invite_ok = bool(invite) and Invite.objects.filter(token=invite, created_at__gte=timezone.now() - timedelta(days=7)).exists()
    if not SiteConfig.registration_is_open() and not invite_ok:
        raise Forbidden('cadastros fechados no momento')
    email = email.lower()

    try:
        validate_password(password)
    except DjangoValidationError as e:
        raise ValidationError(' '.join(e.messages))

    if User.objects.filter(email=email).exists():
        raise Conflict('email already registered')

    try:
        user = User.objects.create_user(email, password, name=name, **extra_fields)
    except IntegrityError:
        raise Conflict('email already registered')
    return user


def login_user(email, password):
    user = authenticate(email=email, password=password)
    if user is None:
        raise Unauthorized('invalid credentials')
    refresh = cast(RefreshToken, RefreshToken.for_user(user))
    return {'access': str(refresh.access_token), 'refresh': str(refresh), 'user': user}


def refresh_token(refresh: str):
    try:
        token = RefreshToken(refresh)
    except TokenError:
        raise Unauthorized('invalid or expired refresh token')
    return {'access': str(token.access_token)}


def change_password(user, current_password, new_password):
    # 400 e não 401: é erro no formulário, não sessão expirada (401 faz o front tentar refresh do token)
    if not user.check_password(current_password):
        raise ValidationError('senha atual incorreta')

    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as e:
        raise ValidationError(' '.join(e.messages))

    user.set_password(new_password)
    user.save(update_fields=['password'])


def forgot_password(email):
    user = User.objects.filter(email=email.lower()).first()
    if user is None:
        return
    send_reset_email_task.delay(user.pk)


def reset_password(uid, token, new_password):
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uid).decode())
    except (User.DoesNotExist, ValueError, OverflowError):
        raise Unauthorized('invalid or expired token')

    if not default_token_generator.check_token(user, token):
        raise Unauthorized('invalid or expired token')

    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as e:
        raise ValidationError(' '.join(e.messages))
    user.set_password(new_password)
    user.save(update_fields=['password'])
    return user
