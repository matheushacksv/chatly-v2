from ninja import Router, Status

from apps.accounts import services
from apps.accounts.schemas import (
    AccessOutDTO,
    ChangePasswordDTO,
    ForgotPasswordDTO,
    LoginDTO,
    RefreshDTO,
    RegisterUserDTO,
    ResetPasswordDTO,
    TokenOutDTO,
    UserOutDTO,
)

router = Router(tags=['Auth'])


@router.post('/register', response={201: UserOutDTO}, auth=None)
def register_endpoint(request, payload: RegisterUserDTO):
    return Status(201, services.register_user(payload.email, payload.password, payload.name, invite=payload.invite))


@router.post('/login', response=TokenOutDTO, auth=None)
def login_endpoint(request, payload: LoginDTO):
    return Status(200, services.login_user(payload.email, payload.password))


@router.post('/refresh', response=AccessOutDTO, auth=None)
def refresh_endpoint(request, payload: RefreshDTO):
    return services.refresh_token(payload.refresh)


@router.post('/change-password', response={204: None})
def change_password_endpoint(request, payload: ChangePasswordDTO):
    services.change_password(request.auth, payload.current_password, payload.new_password)
    return Status(204, None)


@router.post('/forgot-password', response={202: dict}, auth=None)
def forgot_password_endpoint(request, payload: ForgotPasswordDTO):
    services.forgot_password(payload.email)
    return Status(202, {'detail': 'if the email exists, a reset link was sent'})


@router.post('/reset-password', response={200: UserOutDTO}, auth=None)
def reset_password_endpoint(request, payload: ResetPasswordDTO):
    return Status(200, services.reset_password(payload.uid, payload.token, payload.new_password))
